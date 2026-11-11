"""The journal: one JSON object per line, written by the harness, never the model."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    def __init__(self, path: Path, echo: Callable[[dict], None] | None = None):
        self.path = Path(path)
        self.echo = echo

    def write(self, role: str, action: str, note: str = "", **extra: Any) -> dict:
        event = {"ts": utc_now(), "role": role, "action": action, "note": note, **extra}
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if self.echo:
            self.echo(event)
        return event


def read(path: Path) -> list[dict]:
    """Read events, skipping a torn last line if the process died mid-write."""
    events = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def format_event(ev: dict) -> str:
    ts = str(ev.get("ts", ""))
    clock = ts[11:19] if len(ts) >= 19 else ts
    note = ev.get("note") or ""
    return f"{clock}  {ev.get('role', ''):<18} {ev.get('action', ''):<16} {note}".rstrip()
