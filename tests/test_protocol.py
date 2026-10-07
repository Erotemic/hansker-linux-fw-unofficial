import unittest
from unittest import mock

from hansker_updater import protocol as p
from tests.helpers import synthetic_resources


class FakeFeatureDevice:
    def __init__(self, *, r5=None, r6=None):
        self.sent = []
        self.r5 = list(r5 or [])
        self.r6 = list(r6 or [])
        self.closed = False

    def send_feature_report(self, data):
        data = bytes(data)
        self.sent.append(data)
        return len(data)

    def get_feature_report(self, report_id, size):
        if report_id == 5:
            if not self.r5:
                raise RuntimeError("no R5")
            item = self.r5.pop(0)
        elif report_id == 6:
            if not self.r6:
                raise RuntimeError("no R6")
            item = self.r6.pop(0)
        else:
            raise RuntimeError(report_id)
        if isinstance(item, Exception):
            raise item
        return bytes(item)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = synthetic_resources()

    def test_resources_validate(self):
        p.validate_resources(self.resources)

    def test_stage2_first_pass_defers_reset_opcode(self):
        packets = self.resources.packets
        first_pass = b"".join(
            packets[i].data[2:] for i in range(p.IDX_ISP_PAGE_FIRST, p.IDX_ISP_PAGE_LAST + 1)
        )
        expected = self.resources.firmware[: p.SECONDARY_SIZE]
        mismatches = [i for i, (a, b) in enumerate(zip(first_pass, expected)) if a != b]
        self.assertEqual(mismatches, [0])
        self.assertEqual(first_pass[0], 0)
        self.assertEqual(expected[0], 2)
        self.assertEqual(packets[p.IDX_ISP_REARM_PAGE0].data[2:], expected[: p.PAGE_SIZE])

    def test_capture_commands_are_in_safe_order(self):
        packets = self.resources.packets
        self.assertEqual(packets[p.IDX_C9_OPEN].data[:3], bytes.fromhex("05 c9 60"))
        self.assertEqual(packets[p.IDX_C9_COMMIT].data[:3], bytes.fromhex("05 c9 70"))
        self.assertEqual(packets[p.IDX_ENTER_ISP].data[:2], bytes.fromhex("05 75"))
        self.assertGreater(p.IDX_C9_COMMIT, p.IDX_CB_LAST)
        self.assertGreater(p.IDX_ENTER_ISP, p.IDX_POST_COMMIT)


class TestNormalDeviceSelection(unittest.TestCase):
    def test_multiple_performance_mice_are_rejected(self):
        devices = [
            {"product_string": "Hansker Per", "interface_number": 0, "path": b"/dev/hidraw10"},
            {"product_string": "Hansker Per", "interface_number": 1, "path": b"/dev/hidraw11"},
            {"product_string": "Hansker Per", "interface_number": 0, "path": b"/dev/hidraw20"},
            {"product_string": "Hansker Per", "interface_number": 1, "path": b"/dev/hidraw21"},
        ]

        class FakeHid:
            @staticmethod
            def enumerate(_vid, _pid):
                return devices

        with mock.patch.object(p, "_import_hid", return_value=FakeHid):
            with self.assertRaisesRegex(p.DeviceError, "multiple Hansker Performance"):
                p.find_normal_device()

    def test_unvalidated_hansker_product_string_is_rejected(self):
        devices = [
            {"product_string": "Hansker Future", "interface_number": 1, "path": b"/dev/hidraw11"},
        ]

        class FakeHid:
            @staticmethod
            def enumerate(_vid, _pid):
                return devices

        with mock.patch.object(p, "_import_hid", return_value=FakeHid):
            with self.assertRaisesRegex(p.DeviceError, "unvalidated Hansker product"):
                p.find_normal_device()


