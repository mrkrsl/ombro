"""The job browser's own proxy: every connection a page makes goes through it (H2, 18 Nov review).

Chrome is told to send everything here (`proxyServer` on the job's browser context, and
`--proxy-server` for a browser Ombro starts), with loopback not bypassed. For each connection
the proxy resolves the name once, checks every address it got is public
(providers.addresses), and connects to one of exactly those. Chrome never looks a name up
itself, so a name that answers with a public address for the check and a private one a second
later (DNS rebinding) can't lead the page to this machine, the local network or a cloud
metadata service.

It speaks just enough HTTP for a browser: CONNECT for https and WebSockets (a tunnel to the
checked address), and plain http requests in absolute form, each sent on a connection of its
own and closed after its answer, so no later request rides on a connection checked for
another name.
"""

from __future__ import annotations

import select
import socket
import threading
from collections import deque
from typing import Callable
from urllib.parse import urlsplit

from ombro.providers import NotCalled
from ombro.providers.addresses import Resolver, checked_addresses

HEAD_MAX = 64 * 1024  # a request's or answer's head
IDLE = 120.0  # seconds a tunnel may sit with nothing passing before it is closed
HEAD_TIMEOUT = 30.0
CONNECT_TIMEOUT = 15.0
HOP = {b"connection", b"proxy-connection", b"keep-alive", b"proxy-authorization", b"proxy-authenticate", b"te",
       b"trailer", b"upgrade"}

Dial = Callable[[str, int, float], socket.socket]


def dial(address: str, port: int, timeout: float) -> socket.socket:
    """Connect to one checked address (never a name). Tests put their own in its place."""
    return socket.create_connection((address, port), timeout=timeout)


