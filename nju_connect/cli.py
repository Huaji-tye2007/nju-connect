"""Command-line interface: argument parsing and the small commands."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import INSTALL_URL, VERSION, __doc__ as PACKAGE_DOC, configure, connection, paths, service
from .clash import clash_js, clash_yaml, install_clash_script
from .config import DEFAULT_SERVER, load_config, load_settings, socks_address
from .daemon import run_daemon
from .network import on_campus, port_in_use, running_instances, vpn_healthy
from .ruleset import count_rules, ruleset_path, update_ruleset
from .util import die, write_atomic


def cmd_setup(args):
    configure.setup(args.server, args.advanced)


def cmd_config(args):
    if args.action == "show":
        configure.show()
    elif args.action == "get":
        configure.get(args.key)
    else:
        configure.set_value(args.key, args.value)


def cmd_login(args):
    sys.exit(0 if configure.login() else 1)


def cmd_connect(args):
    connection.connect(args.foreground, args.force, args.extra)


def cmd_disconnect(args):
    connection.disconnect()


def cmd_trust(args):
    load_config()
    zju = paths.zju_connect_binary()
    flag = "-untrust-device" if args.command == "untrust" else "-trust-device"
    os.execv(zju, [zju, "-config", str(paths.CONFIG_TOML), flag] + (["-debug-dump"] if args.debug else []))


def cmd_ruleset(args):
    try:
        update_ruleset(args.from_file, args.output, args.force)
    except Exception as e:
        die(str(e))


def cmd_clash_script(args):
    config, settings = load_config(), load_settings()
    if args.install:
        install_clash_script(config, settings, args.output, args.inline)
        return
    text = (clash_yaml if args.format == "yaml" else clash_js)(config, settings, args.inline)
    if args.output:
        write_atomic(args.output, text, 0o644)
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)


def cmd_service(args):
    if args.action == "install":
        service.install(args.force)
    elif args.action == "uninstall":
        service.uninstall()
    elif args.action == "logs":
        service.logs()
    else:
        service.systemctl(args.action, paths.UNIT_NAME, check=False)


def cmd_daemon(args):
    run_daemon()


def cmd_check(args):
    config, settings = load_config(), load_settings()
    socks = socks_address(config)
    campus = on_campus(settings)
    print("network:      ", {True: "on campus", False: "off campus", None: "offline"}[campus])
    listening = port_in_use(socks[1], socks[0])
    print("SOCKS5 proxy: ", f"{socks[0]}:{socks[1]}", "listening" if listening else "not running")
    print("VPN:          ", "working" if listening and vpn_healthy(settings, socks) else "not connected")
    for pid, exe, owner, in_service in running_instances():
        print("zju-connect:  ", f"PID {pid} ({exe}, {'service' if in_service else owner})")
    print("service:      ", service.state())
    print("session:      ", "saved" if paths.CLIENT_DATA.exists() else "none (run `nju-connect connect`)")
    output = ruleset_path(settings)
    if output.exists():
        age = (time.time() - output.stat().st_mtime) / 3600
        print("ruleset:      ", f"{count_rules(output)} rules, updated {age:.1f}h ago ({output})")
    else:
        print("ruleset:      ", f"not generated yet ({output})")


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
        service.uninstall()
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


def build_parser():
    parser = argparse.ArgumentParser(prog="nju-connect", description=PACKAGE_DOC.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"nju-connect {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create or edit the configuration")
    p.add_argument("--server", help=f"aTrust server (default {DEFAULT_SERVER})")
    p.add_argument("--advanced", action="store_true",
                   help="also set the daemon, Clash and campus-detection options")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("config", help="show or change individual settings")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("show", help="list every setting and where it is stored")
    q = actions.add_parser("get", help="print one setting")
    q.add_argument("key", help="e.g. daemon.check_interval")
    q = actions.add_parser("set", help="change one setting (prompts when VALUE is omitted)")
    q.add_argument("key", help="e.g. clash.health_interval")
    q.add_argument("value", nargs="?", help="new value; omit to be prompted (passwords are hidden)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("login", help="log in interactively (SMS code) and save the session")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("connect", help="connect in the background (starts the service if installed)")
    p.add_argument("-f", "--foreground", action="store_true",
                   help="run zju-connect in this terminal instead, showing its output")
    p.add_argument("--force", action="store_true",
                   help="skip the running-instance check and don't hand over to the service")
    p.add_argument("extra", nargs=argparse.REMAINDER, help="extra zju-connect flags")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("disconnect", help="stop the background connection (or the service)")
    p.set_defaults(func=cmd_disconnect)

    for name, text in (("trust", "trust this device (later logins skip SMS)"),
                       ("untrust", "remove this device from the trusted list")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--debug", action="store_true", help="print the server responses")
        p.set_defaults(func=cmd_trust)

    p = sub.add_parser("ruleset", help="download the access policy and write the Clash ruleset")
    p.add_argument("--from-file", action="store_true", help="reuse the last downloaded resource")
    p.add_argument("--output", help="ruleset file to write")
    p.add_argument("--force", action="store_true", help="write even if the rule count dropped a lot")
    p.set_defaults(func=cmd_ruleset)

    p = sub.add_parser("clash-script", help="print or install the Clash global script")
    p.add_argument("--install", action="store_true", help="write the Clash Verge Rev global script")
    p.add_argument("--format", choices=("js", "yaml"), default="js")
    p.add_argument("--inline", action="store_true",
                   help="embed the rules instead of referencing the ruleset file")
    p.add_argument("--output", help="write to this file instead")
    p.set_defaults(func=cmd_clash_script)

    p = sub.add_parser("service", help="manage the systemd user service")
    p.add_argument("action", choices=("install", "uninstall", "start", "stop", "restart", "status", "logs"))
    p.add_argument("--force", action="store_true", help="install even if zju-connect is running")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("daemon", help="supervise zju-connect (used by the service)")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("check", help="show network, VPN, service and ruleset state")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("upgrade", help="install the latest nju-connect and zju-connect")
    p.add_argument("--version", help="nju-connect release tag to install instead of the latest")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("uninstall", help="remove the service and binaries")
    p.add_argument("--purge", action="store_true", help="also delete config and saved session")
    p.set_defaults(func=cmd_uninstall)
    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)
