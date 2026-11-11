"""The outbox: outward actions staged during a run, waiting for the user.

Each action is one Markdown file in the run's outbox/, written by the harness:
the tool, its arguments, the role and its stated reason, the time, and a
status. The user may edit `arguments` by hand; the change is noticed (they
no longer match the fingerprint taken when they were staged) and the arguments are checked again before
anything runs.

Statuses: pending (waiting), executing (running now), executed, declined,
failed (the service refused it), uncertain (Ombro stopped, or lost contact,
while it was running: it may or may not have happened).

Decisions are made on named actions only. An action runs at most once:
`executing` is written before the call and the outcome after it, under a lock
on the run's outbox, so two approvals cannot overlap. An action left
`executing` by a process that died is uncertain and is never retried; the
user settles it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ombro import mdfile, runs
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.mcp import McpCallLost, McpError, McpNotReached
from ombro.settings import SetupError, ToolSettings, load_tool_settings

STATUSES = ("pending", "executing", "executed", "declined", "failed", "uncertain")
WAITING = ("pending", "uncertain")
RESULT_CHARS = 1_000
DECISIONS_HEADING = "## Outbox decisions"

BODY = """\
Ombro staged this action during run {run_id}. `status` above says where it
stands: pending means it has not happened and waits for you.

See it with `ombro outbox show {ref}`, then `ombro outbox approve {ref}` or
`ombro outbox decline {ref}`. You may change `arguments` above, by hand or
with `ombro outbox edit {ref}`; Ombro checks them again before anything runs.
Everything else in this file is written by Ombro.
"""


class OutboxError(Exception):
    """Shown to the user as it is."""


class OutboxBusy(OutboxError):
    pass


def fingerprint(args: Any) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def short_run(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


@dataclass
class Action:
    path: Path
    meta: dict[str, Any]

    @property
    def number(self) -> int:
        return int(self.meta.get("id") or 0)

    @property
    def run_id(self) -> str:
        return str(self.meta.get("run_id") or self.path.parent.parent.name)

    @property
    def ref(self) -> str:
        return f"{short_run(self.run_id)}-{self.number}"

    @property
    def long_ref(self) -> str:
        return f"{self.run_id}-{self.number}"

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "pending")

    @property
    def tool(self) -> str:
        return str(self.meta.get("tool") or "")

    @property
    def arguments(self) -> Any:
        return self.meta.get("arguments")

    @property
    def edited(self) -> bool:
        return fingerprint(self.arguments) != self.meta.get("arguments_sha256")

    @property
    def run_dir(self) -> Path:
        return self.path.parent.parent

    def summary(self, n: int = 90) -> str:
        args = self.arguments if isinstance(self.arguments, dict) else {}
        text = " ".join(f"{k}: {v}" for k, v in args.items()) or "(no arguments)"
        text = " ".join(text.split())
        return text if len(text) <= n else text[: n - 1] + "…"

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "long_ref": self.long_ref, "path": str(self.path), "edited": self.edited,
                **{k: v for k, v in self.meta.items() if k != "input_schema"}}


# ---------------------------------------------------------------------------
# Reading


def outbox_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "outbox"


def load(path: Path) -> Action:
    meta, _ = mdfile.read(path)
    return Action(Path(path), meta)


def actions(run_dir: Path) -> list[Action]:
    folder = outbox_dir(run_dir)
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("[0-9][0-9][0-9]-*.md")):
        try:
            out.append(load(f))
        except (OSError, mdfile.FrontMatterError):
            continue
    return sorted(out, key=lambda a: a.number)


def counts(run_dir: Path) -> dict[str, int]:
    c: dict[str, int] = {}
    for a in actions(run_dir):
        c[a.status] = c.get(a.status, 0) + 1
    return c


def waiting_count(run_dir: Path) -> int:
    c = counts(run_dir)
    return sum(c.get(s, 0) for s in (*WAITING, "executing"))


def describe_counts(c: dict[str, int]) -> str:
    order = ("pending", "uncertain", "executing", "executed", "failed", "declined")
    labels = {"pending": "waiting", "executed": "done"}
    parts = [f"{c[s]} {labels.get(s, s)}" for s in order if c.get(s)]
    return ", ".join(parts) or "nothing staged"


def waiting(home: Home, crew: str | None = None) -> list[Action]:
    """Every action waiting for a decision, oldest run first."""
    out = []
    for _, run_dir in runs.all_runs(home, crew):
        if not outbox_dir(run_dir).is_dir():
            continue
        settle(run_dir)
        out += [a for a in actions(run_dir) if a.status in (*WAITING, "executing")]
    return out


_REF = re.compile(r"^(?P<run>[0-9A-Za-z][0-9A-Za-z-]*?)-(?P<n>\d+)$")


def find(home: Home, ref: str) -> Action:
    """An action by its reference: `ab12-3`, or the run ID (or a unique part of it) and the number."""
    m = _REF.match(ref.strip())
    if not m:
        raise OutboxError(f"{ref!r} is not an action reference; they look like ab12-3 (see `ombro outbox`)")
    frag, n = m.group("run"), int(m.group("n"))
    matches = [d for _, d in runs.all_runs(home) if d.name == frag]
    if not matches:
        matches = [d for _, d in runs.all_runs(home) if d.name.endswith(frag) or d.name.startswith(frag)]
    if not matches:
        raise OutboxError(f"no run matches {frag!r}")
    found = [a for d in matches for a in actions(d) if a.number == n]
    if not found:
        raise OutboxError(f"no staged action {ref}")
    if len(found) > 1:
        raise OutboxError(f"{ref} matches more than one run; use the long form: "
                          + ", ".join(a.long_ref for a in found))
    return found[0]


# ---------------------------------------------------------------------------
# Staging (during a run)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "action"


def stage(run_dir: Path, *, tool: str, effect: str, arguments: dict, reason: str, role: str,
          input_schema: dict | None) -> tuple[Action, bool]:
    """Write one staged action. Returns (action, duplicate): an exact duplicate
    of an action already staged in this run is not staged again."""
    run_dir = Path(run_dir)
    run_id = run_dir.name
    existing = actions(run_dir)
    fp = fingerprint(arguments)
    for a in existing:
        if a.tool == tool and a.meta.get("arguments_sha256") == fp:
            return a, True
    n = max((a.number for a in existing), default=0) + 1
    path = outbox_dir(run_dir) / f"{n:03d}-{_slug(tool.removeprefix('mcp.'))}.md"
    meta = {
        "id": n,
        "run_id": run_id,
        "tool": tool,
        "class": effect,
        "role": role,
        "reason": reason,
        "staged_at": utc_now(),
        "status": "pending",
        "arguments": arguments,
        "arguments_sha256": fp,  # the arguments as staged; a difference means they were edited
        "input_schema": input_schema or {},
    }
    a = Action(path, meta)
    mdfile.write(path, meta, BODY.format(run_id=run_id, ref=a.ref))
    return a, False


# ---------------------------------------------------------------------------
# Deciding (after a run)


@contextmanager
def lock(run_dir: Path):
    """One outbox command at a time per run. Raises OutboxBusy if another holds it."""
    folder = outbox_dir(run_dir)
    folder.mkdir(exist_ok=True)
    import fcntl

    with open(folder / ".lock", "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OutboxBusy(f"another outbox command is working on run {Path(run_dir).name}; "
                             "wait for it to finish") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _save(a: Action, **fields: Any) -> None:
    a.meta.update(fields)
    _, body = mdfile.read(a.path)
    mdfile.write(a.path, a.meta, body)


def _settle_locked(run_dir: Path) -> list[Action]:
    """With the lock held: an action still `executing` was left by a process that
    died mid-call. It may or may not have happened, so it becomes uncertain."""
    out = []
    for a in actions(run_dir):
        if a.status == "executing":
            _save(a, status="uncertain", note="Ombro stopped while this action was running, so it may or may not "
                  "have happened. It is never retried; settle it with `ombro outbox resolve`.")
            _journal(run_dir, "outbox_uncertain", a, "Ombro stopped while it was running")
            out.append(a)
    if out:
        _record(run_dir)
    return out


def settle(run_dir: Path) -> None:
    """Settle orphaned `executing` actions if no outbox command is running."""
    if not any(a.status == "executing" for a in actions(run_dir)):
        return
    try:
        with lock(run_dir):
            _settle_locked(run_dir)
    except OutboxBusy:
        pass


def _journal(run_dir: Path, action: str, a: Action, note: str, **extra: Any) -> None:
    Journal(Path(run_dir) / "journal.jsonl").write("you", action, f"{a.ref} {a.tool}: {note}",
                                                   ref=a.ref, tool=a.tool, **extra)


def _record(run_dir: Path, a: Action | None = None, line: str | None = None) -> None:
    """Counts in run.md, and a line in the report's Outbox decisions section."""
    try:
        runs.update_run(run_dir, outbox=counts(run_dir))
    except (OSError, mdfile.FrontMatterError):
        pass
    report = Path(run_dir) / "report.md"
    if line and a and report.is_file():
        text = report.read_text(encoding="utf-8")
        if DECISIONS_HEADING not in text:
            text = text.rstrip() + f"\n\n{DECISIONS_HEADING}\n\nRecorded by Ombro as you decide.\n"
        text = text.rstrip() + f"\n- {utc_now()} {a.ref} `{a.tool}`: {line}\n"
        mdfile.write_atomic(report, text)


