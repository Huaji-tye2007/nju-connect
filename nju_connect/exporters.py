"""`nju-connect export`: rules for proxy tools, and remembered exports kept up to date."""

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import VERSION, clash
from .config import DEFAULT_HTTP_PORT, DEFAULT_SERVER, MARKER, bind_port, load_config, load_settings, \
    save_settings, socks_address
from .policy import all_ports, load_policy, nodes, routed
from .util import die, write_atomic


def _header(entries, skipped, config):
    lines = [f"{MARKER} {VERSION} from the NJU aTrust access policy",
             f"Updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"Server: {config.get('server_address', DEFAULT_SERVER)}  Entries: {len(routed(entries))}"]
    return lines + [f"Skipped {n}: {why}" for why, n in sorted(skipped.items())]


def _port_text(entry, sep, range_sep):
    return sep.join(str(lo) if lo == hi else f"{lo}{range_sep}{hi}" for lo, hi in entry.ports)


def _regex(domain):
    return "^.+\\." + domain.replace(".", "\\.") + "$"


# ------------------------------------------------------------------ formats

def render_clash(entries, skipped, config, settings, exports):
    return clash.rule_provider(entries, _header(entries, skipped, config), clash.resolve_domains(settings))


def render_clash_config(entries, skipped, config, settings, exports):
    return clash.clash_config(entries, config, settings, exports.get("clash"))


def render_clash_verge(entries, skipped, config, settings, exports):
    return clash.clash_verge_script(entries, config, settings, exports.get("clash"))


def _sing_box_rules(entries):
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
            rule.setdefault("domain_regex", []).append(_regex(e.value))
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


def render_sing_box(entries, skipped, config, settings, exports):
    """sing-box rule-set source (version 1)."""
    return json.dumps({"version": 1, "rules": _sing_box_rules(entries)}, ensure_ascii=False, indent=2) + "\n"


def render_sing_box_config(entries, skipped, config, settings, exports):
    """sing-box outbound + route rules around the nju-vpn rule-set.

    The rule-set is matched once by domain, then the destination is resolved (only for
    the configured domains) and matched again, so NJU hosts that are only covered by an
    IP range are routed like zju-connect routes them. Needs sing-box 1.11+.
    """
    tag = settings["export"]["proxy_name"]
    host, port = socks_address(config)
    if "sing-box" in exports:
        rule_set = {"type": "local", "tag": "nju-vpn", "format": "source", "path": exports["sing-box"]}
    else:
        rule_set = {"type": "inline", "tag": "nju-vpn", "rules": _sing_box_rules(entries)}
    server = config.get("server_address", DEFAULT_SERVER)
    rules = [{"domain": [server], "outbound": "direct"}]
    node_ips = [n.value for n in nodes(entries)]
    if node_ips:
        rules.append({"ip_cidr": node_ips, "outbound": "direct"})
    rules.append({"rule_set": "nju-vpn", "outbound": tag})
    resolve = clash.resolve_domains(settings)
    if resolve:
        rules.append({"action": "resolve"} if "*" in resolve else
                     {"domain_suffix": list(resolve), "action": "resolve"})
        rules.append({"rule_set": "nju-vpn", "outbound": tag})
    return json.dumps({
        "outbounds": [{"type": "socks", "tag": tag, "server": host, "server_port": port, "version": "5"}],
        "route": {"rule_set": [rule_set], "rules": rules},
    }, ensure_ascii=False, indent=2) + "\n"


def render_xray(entries, skipped, config, settings, exports):
    """Xray/V2Ray outbound + routing rules; one rule per (ports, network) and per
    domain/IP kind, because Xray ANDs the conditions inside a rule."""
    tag = settings["export"]["proxy_name"]
    host, port = socks_address(config)
    groups = {}
    for e in routed(entries):
        field = "ip" if e.kind == "cidr" else "domain"
        key = (field, e.ports, e.network)
        rule = groups.get(key)
        if rule is None:
            rule = groups[key] = {"type": "field", field: [], "outboundTag": tag}
            if not all_ports(e):
                rule["port"] = _port_text(e, ",", "-")
            if e.network:
                rule["network"] = e.network
        rule[field].append(e.value if e.kind == "cidr" else
                           f"full:{e.value}" if e.kind == "domain" else f"regexp:{_regex(e.value)}")
    server = config.get("server_address", DEFAULT_SERVER)
    # zju-connect's own connections to the server and VPN nodes must stay direct
    rules = [{"type": "field", "domain": [f"full:{server}"], "outboundTag": "direct"}]
    node_ips = [n.value for n in nodes(entries)]
    if node_ips:
        rules.append({"type": "field", "ip": node_ips, "outboundTag": "direct"})
    rules += list(groups.values())
    routing = {"rules": rules}
    if clash.resolve_domains(settings):
        # resolve a domain as soon as an IP rule is checked, so the IP ranges apply to NJU
        # hosts too; IPIfNonMatch would not help once a later domain rule (e.g. geosite:cn)
        # matches. Xray cannot limit the resolving to some domains.
        routing["domainStrategy"] = "IPOnDemand"
    return json.dumps({
        "outbounds": [{"tag": tag, "protocol": "socks",
                       "settings": {"servers": [{"address": host, "port": port}]}}],
        "routing": routing,
    }, ensure_ascii=False, indent=2) + "\n"


PAC_TEMPLATE = """// {header}
// Sends NJU resources to zju-connect's HTTP proxy and everything else DIRECT.
var PROXY = "PROXY {proxy}; DIRECT";
var EXACT = {exact};
var SUBDOMAINS = {subdomains};
var NETS = {nets};
var RESOLVE = {resolve};   // domains resolved to check NETS ([] = only IP literals, ["*"] = all)

function portOf(url) {{
  var m = url.match(/^[a-z]+:\\/\\/(?:[^@\\/]*@)?(?:\\[[^\\]]*\\]|[^:\\/]+)(?::(\\d+))?/i);
  if (m && m[1]) return parseInt(m[1], 10);
  return url.substring(0, 6).toLowerCase() === "https:" ? 443 : 80;
}}

function inRanges(port, ranges) {{
  if (ranges === null) return true;
  for (var i = 0; i < ranges.length; i++) {{
    if (port >= ranges[i][0] && port <= ranges[i][1]) return true;
  }}
  return false;
}}

function FindProxyForURL(url, host) {{
  host = host.toLowerCase();
  var port = portOf(url);
  if (EXACT.hasOwnProperty(host) && inRanges(port, EXACT[host])) return PROXY;
  for (var dot = host.indexOf("."); dot !== -1; dot = host.indexOf(".", dot + 1)) {{
    var parent = host.substring(dot + 1);
    if (SUBDOMAINS.hasOwnProperty(parent) && inRanges(port, SUBDOMAINS[parent])) return PROXY;
  }}
  var ip = /^\\d+\\.\\d+\\.\\d+\\.\\d+$/.test(host) ? host : null;
  for (var r = 0; ip === null && r < RESOLVE.length; r++) {{
    if (RESOLVE[r] === "*" || host === RESOLVE[r] || dnsDomainIs(host, "." + RESOLVE[r])) {{
      ip = dnsResolve(host);
      break;
    }}
  }}
  if (ip) {{
    for (var i = 0; i < NETS.length; i++) {{
      if (isInNet(ip, NETS[i][0], NETS[i][1]) && inRanges(port, NETS[i][2])) return PROXY;
    }}
  }}
  return "DIRECT";
}}
"""


def render_pac(entries, skipped, config, settings, exports):
    """PAC file (TCP only: browsers speak HTTP/HTTPS through the HTTP proxy)."""
    import ipaddress
    exact, subdomains, nets = {}, {}, {}
    for e in routed(entries):
        if e.network == "udp":
            continue
        ports = None if all_ports(e) else [list(r) for r in e.ports]
        target = {"domain": exact, "subdomains": subdomains, "cidr": nets}[e.kind]
        if e.value in target and (target[e.value] is None or ports is None):
            target[e.value] = None
        elif e.value in target:
            target[e.value] = sorted(target[e.value] + ports)
        else:
            target[e.value] = ports
    net_list = []
    for cidr, ports in nets.items():
        net = ipaddress.ip_network(cidr)
        net_list.append([str(net.network_address), str(net.netmask), ports])
    http_port = bind_port(config.get("http_bind", ""), DEFAULT_HTTP_PORT)
    return PAC_TEMPLATE.format(
        header=" | ".join(_header(entries, skipped, config)[:3]),
        proxy=f"127.0.0.1:{http_port}",
        exact=json.dumps(exact, sort_keys=True), subdomains=json.dumps(subdomains, sort_keys=True),
        nets=json.dumps(net_list),
        resolve=json.dumps(list(clash.resolve_domains(settings))))


def render_list(entries, skipped, config, settings, exports):
    lines = [f"# {line}" for line in _header(entries, skipped, config)]
    lines.append("# destination\tports\tnetwork")
    for e in routed(entries):
        host = f"*.{e.value}" if e.kind == "subdomains" else e.value
        ports = "all" if all_ports(e) else _port_text(e, ",", "-")
        lines.append(f"{host}\t{ports}\t{e.network or 'tcp+udp'}")
    for n in nodes(entries):
        lines.append(f"# VPN node (keep direct): {n.value}\t{_port_text(n, ',', '-')}")
    return "\n".join(lines) + "\n"


FORMATS = {
    "clash": (render_clash, "mihomo/Clash Meta rule-provider (YAML, classical)"),
    "clash-config": (render_clash_config, "mihomo config snippet: proxy, group, rule-provider, rules"),
    "clash-verge": (render_clash_verge, "Clash Verge Rev global script (use --install)"),
    "sing-box": (render_sing_box, "sing-box rule-set source (JSON)"),
    "sing-box-config": (render_sing_box_config, "sing-box outbound and route rules to merge (JSON, 1.11+)"),
    "xray": (render_xray, "Xray/V2Ray outbound and routing rules (JSON)"),
    "pac": (render_pac, "PAC file for browsers / system proxy settings"),
    "list": (render_list, "plain list of destinations, ports and protocols"),
}


# ------------------------------------------------------- remembered exports

def remembered(settings=None):
    settings = settings or load_settings()
    return dict(settings["exports"])


def render(name, entries=None, skipped=None, refresh=False):
    if name not in FORMATS:
        die(f"unknown format {name!r}; choose from: {', '.join(FORMATS)}")
    if entries is None:
        entries, skipped = load_policy(refresh)
    return FORMATS[name][0](entries, skipped or {}, load_config(), load_settings(), remembered())


def write_file(path, content):
    """Write an export; a file that nju-connect did not generate is backed up first."""
    path = Path(path).expanduser()
    if path.exists():
        old = path.read_text(errors="replace")
        if old == content:
            return False
        if MARKER not in old[:300]:
            backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(path, backup)
            print(f"Backed up {path} -> {backup}")
    write_atomic(path, content, 0o644)
    return True


def remember(name, path):
    settings = load_settings()
    settings["exports"][name] = str(Path(path).expanduser().resolve())
    save_settings(settings)


def forget(name):
    settings = load_settings()
    if not settings.remove_option("exports", name):
        die(f"{name} is not a remembered export (see `nju-connect export --list`)")
    save_settings(settings)


def export(name, output=None, refresh=False, install=False):
    entries, skipped = load_policy(refresh)
    if install:
        if name != "clash-verge":
            die("--install is only for clash-verge; use -o PATH for other formats")
        target = output or clash.verge_script_path()
        if not target:
            die("Clash Verge Rev not found; use -o PATH to write the script somewhere else")
        # the script loads the rules from a rule-provider file inside Verge's directory
        exports = remembered()
        if "clash" not in exports:
            ruleset = clash.verge_ruleset_path()
            write_file(ruleset, render("clash", entries, skipped))
            remember("clash", ruleset)
            print(f"Wrote {ruleset} (remembered as the clash export)")
        output = target
    if output is None:
        print(render(name, entries, skipped), end="")
        return
    changed = write_file(output, render(name, entries, skipped))
    remember(name, output)
    print(f"{'Wrote' if changed else 'Unchanged:'} {Path(output).expanduser()} "
          f"({len(entries)} entries); it will be kept up to date")
    if name == "clash-verge":
        print("Reload your profile in Clash Verge to apply it")


def refresh_exports(entries=None, skipped=None, quiet=False):
    """Regenerate every remembered export; returns the number of files rewritten."""
    exports = remembered()
    if not exports:
        return 0
    if entries is None:
        entries, skipped = load_policy()
    changed = 0
    # the clash rule-provider first: the clash-verge/clash-config exports point at it
    for name in sorted(exports, key=lambda n: n not in ("clash", "sing-box")):
        if name not in FORMATS:
            continue
        if write_file(exports[name], render(name, entries, skipped)):
            changed += 1
            if not quiet:
                print(f"Updated {exports[name]} ({name})")
    return changed


def list_exports():
    exports = remembered()
    if not exports:
        print("No remembered exports; create one with `nju-connect export FORMAT -o PATH`")
    for name, path in exports.items():
        p = Path(path)
        age = f"updated {(datetime.now().timestamp() - p.stat().st_mtime) / 3600:.1f}h ago" \
            if p.exists() else "missing"
        print(f"{name:<13} {path}  ({age})")
