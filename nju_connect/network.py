"""Network probes: campus detection, VPN health and running zju-connect instances."""

import os
import socket
import struct
import sys
from pathlib import Path

from . import paths
from .config import DEFAULT_HTTP_PORT, DEFAULT_SOCKS_PORT, bind_port
from .util import die


def dns_query(name):
    header = struct.pack(">HHHHHH", os.getpid() & 0xFFFF, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    return header + qname + struct.pack(">HH", 1, 1)


def dns_answered(reply):
    return len(reply) >= 12 and bool(reply[2] & 0x80) and reply[3] & 0x0F == 0


def campus_servers(settings):
    return [s.strip() for s in settings["campus"]["dns_servers"].split(",") if s.strip()]


def on_campus(settings):
    """True on campus, False off campus, None if there is no network at all."""
    reachable = False
    for server in campus_servers(settings):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(2)
                s.sendto(dns_query(settings["campus"]["probe_name"]), (server, 53))
                reachable = True
                if dns_answered(s.recv(2048)):
                    return True
        except OSError:  # timeout or network unreachable
            continue
    return False if reachable else None


def port_in_use(port, host="127.0.0.1"):
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


def vpn_healthy(settings, socks):
    """Ask a campus DNS server through the SOCKS5 proxy (UDP ASSOCIATE)."""
    for server in campus_servers(settings):
        try:
            with socket.create_connection(socks, timeout=5) as ctrl:
                ctrl.sendall(b"\x05\x01\x00")
                if ctrl.recv(2) != b"\x05\x00":
                    return False
                ctrl.sendall(b"\x05\x03\x00\x01" + bytes(6))
                reply = ctrl.recv(10)
                if len(reply) < 10 or reply[1] != 0:
                    return False
                host = socket.inet_ntoa(reply[4:8])
                relay = (socks[0] if host == "0.0.0.0" else host, struct.unpack(">H", reply[8:10])[0])
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    udp.settimeout(5)
                    udp.sendto(b"\0\0\0\x01" + socket.inet_aton(server) + struct.pack(">H", 53)
                               + dns_query(settings["campus"]["probe_name"]), relay)
                    if dns_answered(udp.recv(2048)[10:]):
                        return True
        except OSError:
            continue
    return False


def running_instances(exclude_pid=None):
    """Running zju-connect processes as (pid, executable, owner, in_service)."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == exclude_pid:
            continue
        try:
            if (entry / "comm").read_text().strip() != "zju-connect":
                continue
            uid = entry.stat().st_uid
        except OSError:
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            exe = "?"
        try:
            in_service = paths.UNIT_NAME in (entry / "cgroup").read_text()
        except OSError:
            in_service = False
        try:
            import pwd
            owner = pwd.getpwuid(uid).pw_name
        except (ImportError, KeyError):
            owner = str(uid)
        found.append((int(entry.name), exe, owner, in_service))
    return found


def instance_problems(config, ignore_service=False):
    """Human-readable reasons why starting zju-connect now would conflict."""
    problems = []
    instances = running_instances()
    if ignore_service and any(in_service for *_, in_service in instances):
        # the service's own zju-connect holds the ports; only report the others
        return [p for p in instance_problems(config) if "nju-connect service" not in p
                and not p.startswith("port ")]
    for pid, exe, owner, in_service in instances:
        source = "the nju-connect service" if in_service else f"user {owner}"
        stop = "nju-connect service stop" if in_service else f"kill {pid}"
        problems.append(f"zju-connect is already running (PID {pid}, {exe}, started by {source}); "
                        f"stop it with `{stop}`")
    for key, default in (("socks_bind", DEFAULT_SOCKS_PORT), ("http_bind", DEFAULT_HTTP_PORT)):
        bind = config.get(key)
        if bind is None and key == "http_bind":
            bind = f":{DEFAULT_HTTP_PORT}"
        if not bind:
            continue
        port = bind_port(bind, default)
        if port_in_use(port):
            problems.append(f"port {port} ({key}) is already in use")
    return problems


def require_no_instance(config, force, ignore_service=False):
    problems = instance_problems(config, ignore_service)
    if problems and not force:
        for problem in problems:
            print(f"nju-connect: {problem}", file=sys.stderr)
        die("stop the running instance first, or pass --force")
