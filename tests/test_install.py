import configparser
import io
import json
import os
from contextlib import redirect_stdout
from unittest import mock

from nju_connect import clash, clients, config, exporters, paths, singbox, util
from tests.test_policy_and_exports import ExportFixture

SING_BOX_CONFIG = """{
  // a hand-written config
  "dns": {"servers": [{"type": "udp", "tag": "dns", "server": "223.5.5.5"}]},
  "outbounds": [{"type": "vless", "tag": "proxy"}, {"type": "direct", "tag": "direct-out"}],
  "route": {
    "default_domain_resolver": "dns",
    "rule_set": [{"type": "remote", "tag": "geosite-cn", "url": "https://example.com/cn.srs"}],
    "rules": [{"rule_set": ["nju-vpn", "geosite-cn"], "outbound": "proxy"},
              {"rule_set": "geosite-cn", "outbound": "direct-out"}]
  }
}
"""


class MihomoInstallTest(ExportFixture):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "mihomo"
        self.home.mkdir()
        (self.home / "config.yaml").write_text("mode: rule\n")
        if exporters.SNIPPETS.exists():
            exporters.SNIPPETS.unlink()

    def install(self, color=False):
        env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "PYTHON_COLORS")}
        env["FORCE_COLOR" if color else "NO_COLOR"] = "1"
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out):
            exporters.export("clash-config", self.home / "config.yaml", install=True)   # the file: its folder
        return out.getvalue()

    def test_files_and_steps(self):
        text = self.install()
        self.assertEqual(exporters.remembered(), {"clash-config": exporters.INSTALL + str(self.home)})
        self.assertIn("'IP-CIDR,219.219.118.25/32,no-resolve'", (self.home / "ruleset/nju-direct.yaml").read_text())
        self.assertIn("'IP-CIDR,114.212.0.0/16,no-resolve'", (self.home / "ruleset/nju-vpn.yaml").read_text())
        self.assertIn('"port": 2080', (self.home / "proxies/nju-connect.yaml").read_text())
        self.assertEqual((self.home / "config.yaml").read_text(), "mode: rule\n")   # never touched
        for expected in ("① Under proxy-providers:", "② Under proxy-groups:", "③ Under rule-providers:",
                         "④ At the TOP of rules:", "    - RULE-SET,nju-direct,DIRECT", "    - RULE-SET,nju-vpn,NJU",
                         '"use": ["nju-connect"]', f"Merge into {self.home / 'config.yaml'} once"):
            self.assertIn(expected, text)
        self.assertNotIn("\033[", text)
        self.assertNotIn("The same as last time", text)
        self.assertIn("The same as last time", self.install())

    def test_colors_never_in_the_lines_to_copy(self):
        text = self.install(color=True)
        self.assertIn("\033[", text)
        copied = [line for line in text.splitlines() if line.startswith("    ") and ("{" in line or "RULE-SET" in line)]
        self.assertEqual(len(copied), 6)
        self.assertFalse(any("\033[" in line for line in copied))

    def test_refresh_notifies_only_when_config_yaml_must_change(self):
        self.install()
        with mock.patch.object(exporters, "notify") as notify:
            config.set_options({"proxy.socks_port": 2090})   # in the proxy file: nothing to merge
            self.addCleanup(config.set_options, {"proxy.socks_port": 2080})
            exporters.refresh_exports(quiet=True)            # what `config set` does next (EXPORTS effect)
            self.assertIn('"port": 2090', (self.home / "proxies/nju-connect.yaml").read_text())
            notify.assert_not_called()
            config.set_options({"export.group_name": "NJU2"})   # in config.yaml: merge again
            self.addCleanup(config.set_options, {"export.group_name": "NJU"})
            exporters.refresh_exports(quiet=True)
        self.assertTrue(any("merge it again" in c.args[0] for c in notify.call_args_list))


