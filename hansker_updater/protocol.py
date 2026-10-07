from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .vendor import (
    FIRMWARE_FILENAME,
    FIRMWARE_SHA256,
    PACKETS_FILENAME,
    PACKETS_SHA256,
    VendorResourceError,
    load_vendor_bytes,
)
from typing import Callable, Iterable, Sequence

NORMAL_VID = 0x258A
NORMAL_PID = 0x0136
ISP_VID = 0x0603
ISP_PID = 0x1020
TARGET_VERSION = "MS1000V2.23"
NORMAL_PRODUCT = "Hansker Per"

REPORT_CMD = 0x05
REPORT_XFER = 0x06
REPORT_LED = 0x09
CMD_C9 = 0xC9
CMD_C9_OPEN = 0x60
CMD_C9_COMMIT = 0x70
CMD_ENTER_ISP = 0x75
STATUS_BUSY = 0x55
STATUS_READY = 0x00
STATUS_IDLE = 0xFF

PAGE_SIZE = 2048
SECONDARY_PAGES = 14
SECONDARY_SIZE = PAGE_SIZE * SECONDARY_PAGES  # 0x7000

# Packet positions in the Performance capture. These are checked by
# validate_resources() before any destructive operation.
IDX_HEARTBEAT_0 = 0
IDX_C9_OPEN = 1
IDX_C9_INIT = 2
IDX_CB_FIRST = 3
IDX_CB_LAST = 124
IDX_C9_COMMIT = 125
IDX_POST_COMMIT = 126
IDX_HEARTBEAT_1 = 127
IDX_ENTER_ISP = 128
IDX_ISP_HEARTBEAT = 129
IDX_ISP_ERASE = 130
IDX_ISP_INIT_WRITE = 131
IDX_ISP_PAGE_FIRST = 132
IDX_ISP_PAGE_LAST = 145
IDX_ISP_REARM_WRITE = 146
IDX_ISP_REARM_PAGE0 = 147
IDX_ISP_INIT_READ = 148
IDX_EEPROM_FIRST = 149
IDX_EEPROM_LAST = 153
IDX_ENABLE = 154
IDX_REBOOT = 155
IDX_NORMAL_HEARTBEAT = 156
IDX_LED_FIRST = 157
IDX_LED_LAST = 159
IDX_FINAL_HEARTBEAT = 160

LogFn = Callable[[str], None]


class UpdateError(RuntimeError):
    pass


class ResourceError(UpdateError):
    pass


class DeviceError(UpdateError):
    pass


class VerificationError(UpdateError):
    pass


@dataclass(frozen=True)
class Packet:
    type: str
    report_id: int
    interface: int
    data: bytes

    @classmethod
    def from_json(cls, item: dict) -> "Packet":
        return cls(
            type=item["type"],
            report_id=int(item["reportId"]),
            interface=int(item["interface"]),
            data=bytes.fromhex(item["hex"]),
        )


@dataclass(frozen=True)
class Resources:
    packets: tuple[Packet, ...]
    firmware: bytes


@dataclass(frozen=True)
class NormalDevicePaths:
    iface0: bytes | None
    iface1: bytes
    product: str


@dataclass(frozen=True)
class UsbLocation:
    bus: int | None
    address: int | None
    interface: int = 0

    def describe(self) -> str:
        bus = "?" if self.bus is None else str(self.bus)
        address = "?" if self.address is None else str(self.address)
        return f"usb:{bus}:{address}:{self.interface}"


@dataclass(frozen=True)
class Stage1Result:
    session_healthy: bool
    commit_status: int


@dataclass(frozen=True)
class FlashResult:
    stage1_attempts: int
    version_after: str | None
    eeprom_warning: str | None = None
    led_warning: str | None = None



def load_resources() -> Resources:
    try:
        firmware = load_vendor_bytes(FIRMWARE_FILENAME)
        packet_bytes = load_vendor_bytes(PACKETS_FILENAME)
    except VendorResourceError as ex:
        raise ResourceError(str(ex)) from ex
    packet_items = json.loads(packet_bytes.decode("utf8"))
    resources = Resources(
        packets=tuple(Packet.from_json(item) for item in packet_items),
        firmware=firmware,
    )
    validate_resources(resources)
    return resources


