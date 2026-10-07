import argparse
import unittest
from unittest import mock

from hansker_updater import cli
from tests.helpers import synthetic_resources


class TestCli(unittest.TestCase):
    def test_plan_is_offline(self):
        resources = synthetic_resources()
        with mock.patch.object(cli, "load_resources", return_value=resources), mock.patch(
            "hansker_updater.protocol._import_hid", side_effect=AssertionError("USB touched")
        ):
            self.assertEqual(cli.main(["plan"]), 0)


    def test_probe_warns_about_input_side_effect(self):
        with mock.patch.object(cli, "probe_version", return_value="MS1000V2.23H"), mock.patch(
            "builtins.print"
        ) as print_mock:
            self.assertEqual(cli.main(["probe"]), 0)
        output = "\n".join(" ".join(str(v) for v in call.args) for call in print_mock.call_args_list)
        self.assertIn("temporarily", output)
        self.assertIn("toggle away from Wired", output)
        self.assertIn("already at target", output)

    def test_isp_status_is_read_only(self):
        loc = cli.find_isp_location.__globals__["UsbLocation"](1, 30, 0)
        with mock.patch.object(cli, "find_isp_location", return_value=loc):
            self.assertEqual(cli.main(["isp-status"]), 0)

    def test_recover_refuses_without_isp(self):
        with mock.patch.object(cli, "load_resources", return_value=synthetic_resources()), mock.patch.object(
            cli, "find_isp_location", return_value=None
        ), mock.patch.object(cli.os, "geteuid", return_value=0):
            self.assertEqual(cli.main(["recover-isp", "--yes"]), 1)



    def test_doctor_refuses_preexisting_generic_isp(self):
        resources = synthetic_resources()
        paths = cli.find_normal_device.__globals__["NormalDevicePaths"](
            b"/dev/hidraw10", b"/dev/hidraw11", "Hansker Per"
        )
        loc = cli.find_isp_location.__globals__["UsbLocation"](1, 30, 0)
        with mock.patch.object(cli, "load_resources", return_value=resources), mock.patch.object(
            cli, "find_normal_device", return_value=paths
        ), mock.patch.object(cli, "usb_topology", return_value=(True, "direct to host/root hub")), mock.patch.object(
            cli, "find_isp_location", return_value=loc
        ):
            self.assertEqual(cli.main(["doctor"]), 2)

    def test_flash_refuses_preexisting_generic_isp(self):
        resources = synthetic_resources()
        paths = cli.find_normal_device.__globals__["NormalDevicePaths"](
            b"/dev/hidraw10", b"/dev/hidraw11", "Hansker Per"
        )
        loc = cli.find_isp_location.__globals__["UsbLocation"](1, 30, 0)
        with mock.patch.object(cli, "load_resources", return_value=resources), mock.patch.object(
            cli, "find_normal_device", return_value=paths
        ), mock.patch.object(cli, "usb_topology", return_value=(True, "direct to host/root hub")), mock.patch.object(
            cli, "find_isp_location", return_value=loc
        ), mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli, "flash_full"
        ) as flash_mock:
            self.assertEqual(cli.main(["flash", "--yes"]), 1)
        flash_mock.assert_not_called()

    def test_flash_refuses_unknown_topology_by_default(self):
        resources = synthetic_resources()
        paths = cli.find_normal_device.__globals__["NormalDevicePaths"](
            b"/dev/hidraw10", b"/dev/hidraw11", "Hansker Per"
        )
        with mock.patch.object(cli, "load_resources", return_value=resources), mock.patch.object(
            cli, "find_normal_device", return_value=paths
        ), mock.patch.object(cli, "usb_topology", return_value=(None, "unknown")), mock.patch.object(
            cli.os, "geteuid", return_value=0
        ), mock.patch.object(cli, "flash_full") as flash_mock:
            self.assertEqual(cli.main(["flash", "--yes"]), 1)
        flash_mock.assert_not_called()

    def test_recover_confirmation_is_short(self):
        args = argparse.Namespace(yes=False)
        with mock.patch("builtins.input", return_value="RECOVER HANSKER") as input_mock:
            cli._confirm_recover_isp(args)
        self.assertIn("RECOVER HANSKER", input_mock.call_args.args[0])
        self.assertNotIn("MS1000", input_mock.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
