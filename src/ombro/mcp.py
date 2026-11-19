"""A small MCP client: tools only, spoken directly rather than through the SDK.

Two transports:
- stdio: Ombro starts the server as a child process (it handles its own
  sign-in) and talks newline-delimited JSON-RPC over its stdin and stdout;
- http: Ombro posts JSON-RPC to a remote server's address ("streamable HTTP"),
  with any token taken from secrets/keys.env.

Only what Ombro needs is implemented: tools/list and tools/call, in both eras
of the protocol:
- modern (2026-07-28): no handshake and no session; every request carries
  its protocol version, client capabilities and client info in `_meta`, and
  over HTTP the MCP-Protocol-Version, Mcp-Method and Mcp-Name headers;
- legacy (2025-06-18): the initialize handshake, and a session over HTTP.
Ombro asks in the modern way first (server/discover) and falls back to the
legacy handshake for a server that does not understand it, as the standard
describes. Requests from a legacy server (sampling, roots, elicitation) are
refused, since Ombro offers none of them; pings are answered.

Values written as ${NAME} in a server's settings are read from secrets/keys.env when the
server starts, and are never logged.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ombro import __version__, keys
from ombro.home import Home
from ombro.tools import ToolError

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-06-18"
PROTOCOL_VERSION = LEGACY_VERSION  # what the legacy handshake asks for
PROBE_TIMEOUT = 10.0  # a stdio server that says nothing this long is treated as legacy
UNSUPPORTED_VERSION = -32022
_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
START_TIMEOUT = 30.0
CALL_TIMEOUT = 60.0
RESULT_MAX_CHARS = 8_000
ANSWER_MAX_BYTES = 4_000_000  # the most of one answer Ombro reads, over HTTP or stdio
TOO_LARGE = "_ombro_answer_too_large"  # stdio: stands for a message that was over the cap
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# What a started server gets from Ombro's own environment: enough to find programs,
# a home folder, the locale and a temporary folder. Nothing else, so keys kept in a
# shell never reach a third-party server. `pass_env` in tools.md adds names.
BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")


class McpError(ToolError):
    """An MCP server could not do what was asked. The message is shown to the caller."""

    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code, self.data = code, data


class McpNotReached(McpError):
    """The server could not be started or reached, so nothing was sent to it."""


class McpCallLost(McpError):
    """A call was sent, but no answer came back: it may or may not have happened."""


class _AnswerLost(TimeoutError):
    """Inside a transport: the request left, but no usable answer came back."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    pass_env: tuple[str, ...] = ()
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
            pass_env=tuple(str(n) for n in d.get("pass_env") or ()),
            url=d.get("url"),
            headers={str(k): str(v) for k, v in (d.get("headers") or {}).items()},
            timeout=float(d.get("timeout") or CALL_TIMEOUT),
            start_timeout=float(d.get("start_timeout") or START_TIMEOUT),
        )

    def argv_key_refs(self) -> list[str]:
        """${NAME} values in `command` or `args`: on the command line, which other users of
        this machine can read in the process list. `env` is the place for them."""
        return sorted({m for t in [self.command or "", *self.args] for m in _REF.findall(t)})

    def argv_warning(self) -> str | None:
        refs = self.argv_key_refs()
        if not refs:
            return None
        return (f"server {self.name} puts {', '.join(refs)} on its command line (`command` or `args` in tools.md), "
                "where other users of this machine can see it in the process list. Pass it in `env` instead.")

    def key_refs(self) -> list[str]:
        texts = [self.command or "", *self.args, *self.env.values(), self.url or "", *self.headers.values()]
        return sorted({m for t in texts for m in _REF.findall(t)})


def server_env(home: Home, cfg: "ServerConfig") -> dict[str, str]:
    """The environment a started server gets: BASE_ENV and its pass_env from Ombro's
    own, then its `env` from tools.md with ${NAME} read from secrets/keys.env."""
    out = {k: os.environ[k] for k in (*BASE_ENV, *cfg.pass_env) if k in os.environ}
    out.update({k: expand(home, v, cfg.name) for k, v in cfg.env.items()})
    return out


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
    param_headers: dict[str, str] = field(default_factory=dict)  # argument -> Mcp-Param-<Name>


