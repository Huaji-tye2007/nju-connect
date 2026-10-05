"""Xray and v2rayN: routing rules, v2rayN's rule import, and merging into an Xray config."""

import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import quote

from . import VERSION, paths
from .clash import resolve_domains
from .config import DEFAULT_SERVER, MARKER, socks_address
from .policy import all_ports, nodes, routed
from .util import check_with, read_json_config, replace_config

TAG = "nju-connect"              # ruleTag of the rules merged into an Xray config
REMARK = TAG + ":"               # prefix of the remarks of the rules imported into v2rayN


def subdomain_regex(domain):
    """aTrust's `*.x`: subdomains of x, not x itself."""
    return "^.+\\." + domain.replace(".", "\\.") + "$"


def _ports(entry):
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in entry.ports)


def outbound(config, tag):
    host, port = socks_address(config)
    return {"tag": tag, "protocol": "socks", "settings": {"servers": [{"address": host, "port": port}]}}


def suffixes(settings):
    """Domain suffixes sent to zju-connect as a whole (none when every domain is resolved)."""
    domains = resolve_domains(settings)
    return () if "*" in domains else tuple(domains)


def routing_rules(entries, config, settings, tag):
    """Xray routing rules: the VPN server and nodes direct, the policy to `tag`, then the
    rest of nju.edu.cn to `tag` too.

    One rule per (ports, network) and per domain/IP kind, because Xray ANDs the conditions
    inside a rule. Many NJU hosts are in the policy only by IP range; instead of resolving
    them here (with the public DNS off campus), their domains go to zju-connect, which
    resolves them with the campus DNS through the VPN and routes them by its policy
    (anything outside the policy leaves the machine directly).
    """
    groups = {}
    for e in routed(entries):
        field = "ip" if e.kind == "cidr" else "domain"
        key = (field, e.ports, e.network)
        rule = groups.get(key)
        if rule is None:
            rule = groups[key] = {"type": "field", field: [], "outboundTag": tag}
            if not all_ports(e):
                rule["port"] = _ports(e)
            if e.network:
                rule["network"] = e.network
        rule[field].append(e.value if e.kind == "cidr" else
                           f"full:{e.value}" if e.kind == "domain" else f"regexp:{subdomain_regex(e.value)}")
    server = config.get("server_address", DEFAULT_SERVER)
    # zju-connect's own connections to the server and VPN nodes must stay direct
    rules = [{"type": "field", "domain": [f"full:{server}"], "outboundTag": "direct"}]
    node_ips = [n.value for n in nodes(entries)]
    if node_ips:
        rules.append({"type": "field", "ip": node_ips, "outboundTag": "direct"})
    rules += list(groups.values())
    if suffixes(settings):
        rules.append({"type": "field", "domain": [f"domain:{d}" for d in suffixes(settings)],
                      "outboundTag": tag})
    return rules


def domain_strategy(settings):
    # only when every domain is to be resolved (resolve_domains = *): then resolve a domain as
    # soon as an IP rule is checked; IPIfNonMatch would not help once a later domain rule
    # (e.g. geosite:cn) matches. Xray cannot limit the resolving to some domains.
    return "IPOnDemand" if "*" in resolve_domains(settings) else None


# ------------------------------------------------------------------ v2rayN

def v2rayn_dirs():
    """v2rayN's data folders: the program folder when it is writable, else ~/.local/share/v2rayN."""
    candidates = [os.environ.get("NJU_CONNECT_V2RAYN_DIR"), paths.DATA_DIR / "v2rayN",
                  "/opt/v2rayN", "/usr/share/v2rayN", "/usr/lib/v2rayN"]
    return [Path(c) for c in candidates if c and (Path(c) / "guiConfigs").is_dir()]


def v2rayn_routing():
    """(name, rules) of v2rayN's active routing, read from its database (read-only), or None."""
    for folder in v2rayn_dirs():
        db = folder / "guiConfigs/guiNDB.db"
        if not db.is_file():
            continue
        try:
            conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                row = conn.execute("SELECT Remarks, RuleSet FROM RoutingItem WHERE IsActive = 1 "
                                   "ORDER BY Sort LIMIT 1").fetchone()
            finally:
                conn.close()
            if row:
                rules = json.loads(row[1] or "[]")
                if isinstance(rules, list):
                    return row[0] or "", [r for r in rules if isinstance(r, dict)]
        except (sqlite3.Error, ValueError):
            continue
    return None


def _ours(rule):
    """A rule this module added before (v2rayN's own keys are PascalCase)."""
    remarks = next((v for k, v in rule.items() if k.lower() == "remarks"), None) or ""
    return remarks.startswith(REMARK)


