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
def with_nodes(text, addresses):
    data = json.loads(text)
    data["data"]["appList"]["data"]["config"] = {"nodeGroupConf": {"nodeGroupList": [
        {"id": "g", "addressInfo": [{"address": a, "type": "wan"} for a in addresses]}]}}
    return json.dumps(data)


SAMPLE = with_nodes(resource([
    app(addr("lib.nju.edu.cn", "80"), addr("lib.nju.edu.cn", "443")),
    app(addr("*.51cto.com", "443", "tcp")),
    app(addr("114.212.0.0/16", "1-65535")),
    app(addr("10.12.253.4", "53", "udp")),
    app(addr("pan.example.com", "443", ip=["1.2.3.4"])),
    app(addr("10.0.0.0-10.0.1.255", "8000-8080", "tcp")),
    app(addr("219.219.112.0/20", "1-65535")),
]), ["219.219.118.25:441", "[2001:da8::25]:441", "{{sdpcHost}}"])


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
        self.assertEqual(rules, {"IP-CIDR,114.212.0.0/16,no-resolve",
                                 "AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,114.212.0.0/16))"})

    def test_ip_range_is_summarised(self):
        rules, _ = self.rules(app(addr("10.0.0.0-10.0.1.255", "1-65535")))
        self.assertIn("IP-CIDR,10.0.0.0/23,no-resolve", rules)

    def test_udp_is_kept(self):
        rules, _ = self.rules(app(addr("10.12.253.4", "53", "udp")))
        self.assertEqual(rules, {
            "AND,((IP-CIDR,10.12.253.4/32,no-resolve),(NETWORK,udp),(DST-PORT,53))",
            "AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,10.12.253.4/32),(NETWORK,udp),(DST-PORT,53))"})

    def test_unsupported_entries_are_skipped(self):
        rules, skipped = self.rules(app(addr("lf*-cdn.example.com"), addr("2001:da8::1"),
                                        addr("::/0", "1-65535")),
                                    app(addr("web.example.com"), model="WEB"))
        self.assertEqual(rules, set())
        self.assertEqual(skipped, {"unsupported wildcard": 1, "IPv6": 2, "non-L3VPN app address": 1})

    def test_domain_ips_add_ip_rules(self):
        rules, _ = self.rules(app(addr("pan.example.com", "443", ip=["1.2.3.4", "2409::1"])))
        self.assertEqual(rules, {"AND,((DOMAIN,pan.example.com),(DST-PORT,443))",
                                 "AND,((IP-CIDR,1.2.3.4/32,no-resolve),(DST-PORT,443))",
                                 "AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,1.2.3.4/32),(DST-PORT,443))"})

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


class ExportFixture(unittest.TestCase):
    """A config and the SAMPLE policy in the test folders (shared with tests/test_xray.py)."""
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


