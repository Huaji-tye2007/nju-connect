"""Where nju-connect keeps its files, and how it finds zju-connect and itself."""

import os
import shutil
import sys
from pathlib import Path

from .util import die

HOME = Path.home()
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
CONFIG_DIR = Path(os.environ.get("NJU_CONNECT_CONFIG_DIR") or XDG_CONFIG / "nju-connect")
STATE_DIR = Path(os.environ.get("NJU_CONNECT_STATE_DIR")
                 or Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "nju-connect")
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")

CONFIG_TOML = CONFIG_DIR / "config.toml"        # read by zju-connect itself
SETTINGS_FILE = CONFIG_DIR / "nju-connect.conf"  # nju-connect's own settings
CLIENT_DATA = STATE_DIR / "client_data.json"     # saved login session
RESOURCE = STATE_DIR / "resource.json"           # last downloaded access policy

UNIT_NAME = "nju-connect.service"
UNIT_FILE = XDG_CONFIG / "systemd/user" / UNIT_NAME

VERGE_DIR = DATA_DIR / "io.github.clash-verge-rev.clash-verge-rev"
MIHOMO_DIR = XDG_CONFIG / "mihomo"
# rule sets for a sing-box config nju-connect cannot write (merged by hand once)
SING_BOX_RULE_SETS = DATA_DIR / "nju-connect/sing-box"


def self_path():
    return Path(sys.argv[0]).resolve()


def installed_path():
    """Path of the installed nju-connect executable; refuses to run from a source checkout."""
    path = self_path()
    if path.suffix == ".py":
        die("run this from the installed `nju-connect` (build it with tools/build.sh or ./install.sh)")
    return path


def zju_connect_binary():
    candidates = [os.environ.get("NJU_CONNECT_ZJU_CONNECT"),
                  str(self_path().parent / "zju-connect"),
                  shutil.which("zju-connect"),
                  str(HOME / ".local/bin/zju-connect")]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK) and not os.path.isdir(candidate):
            return candidate
    die("zju-connect not found; rerun the installer or set NJU_CONNECT_ZJU_CONNECT")

