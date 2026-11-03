"""Linux sysfs USB topology and device-opener checks for HIDAPI paths.

Linux HIDAPI may return /dev/hidrawN (hidraw backend) or a USB interface
identifier such as 3-4:1.1 (libusb backend). Both refer to the same sysfs
USB device tree but have different lookup paths.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_HIDRAW_PATH = re.compile(r"/dev/hidraw\d+\Z")
_HIDRAW_NAME = re.compile(r"hidraw\d+\Z")
_USB_INTERFACE = re.compile(r"\d+-\d+(?:\.\d+)*:\d+\.\d+\Z")
_USB_DEVICE = re.compile(r"\d+-\d+(?:\.\d+)*\Z")
_USB_ROOT = re.compile(r"usb\d+\Z")


class UsbInspectionError(RuntimeError):
    """The USB device cannot be located or inspected sufficiently safely."""


@dataclass(frozen=True)
class UsbSysfsLocation:
    interface: Path
    device: Path
    root_hub: Path
    intermediates: tuple[Path, ...]


def inspect_hidapi_path(
    path: bytes | str,
    *,
    sysfs_root: Path = Path("/sys"),
    expected_vid: int,
    expected_pid: int,
) -> UsbSysfsLocation:
    """Resolve either Linux HIDAPI path format to the physical USB ancestry.

    Raises UsbInspectionError instead of trusting a nonexistent or mismatched
    sysfs path. The root hub is an allowed ancestor, not an external hub.
    """
    source = os.fsdecode(path)
    if _HIDRAW_PATH.fullmatch(source):
        lookup = sysfs_root / "class/hidraw" / Path(source).name / "device"
    elif _USB_INTERFACE.fullmatch(source):
        lookup = sysfs_root / "bus/usb/devices" / source
    else:
        raise UsbInspectionError(f"unsupported Linux HIDAPI path: {source!r}")

    try:
        current = lookup.resolve(strict=True)
    except (OSError, RuntimeError) as ex:
        raise UsbInspectionError(f"cannot resolve USB sysfs path {lookup}: {ex}") from ex

    interface = next(
        (node for node in (current, *current.parents) if _USB_INTERFACE.fullmatch(node.name)),
        None,
    )
    if interface is None or not _USB_DEVICE.fullmatch(interface.parent.name):
        raise UsbInspectionError(f"USB interface ancestry not found for {source!r}")
    device = interface.parent

    try:
        vid = int((device / "idVendor").read_text().strip(), 16)
        pid = int((device / "idProduct").read_text().strip(), 16)
    except (OSError, ValueError) as ex:
        raise UsbInspectionError(f"cannot read USB identity at {device}: {ex}") from ex
    if (vid, pid) != (expected_vid, expected_pid):
        raise UsbInspectionError(
            f"USB identity mismatch at {device}: {vid:04x}:{pid:04x} "
            f"(expected {expected_vid:04x}:{expected_pid:04x})"
        )

    root_hub = next((p for p in device.parents if _USB_ROOT.fullmatch(p.name)), None)
    expected_root_name = f"usb{device.name.split('-', 1)[0]}"
    if root_hub is None or root_hub.name != expected_root_name:
        raise UsbInspectionError(f"USB root-hub ancestry not found for {device}")

    intermediates = tuple(p for p in device.parents if p != root_hub and root_hub in p.parents)
    return UsbSysfsLocation(interface, device, root_hub, intermediates)


def usb_topology(
    path: bytes | str,
    *,
    sysfs_root: Path = Path("/sys"),
    expected_vid: int,
    expected_pid: int,
) -> tuple[bool | None, str]:
    """Return whether the matching device is directly attached to a root hub."""
    try:
        location = inspect_hidapi_path(
            path, sysfs_root=sysfs_root, expected_vid=expected_vid, expected_pid=expected_pid
        )
    except UsbInspectionError as ex:
        return None, str(ex)

    if location.intermediates:
        return False, "via USB hub(s): " + ", ".join(p.name for p in reversed(location.intermediates))
    return True, "direct to host/root hub"


def _opener_device_paths(
    path: bytes | str,
    location: UsbSysfsLocation,
    *,
    sysfs_root: Path,
    dev_root: Path,
) -> set[str]:
    """Find relevant device nodes for either HIDAPI backend."""
    source = os.fsdecode(path)
    if _HIDRAW_PATH.fullmatch(source):
        return {str(dev_root / Path(source).name)}

    # A libusb client opens a usbfs node rather than /dev/hidrawN. Check both
    # that node and any kernel hidraw node belonging to this exact interface.
    try:
        bus = int((location.device / "busnum").read_text().strip())
        dev = int((location.device / "devnum").read_text().strip())
    except (OSError, ValueError) as ex:
        raise UsbInspectionError(f"cannot locate usbfs node for {location.device}: {ex}") from ex
    if not (1 <= bus <= 999 and 1 <= dev <= 999):
        raise UsbInspectionError(f"invalid USB bus/device numbers: {bus}:{dev}")
    usbfs = dev_root / "bus/usb" / f"{bus:03d}" / f"{dev:03d}"
    if not usbfs.exists():
        raise UsbInspectionError(f"usbfs node is missing: {usbfs}")
    targets = {str(usbfs)}

    hidraw_class = sysfs_root / "class/hidraw"
    if hidraw_class.is_dir():
        try:
            entries = list(hidraw_class.iterdir())
        except OSError as ex:
            raise UsbInspectionError(f"cannot enumerate {hidraw_class}: {ex}") from ex
        for entry in entries:
            if not _HIDRAW_NAME.fullmatch(entry.name):
                continue
            try:
                hid_sysfs = (entry / "device").resolve(strict=True)
            except (OSError, RuntimeError):
                # A removed HID device can leave a transient stale entry.
                continue
            if hid_sysfs == location.interface or location.interface in hid_sysfs.parents:
                targets.add(str(dev_root / entry.name))
    return targets


def find_hidraw_openers(
    path: bytes | str,
    *,
    sysfs_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    proc_root: Path = Path("/proc"),
    expected_vid: int,
    expected_pid: int,
) -> list[tuple[int, str]]:
    """Find processes holding the HID control interface or its USB device.

    The historical name is retained for the protocol/CLI API. For a libusb
    HIDAPI path, inspect the matching usbfs device and associated hidraw node
    instead of silently comparing '3-4:1.1' against /proc fd destinations.
    """
    location = inspect_hidapi_path(
        path, sysfs_root=sysfs_root, expected_vid=expected_vid, expected_pid=expected_pid
    )
    targets = _opener_device_paths(path, location, sysfs_root=sysfs_root, dev_root=dev_root)
    if not proc_root.is_dir():
        raise UsbInspectionError(f"cannot scan process descriptors: {proc_root} is missing")

    result: list[tuple[int, str]] = []
    self_pid = os.getpid()
    try:
        procs = list(proc_root.iterdir())
    except OSError as ex:
        raise UsbInspectionError(f"cannot enumerate {proc_root}: {ex}") from ex
    for child in procs:
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        if pid == self_pid:
            continue
        try:
            fds = list((child / "fd").iterdir())
        except OSError:
            # Processes can exit while /proc is inspected. Keep the previous
            # behavior of ignoring processes whose descriptors disappear.
            continue
        for fd in fds:
            try:
                dest = os.readlink(fd)
            except OSError:
                continue
            if dest in targets:
                try:
                    comm = (child / "comm").read_text().strip()
                except OSError:
                    comm = "?"
                result.append((pid, comm))
                break
    return sorted(result)
