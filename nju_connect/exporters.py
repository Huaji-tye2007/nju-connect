"""`nju-connect export`: rules for proxy tools, and remembered exports kept up to date."""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import VERSION, clash, xray
from .config import DEFAULT_HTTP_PORT, DEFAULT_SERVER, MARKER, bind_port, load_config, load_settings, \
    save_settings, socks_address
from .policy import all_ports, load_policy, nodes, routed
from .util import ask_yes, die, notify, write_atomic


def _header(entries, skipped, config):
    lines = [f"{MARKER} {VERSION} from the NJU aTrust access policy",
             f"Updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"Server: {config.get('server_address', DEFAULT_SERVER)}  Entries: {len(routed(entries))}"]
    return lines + [f"Skipped {n}: {why}" for why, n in sorted(skipped.items())]


def _port_text(entry, sep, range_sep):
    return sep.join(str(lo) if lo == hi else f"{lo}{range_sep}{hi}" for lo, hi in entry.ports)


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
            rule.setdefault("domain_regex", []).append(xray.subdomain_regex(e.value))
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
    """Xray/V2Ray outbound + routing rules to merge (or `--install` to merge them)."""
    tag = settings["export"]["proxy_name"]
    routing = {"rules": xray.routing_rules(entries, config, tag)}
    if xray.domain_strategy(settings):
        routing["domainStrategy"] = xray.domain_strategy(settings)
    return json.dumps({"outbounds": [xray.outbound(config, tag)], "routing": routing},
                      ensure_ascii=False, indent=2) + "\n"


def render_v2rayn(entries, skipped, config, settings, exports):
    return xray.render_v2rayn(entries, config, settings)


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
    "xray": (render_xray, "Xray outbound and routing rules (JSON; --install merges them into your config)"),
    "v2rayn": (render_v2rayn, "v2rayN routing rules to import (JSON list, NJU rules first)"),
    "pac": (render_pac, "PAC file for browsers / system proxy settings"),
    "list": (render_list, "plain list of destinations, ports and protocols"),
}


# ------------------------------------------------------- remembered exports

def remembered(settings=None):
    settings = settings or load_settings()
    return dict(settings["exports"])


INLINE = "inline:"   # prefix of a remembered path whose export embeds the rules
MERGE = "merge:"     # prefix of a remembered Xray config the rules are merged into


def split_mode(value):
    """A remembered value -> (INLINE, MERGE or "", path)."""
    for mode in (INLINE, MERGE):
        if value.startswith(mode):
            return mode, value[len(mode):]
    return "", value


def remembered_paths():
    """{format: path} of the remembered exports, without the inline/merge marker."""
    return {name: split_mode(value)[1] for name, value in remembered().items()}


def render(name, entries=None, skipped=None, refresh=False, inline=False):
    """Render a format. clash-verge/clash-config and sing-box-config reference the remembered
    clash/sing-box rule files; inline=True embeds the rules instead (for another client that
    cannot read those files)."""
    if name not in FORMATS:
        die(f"unknown format {name!r}; choose from: {', '.join(FORMATS)}")
    if entries is None:
        entries, skipped = load_policy(refresh)
    exports = remembered_paths()
    if inline:
        exports = {k: v for k, v in exports.items() if k not in ("clash", "sing-box")}
    return FORMATS[name][0](entries, skipped or {}, load_config(), load_settings(), exports)


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


def remember(name, path, mode=""):
    settings = load_settings()
    settings["exports"][name] = mode + str(Path(path).expanduser().resolve())
    save_settings(settings)


def forget(name):
    settings = load_settings()
    if not settings.remove_option("exports", name):
        die(f"{name} is not a remembered export (see `nju-connect export --list`)")
    save_settings(settings)


def export(name, output=None, refresh=False, install=False, inline=False):
    entries, skipped = load_policy(refresh)
    if install and name == "xray":
        install_xray(entries, output)
        return
    if install:
        if name != "clash-verge":
            die("--install is only for clash-verge and xray; use -o PATH for other formats")
        target = output or clash.verge_script_path()
        if not target:
            die("Clash Verge Rev not found; use -o PATH to write the script somewhere else")
        # the script loads the rules from a rule-provider file inside Verge's directory,
        # which must be (re)written together with it
        exports = remembered_paths()
        ruleset = exports.get("clash") or clash.verge_ruleset_path()
        changed = write_file(ruleset, render("clash", entries, skipped))
        if "clash" not in exports:
            remember("clash", ruleset)
        print(f"{'Wrote' if changed else 'Unchanged:'} {ruleset} (the clash export the script uses)")
        output = target
    if output is None:
        print(render(name, entries, skipped, inline=inline), end="")
        return
    changed = write_file(output, render(name, entries, skipped, inline=inline))
    remember(name, output, INLINE if inline else "")
    print(f"{'Wrote' if changed else 'Unchanged:'} {Path(output).expanduser()} "
          f"({len(entries)} entries); it will be kept up to date")
    if install and changed:
        apply_verge_script()
    if name == "v2rayn":
        print(v2rayn_steps(output))


