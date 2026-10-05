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


def can_color():
    """Same decision argparse makes for its own colors (NO_COLOR, FORCE_COLOR, TERM, a tty)."""
    try:
        from _colorize import can_colorize   # Python 3.13+
        return can_colorize(file=sys.stdout)
    except (ImportError, TypeError):
        if "FORCE_COLOR" in os.environ:
            return True
        return (sys.stdout.isatty() and "NO_COLOR" not in os.environ
                and os.environ.get("TERM") != "dumb")


def styler():
    """paint(text, code) -> text in an ANSI style (e.g. "1;34") when stdout takes colors."""
    color = can_color()
    return lambda text, code: f"\033[{code}m{text}\033[0m" if color else text


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


def strip_json_comments(text):
    """JSON with //, # and /* */ comments (Xray and sing-box accept them) -> plain JSON."""
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


def read_json_config(path, what):
    """(text, data) of a proxy core's JSON config; RuntimeError with a readable reason."""
    import json
    try:
        text = Path(path).read_text()
    except OSError as e:
        raise RuntimeError(f"cannot read the {what} config {path}: {e.strerror}")
    try:
        data = json.loads(strip_json_comments(text))
    except ValueError as e:
        raise RuntimeError(f"{path} is not a JSON {what} config: {e}")
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} is not a {what} config")
    return text, data


def replace_config(path, content, backup):
    """Write a merged config in place (keeping its mode); back the original up first if asked."""
    import shutil
    from datetime import datetime
    path = Path(path)
    if backup:
        copy = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, copy)
        print(f"Backed up {path} -> {copy} (comments are not kept in the merged file)")
    try:
        write_atomic(path, content, path.stat().st_mode & 0o7777)
    except OSError as e:
        raise RuntimeError(f"cannot write {path}: {e.strerror}")


def check_with(binary, args, content, what):
    """Run `binary args <temp file>` on a config; RuntimeError with its last lines if it fails."""
    fd, tmp = tempfile.mkstemp(prefix="nju-connect-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        result = subprocess.run([binary] + args + [tmp], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=60)
    finally:
        os.unlink(tmp)
    if result.returncode != 0:
        tail = "\n".join(result.stdout.strip().splitlines()[-5:])
        raise RuntimeError(f"{what} rejected the merged config, left unchanged:\n{tail}")
