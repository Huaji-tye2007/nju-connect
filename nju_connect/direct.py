"""A direct SOCKS5/HTTP proxy on zju-connect's ports while the service stops it on campus.

Proxy clients send NJU traffic to zju-connect's ports (the Xray, v2rayN and sing-box rules
have no fallback), so on campus, where the service stops zju-connect, those requests would
fail. This proxy connects them directly instead, resolving names with the system's DNS,
which on campus is the campus DNS.
"""

import os
import selectors
import socket
import socketserver
import struct
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import paths
from .config import DEFAULT_HTTP_PORT, DEFAULT_SOCKS_PORT, bind_port
from .util import write_atomic

STATE = paths.STATE_DIR / "direct-proxy"   # PID of the service while it serves the proxy
TIMEOUT = 30                               # for handshakes and connecting
BUFFER = 65536
HEAD_LIMIT = 65536                         # longest HTTP request head accepted


def active_pid():
    """PID of the service while it serves the direct proxy, else None."""
    try:
        pid = int(STATE.read_text())
    except (OSError, ValueError):
        return None
    return pid if Path(f"/proc/{pid}").exists() else None


def listen_address(bind, default_port):
    """zju-connect's semantics: "host:port", and ":port" listens on every interface."""
    bind = str(bind)
    host = bind.rsplit(":", 1)[0].strip("[]") if ":" in bind else ""
    return host, bind_port(bind, default_port)


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ValueError("connection closed")
        data += chunk
    return data


def pack_address(address):
    """(host, port) -> SOCKS5 ATYP + address + port."""
    host, port = address[0], address[1]
    try:
        packed = b"\x01" + socket.inet_pton(socket.AF_INET, host)
    except OSError:
        packed = b"\x04" + socket.inet_pton(socket.AF_INET6, host)
    return packed + struct.pack(">H", port)


def relay(a, b):
    """Copy both ways until both sides have closed (or one fails)."""
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ, b)
    sel.register(b, selectors.EVENT_READ, a)
    try:
        while sel.get_map():
            for key, _ in sel.select():
                src, dst = key.fileobj, key.data
                try:
                    data = src.recv(BUFFER)
                except OSError:
                    return
                if not data:
                    sel.unregister(src)
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, proxy):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.proxy = proxy
        super().__init__(address, handler)


class _Handler(socketserver.BaseRequestHandler):
    def setup(self):
        self.request.settimeout(TIMEOUT)
        self.sockets = [self.request]
        self.server.proxy.track(self.request)

    def connect(self, host, port):
        remote = socket.create_connection((host, port), timeout=TIMEOUT)
        self.sockets.append(remote)
        self.server.proxy.track(remote)
        return remote

    def relay(self, remote):
        self.request.settimeout(None)
        remote.settimeout(None)
        relay(self.request, remote)

    def finish(self):
        for sock in self.sockets[1:]:   # socketserver closes the request itself
            self.server.proxy.untrack(sock)
            sock.close()
        self.server.proxy.untrack(self.request)


class SocksHandler(_Handler):
    """SOCKS5 (RFC 1928): CONNECT and UDP ASSOCIATE, optional username/password (RFC 1929)."""

    def handle(self):
        try:
            self.serve()
        except (OSError, ValueError):
            pass

    def reply(self, code, address=("0.0.0.0", 0)):
        self.request.sendall(bytes([5, code, 0]) + pack_address(address))

    def serve(self):
        sock, auth = self.request, self.server.proxy.auth
        version, count = recv_exact(sock, 2)
        if version != 5:
            return
        method = 2 if auth else 0
        if method not in recv_exact(sock, count):
            sock.sendall(b"\x05\xff")
            return
        sock.sendall(bytes([5, method]))
        if auth:
            _, length = recv_exact(sock, 2)
            user = recv_exact(sock, length).decode(errors="replace")
            password = recv_exact(sock, recv_exact(sock, 1)[0]).decode(errors="replace")
            ok = (user, password) == auth
            sock.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
            if not ok:
                return
        _, command, _, kind = recv_exact(sock, 4)
        if kind == 1:
            host = socket.inet_ntop(socket.AF_INET, recv_exact(sock, 4))
        elif kind == 3:
            host = recv_exact(sock, recv_exact(sock, 1)[0]).decode(errors="replace")
        elif kind == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(sock, 16))
        else:
            self.reply(8)
            return
        port = struct.unpack(">H", recv_exact(sock, 2))[0]
        if command == 1:
            self.connect_command(host, port)
        elif command == 3:
            self.udp_associate()
        else:
            self.reply(7)

    def connect_command(self, host, port):
        try:
            remote = self.connect(host, port)
        except socket.gaierror:
            self.reply(4)
            return
        except ConnectionRefusedError:
            self.reply(5)
            return
        except OSError:
            self.reply(1)
            return
        self.reply(0, remote.getsockname())
        self.relay(remote)

    def udp_associate(self):
        """Relay datagrams between the client (on the address it reached us) and the
        destinations, through unbound sockets that can reach any network."""
        local = self.request.getsockname()[0]
        family = socket.AF_INET6 if ":" in local else socket.AF_INET
        relay_sock = self.udp_socket(family)
        relay_sock.bind((local, 0))
        self.reply(0, relay_sock.getsockname())
        self.request.settimeout(None)
        client_ip, client = self.client_address[0], None
        outgoing = {}   # address family -> socket towards the destinations
        sel = selectors.DefaultSelector()
        sel.register(self.request, selectors.EVENT_READ)
        sel.register(relay_sock, selectors.EVENT_READ)
        try:
            while True:
                for key, _ in sel.select():
                    sock = key.fileobj
                    if sock is self.request:
                        if not self.request.recv(BUFFER):   # the association ends with its TCP connection
                            return
                        continue
                    data, source = sock.recvfrom(BUFFER)
                    if sock is relay_sock:
                        if source[0] != client_ip or client not in (None, source):
                            continue
                        client = source
                        target = self.parse(data)
                        if not target:
                            continue
                        host, port, payload = target
                        try:
                            info = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)[0]
                            out = outgoing.get(info[0])
                            if out is None:
                                out = outgoing[info[0]] = self.udp_socket(info[0])
                                sel.register(out, selectors.EVENT_READ)
                            out.sendto(payload, info[4])
                        except OSError:
                            continue
                    elif client is not None:
                        relay_sock.sendto(b"\0\0\0" + pack_address(source) + data, client)
        finally:
            sel.close()

    def udp_socket(self, family):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        self.sockets.append(sock)
        self.server.proxy.track(sock)
        return sock

    @staticmethod
    def parse(data):
        """A client datagram: RSV RSV FRAG ATYP DST.ADDR DST.PORT DATA -> (host, port, data)."""
        if len(data) < 10 or data[2] != 0:   # fragments are not supported
            return None
        kind, rest = data[3], data[4:]
        try:
            if kind == 1:
                host, rest = socket.inet_ntop(socket.AF_INET, rest[:4]), rest[4:]
            elif kind == 3:
                host, rest = rest[1:1 + rest[0]].decode(), rest[1 + rest[0]:]
            elif kind == 4:
                host, rest = socket.inet_ntop(socket.AF_INET6, rest[:16]), rest[16:]
            else:
                return None
            return host, struct.unpack(">H", rest[:2])[0], rest[2:]
        except (OSError, ValueError, struct.error):
            return None


