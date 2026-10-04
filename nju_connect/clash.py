"""Clash / mihomo formats: rule-provider, config snippet and Clash Verge Rev script."""

import json
from pathlib import Path

from . import VERSION, paths
from .config import DEFAULT_SERVER, MARKER, socks_address
from .policy import all_ports, nodes, routed

RULE_ORDER = {"DOMAIN": 0, "DOMAIN-WILDCARD": 1, "IP-CIDR": 2}


DEFAULT_RESOLVE = ("nju.edu.cn",)


def resolve_domains(settings):
    """Domains resolved to check NJU's IP ranges: a tuple, ("*",) for all, () for none.

    zju-connect routes a host by its IP too, so e.g. xk.nju.edu.cn (only covered by
    219.219.112.0/20) must be resolved; limiting this to NJU domains avoids a DNS lookup
    for every other domain the proxy tool sees.
    """
    value = settings["export"].get("resolve_domains", ", ".join(DEFAULT_RESOLVE)).strip()
    return tuple(d.strip() for d in value.split(",") if d.strip())


def _with_conditions(base, entry):
    conds = [base]
    if entry.network:
        conds.append(f"NETWORK,{entry.network}")
    if not all_ports(entry):
        conds.append("DST-PORT," + "/".join(str(lo) if lo == hi else f"{lo}-{hi}"
                                            for lo, hi in entry.ports))
    return base if len(conds) == 1 else "AND,(" + ",".join(f"({c})" for c in conds) + ")"


def clash_entry_rules(entry, resolve=DEFAULT_RESOLVE):
    """mihomo classical rules (without policy) for a policy entry."""
    if entry.kind == "domain":
        return [_with_conditions(f"DOMAIN,{entry.value}", entry)]
    if entry.kind == "subdomains":
        return [_with_conditions(f"DOMAIN-WILDCARD,*.{entry.value}", entry)]
    if "*" in resolve:
        return [_with_conditions(f"IP-CIDR,{entry.value}", entry)]   # resolves every domain
    rules = [_with_conditions(f"IP-CIDR,{entry.value},no-resolve", entry)]   # IP destinations
    for domain in resolve:
        # mihomo only resolves when DOMAIN-SUFFIX matched, so other domains stay unresolved
        inner = _with_conditions(f"IP-CIDR,{entry.value}", entry)
        inner = inner[len("AND,("):-1] if inner.startswith("AND,(") else f"({inner})"
        rules.append(f"AND,((DOMAIN-SUFFIX,{domain}),{inner})")
    return rules


def clash_rules(entries, resolve=DEFAULT_RESOLVE):
    rules = [rule for e in routed(entries) for rule in clash_entry_rules(e, resolve)]
    rules.sort(key=lambda r: (RULE_ORDER.get(r.replace("AND,((", "", 1).split(",")[0], 9), r))
    return rules


def rule_provider(entries, header, resolve=DEFAULT_RESOLVE):
    lines = [f"# {line}" for line in header] + ["payload:"]
    lines += [f"  - '{rule}'" for rule in clash_rules(entries, resolve)]
    return "\n".join(lines) + "\n"


def provider_for(entries, clash_path, resolve=DEFAULT_RESOLVE):
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
    return {"type": "inline", "behavior": "classical", "payload": clash_rules(entries, resolve)}


def clash_parts(entries, config, settings, clash_path=None):
    host, port = socks_address(config)
    e = settings["export"]
    proxy = {"name": e["proxy_name"], "type": "socks5", "server": host, "port": port, "udp": True}
    group = {"name": e["group_name"], "type": e["group_type"], "proxies": [e["proxy_name"], "DIRECT"]}
    if e["group_type"] in ("fallback", "url-test"):
        group.update({"url": e["health_url"], "interval": int(e["health_interval"])})
    # keep zju-connect's own connections to the VPN server and nodes out of the proxy
    rules = [f"DOMAIN,{config.get('server_address', DEFAULT_SERVER)},DIRECT"]
    rules += [f"IP-CIDR,{n.value},DIRECT,no-resolve" for n in nodes(entries)]
    rules.append(f"RULE-SET,nju-vpn,{e['group_name']}")
    return proxy, group, provider_for(entries, clash_path, resolve_domains(settings)), rules


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
