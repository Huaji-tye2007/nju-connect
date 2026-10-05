import os
import shutil
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nju_connect import clients, paths


def proc(argv, cwd="/", unit=None, uid=None, pid=4242):
    return clients.Process(pid, "/usr/bin/" + Path(argv[0]).name, argv, cwd,
                           os.getuid() if uid is None else uid, unit)


class UnitTest(unittest.TestCase):
    def test_unit_of_cgroup(self):
        self.assertEqual(clients.unit_of("0::/user.slice/user-1000.slice/user@1000.service/app.slice/xray.service"),
                         ("user", "xray.service"))
        self.assertEqual(clients.unit_of("0::/system.slice/sing-box.service\n"), ("system", "sing-box.service"))
        # a desktop app, and the user manager itself, are no unit
        self.assertIsNone(clients.unit_of("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                                          "app-gnome-mihomo-123.scope"))
        self.assertIsNone(clients.unit_of("0::/user.slice/user-1000.slice/user@1000.service/init.scope"))
        # cgroup v1: several hierarchies
        self.assertEqual(clients.unit_of("12:pids:/system.slice/xray.service\n1:name=systemd:/system.slice/xray.service"),
                         ("system", "xray.service"))

    def test_flag_values(self):
        argv = ["xray", "run", "-c", "a.json", "--c=b.json", "-config", "c.json", "-confdir=/etc/x", "-test"]
        self.assertEqual(clients.flag_values(argv, "c", "config"), ["a.json", "b.json", "c.json"])
        self.assertEqual(clients.flag_values(argv, "confdir"), ["/etc/x"])
        self.assertEqual(clients.flag_values(["sing-box", "-D", "/var/lib/sb", "-C", "/etc/sb", "run"], "C"),
                         ["/etc/sb"])


class ConfigsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_xray(self):
        (self.tmp / "conf.d").mkdir()
        (self.tmp / "conf.d/b.json").write_text("{}")
        (self.tmp / "conf.d/a.json").write_text("{}")
        p = proc(["xray", "run", "-c", "main.json", "-confdir", "conf.d"], cwd=str(self.tmp))
        self.assertEqual(clients.configs_of("xray", p),
                         [self.tmp / "main.json", self.tmp / "conf.d/a.json", self.tmp / "conf.d/b.json"])
        self.assertEqual(clients.configs_of("xray", proc(["xray", "run"], cwd=str(self.tmp))),
                         [self.tmp / "config.json"])   # Xray's default, in its working directory

    def test_gui_cores_are_left_out(self):
        v2rayn = proc(["xray", "run", "-c", "/home/u/.local/share/v2rayN/binConfigs/config.json"])
        self.assertEqual(clients.configs_of("xray", v2rayn), [])
        party = proc(["mihomo", "-d", "/home/u/.config/mihomo-party/work"])
        self.assertEqual(clients.configs_of("mihomo", party), [])
        self.assertFalse(clients.CORES["mihomo"]["match"]("verge-mihomo"))   # Clash Verge Rev's core
        self.assertFalse(clients.CORES["mihomo"]["match"]("FlClashCore"))

    def test_sing_box_directory_and_working_directory(self):
        (self.tmp / "sb").mkdir()
        (self.tmp / "sb/config.json").write_text("{}")
        p = proc(["sing-box", "-D", str(self.tmp), "-C", "sb", "run"], cwd=str(self.tmp))
        self.assertEqual(clients.configs_of("sing-box", p), [self.tmp / "sb/config.json"])

    def test_mihomo_home(self):
        self.assertEqual(clients.configs_of("mihomo", proc(["mihomo", "-d", "/srv/mihomo"])), [Path("/srv/mihomo")])
        self.assertEqual(clients.configs_of("mihomo", proc(["mihomo"])), [paths.MIHOMO_DIR])   # ours, no -d

    def test_find_and_choose(self):
        a, b = self.tmp / "a.json", self.tmp / "b.json"
        a.write_text("{}")
        b.write_text("{}")
        with mock.patch.object(clients, "processes", return_value=[proc(["sing-box", "run", "-c", str(a)])]):
            self.assertEqual(clients.choose_config("sing-box"), a)
            self.assertEqual(clients.choose_config("sing-box", "~/x.json"), Path.home() / "x.json")   # -o wins
        two = [proc(["sing-box", "run", "-c", str(a)]), proc(["sing-box", "run", "-c", str(b)], pid=7)]
        with mock.patch.object(clients, "processes", return_value=two), \
                self.assertRaisesRegex(RuntimeError, "several sing-box configs"):
            clients.choose_config("sing-box")
        fallback = dict(clients.CORES["xray"], fallback=[self.tmp / "missing.json", a])
        with mock.patch.object(clients, "processes", return_value=[]), \
                mock.patch.dict(clients.CORES, {"xray": fallback}):
            self.assertEqual(clients.choose_config("xray"), a)   # nothing running: the usual places
        fallback = dict(clients.CORES["xray"], fallback=[self.tmp / "missing.json"])
        with mock.patch.object(clients, "processes", return_value=[]), \
                mock.patch.dict(clients.CORES, {"xray": fallback}), \
                self.assertRaisesRegex(RuntimeError, "use -o PATH"):
            clients.choose_config("xray")

    @unittest.skipIf(os.getuid() == 0, "root can write anywhere")
    def test_require_writable(self):
        clients.require_writable(self.tmp / "new/sub/config.json")   # created later, under a writable folder
        locked = self.tmp / "locked"
        locked.mkdir()
        (locked / "config.json").write_text("{}")
        os.chmod(locked / "config.json", 0o400)
        self.addCleanup(os.chmod, locked / "config.json", 0o600)
        with self.assertRaisesRegex(RuntimeError, "not writable"):
            clients.require_writable(locked / "config.json")


class ReloadTest(unittest.TestCase):
    TARGET = Path("/srv/sb/config.json")

    def reload(self, procs, core="sing-box", confirm=None):
        env = {k: v for k, v in os.environ.items() if k != "NJU_CONNECT_CONFIG_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clients, "running", return_value=procs), \
                mock.patch.object(clients.shutil, "which", return_value="/usr/bin/systemctl"), \
                mock.patch.object(clients.subprocess, "run") as run, \
                mock.patch.object(clients.os, "kill") as kill:
            result = clients.reload(core, self.TARGET, confirm)
        return result, run, kill

    def test_user_service_and_manual_process(self):
        procs = [proc(["sing-box", "run", "-c", str(self.TARGET)], unit=("user", "sing-box.service")),
                 proc(["sing-box", "run", "-c", str(self.TARGET)], pid=99),
                 proc(["sing-box", "run", "-c", "/other.json"], unit=("user", "other.service"), pid=7)]
        (done, message), run, kill = self.reload(procs)
        self.assertTrue(done)
        run.assert_called_once_with(["systemctl", "--user", "reload-or-restart", "sing-box.service"], timeout=60)
        kill.assert_called_once_with(99, signal.SIGHUP)

    def test_xray_user_service_is_restarted(self):
        procs = [proc(["xray", "run", "-c", str(self.TARGET)], unit=("user", "xray.service"))]
        (done, _), run, kill = self.reload(procs, core="xray")
        run.assert_called_once_with(["systemctl", "--user", "restart", "xray.service"], timeout=60)
        kill.assert_not_called()   # only sing-box reloads on SIGHUP

    def test_system_service_needs_root(self):
        procs = [proc(["sing-box", "run", "-c", str(self.TARGET)], unit=("system", "sing-box.service"), uid=0)]
        (done, message), run, kill = self.reload(procs)
        self.assertFalse(done)
        self.assertIn("sudo systemctl reload-or-restart sing-box.service", message)
        run.assert_not_called()

    def test_declined(self):
        procs = [proc(["sing-box", "run", "-c", str(self.TARGET)], unit=("user", "sing-box.service"))]
        (done, _), run, _ = self.reload(procs, confirm=lambda prompt: False)
        self.assertFalse(done)
        run.assert_not_called()

    def test_sandbox_never_reloads(self):
        with mock.patch.object(clients, "running") as running:
            self.assertFalse(clients.reload("sing-box", self.TARGET)[0])   # NJU_CONNECT_CONFIG_DIR is set
        running.assert_not_called()


if __name__ == "__main__":
    unittest.main()
