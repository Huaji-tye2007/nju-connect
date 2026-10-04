"""The systemd user service that runs `nju-connect daemon`."""

import os
import shutil
import subprocess

from . import paths
from .config import load_config
from .network import require_no_instance
from .util import die, write_atomic

UNIT_TEMPLATE = """[Unit]
Description=NJU Connect: zju-connect while off campus, plus Clash ruleset updates
After=default.target

[Service]
ExecStart={exe} daemon
Restart=always
RestartSec=10
# The daemon stops zju-connect itself on SIGTERM; anything left is killed afterwards
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=default.target
"""


def systemctl(*args, check=True, quiet=False):
    if not shutil.which("systemctl"):
        die("systemctl not found; run `nju-connect daemon` from your session's autostart instead")
    kwargs = {"capture_output": True, "text": True} if quiet else {}
    return subprocess.run(["systemctl", "--user", *args], check=check, **kwargs)


def is_active():
    if not shutil.which("systemctl"):
        return False
    return systemctl("is-active", "--quiet", paths.UNIT_NAME, check=False, quiet=True).returncode == 0


def state():
    if not shutil.which("systemctl"):
        return "unavailable"
    return systemctl("is-active", paths.UNIT_NAME, check=False, quiet=True).stdout.strip() or "unknown"


def uses_default_config():
    """The service reads the default config folder; a NJU_CONNECT_CONFIG_DIR override is another setup."""
    return "NJU_CONNECT_CONFIG_DIR" not in os.environ


def restart_if_active():
    """Restart the service so it picks up changed settings; True if it was running."""
    if not uses_default_config() or not is_active():
        return False
    systemctl("restart", paths.UNIT_NAME)
    return True


def install(force=False):
    exe = paths.installed_path()
    require_no_instance(load_config(), force, ignore_service=True)
    write_atomic(paths.UNIT_FILE, UNIT_TEMPLATE.format(exe=exe), 0o644)
    systemctl("daemon-reload")
    systemctl("enable", "--now", paths.UNIT_NAME)
    systemctl("restart", paths.UNIT_NAME)
    print(f"Installed and started {paths.UNIT_FILE}")
    print("Follow it with: nju-connect service logs")


def uninstall():
    systemctl("disable", "--now", paths.UNIT_NAME, check=False)
    if paths.UNIT_FILE.exists():
        paths.UNIT_FILE.unlink()
    systemctl("daemon-reload")
    print(f"Removed {paths.UNIT_FILE}")


def logs():
    os.execvp("journalctl", ["journalctl", "--user", "-u", paths.UNIT_NAME, "-n", "50", "-f"])
