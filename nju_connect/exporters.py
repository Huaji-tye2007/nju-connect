"""`nju-connect export`: rules for proxy tools, and remembered exports kept up to date.

Two kinds of export: `--install` puts the rules where the client keeps reading them (files
nju-connect keeps up to date, merged into the client's config once), so policy changes
reach it by themselves; everything else is self-contained (rules inline), and the client
needs it imported or merged again when the policy changes.
"""

import json
import shutil
import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import DOCS_URL, VERSION, clash, clients, paths, singbox, xray
from .config import DEFAULT_HTTP_PORT, DEFAULT_SERVER, MARKER, bind_port, load_config, load_settings, \
    save_settings, socks_address
from .policy import all_ports, load_policy, nodes, routed
from .util import ask_yes, die, log, notify, read_json_config, styler, write_atomic


def _header(entries, skipped, config):
    lines = [f"{MARKER} {VERSION} from the NJU aTrust access policy",
             f"Updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"Server: {config.get('server_address', DEFAULT_SERVER)}  Entries: {len(routed(entries))}"]
    return lines + [f"Skipped {n}: {why}" for why, n in sorted(skipped.items())]


def _port_text(entry, sep, range_sep):
    return sep.join(str(lo) if lo == hi else f"{lo}{range_sep}{hi}" for lo, hi in entry.ports)


# ------------------------------------------------------------------ formats

def render_clash(entries, skipped, config, settings):
    return clash.rule_provider(entries, _header(entries, skipped, config), clash.resolve_domains(settings))


def render_clash_config(entries, skipped, config, settings):
    return clash.clash_config(clash.clash_parts(entries, config, settings))


def render_mihomo_script(entries, skipped, config, settings):
    return clash.clash_verge_script(clash.clash_parts(entries, config, settings),
                                    regenerate="nju-connect export mihomo-script -o PATH")


def render_sing_box(entries, skipped, config, settings):
    """sing-box rule-set source (version 1)."""
    return singbox.rule_set_source(singbox.policy_rules(entries))


def render_sing_box_config(entries, skipped, config, settings):
    """sing-box outbound + route rules, rules inline (`--install` keeps them in files).

    The rule-set is matched once by domain, then the destination is resolved (only for
    the configured domains) and matched again, so NJU hosts that are only covered by an
    IP range are routed like zju-connect routes them. Needs sing-box 1.11+.
    """
    tag = settings["export"]["proxy_name"]
    host, port = socks_address(config)
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


def render_xray(entries, skipped, config, settings):
    """Xray/V2Ray outbound + routing rules to merge (or `--install` to merge them)."""
    tag = settings["export"]["proxy_name"]
    routing = {"rules": xray.routing_rules(entries, config, settings, tag)}
    if xray.domain_strategy(settings):
        routing["domainStrategy"] = xray.domain_strategy(settings)
    return json.dumps({"outbounds": [xray.outbound(config, tag)], "routing": routing},
                      ensure_ascii=False, indent=2) + "\n"


def render_v2rayn(entries, skipped, config, settings):
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


def render_pac(entries, skipped, config, settings):
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


def render_list(entries, skipped, config, settings):
    lines = [f"# {line}" for line in _header(entries, skipped, config)]
    lines.append("# destination\tports\tnetwork")
    for e in routed(entries):
        host = f"*.{e.value}" if e.kind == "subdomains" else e.value
        ports = "all" if all_ports(e) else _port_text(e, ",", "-")
        lines.append(f"{host}\t{ports}\t{e.network or 'tcp+udp'}")
    for n in nodes(entries):
        lines.append(f"# VPN node (keep direct): {n.value}\t{_port_text(n, ',', '-')}")
    return "\n".join(lines) + "\n"


# render: (entries, skipped, config, settings) -> text, or None for an install-only format.
# summary: one line for the list of formats; description, output (what -o means), install
# (what --install does, None if not supported) and examples: that format's own --help.
Format = namedtuple("Format", "render summary description output install examples")

FORMATS = {
    "clash": Format(
        render_clash, "mihomo rule-provider file of the NJU resources (YAML)",
        "The NJU resources as a mihomo rule-provider (behavior: classical), for a config that loads it "
        "itself. The VPN server and node rules are not in it; for mihomo itself, `clash-config --install` "
        "is usually what you want.",
        "the rule file to write; it is kept up to date", None,
        ["nju-connect export clash -o ~/.config/mihomo/ruleset/nju-vpn.yaml"]),
    "clash-config": Format(
        render_clash_config, "mihomo: --install for mihomo itself, else a self-contained snippet",
        "With --install: writes the NJU rule sets and the NJUConnect proxy as files into mihomo's home "
        "folder, kept up to date by the service (mihomo rereads them by itself), and prints what to merge "
        "into config.yaml once. Without: prints a self-contained snippet (everything inline) to merge by "
        "hand, again after each policy change.",
        "with --install: mihomo's home folder (or its config.yaml; otherwise found from the running "
        "mihomo, else ~/.config/mihomo); without: the snippet file",
        "write the files into mihomo's folder and print what to merge into config.yaml once",
        ["nju-connect export clash-config --install", "nju-connect export clash-config --install -o ~/.config/mihomo"]),
    "clash-verge": Format(
        None, "Clash Verge Rev (use --install)",
        "Writes Clash Verge Rev's global extension script and the rule and proxy files it loads; the "
        "service keeps the files up to date and Clash Verge Rev rereads them by itself. For FlClash, "
        "Clash Party and other mihomo GUIs, use mihomo-script.",
        "a script path instead of Clash Verge Rev's profiles/Script.js",
        "write the script and its files (required)",
        ["nju-connect export clash-verge --install"]),
    "mihomo-script": Format(
        render_mihomo_script, "override script for FlClash, Clash Party and other mihomo GUIs",
        "A main(config) override script with the NJUConnect proxy, the NJU group and the rules inline. "
        "Import it in the client (FlClash: Tools > Advanced settings > Scripts; Clash Party: Overrides). "
        "The file is kept up to date, but the client keeps its own copy: import it again after it changes.",
        "the script to write", None,
        ["nju-connect export mihomo-script -o ~/nju-mihomo.js"]),
    "sing-box": Format(
        render_sing_box, "sing-box rule-set source of the NJU resources (JSON)",
        "The NJU resources as a sing-box rule-set source, for a config that loads it itself; for sing-box "
        "itself, `sing-box-config --install` is usually what you want.",
        "the rule-set file to write; it is kept up to date", None,
        ["nju-connect export sing-box -o ~/nju-vpn.json"]),
    "sing-box-config": Format(
        render_sing_box_config, "sing-box: --install merges into its config, else a self-contained snippet",
        "With --install: merges the NJUConnect outbound, three local rule sets and the rules into the "
        "sing-box config (1.11+) and reloads sing-box; the rule sets are kept up to date and sing-box "
        "rereads them by itself. A config you cannot write (e.g. root's /etc/sing-box) is left alone: the "
        f"rule sets go to {paths.SING_BOX_RULE_SETS} and what to merge once is printed. Without "
        "--install: prints a self-contained snippet (rules inline), to merge again after policy changes; "
        "its direct rules use the outbound tagged `direct`, which your config must have.",
        "with --install: the sing-box config (otherwise found from the running sing-box); "
        "without: the snippet file",
        "merge into the config (or print what to merge once) and keep the rules up to date",
        ["nju-connect export sing-box-config --install"]),
    "xray": Format(
        render_xray, "Xray: --install merges into its config, else the outbound and routing rules",
        "With --install: merges the NJUConnect outbound and the routing rules into the Xray config and "
        "restarts Xray, again whenever the policy changes. A config you cannot write is left alone and "
        "what to merge is printed (merge it again after policy changes, or run Xray as your user). "
        "Without --install: prints the outbound and routing rules; their direct rules use the outbound "
        "tagged `direct` (freedom), which your config must have.",
        "with --install: the Xray config (otherwise found from the running Xray); without: the file to write",
        "merge into the config and keep it up to date",
        ["nju-connect export xray --install"]),
    "v2rayn": Format(
        render_v2rayn, "v2rayN routing rules to import (NJU rules first)",
        "A rules file for v2rayN's Import Rules From File: the NJU rules followed by the rules of the "
        "active routing, to import with Replace. Prints the share link of the NJUConnect node and the "
        "steps. The file is kept up to date; import it again after it changes.",
        "the rules file to write", None,
        ["nju-connect export v2rayn -o ~/nju-v2rayn.json"]),
    "pac": Format(
        render_pac, "PAC file for browsers and system proxy settings",
        "A PAC file sending NJU resources to zju-connect's HTTP proxy and everything else DIRECT. "
        "Point the browser or system proxy at file:///home/<you>/nju.pac; it is kept up to date.",
        "the PAC file to write", None,
        ["nju-connect export pac -o ~/nju.pac"]),
    "list": Format(
        render_list, "plain list of destinations, ports and protocols",
        "Every destination of the access policy with its ports and protocol, and the VPN nodes that "
        "must stay direct, to convert into any other tool's format.",
        "the file to write", None,
        ["nju-connect export list"]),
}

MANUAL_IMPORT = ("v2rayn", "mihomo-script")   # the client reads them only when imported again


def render(name, entries=None, skipped=None, refresh=False):
    if name not in FORMATS or FORMATS[name].render is None:
        die(f"{name!r} cannot be printed; see `nju-connect export {name} -h`")
    if entries is None:
        entries, skipped = load_policy(refresh)
    return FORMATS[name].render(entries, skipped or {}, load_config(), load_settings())


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


# ------------------------------------------------------- remembered exports

INSTALL = "install:"   # prefix of a remembered --install target (a client's config or folder)

# label: the [exports] key, the format name, or format@N for further exports of a format
Export = namedtuple("Export", "label format mode path")


def remembered(settings=None):
    """[Export] of the remembered exports, in the order they were made."""
    settings = settings or load_settings()
    found = []
    for label, value in settings["exports"].items():
        mode = INSTALL if value.startswith(INSTALL) else ""
        found.append(Export(label, label.split("@")[0], mode, value[len(mode):]))
    return found


def remember(name, path, mode=""):
    """Remember an export; the same format and path again updates its entry."""
    settings = load_settings()
    path = str(Path(path).expanduser().resolve())
    exports = settings["exports"]
    same = [e.label for e in remembered(settings) if e.format == name and e.path == path]
    if same:
        label = same[0]
    elif name not in exports:
        label = name
    else:
        label = next(f"{name}@{n}" for n in range(2, 1000) if f"{name}@{n}" not in exports)
    exports[label] = mode + path
    save_settings(settings)
    return label


def forget(what):
    """Forget the exports a label, format name or path names; returns the forgotten ones."""
    settings = load_settings()
    path = str(Path(what).expanduser().resolve()) if "/" in what or what.startswith("~") else None
    gone = [e for e in remembered(settings) if what in (e.label, e.format) or e.path == path]
    if not gone:
        die(f"{what} is not a remembered export (see `nju-connect export --list`)")
    for e in gone:
        settings.remove_option("exports", e.label)
    save_settings(settings)
    return gone


def export(name, output=None, refresh=False, install=False):
    if name not in FORMATS:
        die(f"unknown format {name!r}; choose from: {', '.join(FORMATS)}")
    entries, skipped = load_policy(refresh)
    if install:
        installer = INSTALLERS.get(name)
        if not installer:
            die(f"--install works with {', '.join(INSTALLERS)}; use -o PATH for {name}")
        try:
            target = installer.target(output)
            installer.apply(target, entries, skipped, interactive=True)
        except RuntimeError as e:
            die(str(e))
        if installer.keeps(target):
            remember(name, target, INSTALL)
        return
    if FORMATS[name].render is None:
        die(f"{name} only works with --install; for FlClash, Clash Party and other mihomo GUIs "
            "use `nju-connect export mihomo-script -o PATH`")
    if output is None:
        print(render(name, entries, skipped), end="")
        return
    changed = write_file(output, render(name, entries, skipped))
    remember(name, output)
    print(f"{'Wrote' if changed else 'Unchanged:'} {Path(output).expanduser()} "
          f"({len(entries)} entries); it will be kept up to date")
    if name == "v2rayn":
        print(v2rayn_steps(output))
    elif name == "mihomo-script":
        print("Import it as an override script in your mihomo GUI (FlClash: Tools > Advanced settings > "
              f"Scripts; Clash Party: Overrides), and again after it changes: {DOCS_URL}#flclash")


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


def apply_verge_script(interactive=True, pending=False):
    """Clash Verge Rev only runs the global script when it rebuilds its configuration,
    which it does on start or when you change something in its GUI, not when the file
    changes. Offer to restart it (pending: the script is unchanged but not loaded yet)."""
    hint = "restart Clash Verge Rev (or open the global script in its GUI and save it) to apply it"
    if not interactive:
        notify(f"The nju-connect script for Clash Verge Rev changed; {hint}")
        return
    if not clash.verge_processes():
        print("Clash Verge Rev will load the script the next time it starts")
        return
    if pending:
        print("Clash Verge Rev has not loaded this script yet (its rules page still shows the old rules).")
    if not sys.stdin.isatty():
        print(f"To load the new script, {hint}")
    elif ask_yes("Clash Verge Rev loads the script only when it rebuilds its configuration. "
                 "Restart Clash Verge Rev now?", default=True):
        clash.restart_verge()
        print("Restarted Clash Verge Rev; the NJU rules are active once it has started")
    else:
        print(f"Not restarted; {hint}")


# ------------------------------------------------------------------ --install

SNIPPETS = paths.STATE_DIR / "install-snippets.json"   # what to merge, as last shown, per install


def _shown_snippets():
    try:
        return json.loads(SNIPPETS.read_text())
    except (OSError, ValueError):
        return {}


def _track_snippet(key, snippet, interactive, notice):
    """Remember the merge snippet of an install. Returns (shown before, same as then); when it
    changed in the background, notify (only the user can merge it)."""
    shown = _shown_snippets()
    last = shown.get(key)
    if not interactive and last not in (None, snippet):
        notify(notice)
        return True, False
    if last != snippet:
        shown[key] = snippet
        write_atomic(SNIPPETS, json.dumps(shown, ensure_ascii=False, indent=2) + "\n", 0o644)
    return last is not None, last == snippet


def merge_steps(title, written, config_file, sections, notes, same=False):
    """Printable steps for what to merge into a config by hand, once.

    written: [(path, what)] of the files the service keeps up to date; sections: [(key, note,
    lines, top)], top=True for "at the top of key". The lines to copy are never colored.
    """
    paint = styler()
    rule = "─" * 64
    out = [paint(title, "1;34"), rule]
    if written:
        width = max(len(str(path)) for path, _ in written) + 2
        out.append(paint("✓", "1;32") + " Written, kept up to date by the service (the client rereads them by itself):")
        out += [f"    {paint(str(path).ljust(width), '32')}{paint(what, '2')}" for path, what in written]
        out.append("")
    out.append(paint(f"── Merge into {config_file} once ", "1;34"))
    if same:
        out.append(paint("   The same as last time: skip it if you have merged it already.", "2"))
    for number, (key, note, lines, top) in zip("①②③④⑤⑥", sections):
        if top:
            head = paint(f"At the TOP of {key}:", "1;33") + (f"  {paint(note, '1;33')}" if note else "")
        else:
            head = f"Under {paint(key, '32')}:" + (f"  {paint(note, '2')}" if note else "")
        out.append(f"{number} {head}")
        out += [f"    {line}" for line in lines]
        out.append("")
    return "\n".join(out + [rule] + notes)


class Installer:
    """Puts the NJU rules into one client and keeps them there (export FORMAT --install)."""

    def target(self, output):
        """The file or folder to install into (output: the user's -o)."""
        raise NotImplementedError

    def apply(self, target, entries, skipped, interactive):
        """Install or refresh; True if a file changed. RuntimeError with the reason on failure."""
        raise NotImplementedError

    def files(self, target):
        """[(path, what)] of the files this install keeps up to date."""
        return [(Path(target), "")]

    def describe(self, target):
        return f"merged into {target}"

    def keeps(self, target):
        """Whether the service keeps this install up to date (so it is remembered)."""
        return True


def _reload(core, target, interactive):
    """Make a core load its changed config, asking first when run from a terminal."""
    confirm = (lambda prompt: ask_yes(prompt, default=True)) if interactive and sys.stdin.isatty() else None
    done, message = clients.reload(core, target, confirm)
    if interactive:
        print(message if done else message[0].upper() + message[1:])
    elif not done:
        notify(f"The NJU rules in {target} changed; {message}")


PROVIDER_ROLES = {"nju-vpn": "NJU resources", "nju-direct": "VPN server and nodes (must stay direct)",
                  clash.PROXY_PROVIDER: "NJUConnect and its direct fallback"}


def _provider_files(home, entries, skipped):
    """Write the mihomo provider files under home; True if any changed."""
    config, settings = load_config(), load_settings()
    files = clash.provider_files(entries, config, settings, _header(entries, skipped, config))
    changed = False
    for relative, content in files.items():
        changed = write_file(Path(home) / relative, content) or changed
    return changed


def _provider_list(home):
    return [(Path(home) / clash.PROVIDER_FILES[name], role) for name, role in PROVIDER_ROLES.items()]


class VergeInstaller(Installer):
    def target(self, output):
        target = output or clash.verge_script_path()
        if not target:
            raise RuntimeError("Clash Verge Rev not found; use -o PATH to write the script somewhere else")
        clients.require_writable(paths.VERGE_DIR)
        return Path(target).expanduser()

    def apply(self, target, entries, skipped, interactive):
        files = _provider_files(paths.VERGE_DIR, entries, skipped)
        parts = clash.installed_parts(load_settings())
        changed = write_file(target, clash.clash_verge_script(parts))
        if interactive:
            print(f"{'Wrote' if changed else 'Unchanged:'} {target}, and its rule and proxy files in "
                  f"{paths.VERGE_DIR}; they will be kept up to date")
        if changed:
            apply_verge_script(interactive)
        elif interactive and clash.verge_loaded(parts["rules"]) is False:
            # e.g. the service rewrote the script, and Clash Verge has not rebuilt since
            apply_verge_script(interactive, pending=True)
        return files or changed

    def files(self, target):
        return [(Path(target), "the global script")] + _provider_list(paths.VERGE_DIR)

    def describe(self, target):
        return "installed into Clash Verge Rev"


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
        _, same = _track_snippet(
            f"clash-config:{target}", snippet, interactive,
            f"The nju-connect part of {target}/config.yaml changed; run "
            "`nju-connect export clash-config --install` and merge it again")
        if interactive:
            print(mihomo_steps(target, snippet, same))
        return changed

    def files(self, target):
        return _provider_list(target)

    def describe(self, target):
        return f"installed for mihomo in {target} (config.yaml merged by hand once)"


def mihomo_steps(home, snippet, same=False):
    """What to merge into mihomo's config.yaml, once, as numbered steps."""
    sections, current = {}, None
    for line in snippet.splitlines()[1:]:
        if not line.startswith(" "):
            current = line.split(":")[0]
            sections[current] = []
        else:
            sections[current].append(line.strip().split("  #")[0])
    return merge_steps(
        f"nju-connect: mihomo set up in {home}",
        [(path.relative_to(home), what) for path, what in _provider_list(home)], Path(home) / "config.yaml",
        [("proxy-providers", "(create the key if it is missing)", sections["proxy-providers"], False),
         ("proxy-groups", "", sections["proxy-groups"], False),
         ("rule-providers", "", sections["rule-providers"], False),
         ("rules", "← before all your other rules", sections["rules"], True)],
        ["Then reload mihomo once (restart it, or PUT /configs through its API).",
         "· Policy changes and proxy.socks_port: applied automatically, nothing to redo.",
         "· export.proxy_name / group_name / group_type / health_*: run this command again",
         "  and update ② and ④ (the service notifies you when that is needed).",
         f"· Details: {DOCS_URL}#原生-mihomo--自己维护的-configyaml"],
        same)


class SingBoxInstaller(Installer):
    def target(self, output):
        return clients.choose_config("sing-box", output)

    def apply(self, target, entries, skipped, interactive):
        if not clients.writable(target):
            return self.print_steps(target, entries, interactive)
        changed, warnings = singbox.merge_file(target, entries, load_config(), load_settings())
        for warning in warnings:
            if interactive:
                print(f"Warning: {warning}")
            else:
                log(f"warning: {warning}")
        if interactive:
            print(f"{'Merged the NJU outbound and rules into' if changed else 'Unchanged:'} {target}; "
                  f"its rule sets in {singbox.rule_sets_folder(target)} will be kept up to date")
        if changed:
            _reload("sing-box", target, interactive)
        return changed

    def print_steps(self, target, entries, interactive):
        """A config nju-connect cannot write: keep the rule sets in our folder, print the rest."""
        config, settings = load_config(), load_settings()
        files, changed = singbox.write_rule_sets(paths.SING_BOX_RULE_SETS, entries, config, settings)
        try:
            direct = singbox.direct_tag(read_json_config(target, "sing-box")[1])
        except RuntimeError:
            direct = None   # not readable either: assume the usual tag
        outbound, rule_sets, rules = singbox.additions(files, config, settings, direct or "direct")
        outbounds = [outbound] + ([] if direct else [{"type": "direct", "tag": "direct"}])

        def lines(items):
            return [json.dumps(item, ensure_ascii=False) + "," for item in items]
        snippet = json.dumps([outbounds, rule_sets, rules], ensure_ascii=False)
        _, same = _track_snippet(
            f"sing-box-config:{target}", snippet, interactive,
            f"The nju-connect part of {target} changed; run `nju-connect export sing-box-config --install` "
            "and merge it again")
        if interactive:
            print(merge_steps(
                f"nju-connect: {target} is not writable by you, so merge this part once by hand",
                [(path, f"rule set {name}") for name, path in files.items()], target,
                [("outbounds", "(at the end: the first outbound stays your default)", lines(outbounds), False),
                 ("route.rule_set", "(create the key if it is missing)", lines(rule_sets), False),
                 ("route.rules", "← before all your other rules", lines(rules), True)],
                ["Then check it (`sing-box check -c CONFIG`) and reload sing-box once.",
                 "· Policy changes: the rule sets above are kept up to date and sing-box rereads them.",
                 "· proxy.socks_port, export.proxy_name: run this command again and update ① and ③.",
                 f"· Details: {DOCS_URL}#三sing-box-内核的客户端sing-box-111"],
                same))
        return changed

    def files(self, target):
        if not clients.writable(target):
            return [(paths.SING_BOX_RULE_SETS / f"{name}.json", f"rule set {name}") for name in singbox.RULE_SETS]
        folder = singbox.rule_sets_folder(target)
        return [(Path(target), "the config")] + [(folder / f"{name}.json", f"rule set {name}")
                                                 for name in singbox.RULE_SETS]

    def describe(self, target):
        if not clients.writable(target):
            return f"rule sets for {target} (merged by hand once)"
        return f"merged into {target}"


class XrayInstaller(Installer):
    def target(self, output):
        return clients.choose_config("xray", output)

    def apply(self, target, entries, skipped, interactive):
        if not clients.writable(target):
            if not interactive:
                raise RuntimeError(f"{target} is no longer writable by you")
            print(self.steps(target, entries))
            return False
        changed = xray.merge_file(target, entries, load_config(), load_settings())
        if interactive:
            print(f"{'Merged the NJU outbound and rules into' if changed else 'Unchanged:'} {target}; "
                  "they will be kept up to date")
        if changed:
            _reload("xray", target, interactive)
        return changed

    def steps(self, target, entries):
        """Xray has no rule files to point at: print everything, to merge again after policy changes."""
        config, settings = load_config(), load_settings()
        tag = settings["export"]["proxy_name"]
        rules = [dict(rule, ruleTag=xray.TAG) for rule in xray.routing_rules(entries, config, settings, tag)]
        sections = [("outbounds", "(at the end: the first outbound stays your default)",
                     [json.dumps(xray.outbound(config, tag), ensure_ascii=False) + ","], False),
                    ("routing.rules", "← before all your other rules",
                     [json.dumps(rule, ensure_ascii=False) + "," for rule in rules], True)]
        if xray.domain_strategy(settings):
            sections.append(("routing", "", [f'"domainStrategy": "{xray.domain_strategy(settings)}",'], False))
        return merge_steps(
            f"nju-connect: {target} is not writable by you, so merge this part by hand", [], target, sections,
            ["Then restart Xray. A config outbound tagged direct (freedom) must exist.",
             "· Not kept up to date: Xray has no rule files to point at, so merge the rules again after",
             "  the policy changes (`nju-connect export xray --install` prints them), or run Xray as your",
             "  user with a config you can write, and nju-connect keeps it up to date.",
             f"· Details: {DOCS_URL}#xray原生内核"])

    def keeps(self, target):
        return clients.writable(target)


INSTALLERS = {"clash-verge": VergeInstaller(), "clash-config": MihomoInstaller(),
              "sing-box-config": SingBoxInstaller(), "xray": XrayInstaller()}


# ------------------------------------------------- keeping remembered exports up to date

def refresh_exports(entries=None, skipped=None, quiet=False):
    """Regenerate every remembered export; returns the number of exports that changed."""
    exports = remembered()
    if not exports:
        return 0
    if entries is None:
        entries, skipped = load_policy()
    changed = 0
    for e in exports:
        if e.format not in FORMATS:
            continue
        if e.mode == INSTALL:
            try:
                if e.format not in INSTALLERS or not INSTALLERS[e.format].apply(Path(e.path), entries, skipped,
                                                                                interactive=False):
                    continue
            except RuntimeError as err:
                notify(f"Could not update the NJU rules for {e.label} in {e.path}: {err}")
                continue
        elif FORMATS[e.format].render is None or \
                not write_file(e.path, render(e.format, entries, skipped), ours=True):
            continue
        changed += 1
        if not quiet:
            again = "; import it again in your client" if e.format in MANUAL_IMPORT else ""
            print(f"Updated {e.path} ({e.label}){again}")
    return changed


def _age(path):
    path = Path(path)
    if not path.exists():
        return "missing"
    return f"updated {(datetime.now().timestamp() - path.stat().st_mtime) / 3600:.1f}h ago"


def list_exports():
    exports = remembered()
    if not exports:
        print("No remembered exports; create one with `nju-connect export FORMAT --install` "
              "or `nju-connect export FORMAT -o PATH`")
    for e in exports:
        if e.mode == INSTALL and e.format in INSTALLERS:
            installer = INSTALLERS[e.format]
            print(f"{e.label:<16} {installer.describe(Path(e.path))}")
            files = installer.files(Path(e.path))
            width = max(len(str(path)) for path, _ in files) + 2
            for path, what in files:
                print(f"    {str(path).ljust(width)}{_age(path):<20}{what}".rstrip())
        else:
            again = "; import it again after it changes" if e.format in MANUAL_IMPORT else ""
            print(f"{e.label:<16} {e.path}  ({_age(e.path)}{again})")