def _require_packet(
    packets: Sequence[Packet],
    index: int,
    *,
    interface: int,
    report_id: int,
    prefix: bytes,
    length: int | None = None,
) -> None:
    try:
        packet = packets[index]
    except IndexError as ex:
        raise ResourceError(f"capture missing packet {index}") from ex
    if packet.interface != interface:
        raise ResourceError(f"packet {index}: expected interface {interface}, got {packet.interface}")
    if packet.report_id != report_id:
        raise ResourceError(f"packet {index}: expected report {report_id}, got {packet.report_id}")
    if not packet.data.startswith(prefix):
        raise ResourceError(
            f"packet {index}: expected prefix {prefix.hex(' ')}, got {packet.data[:len(prefix)].hex(' ')}"
        )
    if length is not None and len(packet.data) != length:
        raise ResourceError(f"packet {index}: expected length {length}, got {len(packet.data)}")


def validate_resources(resources: Resources) -> None:
    # Cryptographic verification happens while loading the locally imported
    # vendor files. This function validates the protocol structure itself, so
    # the public test suite can exercise the updater using synthetic fixtures
    # without redistributing Hansker firmware bytes.
    p = resources.packets
    if len(p) != 161:
        raise ResourceError(f"expected 161 Performance packets, got {len(p)}")
    if len(resources.firmware) != 0x1F000:
        raise ResourceError(f"expected firmware.bin size 0x1f000, got {len(resources.firmware):#x}")

    _require_packet(p, IDX_C9_OPEN, interface=1, report_id=5, prefix=b"\x05\xc9\x60", length=6)
    _require_packet(p, IDX_C9_INIT, interface=1, report_id=6, prefix=b"\x06\xc9\x05\x01", length=1032)
    _require_packet(p, IDX_C9_COMMIT, interface=1, report_id=5, prefix=b"\x05\xc9\x70", length=6)
    _require_packet(p, IDX_ENTER_ISP, interface=1, report_id=5, prefix=b"\x05\x75", length=6)
    _require_packet(p, IDX_ISP_ERASE, interface=0, report_id=5, prefix=b"\x05\x45", length=6)
    _require_packet(p, IDX_ISP_INIT_WRITE, interface=0, report_id=5, prefix=b"\x05\x57\x00\x00\x00\x70", length=6)
    _require_packet(p, IDX_ISP_INIT_READ, interface=0, report_id=5, prefix=b"\x05\x52\x00\x00\x00\x70", length=6)
    _require_packet(p, IDX_ENABLE, interface=0, report_id=5, prefix=b"\x05\x55", length=6)
    _require_packet(p, IDX_REBOOT, interface=0, report_id=5, prefix=b"\x05\x5a", length=6)

    # The Windows capture intentionally writes page 0 with byte 0 cleared,
    # writes all 14 pages, then re-writes page 0 with the real LJMP opcode.
    first_pass = b"".join(p[i].data[2:] for i in range(IDX_ISP_PAGE_FIRST, IDX_ISP_PAGE_LAST + 1))
    if len(first_pass) != SECONDARY_SIZE:
        raise ResourceError("secondary payload reconstructed to the wrong length")
    expected = resources.firmware[:SECONDARY_SIZE]
    mismatches = [i for i, (a, b) in enumerate(zip(first_pass, expected)) if a != b]
    if mismatches != [0] or first_pass[0] != 0x00 or expected[0] != 0x02:
        raise ResourceError("unexpected first-pass secondary payload relation to firmware.bin")
    if p[IDX_ISP_REARM_PAGE0].data[2:] != expected[:PAGE_SIZE]:
        raise ResourceError("re-arm page does not equal firmware.bin page 0")

    target_blob = p[IDX_CB_FIRST].data
    if TARGET_VERSION.encode("ascii") not in target_blob:
        raise ResourceError(f"capture does not identify target {TARGET_VERSION}")

    for i in range(IDX_LED_FIRST, IDX_LED_LAST + 1):
        _require_packet(p, i, interface=1, report_id=9, prefix=b"\x09", length=520)


def _import_hid():
    try:
        import hid  # type: ignore
    except ImportError as ex:
        raise DeviceError("Python package 'hidapi' is required (module 'hid')") from ex
    return hid


def _unique_path_devices(vid: int, pid: int) -> list[dict]:
    hid = _import_hid()
    seen: set[bytes] = set()
    result: list[dict] = []
    for dev in hid.enumerate(vid, pid):
        path = dev.get("path")
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(dev)
    return result