class ExportTest(ExportFixture):
    def test_clash_rule_provider(self):
        text = self.render("clash")
        self.assertTrue(text.startswith("# Generated by nju-connect"))
        self.assertIn("  - 'AND,((DOMAIN,lib.nju.edu.cn),(DST-PORT,80/443))'", text)
        self.assertIn("  - 'IP-CIDR,114.212.0.0/16,no-resolve'", text)
        self.assertIn("  - 'AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,114.212.0.0/16))'", text)
        self.assertNotIn("219.219.118.25", text)          # the VPN node is not routed to the VPN

    def test_resolve_domains_setting(self):
        config.set_options({"export.resolve_domains": ""})
        self.assertNotIn("DOMAIN-SUFFIX", self.render("clash"))
        self.assertNotIn("domain:nju.edu.cn", self.render("xray"))
        self.assertIn("var RESOLVE = [];", self.render("pac"))
        self.assertNotIn("resolve", self.render("sing-box-config"))
        config.set_options({"export.resolve_domains": "*"})
        self.assertIn("  - 'IP-CIDR,114.212.0.0/16'", self.render("clash"))
        self.assertIn({"action": "resolve"}, json.loads(self.render("sing-box-config"))["route"]["rules"])
        xray_routing = json.loads(self.render("xray"))["routing"]
        self.assertEqual(xray_routing["domainStrategy"], "IPOnDemand")   # every domain: Xray resolves them
        self.assertNotIn("domain:", json.dumps(xray_routing))
        with self.assertRaises(ValueError):
            config.set_options({"export.resolve_domains": "not a domain"})

    def test_sing_box_config(self):
        data = json.loads(self.render("sing-box-config"))
        self.assertEqual(data["outbounds"][0], {"type": "socks", "tag": "NJUConnect", "server": "127.0.0.1",
                                                "server_port": 2080, "version": "5"})
        self.assertEqual(data["route"]["rule_set"][0]["type"], "inline")
        self.assertEqual(data["route"]["rules"], [
            {"domain": ["vpn.nju.edu.cn"], "outbound": "direct"},
            {"ip_cidr": ["219.219.118.25/32"], "outbound": "direct"},
            {"rule_set": "nju-vpn", "outbound": "NJUConnect"},
            {"domain_suffix": ["nju.edu.cn"], "action": "resolve"},
            {"rule_set": "nju-vpn", "outbound": "NJUConnect"}])

    def test_clash_config_inlines_without_a_readable_file(self):
        text = self.render("clash-config")
        self.assertIn('"type": "inline"', text)
        self.assertIn('"port": 2080', text)
        self.assertIn("DOMAIN,vpn.nju.edu.cn,DIRECT", text)
        self.assertIn("IP-CIDR,219.219.118.25/32,DIRECT,no-resolve", text)
        self.assertLess(text.index("219.219.118.25/32,DIRECT"), text.index("RULE-SET,nju-vpn"))

    def test_clash_provider_inside_mihomo_dir_is_a_file(self):
        ruleset = paths.MIHOMO_DIR / "ruleset/nju-vpn.yaml"
        provider = clash.provider_for([], ruleset)
        self.assertEqual(provider["path"], "./ruleset/nju-vpn.yaml")
        self.assertEqual(provider["interval"], clash.PROVIDER_INTERVAL)   # mihomo re-reads it by itself
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
        self.assertIn({"ip_cidr": ["114.212.0.0/16", "219.219.112.0/20"]}, rules)
        self.assertIn({"ip_cidr": ["10.0.0.0/23"], "port_range": ["8000:8080"], "network": "tcp"}, rules)
        for rule in rules:   # domain and IP conditions are never mixed in one rule
            self.assertFalse({"domain", "domain_regex"} & set(rule) and "ip_cidr" in rule)

    def test_xray(self):
        data = json.loads(self.render("xray"))
        self.assertEqual(data["outbounds"][0]["settings"]["servers"], [{"address": "127.0.0.1", "port": 2080}])
        rules = data["routing"]["rules"]
        self.assertNotIn("domainStrategy", data["routing"])   # nju.edu.cn goes to zju-connect instead
        self.assertEqual(rules[0], {"type": "field", "domain": ["full:vpn.nju.edu.cn"], "outboundTag": "direct"})
        # last, after the policy: the rest of nju.edu.cn, resolved by zju-connect with the campus DNS
        self.assertEqual(rules[-1], {"type": "field", "domain": ["domain:nju.edu.cn"], "outboundTag": "NJUConnect"})
        self.assertEqual(rules[1], {"type": "field", "ip": ["219.219.118.25/32"], "outboundTag": "direct"})
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
                 ("https://example.com/", "example.com", False),
                 ("https://xk.nju.edu.cn/", "xk.nju.edu.cn", True),        # only its IP is listed
                 ("https://other.example/", "other.example", False)]       # not resolved at all
        js = pac + """
function isInNet(host, base, mask) {
  function n(a) { return a.split(".").reduce(function (x, y) { return x * 256 + (+y); }, 0); }
  var m = n(mask); return (n(host) & m) >>> 0 === (n(base) & m) >>> 0;
}
var DNS = {"xk.nju.edu.cn": "219.219.120.91"};
function dnsDomainIs(host, domain) { return host.length >= domain.length &&
  host.substring(host.length - domain.length) === domain; }
function dnsResolve(host) {   // only NJU hosts may be resolved
  if (!dnsDomainIs(host, ".nju.edu.cn")) throw new Error("resolved " + host);
  return DNS[host] || null;
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

    def test_verge_install_rewrites_the_remembered_ruleset(self):
        ruleset, script = self.tmp / "nju-vpn.yaml", self.tmp / "Script.js"
        ruleset.write_text("# Generated by nju-connect (old rules)\npayload:\n")
        exporters.remember("clash", ruleset)
        with mock.patch("builtins.print"), \
                mock.patch.object(exporters, "apply_verge_script") as apply:   # never restart a real Clash Verge
            exporters.export("clash-verge", script, install=True)
        apply.assert_called_once()
        self.assertIn("IP-CIDR,114.212.0.0/16,no-resolve", ruleset.read_text())
        self.assertIn("function main(config)", script.read_text())

    def test_inline_export_ignores_the_remembered_rule_files(self):
        ruleset = paths.MIHOMO_DIR / "ruleset/nju-vpn.yaml"
        snippet = self.tmp / "flclash.yaml"
        with mock.patch("builtins.print"):
            exporters.export("clash", ruleset)
            exporters.export("clash-config", snippet, inline=True)
        self.assertIn('"type": "inline"', snippet.read_text())
        self.assertIn('"path": "./ruleset/nju-vpn.yaml"', self.render("clash-config"))   # not inline
        self.assertTrue(exporters.remembered()["clash-config"].startswith(exporters.INLINE))
        snippet.write_text("# Generated by nju-connect (stale)\n")
        exporters.refresh_exports(quiet=True)
        self.assertIn('"type": "inline"', snippet.read_text())   # stays inline when refreshed

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