class TestPolling(unittest.TestCase):
    def test_wait_ready_returns_first_nonbusy(self):
        dev = FakeFeatureDevice(r5=[
            bytes.fromhex("05 00 55 00 00 00"),
            bytes.fromhex("05 00 55 00 00 00"),
            bytes.fromhex("05 00 00 00 00 00"),
        ])
        clock = FakeClock()
        with mock.patch.object(p.time, "monotonic", clock.monotonic), mock.patch.object(
            p.time, "sleep", clock.sleep
        ):
            status, data, polls = p.wait_for_ready_byte(dev, 1.0)
        self.assertEqual(status, 0)
        self.assertEqual(data[2], 0)
        self.assertEqual(polls, 3)

    def test_stable_ready_requires_400ms_same_value(self):
        # More than enough repeated 0x00 readings for 400ms at a 5ms poll.
        dev = FakeFeatureDevice(r5=[bytes.fromhex("05 00 00 00 00 00")] * 100)
        clock = FakeClock()
        with mock.patch.object(p.time, "monotonic", clock.monotonic), mock.patch.object(
            p.time, "sleep", clock.sleep
        ):
            status = p.wait_for_stable_ready(dev, log=lambda _msg: None)
        self.assertEqual(status, 0)
        self.assertGreaterEqual(clock.now, 0.4)
        self.assertLess(clock.now, 1.0)

    def test_stable_ready_detects_session_drop(self):
        dev = FakeFeatureDevice(r5=[bytes.fromhex("05 00 ff 00 00 00")] * 100)
        clock = FakeClock()
        with mock.patch.object(p.time, "monotonic", clock.monotonic), mock.patch.object(
            p.time, "sleep", clock.sleep
        ):
            status = p.wait_for_stable_ready(dev, log=lambda _msg: None)
        self.assertEqual(status, 0xFF)


class TestVersionProbe(unittest.TestCase):
    def test_probe_never_commits_or_enters_isp(self):
        resources = synthetic_resources()
        status = FakeFeatureDevice(r5=[
            bytes.fromhex("05 00 00 00 00 00"),
            RuntimeError("timeout"),
        ])
        xfer = FakeFeatureDevice(r6=[bytes.fromhex("05 01 4d 53 31 30 30 30 56 32 2e 30 37 48 01 02")])
        devices = [status, xfer]

        class FakeHid:
            @staticmethod
            def Device(path):
                return devices.pop(0)

        with mock.patch.object(p, "load_resources", return_value=resources), mock.patch.object(
            p, "_import_hid", return_value=FakeHid
        ), mock.patch.object(
            p, "find_normal_device", return_value=p.NormalDevicePaths(None, b"/dev/fake", "Hansker Per")
        ), mock.patch.object(p, "wait_for_ready_byte", return_value=(0, b"", 1)):
            version = p.probe_version(log=lambda _msg: None)

        self.assertEqual(version, "MS1000V2.07H")
        all_sent = status.sent + xfer.sent
        self.assertNotIn(bytes.fromhex("05 c9 70 00 00 00"), all_sent)
        self.assertNotIn(bytes.fromhex("05 75 00 00 00 00"), all_sent)
        self.assertIn(resources.packets[p.IDX_C9_OPEN].data, all_sent)
        self.assertIn(resources.packets[p.IDX_C9_INIT].data, all_sent)


class TestStage1(unittest.TestCase):
    def test_healthy_commit_uses_capture_through_isp(self):
        resources = synthetic_resources()
        status_dev = FakeFeatureDevice()
        xfer_dev = FakeFeatureDevice()
        devices = [status_dev, xfer_dev]

        class FakeHid:
            @staticmethod
            def Device(path):
                return devices.pop(0)

        # All ordinary packet waits return ready. Commit stable state is healthy.
        with mock.patch.object(p, "_import_hid", return_value=FakeHid), mock.patch.object(
            p, "find_normal_device", return_value=p.NormalDevicePaths(None, b"/dev/fake", "Hansker Per")
        ), mock.patch.object(p, "wait_for_ready_byte", return_value=(0, b"", 1)), mock.patch.object(
            p, "wait_for_stable_ready", return_value=0
        ), mock.patch.object(p.time, "sleep", return_value=None):
            result = p.replay_c9cb_stage1(resources, packet_delay=0, log=lambda _msg: None)

        self.assertTrue(result.session_healthy)
        self.assertEqual(result.commit_status, 0)
        sent = status_dev.sent + xfer_dev.sent
        self.assertIn(resources.packets[p.IDX_C9_COMMIT].data, sent)
        self.assertIn(resources.packets[p.IDX_ENTER_ISP].data, sent)
        # The complete CB range must be sent before the commit.
        self.assertTrue(all(resources.packets[i].data in xfer_dev.sent for i in range(p.IDX_CB_FIRST, p.IDX_CB_LAST + 1)))

    def test_ambiguous_commit_refuses_isp(self):
        resources = synthetic_resources()
        status_dev = FakeFeatureDevice()
        xfer_dev = FakeFeatureDevice()
        devices = [status_dev, xfer_dev]

        class FakeHid:
            @staticmethod
            def Device(path):
                return devices.pop(0)

        with mock.patch.object(p, "_import_hid", return_value=FakeHid), mock.patch.object(
            p, "find_normal_device", return_value=p.NormalDevicePaths(None, b"/dev/fake", "Hansker Per")
        ), mock.patch.object(p, "wait_for_ready_byte", return_value=(0, b"", 1)), mock.patch.object(
            p, "wait_for_stable_ready", return_value=None
        ), mock.patch.object(p.time, "sleep", return_value=None):
            with self.assertRaises(p.DeviceError):
                p.replay_c9cb_stage1(resources, packet_delay=0, log=lambda _msg: None)

        self.assertNotIn(resources.packets[p.IDX_ENTER_ISP].data, status_dev.sent)


