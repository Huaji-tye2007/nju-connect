"""`nju-connect export`: rules for proxy tools, and remembered exports kept up to date."""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import DOCS_URL, VERSION, clash, clients, paths, singbox, xray
from .config import DEFAULT_HTTP_PORT, DEFAULT_SERVER, MARKER, bind_port, load_config, load_settings, \
    save_settings, socks_address
from .policy import all_ports, load_policy, nodes, routed
from .util import ask_yes, die, log, notify, styler, write_atomic


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
    return clash.clash_config(clash.clash_parts(entries, config, settings, exports.get("clash")))


def render_clash_verge(entries, skipped, config, settings, exports):
    return clash.clash_verge_script(clash.clash_parts(entries, config, settings, exports.get("clash")))


def render_sing_box(entries, skipped, config, settings, exports):
    """sing-box rule-set source (version 1)."""
    return singbox.rule_set_source(singbox.policy_rules(entries))


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
        rule_set = {"type": "inline", "tag": "nju-vpn", "rules": singbox.policy_rules(entries)}
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
    routing = {"rules": xray.routing_rules(entries, config, settings, tag)}
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
    "clash-config": (render_clash_config, "mihomo config snippet (--install: kept-up-to-date files in mihomo's folder)"),
    "clash-verge": (render_clash_verge, "Clash Verge Rev global script (--install: into Clash Verge Rev)"),
    "sing-box": (render_sing_box, "sing-box rule-set source (JSON)"),
    "sing-box-config": (render_sing_box_config, "sing-box outbound and route rules (1.11+; --install merges them into the config)"),
    "xray": (render_xray, "Xray outbound and routing rules (--install merges them into the config)"),
    "v2rayn": (render_v2rayn, "v2rayN routing rules to import (JSON list, NJU rules first)"),
    "pac": (render_pac, "PAC file for browsers / system proxy settings"),
    "list": (render_list, "plain list of destinations, ports and protocols"),
}


# ------------------------------------------------------- remembered exports

def remembered(settings=None):
    settings = settings or load_settings()
    return dict(settings["exports"])


INLINE = "inline:"     # prefix of a remembered path whose export embeds the rules
INSTALL = "install:"   # prefix of a remembered --install target (a client's config or folder)


def split_mode(value):
    """A remembered value -> (INLINE, INSTALL or "", path)."""
    for mode in (INLINE, INSTALL):
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


def write_file(path, content, ours=False):
    """Write an export; a file nju-connect did not generate (no MARKER, not `ours`) is backed up first."""
    path = Path(path).expanduser()
    if path.exists():
        old = path.read_text(errors="replace")
        if old == content:
            return False
        if not ours and MARKER not in old[:300]:
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
    if install:
        installer = INSTALLERS.get(name)
        if not installer:
            die(f"--install works with {', '.join(INSTALLERS)}; use -o PATH for other formats")
        try:
            target = installer.target(output)
            installer.apply(target, entries, skipped, interactive=True)
        except RuntimeError as e:
            die(str(e))
        remember(name, target, INSTALL)
        return
    if output is None:
        print(render(name, entries, skipped, inline=inline), end="")
        return
    changed = write_file(output, render(name, entries, skipped, inline=inline))
    remember(name, output, INLINE if inline else "")
    print(f"{'Wrote' if changed else 'Unchanged:'} {Path(output).expanduser()} "
          f"({len(entries)} entries); it will be kept up to date")
    if name == "v2rayn":
        print(v2rayn_steps(output))


def v2rayn_steps(output):
    config, settings = load_config(), load_settings()
    routing = xray.v2rayn_routing()
    lines = [f"Next, in v2rayN (step by step: {DOCS_URL}#v2rayn):",
             f"  1. copy {xray.share_link(config, settings)} and choose Configuration > "
             "Import Share Links from clipboard (once)",
             "  2. Settings > Routing Setting, double-click the routing you use,",
             f"     Import Rules From File: {Path(output).expanduser()}"]
    if routing:
        lines.append(f"     answer No (replace all): the file already contains the rules of \"{routing[0]}\",")
        lines.append("     the active routing, so import it there (activate another one and export again to change)")
    else:
        lines.append("     answer Yes (append), then move the nju-connect rules to the top")
    lines.append("     and Confirm both windows; v2rayN restarts its core with the new rules")
    if xray.domain_strategy(settings):
        lines.append(f"  3. set Domain strategy in the Routing Setting window to {xray.domain_strategy(settings)}")
    lines.append("When the policy changes this file is regenerated; repeat step 2 to apply it.")
    return "\n".join(lines)


