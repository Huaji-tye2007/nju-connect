"""Running zju-connect for one-shot tasks: feature check, policy download, login."""

import json
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import paths

# zju-connect output meaning the login is waiting for input (an SMS code)
NEEDS_INPUT = re.compile(r"Please enter|challenge: EOF|verification code", re.I)

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


def download_resource():
    """Download the access policy with the saved session; returns the raw JSON (not saved)."""
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
    json.loads(data)  # must be valid JSON before anyone uses it
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


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def session_mtime():
    return _mtime(paths.CLIENT_DATA)


def interactive_login(timeout=600):
    """Run zju-connect in this terminal until it saves a session, then stop it.

    zju-connect prompts for SMS codes itself; client_data.json is written once
    the login has fully succeeded. Returns True on success.
    """
    before = session_mtime()
    proc = subprocess.Popen([paths.zju_connect_binary(), "-config", str(paths.CONFIG_TOML)])
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            saved = session_mtime()
            if saved is not None and saved != before:
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.5)
        saved = session_mtime()
        return saved is not None and saved != before
    except KeyboardInterrupt:
        return False
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def untrust_device():
    """Remove this device from the account's trusted devices; returns (ok, message).

    Uses the saved session (no login); a device that is not trusted counts as success.
    """
    try:
        proc = subprocess.run([paths.zju_connect_binary(), "-config", str(paths.CONFIG_TOML),
                               "-untrust-device"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    output = proc.stdout + proc.stderr
    if proc.returncode == 0:
        return True, "it was not trusted" if "already untrusted" in output else "done"
    lines = [line for line in output.splitlines() if line.strip()]
    return False, lines[-1] if lines else f"zju-connect exited with code {proc.returncode}"


SESSION_INVALID = re.compile(r"not logged in|session is invalid|reauthentication required", re.I)


def check_session():
    """State of the saved login session: "valid", "expired", "missing" or "unknown".

    Asks the server with the saved cookies only (never logs in, so no SMS is sent);
    "unknown" means the server could not be reached. A valid check also refreshes
    the cached access policy.
    """
    if not paths.CLIENT_DATA.exists():
        return "missing"
    try:
        data = download_resource()
    except RuntimeError as e:
        return "expired" if SESSION_INVALID.search(str(e)) else "unknown"
    except subprocess.TimeoutExpired:
        return "unknown"
    from .util import write_atomic
    write_atomic(paths.RESOURCE, data, 0o600)
    return "valid"