def v2rayn_steps(output):
    config, settings = load_config(), load_settings()
    routing = xray.v2rayn_routing()
    name = f"\"{routing[0]}\"" if routing else "the one in use"
    lines = ["Next, in v2rayN (details in docs/proxy-clients.md):",
             f"  1. copy {xray.share_link(config, settings)} and choose Configuration > "
             "Import Share Links from clipboard (once)",
             f"  2. Settings > Routing Setting, double-click the routing {name},",
             f"     Import Rules From File: {Path(output).expanduser()}"]
    if routing:
        lines.append("     answer No (replace all): the file already contains that routing's own rules")
    else:
        lines.append("     answer Yes (append), then move the nju-connect rules to the top")
    lines.append(f"  3. in the same window set Domain strategy to {xray.domain_strategy(settings) or 'AsIs'}, "
                 "then Confirm in both windows")
    lines.append("When the policy changes this file is regenerated; repeat step 2 to apply it.")
    return "\n".join(lines)


def install_xray(entries, output):
    target = output or xray.default_config()
    if not target:
        die("no Xray config found in " + ", ".join(map(str, xray.XRAY_CONFIGS)) + "; use -o PATH")
    try:
        changed = xray.merge_file(target, entries, load_config(), load_settings())
    except RuntimeError as e:
        die(str(e))
    remember("xray", target, MERGE)
    target = Path(target).expanduser()
    if not changed:
        print(f"Unchanged: {target} already has the current NJU rules; they will be kept up to date")
        return
    print(f"Merged the NJU outbound and {len(entries)} entries into {target}; they will be kept up to date")
    confirm = (lambda prompt: ask_yes(prompt, default=True)) if sys.stdin.isatty() else None
    restarted, message = xray.restart_xray(confirm)
    print(message if restarted else message[0].upper() + message[1:])


def apply_verge_script():
    """Clash Verge Rev only runs the global script when it rebuilds its configuration,
    which it does on start or when you change something in its GUI, not when the file
    changes. Offer to restart it."""
    if not clash.verge_processes():
        print("Clash Verge Rev will load the script the next time it starts")
        return
    hint = "restart Clash Verge Rev (or open the global script in its GUI and save it) to apply it"
    if not sys.stdin.isatty():
        print(f"To load the new script, {hint}")
    elif ask_yes("Clash Verge Rev loads the script only when it rebuilds its configuration. "
                 "Restart Clash Verge Rev now?", default=True):
        clash.restart_verge()
        print("Restarted Clash Verge Rev; the NJU rules are active once it has started")
    else:
        print(f"Not restarted; {hint}")


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
        mode, path = split_mode(exports[name])
        if mode == MERGE:
            if not refresh_merged(path, entries, quiet):
                continue
        elif not write_file(path, render(name, entries, skipped, inline=mode == INLINE)):
            continue
        changed += 1
        if not quiet:
            print(f"Updated {path} ({name})")
    return changed


def refresh_merged(path, entries, quiet):
    """Merge the new rules into a remembered Xray config and restart Xray if possible."""
    try:
        if not xray.merge_file(path, entries, load_config(), load_settings()):
            return False
    except RuntimeError as e:
        notify(f"Could not update the NJU rules in {path}: {e}")
        return False
    restarted, message = xray.restart_xray()
    if restarted:
        if not quiet:
            print(message)
    else:
        notify(f"The NJU access policy changed and {path} was updated; {message}")
    return True


def list_exports():
    exports = remembered()
    if not exports:
        print("No remembered exports; create one with `nju-connect export FORMAT -o PATH`")
    for name, value in exports.items():
        mode, path = split_mode(value)
        p = Path(path)
        age = f"updated {(datetime.now().timestamp() - p.stat().st_mtime) / 3600:.1f}h ago" \
            if p.exists() else "missing"
        note = {INLINE: ", rules inline", MERGE: ", merged into this Xray config"}.get(mode, "")
        print(f"{name:<16} {path}  ({age}{note})")