def apply_verge_script(interactive=True):
    """Clash Verge Rev only runs the global script when it rebuilds its configuration,
    which it does on start or when you change something in its GUI, not when the file
    changes. Offer to restart it."""
    hint = "restart Clash Verge Rev (or open the global script in its GUI and save it) to apply it"
    if not interactive:
        notify(f"The nju-connect script for Clash Verge Rev changed; {hint}")
        return
    if not clash.verge_processes():
        print("Clash Verge Rev will load the script the next time it starts")
        return
    if not sys.stdin.isatty():
        print(f"To load the new script, {hint}")
    elif ask_yes("Clash Verge Rev loads the script only when it rebuilds its configuration. "
                 "Restart Clash Verge Rev now?", default=True):
        clash.restart_verge()
        print("Restarted Clash Verge Rev; the NJU rules are active once it has started")
    else:
        print(f"Not restarted; {hint}")


# ------------------------------------------------------------------ --install

class Installer:
    """Puts the NJU rules into one client and keeps them there (export FORMAT --install)."""

    def target(self, output):
        """The file or folder to install into (output: the user's -o)."""
        raise NotImplementedError

    def apply(self, target, entries, skipped, interactive):
        """Install or refresh; True if a file changed. RuntimeError with the reason on failure."""
        raise NotImplementedError


def _reload(core, target, interactive):
    """Make a core load its changed config, asking first when run from a terminal."""
    confirm = (lambda prompt: ask_yes(prompt, default=True)) if interactive and sys.stdin.isatty() else None
    done, message = clients.reload(core, target, confirm)
    if interactive:
        print(message if done else message[0].upper() + message[1:])
    elif not done:
        notify(f"The NJU rules in {target} changed; {message}")


def _provider_files(home, entries, skipped):
    """Write the mihomo provider files under home; True if any changed."""
    config, settings = load_config(), load_settings()
    files = clash.provider_files(entries, config, settings, _header(entries, skipped, config))
    changed = False
    for relative, content in files.items():
        changed = write_file(Path(home) / relative, content) or changed
    return changed


class VergeInstaller(Installer):
    def target(self, output):
        target = output or clash.verge_script_path()
        if not target:
            raise RuntimeError("Clash Verge Rev not found; use -o PATH to write the script somewhere else")
        clients.require_writable(paths.VERGE_DIR)
        return Path(target).expanduser()

    def apply(self, target, entries, skipped, interactive):
        files = _provider_files(paths.VERGE_DIR, entries, skipped)
        script = clash.clash_verge_script(clash.installed_parts(load_settings()))
        changed = write_file(target, script)
        if interactive:
            print(f"{'Wrote' if changed else 'Unchanged:'} {target}, and its rule and proxy files in "
                  f"{paths.VERGE_DIR}; they will be kept up to date")
        if changed:
            apply_verge_script(interactive)
        return files or changed


class MihomoInstaller(Installer):
    def target(self, output):
        home = clients.choose_config("mihomo", output)
        if home.suffix in (".yaml", ".yml") or home.is_file():
            home = home.parent   # given config.yaml: the provider paths are relative to its folder
        clients.require_writable(home)
        return home

    def apply(self, target, entries, skipped, interactive):
        changed = _provider_files(target, entries, skipped)
        snippet = clash.clash_config(clash.installed_parts(load_settings()), intro="merge into config.yaml once.")
        shown = _shown_snippets()
        last = shown.get(str(target))
        if interactive:
            print(mihomo_steps(target, snippet, same=last == snippet))
        elif last not in (None, snippet):
            # names or group settings changed: only the user can update config.yaml
            notify(f"The nju-connect part of {target}/config.yaml changed; run "
                   "`nju-connect export clash-config --install` and merge it again")
            return changed
        if last != snippet:
            shown[str(target)] = snippet
            write_atomic(SNIPPETS, json.dumps(shown, ensure_ascii=False, indent=2) + "\n", 0o644)
        return changed


class SingBoxInstaller(Installer):
    def target(self, output):
        target = clients.choose_config("sing-box", output)
        clients.require_writable(target)
        return target

    def apply(self, target, entries, skipped, interactive):
        changed, warnings = singbox.merge_file(target, entries, load_config(), load_settings())
        for warning in warnings:
            if interactive:
                print(f"Warning: {warning}")
            else:
                log(f"warning: {warning}")
        if interactive:
            print(f"{'Merged the NJU outbound and rules into' if changed else 'Unchanged:'} {target}; "
                  f"its rule sets in {Path(target).parent / singbox.FILES_DIR} will be kept up to date")
        if changed:
            _reload("sing-box", target, interactive)
        return changed


