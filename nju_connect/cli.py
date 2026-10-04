"""Command-line interface: argument parsing and the small commands."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import INSTALL_URL, VERSION, __doc__ as PACKAGE_DOC, configure, exporters, paths, service
from .config import DEFAULT_HTTP_PORT, DEFAULT_SERVER, bind_port, load_config, load_settings, socks_address
from .daemon import run_daemon
from .network import on_campus, port_in_use, running_instances, vpn_healthy
from .util import die

SERVICE_ACTIONS = ("start", "stop", "restart", "status", "logs", "enable", "disable", "run")
SERVICE_ALIASES = {"install": "enable", "uninstall": "disable"}   # names used before v0.5


def cmd_setup(args):
    configure.setup(args.server, args.advanced)


def cmd_login(args):
    sys.exit(0 if configure.login() else 1)


def cmd_config(args):
    if args.action == "show":
        configure.show()
    elif args.action == "get":
        configure.get(args.key)
    else:
        configure.set_value(args.key, args.value)


def cmd_trust(args):
    load_config()
    zju = paths.zju_connect_binary()
    flag = "-untrust-device" if args.command == "untrust" else "-trust-device"
    os.execv(zju, [zju, "-config", str(paths.CONFIG_TOML), flag] + (["-debug-dump"] if args.debug else []))


def cmd_export(args):
    load_config()
    if args.list:
        exporters.list_exports()
    elif args.forget:
        exporters.forget(args.forget)
        print(f"{args.forget} is no longer kept up to date (the file was left in place)")
    elif not args.format:
        die("choose a format (" + ", ".join(exporters.FORMATS) + "), or use --list / --forget")
    else:
        try:
            exporters.export(args.format, args.output, args.refresh, args.install)
        except RuntimeError as e:
            die(str(e))


def status():
    config, settings = load_config(), load_settings()
    host, socks = socks_address(config)
    http = bind_port(config.get("http_bind", ""), DEFAULT_HTTP_PORT)
    mode = settings["daemon"]["mode"]
    print("mode:         ", "always connected" if mode == "always" else "auto (connects only off campus)")
    campus = on_campus(settings)
    print("network:      ", {True: "on campus", False: "off campus", None: "offline"}[campus])
    listening = port_in_use(socks, host)
    print("VPN:          ", "connected" if listening and vpn_healthy(settings, (host, socks)) else "not connected")
    print("proxies:      ", f"SOCKS5 {host}:{socks}, HTTP {host}:{http}"
          + ("" if listening else " (not running)"))
    print("service:      ", service.state())
    for pid, exe, owner, in_service in running_instances():
        if not in_service:
            print("zju-connect:  ", f"PID {pid} ({exe}, user {owner}) started outside the service")
    print("session:      ", "saved" if paths.CLIENT_DATA.exists() else "none (run `nju-connect login`)")
    exports = exporters.remembered(settings)
    print("exports:      ", ", ".join(exports) if exports else "none (see `nju-connect export --help`)")


def cmd_service(args):
    action = SERVICE_ALIASES.get(args.action, args.action)
    if action == "run":
        run_daemon()
    elif action == "status":
        status()
    elif action == "logs":
        service.logs()
    elif action == "start":
        service.start(args.force)
    elif action == "enable":
        service.enable(args.force)
    else:
        getattr(service, action)()


def cmd_daemon(args):
    run_daemon()   # old unit files run `nju-connect daemon`


def cmd_upgrade(args):
    """Rerun the installer; it keeps the configuration and restarts the service."""
    env = dict(os.environ, NJU_CONNECT_PREFIX=str(paths.installed_path().parent),
               NJU_CONNECT_NONINTERACTIVE="1")
    if args.version:
        env["NJU_CONNECT_VERSION"] = args.version
    script = subprocess.run(["curl", "-fsSL", INSTALL_URL], capture_output=True, text=True)
    if script.returncode != 0:
        die(f"cannot download the installer from {INSTALL_URL}")
    sys.exit(subprocess.run(["bash", "-s"], input=script.stdout, text=True, env=env).returncode)


def cmd_uninstall(args):
    exe = paths.installed_path()
    if paths.UNIT_FILE.exists():
        service.disable()
    for path in {exe, Path(paths.zju_connect_binary())}:
        if path.exists():
            path.unlink()
            print(f"Removed {path}")
    if args.purge:
        for path in (paths.CONFIG_DIR, paths.STATE_DIR):
            shutil.rmtree(path, ignore_errors=True)
            print(f"Removed {path}")
    else:
        print(f"Kept {paths.CONFIG_DIR} and {paths.STATE_DIR} (use --purge to remove them)")


EXPORT_EPILOG = """formats:
""" + "\n".join(f"  {name:<13} {desc}" for name, (_, desc) in exporters.FORMATS.items()) + """