def _check_run(run_dir: Path) -> None:
    meta, _ = runs.read_run(run_dir)
    if meta.get("status") == "running" and not runs.is_dead(meta):
        raise OutboxError(f"run {Path(run_dir).name} is still running; decide once it has finished")


def _reload(a: Action) -> Action:
    return load(a.path)


def _refused_now(home: Home, tool: str) -> bool:
    try:
        return load_tool_settings(home).tools.get(tool, {}).get("permission") == "refuse"
    except (SetupError, mdfile.FrontMatterError):
        return True  # if tools.md cannot be read, the hard limit cannot be checked: refuse


def check_arguments(a: Action) -> str | None:
    from ombro.outward import validation_error

    return validation_error(a.meta.get("input_schema") or {}, a.arguments)


PoolFactory = Callable[[Home, Path], Any]


def _default_pool(home: Home, run_dir: Path):
    from ombro.outward import McpPool

    meta, _ = runs.read_run(run_dir)
    return McpPool(home, ToolSettings.from_dict(meta.get("tools"), "run.md tools"), log_dir=run_dir)


def approve(home: Home, a: Action, *, pool_factory: PoolFactory | None = None) -> Action:
    """Run one action, at most once. Returns it with its outcome recorded."""
    from ombro.outward import split_name

    run_dir = a.run_dir
    with lock(run_dir):
        _settle_locked(run_dir)
        a = _reload(a)
        _check_run(run_dir)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be approved"
                              + (" (settle it with `ombro outbox resolve`)" if a.status == "uncertain" else ""))
        if _refused_now(home, a.tool):
            raise OutboxError(f"{a.tool} is set to `permission: refuse` in tools.md, so it cannot run")
        problem = check_arguments(a)
        if problem:
            raise OutboxError(f"{a.ref} cannot run: {problem}. Fix it with `ombro outbox edit {a.ref}`")
        parts = split_name(a.tool)
        if parts is None:
            raise OutboxError(f"{a.ref} names {a.tool!r}, which is not an MCP tool")
        try:
            pool = (pool_factory or _default_pool)(home, run_dir)
        except SetupError as e:
            raise OutboxError(f"{a.ref} cannot run: the run's tool settings have a problem: {e}") from None
        _save(a, status="executing", executing_at=utc_now(), executing_pid=os.getpid())
        _journal(run_dir, "outbox_approved", a, "approved" + (" (edited)" if a.edited else ""),
                 edited=a.edited)
        sent: list = []
        try:
            result = pool.call(parts[0], parts[1], a.arguments, sent=sent)
        except McpNotReached as e:
            _save(a, status="pending", last_error=str(e), executing_at=None, executing_pid=None)
            _journal(run_dir, "outbox_not_reached", a, f"not sent: {e}")
            _record(run_dir)
            raise OutboxError(f"{a.ref} was not sent, because the server could not be reached: {e}. "
                              "It is still waiting; try again later") from None
        except McpCallLost as e:
            _save(a, status="uncertain", note=f"{e}; it may or may not have happened. It is never retried.",
                  decided_at=utc_now())
            _journal(run_dir, "outbox_uncertain", a, str(e))
            _record(run_dir, a, f"approved, but no answer came back ({e}): uncertain")
            return a
        except McpError as e:
            _save(a, status="failed", error=str(e)[:RESULT_CHARS], decided_at=utc_now())
            _journal(run_dir, "outbox_failed", a, str(e)[:300])
            _record(run_dir, a, f"approved; the service refused it: {str(e)[:200]}")
            return a
        finally:
            pool.close()
        from ombro.mcp import result_text

        text = result_text(result, RESULT_CHARS)
        if result.get("isError"):
            _save(a, status="failed", error=text or "the service reported an error", decided_at=utc_now())
            _journal(run_dir, "outbox_failed", a, (text or "error")[:300])
            _record(run_dir, a, f"approved; the service reported an error: {(text or 'no detail')[:200]}")
        else:
            _save(a, status="executed", executed_at=utc_now(), decided_at=utc_now(), result=text)
            _journal(run_dir, "outbox_executed", a, (text or "done")[:300])
            _record(run_dir, a, "approved" + (" after editing" if a.edited else "") + "; done")
        return a


