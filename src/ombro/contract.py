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


SUBMIT_NAME = "submit_result"
SUBMIT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SUBMIT_NAME,
        "description": "Hand in your result for this brief. This ends the brief. Keep it short: put long content "
        "in a workspace file and give its path as a deliverable.",
        "parameters": RESULT_SCHEMA,
    },
}

LAST_CALL_INSTRUCTION = (
    "This is your last model call for this brief. In this one reply, save anything you have drafted but not yet "
    "written with workspace.write (mode append to add to a file), then call submit_result: give the path of every "
    "file you wrote, say in the summary how complete each one is, and put whatever is unfinished in did_not_do. "
    "Only workspace.write, workspace.place and submit_result are run now."
)

CUT_OFF_INSTRUCTION = (
    "Your last reply was cut off because it reached the output limit for one reply, so none of it was used "
    "(a tool call in it was not run). Write less in one reply: write a long file in parts (workspace.write with "
    "mode append), and keep your result short, with long content in a workspace file given by path. Then carry on."
)


def validate_value(data: Any) -> tuple[dict | None, list[str]]:
    """Return (result, []) if `data` (submit_result's arguments) is a valid result, else (None, errors)."""
    if not isinstance(data, dict):
        return None, ["the arguments were not a JSON object"]
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