class SingBoxInstallTest(ExportFixture):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / "config.json"
        self.conf.write_text(SING_BOX_CONFIG)
        for target, name, value in ((singbox, "sing_box_binary", None), (clients, "running", [])):
            patcher = mock.patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("builtins.print")   # "Backed up …"
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self):
        with mock.patch("builtins.print"):
            exporters.export("sing-box-config", self.conf, install=True)
        return json.loads(self.conf.read_text())

    def test_merge(self):
        data = self.install()
        self.assertEqual([o["tag"] for o in data["outbounds"]], ["proxy", "direct-out", "NJUConnect"])
        route = data["route"]
        folder = self.tmp / singbox.FILES_DIR
        self.assertEqual(route["rule_set"][0]["tag"], "geosite-cn")
        self.assertEqual({r["tag"]: r["path"] for r in route["rule_set"][1:]},
                         {name: str(folder / f"{name}.json") for name in singbox.RULE_SETS})
        self.assertEqual(route["rules"], [
            {"rule_set": "nju-direct", "outbound": "direct-out"},   # the user's direct outbound
            {"rule_set": "nju-vpn", "outbound": "NJUConnect"},
            {"rule_set": "nju-resolve", "action": "resolve"},
            {"rule_set": "nju-vpn", "outbound": "NJUConnect"},
            {"rule_set": "geosite-cn", "outbound": "direct-out"}])   # a rule using nju-vpn counts as ours
        self.assertEqual(route["default_domain_resolver"], "dns")
        direct = json.loads((folder / "nju-direct.json").read_text())
        self.assertEqual(direct["rules"], [{"domain": ["vpn.nju.edu.cn"]}, {"ip_cidr": ["219.219.118.25/32"]}])
        self.assertEqual(json.loads((folder / "nju-resolve.json").read_text())["rules"],
                         [{"domain_suffix": ["nju.edu.cn"]}])
        self.assertEqual(len(list(self.tmp.glob("config.json.bak-*"))), 1)
        self.assertEqual(exporters.remembered(), {"sing-box-config": exporters.INSTALL + str(self.conf)})

    def test_again_is_idempotent_and_policy_changes_need_no_reload(self):
        first = self.install()
        self.assertEqual(self.install(), first)
        (self.tmp / singbox.FILES_DIR / "nju-vpn.json").write_text("{}")   # an older policy
        with mock.patch.object(clients, "reload") as reload:
            exporters.refresh_exports(quiet=True)
        reload.assert_not_called()   # sing-box watches its local rule sets
        self.assertIn("ip_cidr", (self.tmp / singbox.FILES_DIR / "nju-vpn.json").read_text())

    def test_resolve_domains(self):
        config.set_options({"export.resolve_domains": ""})
        self.addCleanup(config.set_options, {"export.resolve_domains": "nju.edu.cn"})
        data = self.install()
        self.assertEqual([r.get("rule_set") for r in data["route"]["rules"][:3]],
                         ["nju-direct", "nju-vpn", "geosite-cn"])
        config.set_options({"export.resolve_domains": "*"})
        self.install()
        self.assertEqual(json.loads((self.tmp / singbox.FILES_DIR / "nju-resolve.json").read_text())["rules"],
                         [{"domain_regex": [".*"]}])

    def test_adds_a_direct_outbound_when_missing(self):
        self.conf.write_text('{"outbounds": [{"type": "vless", "tag": "proxy"}]}')
        data = self.install()
        self.assertEqual([o["tag"] for o in data["outbounds"]], ["proxy", "direct", "NJUConnect"])

    def test_rejected_config_is_left_unchanged(self):
        def check(binary, args, content, what):
            if "NJUConnect" in content:
                raise RuntimeError("sing-box rejected it")
        with mock.patch.object(singbox, "sing_box_binary", return_value="sing-box"), \
                mock.patch.object(singbox, "check_with", side_effect=check), \
                mock.patch("builtins.print"), self.assertRaises(SystemExit):
            exporters.export("sing-box-config", self.conf, install=True)
        self.assertEqual(self.conf.read_text(), SING_BOX_CONFIG)

    def test_split_config_is_merged_with_a_warning(self):
        with mock.patch.object(singbox, "sing_box_binary", return_value="sing-box"), \
                mock.patch.object(singbox, "check_with", side_effect=RuntimeError("unknown outbound")):
            changed, warnings = singbox.merge_file(self.conf, *self.policy())
        self.assertTrue(changed)
        self.assertIn("does not check on its own", warnings[0])

    def test_warns_without_a_domain_resolver(self):
        data = json.loads(util.strip_json_comments(SING_BOX_CONFIG))
        del data["route"]["default_domain_resolver"]
        self.conf.write_text(json.dumps(data))
        _, warnings = singbox.merge_file(self.conf, *self.policy())
        self.assertIn("default_domain_resolver", warnings[0])

    def test_refuses_v2rayn_generated_config(self):
        conf = self.tmp / "binConfigs/config.json"
        conf.parent.mkdir()
        conf.write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "export v2rayn"):
            singbox.merge_file(conf, *self.policy())

    def policy(self):
        from nju_connect.policy import load_policy
        return load_policy()[0], config.load_config(), config.load_settings()


class MigrationTest(ExportFixture):
    def test_old_install_entries(self):
        old = configparser.ConfigParser()
        old.read_dict({"exports": {"xray": "merge:/home/u/.config/xray/config.json",
                                   "clash-verge": str(paths.VERGE_DIR / "profiles/Script.js"),
                                   "clash": str(paths.VERGE_DIR / "ruleset/nju-vpn.yaml"),
                                   "pac": "/home/u/nju.pac"}})
        with open(paths.SETTINGS_FILE, "w") as f:
            old.write(f)
        self.assertEqual(dict(config.load_settings()["exports"]), {
            "xray": "install:/home/u/.config/xray/config.json",
            "clash-verge": "install:" + str(paths.VERGE_DIR / "profiles/Script.js"),
            "pac": "/home/u/nju.pac"})
        self.assertNotIn("merge:", paths.SETTINGS_FILE.read_text())   # saved converted

    def test_provider_files_have_the_marker(self):
        settings, cfg = config.load_settings(), config.load_config()
        for content in clash.provider_files([], cfg, settings, ["Generated by nju-connect test"]).values():
            self.assertTrue(content.startswith("# Generated by nju-connect"))
