from __future__ import annotations

import argparse
import datetime as _dt
import os
import pwd
import sys
from pathlib import Path

from . import __version__
from .protocol import (
    TARGET_VERSION,
    UpdateError,
    describe_plan,
    find_hidraw_openers,
    find_isp_location,
    find_normal_device,
    flash_full,
    flash_secondary_stage2,
    load_resources,
    probe_version,
    usb_topology,
    validate_resources,
)
from .vendor import (
    FIRMWARE_SHA256,
    PACKETS_SHA256,
    VendorResourceError,
    default_resource_dir,
    import_vendor_resources,
)


class TeeLogger:
    def __init__(self, path: Path | None):
        self.path = path
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("w", encoding="utf8", buffering=1)

    def __call__(self, message: str) -> None:
        print(message, flush=True)
        if self._fh is not None:
            self._fh.write(message + "\n")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _original_user_home() -> tuple[Path, int | None, int | None]:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            ent = pwd.getpwnam(sudo_user)
            return Path(ent.pw_dir), ent.pw_uid, ent.pw_gid
        except KeyError:
            pass
    return Path.home(), None, None


def _default_log_path() -> Path:
    home, _uid, _gid = _original_user_home()
    directory = home / "Downloads"
    if not directory.exists():
        directory = Path.cwd()
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"hansker-linux-flash-{stamp}.log"


def _chown_log(path: Path) -> None:
    _home, uid, gid = _original_user_home()
    if uid is None or gid is None:
        return
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