def decline(home: Home, a: Action) -> Action:
    run_dir = a.run_dir
    with lock(run_dir):
        _settle_locked(run_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be declined")
        _save(a, status="declined", decided_at=utc_now())
        _journal(run_dir, "outbox_declined", a, "declined")
        _record(run_dir, a, "declined; it will never run")
        return a


def edit(home: Home, a: Action, arguments: Any) -> Action:
    """Replace an action's arguments after checking them. It stays waiting."""
    run_dir = a.run_dir
    with lock(run_dir):
        _settle_locked(run_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be edited")
        trial = Action(a.path, {**a.meta, "arguments": arguments})
        problem = check_arguments(trial)
        if problem:
            raise OutboxError(f"the edit was not saved: {problem}")
        _save(a, arguments=arguments, edited_at=utc_now())
        _journal(run_dir, "outbox_edited", a, "arguments edited; still waiting")
        _record(run_dir)
        return a


def resolve(home: Home, a: Action, sent: bool) -> Action:
    """Settle an uncertain action: it happened (executed) or it did not (back to waiting)."""
    run_dir = a.run_dir
    with lock(run_dir):
        _settle_locked(run_dir)
        a = _reload(a)
        if a.status != "uncertain":
            raise OutboxError(f"{a.ref} is {a.status}; only an uncertain action needs settling")
        if sent:
            _save(a, status="executed", resolved_at=utc_now(), result="marked as done by you")
            _journal(run_dir, "outbox_resolved", a, "you marked it as done")
            _record(run_dir, a, "you marked it as done")
        else:
            _save(a, status="pending", resolved_at=utc_now(), executing_at=None, executing_pid=None)
            _journal(run_dir, "outbox_resolved", a, "you marked it as not done; waiting again")
            _record(run_dir, a, "you marked it as not done; waiting again")
        return a
