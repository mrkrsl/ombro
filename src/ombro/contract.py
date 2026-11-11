"""The result contract. No role returns free text."""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "deliverables", "open_questions", "did_not_do", "requests"],
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "deliverables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "path"],
                "properties": {"name": {"type": "string", "minLength": 1}, "path": {"type": "string"}},
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "did_not_do": {"type": "array", "items": {"type": "string"}},
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["helper", "task"],
                "properties": {"helper": {"type": "string"}, "task": {"type": "string", "minLength": 1}},
            },
        },
        "journal_note": {"type": "string"},
    },
}

_validator = Draft202012Validator(RESULT_SCHEMA)

EXAMPLE = """\
{
  "summary": "one paragraph on what was done",
  "deliverables": [{"name": "string", "path": "workspace-relative path or inline content"}],
  "open_questions": ["string"],
  "did_not_do": ["string"],
  "requests": [{"helper": "role name or empty", "task": "what is wanted and why"}],
  "journal_note": "optional one line"
}"""

_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _extract(text: str) -> Any:
    text = (text or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found")


def validate_text(text: str | None) -> tuple[dict | None, list[str]]:
    """Return (result, []) if the text is a valid result, else (None, errors)."""
    try:
        data = _extract(text or "")
    except ValueError as e:
        return None, [f"the reply was not a JSON object ({e})"]
    errors = []
    for err in sorted(_validator.iter_errors(data), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "the result"
        errors.append(f"{where}: {err.message}")
    if errors:
        return None, errors[:10]
    return data, []


def retry_instruction(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors)
    return (
        "Your reply was rejected because it is not a valid result:\n"
        f"{listed}\n\n"
        "Reply again with ONLY one JSON object: no prose, no Markdown, no code fence. "
        "It must have exactly these keys, all present even when empty, and no others "
        "(journal_note may be left out):\n"
        f"{EXAMPLE}\n"
        "This is your last attempt for this brief."
    )
