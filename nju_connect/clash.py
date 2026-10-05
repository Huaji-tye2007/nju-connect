"""Clash / mihomo formats: rule-provider, config snippet and Clash Verge Rev script."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from . import VERSION, clients, paths
from .config import DEFAULT_SERVER, MARKER, socks_address
from .policy import all_ports, nodes, routed

RULE_ORDER = {"DOMAIN": 0, "DOMAIN-WILDCARD": 1, "IP-CIDR": 2}
PROVIDER_INTERVAL = 600   # seconds between mihomo re-reading the ruleset file


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


PROXY_PROVIDER = "nju-connect"
# files an installed export keeps up to date, relative to mihomo's home directory
PROVIDER_FILES = {"nju-vpn": "ruleset/nju-vpn.yaml", "nju-direct": "ruleset/nju-direct.yaml",
                  PROXY_PROVIDER: "proxies/nju-connect.yaml"}


def direct_rules(entries, config):
    """zju-connect's own connections to the VPN server and nodes, kept out of the proxy."""
    return ([f"DOMAIN,{config.get('server_address', DEFAULT_SERVER)}"]
            + [f"IP-CIDR,{n.value},no-resolve" for n in nodes(entries)])


def _group(settings, members):
    e = settings["export"]
    group = dict({"name": e["group_name"], "type": e["group_type"]}, **members)
    if e["group_type"] in ("fallback", "url-test"):
        group.update({"url": e["health_url"], "interval": int(e["health_interval"])})
    return group


def _proxy(config, settings):
    host, port = socks_address(config)
    return {"name": settings["export"]["proxy_name"], "type": "socks5", "server": host, "port": port, "udp": True}


def direct_proxy_name(settings):
    return settings["export"]["proxy_name"] + "-DIRECT"


def _parts(settings, proxies, rule_providers):
    """The additions for every mihomo client: a fallback group over the nju-connect proxy
    provider, the nju-direct and nju-vpn rule sets, and the two rules that use them."""
    # the direct member lives in the provider too: mihomo puts a group's `proxies` before its
    # `use` providers, and the fallback group must try zju-connect first
    group = _group(settings, {"use": [PROXY_PROVIDER]})
    return {"proxy-providers": {PROXY_PROVIDER: proxies}, "proxy-groups": [group],
            "rule-providers": rule_providers,
            "rules": ["RULE-SET,nju-direct,DIRECT", f"RULE-SET,nju-vpn,{group['name']}"]}


def _proxies(config, settings):
    return [_proxy(config, settings), {"name": direct_proxy_name(settings), "type": "direct"}]


def clash_parts(entries, config, settings):
    """Self-contained additions (providers inline), for clients that cannot read our files;
    they must be imported again when what the policy grants changes."""
    def inline(rules):
        return {"type": "inline", "behavior": "classical", "payload": rules}
    return _parts(settings, {"type": "inline", "payload": _proxies(config, settings)},
                  {"nju-direct": inline(direct_rules(entries, config)),
                   "nju-vpn": inline(clash_rules(entries, resolve_domains(settings)))})


def installed_parts(settings):
    """Additions that only point at the provider files (PROVIDER_FILES): policy changes and
    the proxy's port reach mihomo through those files, so these stay the same."""
    def provider(name):
        return {"type": "file", "behavior": "classical", "format": "yaml",
                "path": "./" + PROVIDER_FILES[name], "interval": PROVIDER_INTERVAL}
    return _parts(settings, {"type": "file", "path": "./" + PROVIDER_FILES[PROXY_PROVIDER],
                             "interval": PROVIDER_INTERVAL},
                  {"nju-direct": provider("nju-direct"), "nju-vpn": provider("nju-vpn")})


def provider_files(entries, config, settings, header):
    """{relative path: content} of the files behind installed_parts()."""
    def payload(title, rules):
        lines = [f"# {line}" for line in header] + [f"# {title}", "payload:"]
        return "\n".join(lines + [f"  - '{rule}'" for rule in rules]) + "\n"
    proxies = _proxies(config, settings)
    return {
        PROVIDER_FILES["nju-vpn"]: payload("NJU resources (through zju-connect)",
                                           clash_rules(entries, resolve_domains(settings))),
        PROVIDER_FILES["nju-direct"]: payload("VPN server and nodes (direct)", direct_rules(entries, config)),
        PROVIDER_FILES[PROXY_PROVIDER]: "\n".join([f"# {line}" for line in header[:1]] + ["proxies:"]
                                                  + [f"  - {_js(p)}" for p in proxies]) + "\n",
    }


