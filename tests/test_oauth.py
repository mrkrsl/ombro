"""Signing in to remote MCP servers (OAuth). An invented sign-in service and
remote server are stood in for by a mock transport, and the browser by a
function, so nothing touches the network."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest

from ombro import keys, oauth
from ombro.mcp import McpClient, McpNotReached, ServerConfig

MCP = "https://mcp.invalid/mcp"
ISSUER = "https://auth.invalid"
CFG = {"url": MCP}


class World:
    """An invented remote MCP server and its sign-in service."""

    def __init__(self, *, s256=True, iss_supported=True, metadata_issuer=ISSUER, registration=True):
        self.s256, self.iss_supported, self.metadata_issuer, self.registration = (
            s256,
            iss_supported,
            metadata_issuer,
            registration,
        )
        self.codes: dict[str, dict] = {}
        self.valid_access = set()
        self.refresh = {}
        self.seen: list[tuple[str, str, dict]] = []
        self.revoked = []
        self.n = 0
        self.fail_refresh = False

    def _token_pair(self, client_id):
        self.n += 1
        access, refresh = f"access-token-{self.n:04d}-invented", f"refresh-token-{self.n:04d}-invented"
        self.valid_access.add(access)
        self.refresh[refresh] = client_id
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh,
            "scope": "notes offline_access",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        body = request.content.decode() if request.content else ""
        self.seen.append((request.method, url, dict(request.headers)))
        if url == MCP:
            auth = request.headers.get("authorization", "")
            if auth.removeprefix("Bearer ") not in self.valid_access:
                return httpx.Response(
                    401,
                    headers={
                        "WWW-Authenticate": (
                            'Bearer realm="OAuth", resource_metadata="https://mcp.invalid/.well-known/'
                            'oauth-protected-resource/mcp", scope="notes"'
                        )
                    },
                )
            msg = json.loads(body)
            if "id" not in msg:
                return httpx.Response(202)
            if msg["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {}}
            elif msg["method"] == "tools/list":
                result = {"tools": [{"name": "add_page", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "page added"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": result})
        if url == "https://mcp.invalid/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200, json={"resource": MCP, "authorization_servers": [ISSUER], "scopes_supported": ["notes"]}
            )
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            meta = {
                "issuer": self.metadata_issuer,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "revocation_endpoint": f"{ISSUER}/revoke",
                "scopes_supported": ["notes", "offline_access"],
                "code_challenge_methods_supported": ["S256"] if self.s256 else ["plain"],
                "authorization_response_iss_parameter_supported": self.iss_supported,
            }
            if self.registration:
                meta["registration_endpoint"] = f"{ISSUER}/register"
            return httpx.Response(200, json=meta)
        if url == f"{ISSUER}/register":
            self.registered = json.loads(body)
            return httpx.Response(201, json={"client_id": "invented-client-1"})
        if url == f"{ISSUER}/token":
            form = dict(urllib.parse.parse_qsl(body))
            self.token_forms = getattr(self, "token_forms", []) + [form]
            if form.get("resource") != MCP:
                return httpx.Response(400, json={"error": "invalid_target"})
            if form["grant_type"] == "authorization_code":
                grant = self.codes.pop(form.get("code"), None)
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                if not grant or grant["code_challenge"] != challenge or grant["redirect_uri"] != form["redirect_uri"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._token_pair(form["client_id"]))
            if form["grant_type"] == "refresh_token":
                if self.fail_refresh or self.refresh.pop(form.get("refresh_token"), None) is None:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._token_pair(form["client_id"]))
        if url == f"{ISSUER}/revoke":
            self.revoked.append(dict(urllib.parse.parse_qsl(body))["token_type_hint"])
            return httpx.Response(200)
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handle)


class FakeBrowser:
    """Plays the user signing in: reads the sign-in address, and answers as the
    service would. `tamper` changes the answer."""

    def __init__(self, world: World, tamper=None):
        self.world, self.tamper = world, tamper or (lambda a: a)
        self.redirect_uri = "http://127.0.0.1:9/callback"
        self.params: dict = {}
        self._answer: dict = {}

    def __call__(self, url: str):
        self.params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        code = "invented-code-1"
        self.world.codes[code] = {
            "code_challenge": self.params["code_challenge"],
            "redirect_uri": self.params["redirect_uri"],
        }
        self._answer = self.tamper({"code": code, "state": self.params["state"], "iss": ISSUER})

    def wait(self, timeout):
        return self._answer

    def close(self):
        pass


def connect(home, world, browser=None):
    browser = browser or FakeBrowser(world)
    return oauth.connect(
        home, "notes", CFG, open_browser=browser, say=lambda s: None, transport=world.transport(), receiver=browser
    ), browser


def test_connect_signs_in_and_the_server_is_then_used_with_its_token(home):
    world = World()
    done, browser = connect(home, world)
    assert done.issuer == ISSUER and done.refreshes
    p = browser.params
    assert p["code_challenge_method"] == "S256" and p["resource"] == MCP and p["client_id"] == "invented-client-1"
    assert p["scope"] == "notes offline_access"  # from the challenge, plus renewal where offered
    assert world.registered["application_type"] == "native" and world.registered["token_endpoint_auth_method"] == "none"
    assert world.token_forms[0]["resource"] == MCP  # sent to the token endpoint as well
    path = oauth.store_path(home, "notes")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(home.oauth_dir).st_mode) == 0o700
    assert json.loads(path.read_text())["issuer"] == ISSUER
    with McpClient(home, ServerConfig(name="notes", url=MCP), transport=world.transport()) as c:
        assert [t.name for t in c.list_tools()] == ["add_page"]
    mcp_calls = [h for m, u, h in world.seen if u == MCP]
    assert mcp_calls[-1]["authorization"] == "Bearer access-token-0001-invented"
    assert oauth.status(home, "notes") == "signed in (renews itself)"


@pytest.mark.parametrize(
    "tamper, msg",
    [
        (lambda a: {**a, "iss": "https://elsewhere.invalid"}, "different service"),
        (lambda a: {k: v for k, v in a.items() if k != "iss"}, "did not say which service"),
        (lambda a: {**a, "state": "forged"}, "did not match"),
        (lambda a: {"error": "access_denied", "state": a["state"], "iss": ISSUER}, "access_denied"),
    ],
)
def test_a_bad_sign_in_answer_is_refused_and_nothing_is_kept(home, tamper, msg):
    world = World()
    with pytest.raises(oauth.SignInError, match=msg):
        connect(home, world, FakeBrowser(world, tamper))
    assert oauth.load(home, "notes") is None


@pytest.mark.parametrize(
    "world, msg",
    [
        (World(metadata_issuer="https://attacker.invalid"), "claim to be from"),
        (World(s256=False), "S256"),
        (World(registration=False), "client_id"),
    ],
)
def test_an_unsafe_or_unusable_sign_in_service_is_refused(home, world, msg):
    with pytest.raises(oauth.SignInError, match=msg):
        connect(home, world)


def test_a_client_id_in_tools_md_skips_registration(home):
    world = World(registration=False)
    browser = FakeBrowser(world)
    oauth.connect(
        home,
        "notes",
        {**CFG, "client_id": "given-client"},
        open_browser=browser,
        say=lambda s: None,
        transport=world.transport(),
        receiver=browser,
    )
    assert browser.params["client_id"] == "given-client"


def test_an_expired_token_renews_itself_and_the_new_one_is_kept(home):
    world = World()
    connect(home, world)
    data = oauth.load(home, "notes")
    data["tokens"]["expires_at"] = time.time() - 10
    oauth.save(home, "notes", data)
    with McpClient(home, ServerConfig(name="notes", url=MCP), transport=world.transport()) as c:
        c.list_tools()
    kept = oauth.load(home, "notes")["tokens"]
    assert (
        kept["access_token"] == "access-token-0002-invented" and kept["refresh_token"] == "refresh-token-0002-invented"
    )
    assert world.token_forms[-1]["grant_type"] == "refresh_token" and world.token_forms[-1]["resource"] == MCP


def test_a_refused_token_is_renewed_once_and_the_request_sent_again(home):
    world = World()
    connect(home, world)
    world.valid_access.clear()  # the service withdrew it early
    with McpClient(home, ServerConfig(name="notes", url=MCP), transport=world.transport()) as c:
        assert [t.name for t in c.list_tools()] == ["add_page"]


def test_a_lapsed_sign_in_means_not_reached_and_never_a_browser(home):
    world = World()
    connect(home, world)
    world.valid_access.clear()
    world.fail_refresh = True
    with pytest.raises(McpNotReached, match="ombro connect notes"):
        McpClient(home, ServerConfig(name="notes", url=MCP), transport=world.transport()).start()


def test_a_server_never_connected_says_how_to_connect(home):
    with pytest.raises(McpNotReached, match="needs sign-in: run `ombro connect notes`"):
        McpClient(home, ServerConfig(name="notes", url=MCP), transport=World().transport()).start()


def test_a_fixed_authorization_header_is_used_instead_of_a_sign_in(home):
    world = World()
    connect(home, world)
    assert oauth.session_for(home, "notes", {**CFG, "headers": {"Authorization": "Bearer x"}}) is None
    assert oauth.session_for(home, "notes", CFG) is not None


def test_disconnect_revokes_and_forgets(home):
    world = World()
    connect(home, world)
    assert oauth.disconnect(home, "notes", transport=world.transport())
    assert world.revoked == ["refresh_token", "access_token"]
    assert oauth.load(home, "notes") is None and oauth.status(home, "notes") == "not signed in"
    assert not oauth.disconnect(home, "notes")


def test_tokens_are_removed_from_text_shown_to_a_model(home):
    world = World()
    connect(home, world)
    text, n = keys.scrub(home, "token access-token-0001-invented and refresh-token-0001-invented")
    assert n == 2 and "invented" not in text


def test_only_https_or_a_local_address_is_accepted(home):
    with pytest.raises(oauth.SignInError, match="https"):
        oauth.connect(home, "notes", {"url": "http://mcp.invalid/mcp"}, open_browser=lambda u: None)


def test_challenges_and_addresses_are_read_correctly():
    ch = oauth.parse_www_authenticate('Bearer realm="OAuth", resource_metadata="https://x.invalid/a", scope="a b"')
    assert ch == {"realm": "OAuth", "resource_metadata": "https://x.invalid/a", "scope": "a b"}
    assert oauth.parse_www_authenticate("Bearer error=insufficient_scope")["error"] == "insufficient_scope"
    assert oauth.canonical("HTTPS://MCP.Example.invalid/mcp/") == "https://mcp.example.invalid/mcp"


# -- edge cases ------------------------------------------------------------------


class _Changed(World):
    """A World whose answers for one address are replaced."""

    def __init__(self, url, answer, **kw):
        super().__init__(**kw)
        self._url, self._answer = url, answer

    def handle(self, request):
        if str(request.url).split("?")[0] == self._url:
            return self._answer(super().handle(request))
        return super().handle(request)


def _json_without(key):
    def change(resp):
        data = resp.json()
        data.pop(key, None)
        return httpx.Response(resp.status_code, json=data)

    return change


def _json_with(**extra):
    def change(resp):
        return httpx.Response(resp.status_code, json={**resp.json(), **extra})

    return change


@pytest.mark.parametrize(
    "world, msg",
    [
        (
            _Changed("https://mcp.invalid/.well-known/oauth-protected-resource/mcp", _json_without("resource")),
            "no `resource`",
        ),
        (
            _Changed(
                f"{ISSUER}/.well-known/oauth-authorization-server",
                _json_with(revocation_endpoint="http://auth.invalid/revoke"),
            ),
            "revocation_endpoint",
        ),
        (_Changed(f"{ISSUER}/register", lambda r: httpx.Response(201, text="<html>registered</html>")), "not JSON"),
    ],
)
def test_unchecked_or_unsafe_sign_in_details_are_refused(home, world, msg):
    with pytest.raises(oauth.SignInError, match=msg):
        connect(home, world)
    assert oauth.load(home, "notes") is None


def test_disconnect_never_sends_tokens_to_a_plain_http_address(home):
    world = World()
    connect(home, world)
    data = oauth.load(home, "notes")
    oauth.save(home, "notes", {**data, "revocation_endpoint": "http://auth.invalid/revoke"})
    sent = []
    assert oauth.disconnect(
        home, "notes", transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    )
    assert sent == [] and oauth.load(home, "notes") is None


def test_the_callback_ignores_an_answer_for_another_sign_in(monkeypatch):
    import _socket
    import socket

    rec = oauth.Receiver()
    try:
        rec.expect("the-right-state")
        port = int(rec.redirect_uri.rsplit(":", 1)[1].split("/")[0])

        def get(query):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _socket.socket.connect(s, ("127.0.0.1", port))  # this machine only
            s.sendall(f"GET /callback?{query} HTTP/1.0\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(4096):
                data += chunk
            s.close()
            return data

        assert b" 400 " in get("code=from-elsewhere&state=wrong").split(b"\r\n", 1)[0] + b" "
        with pytest.raises(oauth.SignInError, match="no answer"):
            rec.wait(0.2)  # still waiting: the wrong answer did not end the sign-in
        page = get("code=invented-code&state=the-right-state")
        assert rec.wait(2) == {"code": "invented-code", "state": "the-right-state"}
        assert b"Sign-in successful." in page and b"return to the terminal." in page
    finally:
        rec.close()


def test_the_signed_in_page_says_where_to_go_back_to():
    from ombro import signin_page

    app = signin_page.success_page()
    assert "Sign-in successful." in app and "You can close this tab and return to the Ombro app." in app
    assert "Geist" in app and 'aria-label="Ombro"' in app
    assert "return to the terminal." in signin_page.success_page("the terminal")


def test_the_apps_signed_in_page_is_ombros():
    # The app's listener (gui/src-tauri/src/signin.rs) includes this file; it is written by
    # `python -m ombro.signin_page` and must match.
    copy = Path(__file__).resolve().parents[1] / "gui/src-tauri/src/signin_page.html"
    from ombro import signin_page

    assert copy.read_text(encoding="utf-8") == signin_page.success_page()


# -- signing in when the browser is on another computer (Ombro on a server) ------------------


def browse(world, url: str) -> str:
    """The user signing in, in a browser elsewhere: the address it is sent back to."""
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    world.codes["invented-code-9"] = {
        "code_challenge": params["code_challenge"],
        "redirect_uri": params["redirect_uri"],
    }
    answer = urllib.parse.urlencode({"code": "invented-code-9", "state": params["state"], "iss": ISSUER})
    return f"{params['redirect_uri']}?{answer}"


def test_the_answer_can_be_handed_over_from_another_computer(home):
    world = World()
    receiver = oauth.HandOver(5555)

    def elsewhere(url):
        back = browse(world, url)
        assert back.startswith("http://127.0.0.1:5555/callback?")
        assert not receiver.give({"code": "x", "state": "not-this-one"})  # a stray answer is refused
        assert receiver.give(oauth.answer_from(back))

    done = oauth.connect(
        home, "notes", CFG, open_browser=elsewhere, say=lambda s: None, transport=world.transport(), receiver=receiver
    )
    assert done.issuer == ISSUER and world.registered["redirect_uris"] == ["http://127.0.0.1:5555/callback"]
    assert oauth.store_path(home, "notes").is_file()


def test_an_answer_is_read_from_a_pasted_address():
    assert oauth.answer_from(" http://127.0.0.1:9/callback?code=a%2Bb&state=s1 ") == {"code": "a+b", "state": "s1"}
    assert oauth.answer_from("?code=c&state=s2") == {"code": "c", "state": "s2"}
    assert oauth.answer_from("no answer here") == {}
    assert oauth.HandOver().redirect_uri.startswith("http://127.0.0.1:")


def test_ombro_connect_over_ssh_asks_for_the_address(home, monkeypatch, capsys):
    from ombro import cli

    from ombro import mcp_servers

    world = World()
    mcp_servers.add_server(home, "notes", mcp_servers.server_entry(url=MCP))
    real = oauth.connect
    monkeypatch.setattr(oauth, "connect", lambda *a, **k: real(*a, transport=world.transport(), **k))
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.1 50000 192.0.2.1 22")
    shown = {}

    def paste(prompt):
        url = capsys.readouterr().out.split("sign in to notes:")[1].split()[0]
        shown["url"] = url
        return browse(world, url)

    monkeypatch.setattr("builtins.input", paste)
    assert cli.main(["connect", "notes"]) == 0
    out = capsys.readouterr().out
    assert "Connected notes" in out and shown["url"].startswith(f"{ISSUER}/authorize?")
