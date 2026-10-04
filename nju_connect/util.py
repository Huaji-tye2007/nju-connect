"""Small helpers shared by all modules: messages, prompts and safe file writes."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def die(msg, code=1):
    print(f"nju-connect: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg):
    print(msg, flush=True)


def write_atomic(path, data, mode):
    """Write via a temporary file + rename so readers never see a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def ask(prompt, default=""):
    suffix = f" [{default}]" if default != "" else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or str(default)


def ask_yes(prompt, default=False):
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{prompt} [{hint}] ").strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in ("y", "yes")


def notify(msg):
    """Log a message and show it as a desktop notification when possible."""
    log(msg)
    try:
        subprocess.run(["notify-send", "-a", "NJU Connect", "NJU Connect", msg],
                       timeout=5, stderr=subprocess.DEVNULL)
    except Exception:
        pass
