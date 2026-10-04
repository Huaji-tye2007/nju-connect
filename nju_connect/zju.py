"""Running zju-connect for one-shot tasks: feature check, resource download, login methods."""

import json
import subprocess
import tempfile
from pathlib import Path

from . import paths
from .util import write_atomic

_fetch_resource_checked = False


def require_fetch_resource():
    """Make sure zju-connect supports --fetch-resource (added in the NJU fork)."""
    global _fetch_resource_checked
    if _fetch_resource_checked:
        return
    zju = paths.zju_connect_binary()
    try:
        help_text = subprocess.run([zju, "-h"], capture_output=True, text=True, timeout=10)
        supported = "fetch-resource" in help_text.stdout + help_text.stderr
    except (OSError, subprocess.TimeoutExpired):
        supported = False
    if not supported:
        raise RuntimeError(f"{zju} is too old: it has no --fetch-resource option. "
                           "Run `nju-connect upgrade` to install a zju-connect build that has it.")
    _fetch_resource_checked = True


def fetch_resource():
    """Download the resource with the saved session; returns the raw JSON."""
    require_fetch_resource()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "resource.json"
        proc = subprocess.run([paths.zju_connect_binary(), "-config", str(paths.CONFIG_TOML),
                               "-fetch-resource", str(out)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("zju-connect --fetch-resource failed:\n" + proc.stdout + proc.stderr
                               + "\nIf the session expired, log in again with `nju-connect connect`.")
        data = out.read_bytes()
    json.loads(data)  # must be valid JSON before it replaces the old copy
    write_atomic(paths.RESOURCE, data, 0o600)
    return data


def auth_domains(server, port):
    """Password login methods offered by the server, as returned by -auth-info."""
    try:
        proc = subprocess.run([paths.zju_connect_binary(), "-protocol", "atrust", "-server", server,
                               "-port", str(port), "-auth-info"],
                              capture_output=True, text=True, timeout=30)
        infos = json.loads(proc.stdout)
    except Exception:
        return []
    return [i for i in infos if i.get("authType") == "auth/psw"]