def _js(value):
    return json.dumps(value, ensure_ascii=False)


def clash_config(parts, intro="merge into your mihomo config."):
    lines = [f"# {MARKER} {VERSION}; {intro}"]
    for key in ("proxy-providers", "proxies", "proxy-groups", "rule-providers"):
        if key not in parts:
            continue
        lines.append(f"{key}:")
        value = parts[key]
        lines += [f"  {name}: {_js(item)}" for name, item in value.items()] if isinstance(value, dict) \
            else [f"  - {_js(item)}" for item in value]
    lines.append("rules:  # put these above your other rules")
    lines += [f"  - {rule}" for rule in parts["rules"]]
    return "\n".join(lines) + "\n"


def clash_verge_script(parts, regenerate="nju-connect export clash-verge --install"):
    group = parts["proxy-groups"][0]
    return f"""// {MARKER} {VERSION}; regenerate with `{regenerate}`
// Proxies NJU resources through zju-connect; the {group['type']} group {group['name']} falls back to
// DIRECT while zju-connect is not reachable.
function main(config) {{
  const add = {_js(parts)};
  for (const key of ["proxies", "proxy-groups"]) {{
    const names = (add[key] || []).map((item) => item.name);
    config[key] = (config[key] || []).filter((item) => !names.includes(item.name)).concat(add[key] || []);
  }}
  for (const key of ["proxy-providers", "rule-providers"]) {{
    config[key] = Object.assign(config[key] || {{}}, add[key] || {{}});
  }}
  config.rules = add.rules.concat(config.rules || []);

  console.log("nju-connect: injected group {group['name']} and its rules");
  return config;
}}
"""


def verge_loaded(rules):
    """Whether the configuration Clash Verge Rev last built has these rules: True, False, or
    None when it cannot tell (it writes that configuration to clash-verge.yaml on every rebuild)."""
    try:
        built = (paths.VERGE_DIR / "clash-verge.yaml").read_text(errors="replace")
    except OSError:
        return None
    return all(rule in built for rule in rules)


def verge_script_path():
    """Clash Verge Rev's global script, if Clash Verge Rev is installed."""
    return paths.VERGE_DIR / "profiles/Script.js" if paths.VERGE_DIR.is_dir() else None


# ------------------------------------------------- restarting Clash Verge Rev

def verge_processes(only_exe=None):
    """Running Clash Verge Rev GUI processes of this user as (pid, exe, argv, environ).

    only_exe limits the search to one executable (used by the tests, so they never touch
    a real Clash Verge).
    """
    found = []
    for proc in clients.processes(lambda name: name == "clash-verge"):
        if proc.uid != os.getuid() or Path(proc.exe).name != "clash-verge" \
                or (only_exe and proc.exe != str(only_exe)):
            continue
        try:
            environ = dict(item.decode(errors="replace").split("=", 1)
                           for item in (Path("/proc") / str(proc.pid) / "environ").read_bytes().split(b"\0")
                           if b"=" in item)
        except OSError:
            continue
        found.append((proc.pid, proc.exe, proc.argv, environ))
    return found


def _alive(pid):
    """Running and not an exited process waiting to be reaped."""
    try:
        return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def restart_verge(timeout=15, only_exe=None):
    """Restart the Clash Verge Rev GUI so it rebuilds its configuration (and runs the script).

    Each process is started again with its own command line and environment (DISPLAY,
    WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS, ...), so this also works from a plain terminal.
    The mihomo core (service mode) keeps running; Clash Verge reapplies the configuration
    when it starts. Returns False if Clash Verge was not running.
    """
    processes = verge_processes(only_exe)
    if not processes:
        return False
    for pid, *_ in processes:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(pid) for pid, *_ in processes):
        time.sleep(0.2)
    started = set()
    for pid, exe, argv, environ in processes:
        key = (exe, tuple(argv[1:]))
        if key in started:
            continue
        started.add(key)
        subprocess.Popen([exe] + argv[1:], env=environ, cwd=str(Path.home()), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True