def _remark(rule):
    if rule["outboundTag"] == "direct":
        what = "VPN server direct" if "domain" in rule else "VPN nodes direct"
    elif any(d.startswith("domain:") for d in rule.get("domain", [])):
        return f"{REMARK} other {', '.join(d[len('domain:'):] for d in rule['domain'])} hosts (resolved by zju-connect)"
    else:
        what = "NJU domains" if "domain" in rule else "NJU IP ranges"
        if "port" in rule:
            what += f", port {rule['port']}"
        if "network" in rule:
            what += f", {rule['network']}"
    return f"{REMARK} {what}"


def v2rayn_rules(entries, config, settings, current=None):
    """v2rayN routing rules: ours first, then the active routing's rules without our old ones."""
    tag = settings["export"]["proxy_name"]
    rules = []
    for rule in routing_rules(entries, config, settings, tag):
        item = {"remarks": _remark(rule)}
        item.update((k, v) for k, v in rule.items() if k != "type")
        rules.append(item)
    if current:
        rules += [{k: v for k, v in r.items() if k.lower() != "id"} for r in current if not _ours(r)]
    return rules


def share_link(config, settings):
    """v2rayN's share link for zju-connect's SOCKS5 proxy (no credentials: base64 of ":")."""
    host, port = socks_address(config)
    return f"socks://Og@{host}:{port}#{quote(settings['export']['proxy_name'])}"


def render_v2rayn(entries, config, settings):
    routing = v2rayn_routing()
    name, current = routing if routing else (None, None)
    lines = [f"// {MARKER} {VERSION}: v2rayN routing rules, NJU rules first.",
             f"// Rules go to the v2rayN node named \"{settings['export']['proxy_name']}\" "
             f"(add it from {share_link(config, settings)})."]
    if domain_strategy(settings):
        lines.append(f"// Set the domain strategy (Routing Setting window) to {domain_strategy(settings)}.")
    if routing:
        lines.append(f"// Includes the {len(current)} rule(s) of your active routing \"{name}\": import it there "
                     "and answer No (replace all).")
    else:
        lines.append("// v2rayN's routing was not found: import with Yes (append), then move these rules to the top.")
    return "\n".join(lines) + "\n" + json.dumps(v2rayn_rules(entries, config, settings, current),
                                                ensure_ascii=False, indent=2) + "\n"


# ------------------------------------------------------- merging into Xray

def merge_config(data, entries, config, settings):
    """A copy of an Xray config with our outbound and rules; merging again replaces them."""
    tag = settings["export"]["proxy_name"]
    data = dict(data)
    # appended: the first outbound is Xray's default and must stay the user's
    outbounds = [o for o in data.get("outbounds") or [] if o.get("tag") != tag]
    if not any(o.get("tag") == "direct" for o in outbounds):
        outbounds.append({"tag": "direct", "protocol": "freedom"})
    data["outbounds"] = outbounds + [outbound(config, tag)]
    routing = dict(data.get("routing") or {})
    ours = [dict(rule, ruleTag=TAG) for rule in routing_rules(entries, config, settings, tag)]
    routing["rules"] = ours + [r for r in routing.get("rules") or [] if r.get("ruleTag") != TAG]
    if domain_strategy(settings):
        routing["domainStrategy"] = domain_strategy(settings)
    data["routing"] = routing
    return data


def xray_binary():
    for candidate in (os.environ.get("NJU_CONNECT_XRAY"), shutil.which("xray")):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def merge_file(path, entries, config, settings):
    """Merge the NJU outbound and rules into the Xray config at path; True if it changed."""
    path = Path(path).expanduser()
    if path.parent.name == "binConfigs" and (path.parent.parent / "guiConfigs").is_dir():
        raise RuntimeError(f"{path} is rewritten by v2rayN; use `nju-connect export v2rayn` for v2rayN")
    text, data = read_json_config(path, "Xray")
    if not data.get("outbounds"):
        raise RuntimeError(f"{path} has no outbounds; point -o at the Xray config you run")
    first = not any(isinstance(r, dict) and r.get("ruleTag") == TAG
                    for r in (data.get("routing") or {}).get("rules") or [])
    content = json.dumps(merge_config(data, entries, config, settings), ensure_ascii=False, indent=2) + "\n"
    if content == text:
        return False
    if xray_binary():
        check_with(xray_binary(), ["run", "-test", "-c"], content, "xray")
    replace_config(path, content, backup=first)
    return True