def header_value(value: Any) -> str:
    """A header value as the 2026-07-28 transport sends it: plain ASCII as it is,
    anything else as =?base64?...?=."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    plain = (text.isascii() and text == text.strip() and text.isprintable() and not text.startswith("=?base64?"))
    return text if plain else "=?base64?" + base64.b64encode(text.encode()).decode() + "?="


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
        env = server_env(home, cfg)
        # Values from secrets/keys.env, so they can be hidden from anything the server prints.
        self._secrets = [v for v in (keys.get_key(home, n) for n in cfg.key_refs()) if v]
        # The server's own output goes to the job's log, or to a temporary file,
        # so a failure to start can show its last lines.
        self._log_path = log
        self._log = open(log, "ab") if log else tempfile.TemporaryFile()
        self._log_start = self._log.tell()
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
        out = self.proc.stdout
        while True:
            raw = out.readline(ANSWER_MAX_BYTES + 1)
            if not raw:
                break
            if len(raw) > ANSWER_MAX_BYTES and not raw.endswith(b"\n"):
                # Over the cap: skip the rest of this line and say so, never hold it.
                while (more := out.readline(1 << 20)) and not more.endswith(b"\n"):
                    pass
                self.inbox.put({TOO_LARGE: True})
                continue
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

    def output_tail(self, lines: int = 5, max_chars: int = 600) -> str:
        """The last lines the server printed since it was started, with key values hidden."""
        try:
            self._log.flush()
            if self._log_path:
                with open(self._log_path, "rb") as f:
                    f.seek(self._log_start)
                    data = f.read()
            else:
                self._log.seek(self._log_start)
                data = self._log.read()
        except (OSError, ValueError):
            return ""
        text = data.decode("utf-8", "replace")
        for secret in self._secrets:
            text = text.replace(secret, "[hidden]")
        kept = [ln.strip() for ln in text.splitlines() if ln.strip()][-lines:]
        out = " | ".join(kept)
        return out if len(out) <= max_chars else "…" + out[-max_chars:]

    def _close_log(self) -> None:
        if self._log is not None and not self._log.closed:
            self._log.close()


def _json_rpc_error(r: httpx.Response, rid: Any) -> dict | None:
    """The JSON-RPC error in a 4xx answer to request `rid`, if it holds one."""
    if rid is None or "json" not in r.headers.get("content-type", ""):
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("id") == rid and isinstance(body.get("error"), dict):
        return body
    return None


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
        from ombro import oauth

        self.name = cfg.name
        self.url = expand(home, cfg.url or "", cfg.name)
        self.headers = {k: expand(home, v, cfg.name) for k, v in cfg.headers.items()}
        self.client = httpx.Client(transport=transport, follow_redirects=False)
        # A sign-in from `ombro connect`, if this server has one. Never opens a browser.
        self.auth = oauth.session_for(home, cfg.name, {"url": self.url, "headers": self.headers}, transport)
        self.session: str | None = None
        self.protocol: str | None = None
        self.modern = False  # set by the client once it knows the server's era
        self.extra_headers: dict[str, str] = {}  # for the next request only (Mcp-Param-*)
        self.last_status: int | None = None
        self.pending: list[dict] = []

    def _headers(self, msg: dict | None = None) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session and not self.modern:
            h["Mcp-Session-Id"] = self.session
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        if self.modern and msg and msg.get("method"):
            h["Mcp-Method"] = header_value(msg["method"])
            name = (msg.get("params") or {}).get("name")
            if msg["method"] == "tools/call" and name:
                h["Mcp-Name"] = header_value(name)
            h.update(self.extra_headers)
        return h

    def _post(self, msg: dict, timeout: float, token: str | None) -> httpx.Response:
        """Post one message and read at most ANSWER_MAX_BYTES of the answer."""
        headers = self._headers(msg)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with self.client.stream("POST", self.url, content=json.dumps(msg), headers=headers,
                                timeout=httpx.Timeout(max(timeout, 0.5), connect=min(max(timeout, 0.5), 15.0))) as r:
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > ANSWER_MAX_BYTES:
                    raise _AnswerLost(f"the answer was larger than {ANSWER_MAX_BYTES:,} bytes, so it was not read")
            kept = {k: v for k, v in r.headers.items() if k.lower() not in ("content-encoding", "content-length",
                                                                               "transfer-encoding")}
            return httpx.Response(r.status_code, headers=kept, content=bytes(body), request=r.request)

    def _token(self, stale: str | None = None) -> str | None:
        from ombro.oauth import SignInError

        if self.auth is None:
            return None
        try:
            return self.auth.refresh(stale=stale) if stale else self.auth.token()
        except SignInError as e:
            raise ConnectionError(str(e)) from None

    def request(self, msg: dict, timeout: float) -> list[dict]:
        token = self._token()
        try:
            r = self._post(msg, timeout, token)
            if r.status_code == 401 and token:
                # Refused before it was handled, so it is safe to send again once renewed.
                token = self._token(stale=token)
                r = self._post(msg, timeout, token)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol) as e:
            # Nothing left the machine: safe to try again later.
            raise ConnectionError(str(e) or type(e).__name__) from None
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError as e:
            # The request may have reached the server (a dropped connection, a broken
            # answer): it may or may not have happened.
            raise _AnswerLost(f"the connection failed after the request was sent ({str(e) or type(e).__name__})") from None
        self.last_status = r.status_code
        if r.headers.get("mcp-session-id") and not self.modern:
            self.session = r.headers["mcp-session-id"]
        if r.status_code in (202, 204):
            return []
        if r.status_code >= 500:
            # The server failed while handling it; it may have acted first.
            raise _AnswerLost(f"the server answered HTTP {r.status_code}")
        if r.status_code == 401:
            raise ConnectionError(f"it needs sign-in: run `ombro connect {self.name}`" if not token else
                                  f"it refused the sign-in: run `ombro connect {self.name}` again")
        if r.status_code == 403 and "insufficient_scope" in r.headers.get("www-authenticate", ""):
            raise ConnectionError(f"it needs more access than the sign-in gave: run `ombro connect {self.name}` "
                                  "again")
        if r.status_code >= 400:
            # A server that answers with a JSON-RPC error has answered: the error is
            # the reply (the 2026-07-28 transport sends errors with HTTP 4xx).
            answer = _json_rpc_error(r, msg.get("id"))
            if answer is not None:
                return [answer]
            # Refused before it was handled (a lost session, a bad address).
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
        if self.session and not self.modern:
            headers = self._headers()
            try:
                token = self._token()
            except ConnectionError:
                token = None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                self.client.delete(self.url, headers=headers, timeout=5.0)
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
        self.version: str | None = None  # the protocol version agreed with the server
        self._param_headers: dict[str, dict[str, str]] = {}

    @property
    def modern(self) -> bool:
        return self.version == MODERN_VERSION

    def _meta(self) -> dict[str, Any]:
        return {"io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "ombro", "version": __version__}}

    def _params(self, params: dict) -> dict:
        """Request params as the agreed era wants them."""
        return {**params, "_meta": self._meta()} if self.modern else params

    def _go_modern(self, info: dict | None = None) -> None:
        self.version = MODERN_VERSION
        if isinstance(self._t, _Http):
            self._t.modern, self._t.protocol = True, MODERN_VERSION
        meta = (info or {}).get("_meta") or {}
        self.server_info = meta.get("io.modelcontextprotocol/serverInfo") or {}

    def _modern_offered(self, e: McpError) -> bool:
        data = e.data if isinstance(e.data, dict) else {}
        return e.code == UNSUPPORTED_VERSION and MODERN_VERSION in (data.get("supported") or [])

    def _probe(self) -> bool:
        """Does the server speak 2026-07-28? Asks server/discover, as the standard
        advises. A modern answer, or a modern 'unsupported version' error that
        offers 2026-07-28, means modern. Anything else means legacy: an
        unknown-method error, a plain HTTP refusal (400, 404, 405), or, over
        stdio, no answer within PROBE_TIMEOUT."""
        assert self._t is not None
        stdio = isinstance(self._t, _Stdio)
        if not stdio:
            self._t.modern, self._t.protocol = True, MODERN_VERSION
        try:
            # _call adds the modern _meta itself once the version is set, so set it for the probe.
            self.version = MODERN_VERSION
            result = self._call("server/discover", {}, PROBE_TIMEOUT if stdio else self.cfg.start_timeout)
        except McpError as e:
            self.version = None
            if self._modern_offered(e):
                self._go_modern()
                return True
            if isinstance(e, McpCallLost):
                if stdio:
                    return False  # silence: a legacy server, or one still starting
                raise
            if isinstance(e, McpNotReached):
                if not stdio and self._t.last_status in (400, 404, 405):
                    return self._legacy_http()
                raise
            return self._legacy_http() if not stdio else False
        self.version = None
        if MODERN_VERSION in (result.get("supportedVersions") or []):
            self._go_modern(result)
            return True
        return self._legacy_http() if not stdio else False

    def _legacy_http(self) -> bool:
        assert isinstance(self._t, _Http)
        self._t.modern, self._t.protocol = False, None
        return False

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
            if not self._probe():
                try:
                    result = self._call("initialize", {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ombro", "version": __version__},
                    }, self.cfg.start_timeout)
                except McpError as e:
                    if not self._modern_offered(e):  # a slow modern server, found out late
                        raise
                    self._go_modern()
                else:
                    self.version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
                    if isinstance(self._t, _Http):
                        self._t.protocol = self.version
                    self.server_info = result.get("serverInfo") or {}
                    self._notify("notifications/initialized")
        except McpError as e:
            tail = ""
            if isinstance(self._t, _Stdio):
                time.sleep(0.2)  # let a dying server finish printing
                tail = self._t.output_tail()
            self.close()
            raise McpNotReached(f"server {self.cfg.name} did not start properly: {e}"
                                + (f". Its last output: {tail}" if tail else "")) from None

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
            replies = self._t.request({"jsonrpc": "2.0", "id": rid, "method": method,
                                       "params": self._params(params)}, timeout)
        except TimeoutError as e:
            if sent is not None:
                sent.append(True)
            if isinstance(e, _AnswerLost):
                raise McpCallLost(f"server {self.cfg.name}: {e}") from None
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
            if msg.get(TOO_LARGE):
                raise McpCallLost(f"server {self.cfg.name} sent an answer to {method} larger than "
                                  f"{ANSWER_MAX_BYTES:,} bytes, so it was not read")
            if "method" in msg and "id" in msg:
                self._answer_server(msg)
                continue
            if msg.get("id") != rid:
                continue  # a notification, or an answer to something abandoned
            if "error" in msg:
                err = msg.get("error") or {}
                raise McpError(f"server {self.cfg.name}: {err.get('message') or 'error'}",
                               code=err.get("code"), data=err.get("data"))
            result = msg.get("result")
            result = result if isinstance(result, dict) else {}
            kind = result.get("resultType", "complete")
            if kind == "input_required":
                raise McpError(f"server {self.cfg.name} asked for input during {method}, which Ombro cannot give")
            if kind != "complete":
                raise McpError(f"server {self.cfg.name} gave an answer of an unknown kind ({kind!r})")
            return result

    # -- tools -------------------------------------------------------------

    def list_tools(self) -> list[McpTool]:
        self.start()
        out: list[McpTool] = []
        cursor = None
        for _ in range(50):
            params = {"cursor": cursor} if cursor else {}
            try:
                result = self._call("tools/list", params, self.cfg.start_timeout)
            except McpCallLost:
                if not self.modern:
                    raise
                # 2026-07-28: a broken answer loses the request; ask again with a new id.
                # Only listing is asked again: a tool call that may have happened never is.
                result = self._call("tools/list", params, self.cfg.start_timeout)
            for t in result.get("tools") or []:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
                headers = _param_headers(schema)
                if headers is None:
                    continue  # an invalid x-mcp-header: the standard says to leave the tool out
                out.append(McpTool(
                    name=str(t["name"]),
                    description=str(t.get("description") or ""),
                    input_schema=schema or {"type": "object", "properties": {}},
                    annotations=t.get("annotations") if isinstance(t.get("annotations"), dict) else {},
                    param_headers=headers,
                ))
                self._param_headers[str(t["name"])] = headers
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return out

    def call_tool(self, name: str, arguments: dict, timeout: float | None = None,
                  sent: list | None = None) -> dict[str, Any]:
        """The raw tools/call result. Raises McpNotReached if nothing was sent,
        McpCallLost if it was sent and no answer came, McpError if refused."""
        self.start()
        if isinstance(self._t, _Http) and self.modern:
            self._t.extra_headers = {
                f"Mcp-Param-{h}": header_value(arguments[arg])
                for arg, h in self._param_headers.get(name, {}).items()
                if isinstance(arguments.get(arg), (str, int, float, bool)) and arguments.get(arg) is not None}
        try:
            return self._call("tools/call", {"name": name, "arguments": arguments},
                              min(timeout or self.cfg.timeout, self.cfg.timeout), sent=sent)
        finally:
            if isinstance(self._t, _Http):
                self._t.extra_headers = {}


def _param_headers(schema: dict) -> dict[str, str] | None:
    """Arguments a 2026-07-28 server wants mirrored into Mcp-Param-<Name> headers,
    or None if one of its x-mcp-header names is invalid."""
    out: dict[str, str] = {}
    for arg, prop in ((schema or {}).get("properties") or {}).items():
        if isinstance(prop, dict) and "x-mcp-header" in prop:
            name = prop["x-mcp-header"]
            if not isinstance(name, str) or not _HEADER_TOKEN.match(name):
                return None
            out[arg] = name
    return out
