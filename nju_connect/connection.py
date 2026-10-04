"""`connect` / `disconnect`: zju-connect in the background without the service."""

import os
import signal
import subprocess
import time
from pathlib import Path

from . import paths, service
from .config import load_config, socks_address
from .network import port_in_use, require_no_instance, running_instances
from .util import die
from .zju import NEEDS_INPUT

PID_FILE = paths.STATE_DIR / "zju-connect.pid"
LOG_FILE = paths.STATE_DIR / "zju-connect.log"


def _alive(pid):
    """True if PID is a live zju-connect started with our config (guards against PID reuse)."""
    proc = Path("/proc") / str(pid)
    try:
        if (proc / "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
        cmdline = (proc / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return str(paths.CONFIG_TOML).encode() in cmdline


def background_pid():
    """PID of the zju-connect started by `nju-connect connect`, if it still runs."""
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def service_managed():
    return paths.UNIT_FILE.exists() and service.uses_default_config()


def connect(foreground=False, force=False, extra=()):
    config = load_config()
    if not paths.CLIENT_DATA.exists():
        die("log in once first with `nju-connect login`")
    zju = paths.zju_connect_binary()
    if foreground:
        require_no_instance(config, force)
        os.execv(zju, [zju, "-config", str(paths.CONFIG_TOML), *extra])

    if service_managed() and not force:
        if service.is_active():
            print(f"{paths.UNIT_NAME} is running and connects whenever you are off campus "
                  "(see `nju-connect check`)")
        else:
            service.systemctl("start", paths.UNIT_NAME)
            print(f"Started {paths.UNIT_NAME}; it connects whenever you are off campus")
        return

    require_no_instance(config, force)
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "wb") as log_file:
        proc = subprocess.Popen([zju, "-config", str(paths.CONFIG_TOML), *extra],
                                stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                                start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    host, port = socks_address(config)
    print("Connecting", end="", flush=True)
    deadline = time.monotonic() + 60
    while proc.poll() is None and time.monotonic() < deadline:
        if port_in_use(port, host):
            print(f"\nConnected in the background (PID {proc.pid}); SOCKS5 proxy on {host}:{port}")
            print(f"Log: {LOG_FILE}\nDisconnect with `nju-connect disconnect`")
            return
        print(".", end="", flush=True)
        time.sleep(1)
    print(flush=True)
    if proc.poll() is None:
        proc.terminate()
    output = LOG_FILE.read_text(errors="replace")
    if NEEDS_INPUT.search(output):
        die("the saved session has expired and the login needs an SMS code; run `nju-connect login`")
    tail = "\n".join(output.splitlines()[-15:])
    die(f"zju-connect did not connect; last lines of {LOG_FILE}:\n{tail}")


def _wait_gone(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not _alive(pid)


def disconnect():
    pid = background_pid()
    if pid:
        os.killpg(pid, signal.SIGTERM)
        if not _wait_gone(pid):
            os.killpg(pid, signal.SIGKILL)
        PID_FILE.unlink(missing_ok=True)
        print(f"Disconnected (stopped zju-connect PID {pid})")
        return
    if service_managed() and service.is_active():
        service.systemctl("stop", paths.UNIT_NAME)
        print(f"Stopped {paths.UNIT_NAME}; it stays off until `nju-connect connect`, "
              "`nju-connect service start` or your next login")
        return
    others = running_instances()
    if others:
        for other_pid, exe, owner, _ in others:
            print(f"zju-connect PID {other_pid} ({exe}, user {owner}) was not started by nju-connect; "
                  f"stop it with `kill {other_pid}`")
    else:
        print("Not connected")
