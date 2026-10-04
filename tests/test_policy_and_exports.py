import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nju_connect import clash, config, exporters, paths
from nju_connect.policy import parse_policy


def resource(apps):
    return json.dumps({"code": 0, "message": "OK",
                       "data": {"appList": {"data": {"appInfo": [{"apps": apps}]}}}})


def app(*addresses, model="L3VPN"):
    return {"accessModel": model, "addressList": list(addresses)}


def addr(host, port="80", protocol="all", ip=None):
    entry = {"host": host, "port": port, "protocol": protocol}
    if ip is not None:
        entry["ip"] = ip
    return entry


# one policy covering every kind of entry, shared by the exporter tests
SAMPLE = resource([
    app(addr("lib.nju.edu.cn", "80"), addr("lib.nju.edu.cn", "443")),
    app(addr("*.51cto.com", "443", "tcp")),
    app(addr("114.212.0.0/16", "1-65535")),
    app(addr("10.12.253.4", "53", "udp")),
    app(addr("pan.example.com", "443", ip=["1.2.3.4"])),
    app(addr("10.0.0.0-10.0.1.255", "8000-8080", "tcp")),
])


class ParsePolicyTest(unittest.TestCase):
    def rules(self, *apps):
        entries, skipped = parse_policy(resource(list(apps)))
        return set(clash.clash_rules(entries)), skipped

    def test_domain_ports_are_merged(self):
        rules, _ = self.rules(app(addr("lib.nju.edu.cn", "80"), addr("lib.nju.edu.cn", "443")))
        self.assertEqual(rules, {"AND,((DOMAIN,lib.nju.edu.cn),(DST-PORT,80/443))"})

    def test_prefix_wildcard_tcp_only(self):
        rules, _ = self.rules(app(addr("*.51cto.com", "443", "tcp")))
        self.assertEqual(rules, {"AND,((DOMAIN-WILDCARD,*.51cto.com),(NETWORK,tcp),(DST-PORT,443))"})

    def test_full_port_cidr_is_a_plain_rule(self):
        rules, _ = self.rules(app(addr("114.212.0.0/16", "1-65535")))
        self.assertEqual(rules, {"IP-CIDR,114.212.0.0/16,no-resolve"})

    def test_ip_range_is_summarised(self):
        rules, _ = self.rules(app(addr("10.0.0.0-10.0.1.255", "1-65535")))
        self.assertEqual(rules, {"IP-CIDR,10.0.0.0/23,no-resolve"})

    def test_udp_is_kept(self):
        rules, _ = self.rules(app(addr("10.12.253.4", "53", "udp")))
        self.assertEqual(rules, {"AND,((IP-CIDR,10.12.253.4/32,no-resolve),(NETWORK,udp),(DST-PORT,53))"})

    def test_unsupported_entries_are_skipped(self):
        rules, skipped = self.rules(app(addr("lf*-cdn.example.com"), addr("2001:da8::1"),
                                        addr("::/0", "1-65535")),
                                    app(addr("web.example.com"), model="WEB"))
        self.assertEqual(rules, set())
        self.assertEqual(skipped, {"unsupported wildcard": 1, "IPv6": 2, "non-L3VPN app address": 1})

    def test_domain_ips_add_ip_rules(self):
        rules, _ = self.rules(app(addr("pan.example.com", "443", ip=["1.2.3.4", "2409::1"])))
        self.assertEqual(rules, {"AND,((DOMAIN,pan.example.com),(DST-PORT,443))",
                                 "AND,((IP-CIDR,1.2.3.4/32,no-resolve),(DST-PORT,443))"})

    def test_error_code_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_policy(json.dumps({"code": 1, "message": "denied"}))


class TomlTest(unittest.TestCase):
    def test_fallback_toml_parser(self):
        saved = sys.modules.get("tomllib")
        sys.modules["tomllib"] = None  # behave like Python < 3.11
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
                f.write('# c\nusername = "1"\npassword = "p\\"w"\nserver_port = 443\n'
                        'disable_zju_config = true\nsocks_bind = "127.0.0.1:2080"\n')
            self.assertEqual(config.read_toml(f.name), {
                "username": "1", "password": 'p"w', "server_port": 443,
                "disable_zju_config": True, "socks_bind": "127.0.0.1:2080"})
        finally:
            os.unlink(f.name)
            if saved is None:
                del sys.modules["tomllib"]
            else:
                sys.modules["tomllib"] = saved

    def test_socks_address(self):
        self.assertEqual(config.socks_address({"socks_bind": ":1080"}), ("127.0.0.1", 1080))
        self.assertEqual(config.socks_address({"socks_bind": "127.0.0.1:2080"}), ("127.0.0.1", 2080))