class XrayInstaller(Installer):
    def target(self, output):
        target = clients.choose_config("xray", output)
        clients.require_writable(target)
        return target

    def apply(self, target, entries, skipped, interactive):
        changed = xray.merge_file(target, entries, load_config(), load_settings())
        if interactive:
            print(f"{'Merged the NJU outbound and rules into' if changed else 'Unchanged:'} {target}; "
                  "they will be kept up to date")
        if changed:
            _reload("xray", target, interactive)
        return changed


INSTALLERS = {"clash-verge": VergeInstaller(), "clash-config": MihomoInstaller(),
              "sing-box-config": SingBoxInstaller(), "xray": XrayInstaller()}

SNIPPETS = paths.STATE_DIR / "mihomo-snippets.json"   # the merge snippet last shown, per mihomo folder


def _shown_snippets():
    try:
        return json.loads(SNIPPETS.read_text())
    except (OSError, ValueError):
        return {}


def mihomo_steps(home, snippet, same=False):
    """What to merge into mihomo's config.yaml, once, as numbered steps."""
    paint = styler()
    sections, current = {}, None
    for line in snippet.splitlines()[1:]:
        if not line.startswith(" "):
            current = line.split(":")[0]
            sections[current] = []
        else:
            sections[current].append(line.strip())
    rule = "─" * 64
    out = [paint(f"nju-connect: mihomo set up in {home}", "1;34"), rule,
           paint("✓", "1;32") + " Written, kept up to date by the service (mihomo reloads them by itself):"]
    for path, what in ((clash.PROVIDER_FILES["nju-vpn"], "NJU resources"),
                       (clash.PROVIDER_FILES["nju-direct"], "VPN server and nodes (must stay direct)"),
                       (clash.PROVIDER_FILES[clash.PROXY_PROVIDER], "NJUConnect and its direct fallback")):
        out.append(f"    {paint(path.ljust(26), '32')}{paint(what, '2')}")
    out += ["", paint(f"── Merge into {Path(home) / 'config.yaml'} once ", "1;34")]
    if same:
        out.append(paint("   The same as last time: skip it if you have merged it already.", "2"))
    steps = (("proxy-providers", "(create the key if it is missing)"), ("proxy-groups", ""),
             ("rule-providers", ""), ("rules", "← before all your other rules"))
    for number, (key, note) in zip("①②③④", steps):
        if key == "rules":
            head = paint("At the TOP of rules:", "1;33") + "  " + paint(note, "1;33")
        else:
            head = f"Under {paint(key, '32')}:" + (f"  {paint(note, '2')}" if note else "")
        out.append(f"{number} {head}")
        out += [f"    {line.split('  #')[0]}" for line in sections[key]]
        out.append("")
    out += ["Then reload mihomo once (restart it, or PUT /configs through its API).", rule,
            "· Policy changes and proxy.socks_port: applied automatically, nothing to redo.",
            "· export.proxy_name / group_name / group_type / health_*: run this command again",
            "  and update ② and ④ (the service notifies you when that is needed).",
            f"· Details: {DOCS_URL}#原生-mihomo--自己维护的-configyaml"]
    return "\n".join(out)


# ------------------------------------------------- keeping remembered exports up to date

MANUAL_IMPORT = ("v2rayn",)   # exports a client only reads when the user imports them again


def refresh_exports(entries=None, skipped=None, quiet=False):
    """Regenerate every remembered export; returns the number of files rewritten."""
    exports = remembered()
    if not exports:
        return 0
    if entries is None:
        entries, skipped = load_policy()
    changed = 0
    # the clash/sing-box rule files first: clash-config and sing-box-config may point at them
    for name in sorted(exports, key=lambda n: n not in ("clash", "sing-box")):
        if name not in FORMATS:
            continue
        mode, path = split_mode(exports[name])
        if mode == INSTALL:
            try:
                if name not in INSTALLERS or not INSTALLERS[name].apply(Path(path), entries, skipped,
                                                                         interactive=False):
                    continue
            except RuntimeError as e:
                notify(f"Could not update the NJU rules for {name} in {path}: {e}")
                continue
        elif not write_file(path, render(name, entries, skipped, inline=mode == INLINE), ours=True):
            continue
        changed += 1
        if not quiet:
            again = "; import it again in your client" if mode == INLINE or name in MANUAL_IMPORT else ""
            print(f"Updated {path} ({name}){again}")
    return changed


def list_exports():
    exports = remembered()
    if not exports:
        print("No remembered exports; create one with `nju-connect export FORMAT -o PATH`")
    for name, value in exports.items():
        mode, path = split_mode(value)
        p = Path(path)
        age = f"updated {(datetime.now().timestamp() - p.stat().st_mtime) / 3600:.1f}h ago" \
            if p.exists() else "missing"
        note = {INLINE: ", rules inline", INSTALL: ", installed"}.get(mode, "")
        print(f"{name:<16} {path}  ({age}{note})")
