"""Interactive setup and the `config show|get|set` commands."""

import getpass
import os
import shutil
from pathlib import Path

from . import paths, service
from .clash import install_clash_script, refresh_installed_script
from .config import (CLASH, DEFAULT_DOMAIN, DEFAULT_HTTP_PORT, DEFAULT_SERVER, DEFAULT_SOCKS_PORT,
                     OPTIONS, RESTART, bind_port, load_config, load_settings, option, read_toml,
                     save_settings, set_options, write_config)
from .network import port_in_use, require_no_instance
from .ruleset import update_ruleset
from .util import ask, ask_yes, die
from .zju import auth_domains, interactive_login


def apply_effects(effects):
    """Regenerate the Clash script and/or restart the service after a change."""
    if not effects:
        return
    config, settings = load_config(), load_settings()
    if CLASH in effects and not refresh_installed_script(config, settings):
        print("Run `nju-connect clash-script --install` (or --format yaml) to update your Clash config")
    if RESTART in effects and service.restart_if_active():
        print(f"Restarted {paths.UNIT_NAME} to apply the change")


def choose_port(name, current):
    while True:
        value = ask(f"{name} port", str(current))
        try:
            port = int(value)
        except ValueError:
            print("  please enter a number")
            continue
        if not 1 <= port <= 65535:
            print("  ports go from 1 to 65535")
            continue
        if port != current and port_in_use(port):
            print(f"  port {port} is already in use, pick another one")
            continue
        return port


