import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from nju_connect import clash


class VergeRestartTest(unittest.TestCase):
    """restart_verge() must stop Clash Verge and start it again with the same argv and env.

    Every call is limited to the stand-in executable, so a real Clash Verge is never touched.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.exe = Path(self.tmp) / "clash-verge"            # stand-in: a renamed `sleep`
        shutil.copy(shutil.which("sleep"), self.exe)
        self.addCleanup(self.kill_all)

    def kill_all(self):
        for pid, *_ in clash.verge_processes(only_exe=self.exe):
            os.kill(pid, 9)

    def mine(self):
        return clash.verge_processes(only_exe=self.exe)

    def test_restart_keeps_argv_and_environment(self):
        old = subprocess.Popen([str(self.exe), "300"], env=dict(os.environ, NJU_TEST_DISPLAY=":42"))
        time.sleep(0.2)
        self.assertEqual([p[0] for p in self.mine()], [old.pid])
        self.assertTrue(clash.restart_verge(timeout=5, only_exe=self.exe))
        old.wait(timeout=5)                                    # the old one was stopped
        time.sleep(0.3)
        (pid, exe, argv, environ), = self.mine()
        self.assertNotEqual(pid, old.pid)
        self.assertEqual(argv[1:], ["300"])
        self.assertEqual(environ.get("NJU_TEST_DISPLAY"), ":42")

    def test_nothing_to_restart(self):
        self.assertFalse(clash.restart_verge(only_exe=self.exe))


if __name__ == "__main__":
    unittest.main()
