import os
import unittest
from unittest import mock

from nju_connect import configure, service, zju


class SessionBeforeStartTest(unittest.TestCase):
    """`service start/enable/restart` must not hand an expired session to the service."""

    def run_require(self, state, tty=True, login_ok=True):
        with mock.patch.object(service, "check_session", return_value=state), \
                mock.patch.object(service.sys.stdin, "isatty", return_value=tty), \
                mock.patch.object(configure, "login", return_value=login_ok) as login, \
                mock.patch("builtins.print"):
            try:
                service._require_session()
                exited = False
            except SystemExit:
                exited = True
        return exited, login.called

    def test_valid_session_starts_without_login(self):
        self.assertEqual(self.run_require("valid"), (False, False))

    def test_unknown_state_starts_anyway(self):
        self.assertEqual(self.run_require("unknown"), (False, False))

    def test_expired_session_logs_in_first(self):
        self.assertEqual(self.run_require("expired"), (False, True))

    def test_missing_session_logs_in_first(self):
        self.assertEqual(self.run_require("missing"), (False, True))

    def test_failed_login_does_not_start(self):
        self.assertEqual(self.run_require("expired", login_ok=False), (True, True))

    def test_no_terminal_refuses(self):
        self.assertEqual(self.run_require("expired", tty=False), (True, False))

    def test_check_session_states(self):
        with mock.patch.object(zju.paths, "CLIENT_DATA") as client_data:
            client_data.exists.return_value = False
            self.assertEqual(zju.check_session(), "missing")
            client_data.exists.return_value = True
            for error, state in (("Fetch resource error: login method is nil, but user is not logged in", "expired"),
                                 ("dial tcp: lookup vpn.nju.edu.cn: no such host", "unknown")):
                with mock.patch.object(zju, "download_resource", side_effect=RuntimeError(error)):
                    self.assertEqual(zju.check_session(), state)


if __name__ == "__main__":
    unittest.main()
