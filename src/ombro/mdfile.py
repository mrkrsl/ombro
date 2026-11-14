"""Markdown files with YAML front matter: the format of every settings file."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml


class FrontMatterError(ValueError):
    pass


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split text into (front matter, body). Text without front matter has {}."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError as e:
                raise FrontMatterError(f"invalid front matter: {e}") from None
            if not isinstance(meta, dict):
                raise FrontMatterError("front matter must be a set of key: value lines")
            return meta, body.lstrip("\n")
    raise FrontMatterError("front matter is not closed with ---")


def read(path: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(path)
    try:
        return parse(path.read_text(encoding="utf-8"))
    except FrontMatterError as e:
        raise FrontMatterError(f"{path}: {e}") from None


def dump(meta: dict[str, Any], body: str = "") -> str:
    if meta:
        fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
        out = f"---\n{fm}\n---\n"
    else:
        out = "---\n---\n"
    if body:
        out += "\n" + body.lstrip("\n")
    if not out.endswith("\n"):
        out += "\n"
    return out


def write_atomic(path: str | Path, text: str, mode: int | None = None) -> None:
    """Write via a temporary file and rename, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write(path: str | Path, meta: dict[str, Any], body: str = "", mode: int | None = None) -> None:
    write_atomic(path, dump(meta, body), mode)


def prompt_text(body: str) -> str:
    """Body text as it goes into a prompt: HTML comments (file guidance) removed."""
    return _COMMENT.sub("", body).strip()


def with_comments(old_body: str, text: str) -> str:
    """A new body for a file a person edits as plain text: the old body's guidance comments,
    which they were not shown, kept above what they wrote."""
    notes = [m.group(0).strip() for m in _COMMENT.finditer(old_body or "")]
    text = text.strip()
    return "\n\n".join([*notes, text]) + "\n" if text or notes else ""
