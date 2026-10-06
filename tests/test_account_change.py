import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from nju_connect import config, configure, paths, service


class AccountChangeTest(unittest.TestCase):
    """A saved session belongs to an account and a server: changing them means logging in again."""

    def setUp(self):
        config.write_config({"server_address": "vpn.nju.edu.cn", "username": "u", "password": "p",
                             "socks_bind": "127.0.0.1:2080"})
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.CLIENT_DATA.write_text('{"cookies": []}')
        self.addCleanup(self.cleanup)
        patcher = mock.patch.object(service, "restart_if_active", return_value=False)   # no real service
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def cleanup():
        for path in paths.STATE_DIR.glob(paths.CLIENT_DATA.name + "*"):
            path.unlink()

    def set(self, key, value):
        out = io.StringIO()
        with redirect_stdout(out):
            configure.set_value(key, value)
        return out.getvalue()

    def backups(self):
        return list(paths.STATE_DIR.glob(paths.CLIENT_DATA.name + ".bak-*"))

    def test_username_sets_the_session_aside(self):
        text = self.set("account.username", "other")
        self.assertFalse(paths.CLIENT_DATA.exists())
        self.assertEqual(len(self.backups()), 1)          # moved, not deleted
        self.assertIn("nju-connect login", text)

    def test_server_too(self):
        self.set("server.address", "vpn.example.edu.cn")
        self.assertFalse(paths.CLIENT_DATA.exists())

    def test_password_keeps_the_session(self):
        text = self.set("account.password", "new")
        self.assertTrue(paths.CLIENT_DATA.exists())
        self.assertIn("stays valid", text)

    def test_unchanged_value_does_nothing(self):
        self.set("account.username", "u")
        self.assertTrue(paths.CLIENT_DATA.exists())
        self.assertEqual(self.backups(), [])

    def test_account_effects(self):
        old = {"username": "u", "password": "p", "server_address": "vpn.nju.edu.cn"}
        self.assertEqual(configure.account_effects(old, dict(old, username="v")), {config.LOGIN})
        self.assertEqual(configure.account_effects(old, dict(old, server_port=8443)), {config.LOGIN})
        self.assertEqual(configure.account_effects(old, dict(old, password="q")), {config.PASSWORD})
        self.assertEqual(configure.account_effects(old, dict(old, socks_bind="127.0.0.1:1")), set())

    def test_setup_asks_to_log_in_after_a_new_username(self):
        new = dict(config.load_config(), username="other")
        with mock.patch.object(configure, "ask_basic", return_value=new), \
                mock.patch.object(configure, "ask_yes", side_effect=[True, False]) as ask_yes, \
                mock.patch.object(configure.shutil, "which", return_value=None), \
                redirect_stdout(io.StringIO()) as out:
            configure.setup()
        # 1: reconfigure? 2: log in now? (asked because the old session was set aside)
        self.assertEqual(ask_yes.call_count, 2)
        self.assertIn("Log in now?", ask_yes.call_args_list[1].args[0])
        self.assertFalse(paths.CLIENT_DATA.exists())
        self.assertIn("nju-connect login", out.getvalue())


if __name__ == "__main__":
    unittest.main()