def find_normal_device() -> NormalDevicePaths:
    hid = _import_hid()
    devices = hid.enumerate(NORMAL_VID, NORMAL_PID)
    candidates: list[dict] = []
    unexpected_products: set[str] = set()
    for dev in devices:
        product = dev.get("product_string") or ""
        if product == NORMAL_PRODUCT:
            candidates.append(dev)
        elif product.startswith("Hansker"):
            unexpected_products.add(product)
    if not candidates:
        if unexpected_products:
            names = ", ".join(repr(x) for x in sorted(unexpected_products))
            raise DeviceError(
                f"found unvalidated Hansker product string(s) at {NORMAL_VID:04x}:{NORMAL_PID:04x}: "
                f"{names}; only {NORMAL_PRODUCT!r} is supported"
            )
        raise DeviceError("Hansker Performance 258a:0136 not found")

    iface0_paths = sorted({dev.get("path") for dev in candidates if dev.get("interface_number") == 0 and dev.get("path")})
    iface1_paths = sorted({dev.get("path") for dev in candidates if dev.get("interface_number") == 1 and dev.get("path")})

    if len(iface1_paths) == 0:
        raise DeviceError("Hansker interface 1 (vendor control interface) not found")
    if len(iface1_paths) > 1 or len(iface0_paths) > 1:
        raise DeviceError(
            "multiple Hansker Performance devices are connected; disconnect all but the one being updated"
        )

    iface0 = iface0_paths[0] if iface0_paths else None
    iface1 = iface1_paths[0]
    return NormalDevicePaths(iface0=iface0, iface1=iface1, product=NORMAL_PRODUCT)


def _import_usb():
    try:
        import usb.core  # type: ignore
        import usb.util  # type: ignore
    except ImportError as ex:
        raise DeviceError(
            "Python package 'pyusb' is required for raw USB access to the "
            "0603:1020 ISP bootloader (install with: uv pip install pyusb)"
        ) from ex
    return usb.core, usb.util


def find_isp_usb_devices() -> list[object]:
    """Find the control-only 0603:1020 bootloader via libusb/PyUSB.

    Linux usbhid may refuse to bind this device because it has no interrupt-IN
    endpoint, so hidapi/hidraw enumeration is intentionally not used here.
    """
    usb_core, _usb_util = _import_usb()
    found = usb_core.find(find_all=True, idVendor=ISP_VID, idProduct=ISP_PID)
    return list(found or [])


def _usb_location(dev) -> UsbLocation:
    return UsbLocation(
        bus=getattr(dev, "bus", None),
        address=getattr(dev, "address", None),
        interface=0,
    )


def find_isp_location() -> UsbLocation | None:
    devices = find_isp_usb_devices()
    if not devices:
        return None
    if len(devices) > 1:
        locations = ", ".join(_usb_location(d).describe() for d in devices)
        raise DeviceError(f"multiple 0603:1020 ISP devices found: {locations}")
    return _usb_location(devices[0])


def wait_for_isp(timeout: float = 30.0, poll: float = 0.25, log: LogFn = print) -> UsbLocation:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        location = find_isp_location()
        if location is not None:
            log(f"ISP bootloader found via raw USB: {location.describe()}")
            return location
        time.sleep(poll)
    raise DeviceError("ISP bootloader 0603:1020 did not appear within 30 seconds")


