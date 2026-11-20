"""Signing in to remote MCP servers with OAuth, as MCP's authorization standard
(version 2026-07-28) describes it for a native client.

`ombro connect <server>` does it once, in the browser:

1. Ask the server without a token. Its 401 names its protected-resource
   metadata (RFC 9728), or the well-known addresses are tried.
2. Find the authorization server's metadata (RFC 8414, then OpenID Connect
   discovery), and check that its issuer is the one asked for.
3. Register Ombro as a native client (dynamic registration, RFC 7591), unless
   tools.md gives a `client_id`.
4. Open the browser with PKCE (S256), `state` and `resource` (RFC 8707), and
   catch the answer on a 127.0.0.1 address. Check `state`, and `iss`
   (RFC 9207) before anything else.
5. Swap the code for tokens, sending `resource` again.

The sign-in is kept in secrets/oauth/<server>.json (owner-only), keyed by the
issuer, and refreshed as needed. A job never opens a browser: if the sign-in
has lapsed, the server is not reached and the user is told to connect again.
Tokens are never logged, and each goes only to the server it was issued for.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx

from ombro import mdfile
from ombro.home import Home

CONNECT_TIMEOUT = 300.0  # seconds to wait for the user to sign in
HTTP_TIMEOUT = 20.0
REFRESH_MARGIN = 60.0  # refresh this many seconds before the token expires
CLIENT_NAME = "Ombro"


class SignInError(Exception):
    """Signing in could not be done. The message is shown to the user as it is."""


class SignInNeeded(SignInError):
    """There is no usable sign-in: the user must run `ombro connect`."""


# ---------------------------------------------------------------------------
# Small helpers


def canonical(url: str) -> str:
    """The server's canonical address, used as `resource`: lower-case scheme and
    host, no fragment, no trailing slash."""
    p = urllib.parse.urlsplit(url.strip())
    path = p.path.rstrip("/")
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def _is_local(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _require_https(url: str, what: str) -> None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme != "https" and not (scheme == "http" and _is_local(url)):
        raise SignInError(f"{what} must use https, not {url!r}")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def parse_www_authenticate(value: str) -> dict[str, str]:
    """The parameters of a Bearer challenge, e.g. resource_metadata and scope."""
    m = re.search(r"(?i)\bbearer\b(.*)", value or "")
    if not m:
        return {}
    out = {}
    for k, v1, v2 in re.findall(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))', m.group(1)):
        out[k.lower()] = v1 if v1 or not v2 else v2
    return out


# ---------------------------------------------------------------------------
# The stored sign-in


def store_path(home: Home, server: str) -> Path:
    return home.oauth_dir / f"{server}.json"


def load(home: Home, server: str) -> dict[str, Any] | None:
    try:
        data = json.loads(store_path(home, server).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(home: Home, server: str, data: dict[str, Any]) -> None:
    home.ensure_secrets()
    home.oauth_dir.mkdir(exist_ok=True)
    try:
        os.chmod(home.oauth_dir, 0o700)
    except OSError:
        pass
    mdfile.write_atomic(store_path(home, server), json.dumps(data, indent=2) + "\n", mode=0o600)


@contextmanager
def _lock(home: Home, server: str):
    """One refresh at a time per server, across processes, so a rotated refresh
    token is never used twice."""
    import fcntl

    home.ensure_secrets()
    home.oauth_dir.mkdir(exist_ok=True)
    with open(home.oauth_dir / f".{server}.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def status(home: Home, server: str) -> str:
    """One line for listings: signed in or not, and until when."""
    data = load(home, server)
    if not data or not (data.get("tokens") or {}).get("access_token"):
        return "not signed in"
    tok = data["tokens"]
    if tok.get("refresh_token"):
        return "signed in (renews itself)"
    exp = tok.get("expires_at")
    if exp and exp < time.time():
        return "sign-in expired"
    if exp:
        return "signed in until " + time.strftime("%Y-%m-%d %H:%M", time.localtime(exp))
    return "signed in"


def secret_values(home: Home) -> set[str]:
    """Every token held, so they can be removed from anything shown to a model."""
    out: set[str] = set()
    if not home.oauth_dir.is_dir():
        return out
    for f in home.oauth_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tok = data.get("tokens") or {}
        client = data.get("client") or {}
        for v in (tok.get("access_token"), tok.get("refresh_token"), client.get("client_secret")):
            if isinstance(v, str) and len(v) >= 8:
                out.add(v)
    return out


# ---------------------------------------------------------------------------
# Discovery


def _get_json(client: httpx.Client, url: str) -> dict[str, Any] | None:
    try:
        r = client.get(url, headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def challenge(client: httpx.Client, url: str) -> dict[str, str] | None:
    """Ask the server without a token. Returns the Bearer challenge's parameters,
    or None if the server did not ask for sign-in."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    try:
        r = client.post(
            url,
            content=body,
            timeout=HTTP_TIMEOUT,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/list",
            },
        )
    except httpx.HTTPError as e:
        raise SignInError(f"the server could not be reached: {e}") from None
    if r.status_code != 401:
        return None
    return parse_www_authenticate(r.headers.get("www-authenticate", ""))