def choose_login_domain(server, port):
    choices = auth_domains(server, port)
    names = [c.get("loginDomain") for c in choices if c.get("loginDomain")]
    if len(names) <= 1:
        return names[0] if names else DEFAULT_DOMAIN
    for i, c in enumerate(choices, 1):
        print(f"  {i}) {c.get('authName')} ({c.get('loginDomain')})")
    pick = ask("Login method", "1")
    return names[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(names) else names[0]


def ask_basic(existing, server_override):
    """Account, login method and proxy ports; returns the new config.toml contents."""
    server = server_override or existing.get("server_address") or DEFAULT_SERVER
    port = int(existing.get("server_port") or 443)
    username = ask("NJU username (学号)", existing.get("username", ""))
    while not username:
        username = ask("NJU username (学号)")
    known = existing.get("password", "")
    password = getpass.getpass("Password (统一身份认证密码)"
                               + (" [Enter keeps the saved one]" if known else "") + ": ") or known
    while not password:
        password = getpass.getpass("Password: ")
    domain = existing.get("login_domain") or choose_login_domain(server, port)

    socks = bind_port(existing.get("socks_bind", ""), DEFAULT_SOCKS_PORT)
    http = bind_port(existing.get("http_bind", ""), DEFAULT_HTTP_PORT)
    if ask_yes(f"Change the default proxy ports (SOCKS5 {socks}, HTTP {http})?"):
        socks = choose_port("SOCKS5", socks)
        http = choose_port("HTTP", http)

    config = {
        "protocol": "atrust",
        "server_address": server,
        "server_port": port,
        "username": username,
        "password": password,
        "auth_type": existing.get("auth_type", "auth/psw"),
        "login_domain": domain,
        "disable_zju_config": True,
        "socks_bind": f"127.0.0.1:{socks}",
        "http_bind": f"127.0.0.1:{http}",
        "client_data_file": str(paths.CLIENT_DATA),
    }
    if existing.get("totp_secret"):
        config["totp_secret"] = existing["totp_secret"]
    return config


def ask_advanced():
    """Daemon, Clash and campus settings; Enter keeps the current value."""
    print("\nAdvanced settings (press Enter to keep the current value):")
    config, settings = load_config(), load_settings()
    changes = {}
    for opt in OPTIONS:
        if not opt.advanced:
            continue
        current = opt.get(config, settings)
        prompt = opt.help + (f" ({'/'.join(opt.choices)})" if opt.choices else "")
        while True:
            raw = ask(prompt, current)
            try:
                opt.parse(raw)
            except ValueError as e:
                print(f"  {e}")
                continue
            changes[opt.key] = raw
            break
    return set_options(changes)


def login():
    """Log in interactively (SMS code if asked) and save the session.

    The service is paused meanwhile, since only one zju-connect can run.
    """
    config = load_config()
    resume = service.uses_default_config() and service.is_active()
    if resume:
        print(f"Pausing {paths.UNIT_NAME} while you log in")
        service.systemctl("stop", paths.UNIT_NAME)
    try:
        require_no_instance(config, force=False)
        print(f"Logging in to {config.get('server_address', DEFAULT_SERVER)} as {config.get('username')}; "
              "enter the SMS code when zju-connect asks for it.\n", flush=True)
        ok = interactive_login()
    finally:
        if resume:
            service.systemctl("start", paths.UNIT_NAME)
            print(f"Resumed {paths.UNIT_NAME}")
    if ok:
        print(f"\nLogged in; the session is saved in {paths.CLIENT_DATA}")
    else:
        print("\nLogin did not complete; run `nju-connect login` to try again")
    return ok


def setup(server=None, advanced=False):
    """First-run (or re-run) wizard: config, first login, ruleset, Clash script, service."""
    existing = read_toml(paths.CONFIG_TOML) if paths.CONFIG_TOML.exists() else {}
    effects = set()
    if not existing or ask_yes(f"{paths.CONFIG_TOML} exists. Reconfigure username/password/ports?"):
        write_config(ask_basic(existing, server))
        print(f"Wrote {paths.CONFIG_TOML} (mode 600)")
        if existing:
            effects |= {RESTART, CLASH}

    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.STATE_DIR, 0o700)
    settings = load_settings()
    if not settings["ruleset"]["output"]:
        output, provider_path, script = paths.detect_clash()
        settings["ruleset"]["output"] = output
        settings["ruleset"]["provider_path"] = provider_path
        settings["clash"]["script"] = script
    save_settings(settings)
    print(f"Wrote {paths.SETTINGS_FILE}")
    if advanced:
        effects |= ask_advanced()

    todo = []
    logged_in = paths.CLIENT_DATA.exists()
    if not logged_in:
        print("\nFirst login: zju-connect connects once so you can enter the SMS code; "
              "the saved session lets later logins (and the background service) skip it.")
        if ask_yes("Log in now?", default=True):
            logged_in = login()
        if not logged_in:
            todo.append("nju-connect login               # log in once (SMS code)")

    if logged_in:
        print("\nGenerating the Clash ruleset")
        try:
            update_ruleset()
        except Exception as e:
            print(f"  failed: {e}")
            todo.append("nju-connect ruleset             # generate the Clash ruleset")

    config, settings = load_config(), load_settings()
    script = settings["clash"]["script"]
    if script and not Path(settings["ruleset"]["output"]).exists():
        todo.append("nju-connect clash-script --install   # after generating the ruleset")
    elif script:
        if refresh_installed_script(config, settings):
            effects.discard(CLASH)
        elif ask_yes(f"Install the Clash Verge Rev global script ({script})?", default=True):
            install_clash_script(config, settings)
            effects.discard(CLASH)
        else:
            todo.append("nju-connect clash-script --install   # Clash Verge Rev global script")
    elif not script:
        todo.append("nju-connect clash-script --format yaml   # mihomo config snippet to merge")

    if paths.UNIT_FILE.exists():
        apply_effects(effects)
    elif logged_in and shutil.which("systemctl"):
        if ask_yes("Connect automatically whenever you are off campus (systemd user service)?",
                   default=True):
            service.install()
        else:
            todo.append("nju-connect service install     # connect automatically off campus")
    elif not logged_in:
        todo.append("nju-connect service install     # after logging in")

    print("\nSetup finished." if not todo else "\nSetup finished. Still to do:")
    for line in todo:
        print(f"  {line}")
    print("\nUseful commands: `nju-connect check`, `nju-connect config show`, `nju-connect service logs`")
    if not advanced:
        print("Daemon, Clash and campus-detection settings: `nju-connect setup --advanced`")


def display(opt, value):
    if opt.kind == "password":
        return "********" if value else "(not set)"
    return value if value != "" else "(auto)"


def show():
    config, settings = load_config(), load_settings()
    width = max(len(opt.key) for opt in OPTIONS)
    section = None
    for opt in OPTIONS:
        if opt.key.split(".")[0] != section:
            section = opt.key.split(".")[0]
            print(f"\n[{section}]  ({opt.file})")
        print(f"  {opt.key:<{width}}  {display(opt, opt.get(config, settings))}")


def get(key):
    opt = option(key)
    print(display(opt, opt.get(load_config(), load_settings())))


def set_value(key, value=None):
    opt = option(key)
    config, settings = load_config(), load_settings()
    current = opt.get(config, settings)
    if value is None:
        value = getpass.getpass(f"{opt.help}: ") if opt.kind == "password" else ask(opt.help, current)
    try:
        parsed = opt.parse(value)
    except ValueError as e:
        die(str(e))
    if opt.kind == "bind" and parsed != current and port_in_use(parsed):
        die(f"port {parsed} is already in use")
    effects = set_options({key: value})
    if not effects:
        print(f"{key} is already {display(opt, parsed)}")
        return
    print(f"Set {key} = {display(opt, parsed)} in {opt.file}")
    apply_effects(effects)
