"""The aTrust access policy (resource.json) as a neutral list of entries.

Each entry says which destinations zju-connect sends through the VPN, in the
same way zju-connect itself reads the policy. The exporters turn the entries
into rules for a particular proxy tool.
"""

import ipaddress
import json
from collections import defaultdict, namedtuple

from . import paths
from .util import write_atomic
from .zju import download_resource

FULL_RANGE = ((1, 65535),)

# kind: "domain" (exact host), "subdomains" (any subdomain of value, not value
# itself, like aTrust's *.example.com), "cidr" (IPv4 network), or "node" (a VPN
# node zju-connect itself connects to, which must never go through the VPN);
# network: None (tcp and udp), "tcp" or "udp"; ports: merged (lo, hi) ranges
Entry = namedtuple("Entry", "kind value network ports")


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
    return tuple((lo, hi) for lo, hi in out)


def all_ports(entry):
    return entry.ports in (FULL_RANGE, ((0, 65535),))


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


KIND_ORDER = {"node": 0, "domain": 1, "subdomains": 2, "cidr": 3}


def routed(entries):
    """Entries whose traffic goes through the VPN."""
    return [e for e in entries if e.kind != "node"]


def nodes(entries):
    """VPN node addresses, which must stay DIRECT."""
    return [e for e in entries if e.kind == "node"]


def _node_entries(data):
    found = {}
    groups = data["data"]["appList"]["data"].get("config", {}).get("nodeGroupConf", {}).get("nodeGroupList")
    for group in groups or []:
        for info in group.get("addressInfo") or []:
            host, _, port = str(info.get("address", "")).rpartition(":")
            try:
                ip = ipaddress.ip_address(host)
                port = int(port)
            except ValueError:
                continue   # IPv6 in brackets or the {{sdpcHost}} placeholder (the server itself)
            if ip.version == 4:
                found.setdefault(f"{ip}/32", set()).add((port, port))
    return [Entry("node", cidr, None, merge_ranges(ports)) for cidr, ports in found.items()]


def parse_policy(resource):
    """Return (entries, skipped) for the raw resource.json bytes."""
    data = json.loads(resource)
    if data.get("code") != 0:
        raise ValueError(f"resource has code {data.get('code')}: {data.get('message')}")
    apps = [app for group in data["data"]["appList"]["data"]["appInfo"]
            for app in group.get("apps") or []]

    found = defaultdict(list)   # (kind, value, network) -> port ranges
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
                    found[("cidr", str(net), network)].append(ports)
                continue
            if host.startswith("*.") and "*" not in host[2:]:
                found[("subdomains", host[2:], network)].append(ports)
            elif "*" in host or not host:
                skipped["unsupported wildcard"] += 1
                continue
            else:
                found[("domain", host, network)].append(ports)
            for ip in addr.get("ip") or []:
                for net in ipv4_networks(ip) or []:
                    found[("cidr", str(net), network)].append(ports)

    entries = [Entry(kind, value, network, merge_ranges(ranges))
               for (kind, value, network), ranges in found.items()]
    entries += _node_entries(data)
    entries.sort(key=lambda e: (KIND_ORDER[e.kind], e.value, e.network or ""))
    return entries, dict(skipped)


def update_policy(force=False, min_ratio=0.5):
    """Download the policy and cache it; refuses a suspiciously small policy unless forced."""
    old = parse_policy(paths.RESOURCE.read_bytes())[0] if paths.RESOURCE.exists() else []
    resource = download_resource()
    entries, skipped = parse_policy(resource)
    if not force and old and len(entries) < len(old) * min_ratio:
        raise RuntimeError(f"the new policy has {len(entries)} entries instead of {len(old)}; "
                           "keeping the old one (use --refresh --force if this is expected)")
    write_atomic(paths.RESOURCE, resource, 0o600)
    return entries, skipped


def campus_dns_servers(resource):
    """NJU's internal DNS servers, as listed in the access policy.

    The policy grants VPN clients UDP port 53 on these private addresses; they only
    answer from inside the campus network, which is what the campus check relies on.
    Public resolvers would answer from anywhere, so only private addresses count.
    """
    data = json.loads(resource)
    servers = []
    entries, _ = parse_policy(resource)
    for e in entries:
        if (e.kind == "cidr" and e.value.endswith("/32") and e.network in (None, "udp")
                and not all_ports(e) and any(lo <= 53 <= hi for lo, hi in e.ports)):
            servers.append(e.value[:-3])
    option = (data["data"].get("sdpPolicy", {}).get("data", {}).get("clientOption", {})
              .get("dnsOption") or {})
    servers += [option.get(k, "") for k in ("firstDNS", "secondDNS")]
    found = []
    for server in servers:
        try:
            ip = ipaddress.ip_address(server)
        except ValueError:
            continue
        if ip.version == 4 and ip.is_private and server not in found:
            found.append(server)
    return found


def load_policy(refresh=False, force=False):
    """Entries from the cached resource.json, downloading it first if needed."""
    if refresh or not paths.RESOURCE.exists():
        return update_policy(force)
    return parse_policy(paths.RESOURCE.read_bytes())
