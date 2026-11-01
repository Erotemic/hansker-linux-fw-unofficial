"""Offline regression tests for Linux HIDAPI libusb/hidraw path handling.

No real USB device, firmware image, or elevated privileges are needed.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hansker_updater import linux_usb
from hansker_updater import protocol

VID = 0x258A
PID = 0x0136


class FakeLinuxUsb:
    def __init__(self, base: Path, *, behind_hub: bool = False, vid: str = "258a"):
        self.sys = base / "sys"
        self.dev = base / "dev"
        self.proc = base / "proc"
        self.proc.mkdir(parents=True)
        self.root = self.sys / "devices/pci0000:00/0000:00:14.0/usb3"
        device_name = "3-2.4" if behind_hub else "3-4"
        self.device = self.root / "3-2" / device_name if behind_hub else self.root / device_name
        self.interface = self.device / f"{device_name}:1.1"
        self.hid = self.interface / "0003:258A:0136.0001"
        self.hid.mkdir(parents=True)
        (self.device / "idVendor").write_text(vid + "\n")
        (self.device / "idProduct").write_text("0136\n")
        (self.device / "busnum").write_text("3\n")
        (self.device / "devnum").write_text("8\n")
        (self.root / "bDeviceClass").write_text("09\n")

        usb_link = self.sys / "bus/usb/devices" / self.interface.name
        usb_link.parent.mkdir(parents=True)
        usb_link.symlink_to(self.interface)
        hidraw_link = self.sys / "class/hidraw/hidraw7/device"
        hidraw_link.parent.mkdir(parents=True)
        hidraw_link.symlink_to(self.hid)
        self.usbfs = self.dev / "bus/usb/003/008"
        self.usbfs.parent.mkdir(parents=True)
        self.usbfs.touch()
        self.hidraw = self.dev / "hidraw7"
        self.hidraw.touch()
        self.interface_path = self.interface.name.encode()

    def topology(self, path):
        return linux_usb.usb_topology(path, sysfs_root=self.sys, expected_vid=VID, expected_pid=PID)

    def openers(self, path):
        return linux_usb.find_hidraw_openers(
            path, sysfs_root=self.sys, dev_root=self.dev, proc_root=self.proc,
            expected_vid=VID, expected_pid=PID,
        )

    def add_opener(self, pid: int, name: str, path: Path):
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fd/3").symlink_to(path)
        (directory / "comm").write_text(name + "\n")


class TestLinuxHidapiSysfs(unittest.TestCase):
    def test_direct_libusb_path_from_issue_1(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            self.assertEqual(fake.interface_path, b"3-4:1.1")
            self.assertEqual(fake.topology(fake.interface_path), (True, "direct to host/root hub"))

    def test_direct_hidraw_backend_path(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            self.assertEqual(fake.topology(b"/dev/hidraw7"), (True, "direct to host/root hub"))

    def test_intervening_hub_rejected_with_both_backends(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root), behind_hub=True)
            for path in (fake.interface_path, b"/dev/hidraw7"):
                direct, reason = fake.topology(path)
                self.assertIs(direct, False)
                self.assertIn("3-2", reason)

    def test_identity_mismatch_is_unknown_not_direct(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root), vid="ffff")
            direct, reason = fake.topology(fake.interface_path)
            self.assertIsNone(direct)
            self.assertIn("identity mismatch", reason)

    def test_missing_sysfs_link_is_unknown_not_direct(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            (fake.sys / "bus/usb/devices" / fake.interface.name).unlink()
            direct, reason = fake.topology(fake.interface_path)
            self.assertIsNone(direct)
            self.assertIn("cannot resolve", reason)

    def test_missing_root_hub_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            disconnected = fake.sys / "orphan/3-4/3-4:1.1"
            disconnected.mkdir(parents=True)
            (disconnected.parent / "idVendor").write_text("258a")
            (disconnected.parent / "idProduct").write_text("0136")
            link = fake.sys / "bus/usb/devices/3-4:1.1"
            link.unlink()
            link.symlink_to(disconnected)
            direct, reason = fake.topology(fake.interface_path)
            self.assertIsNone(direct)
            self.assertIn("root-hub", reason)

    def test_unrecognized_path_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            direct, reason = fake.topology(b"hidraw7")
            self.assertIsNone(direct)
            self.assertIn("unsupported", reason)

    def test_libusb_openers_include_usbfs_and_matching_hidraw(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            fake.add_opener(10001, "usbfs-holder", fake.usbfs)
            fake.add_opener(10002, "hidraw-holder", fake.hidraw)
            self.assertEqual(
                fake.openers(fake.interface_path),
                [(10001, "usbfs-holder"), (10002, "hidraw-holder")],
            )

    def test_libusb_openers_ignore_other_interface_hidraw(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            other_interface = fake.device / "3-4:1.0"
            other_hid = other_interface / "0003:258A:0136.0002"
            other_hid.mkdir(parents=True)
            other_entry = fake.sys / "class/hidraw/hidraw8/device"
            other_entry.parent.mkdir(parents=True)
            other_entry.symlink_to(other_hid)
            other_device = fake.dev / "hidraw8"
            other_device.touch()
            fake.add_opener(10003, "other-interface", other_device)
            self.assertEqual(fake.openers(fake.interface_path), [])

    def test_unavailable_process_descriptors_are_not_reported_as_clear(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            fake.proc.rmdir()
            with self.assertRaisesRegex(linux_usb.UsbInspectionError, "cannot scan process"):
                fake.openers(fake.interface_path)

    def test_hidraw_openers_only_check_selected_node(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            fake.add_opener(10001, "usbfs-holder", fake.usbfs)
            fake.add_opener(10002, "hidraw-holder", fake.hidraw)
            self.assertEqual(fake.openers(b"/dev/hidraw7"), [(10002, "hidraw-holder")])

    def test_libusb_opener_mapping_must_not_silently_succeed_without_usbfs(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            fake.usbfs.unlink()
            with self.assertRaisesRegex(linux_usb.UsbInspectionError, "usbfs node is missing"):
                fake.openers(fake.interface_path)

    def test_libusb_opener_mapping_requires_bus_and_device_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            fake = FakeLinuxUsb(Path(root))
            (fake.device / "devnum").unlink()
            with self.assertRaisesRegex(linux_usb.UsbInspectionError, "cannot locate usbfs"):
                fake.openers(fake.interface_path)


class TestProtocolIntegration(unittest.TestCase):
    def test_protocol_passes_expected_identity_to_topology(self):
        with mock.patch.object(
            linux_usb, "usb_topology", return_value=(True, "direct to host/root hub")
        ) as inspect:
            self.assertEqual(
                protocol.usb_topology(b"3-4:1.1"), (True, "direct to host/root hub")
            )
            inspect.assert_called_once_with(
                b"3-4:1.1", expected_vid=protocol.NORMAL_VID, expected_pid=protocol.NORMAL_PID
            )

    def test_protocol_maps_unverified_opener_check_to_device_error(self):
        with mock.patch.object(
            linux_usb, "find_hidraw_openers",
            side_effect=linux_usb.UsbInspectionError("unmapped libusb interface"),
        ):
            with self.assertRaisesRegex(protocol.DeviceError, "unmapped libusb interface"):
                protocol.find_hidraw_openers(b"3-4:1.1")


if __name__ == "__main__":
    unittest.main()
