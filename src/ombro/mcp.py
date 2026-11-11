"""A small MCP client: tools only, spoken directly rather than through the SDK.

Two transports:
- stdio: Ombro starts the server as a child process (it handles its own
  sign-in) and talks newline-delimited JSON-RPC over its stdin and stdout;
- http: Ombro posts JSON-RPC to a remote server's address ("streamable HTTP"),
  with any token taken from .env.

Only what Ombro needs is implemented: initialize, tools/list and tools/call.
Requests from the server (sampling, roots, elicitation) are refused, since
Ombro offers none of them; pings are answered.

Values written as ${NAME} in a server's settings are read from .env when the
server starts, and are never logged.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ombro import __version__, keys
from ombro.home import Home
from ombro.tools import ToolError

PROTOCOL_VERSION = "2025-06-18"
START_TIMEOUT = 30.0
CALL_TIMEOUT = 60.0
RESULT_MAX_CHARS = 8_000
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpError(ToolError):
    """An MCP server could not do what was asked. The message is shown to the caller."""


class McpNotReached(McpError):
    """The server could not be started or reached, so nothing was sent to it."""

    billable = False


class McpCallLost(McpError):
    """A call was sent, but no answer came back: it may or may not have happened."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = CALL_TIMEOUT
    start_timeout: float = START_TIMEOUT

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "ServerConfig":
        return cls(
            name=name,
            command=d.get("command"),
            args=tuple(str(a) for a in d.get("args") or ()),
            env={str(k): str(v) for k, v in (d.get("env") or {}).items()},
            url=d.get("url"),
            headers={str(k): str(v) for k, v in (d.get("headers") or {}).items()},
            timeout=float(d.get("timeout") or CALL_TIMEOUT),
            start_timeout=float(d.get("start_timeout") or START_TIMEOUT),
        )

    def key_refs(self) -> list[str]:
        texts = [self.command or "", *self.args, *self.env.values(), self.url or "", *self.headers.values()]
        return sorted({m for t in texts for m in _REF.findall(t)})


def expand(home: Home, text: str, server: str) -> str:
    def sub(m: re.Match) -> str:
        value = keys.get_key(home, m.group(1))
        if not value:
            raise McpNotReached(f"server {server} needs {m.group(1)}, which is not set (`ombro keys set {m.group(1)}`)")
        return value

    return _REF.sub(sub, text)


@dataclass
class McpTool:
    name: str  # the server's own name for it
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]


def result_text(result: dict[str, Any], max_chars: int = RESULT_MAX_CHARS) -> str:
    """The text of a tools/call result, as the caller sees it, capped."""
    parts: list[str] = []
    for block in result.get("content") or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "resource" and isinstance(block.get("resource"), dict) and "text" in block["resource"]:
            parts.append(str(block["resource"]["text"]))
        elif kind:
            parts.append(f"[{kind} content omitted]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…[cut short]"
    return text


# ---------------------------------------------------------------------------
# Transports


class _Stdio:
    def __init__(self, home: Home, cfg: ServerConfig, log: Path | None):
        cmd = [expand(home, cfg.command or "", cfg.name), *(expand(home, a, cfg.name) for a in cfg.args)]
        env = {**os.environ, **{k: expand(home, v, cfg.name) for k, v in cfg.env.items()}}
        self._log = open(log, "ab") if log else subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, env=env,
                start_new_session=True,
            )
        except OSError as e:
            self._close_log()
            raise McpNotReached(f"server {cfg.name} could not be started ({cmd[0]}): {e.strerror or e}") from None
        self.inbox: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.lock = threading.Lock()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # servers sometimes print stray lines; they are not messages
            if isinstance(msg, dict):
                self.inbox.put(msg)
        self.inbox.put(None)  # the server closed its output

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode()
        with self.lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                raise ConnectionError("the server has stopped") from None

    def receive(self, timeout: float) -> dict | None:
        """The next message, or None if the server has closed. Raises TimeoutError."""
        try:
            msg = self.inbox.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            raise TimeoutError from None
        if msg is None:
            self.inbox.put(None)
        return msg

    def request(self, msg: dict, timeout: float) -> list[dict]:
        self.send(msg)
        return []  # replies arrive through receive()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._close_log()

    def _close_log(self) -> None:
        if self._log not in (subprocess.DEVNULL, None):
            self._log.close()


def _sse_messages(text: str) -> list[dict]:
    out, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            try:
                msg = json.loads("\n".join(data))
            except ValueError:
                msg = None
            if isinstance(msg, dict):
                out.append(msg)
            elif isinstance(msg, list):
                out.extend(m for m in msg if isinstance(m, dict))
            data = []
    return out