def cmd_import_resources(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    destination = Path(args.destination).expanduser() if args.destination else default_resource_dir()
    try:
        result = import_vendor_resources(source, destination)
    except VendorResourceError as ex:
        raise UpdateError(str(ex)) from ex
    print(f"vendor resources imported to: {result.destination}")
    print(f"firmware.bin: {FIRMWARE_SHA256}")
    print(f"packets_performance.json: {PACKETS_SHA256}")
    print("resources: OK")
    return 0


def _post_update_instructions(log) -> None:
    log("POST-UPDATE: firmware is verified and it is safe to disconnect the cable.")
    log(
        "If mouse input is unresponsive: unplug USB-C, toggle the bottom switch "
        "away from Wired and back to Wired, wait 10 seconds, then reconnect."
    )


def cmd_plan(_args: argparse.Namespace) -> int:
    resources = load_resources()
    for line in describe_plan(resources):
        print(line)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    resources = load_resources()
    validate_resources(resources)
    print("resources: OK")
    paths = find_normal_device()
    print(f"mouse: {paths.product!r}")
    print(f"iface0: {paths.iface0!r}")
    print(f"iface1: {paths.iface1!r}")

    direct, explanation = usb_topology(paths.iface1)
    print(f"USB topology: {explanation}")
    if direct is False and not args.allow_hub:
        print("ERROR: mouse is behind a USB hub/adapter; direct USB-C is required for flashing", file=sys.stderr)
        return 2

    openers = find_hidraw_openers(paths.iface1)
    if openers:
        print("userspace processes currently holding the control hidraw device:")
        for pid, name in openers:
            print(f"  pid={pid} {name}")
        if not args.ignore_openers:
            print("ERROR: close these processes before flashing", file=sys.stderr)
            return 2
    else:
        print("hidraw openers: none")
    print("doctor: OK")
    return 0


def cmd_probe(_args: argparse.Namespace) -> int:
    print(
        "WARNING: probe opens a C9 diagnostic session. On the validated mouse, "
        "normal input temporarily stops until the connection mode is toggled or the mouse is power-cycled."
    )
    print("No firmware commit or ISP entry is performed by probe.")
    version = probe_version()
    if version is None:
        return 2
    if version.startswith(TARGET_VERSION):
        print(f"status: already at target {TARGET_VERSION}")
    else:
        print(f"status: update available -> {TARGET_VERSION}")
    print(
        "POST-PROBE: if mouse input is inactive, unplug USB-C, toggle away from Wired "
        "and back to Wired, wait about 10 seconds, then reconnect."
    )
    return 0


def _confirm_flash(args: argparse.Namespace) -> None:
    if args.yes:
        return
    print()
    print("DESTRUCTIVE FIRMWARE UPDATE")
    print("- Use a direct USB-C connection, not a hub.")
    print("- Keep the cable connected until the command finishes.")
    print("- Have another pointing device available.")
    print("- If you just ran 'probe', unplug the Hansker for at least 5 seconds and reconnect it first.")
    print()
    phrase = f"FLASH HANSKER PERFORMANCE {TARGET_VERSION}"
    answer = input(f"Type exactly '{phrase}' to continue: ").strip()
    if answer != phrase:
        raise UpdateError("confirmation did not match; no firmware write attempted")


def cmd_isp_status(_args: argparse.Namespace) -> int:
    location = find_isp_location()
    if location is None:
        print("ISP bootloader 0603:1020: not found")
        return 2
    print(f"ISP bootloader 0603:1020: present at {location.describe()}")
    print("raw USB enumeration: OK")
    return 0


def _confirm_recover_isp(args: argparse.Namespace) -> None:
    if args.yes:
        return
    print()
    print("ISP RECOVERY / STAGE-2 FLASH")
    print("- This is only for a mouse already enumerating as 0603:1020.")
    print("- It skips the C9/CB main-firmware stage entirely.")
    print("- Keep the cable connected until the command finishes.")
    print("- It erases and rewrites the secondary 0x7000-byte firmware region.")
    print()
    phrase = "RECOVER HANSKER"
    answer = input(f"Type exactly '{phrase}' to continue: ").strip()
    if answer != phrase:
        raise UpdateError("confirmation did not match; no ISP flash attempted")


def cmd_recover_isp(args: argparse.Namespace) -> int:
    if os.geteuid() != 0 and not args.no_root_check:
        raise UpdateError("recover-isp currently requires root; run with sudo")

    resources = load_resources()
    validate_resources(resources)
    location = find_isp_location()
    if location is None:
        raise UpdateError(
            "0603:1020 ISP bootloader is not present; refusing Stage-2-only recovery"
        )
    print(f"ISP bootloader: {location.describe()}")
    _confirm_recover_isp(args)

    log_path = Path(args.log_file).expanduser() if args.log_file else _default_log_path()
    logger = TeeLogger(log_path)
    try:
        logger("Unofficial Hansker Linux Firmware Updater — ISP recovery")
        logger(f"target: {TARGET_VERSION}")
        logger(f"starting bootloader: {location.describe()}")
        logger(f"log: {log_path}")
        logger("Stage 1 is intentionally skipped; device is already in 0603:1020 ISP mode.")
        eeprom_warning, led_warning = flash_secondary_stage2(resources, log=logger)
        version_after = None
        if args.post_probe:
            version_after = probe_version(log=logger)
            if version_after is None or not version_after.startswith(TARGET_VERSION):
                raise UpdateError(
                    "Stage 2 completed and the normal mouse returned, but final version "
                    f"is {version_after!r}; expected prefix {TARGET_VERSION!r}. "
                    "Do not automatically reflash; inspect the log first."
                )
        logger("=== RECOVERY SUCCESS ===")
        if version_after is None:
            logger("post-flash version probe: skipped by default to avoid opening a fresh C9 session")
        else:
            logger(f"post-flash version: {version_after}")
        _post_update_instructions(logger)
        if eeprom_warning:
            logger(f"WARNING: EEPROM config: {eeprom_warning}")
        if led_warning:
            logger(f"WARNING: LED config: {led_warning}")
    finally:
        logger.close()
        _chown_log(log_path)

    print(f"log saved: {log_path}")
    return 0


def cmd_flash(args: argparse.Namespace) -> int:
    if os.geteuid() != 0 and not args.no_root_check:
        raise UpdateError("flash currently requires root; run with sudo")

    resources = load_resources()
    validate_resources(resources)
    paths = find_normal_device()

    direct, explanation = usb_topology(paths.iface1)
    print(f"USB topology: {explanation}")
    if direct is False and not args.allow_hub:
        raise UpdateError("refusing to flash through a USB hub/adapter; use a direct USB-C port")

    openers = find_hidraw_openers(paths.iface1)
    if openers and not args.ignore_openers:
        detail = ", ".join(f"{name}[{pid}]" for pid, name in openers)
        raise UpdateError(f"control hidraw device is already open by: {detail}")

    _confirm_flash(args)

    log_path = Path(args.log_file).expanduser() if args.log_file else _default_log_path()
    logger = TeeLogger(log_path)
    try:
        logger("Unofficial Hansker Linux Firmware Updater")
        logger(f"target: {TARGET_VERSION}")
        logger(f"mouse: {paths.product!r}")
        logger(f"USB topology: {explanation}")
        logger(f"log: {log_path}")
        logger("IMPORTANT: iface-0 heartbeat is skipped because it timed out on this Linux host and is nonessential.")
        result = flash_full(
            max_attempts=args.max_attempts,
            packet_delay=args.packet_delay_ms / 1000.0,
            log=logger,
            post_verify=args.post_probe,
        )
        logger("=== SUCCESS ===")
        logger(f"stage-1 attempts: {result.stage1_attempts}")
        if result.version_after is None:
            logger("post-flash version probe: skipped by default to avoid opening a fresh C9 session")
        else:
            logger(f"post-flash version: {result.version_after}")
        _post_update_instructions(logger)
        if result.eeprom_warning:
            logger(f"WARNING: EEPROM config: {result.eeprom_warning}")
        if result.led_warning:
            logger(f"WARNING: LED config: {result.led_warning}")
    finally:
        logger.close()
        _chown_log(log_path)

    print(f"log saved: {log_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hansker-fw",
        description="Unofficial Linux firmware updater for Hansker Performance (HK95)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "import-resources",
        help="import and verify vendor firmware from Hansker's official updater",
    )
    p.add_argument("source", help="official updater .dmg/archive or an extracted updater directory")
    p.add_argument(
        "--destination",
        help="resource directory override (normally .vendor/v2.23 in a source checkout)",
    )
    p.set_defaults(func=cmd_import_resources)

    p = sub.add_parser("plan", help="show the validated update plan without touching USB")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("doctor", help="run non-destructive resource/device/topology checks")
    p.add_argument("--allow-hub", action="store_true", help="do not fail doctor when a hub is detected")
    p.add_argument("--ignore-openers", action="store_true", help="do not fail when another process has hidraw open")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "probe",
        help="read firmware version; no commit/ISP, but may temporarily suppress mouse input",
    )
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("isp-status", help="read-only check for the 0603:1020 ISP bootloader via raw USB")
    p.set_defaults(func=cmd_isp_status)

    p = sub.add_parser(
        "recover-isp",
        help="finish Stage 2 when the mouse is already in the 0603:1020 ISP bootloader",
    )
    p.add_argument("--yes", action="store_true", help="skip the interactive destructive-action confirmation")
    p.add_argument("--no-root-check", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--post-probe",
        action="store_true",
        help="run a final C9 version probe (may require a power-cycle afterward)",
    )
    p.add_argument("--log-file", help="write recovery log to this path instead of ~/Downloads")
    p.set_defaults(func=cmd_recover_isp)

    p = sub.add_parser("flash", help="perform the full v2.23 update")
    p.add_argument("--yes", action="store_true", help="skip the interactive destructive-action confirmation")
    p.add_argument("--allow-hub", action="store_true", help="allow flashing through a USB hub (not recommended)")
    p.add_argument("--ignore-openers", action="store_true", help="ignore userspace processes holding the control hidraw node")
    p.add_argument("--no-root-check", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--max-attempts", type=int, default=3, choices=range(1, 4), metavar="N")
    p.add_argument("--packet-delay-ms", type=float, default=15.0, metavar="MS")
    p.add_argument(
        "--post-probe",
        action="store_true",
        help="run a final C9 version probe (may require a power-cycle afterward)",
    )
    p.add_argument("--log-file", help="write flash log to this path instead of ~/Downloads")
    p.set_defaults(func=cmd_flash)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except UpdateError as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        return 1
    except Exception as ex:
        print(f"ERROR: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
