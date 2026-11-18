"""Signing in with ChatGPT (chatgpt.py) and model calls on the plan (responses_client.py), against a
fake OpenAI: sign-in, token and model-list answers in the documented shapes, ID tokens signed with
a key made here. No network."""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
import urllib.parse

import httpx
import pytest

from ombro import chatgpt, keys, mdfile, models
from ombro.client import ModelError, make_client
from ombro.oauth import HandOver, SignInError, SignInNeeded
from ombro.responses_client import ChatGPTClient, read_stream, to_namespace, to_responses
from ombro.settings import ModelSpec, load_models

ACCESS = "at-INVENTED-0123456789"
REFRESH = "rt-INVENTED-0123456789"
CLIENT = "oaiapp_INVENTED"


# -- a signing key, made here ------------------------------------------------------


def _prime(bits: int, rng: random.Random) -> int:
    def probably(n: int) -> bool:
        if n % 2 == 0:
            return False
        d, r = n - 1, 0
        while d % 2 == 0:
            d, r = d // 2, r + 1
        for _ in range(20):
            x = pow(rng.randrange(2, n - 1), d, n)
            if x in (1, n - 1):
                continue
            for _ in range(r - 1):
                x = pow(x, 2, n)
                if x == n - 1:
                    break
            else:
                return False
        return True

    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if probably(n):
            return n


RNG = random.Random(7)
P, Q = _prime(512, RNG), _prime(512, RNG)
N, E = P * Q, 65537
D = pow(E, -1, (P - 1) * (Q - 1))


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def int_b64(n: int) -> str:
    return b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


