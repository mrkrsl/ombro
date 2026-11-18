"""Signing in with ChatGPT, so Ombro's model calls use the person's ChatGPT plan instead of an
API key, as OpenAI documents it for open-source apps ("Sign in with ChatGPT", ChatGPT plan usage):
https://developers.openai.com/siwc/token-sharing-open-source

How it goes, once, in the browser:

1. This host gets an `ext_agent_host_id` (a urn:uuid, kept for good in secrets/chatgpt-host-id).
2. The browser opens OpenAI's sign-in with PKCE, `state` and `nonce`, asking for the identity
   scopes and the plan-usage ones. The first time, the client is `dynamic_agent_client` and the
   answer names the client OpenAI registered for this person ("oaiapp_…"); later sign-ins reuse it.
3. The answer comes back to http://127.0.0.1:<port>/auth/callback: Ombro's own listener, or the
   desktop app's when Ombro runs on a server (it hands the answer over).
4. The code is swapped for tokens, the ID token's signature and claims are checked, and the
   granted scopes must include `chatgpt.tokens.use.direct` before any model call.

The sign-in is kept in secrets/chatgpt.json (owner-only) and refreshed as needed; a job never
opens a browser. Signing out revokes the refresh token and forgets the tokens, but keeps the
registration and the host ID for the next sign-in. Tokens are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import uuid
from typing import Any, Callable

import httpx

from ombro import mdfile
from ombro.home import Home
from ombro.oauth import (CONNECT_TIMEOUT, HTTP_TIMEOUT, REFRESH_MARGIN, Receiver, SignInError, SignInNeeded,
                         _lock, _pkce)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
REVOKE_URL = ISSUER + "/api/accounts/oauth/revoke"  # as discovery gives it; discovery is asked first
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
NEW_CLIENT = "dynamic_agent_client"
AGENT_NAME = "Ombro"
CALLBACK_PATH = "/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"
LABEL = "ChatGPT plan"

AGAIN = "Sign in with ChatGPT again: Settings › General, or `ombro chatgpt sign-in`."
UNUSABLE_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                    "refresh_token_invalidated", "refresh_token_reused"}


# -- what is kept ----------------------------------------------------------------


def store_path(home: Home):
    return home.secrets / "chatgpt.json"


def host_id(home: Home) -> str:
    """This host's ext_agent_host_id: made once, kept for good (signing out keeps it)."""
    path = home.secrets / "chatgpt-host-id"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value.startswith("urn:uuid:"):
            return value
    except FileNotFoundError:
        pass
    home.ensure_secrets()
    value = f"urn:uuid:{uuid.uuid4()}"
    mdfile.write_atomic(path, value + "\n", mode=0o600)
    return value


