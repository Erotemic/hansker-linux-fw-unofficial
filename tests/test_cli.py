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

    def test_isp_status_is_read_only(self):
        loc = cli.find_isp_location.__globals__["UsbLocation"](1, 30, 0)
        with mock.patch.object(cli, "find_isp_location", return_value=loc):
            self.assertEqual(cli.main(["isp-status"]), 0)

    def test_recover_refuses_without_isp(self):
        with mock.patch.object(cli, "load_resources", return_value=synthetic_resources()), mock.patch.object(
            cli, "find_isp_location", return_value=None
        ), mock.patch.object(cli.os, "geteuid", return_value=0):
            self.assertEqual(cli.main(["recover-isp", "--yes"]), 1)

    def test_recover_confirmation_is_short(self):
        args = argparse.Namespace(yes=False)
        with mock.patch("builtins.input", return_value="RECOVER HANSKER") as input_mock:
            cli._confirm_recover_isp(args)
        self.assertIn("RECOVER HANSKER", input_mock.call_args.args[0])
        self.assertNotIn("MS1000", input_mock.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