JWKS = {"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "k1", "n": int_b64(N), "e": int_b64(E)}]}


def sign(claims: dict, kid: str = "k1", d: int = D) -> str:
    head = b64(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    k = (N.bit_length() + 7) // 8
    digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{head}.{body}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - 3 - len(digest)) + b"\x00" + digest
    sig = pow(int.from_bytes(em, "big"), d, N).to_bytes(k, "big")
    return f"{head}.{body}.{b64(sig)}"


def id_token(nonce: str, *, aud: str = CLIENT, sub: str = "user-INVENTED", **over) -> str:
    claims = {"iss": chatgpt.ISSUER, "aud": aud, "sub": sub, "email": "ada@invented.invalid", "nonce": nonce,
              "exp": time.time() + 3600, "iat": time.time()}
    claims.update(over)
    return sign(claims)


# -- a fake OpenAI -----------------------------------------------------------------


class FakeOpenAI:
    def __init__(self, scope: str = chatgpt.SCOPES, sub: str = "user-INVENTED"):
        self.scope, self.sub = scope, sub
        self.nonce = ""
        self.forms: list[dict] = []
        self.refresh_error: str | None = None
        self.revoked: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == chatgpt.JWKS_URL:
            return httpx.Response(200, json=JWKS)
        if url == chatgpt.DISCOVERY_URL:
            return httpx.Response(200, json={"revocation_endpoint": chatgpt.REVOKE_URL})
        if url == chatgpt.REVOKE_URL:
            self.revoked.append(dict(urllib.parse.parse_qsl(request.content.decode())))
            return httpx.Response(200)
        if url == chatgpt.TOKEN_URL:
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.forms.append(form)
            if form["grant_type"] == "refresh_token":
                if self.refresh_error:
                    return httpx.Response(400, json={"error": self.refresh_error})
                return httpx.Response(200, json={"access_token": ACCESS + "-2", "refresh_token": REFRESH + "-2",
                                                 "token_type": "Bearer", "expires_in": 3600, "scope": self.scope})
            return httpx.Response(200, json={
                "access_token": ACCESS, "refresh_token": REFRESH, "id_token": id_token(self.nonce, sub=self.sub),
                "token_type": "Bearer", "expires_in": 3600, "scope": self.scope, "earliest_refresh_at": 0})
        if url == chatgpt.RESOURCE + "/models":
            return httpx.Response(200, json={"models": [
                {"slug": "gpt-invented", "display_name": "GPT Invented", "visibility": "list"},
                {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide"},
                {"slug": "gpt-invented-mini", "display_name": "Mini", "visibility": "list"}]})
        return httpx.Response(404)


def browser(fake: FakeOpenAI, receiver: HandOver, answer: dict | None = None, seen: list | None = None):
    """Stands in for the browser: reads the sign-in address and gives the callback's answer."""
    def open_browser(url: str) -> None:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if seen is not None:
            seen.append(q)
        fake.nonce = q["nonce"]
        got = {"code": "code-INVENTED", "state": q["state"], "scope": q["scope"]}
        if q["client_id"] == chatgpt.NEW_CLIENT:
            got["client_id"] = CLIENT
        if answer is not None:
            got = {**answer, "state": q["state"]}
        receiver.give(got)
    return open_browser


def sign_in(home, fake: FakeOpenAI, seen=None, answer=None):
    rec = HandOver(1455, path=chatgpt.CALLBACK_PATH)
    return chatgpt.sign_in(home, open_browser=browser(fake, rec, answer, seen), say=lambda _t: None, receiver=rec,
                           transport=httpx.MockTransport(fake.handler), timeout=5)


# -- signing in ------------------------------------------------------------------


def test_first_sign_in_registers_ombro_and_adds_the_plans_models(home):
    _empty_models(home)
    fake, seen = FakeOpenAI(), []
    done = sign_in(home, fake, seen)
    q = seen[0]
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "Ombro"
    assert q["ext_agent_host_id"].startswith("urn:uuid:") and q["ext_agent_host_id"] == chatgpt.host_id(home)
    assert q["redirect_uri"] == "http://127.0.0.1:1455/auth/callback"
    assert q["scope"] == "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    assert q["resource"] == "https://api.openai.com/v1" and q["code_challenge_method"] == "S256"
    assert q["response_type"] == "code" and q["nonce"] and q["state"]
    exchange = fake.forms[0]
    assert exchange["client_id"] == CLIENT and exchange["code_verifier"] and "client_secret" not in exchange
    assert exchange["redirect_uri"] == q["redirect_uri"] and exchange["resource"] == chatgpt.RESOURCE
    assert hashlib.sha256(exchange["code_verifier"].encode()).digest() == base64.urlsafe_b64decode(
        q["code_challenge"] + "=")
    assert done == {"email": "ada@invented.invalid", "plan": True, "models": ["gpt-invented-plan",
                    "gpt-invented-mini-plan"], "welcome": chatgpt.WELCOME}
    saved = chatgpt.load(home)
    assert saved["client_id"] == CLIENT and saved["subject"] == "user-INVENTED" and saved["refresh_token"] == REFRESH
    assert (chatgpt.store_path(home).stat().st_mode & 0o777) == 0o600
    entries = mdfile.read(home.models)[0]["models"]
    assert entries["gpt-invented-plan"] == {"api": "chatgpt", "model": "gpt-invented"}
    assert entries["default"] == "gpt-invented-plan"  # there was none
    assert load_models(home)["gpt-invented-plan"].api == "chatgpt"
    assert chatgpt.status(home) == {"state": "signed in", "email": "ada@invented.invalid"}


def test_signing_in_again_reuses_the_registration_and_host(home):
    _empty_models(home)
    fake = FakeOpenAI()
    sign_in(home, fake)
    chatgpt.mark_welcomed(home)
    host = chatgpt.host_id(home)
    assert chatgpt.sign_out(home, httpx.MockTransport(fake.handler)) == {"signed_out": True, "revoked": True}
    assert fake.revoked[0] == {"token": REFRESH, "token_type_hint": "refresh_token", "client_id": CLIENT}
    assert chatgpt.status(home)["state"] == "not signed in" and chatgpt.load(home)["client_id"] == CLIENT
    seen: list = []
    done = sign_in(home, fake, seen)
    q = seen[0]
    assert q["client_id"] == CLIENT and "agent_name_hint" not in q and q["ext_agent_host_id"] == host
    assert q["login_hint"] == "ada@invented.invalid" and q["id_token_hint"]
    assert done["welcome"] is None  # said once


def test_a_declined_plan_or_refusal_keeps_no_tokens_to_use(home):
    _empty_models(home)
    with pytest.raises(SignInError, match="didn't allow"):
        sign_in(home, FakeOpenAI(), answer={"error": "access_denied"})
    assert chatgpt.load(home) is None
    done = sign_in(home, FakeOpenAI(scope="openid profile email offline_access"))
    assert done["plan"] is False and done["models"] == []
    assert chatgpt.status(home)["state"] == "plan not allowed"
    with pytest.raises(SignInNeeded, match="doesn't allow"):
        chatgpt.Session(home).token()


def test_an_identity_that_isnt_openais_or_isnt_for_this_sign_in_is_refused():
    nonce = "n-1"
    good = id_token(nonce)
    assert chatgpt.verify_id_token(good, client_id=CLIENT, nonce=nonce, jwks=JWKS)["sub"] == "user-INVENTED"
    with pytest.raises(SignInError, match="this sign-in"):
        chatgpt.verify_id_token(good, client_id=CLIENT, nonce="other", jwks=JWKS)
    with pytest.raises(SignInError, match="different app"):
        chatgpt.verify_id_token(id_token(nonce, aud="oaiapp_OTHER"), client_id=CLIENT, nonce=nonce, jwks=JWKS)
    with pytest.raises(SignInError, match="expired"):
        chatgpt.verify_id_token(id_token(nonce, exp=time.time() - 600), client_id=CLIENT, nonce=nonce, jwks=JWKS)
    with pytest.raises(SignInError, match="different service"):
        chatgpt.verify_id_token(id_token(nonce, iss="https://evil.invalid"), client_id=CLIENT, nonce=nonce, jwks=JWKS)
    head, body, sig = good.split(".")
    forged_body = b64(json.dumps({**json.loads(base64.urlsafe_b64decode(body + "==")), "sub": "someone"}).encode())
    with pytest.raises(SignInError, match="signature"):
        chatgpt.verify_id_token(f"{head}.{forged_body}.{sig}", client_id=CLIENT, nonce=nonce, jwks=JWKS)
    with pytest.raises(SignInError, match="signature"):
        chatgpt.verify_id_token(good, client_id=CLIENT, nonce=nonce, jwks={"keys": []})


def test_a_different_account_on_the_same_registration_is_refused(home):
    _empty_models(home)
    sign_in(home, FakeOpenAI())
    with pytest.raises(SignInError, match="different ChatGPT account"):
        sign_in(home, FakeOpenAI(sub="someone-else"))
    assert chatgpt.load(home)["subject"] == "user-INVENTED"


def test_tokens_renew_themselves_and_an_ended_sign_in_says_so(home):
    _empty_models(home)
    fake = FakeOpenAI()
    sign_in(home, fake)
    data = chatgpt.load(home)
    data["expires_at"] = time.time() - 1
    chatgpt.save(home, data)
    session = chatgpt.Session(home, httpx.MockTransport(fake.handler))
    assert session.token() == ACCESS + "-2"
    form = fake.forms[-1]
    assert form == {"grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": REFRESH,
                    "resource": chatgpt.RESOURCE}
    assert chatgpt.load(home)["refresh_token"] == REFRESH + "-2"  # rotated, kept
    fake.refresh_error = "invalid_grant"
    with pytest.raises(SignInNeeded, match="ended"):
        session.refresh()
    assert "refresh_token" not in chatgpt.load(home) and chatgpt.load(home)["client_id"] == CLIENT


def test_tokens_never_reach_what_a_model_sees(home):
    _empty_models(home)
    sign_in(home, FakeOpenAI())
    text, n = keys.scrub(home, f"leaked {ACCESS} and {REFRESH}")
    assert ACCESS not in text and REFRESH not in text and n == 2


# -- the plan's models ----------------------------------------------------------------


def _empty_models(home):
    mdfile.write(home.models, {"models": {}})


def test_plan_models_sit_beside_api_key_models_and_go_together(home):
    mdfile.write(home.models, {"models": {
        "default": "gpt-invented",
        "gpt-invented": {"api": "openai", "endpoint": "https://api.openai.com/v1", "model": "gpt-invented",
                         "key": "OPENAI_API_KEY"}}})
    assert models.add_plan_models(home, ["gpt-invented"]) == ["gpt-invented-plan"]
    entries = mdfile.read(home.models)[0]["models"]
    assert entries["default"] == "gpt-invented"  # left as it was
    labels = {c["label"]: c for c in models.connections(home)}
    assert set(labels) == {"OpenAI", "ChatGPT plan"} and labels["ChatGPT plan"]["endpoint"] is None
    assert models.remove_provider(home, models.PLAN) == ["gpt-invented-plan"]
    assert "gpt-invented" in mdfile.read(home.models)[0]["models"]  # the API key's model stays
    assert any(p["label"] == "OpenAI API (key)" for p in models.PRESETS)


def test_the_default_plan_model_cant_be_removed_first(home):
    _empty_models(home)
    models.add_plan_models(home, ["gpt-invented"])
    assert "default model" in models.check_remove_provider(home, models.PLAN)["blocked"]


def test_a_plan_model_gets_the_plan_client(home):
    spec = ModelSpec(name="gpt-invented-plan", api="chatgpt", model="gpt-invented")
    assert isinstance(make_client(spec, home), ChatGPTClient)


# -- model calls on the plan -------------------------------------------------------------


def sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def completed(output, usage=(100, 20, 60, 5)):
    return {"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "output": output, "usage": {
        "input_tokens": usage[0], "output_tokens": usage[1], "input_tokens_details": {"cached_tokens": usage[2]},
        "output_tokens_details": {"reasoning_tokens": usage[3]}}}}


