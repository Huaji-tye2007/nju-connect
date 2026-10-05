import http.server
import socket
import socketserver
import struct
import threading
import unittest
from unittest import mock

from nju_connect import config, daemon, direct, paths


def serve(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(4096)
            if not data:
                return
            self.request.sendall(data)


class Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"path={self.path} host={self.headers['Host']} proxy={self.headers.get('Proxy-Connection')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class DirectProxyTest(unittest.TestCase):
    def setUp(self):
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.echo = serve(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo))
        self.echo.daemon_threads = True
        self.addCleanup(self.close_server, self.echo)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.addCleanup(self.udp.close)
        threading.Thread(target=self.udp_echo, daemon=True).start()

    @staticmethod
    def close_server(server):
        server.shutdown()
        server.server_close()

    def udp_echo(self):
        try:
            while True:
                data, source = self.udp.recvfrom(4096)
                self.udp.sendto(b"echo:" + data, source)
        except OSError:
            pass

    def proxy(self, **extra):
        proxy = direct.DirectProxy(dict({"socks_bind": "127.0.0.1:0", "http_bind": "127.0.0.1:0"}, **extra))
        proxy.start()
        self.addCleanup(proxy.stop)
        return proxy.addresses

    def socks(self, address, auth=None):
        sock = socket.create_connection(address, timeout=5)
        self.addCleanup(sock.close)
        if auth:
            sock.sendall(b"\x05\x01\x02")
            self.assertEqual(sock.recv(2), b"\x05\x02")
            user, password = (x.encode() for x in auth)
            sock.sendall(bytes([1, len(user)]) + user + bytes([len(password)]) + password)
            return sock, sock.recv(2) == b"\x01\x00"
        sock.sendall(b"\x05\x01\x00")
        self.assertEqual(sock.recv(2), b"\x05\x00")
        return sock, True

    def connect(self, sock, host, port):
        if host == "localhost":
            sock.sendall(b"\x05\x01\x00\x03" + bytes([9]) + b"localhost" + struct.pack(">H", port))
        else:
            sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(host) + struct.pack(">H", port))
        return direct.recv_exact(sock, 10)

    def test_socks_connect_by_ip_and_name(self):
        socks_address, _ = self.proxy()
        port = self.echo.server_address[1]
        for host in ("127.0.0.1", "localhost"):
            sock, _ = self.socks(socks_address)
            self.assertEqual(self.connect(sock, host, port)[1], 0)
            sock.sendall(b"hello")
            self.assertEqual(sock.recv(5), b"hello")

    def test_socks_refused_port(self):
        socks_address, _ = self.proxy()
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        sock, _ = self.socks(socks_address)
        self.assertEqual(self.connect(sock, "127.0.0.1", port)[1], 5)

    def test_socks_password(self):
        socks_address, _ = self.proxy(socks_user="u", socks_passwd="p")
        sock, ok = self.socks(socks_address, ("u", "wrong"))
        self.assertFalse(ok)
        sock, ok = self.socks(socks_address, ("u", "p"))
        self.assertTrue(ok)
        self.assertEqual(self.connect(sock, "127.0.0.1", self.echo.server_address[1])[1], 0)
        # without credentials the proxy offers no method
        sock = socket.create_connection(socks_address, timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(b"\x05\x01\x00")
        self.assertEqual(sock.recv(2), b"\x05\xff")

    def test_udp_associate(self):
        socks_address, _ = self.proxy()
        sock, _ = self.socks(socks_address)
        sock.sendall(b"\x05\x03\x00\x01" + bytes(6))
        reply = direct.recv_exact(sock, 10)
        self.assertEqual(reply[1], 0)
        relay = (socket.inet_ntoa(reply[4:8]), struct.unpack(">H", reply[8:10])[0])
        target = self.udp.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(5)
            client.sendto(b"\0\0\0\x01" + socket.inet_aton(target[0]) + struct.pack(">H", target[1]) + b"ping",
                          relay)
            data = client.recv(4096)
        self.assertEqual(data[:10], b"\0\0\0\x01" + socket.inet_aton(target[0]) + struct.pack(">H", target[1]))
        self.assertEqual(data[10:], b"echo:ping")

    def test_http_connect_and_get(self):
        _, http_address = self.proxy()
        web = serve(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hello))
        self.addCleanup(self.close_server, web)
        port = web.server_address[1]
        with socket.create_connection(http_address, timeout=5) as sock:
            sock.sendall(f"GET http://127.0.0.1:{port}/a?b=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                         "Proxy-Connection: keep-alive\r\n\r\n".encode())
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        self.assertIn(b" 200 ", response.split(b"\r\n")[0])
        self.assertTrue(response.endswith(f"path=/a?b=1 host=127.0.0.1:{port} proxy=None".encode()))
        with socket.create_connection(http_address, timeout=5) as sock:
            sock.sendall(f"CONNECT 127.0.0.1:{self.echo.server_address[1]} HTTP/1.1\r\n\r\n".encode())
            self.assertEqual(sock.recv(4096), b"HTTP/1.1 200 Connection established\r\n\r\n")
            sock.sendall(b"tunnel")
            self.assertEqual(sock.recv(6), b"tunnel")

    def test_stop_frees_the_ports_and_connections(self):
        proxy = direct.DirectProxy({"socks_bind": "127.0.0.1:0", "http_bind": ""})
        proxy.start()
        self.assertEqual(len(proxy.addresses), 1)   # no HTTP proxy when http_bind is empty
        self.assertEqual(direct.active_pid(), __import__("os").getpid())
        address = proxy.addresses[0]
        sock, _ = self.socks(address)
        self.assertEqual(self.connect(sock, "127.0.0.1", self.echo.server_address[1])[1], 0)
        proxy.stop()
        self.assertIsNone(direct.active_pid())
        self.assertEqual(sock.recv(10), b"")   # open connections are closed too
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(address)                    # what zju-connect does next

    def test_port_taken(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            proxy = direct.DirectProxy({"socks_bind": "127.0.0.1:0",
                                        "http_bind": f"127.0.0.1:{taken.getsockname()[1]}"})
            with self.assertRaises(OSError):
                proxy.start()
            self.assertFalse(proxy.running)

    def test_listen_address(self):
        self.assertEqual(direct.listen_address(":1080", 1), ("", 1080))
        self.assertEqual(direct.listen_address("127.0.0.1:1081", 1), ("127.0.0.1", 1081))
        self.assertEqual(direct.listen_address("[::1]:1080", 1), ("::1", 1080))


class SupervisorTest(unittest.TestCase):
    def supervisor(self, campus_proxy="direct"):
        config.write_config({"server_address": "vpn.nju.edu.cn", "username": "u", "socks_bind": "127.0.0.1:1"})
        config.set_options({"daemon.campus_proxy": campus_proxy})
        self.addCleanup(config.set_options, {"daemon.campus_proxy": "direct"})
        for name, value in (("port_in_use", False), ("vpn_healthy", False), ("refresh_exports", 0)):
            patcher = mock.patch.object(daemon, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        s = daemon.Supervisor(config.load_settings(), config.load_config())
        if s.direct:
            s.direct = mock.Mock(running=False)
            s.direct.start.side_effect = lambda: setattr(s.direct, "running", True)
            s.direct.stop.side_effect = lambda: setattr(s.direct, "running", False)
        return s

    def test_on_campus_the_ports_connect_directly(self):
        s = self.supervisor()
        s.start = mock.Mock()
        with mock.patch.object(daemon, "on_campus", return_value=True):
            s.tick()
            s.tick()
        s.direct.start.assert_called_once_with()
        s.start.assert_not_called()
        paths.CLIENT_DATA.parent.mkdir(parents=True, exist_ok=True)
        paths.CLIENT_DATA.write_text("{}")
        self.addCleanup(paths.CLIENT_DATA.unlink)
        order = mock.Mock()
        s.direct.stop.side_effect = lambda: (order.stop_direct(), setattr(s.direct, "running", False))
        s.start.side_effect = lambda: order.start_zju()
        with mock.patch.object(daemon, "on_campus", return_value=False):
            s.tick()
        # the ports are freed before zju-connect starts
        self.assertEqual([c[0] for c in order.mock_calls], ["stop_direct", "start_zju"])

    def test_campus_proxy_off(self):
        s = self.supervisor("off")
        self.assertIsNone(s.direct)
        with mock.patch.object(daemon, "on_campus", return_value=True):
            s.tick()   # nothing to start

    def test_port_taken_is_logged_once(self):
        s = self.supervisor()
        s.direct.start.side_effect = OSError("in use")
        with mock.patch.object(daemon, "on_campus", return_value=True), \
                mock.patch.object(daemon, "log") as log:
            s.tick()
            s.tick()
        self.assertEqual(sum("direct proxy" in c.args[0] for c in log.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