class ExportTest(unittest.TestCase):
    CONFIG = {"server_address": "vpn.nju.edu.cn", "username": "u",
              "socks_bind": "127.0.0.1:2080", "http_bind": "127.0.0.1:2081"}

    def setUp(self):
        config.write_config(dict(self.CONFIG))
        if paths.SETTINGS_FILE.exists():
            paths.SETTINGS_FILE.unlink()
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.RESOURCE.write_text(SAMPLE)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def render(self, name):
        return exporters.render(name)

    def test_clash_rule_provider(self):
        text = self.render("clash")
        self.assertTrue(text.startswith("# Generated by nju-connect"))
        self.assertIn("  - 'AND,((DOMAIN,lib.nju.edu.cn),(DST-PORT,80/443))'", text)
        self.assertIn("  - 'IP-CIDR,114.212.0.0/16,no-resolve'", text)

    def test_clash_config_inlines_without_a_readable_file(self):
        text = self.render("clash-config")
        self.assertIn('"type": "inline"', text)
        self.assertIn('"port": 2080', text)
        self.assertIn("DOMAIN,vpn.nju.edu.cn,DIRECT", text)

    def test_clash_provider_inside_mihomo_dir_is_a_file(self):
        ruleset = paths.MIHOMO_DIR / "ruleset/nju-vpn.yaml"
        provider = clash.provider_for([], ruleset)
        self.assertEqual(provider["path"], "./ruleset/nju-vpn.yaml")
        self.assertEqual(clash.provider_for([], self.tmp / "x.yaml")["type"], "inline")

    def test_clash_verge_script(self):
        text = self.render("clash-verge")
        self.assertIn("function main(config)", text)
        self.assertIn('"type": "fallback"', text)
        self.assertIn("RULE-SET,nju-vpn,NJU", text)

    def test_sing_box(self):
        data = json.loads(self.render("sing-box"))
        self.assertEqual(data["version"], 1)
        rules = data["rules"]
        self.assertIn({"domain": ["lib.nju.edu.cn"], "port": [80, 443]}, rules)
        self.assertIn({"domain_regex": ["^.+\\.51cto\\.com$"], "port": [443], "network": "tcp"}, rules)
        self.assertIn({"ip_cidr": ["114.212.0.0/16"]}, rules)
        self.assertIn({"ip_cidr": ["10.0.0.0/23"], "port_range": ["8000:8080"], "network": "tcp"}, rules)
        for rule in rules:   # domain and IP conditions are never mixed in one rule
            self.assertFalse({"domain", "domain_regex"} & set(rule) and "ip_cidr" in rule)

    def test_xray(self):
        data = json.loads(self.render("xray"))
        self.assertEqual(data["outbounds"][0]["settings"]["servers"], [{"address": "127.0.0.1", "port": 2080}])
        rules = data["routing"]["rules"]
        self.assertEqual(rules[0], {"type": "field", "domain": ["full:vpn.nju.edu.cn"], "outboundTag": "direct"})
        self.assertIn({"type": "field", "domain": ["full:lib.nju.edu.cn"], "outboundTag": "NJUConnect",
                       "port": "80,443"}, rules)
        self.assertIn({"type": "field", "domain": ["regexp:^.+\\.51cto\\.com$"], "outboundTag": "NJUConnect",
                       "port": "443", "network": "tcp"}, rules)
        self.assertIn({"type": "field", "ip": ["10.0.0.0/23"], "outboundTag": "NJUConnect",
                       "port": "8000-8080", "network": "tcp"}, rules)
        for rule in rules:
            self.assertFalse("domain" in rule and "ip" in rule)

    @unittest.skipUnless(shutil.which("node"), "node is needed to evaluate the PAC file")
    def test_pac_routing(self):
        pac = self.render("pac")
        cases = [("https://lib.nju.edu.cn/", "lib.nju.edu.cn", True),
                 ("https://lib.nju.edu.cn:8443/", "lib.nju.edu.cn", False),
                 ("https://a.b.51cto.com/", "a.b.51cto.com", True),
                 ("https://51cto.com/", "51cto.com", False),
                 ("http://114.212.1.1:22/", "114.212.1.1", True),
                 ("http://10.0.1.9:8080/", "10.0.1.9", True),
                 ("http://10.0.1.9/", "10.0.1.9", False),
                 ("https://example.com/", "example.com", False)]
        js = pac + """
function isInNet(host, base, mask) {
  function n(a) { return a.split(".").reduce(function (x, y) { return x * 256 + (+y); }, 0); }
  var m = n(mask); return (n(host) & m) >>> 0 === (n(base) & m) >>> 0;
}
var cases = %s;
console.log(JSON.stringify(cases.map(function (c) { return FindProxyForURL(c[0], c[1]); })));
""" % json.dumps([[u, h] for u, h, _ in cases])
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        for (url, _, proxied), result in zip(cases, out):
            with self.subTest(url=url):
                self.assertEqual(result.startswith("PROXY 127.0.0.1:2081"), proxied, result)

    def test_list(self):
        text = self.render("list")
        self.assertIn("lib.nju.edu.cn\t80,443\ttcp+udp", text)
        self.assertIn("*.51cto.com\t443\ttcp", text)

    def test_remembered_exports_are_refreshed(self):
        out = self.tmp / "nju.json"
        with mock.patch("builtins.print"):
            exporters.export("sing-box", out)
        self.assertEqual(exporters.remembered(), {"sing-box": str(out.resolve())})
        out.write_text("// Generated by nju-connect (stale)\n")
        self.assertEqual(exporters.refresh_exports(quiet=True), 1)
        self.assertEqual(json.loads(out.read_text())["version"], 1)
        exporters.forget("sing-box")
        self.assertEqual(exporters.remembered(), {})

    def test_foreign_file_is_backed_up(self):
        out = self.tmp / "Script.js"
        out.write_text("function main(c) { return c; }\n")
        with mock.patch("builtins.print"):
            exporters.export("clash-verge", out)
        self.assertEqual(len(list(self.tmp.glob("Script.js.bak-*"))), 1)
        with mock.patch("builtins.print"):
            exporters.export("clash-verge", out)   # our own file: no second backup
        self.assertEqual(len(list(self.tmp.glob("Script.js.bak-*"))), 1)


if __name__ == "__main__":
    unittest.main()
