"""Secrets in secrets/keys.env. Read at the moment of use, never cached, never logged.

Every secret lives in the home's owner-only secrets/ folder: single keys in
keys.env, sign-ins for remote MCP servers in oauth/, and key files by name.
A .env left by an earlier version is moved into secrets/keys.env the first
time Ombro runs (`migrate`)."""

from __future__ import annotations

import os
import re

from ombro import mdfile
from ombro.home import Home

_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def read_env(home: Home) -> dict[str, str]:
    """The keys in secrets/keys.env (and, until it is moved, an old .env)."""
    out: dict[str, str] = {}
    for path in (home.legacy_env, home.env):  # the new file wins
        try:
            out.update(_parse(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
    return out


def migrate(home: Home) -> str | None:
    """Move an old .env into secrets/keys.env. Returns a line saying what was
    done, or None if there was nothing to move. Keys already in keys.env win."""
    old = home.legacy_env
    if not old.is_file():
        return None
    home.ensure_secrets()
    old_text = old.read_text(encoding="utf-8")
    if home.env.is_file() and home.env.read_text(encoding="utf-8").strip():
        new_text = home.env.read_text(encoding="utf-8")
        have = set(_parse(new_text))
        extra = [ln for ln in old_text.splitlines()
                 if "=" in ln and ln.strip() and not ln.strip().startswith("#")
                 and ln.strip().removeprefix("export ").split("=", 1)[0].strip() not in have]
        text = new_text.rstrip("\n") + "\n" + "".join(f"{ln}\n" for ln in extra)
    else:
        text = old_text if old_text.endswith("\n") or not old_text else old_text + "\n"
    mdfile.write_atomic(home.env, text, mode=0o600)
    old.unlink()
    n = len(_parse(old_text))
    return f"Moved {n} key(s) from {old} to {home.env}, where every secret now lives."


def get_key(home: Home, name: str) -> str | None:
    """The current value of a key: secrets/keys.env first, then the process environment."""
    value = read_env(home).get(name) or os.environ.get(name)
    return value or None


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def set_key(home: Home, name: str, value: str) -> None:
    if not valid_name(name):
        raise ValueError(f"invalid key name {name!r}: use capitals, digits and underscores")
    if any(c in value for c in "\r\n"):
        raise ValueError("a key cannot contain line breaks")
    migrate(home)
    home.ensure_secrets()
    lines = home.env.read_text(encoding="utf-8").splitlines() if home.env.exists() else []
    kept = [ln for ln in lines if not re.match(rf"^\s*(export\s+)?{re.escape(name)}\s*=", ln)]
    kept.append(f"{name}={value}")
    mdfile.write_atomic(home.env, "\n".join(kept) + "\n", mode=0o600)


def remove_key(home: Home, name: str) -> bool:
    """Remove a key from secrets/keys.env. Returns whether it was there."""
    migrate(home)
    if not home.env.exists():
        return False
    lines = home.env.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    kept = [ln for ln in lines if not pattern.match(ln)]
    if len(kept) == len(lines):
        return False
    mdfile.write_atomic(home.env, "\n".join(kept) + ("\n" if kept else ""), mode=0o600)
    return True


def key_uses(home: Home) -> dict[str, list[str]]:
    """Key names the settings files refer to, and what uses each."""
    from ombro.settings import PROVIDER_TOOLS, SetupError, load_models, load_tool_settings

    uses: dict[str, list[str]] = {}
    try:
        for spec in load_models(home).values():
            if spec.api == "openai" and spec.key:
                uses.setdefault(spec.key, []).append(f"model {spec.name}")
    except (SetupError, mdfile.FrontMatterError):
        pass
    try:
        tools = load_tool_settings(home)
    except (SetupError, mdfile.FrontMatterError):
        tools = None
    if tools is not None:
        for tool in PROVIDER_TOOLS:
            provider = tools.provider_for(tool)
            name = tools.key_name(provider)
            if name:
                uses.setdefault(name, []).append(f"{tool} ({provider})")
        firecrawl = tools.key_name("firecrawl")
        if firecrawl:
            uses.setdefault(firecrawl, [])
        from ombro.mcp import ServerConfig

        for server, cfg in tools.servers.items():
            for name in ServerConfig.from_dict(server, cfg).key_refs():
                uses.setdefault(name, []).append(f"MCP server {server}")
    return uses


def mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-4:]}"


# Shapes of common API keys and tokens, so one pasted into an interview is
# removed even when it is not in .env.
_SECRET_SHAPES = [
    re.compile(r"\b(?:sk|fc|pk|rk|sess)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
]
REMOVED = "[key removed]"


def scrub(home: Home, text: str) -> tuple[str, int]:
    """Remove every key value in secrets/keys.env (and the environment variables settings
    refer to), and anything shaped like a common API key. Returns (text, count)."""
    if not text:
        return text, 0
    from ombro import oauth

    values = {v for v in read_env(home).values() if len(v) >= 8} | oauth.secret_values(home)
    for name in key_uses(home):
        v = os.environ.get(name)
        if v and len(v) >= 8:
            values.add(v)
    count = 0
    for v in sorted(values, key=len, reverse=True):
        if v in text:
            count += text.count(v)
            text = text.replace(v, REMOVED)
    for pattern in _SECRET_SHAPES:
        text, n = pattern.subn(REMOVED, text)
        count += n
    return text, count
