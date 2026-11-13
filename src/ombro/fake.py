"""A scripted fake model, so the harness can be exercised without spending money.

A script is a list of entries, consumed in order. Each entry is a dict:

    {"content": "text" or {...}}          a reply; a dict is sent as JSON text
    {"tool_calls": [{"name": "brief", "arguments": {...}}]}
    {"usage": {"input": 100, "output": 20}}   token counts to report
    {"times": 5}                          repeat this entry 5 times
    {"error": "message"}                  fail the call with a ModelError
    {"advance": 60}                       move the fake clock on (seconds)
    {"sleep": 1.5}                        really sleep (for process-level tests)

or a callable taking (messages, tools) and returning such a dict.
When the script runs out, calls fail with a ModelError.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable

from ombro.client import ModelError, ModelReply, ToolCall, Usage, estimate_tokens


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


_SHARED: dict[tuple[str, bytes], "ScriptedClient"] = {}

Entry = dict[str, Any] | Callable[[list, list | None], dict[str, Any]]


class ScriptedClient:
    def __init__(self, script: list[Entry], clock: FakeClock | None = None):
        self._queue: list[Entry] = []
        for entry in script:
            n = entry.get("times", 1) if isinstance(entry, dict) else 1
            self._queue.extend([entry] * int(n))
        self.clock = clock
        self.calls: list[dict[str, Any]] = []
        self.models: list[str] | None = None  # what list_models answers; None: the endpoint does not say

    def list_models(self, timeout: float = 20.0) -> list[str]:
        if not self.models:
            raise ModelError("this endpoint does not list its models")
        return sorted(self.models)

    @classmethod
    def from_file(cls, path: str, base: Path | None = None) -> "ScriptedClient":
        p = Path(path).expanduser()
        if not p.is_absolute() and base is not None:
            p = base / p
        try:
            script = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ModelError(f"could not read fake model script {p}: {e}") from None
        if not isinstance(script, list):
            raise ModelError(f"fake model script {p} must be a JSON list")
        return cls(script)

    @classmethod
    def shared(cls, path: str, base: Path | None = None) -> "ScriptedClient":
        """One client per script file per process, so an interview and the run it
        starts read one script in order. A script file that changes starts afresh."""
        p = Path(path).expanduser()
        if not p.is_absolute() and base is not None:
            p = base / p
        try:
            key = (str(p.resolve()), p.read_bytes())
        except OSError:
            return cls.from_file(path, base)
        if key not in _SHARED:
            _SHARED.clear()
            _SHARED[key] = cls.from_file(path, base)
        return _SHARED[key]

    @property
    def remaining(self) -> int:
        return len(self._queue)

    def complete(self, messages, *, tools=None, max_output_tokens, timeout) -> ModelReply:
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "max_output_tokens": max_output_tokens,
                "timeout": timeout,
            }
        )
        if not self._queue:
            raise ModelError("the fake model's script has run out")
        entry = self._queue.pop(0)
        if callable(entry):
            entry = entry(messages, tools)
        if entry.get("sleep"):
            time.sleep(float(entry["sleep"]))
        if entry.get("advance") and self.clock is not None:
            self.clock.advance(float(entry["advance"]))
        if "error" in entry:
            raise ModelError(str(entry["error"]), timed_out=bool(entry.get("timed_out")))

        content = entry.get("content")
        if content is not None and not isinstance(content, str):
            content = json.dumps(content)
        calls = []
        for i, tc in enumerate(entry.get("tool_calls") or []):
            args = tc.get("arguments", {})
            raw = args if isinstance(args, str) else json.dumps(args)
            parsed = None
            try:
                parsed = json.loads(raw)
            except ValueError:
                pass
            calls.append(
                ToolCall(
                    id=f"call_{len(self.calls)}_{i}",
                    name=tc["name"],
                    arguments=parsed if isinstance(parsed, dict) else None,
                    raw_arguments=raw,
                )
            )
        u = entry.get("usage") or {}
        out_default = (len(content or "") + sum(len(c.raw_arguments) for c in calls)) // 4 + 1
        usage = Usage(
            int(u.get("input", estimate_tokens(messages, tools))),
            min(int(u.get("output", out_default)), int(max_output_tokens)),
        )
        return ModelReply(content=content, tool_calls=calls, usage=usage, finish_reason="stop")


# Small helpers for writing scripts --------------------------------------------


def brief(role: str, task: str, **extra) -> dict:
    return {"tool_calls": [{"name": "brief", "arguments": {"role": role, "task": task}}], **extra}


def finish(summary: str = "Done.", outcome: str = "done", **extra) -> dict:
    return {"tool_calls": [{"name": "finish", "arguments": {"outcome": outcome, "summary": summary}}], **extra}


def call(name: str, arguments: dict | str | None = None, **extra) -> dict:
    return {"tool_calls": [{"name": name, "arguments": arguments if arguments is not None else {}}], **extra}


def result(summary: str = "Did the task.", **fields) -> dict:
    body = {
        "summary": summary,
        "deliverables": fields.pop("deliverables", []),
        "open_questions": fields.pop("open_questions", []),
        "did_not_do": fields.pop("did_not_do", []),
        "requests": fields.pop("requests", []),
    }
    extra = {k: fields.pop(k) for k in ("usage", "times", "advance", "sleep") if k in fields}
    body.update(fields)
    return {"content": body, **extra}


def say(text: str, **extra) -> dict:
    return {"content": text, **extra}


def keep_notes(reason: str = "Nothing new to keep.", **extra) -> dict:
    """The Lead's reply in the notes call at the end of a run that briefed a role."""
    return {"tool_calls": [{"name": "keep_notes", "arguments": {"reason": reason}}], **extra}


def write_notes(text: str, **extra) -> dict:
    return {"tool_calls": [{"name": "memory__write_notes", "arguments": {"from_runs": text}}], **extra}