class TestRawUsbTransport(unittest.TestCase):
    def test_feature_reports_use_hid_class_control_transfers(self):
        calls = []

        class FakeUsbDevice:
            bus = 1
            address = 30

            def is_kernel_driver_active(self, interface):
                return False

            def ctrl_transfer(self, bm_request_type, request, value, index, data_or_length, timeout):
                calls.append((bm_request_type, request, value, index, data_or_length, timeout))
                if bm_request_type == 0xA1:
                    return bytes.fromhex("06 72") + bytes(p.PAGE_SIZE)
                return len(data_or_length)

        class FakeUtil:
            claimed = []
            released = []

            @classmethod
            def claim_interface(cls, dev, interface):
                cls.claimed.append((dev, interface))

            @classmethod
            def release_interface(cls, dev, interface):
                cls.released.append((dev, interface))

            @staticmethod
            def dispose_resources(dev):
                return None

        dev = FakeUsbDevice()
        with mock.patch.object(p, "_import_usb", return_value=(object(), FakeUtil)):
            raw = p.RawUsbFeatureDevice(dev)
            sent = raw.send_feature_report(bytes.fromhex("05 45 45 45 45 45"))
            response = raw.get_feature_report(6, p.PAGE_SIZE + 2)
            raw.close()

        self.assertEqual(sent, 6)
        self.assertEqual(response[:2], bytes.fromhex("06 72"))
        self.assertEqual(calls[0][:4], (0x21, 0x09, 0x0305, 0))
        self.assertEqual(bytes(calls[0][4]), bytes.fromhex("05 45 45 45 45 45"))
        self.assertEqual(calls[1][:5], (0xA1, 0x01, 0x0306, 0, p.PAGE_SIZE + 2))
        self.assertEqual(FakeUtil.claimed, [(dev, 0)])
        self.assertEqual(FakeUtil.released, [(dev, 0)])

    def test_wait_for_isp_uses_raw_usb_location(self):
        class Dev:
            bus = 1
            address = 30

        with mock.patch.object(p, "find_isp_usb_devices", return_value=[Dev()]):
            loc = p.wait_for_isp(timeout=0.1, poll=0, log=lambda _msg: None)
        self.assertEqual(loc, p.UsbLocation(1, 30, 0))