examples:
  nju-connect export clash-verge --install          Clash Verge Rev global script
  nju-connect export clash -o ~/.config/mihomo/ruleset/nju-vpn.yaml
  nju-connect export sing-box -o ~/nju-vpn.json     then compile with `sing-box rule-set compile`
  nju-connect export pac -o ~/nju.pac               then set file:///home/<you>/nju.pac as proxy URL
Files written with -o are remembered and kept up to date by the service."""


def build_parser():
    parser = argparse.ArgumentParser(prog="nju-connect", description=PACKAGE_DOC.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"nju-connect {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("setup", help="first-time setup: account, login, service")
    p.add_argument("--server", help=f"aTrust server (default {DEFAULT_SERVER})")
    p.add_argument("--advanced", action="store_true", help="also ask for the service and campus settings")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("login", help="log in in this terminal (SMS code) and save the session")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("service", help="control the background service that runs zju-connect",
                       description="The service is the only thing that runs zju-connect.")
    p.add_argument("action", choices=SERVICE_ACTIONS + tuple(SERVICE_ALIASES),
                   metavar="{" + ",".join(SERVICE_ACTIONS) + "}",
                   help="start/stop/restart it, show status, follow logs, enable/disable autostart "
                        "at login, or run it in this terminal (systems without systemd)")
    p.add_argument("--force", action="store_true", help="start even if another zju-connect is running")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("config", help="show or change individual settings")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("show", help="list every setting and where it is stored")
    q = actions.add_parser("get", help="print one setting")
    q.add_argument("key", help="e.g. daemon.mode")
    q = actions.add_parser("set", help="change one setting (prompts when VALUE is omitted)")
    q.add_argument("key", help="e.g. daemon.check_interval")
    q.add_argument("value", nargs="?", help="new value; omit to be prompted (passwords are hidden)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("export", help="rules for Clash, sing-box, Xray or a PAC file",
                       epilog=EXPORT_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("format", nargs="?", choices=tuple(exporters.FORMATS), metavar="FORMAT")
    p.add_argument("-o", "--output", help="write to this file and keep it up to date")
    p.add_argument("--install", action="store_true",
                   help="clash-verge only: write Clash Verge Rev's global script")
    p.add_argument("--refresh", action="store_true", help="download the access policy again first")
    p.add_argument("--list", action="store_true", help="show the remembered exports")
    p.add_argument("--forget", metavar="FORMAT", help="stop keeping an export up to date")
    p.set_defaults(func=cmd_export)

    for name, text in (("trust", "trust this device (later logins skip SMS)"),
                       ("untrust", "remove this device from the trusted list")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--debug", action="store_true", help="print the server responses")
        p.set_defaults(func=cmd_trust)

    p = sub.add_parser("upgrade", help="install the latest nju-connect and zju-connect")
    p.add_argument("--version", help="nju-connect release tag to install instead of the latest")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("uninstall", help="remove the service and binaries")
    p.add_argument("--purge", action="store_true", help="also delete config and saved session")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("daemon")   # hidden alias of `service run` for old unit files
    p.set_defaults(func=cmd_daemon)
    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)
