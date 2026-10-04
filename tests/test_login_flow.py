import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from nju_connect import config, daemon, paths, zju

SMS_FAILURE = """#!/bin/sh
echo "Perform GET /passport/v1/auth/sms"
printf "Please enter the SMS verification code: "
echo "Login error: complete secondary SMS challenge: EOF"
exit 1
"""

LOGIN_OK = """#!/bin/sh
sleep 1
printf '{"cookies": []}' > "$NJU_TEST_CLIENT_DATA"
exec sleep 30
"""


_FAKES = []


def tearDownModule():
    for path in _FAKES:
        os.unlink(path)


def fake_zju(body):
    fd, path = tempfile.mkstemp(suffix="-zju-connect")
    _FAKES.append(path)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.chmod(path, stat.S_IRWXU)
    return path


class LoginFlowTest(unittest.TestCase):
    def setUp(self):
        config.write_config({"server_address": "vpn.nju.edu.cn", "username": "u",
                             "socks_bind": "127.0.0.1:1"})
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        if paths.CLIENT_DATA.exists():
            paths.CLIENT_DATA.unlink()

    def supervisor(self, binary):
        patches = [mock.patch.object(daemon, "on_campus", return_value=False),
                   mock.patch.object(daemon, "port_in_use", return_value=False),
                   mock.patch.object(daemon, "vpn_healthy", return_value=False),
                   mock.patch.object(daemon, "update_policy", return_value=([], {})),
                   mock.patch.object(daemon, "refresh_exports", return_value=0),
                   mock.patch.object(daemon.paths, "zju_connect_binary", return_value=binary)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return daemon.Supervisor(config.load_settings(), config.load_config())

    def test_no_session_means_no_login_attempt(self):
        s = self.supervisor(fake_zju(SMS_FAILURE))
        s.tick()
        self.assertIsNone(s.proc)

    def test_sms_prompt_stops_retries_until_a_new_login(self):
        paths.CLIENT_DATA.write_text("{}")
        s = self.supervisor(fake_zju(SMS_FAILURE))
        with mock.patch.object(daemon, "notify") as notify:
            s.tick()                        # starts zju-connect, which asks for an SMS code
            s.proc.wait(timeout=10)
            time.sleep(0.2)                 # let the output pump catch up
            s.tick()                        # reaps it: must not retry
            self.assertIsNone(s.proc)
            self.assertIsNotNone(s.login_mark)
            notify.assert_called_once()
            s.retry_at = 0
            s.tick()
            self.assertIsNone(s.proc)
            os.utime(paths.CLIENT_DATA, (time.time() + 5, time.time() + 5))   # `nju-connect login`
            s.tick()
            self.assertIsNotNone(s.proc)
            s.stop("test")

    def test_interactive_login_stops_after_the_session_is_saved(self):
        binary = fake_zju(LOGIN_OK)
        with mock.patch.object(zju.paths, "zju_connect_binary", return_value=binary), \
                mock.patch.dict(os.environ, {"NJU_TEST_CLIENT_DATA": str(paths.CLIENT_DATA)}):
            started = time.monotonic()
            self.assertTrue(zju.interactive_login(timeout=20))
        self.assertLess(time.monotonic() - started, 15)   # stopped, not waiting for `sleep 30`
        self.assertTrue(paths.CLIENT_DATA.exists())

    def test_always_mode_skips_the_campus_probe(self):
        paths.CLIENT_DATA.write_text("{}")
        s = self.supervisor(fake_zju(LOGIN_OK))
        s.always = True
        with mock.patch.object(daemon, "on_campus", side_effect=AssertionError("probed")):
            s.tick()
        self.assertIsNotNone(s.proc)
        s.stop("test")


if __name__ == "__main__":
    unittest.main()