class _Http:
    def __init__(self, home: Home, cfg: ServerConfig, transport: httpx.BaseTransport | None):
        self.url = expand(home, cfg.url or "", cfg.name)
        self.headers = {k: expand(home, v, cfg.name) for k, v in cfg.headers.items()}
        self.client = httpx.Client(transport=transport, follow_redirects=False)
        self.session: str | None = None
        self.protocol: str | None = None
        self.pending: list[dict] = []

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def request(self, msg: dict, timeout: float) -> list[dict]:
        try:
            r = self.client.post(self.url, content=json.dumps(msg), headers=self._headers(),
                                 timeout=httpx.Timeout(max(timeout, 0.5), connect=min(max(timeout, 0.5), 15.0)))
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError as e:
            raise ConnectionError(str(e)) from None
        if r.headers.get("mcp-session-id"):
            self.session = r.headers["mcp-session-id"]
        if r.status_code in (202, 204):
            return []
        if r.status_code >= 400:
            raise ConnectionError(f"HTTP {r.status_code}")
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return _sse_messages(r.text)
        try:
            body = r.json()
        except ValueError:
            return []
        return [m for m in (body if isinstance(body, list) else [body]) if isinstance(m, dict)]

    def send(self, msg: dict) -> None:
        self.request(msg, 15.0)

    def receive(self, timeout: float) -> dict | None:
        if self.pending:
            return self.pending.pop(0)
        raise TimeoutError

    def close(self) -> None:
        if self.session:
            try:
                self.client.delete(self.url, headers=self._headers(), timeout=5.0)
            except httpx.HTTPError:
                pass
        self.client.close()


# ---------------------------------------------------------------------------
# The client


class McpClient:
    """One connection to one server. Not thread-safe; one caller at a time."""

    def __init__(self, home: Home, cfg: ServerConfig, *, log: Path | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.home = home
        self.cfg = cfg
        self._log = log
        self._transport = transport
        self._t: _Stdio | _Http | None = None
        self._next = 0
        self.server_info: dict[str, Any] = {}

    # -- connecting --------------------------------------------------------

    def start(self) -> None:
        if self._t is not None:
            return
        if self.cfg.url:
            self._t = _Http(self.home, self.cfg, self._transport)
        elif self.cfg.command:
            self._t = _Stdio(self.home, self.cfg, self._log)
        else:
            raise McpNotReached(f"server {self.cfg.name} has neither a command nor a url in tools.md")
        try:
            result = self._call("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ombro", "version": __version__},
            }, self.cfg.start_timeout)
            if isinstance(self._t, _Http):
                self._t.protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
            self.server_info = result.get("serverInfo") or {}
            self._notify("notifications/initialized")
        except McpError as e:
            self.close()
            raise McpNotReached(f"server {self.cfg.name} did not start properly: {e}") from None

    def close(self) -> None:
        if self._t is not None:
            try:
                self._t.close()
            finally:
                self._t = None

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- JSON-RPC ----------------------------------------------------------

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            assert self._t is not None
            self._t.send(msg)
        except (ConnectionError, TimeoutError) as e:
            raise McpError(f"server {self.cfg.name}: {e or 'no answer'}") from None

    def _answer_server(self, msg: dict) -> None:
        """A request from the server. Ombro offers none of them, apart from ping."""
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
        if msg.get("method") == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": -32601, "message": "Ombro does not offer this"}
        try:
            assert self._t is not None
            self._t.send(reply)
        except (ConnectionError, TimeoutError):
            pass

    def _call(self, method: str, params: dict, timeout: float, *, sent: list | None = None) -> dict[str, Any]:
        """Send one request and wait for its answer. `sent` is marked once the
        request has left, so a caller can tell 'never sent' from 'no answer'."""
        assert self._t is not None
        self._next += 1
        rid = self._next
        deadline = time.monotonic() + timeout
        try:
            replies = self._t.request({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, timeout)
        except TimeoutError:
            if sent is not None:
                sent.append(True)
            raise McpCallLost(f"server {self.cfg.name} did not answer {method} within {timeout:.0f}s") from None
        except ConnectionError as e:
            raise McpNotReached(f"server {self.cfg.name} could not be reached: {e}") from None
        if sent is not None:
            sent.append(True)
        if isinstance(self._t, _Http):
            self._t.pending.extend(replies)
        while True:
            left = deadline - time.monotonic()
            try:
                msg = self._t.receive(left if left > 0 else 0.01)
            except TimeoutError:
                raise McpCallLost(f"server {self.cfg.name} did not answer {method} within {timeout:.0f}s") from None
            if msg is None:
                raise McpCallLost(f"server {self.cfg.name} stopped before answering {method}")
            if "method" in msg and "id" in msg:
                self._answer_server(msg)
                continue
            if msg.get("id") != rid:
                continue  # a notification, or an answer to something abandoned
            if "error" in msg:
                err = msg.get("error") or {}
                raise McpError(f"server {self.cfg.name}: {err.get('message') or 'error'}")
            result = msg.get("result")
            return result if isinstance(result, dict) else {}

    # -- tools -------------------------------------------------------------

    def list_tools(self) -> list[McpTool]:
        self.start()
        out: list[McpTool] = []
        cursor = None
        for _ in range(50):
            result = self._call("tools/list", {"cursor": cursor} if cursor else {}, self.cfg.start_timeout)
            for t in result.get("tools") or []:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
                out.append(McpTool(
                    name=str(t["name"]),
                    description=str(t.get("description") or ""),
                    input_schema=schema or {"type": "object", "properties": {}},
                    annotations=t.get("annotations") if isinstance(t.get("annotations"), dict) else {},
                ))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return out

    def call_tool(self, name: str, arguments: dict, timeout: float | None = None,
                  sent: list | None = None) -> dict[str, Any]:
        """The raw tools/call result. Raises McpNotReached if nothing was sent,
        McpCallLost if it was sent and no answer came, McpError if refused."""
        self.start()
        return self._call("tools/call", {"name": name, "arguments": arguments},
                          min(timeout or self.cfg.timeout, self.cfg.timeout), sent=sent)