def load(home: Home) -> dict[str, Any] | None:
    try:
        data = json.loads(store_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(home: Home, data: dict[str, Any]) -> None:
    home.ensure_secrets()
    mdfile.write_atomic(store_path(home), json.dumps(data, indent=2) + "\n", mode=0o600)


def plan_enabled(data: dict[str, Any] | None) -> bool:
    return bool(data and data.get("access_token") and PLAN_SCOPE in (data.get("scopes") or []))


def status(home: Home) -> dict[str, Any]:
    """{"state": "signed in" | "plan not allowed" | "not signed in", "email": … or None}."""
    data = load(home)
    if not data or not data.get("access_token"):
        return {"state": "not signed in", "email": (data or {}).get("email")}
    return {"state": "signed in" if plan_enabled(data) else "plan not allowed", "email": data.get("email")}


def secret_values(home: Home) -> set[str]:
    """The tokens held, so they can be removed from anything shown to a model."""
    data = load(home) or {}
    return {v for k in ("access_token", "refresh_token", "id_token") if isinstance(v := data.get(k), str) and len(v) >= 8}


# -- the ID token ------------------------------------------------------------------


def _b64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # DigestInfo for SHA-256


def rsa_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 (RS256): only the public key is used, so this is a check, not a secret."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or k < len(_SHA256_PREFIX) + 32 + 11:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    digest = _SHA256_PREFIX + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - 3 - len(digest)) + b"\x00" + digest
    return hmac.compare_digest(em, expected)


def verify_id_token(token: str, *, client_id: str, nonce: str, jwks: dict[str, Any],
                    now: float | None = None) -> dict[str, Any]:
    """The ID token's claims, once its signature (OpenAI's keys), issuer, audience, expiry and
    nonce are right. Raises SignInError otherwise."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(sig_b64)
    except (ValueError, TypeError):
        raise SignInError("OpenAI's sign-in sent an identity Ombro could not read") from None
    if header.get("alg") != "RS256":
        raise SignInError("OpenAI's sign-in sent an identity signed in a way Ombro doesn't accept")
    keys = [k for k in (jwks.get("keys") or []) if isinstance(k, dict) and k.get("kty") == "RSA"
            and (not header.get("kid") or k.get("kid") == header["kid"])]
    message = f"{head_b64}.{body_b64}".encode("ascii")
    if not any(rsa_verify(int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big"), message, signature)
               for k in keys if k.get("n") and k.get("e")):
        raise SignInError("the identity from OpenAI's sign-in didn't carry OpenAI's signature; it was ignored")
    now = time.time() if now is None else now
    aud = claims.get("aud")
    if claims.get("iss") != ISSUER:
        raise SignInError("the identity came from a different service than OpenAI's; it was ignored")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise SignInError("the identity was meant for a different app; it was ignored")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
        raise SignInError("the identity from OpenAI's sign-in had already expired; try again")
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise SignInError("the identity didn't belong to this sign-in; it was ignored")
    if not claims.get("sub"):
        raise SignInError("the identity from OpenAI's sign-in named no account")
    return claims


# -- signing in --------------------------------------------------------------------


def _oauth_error(r: httpx.Response) -> tuple[str, str]:
    try:
        d = r.json()
    except ValueError:
        return "", r.text[:200]
    err = d.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")[:200]
    return str(err or ""), str(d.get("error_description") or d.get("detail") or "")[:200]


def _tokens(client: httpx.Client, form: dict[str, str]) -> dict[str, Any]:
    try:
        r = client.post(TOKEN_URL, data=form, headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        raise SignInError(f"OpenAI's sign-in could not be reached: {type(e).__name__}") from None
    if r.status_code != 200:
        code, text = _oauth_error(r)
        err = SignInError(f"OpenAI's sign-in refused the request (HTTP {r.status_code}"
                          + (f", {code}" if code else "") + ")" + (f": {text}" if text else ""))
        err.code = code  # type: ignore[attr-defined]
        raise err
    try:
        d = r.json()
    except ValueError:
        raise SignInError("OpenAI's sign-in sent an answer Ombro could not read") from None
    if not d.get("access_token") or str(d.get("token_type", "bearer")).lower() != "bearer":
        raise SignInError("OpenAI's sign-in gave no usable token")
    return d


def _jwks(client: httpx.Client) -> dict[str, Any]:
    try:
        r = client.get(JWKS_URL, timeout=HTTP_TIMEOUT)
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


def authorize_url(params: dict[str, str]) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def connect(home: Home, *, open_browser: Callable[[str], Any], say: Callable[[str], None] = print,
            receiver: Any = None, transport: httpx.BaseTransport | None = None,
            timeout: float = CONNECT_TIMEOUT, back_to: str = "the terminal") -> dict[str, Any]:
    """Sign in with ChatGPT and keep the result. Returns the kept record (no tokens shown)."""
    saved = load(home) or {}
    issued = saved.get("client_id") if saved.get("client_id") not in (None, "", NEW_CLIENT) else None
    host = host_id(home)
    verifier, challenge = _pkce()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    rec = receiver or Receiver(back_to, path=CALLBACK_PATH)
    try:
        if hasattr(rec, "expect"):
            rec.expect(state)
        params = {"client_id": issued or NEW_CLIENT, "ext_agent_host_id": host, "response_type": "code",
                  "redirect_uri": rec.redirect_uri, "scope": SCOPES, "resource": RESOURCE, "state": state,
                  "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
        if issued:
            if saved.get("id_token"):
                params["id_token_hint"] = saved["id_token"]
            if saved.get("email"):
                params["login_hint"] = saved["email"]
        else:
            params["agent_name_hint"] = AGENT_NAME
        url = authorize_url(params)
        # The address can carry the ID token as a hint, so it's opened but never printed.
        say("Opening your browser to sign in with ChatGPT.")
        open_browser(url)
        answer = rec.wait(timeout)
    finally:
        rec.close()

    if answer.get("state") != state:
        raise SignInError("the sign-in answer did not match this request; it was ignored")
    if answer.get("error") == "access_denied":
        raise SignInError("you didn't allow Ombro to use your ChatGPT plan, so nothing was saved")
    if answer.get("error"):
        raise SignInError(f"the sign-in was not completed: {answer['error']}")
    if not answer.get("code"):
        raise SignInError("the sign-in answer had no code")
    client_id = answer.get("client_id")
    if issued:
        if client_id and client_id != issued:
            raise SignInError("the sign-in answer was for a different registration of Ombro; it was ignored")
        client_id = issued
    elif not client_id or client_id == NEW_CLIENT:
        raise SignInError("OpenAI didn't finish registering Ombro for your account; try again")

    with httpx.Client(transport=transport, follow_redirects=False) as client:
        form = {"grant_type": "authorization_code", "client_id": client_id, "code": answer["code"],
                "code_verifier": verifier, "redirect_uri": rec.redirect_uri, "resource": RESOURCE}
        try:
            tok = _tokens(client, form)
        except SignInError as e:
            if getattr(e, "code", "") == "invalid_grant":
                raise SignInError("that sign-in had already been used; sign in again") from None
            raise
        if not tok.get("id_token"):
            raise SignInError("OpenAI's sign-in sent no identity")
        claims = verify_id_token(tok["id_token"], client_id=client_id, nonce=nonce, jwks=_jwks(client))
    if issued and saved.get("subject") and claims["sub"] != saved["subject"]:
        raise SignInError("you signed in to a different ChatGPT account from the one Ombro was registered "
                          "with; sign out in Ombro first to use another account")
    scopes = sorted(str(tok.get("scope") or answer.get("scope") or "").replace("+", " ").split())
    now = time.time()
    record = {
        "email": claims.get("email") or saved.get("email"), "issuer": ISSUER, "subject": claims["sub"],
        "client_id": client_id, "ext_agent_host_id": host, "id_token": tok["id_token"],
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"), "token_type": "Bearer",
        "expires_in": tok.get("expires_in"), "expires_at": now + float(tok.get("expires_in") or 3600),
        "scopes": scopes, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "welcomed": bool(saved.get("welcomed")) and saved.get("subject") == claims["sub"],
    }
    save(home, record)
    return {k: record[k] for k in ("email", "scopes", "welcomed")} | {"plan": plan_enabled(record)}


def mark_welcomed(home: Home) -> None:
    """The first-sign-in confirmation ("You're using your ChatGPT plan") has been shown."""
    data = load(home)
    if data is not None and not data.get("welcomed"):
        data["welcomed"] = True
        save(home, data)


# -- using it ------------------------------------------------------------------------


class Session:
    """The ChatGPT sign-in as a model call uses it. Never opens a browser: when it can't renew
    itself it raises SignInNeeded."""

    def __init__(self, home: Home, transport: httpx.BaseTransport | None = None):
        self.home = home
        self._transport = transport

    def _data(self) -> dict[str, Any]:
        data = load(self.home)
        if not data or not data.get("access_token"):
            raise SignInNeeded(f"Ombro isn't signed in with ChatGPT. {AGAIN}")
        if not plan_enabled(data):
            raise SignInNeeded("Your ChatGPT sign-in doesn't allow Ombro to use your ChatGPT plan. "
                               f"{AGAIN} and allow it, or use an OpenAI API key instead.")
        return data

    def token(self) -> str:
        data = self._data()
        if float(data.get("expires_at") or 0) - REFRESH_MARGIN < time.time():
            return self.refresh(stale=data["access_token"])
        return data["access_token"]

    def refresh(self, stale: str | None = None) -> str:
        """A fresh access token, one refresh at a time (the refresh token rotates)."""
        with _lock(self.home, "chatgpt-plan"):
            data = self._data()
            if stale is not None and data["access_token"] != stale and \
                    float(data.get("expires_at") or 0) - REFRESH_MARGIN >= time.time():
                return data["access_token"]  # another process already renewed it
            if not data.get("refresh_token"):
                raise SignInNeeded(f"Your ChatGPT sign-in has expired. {AGAIN}")
            form = {"grant_type": "refresh_token", "client_id": data["client_id"],
                    "refresh_token": data["refresh_token"], "resource": RESOURCE}
            with httpx.Client(transport=self._transport, follow_redirects=False) as client:
                try:
                    tok = _tokens(client, form)
                except SignInError as e:
                    if getattr(e, "code", "") in UNUSABLE_REFRESH:
                        for k in ("access_token", "refresh_token"):
                            data.pop(k, None)
                        save(self.home, data)
                        raise SignInNeeded(f"Your ChatGPT sign-in has ended. {AGAIN}") from None
                    raise SignInNeeded(f"Your ChatGPT sign-in could not be renewed ({e}).") from None
            now = time.time()
            data.update({"access_token": tok["access_token"],
                         "refresh_token": tok.get("refresh_token") or data["refresh_token"],
                         "expires_in": tok.get("expires_in"), "expires_at": now + float(tok.get("expires_in") or 3600),
                         "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))})
            if tok.get("scope"):
                data["scopes"] = sorted(str(tok["scope"]).split())
            if tok.get("id_token"):
                data["id_token"] = tok["id_token"]
            save(self.home, data)
            return data["access_token"]


def list_models(token: str, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> list[str]:
    """The models this ChatGPT account may use, in OpenAI's order (those meant to be listed)."""
    from ombro.client import ModelError

    try:
        with httpx.Client(transport=transport, timeout=httpx.Timeout(timeout)) as http:
            r = http.get(RESOURCE + "/models", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise ModelError(f"could not reach api.openai.com: {type(e).__name__}") from None
    if r.status_code != 200:
        raise ModelError(f"api.openai.com returned HTTP {r.status_code} for the model list", status=r.status_code)
    try:
        d = r.json()
        rows = d.get("models") if isinstance(d.get("models"), list) else d.get("data")
        out = []
        for m in rows:
            if isinstance(m, dict) and m.get("visibility", "list") == "list":
                slug = m.get("slug") or m.get("id")
                if slug and slug not in out:
                    out.append(str(slug))
    except (ValueError, AttributeError, TypeError):
        raise ModelError("api.openai.com sent a model list Ombro could not read") from None
    if not out:
        raise ModelError("your ChatGPT account listed no models")
    return out


def sign_out(home: Home, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Revoke the renewable session and forget the tokens. The registration and host ID stay, for
    the next sign-in. Returns {"signed_out": bool, "revoked": bool}."""
    data = load(home)
    if not data or not (data.get("access_token") or data.get("refresh_token")):
        return {"signed_out": False, "revoked": False}
    revoked = not data.get("refresh_token")
    if data.get("refresh_token"):
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            endpoint = REVOKE_URL
            try:
                found = client.get(DISCOVERY_URL, timeout=HTTP_TIMEOUT).json().get("revocation_endpoint")
                if isinstance(found, str) and found.startswith(ISSUER + "/"):
                    endpoint = found
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            for attempt in range(3):
                try:
                    r = client.post(endpoint, timeout=HTTP_TIMEOUT, data={
                        "token": data["refresh_token"], "token_type_hint": "refresh_token",
                        "client_id": data.get("client_id", "")})
                    if r.status_code == 200:
                        revoked = True
                        break
                    if r.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5 * (attempt + 1))
    for k in ("access_token", "refresh_token", "expires_at", "expires_in", "scopes"):
        data.pop(k, None)
    save(home, data)
    return {"signed_out": True, "revoked": revoked}


WELCOME = ("You're using your ChatGPT plan. Ombro's model calls on the ChatGPT plan models now use the usage "
           f"included in your plan. You can see and limit it in ChatGPT settings: {USAGE_URL}")


def sign_in(home: Home, *, open_browser: Callable[[str], Any], say: Callable[[str], None] = print,
            receiver: Any = None, transport: httpx.BaseTransport | None = None,
            timeout: float = CONNECT_TIMEOUT, back_to: str = "the terminal") -> dict[str, Any]:
    """Sign in, then add the plan's models to models.md (as name-plan, api: chatgpt).
    Returns {"email", "plan", "models", "welcome"}: welcome is the first-time confirmation to show,
    or None once it has been shown."""
    from ombro.models import add_plan_models

    done = connect(home, open_browser=open_browser, say=say, receiver=receiver, transport=transport,
                   timeout=timeout, back_to=back_to)
    if not done["plan"]:
        return {"email": done["email"], "plan": False, "models": [], "welcome": None}
    slugs = list_models(Session(home, transport).token(), transport)
    names = add_plan_models(home, slugs)
    welcome = None if done["welcomed"] else WELCOME
    return {"email": done["email"], "plan": True, "models": names, "welcome": welcome}


NOT_ALLOWED = ("You signed in, but didn't allow Ombro to use your ChatGPT plan, so it can't make model calls "
               "with it. Sign in again and allow it, or use an OpenAI API key instead.")
