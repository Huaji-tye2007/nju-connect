"""The two configuration files and the registry of user-settable options.

config.toml is zju-connect's own configuration (account, server, proxy ports)
and may only contain keys zju-connect knows. nju-connect.conf holds the
settings of nju-connect itself (daemon, ruleset, Clash, campus detection).
The option registry presents both as one set of dotted keys.
"""

import configparser
import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from . import paths
from .util import die, write_atomic

DEFAULT_SERVER = "vpn.nju.edu.cn"
DEFAULT_DOMAIN = "openldap13924"
DEFAULT_SOCKS_PORT = 1080
DEFAULT_HTTP_PORT = 1081

SETTINGS_DEFAULTS = {
    "campus": {
        # NJU's internal DNS servers only answer from inside the campus network
        "dns_servers": "10.12.253.4, 10.28.253.4",
        "probe_name": "www.nju.edu.cn",
    },
    "daemon": {
        "check_interval": "60",
        "ruleset_interval": "1800",
    },
    "ruleset": {
        "output": "",          # filled in by setup from the detected Clash flavour
        "provider_path": "",   # path written into the Clash rule-provider
    },
    "clash": {
        "proxy_name": "NJUConnect",
        "group_name": "NJU",
        "group_type": "fallback",
        "health_url": "http://lib.nju.edu.cn/",
        "health_interval": "300",
        "script": "",          # Clash Verge Rev global script, if detected
    },
}

# zju-connect keys nju-connect no longer writes (phone is only used by SMS logins,
# which NJU does not offer; password logins look the number up from the server)
DROPPED_KEYS = {"phone"}


# ------------------------------------------------------------- config.toml

def read_toml(path):
    """Read the top-level scalar keys of zju-connect's config.toml."""
    text = Path(path).read_text()
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:
        pass
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            break
        m = re.match(r'^([A-Za-z0-9_]+)\s*=\s*(.+?)\s*(#.*)?$', line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if value.startswith('"'):
            try:
                result[key] = json.loads(value)
            except ValueError:
                continue
        elif value in ("true", "false"):
            result[key] = value == "true"
        elif re.fullmatch(r"-?\d+", value):
            result[key] = int(value)
    return result


def toml_str(value):
    return json.dumps(value, ensure_ascii=False)


def load_config():
    if not paths.CONFIG_TOML.exists():
        die(f"{paths.CONFIG_TOML} not found; run `nju-connect setup` first")
    return read_toml(paths.CONFIG_TOML)


def write_config(config):
    lines = ["# zju-connect configuration written by nju-connect.",
             "# Contains your password: keep this file private (mode 600)."]
    for key, value in config.items():
        if key in DROPPED_KEYS:
            continue
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f"{key} = {toml_str(value)}")
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.CONFIG_DIR, 0o700)
    write_atomic(paths.CONFIG_TOML, "\n".join(lines) + "\n", 0o600)


def bind_port(bind, default):
    try:
        return int(str(bind).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return default


def socks_address(config):
    bind = str(config.get("socks_bind", f":{DEFAULT_SOCKS_PORT}"))
    host = bind.rsplit(":", 1)[0].strip("[]")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, bind_port(bind, DEFAULT_SOCKS_PORT)


# -------------------------------------------------------- nju-connect.conf

def load_settings():
    settings = configparser.ConfigParser()
    settings.read_dict(SETTINGS_DEFAULTS)
    settings.read(paths.SETTINGS_FILE)
    return settings


def save_settings(settings):
    lines = ["# nju-connect settings (zju-connect itself reads config.toml)"]
    for section in settings.sections():
        lines.append(f"\n[{section}]")
        for key, value in settings.items(section):
            lines.append(f"{key} = {value}")
    write_atomic(paths.SETTINGS_FILE, "\n".join(lines) + "\n", 0o600)


# ---------------------------------------------------------- option registry

RESTART = "restart"   # the service has to be restarted to pick the change up
CLASH = "clash"       # the generated Clash script has to be regenerated


class Option:
    """One user-settable key, stored either in config.toml or nju-connect.conf."""

    def __init__(self, key, help, *, toml=None, ini=None, kind="str", minimum=None,
                 choices=None, effects=(), advanced=False):
        self.key = key
        self.help = help
        self.toml = toml            # key in config.toml
        self.ini = ini              # (section, key) in nju-connect.conf
        self.kind = kind
        self.minimum = minimum
        self.choices = choices
        self.effects = set(effects)
        self.advanced = advanced    # asked by `setup --advanced`

    @property
    def file(self):
        return paths.CONFIG_TOML if self.toml else paths.SETTINGS_FILE

    def get(self, config, settings):
        if self.ini:
            return settings[self.ini[0]][self.ini[1]]
        value = config.get(self.toml, "")
        if self.kind == "bind":
            default = DEFAULT_SOCKS_PORT if self.toml == "socks_bind" else DEFAULT_HTTP_PORT
            return bind_port(value, default)
        return value

    def parse(self, raw):
        """Validate a string from the user; returns the value to store."""
        raw = str(raw).strip()
        if self.kind in ("int", "port", "bind"):
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"{self.key} must be a whole number")
            if self.kind in ("port", "bind") and not 1 <= value <= 65535:
                raise ValueError(f"{self.key} must be between 1 and 65535")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.key} must be at least {self.minimum}")
            return value
        if not raw and self.kind != "path":
            raise ValueError(f"{self.key} cannot be empty")
        if self.kind == "name":
            if "," in raw or raw.upper() in ("DIRECT", "REJECT"):
                raise ValueError(f"{self.key} cannot contain commas or be DIRECT/REJECT")
        elif self.kind == "choice":
            if raw not in self.choices:
                raise ValueError(f"{self.key} must be one of: {', '.join(self.choices)}")
        elif self.kind == "url":
            url = urlparse(raw)
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ValueError(f"{self.key} must be an http:// or https:// URL")
        elif self.kind == "host":
            if not re.fullmatch(r"[A-Za-z0-9.-]+", raw):
                raise ValueError(f"{self.key} must be a host name")
        elif self.kind == "ipv4list":
            servers = [s.strip() for s in raw.split(",") if s.strip()]
            try:
                if not servers or any(ipaddress.ip_address(s).version != 4 for s in servers):
                    raise ValueError
            except ValueError:
                raise ValueError(f"{self.key} must be a comma-separated list of IPv4 addresses")
            return ", ".join(servers)
        return raw

    def store(self, config, settings, value):
        if self.ini:
            settings[self.ini[0]][self.ini[1]] = str(value)
        elif self.kind == "bind":
            # local proxies listen on localhost only
            config[self.toml] = f"127.0.0.1:{value}"
        else:
            config[self.toml] = value


