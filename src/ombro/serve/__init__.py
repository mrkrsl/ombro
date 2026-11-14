"""`ombro serve`: the local API the desktop app talks to.

This is stage 1 of the GUI: just enough to prove the app can start a frozen
serve, find it and hear from it. Serve listens on 127.0.0.1 only, on a port
chosen at start, with a token made fresh each time. With --handshake it prints
one line of JSON on stdout, `{"port": …, "token": …}`, for the app to read.
It exits when its stdin closes, so it never outlives the app that started it.

Every request needs the token: HTTP as `Authorization: Bearer <token>`, the
WebSocket as one of its subprotocols, since a browser can't set headers on a
socket. A request that names an origin must name the app's own
(`tauri://localhost`, `http://tauri.localhost`), or Vite's with --dev. The
host must be 127.0.0.1, which keeps out pages that rebind a name to it.

The one socket, /events, sends a hello: a real `ui.py` event, as every core
event will be sent later, `{"type": "event", "session", "seq", "event"}`.
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
import threading

HOST = "127.0.0.1"
DEV_ORIGIN = "http://localhost:5173"


def bind(port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def _exit_on_eof(server) -> None:
    """Stop the server once stdin closes: the app that started us has gone."""
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    server.should_exit = True


def run(port: int = 0, handshake: bool = False, dev: bool = False) -> int:
    import uvicorn

    from ombro.serve.app import build_app

    token = secrets.token_urlsafe(32)
    sock = bind(port)
    port = sock.getsockname()[1]
    if handshake:
        print(json.dumps({"port": port, "token": token}), flush=True)
    else:
        print(f"ombro serve on http://{HOST}:{port}", flush=True)
    if dev:
        print(f"Open {DEV_ORIGIN}/?port={port}&token={token}", flush=True)
    config = uvicorn.Config(build_app(token, dev), log_level="warning", loop="asyncio", http="h11",
                            ws="websockets-sansio", lifespan="off")
    server = uvicorn.Server(config)
    threading.Thread(target=_exit_on_eof, args=(server,), daemon=True).start()
    server.run(sockets=[sock])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro serve", description="the local API for the desktop app")
    add_arguments(p)
    return p


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=0, help="the port on 127.0.0.1 (default: a free one)")
    p.add_argument("--handshake", action="store_true", help='print {"port", "token"} as one line of JSON')
    p.add_argument("--dev", action="store_true", help="also accept Vite's dev server, and print a URL to open")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args.port, args.handshake, args.dev)
    except KeyboardInterrupt:
        return 0
