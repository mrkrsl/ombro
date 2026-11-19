"""Material: text a model reads that came from neither the user nor Ombro (web pages,
MCP results, file contents, script output, what roles return). It is fenced on its
way to a model, and the common signs of injected instructions are flagged for the
report. Flags never block anything."""

from __future__ import annotations

import re
import secrets
import unicodedata

OPEN = "⟪material"
CLOSE = "⟪end material"

# The common signs of text written to steer a model. Lower-case; matched on folded text.
SIGNS = [
    ("addressed to an AI", re.compile(r"\b(?:dear|attention|note to|hey|hello)\s+(?:ai|assistant|llm|language model|"
                                      r"agent|chatbot|claude|gpt)\b")),
    ("override attempt", re.compile(r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?"
                                    r"(?:previous|prior|above|earlier|system|original)?\s*"
                                    r"(?:instructions|rules|prompts?|directions)\b")),
    ("new instructions", re.compile(r"\b(?:new|updated|real|actual)\s+instructions\s*:")),
    ("role change", re.compile(r"\byou are now\b|\bact as (?:an?|the) (?:different|new|unrestricted)\b")),
    ("asks to reveal", re.compile(r"\b(?:reveal|print|show|output|send)\s+(?:your|the)\s+"
                                  r"(?:system prompt|instructions|api key|keys?|secrets?|password)")),
    ("asks to send or delete", re.compile(r"\b(?:email|send|forward|upload|post)\s+(?:this|the|all|these)\s+"
                                          r"(?:file|files|data|contents|document)s?\s+to\b|"
                                          r"\b(?:delete|remove|erase|wipe)\s+(?:all|every|the)\s+files?\b")),
]
HIDDEN = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def _neutral(text: str) -> str:
    """No text inside a fence can open or close one."""
    return text.replace("⟪", "(").replace("⟫", ")")


def fence(text: str, source: str, boundary: str | None = None) -> str:
    """Material wrapped for a model: a random boundary for this call, and where it came from."""
    b = boundary or secrets.token_hex(4)
    src = " ".join(_neutral(source).split())[:300]
    return f"{OPEN} {b} from {src}⟫\n{_neutral(text)}\n{CLOSE} {b}⟫"


def strings(value) -> str:
    """Every string in a tool's result, decoded, one per line: what flags() reads."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(strings(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(strings(v) for v in value)
    return ""


def flags(text) -> list[str]:
    """The signs of injected instructions found in some material, by name, each once."""
    text = strings(text)
    found = []
    if HIDDEN.search(text):
        found.append("hidden or zero-width characters")
    folded = unicodedata.normalize("NFKC", text).lower()
    for name, pattern in SIGNS:
        if pattern.search(folded):
            found.append(name)
    return found


def source(tool: str, args: dict | None) -> str:
    """Where a tool's result came from, for the fence and the report."""
    args = args if isinstance(args, dict) else {}
    for key in ("url", "path", "query", "role", "job_id"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return f"{tool} {v.strip()[:200]}"
    return tool


def unfence(content: str) -> str:
    """The text inside a fence, or the content as it is if it has none (for tests and tools)."""
    if content.startswith(OPEN) and content.rstrip().endswith("⟫"):
        body = content.split("\n", 1)[1] if "\n" in content else ""
        return body.rsplit("\n", 1)[0]
    return content