class RawUsbFeatureDevice:
    """Minimal HID Feature-report transport over libusb control transfers.

    This mirrors hidra's nusb backend: SET_REPORT/GET_REPORT are HID class
    control requests to interface 0, and nonzero report IDs remain present as
    the first byte of the data stage.
    """

    SET_REPORT = 0x09
    GET_REPORT = 0x01
    REPORT_TYPE_FEATURE = 0x03
    BMREQ_OUT_CLASS_INTERFACE = 0x21
    BMREQ_IN_CLASS_INTERFACE = 0xA1

    def __init__(self, dev, *, interface: int = 0, timeout_ms: int = 5000):
        _usb_core, usb_util = _import_usb()
        self._dev = dev
        self._usb_util = usb_util
        self.interface = interface
        self.timeout_ms = timeout_ms
        self._claimed = False
        self._detached = False

        try:
            if hasattr(dev, "is_kernel_driver_active") and dev.is_kernel_driver_active(interface):
                dev.detach_kernel_driver(interface)
                self._detached = True
        except (NotImplementedError, AttributeError):
            pass

        try:
            usb_util.claim_interface(dev, interface)
            self._claimed = True
        except Exception as ex:
            raise DeviceError(
                f"could not claim raw USB ISP interface {interface}: {ex}"
            ) from ex

    @property
    def location(self) -> UsbLocation:
        return _usb_location(self._dev)

    def send_feature_report(self, data: bytes | bytearray) -> int:
        data = bytes(data)
        if not data:
            raise DeviceError("feature report must contain a report ID byte")
        report_id = data[0]
        try:
            sent = self._dev.ctrl_transfer(
                self.BMREQ_OUT_CLASS_INTERFACE,
                self.SET_REPORT,
                (self.REPORT_TYPE_FEATURE << 8) | report_id,
                self.interface,
                data,
                timeout=self.timeout_ms,
            )
        except Exception as ex:
            raise DeviceError(
                f"raw USB SET_REPORT id={report_id:#04x} failed: {ex}"
            ) from ex
        return int(sent)

    def get_feature_report(self, report_id: int, size: int) -> bytes:
        try:
            data = self._dev.ctrl_transfer(
                self.BMREQ_IN_CLASS_INTERFACE,
                self.GET_REPORT,
                (self.REPORT_TYPE_FEATURE << 8) | report_id,
                self.interface,
                size,
                timeout=self.timeout_ms,
            )
        except Exception as ex:
            raise DeviceError(
                f"raw USB GET_REPORT id={report_id:#04x} failed: {ex}"
            ) from ex
        return bytes(data)

    def close(self) -> None:
        if self._dev is None:
            return
        dev = self._dev
        self._dev = None
        try:
            if self._claimed:
                self._usb_util.release_interface(dev, self.interface)
        except Exception:
            pass
        try:
            if self._detached:
                dev.attach_kernel_driver(self.interface)
        except Exception:
            pass
        try:
            self._usb_util.dispose_resources(dev)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def open_isp_raw_usb(*, timeout: float = 30.0, poll: float = 0.25, log: LogFn = print) -> RawUsbFeatureDevice:
    location = wait_for_isp(timeout=timeout, poll=poll, log=log)
    devices = find_isp_usb_devices()
    matches = [d for d in devices if _usb_location(d) == location]
    if len(matches) != 1:
        raise DeviceError(
            f"ISP bootloader changed while opening {location.describe()}; "
            "inspect lsusb and retry recovery"
        )
    dev = RawUsbFeatureDevice(matches[0], interface=location.interface)
    log(f"  claimed raw USB ISP interface: {dev.location.describe()}")
    return dev


def wait_for_normal(timeout: float = 15.0, poll: float = 0.25, log: LogFn = print) -> NormalDevicePaths:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            paths = find_normal_device()
            log(f"Hansker mouse is back online: {paths.iface1!r}")
            return paths
        except Exception as ex:
            last = ex
        time.sleep(poll)
    raise DeviceError(f"Hansker 258a:0136 did not reappear: {last}")


def get_r5(dev) -> bytes:
    data = bytes(dev.get_feature_report(REPORT_CMD, 6))
    if len(data) < 3:
        raise DeviceError(f"short report-5 response: {data.hex(' ')}")
    return data


def wait_for_ready_byte(
    dev,
    timeout: float,
    *,
    poll: float = 0.002,
    log: LogFn | None = None,
    label: str = "ready",
) -> tuple[int | None, bytes | Exception | None, int]:
    """Match the vendor replay's first-nonbusy R5 polling behavior."""
    deadline = time.monotonic() + timeout
    last: bytes | Exception | None = None
    polls = 0
    start = time.monotonic()
    while time.monotonic() < deadline:
        try:
            data = get_r5(dev)
            last = data
            polls += 1
            status = data[2]
            if status != STATUS_BUSY:
                if log:
                    log(
                        f"{label}: ready after {time.monotonic() - start:.3f}s, "
                        f"R5={data.hex(' ')} status=0x{status:02x} polls={polls}"
                    )
                return status, data, polls
        except Exception as ex:
            last = ex
        time.sleep(poll)
    if log:
        log(f"WARNING: {label}: timed out after {timeout:.1f}s; last={last!r}")
    return None, last, polls


