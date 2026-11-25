"""Pinned chats: kept at the top of the list, in the app and the TUI.

Held in a file of their own (preferences/pinned.md), by each chat's id, not in the chat's own
front matter: an open chat rewrites its front matter whenever it saves, and its file is renamed
when it gets its title, so neither could keep a pin. A chat from before ids is pinned by its name.
"""

from __future__ import annotations

from typing import Any

from ombro import mdfile
from ombro.home import Home

NOTE = "Chats pinned to the top of the list, by id. Ombro keeps this file; pin and unpin in the app.\n"


def _path(home: Home):
    return home.root / "preferences" / "pinned.md"


def key(row: dict[str, Any]) -> str:
    """What a chat is pinned by: its id, or its name for a chat from before ids."""
    return str(row.get("id") or f"name:{row['name']}")


def pinned(home: Home) -> list[str]:
    p = _path(home)
    if not p.is_file():
        return []
    try:
        meta, _ = mdfile.read(p)
    except (OSError, mdfile.FrontMatterError):
        return []
    chats = meta.get("chats")
    return [str(c) for c in chats] if isinstance(chats, list) else []


def set_pinned(home: Home, chat_key: str, on: bool) -> None:
    keys = [k for k in pinned(home) if k != chat_key]
    if on:
        keys.insert(0, chat_key)
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    mdfile.write(p, {"chats": keys}, NOTE)
