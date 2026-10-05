"""Proxy cores on this machine: their processes, the config files they use, and reloading them.

Configs are found from the running processes' command lines (so whatever path, distribution
package or unit started the core), then from the usual locations. Cores managed by a GUI
(Clash Verge Rev, FlClash, Clash Party, v2rayN) are left out: their own exports handle them.
"""

import os
import shutil
import signal
import subprocess
from collections import namedtuple
from pathlib import Path

from . import paths

# unit: (scope, name), scope "user" or "system", or None when no systemd service runs it
Process = namedtuple("Process", "pid exe argv cwd uid unit")


def unit_of(cgroup):
    """The systemd service in /proc/PID/cgroup text, as (scope, name), or None."""
    for line in cgroup.splitlines():
        parts = line.split(":", 2)[-1].split("/")
        services = [p for p in parts if p.endswith(".service")]
        if not services or services[-1].startswith("user@"):   # the user manager, not a unit in it
            continue
        return ("user" if any(p.startswith("user@") for p in parts) else "system"), services[-1]
    return None


def processes(match):
    """Running processes whose executable name satisfies match(name)."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            try:
                exe = os.readlink(entry / "exe").replace(" (deleted)", "")
            except OSError:
                exe = ""
            if not (match(comm) or (exe and match(Path(exe).name))):
                continue
            # field 3 of /proc/PID/stat is the state; Z = exited, waiting to be reaped
            if (entry / "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                continue
            argv = [a.decode(errors="replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
            uid = entry.stat().st_uid
        except (OSError, IndexError):
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = ""
        try:
            unit = unit_of((entry / "cgroup").read_text())
        except OSError:
            unit = None
        found.append(Process(int(entry.name), exe, argv, cwd, uid, unit))
    return found


def flag_values(argv, *names):
    """Values of Go-style flags: -c x, --c x, -c=x, --c=x (every occurrence)."""
    values = []
    for i, arg in enumerate(argv):
        flag, eq, value = arg.lstrip("-").partition("=")
        if not arg.startswith("-") or flag not in names:
            continue
        if eq:
            values.append(value)
        elif i + 1 < len(argv):
            values.append(argv[i + 1])
    return values


def _resolve(proc, path):
    path = Path(path).expanduser()
    return path if path.is_absolute() or not proc.cwd else Path(proc.cwd) / path


def _gui_managed(path):
    """Configs a GUI rewrites itself (v2rayN, Clash Party, Clash Verge Rev)."""
    text = str(path)
    return ("/binConfigs/" in text or "/mihomo-party/" in text or "clash-verge" in text
            or str(paths.VERGE_DIR) in text)


# ------------------------------------------------------------------ per core

CORES = {
    "xray": {
        "name": "Xray",
        "match": lambda n: n == "xray" or n.startswith("xray-"),
        "fallback": [paths.XDG_CONFIG / "xray/config.json", Path("/usr/local/etc/xray/config.json"),
                     Path("/etc/xray/config.json")],
    },
    "sing-box": {
        "name": "sing-box",
        "match": lambda n: n == "sing-box" or n.startswith("sing-box-"),
        "fallback": [paths.XDG_CONFIG / "sing-box/config.json", Path("/etc/sing-box/config.json")],
    },
    "mihomo": {
        "name": "mihomo",
        # verge-mihomo (Clash Verge Rev) and FlClashCore have their own exports
        "match": lambda n: n == "clash" or n.startswith(("mihomo", "clash-meta", "clash.meta")),
        "fallback": [paths.MIHOMO_DIR],
    },
}


def configs_of(core, proc):
    """The config files (mihomo: the home directory) a running core uses."""
    argv = proc.argv
    if core == "xray":
        found = [_resolve(proc, v) for v in flag_values(argv, "c", "config")]
        for directory in flag_values(argv, "confdir"):
            found += sorted(_resolve(proc, directory).glob("*.json"))
        if not found and proc.cwd:
            found = [Path(proc.cwd) / "config.json"]
    elif core == "sing-box":
        found = [_resolve(proc, v) for v in flag_values(argv, "c", "config")]
        for directory in flag_values(argv, "C", "config-directory"):
            found += sorted(_resolve(proc, directory).glob("*.json"))
        if not found and proc.cwd:
            found = [Path(proc.cwd) / "config.json"]   # sing-box's default, in its working directory
    else:
        homes = flag_values(argv, "d")
        found = [_resolve(proc, homes[-1])] if homes else [_default_mihomo_home(proc.uid)]
    return [p for p in found if not _gui_managed(p)]


def _default_mihomo_home(uid):
    """mihomo without -d uses ~/.config/mihomo of the user running it."""
    if uid == os.getuid():
        return paths.MIHOMO_DIR
    try:
        import pwd
        return Path(pwd.getpwuid(uid).pw_dir) / ".config/mihomo"
    except (ImportError, KeyError):
        return paths.MIHOMO_DIR


def running(core):
    """Running processes of a core that nju-connect may configure (not GUI-managed ones)."""
    return [p for p in processes(CORES[core]["match"]) if configs_of(core, p)]


def find_config(core):
    """Candidate configs for a core: those of running processes, else the usual paths that exist."""
    found = []
    for proc in running(core):
        for path in configs_of(core, proc):
            if path not in found and path.exists():
                found.append(path)
    if not found:
        found = [p for p in CORES[core]["fallback"] if p.exists()]
    return found


def choose_config(core, output=None):
    """The config (mihomo: home directory) to install into; raises RuntimeError if unclear."""
    if output:
        return Path(output).expanduser()
    found = find_config(core)
    name = CORES[core]["name"]
    if len(found) == 1:
        return found[0]
    if found:
        raise RuntimeError(f"several {name} configs found ({', '.join(map(str, found))}); choose one with -o PATH")
    usual = ", ".join(map(str, CORES[core]["fallback"]))
    raise RuntimeError(f"no running {name} and no config in {usual}; use -o PATH")


def require_writable(path):
    """RuntimeError unless path (or, if it does not exist yet, the folder it would go in) is writable."""
    path = Path(path).expanduser()
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    if not os.access(target, os.W_OK):
        raise RuntimeError(f"{path} is not writable by you (nju-connect does not use sudo); "
                           "run the core as your user with a config in your home directory, "
                           "or merge the printed snippet by hand")


# ---------------------------------------------------------------- reloading

def reload(core, target, confirm=None):
    """Make the cores using target load it again. Returns (done, message).

    User services are restarted (sing-box: reloaded), a sing-box started by hand gets SIGHUP;
    system services need root, so the message says what to run. confirm(prompt) may decline.
    """
    name = CORES[core]["name"]
    if "NJU_CONNECT_CONFIG_DIR" in os.environ:
        # a sandbox or test configuration: never touch the user's real proxy core
        return False, f"restart {name} to load the new rules"
    target = Path(target).expanduser().resolve()
    procs = [p for p in running(core) if target in [c.resolve() for c in configs_of(core, p)]]
    if not procs:
        return False, f"restart {name} to load the new rules"
    user = sorted({p.unit[1] for p in procs if p.unit and p.unit[0] == "user"})
    system = sorted({p.unit[1] for p in procs if p.unit and p.unit[0] == "system"})
    manual = [p for p in procs if not p.unit and p.uid == os.getuid()]
    if confirm is not None and (user or manual) and not confirm(f"Reload {name} to load the new rules?"):
        return False, f"not reloaded; restart {name} to load the new rules"
    done = []
    if user and shutil.which("systemctl"):
        action = "reload-or-restart" if core == "sing-box" else "restart"
        for unit in user:
            subprocess.run(["systemctl", "--user", action, unit], timeout=60)
        done += user
    if core == "sing-box":
        for proc in manual:
            os.kill(proc.pid, signal.SIGHUP)   # sing-box reloads its config on SIGHUP
            done.append(f"sing-box (PID {proc.pid})")
    if system:
        verb = "reload-or-restart" if core == "sing-box" else "restart"
        hint = f"run `sudo systemctl {verb} {' '.join(system)}` to load the new rules"
        return bool(done), (f"Reloaded {', '.join(done)}; " if done else "") + hint
    if done:
        return True, f"Reloaded {', '.join(done)}"
    return False, f"restart {name} to load the new rules"