def wait_for_stable_ready(
    dev,
    *,
    timeout: float = 4.0,
    stable_for: float = 0.4,
    poll: float = 0.005,
    log: LogFn = print,
    label: str = "stable-ready",
) -> int | None:
    """Match the vendor commit-health test: same non-busy R5 for ~400ms."""
    deadline = time.monotonic() + timeout
    overall_start = time.monotonic()
    last_status: int | None = None
    stable_start: float | None = None
    while time.monotonic() < deadline:
        try:
            data = get_r5(dev)
            status = data[2]
        except Exception:
            status = None

        now = time.monotonic()
        if status is None or status == STATUS_BUSY:
            last_status = status
            stable_start = None
        elif status != last_status:
            last_status = status
            stable_start = now
        elif stable_start is None:
            stable_start = now
        elif now - stable_start >= stable_for:
            log(
                f"{label} after {(now - overall_start) * 1000:.0f}ms: "
                f"R5[2]=0x{status:02x}"
            )
            return status
        time.sleep(poll)
    log(f"WARNING: {label}: did not stabilize within {timeout:.1f}s")
    return None


def extract_version(data: bytes) -> str | None:
    printable = "".join(chr(b) if 0x20 <= b < 0x7F else "\0" for b in data)
    # Existing firmware was observed as MS1000V2.07H; target is MS1000V2.23.
    match = re.search(r"MS1000V[0-9A-Za-z._-]+", printable)
    if match:
        return match.group(0)
    match = re.search(r"V2\.[0-9A-Za-z._-]+", printable)
    return match.group(0) if match else None


def probe_version(log: LogFn = print) -> str | None:
    """Read the running firmware version without committing or entering ISP."""
    hid = _import_hid()
    resources = load_resources()
    validate_resources(resources)
    paths = find_normal_device()
    open_packet = resources.packets[IDX_C9_OPEN].data
    init_packet = resources.packets[IDX_C9_INIT].data

    log(f"iface1: {paths.iface1!r}")
    # The bundled version checker opens separate handles for report 5 and 6.
    with hid.Device(path=paths.iface1) as status_dev, hid.Device(path=paths.iface1) as xfer_dev:
        n = status_dev.send_feature_report(open_packet)
        log(f"C9 open bytes: {n}")
        wait_for_ready_byte(status_dev, 15.0, log=log, label="after C9 open")

        n = xfer_dev.send_feature_report(init_packet)
        log(f"C9 init bytes: {n}")
        wait_for_ready_byte(status_dev, 15.0, log=log, label="after C9 init")

        log("reading report 6 despite any R5 timeout (matches bundled updater)...")
        response = bytes(xfer_dev.get_feature_report(REPORT_XFER, 32))
        log(f"R6: {response.hex(' ')}")
        version = extract_version(response)
        log(f"firmware version: {version or 'not recognized'}")

    # Intentionally NO 05 c9 70 commit and NO 05 75 ISP transition.
    log("C9 handles closed without commit; no firmware write requested.")
    return version


