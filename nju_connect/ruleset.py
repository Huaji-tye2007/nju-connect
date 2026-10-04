"""Turn the aTrust access policy into a mihomo (Clash Meta) rule-provider."""

import ipaddress
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import paths
from .config import DEFAULT_SERVER, load_config, load_settings
from .util import log, write_atomic
from .zju import fetch_resource


def port_range(spec):
    lo, _, hi = spec.partition("-")
    lo, hi = int(lo), int(hi or lo)
    if not (0 <= lo <= hi <= 65535):
        raise ValueError(spec)
    return lo, hi


def merge_ranges(ranges):
    out = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def ipv4_networks(host):
    """IPv4 networks for an IP, CIDR or a-b range; None if host isn't IP-like;
    [] for IPv6 (skipped, like zju-connect)."""
    try:
        net = ipaddress.ip_network(host, strict=False)
        return [net] if net.version == 4 else []
    except ValueError:
        pass
    lo, sep, hi = host.partition("-")
    if sep:
        try:
            a, b = ipaddress.ip_address(lo), ipaddress.ip_address(hi)
        except ValueError:
            return None
        if a.version != 4 or b.version != 4:
            return []
        return list(ipaddress.summarize_address_range(a, b))
    return None


def build_rules(resource):
    """Turn the aTrust resource into mihomo classical rules matching exactly
    what zju-connect routes through the VPN."""
    data = json.loads(resource)
    if data.get("code") != 0:
        raise ValueError(f"resource has code {data.get('code')}: {data.get('message')}")
    apps = [app for group in data["data"]["appList"]["data"]["appInfo"]
            for app in group.get("apps") or []]

    entries = defaultdict(list)   # (matcher, value, network) -> port ranges
    skipped = defaultdict(int)

    for app in apps:
        if app.get("accessModel") != "L3VPN":
            if app.get("addressList"):
                skipped["non-L3VPN app address"] += len(app["addressList"])
            continue
        for addr in app.get("addressList") or []:
            proto = addr.get("protocol")
            if proto not in ("tcp", "udp", "all"):
                skipped[f"protocol {proto!r}"] += 1
                continue
            network = None if proto == "all" else proto
            try:
                ports = port_range(addr.get("port", ""))
            except ValueError:
                skipped["bad port"] += 1
                continue

            host = addr.get("host", "").strip().lower().rstrip(".")
            nets = ipv4_networks(host)
            if nets is not None:
                if not nets:
                    skipped["IPv6"] += 1
                for net in nets:
                    entries[("IP-CIDR", str(net), network)].append(ports)
                continue
            if host.startswith("*.") and "*" not in host[2:]:
                entries[("DOMAIN-WILDCARD", host, network)].append(ports)
            elif "*" in host or not host:
                skipped["unsupported wildcard"] += 1
                continue
            else:
                entries[("DOMAIN", host, network)].append(ports)
            for ip in addr.get("ip") or []:
                for net in ipv4_networks(ip) or []:
                    entries[("IP-CIDR", str(net), network)].append(ports)

    rules = []
    for (matcher, value, network), ranges in entries.items():
        ranges = merge_ranges(ranges)
        full = ranges in ([[0, 65535]], [[1, 65535]])
        base = f"{matcher},{value}" + (",no-resolve" if matcher == "IP-CIDR" else "")
        conds = [base]
        if network:
            conds.append(f"NETWORK,{network}")
        if not full:
            conds.append("DST-PORT," + "/".join(
                str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in ranges))
        rules.append(base if len(conds) == 1 else
                     "AND,(" + ",".join(f"({c})" for c in conds) + ")")

    order = {"DOMAIN": 0, "DOMAIN-WILDCARD": 1, "IP-CIDR": 2}
    rules.sort(key=lambda r: (order.get(r.replace("AND,((", "", 1).split(",")[0], 9), r))
    return rules, skipped


def count_rules(path):
    try:
        return sum(1 for line in Path(path).read_text().splitlines() if line.startswith("  - "))
    except FileNotFoundError:
        return 0


def ruleset_path(settings):
    return Path(settings["ruleset"]["output"] or paths.detect_clash()[0])


def update_ruleset(from_file=False, output=None, force=False, min_ratio=0.5):
    config = load_config()
    settings = load_settings()
    if from_file:
        if not paths.RESOURCE.exists():
            raise RuntimeError(f"{paths.RESOURCE} not found; run `nju-connect ruleset` without --from-file")
        resource = paths.RESOURCE.read_bytes()
    else:
        resource = fetch_resource()
    rules, skipped = build_rules(resource)
    output = Path(output) if output else ruleset_path(settings)

    old = count_rules(output)
    if not force and old and len(rules) < old * min_ratio:
        raise RuntimeError(f"refusing to replace {old} rules with {len(rules)}; "
                           "rerun with --force if this is expected")

    header = [
        "# NJU aTrust resources for zju-connect, generated by nju-connect",
        f"# Updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"# Server: {config.get('server_address', DEFAULT_SERVER)}  Rules: {len(rules)}",
    ] + [f"# Skipped {n}: {why}" for why, n in sorted(skipped.items())]
    text = "\n".join(header + ["payload:"] + [f"  - '{r}'" for r in rules]) + "\n"
    write_atomic(output, text, 0o644)
    log(f"Wrote {len(rules)} rules to {output} (was {old})")
    for why, n in sorted(skipped.items()):
        log(f"  skipped {n}: {why}")