class TestStage2(unittest.TestCase):
    def test_stage2_verifies_before_enable(self):
        resources = synthetic_resources()
        expected = resources.firmware[: p.SECONDARY_SIZE]
        read_reports = [
            bytes([0x06, 0x72]) + expected[i : i + p.PAGE_SIZE]
            for i in range(0, p.SECONDARY_SIZE, p.PAGE_SIZE)
        ]
        boot = FakeFeatureDevice(r6=read_reports)
        normal = FakeFeatureDevice()

        class FakeHid:
            @staticmethod
            def Device(path):
                if path == b"/dev/normal":
                    return normal
                raise AssertionError(path)

        with mock.patch.object(p, "_import_hid", return_value=FakeHid), mock.patch.object(
            p, "open_isp_raw_usb", return_value=boot
        ), mock.patch.object(
            p, "wait_for_normal", return_value=p.NormalDevicePaths(None, b"/dev/normal", "Hansker Per")
        ), mock.patch.object(p.time, "sleep", return_value=None):
            eeprom_warning, led_warning = p.flash_secondary_stage2(resources, log=lambda _msg: None)

        self.assertIsNone(eeprom_warning)
        self.assertIsNone(led_warning)
        enable = resources.packets[p.IDX_ENABLE].data
        reboot = resources.packets[p.IDX_REBOOT].data
        self.assertIn(enable, boot.sent)
        self.assertIn(reboot, boot.sent)
        self.assertLess(boot.sent.index(enable), boot.sent.index(reboot))
        for i in range(p.IDX_LED_FIRST, p.IDX_LED_LAST + 1):
            self.assertIn(resources.packets[i].data, normal.sent)

    def test_stage2_bad_verify_does_not_enable(self):
        resources = synthetic_resources()
        expected = bytearray(resources.firmware[: p.SECONDARY_SIZE])
        expected[100] ^= 1
        read_reports = [
            bytes([0x06, 0x72]) + bytes(expected[i : i + p.PAGE_SIZE])
            for i in range(0, p.SECONDARY_SIZE, p.PAGE_SIZE)
        ]
        boot = FakeFeatureDevice(r6=read_reports)

        with mock.patch.object(
            p, "open_isp_raw_usb", return_value=boot
        ), mock.patch.object(p.time, "sleep", return_value=None):
            with self.assertRaises(p.VerificationError):
                p.flash_secondary_stage2(resources, log=lambda _msg: None)

        self.assertNotIn(resources.packets[p.IDX_ENABLE].data, boot.sent)
        self.assertNotIn(resources.packets[p.IDX_REBOOT].data, boot.sent)


class TestFullFlashRetryPolicy(unittest.TestCase):
    def test_post_verify_failure_does_not_repeat_destructive_flash(self):
        calls = {"stage1": 0, "stage2": 0, "probe": 0}

        def fake_stage1(*args, **kwargs):
            calls["stage1"] += 1
            return p.Stage1Result(session_healthy=True, commit_status=0)

        def fake_stage2(*args, **kwargs):
            calls["stage2"] += 1
            return None, None

        def fake_probe(*args, **kwargs):
            calls["probe"] += 1
            return "MS1000V2.22"

        with mock.patch.object(p, "load_resources", return_value=synthetic_resources()), mock.patch.object(
            p, "replay_c9cb_stage1", side_effect=fake_stage1
        ), mock.patch.object(
            p, "flash_secondary_stage2", side_effect=fake_stage2
        ), mock.patch.object(p, "probe_version", side_effect=fake_probe):
            with self.assertRaises(p.VerificationError):
                p.flash_full(max_attempts=3, log=lambda _msg: None, post_verify=True)

        self.assertEqual(calls, {"stage1": 1, "stage2": 1, "probe": 1})

    def test_default_full_flash_does_not_run_post_probe(self):
        with mock.patch.object(p, "load_resources", return_value=synthetic_resources()), mock.patch.object(
            p, "replay_c9cb_stage1", return_value=p.Stage1Result(session_healthy=True, commit_status=0)
        ), mock.patch.object(
            p, "flash_secondary_stage2", return_value=(None, None)
        ), mock.patch.object(p, "probe_version") as probe:
            result = p.flash_full(max_attempts=1, log=lambda _msg: None)

        self.assertIsNone(result.version_after)
        probe.assert_not_called()

    def test_session_drop_retries_after_secondary_recovery(self):
        outcomes = iter([
            p.Stage1Result(session_healthy=False, commit_status=0xFF),
            p.Stage1Result(session_healthy=True, commit_status=0),
        ])
        calls = {"stage1": 0, "stage2": 0}

        def fake_stage1(*args, **kwargs):
            calls["stage1"] += 1
            return next(outcomes)

        def fake_stage2(*args, **kwargs):
            calls["stage2"] += 1
            return None, None

        with mock.patch.object(p, "load_resources", return_value=synthetic_resources()), mock.patch.object(
            p, "replay_c9cb_stage1", side_effect=fake_stage1
        ), mock.patch.object(
            p, "flash_secondary_stage2", side_effect=fake_stage2
        ), mock.patch.object(
            p, "find_normal_device", return_value=p.NormalDevicePaths(None, b"/dev/fake", "Hansker Per")
        ), mock.patch.object(p.time, "sleep", return_value=None):
            result = p.flash_full(max_attempts=3, log=lambda _msg: None, post_verify=False)

        self.assertEqual(result.stage1_attempts, 2)
        self.assertEqual(calls, {"stage1": 2, "stage2": 2})


if __name__ == "__main__":
    unittest.main()
