"""sing-box: rule-set sources, and merging the NJU outbound and rules into a sing-box config."""

import json
import os
import shutil
from pathlib import Path

from .clash import resolve_domains
from .config import DEFAULT_SERVER, socks_address
from .policy import all_ports, nodes, routed
from .util import check_with, read_json_config, replace_config, write_atomic
from .xray import subdomain_regex

RULE_SETS = ("nju-direct", "nju-vpn", "nju-resolve")   # tags of the rule sets an install adds
FILES_DIR = "nju-connect"   # next to the config; not *.json at the top level, so `-C dir` skips it


def policy_rules(entries):
    """Headless rules; domain and IP rules kept apart, since sing-box ANDs the domain
    and IP categories inside one rule."""
    groups = {}
    for e in routed(entries):
        category = "ip" if e.kind == "cidr" else "domain"
        key = (category, e.ports, e.network)
        rule = groups.setdefault(key, {})
        if e.kind == "domain":
            rule.setdefault("domain", []).append(e.value)
        elif e.kind == "subdomains":
            rule.setdefault("domain_regex", []).append(subdomain_regex(e.value))
        else:
            rule.setdefault("ip_cidr", []).append(e.value)
        if not all_ports(e):
            singles = [lo for lo, hi in e.ports if lo == hi]
            ranges = [f"{lo}:{hi}" for lo, hi in e.ports if lo != hi]
            if singles:
                rule["port"] = singles
            if ranges:
                rule["port_range"] = ranges
        if e.network:
            rule["network"] = e.network
    return list(groups.values())


def rule_set_source(rules):
    return json.dumps({"version": 1, "rules": rules}, ensure_ascii=False, indent=2) + "\n"


def rule_set_files(entries, config, settings):
    """{tag: rule-set source} kept up to date for an installed config."""
    direct = [{"domain": [config.get("server_address", DEFAULT_SERVER)]}]
    node_ips = [n.value for n in nodes(entries)]
    if node_ips:
        direct.append({"ip_cidr": node_ips})
    files = {"nju-direct": rule_set_source(direct), "nju-vpn": rule_set_source(policy_rules(entries))}
    resolve = resolve_domains(settings)
    if resolve:
        # resolved, then matched again by IP: hosts the policy only covers by IP range
        files["nju-resolve"] = rule_set_source(
            [{"domain_regex": [".*"]}] if "*" in resolve else [{"domain_suffix": list(resolve)}])
    return files


def _ours(rule):
    used = rule.get("rule_set")
    used = [used] if isinstance(used, str) else used or []
    return any(tag in RULE_SETS for tag in used)


def additions(files, config, settings, direct="direct"):
    """(outbound, rule sets, rules) an install adds; files: {tag: path of its rule-set source}."""
    tag = settings["export"]["proxy_name"]
    host, port = socks_address(config)
    outbound = {"type": "socks", "tag": tag, "server": host, "server_port": port, "version": "5"}
    rule_sets = [{"type": "local", "tag": name, "format": "source", "path": str(path)}
                 for name, path in files.items()]
    rules = [{"rule_set": "nju-direct", "outbound": direct}, {"rule_set": "nju-vpn", "outbound": tag}]
    if "nju-resolve" in files:
        rules += [{"rule_set": "nju-resolve", "action": "resolve"}, {"rule_set": "nju-vpn", "outbound": tag}]
    return outbound, rule_sets, rules


def direct_tag(data):
    """The tag of the config's direct outbound, or None."""
    return next((o.get("tag") for o in data.get("outbounds") or []
                 if isinstance(o, dict) and o.get("type") == "direct" and o.get("tag")), None)


def merge_config(data, files, config, settings):
    """A copy of a sing-box config with our outbound, rule sets and rules (files: {tag: path}).

    Our rules are the ones using our rule sets; merging again replaces them. The outbound is
    appended, so the first outbound (the default without route.final) stays the user's.
    """
    tag = settings["export"]["proxy_name"]
    data = dict(data)
    outbounds = [o for o in data.get("outbounds") or [] if o.get("tag") != tag]
    direct = direct_tag(data)
    if direct is None:
        direct = "direct"
        outbounds.append({"type": "direct", "tag": direct})
    outbound, rule_sets, rules = additions(files, config, settings, direct)
    data["outbounds"] = outbounds + [outbound]
    route = dict(data.get("route") or {})
    route["rule_set"] = [r for r in route.get("rule_set") or [] if r.get("tag") not in RULE_SETS] + rule_sets
    route["rules"] = rules + [r for r in route.get("rules") or [] if not _ours(r)]
    data["route"] = route
    return data


def sing_box_binary():
    for candidate in (os.environ.get("NJU_CONNECT_SING_BOX"), shutil.which("sing-box")):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def rule_sets_folder(config_path):
    """Where an install keeps the rule sets: next to the config."""
    return Path(config_path).expanduser().resolve().parent / FILES_DIR


def write_rule_sets(folder, entries, config, settings):
    """Write the rule-set sources into folder; returns ({tag: path}, changed)."""
    folder = Path(folder)
    files, changed = {}, False
    for name, content in rule_set_files(entries, config, settings).items():
        target = folder / f"{name}.json"
        files[name] = target
        if not target.exists() or target.read_text() != content:
            write_atomic(target, content, 0o644)
            changed = True
    return files, changed


def merge_file(path, entries, config, settings):
    """Install into the sing-box config at path. Returns (config changed, warnings).

    sing-box watches local rule sets, so a change in them alone needs no reload.
    """
    path = Path(path).expanduser()
    if "/binConfigs/" in str(path.resolve()):
        raise RuntimeError(f"{path} is rewritten by v2rayN; use `nju-connect export v2rayn` for v2rayN")
    text, data = read_json_config(path, "sing-box")
    files, _ = write_rule_sets(rule_sets_folder(path), entries, config, settings)
    first = not any(isinstance(r, dict) and r.get("tag") in RULE_SETS
                    for r in (data.get("route") or {}).get("rule_set") or [])
    merged = merge_config(data, files, config, settings)
    warnings = []
    if "nju-resolve" in files and not merged["route"].get("default_domain_resolver"):
        warnings.append("route.default_domain_resolver is not set; the resolve rule needs a DNS server "
                        "(see the sing-box section of the guide)")
    content = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    if content == text:
        return False, warnings
    binary = sing_box_binary()
    if binary:
        try:
            check_with(binary, ["check", "-c"], content, "sing-box")
        except RuntimeError:
            # a config split over `-C dir` may not check on its own; then only ours must not break it
            try:
                check_with(binary, ["check", "-c"], text, "sing-box")
            except RuntimeError:
                warnings.append(f"{path} does not check on its own (split config?); merged without checking")
            else:
                raise
    replace_config(path, content, backup=first)
    return True, warnings