def replay_c9cb_stage1(
    resources: Resources,
    *,
    packet_delay: float = 0.015,
    log: LogFn = print,
) -> Stage1Result:
    """Replay the official Performance C9/CB stream through ISP entry."""
    hid = _import_hid()
    validate_resources(resources)
    paths = find_normal_device()
    packets = resources.packets
    log(f"Stage 1: C9/CB main firmware replay on {paths.iface1!r}")
    log("  iface-0 heartbeat packets are intentionally skipped on Linux")

    commit_status: int | None = None
    extended_busy = False
    healthy = False

    status_dev = hid.Device(path=paths.iface1)
    xfer_dev = hid.Device(path=paths.iface1)
    try:
        for index in range(IDX_C9_OPEN, IDX_ENTER_ISP + 1):
            packet = packets[index]
            if packet.type != "feature" or packet.interface != 1:
                continue
            dev = status_dev if packet.report_id == REPORT_CMD else xfer_dev
            is_enter_isp = packet.data[:2] == bytes([REPORT_CMD, CMD_ENTER_ISP])
            is_commit = packet.data[:3] == bytes([REPORT_CMD, CMD_C9, CMD_C9_COMMIT])

            try:
                sent = dev.send_feature_report(packet.data)
                if sent != len(packet.data):
                    raise DeviceError(f"packet {index}: short feature write {sent}/{len(packet.data)}")
            except Exception as ex:
                if is_enter_isp:
                    log(f"packet {index}: ISP-enter disconnected during send (expected): {ex!r}")
                    break
                raise DeviceError(f"packet {index}: feature write failed: {ex}") from ex

            if index == IDX_C9_OPEN:
                log("  C9 session open sent")
            elif index == IDX_C9_INIT:
                log("  C9 init sent")
            elif IDX_CB_FIRST <= index <= IDX_CB_LAST:
                if index == IDX_CB_FIRST or index == IDX_CB_LAST or (index - IDX_CB_FIRST) % 20 == 0:
                    done = index - IDX_CB_FIRST + 1
                    total = IDX_CB_LAST - IDX_CB_FIRST + 1
                    log(f"  CB data: {done}/{total} packets")
            elif is_commit:
                log("  C9 COMMIT sent after complete CB stream")
            elif index == IDX_POST_COMMIT:
                log("  post-commit packet sent; waiting for internal flash write")
            elif is_enter_isp:
                log("  ISP-enter sent")

            if is_enter_isp:
                # The device normally disconnects/re-enumerates here; do not poll R5.
                break

            timeout = 45.0 if extended_busy else 8.0
            status, _last, _polls = wait_for_ready_byte(
                status_dev,
                timeout,
                log=log if index in (IDX_C9_OPEN, IDX_C9_INIT, IDX_C9_COMMIT, IDX_POST_COMMIT) else None,
                label=f"packet {index}",
            )
            if status is None:
                log(f"  WARNING: packet {index}: device stayed busy/unreadable for >{timeout:.0f}s; continuing")

            if is_commit:
                stable = wait_for_stable_ready(status_dev, log=log, label="c9-close-commit")
                if stable == STATUS_READY:
                    healthy = True
                    commit_status = stable
                    extended_busy = True
                    log("  Stage-1 session is healthy; enabling 45 s post-commit timeout")
                elif stable == STATUS_IDLE:
                    healthy = False
                    commit_status = stable
                    log("  WARNING: C9 session dropped (R5=0xff); main firmware was not committed")
                else:
                    # Unlike the vendor updater, refuse to guess whether an internal
                    # code-flash write is active. Sending 05 75 in this state is unsafe.
                    raise DeviceError(
                        "commit state is ambiguous; refusing to enter ISP. "
                        "Wait at least 60 seconds, power-cycle the mouse, and retry."
                    )

            if index == IDX_POST_COMMIT and healthy and status is None:
                # Vendor notes say exactly-45s timeout can be normal. A small extra
                # grace period is conservative before sending ISP-enter.
                log("  post-commit wait hit 45 s; adding 2 s safety grace")
                time.sleep(2.0)

            if packet_delay:
                time.sleep(packet_delay)
    finally:
        try:
            xfer_dev.close()
        finally:
            status_dev.close()

    if commit_status is None:
        raise DeviceError("C9 commit was not reached")
    return Stage1Result(session_healthy=healthy, commit_status=commit_status)


def _send_feature(dev, packet: Packet, *, index: int, allow_disconnect: bool = False) -> None:
    try:
        sent = dev.send_feature_report(packet.data)
    except Exception as ex:
        if allow_disconnect:
            return
        raise DeviceError(f"packet {index}: feature write failed: {ex}") from ex
    if sent != len(packet.data):
        raise DeviceError(f"packet {index}: short feature write {sent}/{len(packet.data)}")


