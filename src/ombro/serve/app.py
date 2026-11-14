"""The Starlette app behind `ombro serve`: the token and origin guard, /hello and /events."""

from __future__ import annotations

import secrets
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from ombro import __version__
from ombro.serve import DEV_ORIGIN, HOST
from ombro.ui import Note

APP_ORIGINS = ("tauri://localhost", "http://tauri.localhost")
SUBPROTOCOL = "ombro"


def origins(dev: bool = False) -> list[str]:
    return [*APP_ORIGINS, DEV_ORIGIN] if dev else list(APP_ORIGINS)


def hello() -> dict[str, Any]:
    """The first message on every socket."""
    event = Note(f"Ombro {__version__} is running.", level="done")
    return {"type": "event", "session": None, "seq": 1, "event": event.to_dict()}


class Guard:
    """Refuses any request without the token, or from an origin that isn't the app's."""

    def __init__(self, app: ASGIApp, token: str, allowed: list[str]):
        self.app, self.token, self.allowed = app, token, allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed:
            return await self._refuse(scope, receive, send)
        if scope["type"] == "http":
            given = headers.get("authorization", "").removeprefix("Bearer ")
        else:
            given = next((p for p in scope.get("subprotocols", []) if p != SUBPROTOCOL), "")
        if not secrets.compare_digest(given.encode(), self.token.encode()):
            return await self._refuse(scope, receive, send)
        await self.app(scope, receive, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await WebSocket(scope, receive, send).close(code=1008)
        else:
            await Response(status_code=403)(scope, receive, send)


async def _hello(request: Request) -> JSONResponse:
    return JSONResponse({"version": __version__})


async def _events(ws: WebSocket) -> None:
    await ws.accept(subprotocol=SUBPROTOCOL)
    await ws.send_json(hello())
    # Nothing else to say yet; hold the socket open until the app closes it.
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return


def build_app(token: str, dev: bool = False) -> Starlette:
    allowed = origins(dev)
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"] if dev else [HOST]),
        # Answers the app's preflight; never a wildcard.
        Middleware(CORSMiddleware, allow_origins=allowed, allow_methods=["*"], allow_headers=["authorization", "content-type"]),
        Middleware(Guard, token=token, allowed=allowed),
    ]
    routes = [Route("/hello", _hello), WebSocketRoute("/events", _events)]
    return Starlette(routes=routes, middleware=middleware)
