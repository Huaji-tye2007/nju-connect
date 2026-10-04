"""The systemd user service: the one place zju-connect runs (`nju-connect service run`)."""

import os
import shutil
import subprocess
import sys

from . import paths
from .config import load_config
from .network import require_no_instance
from .util import die, write_atomic
from .zju import check_session

UNIT_TEMPLATE = """[Unit]
Description=NJU Connect: zju-connect (off campus in auto mode), plus access-policy updates
After=default.target

[Service]
ExecStart={exe} service run
Restart=always
RestartSec=10
# The service loop stops zju-connect itself on SIGTERM; anything left is killed afterwards
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=default.target
"""


def systemctl(*args, check=True, quiet=False):
    if not shutil.which("systemctl"):
        die("systemctl not found; run `nju-connect service run` from your session's autostart instead")
    kwargs = {"capture_output": True, "text": True} if quiet else {}
    return subprocess.run(["systemctl", "--user", *args], check=check, **kwargs)


def uses_default_config():
    """The service reads the default config folder; a NJU_CONNECT_CONFIG_DIR override is another setup."""
    return "NJU_CONNECT_CONFIG_DIR" not in os.environ


def _require_default_config():
    if not uses_default_config():
        die("the service uses the default configuration; unset NJU_CONNECT_CONFIG_DIR to control it")


def is_active():
    if not shutil.which("systemctl"):
        return False
    return systemctl("is-active", "--quiet", paths.UNIT_NAME, check=False, quiet=True).returncode == 0


def is_enabled():
    if not shutil.which("systemctl"):
        return False
    return systemctl("is-enabled", "--quiet", paths.UNIT_NAME, check=False, quiet=True).returncode == 0


def state():
    if not shutil.which("systemctl"):
        return "unavailable (no systemd)"
    if not paths.UNIT_FILE.exists():
        return "not set up"
    active = systemctl("is-active", paths.UNIT_NAME, check=False, quiet=True).stdout.strip() or "unknown"
    return f"{active}, {'starts automatically' if is_enabled() else 'no autostart'}"


def restart_if_active():
    """Restart the service so it picks up changed settings; True if it was running."""
    if not uses_default_config() or not is_active():
        return False
    systemctl("restart", paths.UNIT_NAME)
    return True


def _write_unit():
    exe = paths.installed_path()
    write_atomic(paths.UNIT_FILE, UNIT_TEMPLATE.format(exe=exe), 0o644)
    systemctl("daemon-reload")


def _require_session():
    """Make sure the service can connect without an SMS code; log in first if needed.

    The service cannot type SMS codes, so starting it with an expired session (on a device
    that is not trusted) would only make it wait for `nju-connect login`.
    """
    state = check_session()
    if state == "valid":
        return
    if state == "unknown":
        print("Could not check the saved session (is the network up?); starting anyway")
        return
    reason = "No saved session" if state == "missing" else "The saved session has expired"
    if not sys.stdin.isatty():
        die(f"{reason}; run `nju-connect login` first (the service cannot enter SMS codes)")
    print(f"{reason}; logging in first (enter the SMS code if asked)")
    from .configure import login   # configure imports this module
    if not login():
        die("the service was not started because the login did not complete")


def start(force=False):
    _require_default_config()
    if is_active():
        print(f"{paths.UNIT_NAME} is already running")
        return
    _require_session()
    require_no_instance(load_config(), force)
    _write_unit()
    systemctl("start", paths.UNIT_NAME)
    print(f"Started {paths.UNIT_NAME} (see `nju-connect service status`)")


def stop():
    _require_default_config()
    if not is_active():
        print(f"{paths.UNIT_NAME} is not running")
        return
    systemctl("stop", paths.UNIT_NAME)
    print(f"Stopped {paths.UNIT_NAME}")


def restart():
    _require_default_config()
    _require_session()
    _write_unit()
    systemctl("restart", paths.UNIT_NAME)
    print(f"Restarted {paths.UNIT_NAME}")


def enable(force=False):
    _require_default_config()
    _require_session()
    require_no_instance(load_config(), force, ignore_service=True)
    _write_unit()
    systemctl("enable", paths.UNIT_NAME)
    systemctl("restart", paths.UNIT_NAME)
    print(f"{paths.UNIT_NAME} is running and starts automatically when you log in")


def disable():
    _require_default_config()
    systemctl("disable", "--now", paths.UNIT_NAME, check=False)
    if paths.UNIT_FILE.exists():
        paths.UNIT_FILE.unlink()
    systemctl("daemon-reload")
    print(f"Stopped {paths.UNIT_NAME} and removed it")


def logs():
    os.execvp("journalctl", ["journalctl", "--user", "-u", paths.UNIT_NAME, "-n", "50", "-f"])