class HttpHandler(_Handler):
    """HTTP proxy: CONNECT tunnels and plain http:// requests (one per connection)."""

    def handle(self):
        try:
            self.serve()
        except (OSError, ValueError):
            pass

    def error(self, status):
        self.request.sendall(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())

    def serve(self):
        data = b""
        while b"\r\n\r\n" not in data:
            if len(data) > HEAD_LIMIT:
                self.error("431 Request Header Fields Too Large")
                return
            chunk = self.request.recv(BUFFER)
            if not chunk:
                return
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            self.error("400 Bad Request")
            return
        if method.upper() == "CONNECT":
            url = urlsplit("//" + target)
            host, port = url.hostname, url.port or 443
            first = rest
        else:
            url = urlsplit(target)
            if url.scheme.lower() != "http" or not url.hostname:
                self.error("400 Bad Request")
                return
            host, port = url.hostname, url.port or 80
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
            headers = [line for line in lines[1:] if line and not line.lower().startswith(
                (b"proxy-", b"connection:", b"keep-alive:"))]
            first = b"\r\n".join([f"{method} {path} {version}".encode("latin-1")] + headers
                                 + [b"Connection: close"]) + b"\r\n\r\n" + rest
        try:
            remote = self.connect(host, port)
        except OSError:
            self.error("502 Bad Gateway")
            return
        if method.upper() == "CONNECT":
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if first:
            remote.sendall(first)
        self.relay(remote)


class DirectProxy:
    """SOCKS5 and HTTP proxies on zju-connect's ports that connect directly."""

    def __init__(self, config):
        self.socks = listen_address(config.get("socks_bind", f":{DEFAULT_SOCKS_PORT}"), DEFAULT_SOCKS_PORT)
        http = config.get("http_bind", f":{DEFAULT_HTTP_PORT}")   # zju-connect's default
        self.http = listen_address(http, DEFAULT_HTTP_PORT) if http else None
        user = config.get("socks_user")
        self.auth = (str(user), str(config.get("socks_passwd", ""))) if user else None
        self.servers = []
        self.active = set()
        self.lock = threading.Lock()

    @property
    def running(self):
        return bool(self.servers)

    @property
    def addresses(self):
        return [server.server_address[:2] for server in self.servers]

    def track(self, sock):
        with self.lock:
            self.active.add(sock)

    def untrack(self, sock):
        with self.lock:
            self.active.discard(sock)

    def start(self):
        """Listen on the proxy ports; raises OSError (and listens on none) if one is taken."""
        servers = []
        try:
            servers.append(_Server(self.socks, SocksHandler, self))
            if self.http:
                servers.append(_Server(self.http, HttpHandler, self))
        except OSError:
            for server in servers:
                server.server_close()
            raise
        for server in servers:
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        self.servers = servers
        try:
            write_atomic(STATE, str(os.getpid()), 0o644)
        except OSError:
            pass

    def stop(self):
        """Close the ports and every connection, so zju-connect can take the ports over."""
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.servers = []
        with self.lock:
            sockets, self.active = list(self.active), set()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        try:
            STATE.unlink()
        except OSError:
            pass
