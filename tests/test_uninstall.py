import argparse
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nju_connect import cli, config, paths, zju


def fake_zju(directory, body):
    path = Path(directory) / "zju-connect"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(stat.S_IRWXU)
    return str(path)


class UntrustTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config.write_config({"server_address": "vpn.nju.edu.cn", "username": "u"})
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.CLIENT_DATA.write_text("{}")

    def untrust(self, body):
        with mock.patch.object(zju.paths, "zju_connect_binary", return_value=fake_zju(self.tmp.name, body)):
            return zju.untrust_device()

    def test_untrusted(self):
        self.assertEqual(self.untrust('echo "Device untrusted successfully" >&2\n'), (True, "done"))

    def test_not_trusted_counts_as_success(self):
        result = self.untrust('echo "Device already untrusted, skipping" >&2\n'
                              'echo "Device untrusted successfully" >&2\n')
        self.assertEqual(result, (True, "it was not trusted"))

    def test_failure_reports_the_error(self):
        result = self.untrust('echo "Perform GET /passport/v1/public/authConfig" >&2\n'
                              'echo "Trust/Untrust device error: login method is nil" >&2\nexit 1\n')
        self.assertEqual(result, (False, "Trust/Untrust device error: login method is nil"))

    def uninstall(self, untrust_result, keep_trust=False):
        exe = Path(self.tmp.name) / "nju-connect"
        binary = Path(self.tmp.name) / "zju-connect-bin"
        exe.write_text("")
        binary.write_text("")
        paths.UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.UNIT_FILE.write_text("[Service]\n")
        calls = []
        with mock.patch.object(cli.paths, "installed_path", return_value=exe), \
                mock.patch.object(cli.paths, "zju_connect_binary", return_value=str(binary)), \
                mock.patch.object(cli, "untrust_device",
                                  side_effect=lambda: calls.append("untrust") or untrust_result), \
                mock.patch.object(cli.service, "disable", side_effect=lambda: calls.append("disable")), \
                mock.patch("builtins.print"):
            cli.cmd_uninstall(argparse.Namespace(purge=False, keep_trust=keep_trust))
        return calls, exe.exists(), binary.exists()

    def test_uninstall_untrusts_first(self):
        calls, exe_left, binary_left = self.uninstall((True, "done"))
        self.assertEqual(calls, ["untrust", "disable"])
        self.assertFalse(exe_left or binary_left)

    def test_uninstall_continues_when_untrust_fails(self):
        calls, exe_left, binary_left = self.uninstall((False, "session expired"))
        self.assertEqual(calls, ["untrust", "disable"])
        self.assertFalse(exe_left or binary_left)

    def test_keep_trust(self):
        calls, _, _ = self.uninstall((True, "done"), keep_trust=True)
        self.assertEqual(calls, ["disable"])


if __name__ == "__main__":
    unittest.main()
