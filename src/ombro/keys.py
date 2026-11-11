"""Secrets in .env. Read at the moment of use, never cached, never logged."""

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
    try:
        return _parse(home.env.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def get_key(home: Home, name: str) -> str | None:
    """The current value of a key: .env first, then the process environment."""
    value = read_env(home).get(name) or os.environ.get(name)
    return value or None


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def set_key(home: Home, name: str, value: str) -> None:
    if not valid_name(name):
        raise ValueError(f"invalid key name {name!r}: use capitals, digits and underscores")
    if any(c in value for c in "\r\n"):
        raise ValueError("a key cannot contain line breaks")
    lines = home.env.read_text(encoding="utf-8").splitlines() if home.env.exists() else []
    kept = [ln for ln in lines if not re.match(rf"^\s*(export\s+)?{re.escape(name)}\s*=", ln)]
    kept.append(f"{name}={value}")
    mdfile.write_atomic(home.env, "\n".join(kept) + "\n", mode=0o600)


def mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-4:]}"
