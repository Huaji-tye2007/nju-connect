"""Clash / mihomo formats: rule-provider, config snippet and Clash Verge Rev script."""

import json
from pathlib import Path

from . import VERSION, paths
from .config import DEFAULT_SERVER, MARKER, socks_address
from .policy import all_ports

RULE_ORDER = {"DOMAIN": 0, "DOMAIN-WILDCARD": 1, "IP-CIDR": 2}


def clash_rule(entry):
    """One mihomo classical rule (without policy) for a policy entry."""
    if entry.kind == "domain":
        base = f"DOMAIN,{entry.value}"
    elif entry.kind == "subdomains":
        base = f"DOMAIN-WILDCARD,*.{entry.value}"
    else:
        base = f"IP-CIDR,{entry.value},no-resolve"
    conds = [base]
    if entry.network:
        conds.append(f"NETWORK,{entry.network}")
    if not all_ports(entry):
        conds.append("DST-PORT," + "/".join(str(lo) if lo == hi else f"{lo}-{hi}"
                                            for lo, hi in entry.ports))
    return base if len(conds) == 1 else "AND,(" + ",".join(f"({c})" for c in conds) + ")"


def clash_rules(entries):
    rules = [clash_rule(e) for e in entries]
    rules.sort(key=lambda r: (RULE_ORDER.get(r.replace("AND,((", "", 1).split(",")[0], 9), r))
    return rules


def rule_provider(entries, header):
    lines = [f"# {line}" for line in header] + ["payload:"]
    lines += [f"  - '{rule}'" for rule in clash_rules(entries)]
    return "\n".join(lines) + "\n"


def provider_for(entries, clash_path):
    """The `nju-vpn` rule-provider: a file mihomo can read, or the rules inline."""
    if clash_path:
        path = Path(clash_path).resolve()
        for home in (paths.VERGE_DIR, paths.MIHOMO_DIR):
            try:
                relative = path.relative_to(home.resolve())
            except ValueError:
                continue
            return {"type": "file", "behavior": "classical", "format": "yaml", "path": f"./{relative}"}
    # mihomo only reads provider files inside its home directory
    return {"type": "inline", "behavior": "classical", "payload": clash_rules(entries)}


def clash_parts(entries, config, settings, clash_path=None):
    host, port = socks_address(config)
    e = settings["export"]
    proxy = {"name": e["proxy_name"], "type": "socks5", "server": host, "port": port, "udp": True}
    group = {"name": e["group_name"], "type": e["group_type"], "proxies": [e["proxy_name"], "DIRECT"]}
    if e["group_type"] in ("fallback", "url-test"):
        group.update({"url": e["health_url"], "interval": int(e["health_interval"])})
    rules = [
        # keep zju-connect's own connection to the VPN server out of the proxy
        f"DOMAIN,{config.get('server_address', DEFAULT_SERVER)},DIRECT",
        f"RULE-SET,nju-vpn,{e['group_name']}",
    ]
    return proxy, group, provider_for(entries, clash_path), rules


def _js(value):
    return json.dumps(value, ensure_ascii=False)


def clash_config(entries, config, settings, clash_path=None):
    proxy, group, provider, rules = clash_parts(entries, config, settings, clash_path)
    lines = [f"# {MARKER} {VERSION}; merge into your mihomo config.",
             "proxies:", f"  - {_js(proxy)}",
             "proxy-groups:", f"  - {_js(group)}",
             "rule-providers:", f"  nju-vpn: {_js(provider)}",
             "rules:  # put these above your other rules"]
    lines += [f"  - {rule}" for rule in rules]
    return "\n".join(lines) + "\n"


def clash_verge_script(entries, config, settings, clash_path=None):
    proxy, group, provider, rules = clash_parts(entries, config, settings, clash_path)
    return f"""// {MARKER} {VERSION}; regenerate with `nju-connect export clash-verge --install`
// Proxies NJU resources through zju-connect ({proxy['server']}:{proxy['port']}); the {group['type']}
// group falls back to DIRECT while zju-connect is not running (e.g. on campus).
function main(config) {{
  const proxy = {_js(proxy)};
  const group = {_js(group)};
  const provider = {_js(provider)};
  const rules = {_js(rules)};

  config.proxies = (config.proxies || []).filter((p) => p.name !== proxy.name);
  config.proxies.push(proxy);
  config["proxy-groups"] = (config["proxy-groups"] || []).filter((g) => g.name !== group.name);
  config["proxy-groups"].push(group);
  config["rule-providers"] = config["rule-providers"] || {{}};
  config["rule-providers"]["nju-vpn"] = provider;
  config.rules = rules.concat(config.rules || []);

  console.log("nju-connect: injected " + proxy.name + ", group " + group.name + " and ruleset nju-vpn");
  return config;
}}
"""


def verge_script_path():
    """Clash Verge Rev's global script, if Clash Verge Rev is installed."""
    return paths.VERGE_DIR / "profiles/Script.js" if paths.VERGE_DIR.is_dir() else None


def verge_ruleset_path():
    return paths.VERGE_DIR / "ruleset/nju-vpn.yaml"