class FixedSession:
    def __init__(self):
        self.refreshed = 0

    def token(self):
        return ACCESS

    def refresh(self, stale=None):
        self.refreshed += 1
        return ACCESS + "-2"


TOOL = {"type": "function", "function": {"name": "brief", "description": "Brief a role.",
                                          "parameters": {"type": "object", "properties": {"task": {"type": "string"}}}}}


def test_a_plan_call_streams_with_store_off_and_no_output_limit():
    seen = []

    def handler(request):
        seen.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.output_text.delta", "delta": "Hel"},
            completed([{"id": "rs_1", "type": "reasoning", "summary": []},
                       {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "Hello"}]},
                       {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "brief",
                        "arguments": "{\"task\": \"x\"}", "status": "completed"}])))

    c = ChatGPTClient("gpt-invented", FixedSession(), transport=httpx.MockTransport(handler), effort="low")
    reply = c.complete([{"role": "system", "content": "You are the Lead."}, {"role": "user", "content": "hi"}],
                       tools=[TOOL], max_output_tokens=900, timeout=5)
    auth, body = seen[0]
    assert auth == f"Bearer {ACCESS}"
    assert body["store"] is False and body["stream"] is True and body["model"] == "gpt-invented"
    assert body["instructions"] == "You are the Lead." and body["input"] == [{"role": "user", "content": "hi"}]
    for refused in ("max_output_tokens", "temperature", "metadata", "previous_response_id", "user", "truncation"):
        assert refused not in body
    assert body["tools"][0]["type"] == "namespace" and body["tools"][0]["tools"][0]["name"] == "brief"
    assert body["reasoning"] == {"effort": "low"}
    assert reply.content == "Hello" and reply.finish_reason == "tool_calls"
    assert (reply.tool_calls[0].id, reply.tool_calls[0].arguments) == ("call_1", {"task": "x"})
    u = reply.usage
    assert (u.input_tokens, u.output_tokens, u.cached_input, u.reasoning) == (100, 20, 60, 5)
    # The reply goes back as it came (no item ids: nothing is stored), the reasoning left out.
    msg = reply.as_message()
    assert [i["type"] for i in msg["_items"]] == ["message", "function_call"] and "id" not in msg["_items"][1]
    _, items = to_responses([{"role": "user", "content": "hi"}, msg,
                             {"role": "tool", "tool_call_id": "call_1", "content": "done"}])
    assert items[2]["call_id"] == "call_1" and items[3] == {"type": "function_call_output", "call_id": "call_1",
                                                             "output": "done"}


