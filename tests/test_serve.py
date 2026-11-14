"""`ombro serve`, stage 1: the handshake, the token and origin checks, the hello event."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import connect

from ombro import __version__
from ombro.serve.app import build_app, hello

# The network guard in conftest refuses every connection; these tests talk to
# a serve they started themselves, so they may reach 127.0.0.1 and nothing else.
_connect = socket.socket.connect
_create_connection = socket.create_connection
_getaddrinfo = socket.getaddrinfo

TOKEN = "t0ken-for-tests"
APP = "tauri://localhost"


def client(dev: bool = False) -> TestClient:
    return TestClient(build_app(TOKEN, dev), base_url="http://127.0.0.1")


def test_hello_is_a_real_ui_event():
    msg = hello()
    assert msg["type"] == "event" and msg["seq"] == 1 and msg["session"] is None
    assert msg["event"] == {"type": "note", "text": f"Ombro {__version__} is running.", "items": [],
                            "spaced": False, "level": "done"}


def test_http_needs_the_token():
    c = client()
    assert c.get("/hello").status_code == 403
    assert c.get("/hello", headers={"Authorization": "Bearer wrong"}).status_code == 403
    r = c.get("/hello", headers={"Authorization": f"Bearer {TOKEN}", "Origin": APP})
    assert r.status_code == 200 and r.json() == {"version": __version__}
    assert r.headers["access-control-allow-origin"] == APP


def test_http_refuses_other_origins_and_hosts():
    c = client()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/hello", headers={**auth, "Origin": "https://example.com"}).status_code == 403
    assert c.get("/hello", headers={**auth, "Origin": "http://localhost:5173"}).status_code == 403
    assert client(dev=True).get("/hello", headers={**auth, "Origin": "http://localhost:5173"}).status_code == 200
    # A page that rebinds its own name to 127.0.0.1 arrives with its own Host.
    assert c.get("http://evil.example/hello", headers=auth).status_code == 400


def test_preflight_from_the_app_only():
    c = client()
    ask = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = c.options("/hello", headers={**ask, "Origin": APP})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == APP
    assert "access-control-allow-origin" not in c.options("/hello", headers={**ask, "Origin": "https://example.com"}).headers


def test_socket_sends_hello_with_the_token_as_subprotocol():
    with client().websocket_connect("ws://127.0.0.1/events", subprotocols=["ombro", TOKEN], headers={"Origin": APP}) as ws:
        assert ws.accepted_subprotocol == "ombro"
        assert ws.receive_json() == hello()


@pytest.mark.parametrize("protocols,origin", [
    (["ombro"], APP),
    (["ombro", "wrong"], APP),
    (["ombro", TOKEN], "https://example.com"),
])
def test_socket_refused(protocols, origin):
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect("ws://127.0.0.1/events", subprotocols=protocols, headers={"Origin": origin}) as ws:
            ws.receive_json()


@pytest.fixture
def loopback(monkeypatch):
    def only_loopback(real):
        def call(*args, **kwargs):
            address = kwargs.get("address") or next((a for a in args if isinstance(a, tuple)), None)
            if not address or address[0] != "127.0.0.1":
                raise AssertionError("a test tried to use the network")
            return real(*args, **kwargs)
        return call

    monkeypatch.setattr(socket.socket, "connect", only_loopback(_connect))
    monkeypatch.setattr(socket, "create_connection", only_loopback(_create_connection))

    def resolve(host, *args, **kwargs):
        if host != "127.0.0.1":
            raise AssertionError("a test tried to use the network")
        return _getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _start():
    proc = subprocess.Popen([sys.executable, "-m", "ombro.serve", "--handshake"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = proc.stdout.readline()
    return proc, json.loads(line)


def test_handshake_then_exits_when_stdin_closes(loopback):
    proc, hs = _start()
    try:
        assert set(hs) == {"port", "token"} and hs["port"] > 0 and len(hs["token"]) >= 40
        url = f"ws://127.0.0.1:{hs['port']}/events"
        with connect(url, subprotocols=["ombro", hs["token"]], origin=APP, open_timeout=5) as ws:
            assert ws.subprotocol == "ombro"
            assert json.loads(ws.recv(timeout=5)) == hello()
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_each_start_has_a_fresh_token():
    tokens = []
    for _ in range(2):
        proc, hs = _start()
        tokens.append(hs["token"])
        proc.stdin.close()
        proc.wait(timeout=10)
    assert tokens[0] != tokens[1]


def test_cli_lists_serve_without_loading_starlette():
    code = "import sys, ombro.cli; ombro.cli.build_parser(); print('starlette' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
