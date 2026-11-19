"""A scripted fake model, so the harness can be exercised without spending money.

A script is a list of entries, consumed in order. Each entry is a dict:

    {"content": "text" or {...}}          a reply; a dict is sent as JSON text
    {"tool_calls": [{"name": "brief", "arguments": {...}}]}
    {"usage": {"input": 100, "output": 20}}   token counts to report
    {"finish_reason": "length"}               a reply cut off at the output limit
    {"times": 5}                          repeat this entry 5 times
    {"error": "message"}                  fail the call with a ModelError
    {"advance": 60}                       move the fake clock on (seconds)
    {"sleep": 1.5}                        really sleep (for process-level tests)

or a callable taking (messages, tools) and returning such a dict.
When the script runs out, calls fail with a ModelError.

The Lead's optional skill call at the end of a job (it is offered
`skills__propose`) is answered `no_skill` without using an entry, unless the
next entry answers it itself (skills__propose, skills__read or no_skill).
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
        """One client per script file per process, so an interview and the job it
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
        if _skill_call(tools) and not _answers_skill_call(self._queue[0] if self._queue else None):
            # Counted as no tokens, so scripts written before skills keep their figures.
            entry: Entry = no_skill("The script does not answer the skill call.", usage={"input": 0, "output": 0})
        elif not self._queue:
            raise ModelError("the fake model's script has run out")
        else:
            # An entry with `needs` waits for the call it belongs to, so one script can serve a chat
            # and the job it starts, each taking its own replies.
            names = {(t.get("function") or {}).get("name") for t in tools or []}
            system = str((messages[0] or {}).get("content") or "") if messages else ""
            i = next((n for n, e in enumerate(self._queue) if _fits(e, names, system, messages)), None)
            if i is None:
                raise ModelError("the fake model's script has no reply for this call")
            entry = self._queue.pop(i)
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
        return ModelReply(content=content, tool_calls=calls, usage=usage,
                          finish_reason=entry.get("finish_reason", "stop"))


def _fits(entry, names: set, system: str = "", messages: list | None = None) -> bool:
    """`needs`: a tool the call offers, "no_tools", "prompt:<words>" found in its system prompt, or
    "text:<words>" found anywhere in what it sends (a job's spec, its report's record)."""
    need = entry.get("needs") if isinstance(entry, dict) else None
    if not need:
        return True
    if need.startswith("prompt:"):
        return need[len("prompt:"):] in system
    if need.startswith("text:"):
        return any(need[len("text:"):] in str(m.get("content") or "") for m in messages or [] if isinstance(m, dict))
    return (need == "no_tools" and not names) or need in names


def _skill_call(tools) -> bool:
    return any((t.get("function") or {}).get("name") == "skills__propose" for t in tools or [])


def _answers_skill_call(entry) -> bool:
    if callable(entry):
        return True
    names = {tc.get("name") for tc in (entry or {}).get("tool_calls") or []}
    return bool(names & {"skills__propose", "skills__read", "no_skill"})


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
    """The Lead's reply in the notes call at the end of a job that briefed a role."""
    return {"tool_calls": [{"name": "keep_notes", "arguments": {"reason": reason}}], **extra}


def write_notes(text: str, **extra) -> dict:
    return {"tool_calls": [{"name": "memory__write_notes", "arguments": {"from_jobs": text}}], **extra}


def no_skill(reason: str = "Nothing new to keep as a skill.", **extra) -> dict:
    return {"tool_calls": [{"name": "no_skill", "arguments": {"reason": reason}}], **extra}


def propose_skill(name: str, description: str, body: str, *, for_roles: list[str] | None = None,
                  reason: str = "It worked in this job.",
                  adds: str | None = "Later jobs of this kind follow it without their brief spelling it out.",
                  jobs: list[str] | None = None, **extra) -> dict:
    args = {"name": name, "description": description, "body": body, "reason": reason}
    if jobs is not None:
        args["jobs"] = jobs
    if adds is not None:
        args["adds"] = adds
    if for_roles is not None:
        args["for"] = for_roles
    return {"tool_calls": [{"name": "skills__propose", "arguments": args}], **extra}