def flash_secondary_stage2(resources: Resources, *, log: LogFn = print) -> tuple[str | None, str | None]:
    """Flash and verify the secondary 0x7000-byte wireless firmware region.

    The 0603:1020 bootloader is intentionally accessed through raw USB rather
    than hidapi/hidraw. On Linux it may expose only control endpoint zero and
    no interrupt-IN endpoint, in which case usbhid creates no hidraw node.
    """
    validate_resources(resources)
    packets = resources.packets

    log("Stage 2: secondary wireless MCU")
    dev = open_isp_raw_usb(log=log)
    eeprom_warning = None
    try:
        log("  erasing secondary flash")
        _send_feature(dev, packets[IDX_ISP_ERASE], index=IDX_ISP_ERASE)
        time.sleep(2.0)

        log(f"  writing {SECONDARY_PAGES} x {PAGE_SIZE}-byte pages")
        _send_feature(dev, packets[IDX_ISP_INIT_WRITE], index=IDX_ISP_INIT_WRITE)
        for index in range(IDX_ISP_PAGE_FIRST, IDX_ISP_PAGE_LAST + 1):
            _send_feature(dev, packets[index], index=index)
            page = index - IDX_ISP_PAGE_FIRST + 1
            log(f"    page {page:02d}/{SECONDARY_PAGES}")

        # Re-write page 0 with byte 0x02 restored. The first pass deliberately
        # left this byte as 0x00 so partially-written firmware cannot boot.
        _send_feature(dev, packets[IDX_ISP_REARM_WRITE], index=IDX_ISP_REARM_WRITE)
        _send_feature(dev, packets[IDX_ISP_REARM_PAGE0], index=IDX_ISP_REARM_PAGE0)

        log("  reading back secondary flash for byte-for-byte verification")
        _send_feature(dev, packets[IDX_ISP_INIT_READ], index=IDX_ISP_INIT_READ)
        expected = resources.firmware[:SECONDARY_SIZE]
        read_back = bytearray()
        for page in range(SECONDARY_PAGES):
            try:
                response = bytes(dev.get_feature_report(REPORT_XFER, PAGE_SIZE + 2))
            except Exception as ex:
                raise VerificationError(f"read-back page {page}: {ex}") from ex
            if len(response) < PAGE_SIZE + 2:
                raise VerificationError(
                    f"read-back page {page}: short report {len(response)}/{PAGE_SIZE + 2}"
                )
            # Match upstream sinowisp: byte 1 is the transfer direction
            # marker. Do not require byte 0 to echo the requested report ID;
            # the running Hansker firmware already demonstrated on Linux that
            # GET_FEATURE may return a different leading byte.
            if response[1] != 0x72:
                raise VerificationError(
                    f"read-back page {page}: expected direction 0x72, got {response[1]:#04x}"
                )
            read_back.extend(response[2 : PAGE_SIZE + 2])
            log(f"    verify page {page + 1:02d}/{SECONDARY_PAGES}")

        if bytes(read_back) != expected:
            mismatch = next(i for i, (a, b) in enumerate(zip(read_back, expected)) if a != b)
            raise VerificationError(
                f"secondary verify failed at byte {mismatch:#06x}: "
                f"got 0x{read_back[mismatch]:02x}, expected 0x{expected[mismatch]:02x}"
            )
        log(f"  secondary firmware verify OK ({SECONDARY_SIZE} bytes)")

        log("  writing EEPROM LED auto-off configuration")
        try:
            for index in range(IDX_EEPROM_FIRST, IDX_EEPROM_LAST + 1):
                _send_feature(dev, packets[index], index=index)
                time.sleep(0.01)
        except Exception as ex:
            eeprom_warning = str(ex)
            log(f"  WARNING: EEPROM config failed: {ex}")

        log("  enabling firmware")
        _send_feature(dev, packets[IDX_ENABLE], index=IDX_ENABLE)
        time.sleep(0.05)

        log("  rebooting mouse")
        _send_feature(dev, packets[IDX_REBOOT], index=IDX_REBOOT, allow_disconnect=True)
    finally:
        dev.close()

    paths = wait_for_normal(log=log)
    log("  waiting 4 s for wireless subsystem initialization")
    time.sleep(4.0)

    hid = _import_hid()
    led_warning = None
    log("  sending post-flash LED configuration")
    try:
        with hid.Device(path=paths.iface1) as normal_dev:
            for index in range(IDX_LED_FIRST, IDX_LED_LAST + 1):
                _send_feature(normal_dev, packets[index], index=index)
                time.sleep(0.02)
        log("  LED configuration sent")
    except Exception as ex:
        led_warning = str(ex)
        log(f"  WARNING: post-flash LED config failed: {ex}")

    return eeprom_warning, led_warning


