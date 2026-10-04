import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# never read or write the real ~/.config/nju-connect while testing
os.environ["NJU_CONNECT_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["NJU_CONNECT_STATE_DIR"] = tempfile.mkdtemp()


def load(name="njc"):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "nju_connect.py"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


njc = load()


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


class BuildRulesTest(unittest.TestCase):
    def rules(self, *apps):
        rules, skipped = njc.build_rules(resource(list(apps)))
        return set(rules), dict(skipped)

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
            njc.build_rules(json.dumps({"code": 1, "message": "denied"}))


class ConfigTest(unittest.TestCase):
    TOML = '\n'.join([
        '# comment',
        'username = "251220100"',
        'password = "p\\"w"',
        'server_port = 443',
        'disable_zju_config = true',
        'socks_bind = "127.0.0.1:2080"',
    ])

    def test_fallback_toml_parser(self):
        saved = sys.modules.get("tomllib")
        sys.modules["tomllib"] = None  # behave like Python < 3.11
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
                f.write(self.TOML)
            self.assertEqual(njc.read_toml(f.name), {
                "username": "251220100", "password": 'p"w', "server_port": 443,
                "disable_zju_config": True, "socks_bind": "127.0.0.1:2080"})
        finally:
            os.unlink(f.name)
            if saved is None:
                del sys.modules["tomllib"]
            else:
                sys.modules["tomllib"] = saved

    def test_socks_address(self):
        self.assertEqual(njc.socks_address({"socks_bind": ":1080"}), ("127.0.0.1", 1080))
        self.assertEqual(njc.socks_address({"socks_bind": "127.0.0.1:2080"}), ("127.0.0.1", 2080))


class ClashScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = njc.load_settings()
        self.ruleset = Path(self.tmp.name) / "nju-vpn.yaml"
        self.ruleset.write_text("payload:\n  - 'IP-CIDR,10.0.0.0/12,no-resolve'\n")
        self.settings["ruleset"]["output"] = str(self.ruleset)
        self.config = {"server_address": "vpn.nju.edu.cn", "socks_bind": "127.0.0.1:2080"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_provider_follows_config(self):
        self.settings["ruleset"]["provider_path"] = "./ruleset/nju-vpn.yaml"
        proxy, group, provider, rules = njc.clash_parts(self.config, self.settings)
        self.assertEqual((proxy["server"], proxy["port"], proxy["udp"]), ("127.0.0.1", 2080, True))
        self.assertEqual(group["type"], "fallback")
        self.assertEqual(group["proxies"], ["NJUConnect", "DIRECT"])
        self.assertEqual(provider["path"], "./ruleset/nju-vpn.yaml")
        self.assertEqual(rules, ["DOMAIN,vpn.nju.edu.cn,DIRECT", "RULE-SET,nju-vpn,NJU"])
        self.assertIn('"port": 2080', njc.clash_js(self.config, self.settings))

    def test_absolute_provider_path_is_inlined(self):
        self.settings["ruleset"]["provider_path"] = str(self.ruleset)
        _, _, provider, _ = njc.clash_parts(self.config, self.settings)
        self.assertEqual(provider, {"type": "inline", "behavior": "classical",
                                    "payload": ["IP-CIDR,10.0.0.0/12,no-resolve"]})


if __name__ == "__main__":
    unittest.main()