class Proxy:
    """A small HTTP proxy on `host` (127.0.0.1 for a browser Ombro starts; for a browser
    elsewhere, the address Ombro reaches it from), taking connections only from `peers`."""

    def __init__(self, resolver: Resolver, *, host: str = "127.0.0.1", peers: set[str] | None = None,
                 dial_to: Dial | None = None, ports: range | None = None):
        self.resolver = resolver
        self.peers = set(peers or {"127.0.0.1", "::1"})
        self._dial = dial_to
        self.refused: deque[str] = deque(maxlen=50)  # recent refusals, newest last, for the role
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._bind(host, ports)
        self.sock.listen(64)
        self.host = host
        self.port = self.sock.getsockname()[1]
        self.closed = False
        self._thread = threading.Thread(target=self._accept, name="ombro-proxy", daemon=True)
        self._thread.start()

    def _bind(self, host: str, ports: range | None) -> None:
        """Any free port, or the first free one in `ports`: a browser in Docker sits behind a guard that
        lets it reach Ombro's container on those ports only (compose.yaml, OMBRO_BROWSER_PROXY_PORTS)."""
        if not ports:
            self.sock.bind((host, 0))
            return
        for port in ports:
            try:
                self.sock.bind((host, port))
                return
            except OSError:
                continue
        self.sock.close()
        raise OSError(f"no free port for the browser's proxy in {ports.start}-{ports.stop - 1}")

    @property
    def address(self) -> str:
        """For Chrome's proxyServer: http://host:port."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    # -- connections ----------------------------------------------------------

    def _accept(self) -> None:
        while not self.closed:
            try:
                conn, peer = self.sock.accept()
            except OSError:
                return
            if _plain(peer[0]) not in {_plain(p) for p in self.peers}:
                conn.close()  # only the browser uses this proxy
                continue
            threading.Thread(target=self._serve, args=(conn,), name="ombro-proxy-conn", daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(HEAD_TIMEOUT)
            head, rest = _read_head(client)
            if head is None:
                return
            line, _, headers = head.partition(b"\r\n")
            try:
                method, target, version = line.decode("latin-1").split(" ")
            except ValueError:
                _answer(client, 400, "that request could not be read")
                return
            if method == "CONNECT":
                host, port = _host_port(target, 443)
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    _answer(client, 400, "only http requests in absolute form are proxied")
                    return
                try:
                    port = parts.port or 80
                except ValueError:
                    _answer(client, 400, "the address has an invalid port")
                    return
                host = parts.hostname
            if not host or port is None:
                _answer(client, 400, "the request names no host")
                return
            try:
                addresses = checked_addresses(host, port, self.resolver)
            except NotCalled as e:
                self.refused.append(f"{host}: {e}")
                if method == "CONNECT":
                    _answer(client, 403, str(e))
                # A plain http request gets no answer at all: a 403 would be shown as if it were the page.
                return
            upstream = self._connect(addresses, port)
            if upstream is None:
                _answer(client, 502, f"{host} could not be reached")
                return
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                _splice(client, upstream, rest)
                return
            # A plain http request: sent in origin form, on a connection of its own, closed after its answer.
            fields = _fields(headers)
            if any(k == b"transfer-encoding" for k, _ in fields):
                _answer(client, 411, "a request body of unknown length is not proxied")
                return
            length = next((int(v) for k, v in fields if k == b"content-length" and v.strip().isdigit()), 0)
            body = rest
            while len(body) < length:
                chunk = client.recv(min(1 << 16, length - len(body)))
                if not chunk:
                    return
                body += chunk
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            out = [f"{method} {path} {version}".encode("latin-1")]
            out += [raw for k, raw in _raw_fields(headers) if k not in HOP]
            out.append(b"Connection: close")
            upstream.sendall(b"\r\n".join(out) + b"\r\n\r\n" + body[:length])
            _relay_answer(upstream, client)
        except (OSError, ValueError):
            pass
        finally:
            for s in (client, upstream):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _connect(self, addresses: list[str], port: int) -> socket.socket | None:
        for a in addresses:  # exactly the addresses checked: never the name again
            try:
                return (self._dial or dial)(a, port, CONNECT_TIMEOUT)
            except OSError:
                continue
        return None


def _plain(address: str) -> str:
    """An address as compared: no zone, IPv4-mapped IPv6 as its IPv4."""
    a = address.split("%", 1)[0]
    return a[7:] if a.lower().startswith("::ffff:") and "." in a else a


def _read_head(s: socket.socket) -> tuple[bytes | None, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > HEAD_MAX:
            return None, b""
        chunk = s.recv(1 << 14)
        if not chunk:
            return None, b""
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head, rest


def _host_port(target: str, default: int) -> tuple[str, int | None]:
    host, port = target, default
    if target.startswith("["):
        end = target.find("]")
        host, after = target[1:end], target[end + 1:]
        if after.startswith(":"):
            port = int(after[1:]) if after[1:].isdigit() else None
    elif ":" in target:
        host, _, p = target.rpartition(":")
        port = int(p) if p.isdigit() else None
    if port is not None and not 0 < port < 65536:
        port = None
    return host, port


def _raw_fields(headers: bytes) -> list[tuple[bytes, bytes]]:
    out = []
    for raw in headers.split(b"\r\n"):
        if b":" in raw:
            out.append((raw.split(b":", 1)[0].strip().lower(), raw))
    return out


def _fields(headers: bytes) -> list[tuple[bytes, bytes]]:
    return [(k, raw.split(b":", 1)[1].strip()) for k, raw in _raw_fields(headers)]


def _answer(s: socket.socket, code: int, text: str) -> None:
    body = f"Ombro refused this connection: {text}\n".encode()
    reason = {400: "Bad Request", 403: "Forbidden", 411: "Length Required", 502: "Bad Gateway"}.get(code, "Error")
    try:
        s.sendall(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                  f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    except OSError:
        pass


def _relay_answer(upstream: socket.socket, client: socket.socket) -> None:
    """The server's answer to the client, its head marked Connection: close, then the rest as it comes."""
    upstream.settimeout(IDLE)
    head, rest = _read_head(upstream)
    if head is None:
        return
    line, _, headers = head.partition(b"\r\n")
    out = [line] + [raw for k, raw in _raw_fields(headers) if k not in HOP] + [b"Connection: close"]
    client.sendall(b"\r\n".join(out) + b"\r\n\r\n" + rest)
    while True:
        chunk = upstream.recv(1 << 16)
        if not chunk:
            return
        client.sendall(chunk)


def _splice(a: socket.socket, b: socket.socket, pending: bytes = b"") -> None:
    """Bytes both ways until either side closes or nothing passes for IDLE seconds."""
    if pending:
        b.sendall(pending)
    a.setblocking(True)
    b.setblocking(True)
    socks = [a, b]
    while True:
        ready, _, _ = select.select(socks, [], [], IDLE)
        if not ready:
            return
        for s in ready:
            try:
                chunk = s.recv(1 << 16)
            except OSError:
                return
            if not chunk:
                return
            (b if s is a else a).sendall(chunk)
