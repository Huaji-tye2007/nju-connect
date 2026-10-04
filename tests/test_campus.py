import json
import unittest

from nju_connect import config, network, paths
from nju_connect.policy import campus_dns_servers


def policy(addresses, dns_option=None):
    data = {"code": 0, "data": {"appList": {"data": {"appInfo": [{"apps": [
        {"accessModel": "L3VPN", "addressList": addresses}]}]}}}}
    if dns_option:
        data["data"]["sdpPolicy"] = {"data": {"clientOption": {"dnsOption": dns_option}}}
    return json.dumps(data)


def addr(host, port, protocol="udp"):
    return {"host": host, "port": port, "protocol": protocol}


class CampusDnsTest(unittest.TestCase):
    def test_private_port_53_servers_from_the_policy(self):
        resource = policy([addr("10.12.253.4", "53"), addr("10.28.253.4", "53"),
                           addr("202.119.32.6", "53"),            # public resolver: answers from anywhere
                           addr("10.1.1.1", "1-65535", "all"),    # a host, not a DNS grant
                           addr("10.2.2.2", "53", "tcp"),         # TCP only
                           addr("10.3.3.3", "80-443", "all")],
                          dns_option={"firstDNS": "10.4.4.4", "secondDNS": "114.114.114.114"})
        self.assertEqual(campus_dns_servers(resource), ["10.12.253.4", "10.28.253.4", "10.4.4.4"])

    def setUp(self):
        paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if paths.SETTINGS_FILE.exists():
            paths.SETTINGS_FILE.unlink()
        network._policy_dns_cache = (None, [])

    def test_auto_uses_the_cached_policy(self):
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.RESOURCE.write_text(policy([addr("10.9.9.9", "53")]))
        settings = config.load_settings()
        self.assertEqual(settings["campus"]["dns_servers"], "auto")
        self.assertEqual(network.campus_servers_with_source(settings), (["10.9.9.9"], "from the access policy"))

    def test_auto_without_a_policy_uses_the_fallback(self):
        if paths.RESOURCE.exists():
            paths.RESOURCE.unlink()
        self.assertEqual(network.campus_servers_with_source(config.load_settings()),
                         (network.FALLBACK_CAMPUS_DNS, "built-in default"))

    def test_configured_list_wins(self):
        settings = config.load_settings()
        settings["campus"]["dns_servers"] = "10.7.7.7"
        self.assertEqual(network.campus_servers_with_source(settings), (["10.7.7.7"], "configured"))

    def test_old_fixed_default_becomes_auto(self):
        paths.SETTINGS_FILE.write_text("[campus]\ndns_servers = 10.12.253.4, 10.28.253.4\n")
        self.assertEqual(config.load_settings()["campus"]["dns_servers"], "auto")
        self.assertIn("dns_servers = auto", paths.SETTINGS_FILE.read_text())

    def test_auto_is_a_valid_value(self):
        self.assertEqual(config.option("campus.dns_servers").parse("auto"), "auto")


if __name__ == "__main__":
    unittest.main()
