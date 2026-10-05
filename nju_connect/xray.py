"""Xray and v2rayN: routing rules, v2rayN's rule import, and merging into an Xray config."""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import VERSION, paths
from .clash import resolve_domains
from .config import DEFAULT_SERVER, MARKER, socks_address
from .policy import all_ports, nodes, routed
from .util import write_atomic

TAG = "nju-connect"              # ruleTag of the rules merged into an Xray config
REMARK = TAG + ":"               # prefix of the remarks of the rules imported into v2rayN

# where Xray's install script and distribution packages put the config
XRAY_CONFIGS = [paths.XDG_CONFIG / "xray/config.json", Path("/usr/local/etc/xray/config.json"),
                Path("/etc/xray/config.json")]


def subdomain_regex(domain):
    """aTrust's `*.x`: subdomains of x, not x itself."""
    return "^.+\\." + domain.replace(".", "\\.") + "$"


def _ports(entry):
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in entry.ports)


def outbound(config, tag):
    host, port = socks_address(config)
    return {"tag": tag, "protocol": "socks", "settings": {"servers": [{"address": host, "port": port}]}}


def routing_rules(entries, config, tag):
    """Xray routing rules: the VPN server and nodes direct, then the policy to `tag`.

    One rule per (ports, network) and per domain/IP kind, because Xray ANDs the conditions
    inside a rule.
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
    return rules + list(groups.values())


def domain_strategy(settings):
    # resolve a domain as soon as an IP rule is checked, so the IP ranges apply to NJU hosts
    # too; IPIfNonMatch would not help once a later domain rule (e.g. geosite:cn) matches.
    # Xray cannot limit the resolving to some domains.
    return "IPOnDemand" if resolve_domains(settings) else None


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
    for rule in routing_rules(entries, config, tag):
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
             f"(add it from {share_link(config, settings)}).",
             f"// Set the routing's domain strategy to {domain_strategy(settings) or 'AsIs'}."]
    if routing:
        lines.append(f"// Includes the {len(current)} rule(s) of your active routing \"{name}\": import it there "
                     "and answer No (replace all).")
    else:
        lines.append("// v2rayN's routing was not found: import with Yes (append), then move these rules to the top.")
    return "\n".join(lines) + "\n" + json.dumps(v2rayn_rules(entries, config, settings, current),
                                                ensure_ascii=False, indent=2) + "\n"


# ------------------------------------------------------- merging into Xray

def strip_comments(text):
    """JSON with // and /* */ comments (Xray accepts them) -> plain JSON."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i) or text.startswith("#", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def default_config():
    return next((p for p in XRAY_CONFIGS if p.is_file()), None)


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
    ours = [dict(rule, ruleTag=TAG) for rule in routing_rules(entries, config, tag)]
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


def check_config(content):
    """Run `xray run -test` on the merged config when xray is installed; raises on failure."""
    binary = xray_binary()
    if not binary:
        return False
    fd, tmp = tempfile.mkstemp(prefix="nju-connect-xray-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        result = subprocess.run([binary, "run", "-test", "-c", tmp], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
    finally:
        os.unlink(tmp)
    if result.returncode != 0:
        tail = "\n".join(result.stdout.strip().splitlines()[-5:])
        raise RuntimeError(f"xray rejected the merged config, left unchanged:\n{tail}")
    return True


def merge_file(path, entries, config, settings):
    """Merge the NJU outbound and rules into the Xray config at path; True if it changed."""
    path = Path(path).expanduser()
    if path.parent.name == "binConfigs" and (path.parent.parent / "guiConfigs").is_dir():
        raise RuntimeError(f"{path} is rewritten by v2rayN; use `nju-connect export v2rayn` for v2rayN")
    try:
        text = path.read_text()
    except OSError as e:
        raise RuntimeError(f"cannot read the Xray config {path}: {e.strerror}")
    try:
        data = json.loads(strip_comments(text))
    except ValueError as e:
        raise RuntimeError(f"{path} is not a JSON Xray config: {e}")
    if not isinstance(data, dict) or not data.get("outbounds"):
        raise RuntimeError(f"{path} has no outbounds; point -o at the Xray config you run")
    first = not any(isinstance(r, dict) and r.get("ruleTag") == TAG
                    for r in (data.get("routing") or {}).get("rules") or [])
    content = json.dumps(merge_config(data, entries, config, settings), ensure_ascii=False, indent=2) + "\n"
    if content == text:
        return False
    check_config(content)
    if first:
        backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        print(f"Backed up {path} -> {backup} (comments are not kept in the merged file)")
    try:
        write_atomic(path, content, path.stat().st_mode & 0o7777)
    except OSError as e:
        raise RuntimeError(f"cannot write {path}: {e.strerror}")
    return True


# ---------------------------------------------------------- restarting Xray

def xray_units():
    """Active systemd services whose name starts with xray, as (scope, unit)."""
    if not shutil.which("systemctl"):
        return []
    found = []
    for scope in ("user", "system"):
        args = ["systemctl"] + (["--user"] if scope == "user" else []) + [
            "list-units", "--type=service", "--state=active", "--plain", "--no-legend", "xray*.service"]
        try:
            out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found += [(scope, line.split()[0]) for line in out.splitlines() if line.strip()]
    return found


def restart_xray(confirm=None):
    """Restart the Xray user services so they load the merged config.

    confirm(prompt) may decline. Returns (restarted, message); system services need root,
    so for those (and a manually started xray) the message says what to run.
    """
    if "NJU_CONNECT_CONFIG_DIR" in os.environ:
        # a sandbox or test configuration: never touch the user's real Xray
        return False, "restart Xray to load the new rules"
    units = xray_units()
    user = [unit for scope, unit in units if scope == "user"]
    system = [unit for scope, unit in units if scope == "system"]
    if user and (confirm is None or confirm(f"Restart {', '.join(user)} to load the new rules?")):
        for unit in user:
            subprocess.run(["systemctl", "--user", "restart", unit], timeout=60)
        return True, f"Restarted {', '.join(user)}"
    if system:
        return False, f"run `sudo systemctl restart {' '.join(system)}` to load the new rules"
    return False, "restart Xray to load the new rules"
