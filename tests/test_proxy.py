"""The browser's proxy (ombro.proxy): each name looked up once, checked, and connected to at the
address checked, so DNS rebinding can't lead a page to this machine or the local network."""

from __future__ import annotations

import socket
import threading

import pytest

from ombro.providers import NotCalled
from ombro.providers.addresses import check_url
from ombro.proxy import Proxy

PUBLIC = "93.184.215.14"
_connect = socket.socket.connect  # before conftest's no_network: only the test's own client uses it


class Rebinding:
    """A resolver that answers with a public address the first time and this machine after."""

    def __init__(self, later: str = "127.0.0.1"):
        self.calls: list[str] = []
        self.later = later

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        return [PUBLIC] if len(self.calls) == 1 else [self.later]


class Dialler:
    """Where the proxy connects: recorded, and answered by one end of a socket pair."""

    def __init__(self):
        self.dialled: list[tuple[str, int]] = []
        self.far: list[socket.socket] = []

    def __call__(self, address: str, port: int, timeout: float) -> socket.socket:
        self.dialled.append((address, port))
        near, far = socket.socketpair()
        self.far.append(far)
        return near


def client(proxy: Proxy) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    _connect(s, ("127.0.0.1", proxy.port))
    return s


def read_all(s: socket.socket) -> bytes:
    out = b""
    while True:
        try:
            chunk = s.recv(65536)
        except (socket.timeout, ConnectionResetError):
            return out
        if not chunk:
            return out
        out += chunk


@pytest.fixture
def made():
    proxies: list[Proxy] = []

    def make(resolver, **kw) -> Proxy:
        p = Proxy(resolver, **kw)
        proxies.append(p)
        return p

    yield make
    for p in proxies:
        p.close()


@pytest.mark.parametrize("later", ["127.0.0.1", "169.254.169.254", "192.168.1.1", "::ffff:10.0.0.1"])
def test_a_name_that_answers_differently_the_second_time_is_refused(made, later):
    resolve = Rebinding(later)
    check_url("http://a.rebind.test/", resolve)  # the page's own check: public the first time
    dialler = Dialler()
    p = made(resolve, dial_to=dialler)
    c = client(p)
    c.sendall(b"CONNECT a.rebind.test:443 HTTP/1.1\r\nHost: a.rebind.test:443\r\n\r\n")
    answer = read_all(c)
    assert answer.startswith(b"HTTP/1.1 403") and b"private network" in answer
    assert dialler.dialled == []  # nothing was connected to at all
    assert resolve.calls == ["a.rebind.test", "a.rebind.test"]
    assert "a.rebind.test" in p.refused[-1]


def test_the_proxy_connects_to_the_address_it_checked_never_the_name(made):
    resolve = Rebinding()
    dialler = Dialler()
    p = made(resolve, dial_to=dialler)
    c = client(p)
    c.sendall(b"CONNECT shop.test:8443 HTTP/1.1\r\nHost: shop.test:8443\r\n\r\n")
    assert c.recv(100).startswith(b"HTTP/1.1 200")
    assert dialler.dialled == [(PUBLIC, 8443)] and resolve.calls == ["shop.test"]  # looked up once
    c.sendall(b"hello through the tunnel")
    far = dialler.far[0]
    far.settimeout(5)
    assert far.recv(100) == b"hello through the tunnel"
    far.sendall(b"and back")
    assert c.recv(100) == b"and back"
    c.close()


def test_a_plain_http_request_goes_on_a_connection_of_its_own(made):
    dialler = Dialler()
    p = made(lambda host, port: [PUBLIC], dial_to=dialler)
    c = client(p)
    c.sendall(
        b"POST http://shop.test/order?x=1 HTTP/1.1\r\nHost: shop.test\r\nProxy-Connection: keep-alive\r\n"
        b"Content-Length: 5\r\n\r\nhello"
    )

    def server():
        far = None
        for _ in range(50):
            if dialler.far:
                far = dialler.far[0]
                break
            threading.Event().wait(0.05)
        far.settimeout(5)
        got = b""
        while b"hello" not in got:
            got += far.recv(65536)
        server.got = got
        far.sendall(b"HTTP/1.1 200 OK\r\nConnection: keep-alive\r\nContent-Length: 2\r\n\r\nok")
        far.close()

    t = threading.Thread(target=server)
    t.start()
    answer = read_all(c)
    t.join(5)
    head = server.got.split(b"\r\n\r\n")[0]
    assert head.startswith(b"POST /order?x=1 HTTP/1.1")
    assert b"Connection: close" in head and b"Proxy-Connection" not in head and b"keep-alive" not in head
    assert server.got.endswith(b"hello")
    assert answer.startswith(b"HTTP/1.1 200 OK") and b"Connection: close" in answer and answer.endswith(b"ok")
    assert b"keep-alive" not in answer
    assert dialler.dialled == [(PUBLIC, 80)]


def test_this_machine_and_local_names_are_refused_without_a_lookup(made):
    looked: list[str] = []

    def resolve(host, port):
        looked.append(host)
        return [PUBLIC]

    dialler = Dialler()
    p = made(resolve, dial_to=dialler)
    for target in (b"127.0.0.1:80", b"[::1]:443", b"localhost:443", b"printer.local:80", b"169.254.169.254:80"):
        c = client(p)
        c.sendall(b"CONNECT " + target + b" HTTP/1.1\r\n\r\n")
        assert read_all(c).startswith(b"HTTP/1.1 403"), target
    assert looked == [] and dialler.dialled == []


def test_only_the_browser_may_use_the_proxy(made):
    p = made(lambda host, port: [PUBLIC], peers={"10.9.9.9"}, dial_to=Dialler())
    c = client(p)
    c.sendall(b"CONNECT shop.test:443 HTTP/1.1\r\n\r\n")
    assert read_all(c) == b""  # closed at once: this machine is not the browser's address


def test_no_address_at_all_is_refused():
    def nothing(host, port):
        raise NotCalled(f"the address {host} could not be found")

    with pytest.raises(NotCalled):
        check_url("https://nowhere.test/", nothing)


def test_proxy_ports_from_the_environment():
    from ombro.browser import proxy_ports

    assert proxy_ports({}) is None
    assert proxy_ports({"OMBRO_BROWSER_PROXY_PORTS": "9300-9302"}) == range(9300, 9303)
    assert proxy_ports({"OMBRO_BROWSER_PROXY_PORTS": "9400"}) == range(9400, 9401)
    for bad in ("80-90", "x-y", "9399-9300", "70000"):
        assert proxy_ports({"OMBRO_BROWSER_PROXY_PORTS": bad}) is None


def test_proxy_takes_the_next_free_port_in_its_range():
    import socket

    from ombro.proxy import Proxy

    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    port = held.getsockname()[1]
    try:
        p = Proxy(lambda host, port: [], ports=range(port, port + 50))
        try:
            assert port < p.port < port + 50
        finally:
            p.close()
        with pytest.raises(OSError):
            Proxy(lambda host, port: [], ports=range(port, port + 1))
    finally:
        held.close()
