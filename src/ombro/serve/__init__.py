"""`ombro serve`: the local API the desktop app talks to.

Serve listens on 127.0.0.1 only, on a port
chosen at start, with a token made fresh each time. With --handshake it prints
one line of JSON on stdout, `{"port": …, "token": …}`, for the app to read.
It exits when its stdin closes, so it never outlives the app that started it.

Every request needs the token: HTTP as `Authorization: Bearer <token>`, the
WebSocket as one of its subprotocols, since a browser can't set headers on a
socket. A request that names an origin must name the app's own
(`tauri://localhost`, `http://tauri.localhost`), or Vite's with --dev. The
host must be 127.0.0.1, which keeps out pages that rebind a name to it.

The routes, the sessions and the protocol are in app.py and live.py; the
socket, /events, opens with a hello, a real `ui.py` event.

**As a service** (no --handshake or --dev): `ombro serve` is the server a desktop app connects
to, from this machine or another. It listens on a fixed port (8765 unless --port), on 127.0.0.1
unless --host says otherwise, and keeps running until stopped. Every request then needs a paired
device's token (pairing.py); the one exception is `POST /pair`, which swaps a pairing code for
one. While no device is paired, the first code is printed at each start. With --tick it also
starts scheduled jobs each minute, for a machine with no cron (a Docker container).
`ombro serve install` sets it up to start on its own (service.py).
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
SERVICE_PORT = 8765


def bind(port: int = 0, host: str = HOST) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
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
    """For the desktop app (--handshake) or Vite (--dev): a fresh token, a free port, 127.0.0.1."""
    import uvicorn

    from ombro.serve.app import build_app

    from ombro.home import Home, tidy_layout

    home = Home()
    if home.exists():
        for line in tidy_layout(home):
            print(line, file=sys.stderr, flush=True)
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
                            ws="websockets-sansio", lifespan="on")
    server = uvicorn.Server(config)
    if handshake:
        threading.Thread(target=_exit_on_eof, args=(server,), daemon=True).start()
    server.run(sockets=[sock])
    return 0


def is_loopback(host: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def is_private(host: str) -> bool:
    """A loopback, private or Tailscale (100.64.0.0/10) address: not reachable from the internet."""
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global  # includes Tailscale's 100.64.0.0/10, which is not global


def service_hosts(host: str, allow: list[str]) -> list[str]:
    """The Host header names a service accepts: 127.0.0.1, the address it listens on (unless that
    is every address), and any names given with --allow-host. A name not on the list is refused,
    which keeps out web pages that point a name of their own at this machine."""
    out = [HOST]
    if host not in ("0.0.0.0", "::", HOST):
        out.append(host.strip("[]"))
    for name in allow:
        name = name.strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def service_lines(host: str, port: int, code: str | None) -> list[str]:
    """What a service prints when it starts: where it listens, how to connect, the first code."""
    from ombro import __version__

    lines = [f"Ombro {__version__} is serving on {host}:{port}."]
    if not is_private(host) and host not in ("0.0.0.0", "::"):
        lines.append(f"Warning: {host} may be reachable from the internet. Ombro is meant to be reached "
                     "over SSH or Tailscale; anyone who can reach this address can try pairing codes.")
    if code:
        lines += ["", "No device is paired yet. In the Ombro app, choose to run Ombro on a server,",
                  f"connect by address, and type this pairing code: {code}",
                  "It works once and doesn't run out. Over SSH the app pairs by itself, with no code.", ""]
    return lines


def run_service(port: int = SERVICE_PORT, host: str = HOST, allow: list[str] | None = None,
                tick: bool = False) -> int:
    """The long-running server: device tokens and pairing, a fixed port, stays up until stopped."""
    import uvicorn

    from ombro.home import Home, tidy_layout
    from ombro.pairing import Devices
    from ombro.serve.app import build_app

    home = Home()
    if home.exists():
        for line in tidy_layout(home):
            print(line, file=sys.stderr, flush=True)
    devices = Devices(home)
    sock = bind(port, host)
    port = sock.getsockname()[1]
    for line in service_lines(host, port, devices.first_code()):
        print(line, flush=True)
    app = build_app(None, home=home, devices=devices, hosts=service_hosts(host, allow or []), tick=tick)
    config = uvicorn.Config(app, log_level="warning", loop="asyncio", http="h11",
                            ws="websockets-sansio", lifespan="on")
    uvicorn.Server(config).run(sockets=[sock])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro serve", description="the local API for the desktop app")
    add_arguments(p)
    return p


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", nargs="?", choices=["install", "remove", "status"],
                   help="install: keep the server running on its own, now and after a restart (systemd or launchd); "
                        "remove: stop that; status: what is installed")
    p.add_argument("--port", type=int, default=None,
                   help=f"the port (default: {SERVICE_PORT}; with --handshake or --dev, a free one)")
    p.add_argument("--host", default=None,
                   help=f"the address to listen on (default: {HOST}); for example this machine's Tailscale "
                        "address, or 0.0.0.0 inside a container")
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also accept requests addressed to this name (for example a Tailscale machine name)")
    p.add_argument("--tick", action="store_true",
                   help="also start scheduled jobs each minute, where there is no cron (a container)")
    p.add_argument("--yes", action="store_true", help="for install and remove: don't ask first")
    p.add_argument("--handshake", action="store_true",
                   help='for the desktop app: print {"port", "token"} as one line of JSON, and exit when stdin closes')
    p.add_argument("--dev", action="store_true", help="also accept Vite's dev server, and print a URL to open")


def dispatch(args: argparse.Namespace) -> int:
    if args.action:
        from ombro import service

        return service.cmd(args)
    if args.handshake or args.dev:
        if args.host or args.tick or args.allow_host:
            raise SystemExit("ombro serve: --host, --allow-host and --tick are for the service, "
                             "not --handshake or --dev")
        return run(args.port or 0, args.handshake, args.dev)
    return run_service(SERVICE_PORT if args.port is None else args.port, args.host or HOST,
                       args.allow_host, args.tick)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        return 0