def resource_metadata(client: httpx.Client, url: str, hint: str | None) -> dict[str, Any]:
    """The server's protected-resource metadata (RFC 9728)."""
    p = urllib.parse.urlsplit(url)
    origin = f"{p.scheme}://{p.netloc}"
    tries = [hint] if hint else []
    if p.path.strip("/"):
        tries.append(f"{origin}/.well-known/oauth-protected-resource{p.path.rstrip('/')}")
    tries.append(f"{origin}/.well-known/oauth-protected-resource")
    for t in tries:
        _require_https(t, "the server's sign-in details")
        data = _get_json(client, t)
        if data and data.get("authorization_servers"):
            res = data.get("resource")
            if not res:
                raise SignInError(
                    "the server's sign-in details do not say which server they are for "
                    "(no `resource`), so they cannot be checked; refusing them"
                )
            if canonical(str(res)) != canonical(url):
                raise SignInError(f"the server's sign-in details are for {res}, not {url}")
            return data
    raise SignInError(
        "the server asks for sign-in but publishes no sign-in details (no protected-resource metadata was found)"
    )


def server_metadata(client: httpx.Client, issuer: str) -> dict[str, Any]:
    """The authorization server's metadata: RFC 8414, then OpenID Connect
    discovery. Its issuer must be exactly the one asked for."""
    _require_https(issuer, "the sign-in service")
    p = urllib.parse.urlsplit(issuer)
    origin, path = f"{p.scheme}://{p.netloc}", p.path.rstrip("/")
    if path:
        tries = [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    else:
        tries = [f"{origin}/.well-known/oauth-authorization-server", f"{origin}/.well-known/openid-configuration"]
    for t in tries:
        data = _get_json(client, t)
        if data is None:
            continue
        if data.get("issuer") != issuer:
            raise SignInError(
                f"the sign-in service's details claim to be from {data.get('issuer')!r}, not {issuer!r}; refusing them"
            )
        for key in ("authorization_endpoint", "token_endpoint"):
            if not data.get(key):
                raise SignInError(f"the sign-in service's details have no {key}")
            _require_https(data[key], f"the sign-in service's {key}")
        if data.get("revocation_endpoint"):  # tokens are sent there on `ombro disconnect`
            _require_https(data["revocation_endpoint"], "the sign-in service's revocation_endpoint")
        if "S256" not in (data.get("code_challenge_methods_supported") or []):
            raise SignInError("the sign-in service does not support PKCE with S256, which MCP requires")
        return data
    raise SignInError(f"no sign-in details were found for {issuer}")


def register(client: httpx.Client, meta: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    """Dynamic client registration (RFC 7591), as a native app."""
    endpoint = meta.get("registration_endpoint")
    if not endpoint:
        raise SignInError(
            "the sign-in service does not let Ombro register itself; set `client_id` for this "
            "server in tools.md (from the service's developer settings)"
        )
    _require_https(endpoint, "the sign-in service's registration_endpoint")
    body = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "application_type": "native",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    try:
        r = client.post(endpoint, json=body, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        raise SignInError(f"registering with the sign-in service failed: {e}") from None
    if r.status_code not in (200, 201):
        raise SignInError(f"the sign-in service refused to register Ombro (HTTP {r.status_code}): {_oauth_error(r)}")
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SignInError("the sign-in service's answer to registering Ombro was not JSON")
    if not data.get("client_id"):
        raise SignInError("the sign-in service registered Ombro but gave no client_id")
    return {
        "client_id": data["client_id"],
        "client_secret": data.get("client_secret"),
        "registered": True,
        "redirect_uri": redirect_uri,
    }


def _oauth_error(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return "no detail"
    if isinstance(d, dict):
        return " ".join(str(d.get(k)) for k in ("error", "error_description") if d.get(k))[:300] or "no detail"
    return "no detail"


# ---------------------------------------------------------------------------
# The browser step


class Receiver:
    """Catches the sign-in answer on a 127.0.0.1 address, once. `back_to` is where the page it
    shows sends the user back to: the terminal, or the app (when the app's own Ombro signs in)."""

    def __init__(self, back_to: str = "the terminal", path: str = "/callback"):
        from ombro.signin_page import success_page

        page = success_page(back_to).encode("utf-8")
        received: dict[str, str] = {}
        done = threading.Event()
        expected: dict[str, str] = {}
        self._expected = expected

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                answer = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
                if expected.get("state") and answer.get("state") != expected["state"]:
                    # Not the answer to this sign-in (another program, or an old tab): ignore it
                    # and keep waiting, so it cannot end the sign-in.
                    self.send_response(400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"This is not the answer Ombro is waiting for.")
                    return
                if not done.is_set():
                    received.update(answer)
                    done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *a):  # keep the terminal quiet, and codes out of logs
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._received, self._done = received, done
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_address[1]}{path}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def expect(self, state: str) -> None:
        """Only an answer carrying this state is taken."""
        self._expected["state"] = state

    def wait(self, timeout: float) -> dict[str, str]:
        if not self._done.wait(timeout):
            raise SignInError(f"no answer came from the browser within {timeout:.0f}s; try again")
        return dict(self._received)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class HandOver:
    """For a sign-in whose browser isn't on this machine (Ombro on a server): the browser is sent
    back to `http://127.0.0.1:<port>/callback` on the computer it runs on, where the desktop app
    catches the answer and hands it over through serve, or you paste the address the browser
    ended up on (`ombro connect` over SSH). `give` takes it; only the answer to this sign-in
    (its `state`) is taken, so a stray one can't end it."""

    def __init__(self, port: int | None = None, path: str = "/callback"):
        if not port:
            import socket

            with socket.socket() as sock:  # a port that's free here; the browser's machine may differ
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        self.redirect_uri = f"http://127.0.0.1:{port}{path}"
        self._expected: str | None = None
        self._answer: dict[str, str] = {}
        self._done = threading.Event()

    def expect(self, state: str) -> None:
        self._expected = state

    def give(self, answer: dict[str, str]) -> bool:
        """Hand over the answer. False if it isn't the answer to this sign-in."""
        if self._expected and answer.get("state") != self._expected:
            return False
        if not self._done.is_set():
            self._answer.update(answer)
            self._done.set()
        return True

    def wait(self, timeout: float) -> dict[str, str]:
        if not self._done.wait(timeout):
            raise SignInError(f"no answer came from the browser within {timeout:.0f}s; try again")
        return dict(self._answer)

    def close(self) -> None:
        self._done.set()  # nothing more is waited for


def answer_from(text: str) -> dict[str, str]:
    """The answer in an address the browser ended up on, as pasted: the whole address, or just
    what follows its `?`."""
    text = text.strip()
    query = urllib.parse.urlsplit(text).query if "://" in text else text.lstrip("?")
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}


@dataclass
class Connected:
    server: str
    issuer: str
    scope: str | None
    refreshes: bool


def connect(
    home: Home,
    server: str,
    cfg: dict[str, Any],
    *,
    open_browser: Callable[[str], Any],
    say: Callable[[str], None] = print,
    transport: httpx.BaseTransport | None = None,
    receiver: Any = None,
    timeout: float = CONNECT_TIMEOUT,
    back_to: str = "the terminal",
) -> Connected:
    """Sign in to one remote server and keep the result. `open_browser(url)` shows
    the sign-in page; `receiver` (for tests) stands in for the local listener, whose page
    afterwards sends the user back to `back_to`."""
    url = cfg.get("url")
    if not url:
        raise SignInError(
            f"server {server} is started by a command, so it signs in by itself; "
            "only servers with a `url` use `ombro connect`"
        )
    _require_https(url, f"the address of server {server}")
    resource = canonical(url)
    with httpx.Client(transport=transport, follow_redirects=False) as client:
        ch = challenge(client, url)
        if ch is None:
            raise SignInError(f"server {server} did not ask for sign-in, so there is nothing to connect")
        prm = resource_metadata(client, url, ch.get("resource_metadata"))
        issuer = str(prm["authorization_servers"][0])
        meta = server_metadata(client, issuer)

        rec = receiver or Receiver(back_to)
        try:
            if cfg.get("client_id"):
                client_info = {
                    "client_id": str(cfg["client_id"]),
                    "registered": False,
                    "redirect_uri": rec.redirect_uri,
                }
            else:
                client_info = register(client, meta, rec.redirect_uri)
            scope = ch.get("scope") or " ".join(map(str, prm.get("scopes_supported") or [])) or None
            if (
                scope is not None
                and "offline_access" in (meta.get("scopes_supported") or [])
                and "offline_access" not in scope.split()
            ):
                scope += " offline_access"
            verifier, code_challenge = _pkce()
            state = secrets.token_urlsafe(24)
            if hasattr(rec, "expect"):
                rec.expect(state)
            params = {
                "response_type": "code",
                "client_id": client_info["client_id"],
                "redirect_uri": rec.redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": resource,
            }
            if scope:
                params["scope"] = scope
            sep = "&" if "?" in meta["authorization_endpoint"] else "?"
            auth_url = meta["authorization_endpoint"] + sep + urllib.parse.urlencode(params)
            say(f"Opening your browser to sign in to {server} ({issuer}).\nIf it does not open, visit:\n  {auth_url}\n")
            open_browser(auth_url)
            answer = rec.wait(timeout)
        finally:
            rec.close()

        # The issuer first: an answer from the wrong service is not acted on, or even shown.
        if "iss" in answer:
            if answer["iss"] != issuer:
                raise SignInError("the sign-in answer came from a different service than the one asked; it was ignored")
        elif meta.get("authorization_response_iss_parameter_supported"):
            raise SignInError("the sign-in answer did not say which service sent it; it was ignored")
        if answer.get("state") != state:
            raise SignInError("the sign-in answer did not match this request; it was ignored")
        if answer.get("error"):
            raise SignInError(
                f"the sign-in was not completed: {answer.get('error')}"
                + (f" ({answer['error_description'][:200]})" if answer.get("error_description") else "")
            )
        if not answer.get("code"):
            raise SignInError("the sign-in answer had no code")

        form = {
            "grant_type": "authorization_code",
            "code": answer["code"],
            "redirect_uri": rec.redirect_uri,
            "client_id": client_info["client_id"],
            "code_verifier": verifier,
            "resource": resource,
        }
        if client_info.get("client_secret"):
            form["client_secret"] = client_info["client_secret"]
        tokens = _token_request(client, meta["token_endpoint"], form)

    data = {
        "server": server,
        "resource": resource,
        "issuer": issuer,
        "token_endpoint": meta["token_endpoint"],
        "revocation_endpoint": meta.get("revocation_endpoint"),
        "client": client_info,
        "tokens": tokens,
        "connected_at": time.time(),
    }
    save(home, server, data)
    return Connected(server, issuer, tokens.get("scope") or scope, bool(tokens.get("refresh_token")))


def _token_request(
    client: httpx.Client, endpoint: str, form: dict[str, str], keep_refresh: str | None = None
) -> dict[str, Any]:
    try:
        r = client.post(endpoint, data=form, headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        raise SignInError(f"the sign-in service could not be reached: {e}") from None
    if r.status_code != 200:
        raise SignInError(f"the sign-in service refused the request (HTTP {r.status_code}): {_oauth_error(r)}")
    try:
        d = r.json()
    except ValueError:
        raise SignInError("the sign-in service's answer was not JSON") from None
    if not d.get("access_token") or str(d.get("token_type", "bearer")).lower() != "bearer":
        raise SignInError("the sign-in service gave no usable bearer token")
    out = {
        "access_token": d["access_token"],
        "token_type": "Bearer",
        "refresh_token": d.get("refresh_token") or keep_refresh,
        "scope": d.get("scope"),
    }
    if d.get("expires_in"):
        out["expires_at"] = time.time() + float(d["expires_in"])
    return out


# ---------------------------------------------------------------------------
# Using a sign-in


class Session:
    """The sign-in for one server, as a job or an approval uses it. Never opens
    a browser: when it cannot renew itself, it raises SignInNeeded."""

    def __init__(self, home: Home, server: str, url: str, transport: httpx.BaseTransport | None = None):
        self.home, self.server, self.resource = home, server, canonical(url)
        self._transport = transport

    def _data(self) -> dict[str, Any]:
        data = load(self.home, self.server)
        if not data or not (data.get("tokens") or {}).get("access_token"):
            raise SignInNeeded(f"server {self.server} needs sign-in: run `ombro connect {self.server}`")
        if data.get("resource") != self.resource:
            raise SignInNeeded(
                f"the sign-in for server {self.server} was for {data.get('resource')}, not its "
                f"current address; run `ombro connect {self.server}` again"
            )
        return data

    def token(self) -> str:
        data = self._data()
        exp = data["tokens"].get("expires_at")
        if exp and exp - REFRESH_MARGIN < time.time():
            return self.refresh(stale=data["tokens"]["access_token"])
        return data["tokens"]["access_token"]

    def refresh(self, stale: str | None = None) -> str:
        """A fresh access token. `stale` is the one that failed: if another process
        has already replaced it, that one is used instead of refreshing again."""
        with _lock(self.home, self.server):
            data = self._data()
            tok = data["tokens"]
            if stale is not None and tok["access_token"] != stale:
                exp = tok.get("expires_at")
                if not exp or exp - REFRESH_MARGIN >= time.time():
                    return tok["access_token"]
            if not tok.get("refresh_token"):
                raise SignInNeeded(
                    f"the sign-in for server {self.server} has expired: run `ombro connect {self.server}`"
                )
            form = {
                "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"],
                "client_id": data["client"]["client_id"],
                "resource": self.resource,
            }
            if data["client"].get("client_secret"):
                form["client_secret"] = data["client"]["client_secret"]
            with httpx.Client(transport=self._transport, follow_redirects=False) as client:
                try:
                    new = _token_request(client, data["token_endpoint"], form, keep_refresh=tok["refresh_token"])
                except SignInError as e:
                    raise SignInNeeded(
                        f"the sign-in for server {self.server} could not be renewed ({e}): "
                        f"run `ombro connect {self.server}`"
                    ) from None
            data["tokens"] = new
            save(self.home, self.server, data)
            return new["access_token"]


def session_for(
    home: Home, server: str, cfg: dict[str, Any], transport: httpx.BaseTransport | None = None
) -> Session | None:
    """The sign-in a remote server uses, if it has one. A server whose headers
    already carry Authorization (a fixed token from keys.env) does not use one."""
    if not cfg.get("url") or any(k.lower() == "authorization" for k in (cfg.get("headers") or {})):
        return None
    if load(home, server) is None:
        return None
    return Session(home, server, cfg["url"], transport)


def disconnect(home: Home, server: str, transport: httpx.BaseTransport | None = None) -> bool:
    """Forget a sign-in, telling the service to revoke it where it can. Returns
    whether there was one."""
    data = load(home, server)
    if data is None:
        return False
    endpoint = data.get("revocation_endpoint")
    tok = data.get("tokens") or {}
    try:
        if endpoint:
            _require_https(endpoint, "the revocation address")
    except SignInError:
        endpoint = None  # never send tokens in the clear; forgetting them locally still happens
    if endpoint:
        try:
            with httpx.Client(transport=transport, follow_redirects=False) as client:
                for kind in ("refresh_token", "access_token"):
                    if tok.get(kind):
                        client.post(
                            endpoint,
                            timeout=HTTP_TIMEOUT,
                            data={
                                "token": tok[kind],
                                "token_type_hint": kind,
                                "client_id": (data.get("client") or {}).get("client_id", ""),
                            },
                        )
        except httpx.HTTPError:
            pass  # forgetting it locally is what matters
    store_path(home, server).unlink(missing_ok=True)
    return True
