from __future__ import annotations

import hashlib
import os
import pwd
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

FIRMWARE_FILENAME = "firmware.bin"
PACKETS_FILENAME = "packets_performance.json"
FIRMWARE_SHA256 = "b932769f06425db7243db17f7ba0464fe8bae6123e3881dcb897c9cf78b76ba4"
PACKETS_SHA256 = "c4e867ec0b2a9ba32d7129ec560badd3bae2959f48b7d653036a793f1c7252ee"
TARGET_RESOURCE_VERSION = "v2.23"


class VendorResourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportedResources:
    destination: Path
    firmware: Path
    packets: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _original_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


def _source_checkout_root() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    if (root / "pyproject.toml").is_file() and (root / "hansker_updater").is_dir():
        return root
    return None


def default_resource_dir() -> Path:
    configured = os.environ.get("HANSKER_FW_RESOURCE_DIR")
    if configured:
        return Path(configured).expanduser()

    checkout = _source_checkout_root()
    if checkout is not None:
        return checkout / ".vendor" / TARGET_RESOURCE_VERSION

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg).expanduser()
    else:
        base = _original_user_home() / ".local" / "share"
    return base / "hansker-linux-fw-unofficial" / TARGET_RESOURCE_VERSION


def _verify_file(path: Path, expected_sha256: str) -> bytes:
    try:
        data = path.read_bytes()
    except FileNotFoundError as ex:
        raise VendorResourceError(f"missing vendor resource: {path}") from ex
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise VendorResourceError(
            f"vendor resource hash mismatch for {path.name}: {digest} != {expected_sha256}"
        )
    return data


def load_vendor_bytes(name: str) -> bytes:
    root = default_resource_dir()
    if name == FIRMWARE_FILENAME:
        expected = FIRMWARE_SHA256
    elif name == PACKETS_FILENAME:
        expected = PACKETS_SHA256
    else:
        raise VendorResourceError(f"unknown vendor resource: {name}")

    path = root / name
    try:
        return _verify_file(path, expected)
    except VendorResourceError as ex:
        raise VendorResourceError(
            f"{ex}. Import Hansker's official updater resources first with:\n"
            "  hansker-fw import-resources ~/Downloads/HanskerFirmwareUpdater.dmg"
        ) from ex


def _find_verified_candidate(root: Path, filename: str, expected_sha256: str) -> Path:
    candidates = [path for path in root.rglob(filename) if path.is_file()]
    if not candidates:
        raise VendorResourceError(f"{filename} was not found under {root}")

    mismatches: list[tuple[Path, str]] = []
    for path in candidates:
        digest = sha256_file(path)
        if digest == expected_sha256:
            return path
        mismatches.append((path, digest))

    details = ", ".join(f"{path}={digest}" for path, digest in mismatches[:4])
    raise VendorResourceError(
        f"found {filename}, but none matched expected SHA-256 {expected_sha256}; {details}"
    )


def _extract_source(source: Path, destination: Path) -> Path:
    if source.is_dir():
        return source

    if not source.is_file():
        raise VendorResourceError(f"source does not exist: {source}")

    suffixes = "".join(source.suffixes).lower()
    if source.suffix.lower() == ".dmg":
        sevenzip = shutil.which("7zz") or shutil.which("7z")
        if sevenzip is None:
            raise VendorResourceError(
                "extracting a .dmg requires a 7-Zip command named '7zz' or '7z'. "
                "Install 7-Zip, or pass an already-extracted Hansker updater directory."
            )
        command = [sevenzip, "x", "-y", f"-o{destination}", str(source)]
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            tail = "\n".join(proc.stdout.splitlines()[-20:])
            raise VendorResourceError(f"7-Zip failed to extract {source}:\n{tail}")
        return destination

    try:
        shutil.unpack_archive(str(source), str(destination))
    except (shutil.ReadError, ValueError) as ex:
        raise VendorResourceError(
            f"unsupported archive format for {source}; pass a .dmg, a zip/tar archive, "
            "or an already-extracted updater directory"
        ) from ex
    return destination


def import_vendor_resources(source: Path, destination: Path | None = None) -> ImportedResources:
    source = source.expanduser()
    destination = (destination or default_resource_dir()).expanduser()

    with tempfile.TemporaryDirectory(prefix="hansker-fw-import-") as tmp:
        extracted = _extract_source(source, Path(tmp))
        firmware = _find_verified_candidate(extracted, FIRMWARE_FILENAME, FIRMWARE_SHA256)
        packets = _find_verified_candidate(extracted, PACKETS_FILENAME, PACKETS_SHA256)

        destination.mkdir(parents=True, exist_ok=True)
        dst_firmware = destination / FIRMWARE_FILENAME
        dst_packets = destination / PACKETS_FILENAME
        shutil.copy2(firmware, dst_firmware)
        shutil.copy2(packets, dst_packets)

    # Verify the final files rather than trusting copy success.
    _verify_file(dst_firmware, FIRMWARE_SHA256)
    _verify_file(dst_packets, PACKETS_SHA256)

    return ImportedResources(
        destination=destination,
        firmware=dst_firmware,
        packets=dst_packets,
    )