def flash_full(
    *,
    max_attempts: int = 3,
    packet_delay: float = 0.015,
    log: LogFn = print,
    post_verify: bool = False,
) -> FlashResult:
    resources = load_resources()
    validate_resources(resources)
    last_error: Exception | None = None
    eeprom_warning = None
    led_warning = None
    successful_attempt: int | None = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log(f"Auto-retry {attempt}/{max_attempts}: waiting 8 s")
            time.sleep(8.0)
            find_normal_device()
        log(f"=== flash attempt {attempt}/{max_attempts} ===")
        try:
            stage1 = replay_c9cb_stage1(resources, packet_delay=packet_delay, log=log)
            eeprom_warning, led_warning = flash_secondary_stage2(resources, log=log)
            if stage1.session_healthy:
                successful_attempt = attempt
                break
            last_error = DeviceError("stage-1 C9 session dropped; secondary recovered; retry required")
            log(f"WARNING: {last_error}")
        except Exception as ex:
            last_error = ex
            log(f"attempt {attempt} failed: {ex}")
            # Only retry if the normal mouse is available again. If the device is
            # left in ISP after a stage-2 verification failure, automatic retries
            # would be unsafe and confusing.
            try:
                find_normal_device()
            except Exception:
                raise ex

    if successful_attempt is None:
        raise UpdateError(f"update failed after {max_attempts} attempts: {last_error}")

    # Do the read-only final version check only after the destructive retry loop
    # is finished. A flaky post-check must never cause an otherwise successful
    # firmware update to be repeated automatically.
    version_after = None
    if post_verify:
        version_after = probe_version(log=log)
        if version_after is None or not version_after.startswith(TARGET_VERSION):
            raise VerificationError(
                "flash stages completed and verified, but final running version "
                f"is {version_after!r}; expected prefix {TARGET_VERSION!r}. "
                "Do not automatically reflash; inspect the log first."
            )

    return FlashResult(
        stage1_attempts=successful_attempt,
        version_after=version_after,
        eeprom_warning=eeprom_warning,
        led_warning=led_warning,
    )


def find_hidraw_openers(path: bytes | str) -> list[tuple[int, str]]:
    target = os.fsdecode(path)
    result: list[tuple[int, str]] = []
    self_pid = os.getpid()
    proc = Path("/proc")
    if not proc.exists():
        return result
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        if pid == self_pid:
            continue
        fd_dir = child / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except (OSError, PermissionError):
            continue
        hit = False
        for fd in fds:
            try:
                dest = os.readlink(fd)
            except OSError:
                continue
            if dest == target:
                hit = True
                break
        if hit:
            try:
                comm = (child / "comm").read_text().strip()
            except OSError:
                comm = "?"
            result.append((pid, comm))
    return sorted(result)


def usb_topology(path: bytes | str) -> tuple[bool | None, str]:
    """Return (direct_to_root_hub, explanation) using Linux sysfs."""
    name = Path(os.fsdecode(path)).name
    sys = Path("/sys/class/hidraw") / name / "device"
    try:
        current = sys.resolve()
    except OSError as ex:
        return None, f"could not resolve sysfs topology: {ex}"

    usb_nodes = []
    for parent in [current, *current.parents]:
        if (parent / "idVendor").exists() and (parent / "idProduct").exists():
            try:
                vid = (parent / "idVendor").read_text().strip()
                pid = (parent / "idProduct").read_text().strip()
                cls = (parent / "bDeviceClass").read_text().strip() if (parent / "bDeviceClass").exists() else ""
            except OSError:
                continue
            usb_nodes.append((parent.name, vid, pid, cls))
    if not usb_nodes:
        return None, "USB device ancestry not found in sysfs"

    # Linux names the root hub itself "usbN" and reports it as class 09.
    # That root hub is not an external hub/adapter. Any other class-09 USB
    # ancestor between the mouse and root hub is an actual intermediate hub.
    intermediates = [
        n for n in usb_nodes[1:]
        if n[3].lower() == "09" and not n[0].startswith("usb")
    ]
    if intermediates:
        return False, "via USB hub(s): " + ", ".join(n[0] for n in intermediates)
    return True, "direct to host/root hub"


def describe_plan(resources: Resources) -> list[str]:
    validate_resources(resources)
    return [
        f"target: Hansker Performance {NORMAL_VID:04x}:{NORMAL_PID:04x}",
        f"main target version: {TARGET_VERSION}",
        f"C9/CB main stream: packets {IDX_C9_OPEN}-{IDX_ENTER_ISP}",
        f"secondary ISP bootloader: {ISP_VID:04x}:{ISP_PID:04x}",
        f"secondary verified region: {SECONDARY_SIZE:#x} ({SECONDARY_PAGES} x {PAGE_SIZE})",
        f"firmware.bin sha256: {FIRMWARE_SHA256}",
        f"packet capture sha256: {PACKETS_SHA256}",
        "commit safety: 05 c9 70 is sent only after all CB packets",
        "verification: secondary flash read back byte-for-byte before enable/reboot",
    ]