OPTIONS = [
    Option("account.username", "NJU username (学号)", toml="username", effects=[RESTART]),
    Option("account.password", "Password (统一身份认证密码)", toml="password", kind="password",
           effects=[RESTART]),
    Option("account.login_domain", "Login domain", toml="login_domain", effects=[RESTART]),
    Option("server.address", "aTrust server", toml="server_address", kind="host",
           effects=[RESTART, CLASH]),
    Option("server.port", "aTrust server port", toml="server_port", kind="port", effects=[RESTART]),
    Option("proxy.socks_port", "SOCKS5 proxy port", toml="socks_bind", kind="bind",
           effects=[RESTART, CLASH]),
    Option("proxy.http_port", "HTTP proxy port", toml="http_bind", kind="bind", effects=[RESTART]),
    Option("daemon.check_interval", "Seconds between network checks", ini=("daemon", "check_interval"),
           kind="int", minimum=10, effects=[RESTART], advanced=True),
    Option("daemon.ruleset_interval", "Seconds between ruleset updates",
           ini=("daemon", "ruleset_interval"), kind="int", minimum=300, effects=[RESTART], advanced=True),
    Option("clash.proxy_name", "Clash proxy name", ini=("clash", "proxy_name"), kind="name",
           effects=[CLASH], advanced=True),
    Option("clash.group_name", "Clash proxy group name", ini=("clash", "group_name"), kind="name",
           effects=[CLASH], advanced=True),
    Option("clash.group_type", "Clash proxy group type", ini=("clash", "group_type"), kind="choice",
           choices=("fallback", "url-test", "select"), effects=[CLASH], advanced=True),
    Option("clash.health_url", "Clash health check URL (reachable through the VPN)",
           ini=("clash", "health_url"), kind="url", effects=[CLASH], advanced=True),
    Option("clash.health_interval", "Seconds between Clash health checks",
           ini=("clash", "health_interval"), kind="int", minimum=30, effects=[CLASH], advanced=True),
    Option("clash.script", "Clash Verge Rev global script to manage", ini=("clash", "script"),
           kind="path", effects=[CLASH]),
    Option("ruleset.output", "Ruleset file to write", ini=("ruleset", "output"), kind="path",
           effects=[CLASH]),
    Option("ruleset.provider_path", "Ruleset path as seen by Clash", ini=("ruleset", "provider_path"),
           kind="path", effects=[CLASH]),
    Option("campus.dns_servers", "Campus DNS servers used to detect the campus network",
           ini=("campus", "dns_servers"), kind="ipv4list", effects=[RESTART], advanced=True),
    Option("campus.probe_name", "Host name asked for when probing the campus DNS",
           ini=("campus", "probe_name"), kind="host", effects=[RESTART], advanced=True),
]
OPTIONS_BY_KEY = {option.key: option for option in OPTIONS}


def option(key):
    try:
        return OPTIONS_BY_KEY[key]
    except KeyError:
        die(f"unknown key {key!r}; see `nju-connect config show` for the list")


def set_options(changes):
    """Validate and store {key: raw value}; returns the effects to apply.

    Raises ValueError (nothing is written) if any value is invalid.
    """
    config, settings = load_config(), load_settings()
    parsed = {key: option(key).parse(raw) for key, raw in changes.items()}
    effects = set()
    touched = set()
    for key, value in parsed.items():
        opt = option(key)
        if str(opt.get(config, settings)) == str(value):
            continue
        opt.store(config, settings, value)
        effects |= opt.effects
        touched.add(opt.file)
    if paths.CONFIG_TOML in touched:
        write_config(config)
    if paths.SETTINGS_FILE in touched:
        save_settings(settings)
    return effects