def test_conversion_without_kept_items():
    instructions, items = to_responses([
        {"role": "system", "content": "S"}, {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "brief", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": ""}, {"role": "system", "content": "later"}])
    assert instructions == "S"
    assert items == [{"role": "user", "content": "go"},
                     {"type": "function_call", "call_id": "c1", "name": "brief", "arguments": "{}"},
                     {"type": "function_call_output", "call_id": "c1", "output": "(no output)"},
                     {"role": "developer", "content": "later"}]
    assert to_namespace([TOOL])["tools"][0]["strict"] is False


def test_a_usage_limit_stops_and_says_where_to_manage_it():
    def handler(request):
        return httpx.Response(429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded",
                                                   "message": "limit"}})

    tries = []
    c = ChatGPTClient("m", FixedSession(), transport=httpx.MockTransport(lambda r: (tries.append(1), handler(r))[1]),
                      sleep=lambda s: None)
    with pytest.raises(ModelError, match="chatgpt.com/settings/usage"):
        c.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5)
    assert len(tries) == 1  # never tried again, never another way

    def mid_stream(request):
        return httpx.Response(200, content=sse({"type": "response.failed", "response": {
            "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}))

    c = ChatGPTClient("m", FixedSession(), transport=httpx.MockTransport(mid_stream), sleep=lambda s: None)
    with pytest.raises(ModelError, match="usage limit"):
        c.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5)


def test_an_expired_token_is_renewed_once():
    session = FixedSession()
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, content=sse(completed([{"type": "message", "content": [
            {"type": "output_text", "text": "ok"}]}])))

    c = ChatGPTClient("m", session, transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5).content == "ok"
    assert seen == [f"Bearer {ACCESS}", f"Bearer {ACCESS}-2"] and session.refreshed == 1


def test_a_stream_that_stops_early_is_not_a_reply():
    with pytest.raises(ModelError, match="stopped before it finished"):
        read_stream(iter(["event: response.created", 'data: {"type": "response.created"}', ""]))
    reply = read_stream(iter([f"data: {json.dumps({'type': 'response.incomplete', 'response': {'output': [], 'incomplete_details': {'reason': 'max_output_tokens'}}})}", ""]))
    assert reply.finish_reason == "length"


def test_the_plans_model_list_keeps_only_those_meant_to_be_listed():
    fake = FakeOpenAI()
    assert chatgpt.list_models(ACCESS, httpx.MockTransport(fake.handler)) == ["gpt-invented", "gpt-invented-mini"]


# -- the app -------------------------------------------------------------------------


def test_the_app_signs_in_through_serve_and_hands_the_answer_over(home, monkeypatch):
    from starlette.testclient import TestClient

    from ombro import views
    from ombro.serve.app import build_app

    _empty_models(home)
    fake = FakeOpenAI()
    real = chatgpt.sign_in
    monkeypatch.setattr(chatgpt, "sign_in", lambda h, **kw: real(h, **kw, transport=httpx.MockTransport(fake.handler)))
    views.CHATGPT_SIGNING.clear()
    token = "t0ken-for-tests"
    c = TestClient(build_app(token, False), base_url="http://127.0.0.1")
    auth = {"Authorization": f"Bearer {token}", "Origin": "tauri://localhost"}
    assert c.get("/models", headers=auth).json()["chatgpt"]["state"] == "not signed in"
    r = c.post("/models/chatgpt/sign-in", json={"hand_over": True, "redirect_port": 51234}, headers=auth).json()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r["url"]).query))
    assert r["state"] == "waiting" and q["redirect_uri"] == "http://127.0.0.1:51234/auth/callback"
    fake.nonce = q["nonce"]
    back = f"http://127.0.0.1:51234/auth/callback?code=c&state={q['state']}&client_id={CLIENT}"
    assert c.post("/models/chatgpt/sign-in/answer", json={"url": back}, headers=auth).json() == {"state": "answered"}
    for _ in range(100):
        view = c.get("/models", headers=auth).json()
        if not view["chatgpt"]["signing_in"]:
            break
        time.sleep(0.05)
    assert view["chatgpt"]["state"] == "signed in" and view["chatgpt"]["welcome"]
    assert any(conn["label"] == "ChatGPT plan" for conn in view["connections"])
    view = c.post("/models/chatgpt/welcomed", json={}, headers=auth).json()
    assert view["chatgpt"]["welcome"] is None and chatgpt.load(home)["welcomed"] is True
    monkeypatch.setattr(chatgpt, "sign_out", lambda h, transport=None: {"signed_out": True, "revoked": True})
    out = c.delete("/models/chatgpt/sign-in", headers=auth).json()
    assert out["signed_out"] is True
