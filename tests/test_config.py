import unittest
from unittest import mock

from nju_connect import config, configure, paths

BASE = {
    "protocol": "atrust",
    "server_address": "vpn.nju.edu.cn",
    "server_port": 443,
    "username": "251220100",
    "password": "secret",
    "login_domain": "openldap13924",
    "disable_zju_config": True,
    "socks_bind": "127.0.0.1:1080",
    "http_bind": "127.0.0.1:1081",
    "phone": "13300000000",
}


class OptionTest(unittest.TestCase):
    def setUp(self):
        config.write_config(dict(BASE))
        if paths.SETTINGS_FILE.exists():
            paths.SETTINGS_FILE.unlink()

    def value(self, key):
        return config.option(key).get(config.load_config(), config.load_settings())

    def test_write_config_drops_phone_and_is_private(self):
        self.assertNotIn("phone", config.load_config())
        self.assertEqual(paths.CONFIG_TOML.stat().st_mode & 0o777, 0o600)

    def test_settings_round_trip(self):
        effects = config.set_options({"daemon.check_interval": "30", "export.health_interval": "600",
                                      "export.group_type": "url-test"})
        self.assertEqual(effects, {config.RESTART, config.EXPORTS})
        self.assertEqual(self.value("daemon.check_interval"), "30")
        self.assertEqual(self.value("export.health_interval"), "600")
        self.assertEqual(config.load_settings()["export"]["group_type"], "url-test")

    def test_port_is_stored_as_localhost_bind(self):
        self.assertEqual(config.set_options({"proxy.socks_port": "2080"}), {config.RESTART, config.EXPORTS})
        self.assertEqual(config.load_config()["socks_bind"], "127.0.0.1:2080")
        self.assertEqual(self.value("proxy.socks_port"), 2080)

    def test_server_port_is_a_plain_number(self):
        self.assertEqual(self.value("server.port"), 443)
        config.set_options({"server.port": "8443"})
        self.assertEqual(config.load_config()["server_port"], 8443)
        with self.assertRaises(ValueError):
            config.set_options({"server.port": "70000"})

    def test_service_is_left_alone_with_a_custom_config_dir(self):
        with mock.patch.object(configure.service, "is_active", return_value=True), \
                mock.patch.object(configure.service, "systemctl") as systemctl:
            self.assertFalse(configure.service.restart_if_active())
        systemctl.assert_not_called()

    def test_unchanged_value_has_no_effects(self):
        self.assertEqual(config.set_options({"daemon.check_interval": "60",
                                             "proxy.socks_port": "1080"}), set())

    def test_invalid_values_are_rejected_without_writing(self):
        before = paths.CONFIG_TOML.read_text()
        for key, value in (("proxy.socks_port", "0"), ("daemon.check_interval", "5"),
                           ("daemon.ruleset_interval", "abc"), ("export.group_type", "foo"),
                           ("export.health_url", "lib.nju.edu.cn"), ("export.group_name", "A,B"),
                           ("export.proxy_name", "DIRECT"), ("campus.dns_servers", "10.0.0.1, ::1"),
                           ("account.username", "")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                config.set_options({"daemon.check_interval": "30", key: value})
        self.assertEqual(paths.CONFIG_TOML.read_text(), before)
        self.assertFalse(paths.SETTINGS_FILE.exists())

    def test_dns_server_list_is_normalised(self):
        config.set_options({"campus.dns_servers": "10.1.1.1 ,10.2.2.2"})
        self.assertEqual(self.value("campus.dns_servers"), "10.1.1.1, 10.2.2.2")

    def test_set_value_applies_effects(self):
        with mock.patch.object(configure, "apply_effects") as apply, \
                mock.patch.object(configure, "port_in_use", return_value=False):
            configure.set_value("proxy.http_port", "2081")
        apply.assert_called_once_with({config.RESTART, config.EXPORTS})
        self.assertEqual(config.load_config()["http_bind"], "127.0.0.1:2081")

    def test_set_value_refuses_busy_port(self):
        with mock.patch.object(configure, "port_in_use", return_value=True), \
                self.assertRaises(SystemExit):
            configure.set_value("proxy.socks_port", "2080")

    def test_apply_effects(self):
        paths.RESOURCE.parent.mkdir(parents=True, exist_ok=True)
        paths.RESOURCE.write_text("{}")
        with mock.patch.object(configure, "refresh_exports") as refresh, \
                mock.patch.object(configure.service, "restart_if_active", return_value=False) as restart:
            configure.apply_effects({config.EXPORTS})
            refresh.assert_called_once()
            restart.assert_not_called()
            configure.apply_effects({config.RESTART})
            restart.assert_called_once()

    def test_mode_option(self):
        self.assertEqual(self.value("daemon.mode"), "auto")
        self.assertEqual(config.set_options({"daemon.mode": "always"}), {config.RESTART})
        with self.assertRaises(ValueError):
            config.set_options({"daemon.mode": "sometimes"})

    def test_v04_settings_are_migrated(self):
        script = paths.CONFIG_DIR / "Script.js"
        script.write_text("// Generated by nju-connect 0.4.0\n")
        paths.SETTINGS_FILE.write_text(
            "[daemon]\ncheck_interval = 30\n\n[ruleset]\noutput = /tmp/x/nju-vpn.yaml\n"
            "provider_path = ./ruleset/nju-vpn.yaml\n\n[clash]\nproxy_name = Campus\n"
            f"group_name = NJU\nscript = {script}\n")
        settings = config.load_settings()
        self.assertEqual(settings["export"]["proxy_name"], "Campus")
        self.assertEqual(dict(settings["exports"]), {"clash": "/tmp/x/nju-vpn.yaml",
                                                     "clash-verge": str(script)})
        self.assertEqual(settings["daemon"]["check_interval"], "30")
        self.assertNotIn("[clash]", paths.SETTINGS_FILE.read_text())

    def test_password_is_masked(self):
        self.assertEqual(configure.display(config.option("account.password"), "secret"), "********")


if __name__ == "__main__":
    unittest.main()
