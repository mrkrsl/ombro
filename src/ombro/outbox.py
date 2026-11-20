"""The outbox: outward actions staged during a job, waiting for the user.

Each action is one Markdown file in the job's outbox/, written by the harness:
the tool, its arguments, the role and its stated reason, the time, and a
status. The user may edit `arguments` by hand; the change is noticed (they
no longer match the fingerprint taken when they were staged) and the arguments are checked again before
anything runs.

Statuses: pending (waiting), executing (running now), executed, declined,
failed (the service refused it), uncertain (Ombro stopped, or lost contact,
while it was running: it may or may not have happened).

Decisions are made on named actions only. An action runs at most once:
`executing` is written before the call and the outcome after it, under a lock
on the job's outbox, so two approvals cannot overlap. An action left
`executing` by a process that died is uncertain and is never retried; the
user settles it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ombro import mdfile, jobs
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.mcp import McpCallLost, McpError, McpNotReached
from ombro.settings import SetupError, ToolSettings, load_tool_settings

STATUSES = ("pending", "executing", "executed", "declined", "failed", "uncertain")
WAITING = ("pending", "uncertain")
RESULT_CHARS = 1_000
DECISIONS_HEADING = "## Approvals"

BODY = """\
Ombro staged this action during job {job_id}. `status` above says where it
stands: pending means it has not happened and waits for you.

See it with `ombro approvals show {ref}`, then `ombro approvals approve {ref}` or
`ombro approvals decline {ref}`. You may change `arguments` above, by hand or
with `ombro approvals edit {ref}`; Ombro checks them again before anything runs.
Everything else in this file is written by Ombro.
"""


class OutboxError(Exception):
    """Shown to the user as it is."""


class OutboxBusy(OutboxError):
    pass


def fingerprint(args: Any) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def short_job(job_id: str) -> str:
    return job_id.rsplit("-", 1)[-1]


@dataclass
class Action:
    path: Path
    meta: dict[str, Any]

    @property
    def number(self) -> int:
        return int(self.meta.get("id") or 0)

    @property
    def job_id(self) -> str:
        return str(self.meta.get("job_id") or self.path.parent.parent.name)

    @property
    def ref(self) -> str:
        return f"{short_job(self.job_id)}-{self.number}"

    @property
    def long_ref(self) -> str:
        return f"{self.job_id}-{self.number}"

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
        if fingerprint(self.arguments) != self.meta.get("arguments_sha256"):
            return True
        if self.tool == SKILL_TOOL:
            try:
                return _text_sha(skill_text(self)) != self.meta.get("file_sha256")
            except OutboxError:
                return True
        return False

    @property
    def job_dir(self) -> Path:
        return self.path.parent.parent

    def summary(self, n: int = 90) -> str:
        args = self.arguments if isinstance(self.arguments, dict) else {}
        text = " ".join(f"{k}: {v}" for k, v in args.items()) or "(no arguments)"
        text = " ".join(text.split())
        return text if len(text) <= n else text[: n - 1] + "…"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "long_ref": self.long_ref,
            "path": str(self.path),
            "edited": self.edited,
            **{k: v for k, v in self.meta.items() if k != "input_schema"},
        }


# ---------------------------------------------------------------------------
# Reading


def outbox_dir(job_dir: Path) -> Path:
    return Path(job_dir) / "outbox"


def load(path: Path) -> Action:
    meta, _ = mdfile.read(path)
    return Action(Path(path), meta)


def actions(job_dir: Path) -> list[Action]:
    folder = outbox_dir(job_dir)
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("[0-9][0-9][0-9]-*.md")):
        try:
            out.append(load(f))
        except (OSError, mdfile.FrontMatterError):
            continue
    return sorted(out, key=lambda a: a.number)


def counts(job_dir: Path) -> dict[str, int]:
    c: dict[str, int] = {}
    for a in actions(job_dir):
        c[a.status] = c.get(a.status, 0) + 1
    return c


def waiting_count(job_dir: Path) -> int:
    c = counts(job_dir)
    return sum(c.get(s, 0) for s in (*WAITING, "executing"))


def describe_counts(c: dict[str, int]) -> str:
    order = ("pending", "uncertain", "executing", "executed", "failed", "declined")
    labels = {"pending": "waiting", "executed": "done"}
    parts = [f"{c[s]} {labels.get(s, s)}" for s in order if c.get(s)]
    return ", ".join(parts) or "nothing waiting for approval"


def waiting(home: Home, crew: str | None = None) -> list[Action]:
    """Every action waiting for a decision, newest first: by when it was staged, then by job and number
    (staging times are to the second, so a job's later action still comes before its earlier one)."""
    out = []
    for i, job_dir in enumerate(jobs.all_jobs(home, crew)):
        if not outbox_dir(job_dir).is_dir():
            continue
        settle(job_dir)
        out += [
            (str(a.meta.get("staged_at") or ""), i, a.number, a)
            for a in actions(job_dir)
            if a.status in (*WAITING, "executing")
        ]
    out.sort(key=lambda t: t[:3], reverse=True)
    return [t[3] for t in out]


_REF = re.compile(r"^(?P<run>[0-9A-Za-z][0-9A-Za-z-]*?)-(?P<n>\d+)$")


def find(home: Home, ref: str) -> Action:
    """An action by its reference: `ab12-3`, or the job ID (or a unique part of it) and the number."""
    m = _REF.match(ref.strip())
    if not m:
        raise OutboxError(f"{ref!r} is not an action reference; they look like ab12-3 (see `ombro approvals`)")
    frag, n = m.group("run"), int(m.group("n"))
    matches = [d for d in jobs.all_jobs(home) if d.name == frag]
    if not matches:
        matches = [d for d in jobs.all_jobs(home) if d.name.endswith(frag) or d.name.startswith(frag)]
    if not matches:
        raise OutboxError(f"no job matches {frag!r}")
    found = [a for d in matches for a in actions(d) if a.number == n]
    if not found:
        raise OutboxError(f"no staged action {ref}")
    if len(found) > 1:
        raise OutboxError(
            f"{ref} matches more than one job; use the long form: " + ", ".join(a.long_ref for a in found)
        )
    return found[0]


# ---------------------------------------------------------------------------
# Staging (during a job)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "action"


# How long staging waits for a decision in progress (which may be running a tool call) to finish.
STAGE_WAIT = 120.0


def stage(
    job_dir: Path, *, tool: str, effect: str, arguments: dict, reason: str, role: str, input_schema: dict | None
) -> tuple[Action, bool]:
    """Write one staged action, under the outbox lock, since decisions can be made
    while the job is going. Returns (action, duplicate): an exact duplicate of an
    action already staged in this job is not staged again."""
    with lock(job_dir, wait=STAGE_WAIT):
        return _stage_locked(
            Path(job_dir),
            tool=tool,
            effect=effect,
            arguments=arguments,
            reason=reason,
            role=role,
            input_schema=input_schema,
        )


def _stage_locked(
    job_dir: Path, *, tool: str, effect: str, arguments: dict, reason: str, role: str, input_schema: dict | None
) -> tuple[Action, bool]:
    job_id = job_dir.name
    existing = actions(job_dir)
    fp = fingerprint(arguments)
    for a in existing:
        if a.tool == tool and a.meta.get("arguments_sha256") == fp:
            return a, True
    n = max((a.number for a in existing), default=0) + 1
    path = outbox_dir(job_dir) / f"{n:03d}-{_slug(tool.removeprefix('mcp.'))}.md"
    meta = {
        "id": n,
        "job_id": job_id,
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
    mdfile.write(path, meta, BODY.format(job_id=job_id, ref=a.ref))
    return a, False


PLACE_TOOL = "workspace.place"
# What may be edited in a staged placement: where it goes, and whether it may replace a file.
PLACE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["to"],
    "properties": {"to": {"type": "string", "minLength": 2, "maxLength": 1000}, "overwrite": {"type": "boolean"}},
}


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stage_placement(
    job_dir: Path, *, source: Path, shown_source: str, to: str, overwrite: bool, reason: str, role: str
) -> tuple[Action, bool]:
    """Stage copying a workspace file to a place not shared for writing. A copy of the
    file as it is now is kept with the action, so later changes in the workspace do
    not change what is approved."""
    import shutil

    job_dir = Path(job_dir)
    sha = _file_sha(source)
    with lock(job_dir, wait=STAGE_WAIT):
        for a in actions(job_dir):
            if (
                a.tool == PLACE_TOOL
                and a.meta.get("file_sha256") == sha
                and isinstance(a.arguments, dict)
                and a.arguments.get("to") == to
            ):
                return a, True
        arguments = {"to": to, "overwrite": overwrite}
        a, _ = _stage_locked(
            job_dir,
            tool=PLACE_TOOL,
            effect="internal",
            arguments=arguments,
            reason=reason,
            role=role,
            input_schema=PLACE_SCHEMA,
        )
        keep = a.path.with_suffix("") / source.name
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, keep)
        _save(a, source=shown_source, file=keep.relative_to(job_dir).as_posix(), file_sha256=sha)
    return a, False


def placement_check(home: Home, a: Action) -> tuple[Path, str | None]:
    """Where a staged placement would go, and a warning if it would replace a file.
    Raises OutboxError if it cannot go there."""
    from ombro import folders

    args = a.arguments if isinstance(a.arguments, dict) else {}
    to = str(args.get("to") or "")
    dest = folders.expand(to)
    if not dest.is_absolute() or ".." in dest.parts:
        raise OutboxError(f"{a.ref} cannot be placed at {to!r}: give a full path")
    try:
        parent = folders.check_place(dest.parent, home, must_exist=False)
    except folders.FolderError as e:
        raise OutboxError(f"{a.ref} cannot be placed there: {e}") from None
    final = parent / dest.name
    if final.is_symlink() or final.is_dir():
        raise OutboxError(f"{a.ref} cannot be placed at {to}: that is a folder or a symlink")
    warning = None
    if final.exists():
        if not args.get("overwrite"):
            raise OutboxError(
                f"{a.ref} would replace {to}, which already exists. To replace it, set overwrite "
                f"to true with `ombro approvals edit {a.ref}`; otherwise decline it"
            )
        warning = f"this replaces {to}, which already exists"
    return final, warning


def _approve_place(home: Home, a: Action) -> Action:
    from ombro import folders

    job_dir = a.job_dir
    final, warning = placement_check(home, a)
    kept = job_dir / str(a.meta.get("file") or "")
    own = a.path.with_suffix("")  # outbox/<action>/: where the copy was kept when it was staged
    if kept.is_symlink() or not kept.resolve().is_relative_to(own.resolve()) or kept.resolve() == own.resolve():
        raise OutboxError(f"{a.ref} cannot run: its `file` must be the copy kept in {own.relative_to(job_dir)}/")
    if not kept.is_file() or _file_sha(kept) != a.meta.get("file_sha256"):
        raise OutboxError(f"{a.ref} cannot run: the copy kept with it is missing or has changed")
    _save(a, status="executing", executing_at=utc_now(), executing_pid=os.getpid())
    _journal(job_dir, "outbox_approved", a, "approved" + (" (edited)" if a.edited else ""), edited=a.edited)
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        folders.safe_copy(kept, final)
    except OSError as e:
        _save(a, status="failed", error=str(e)[:RESULT_CHARS], decided_at=utc_now())
        _journal(job_dir, "outbox_failed", a, str(e)[:300])
        _record(job_dir, a, f"approved; the copy failed: {str(e)[:200]}")
        return a
    note = f"copied to {final}" + (f" ({warning})" if warning else "")
    _save(a, status="executed", executed_at=utc_now(), decided_at=utc_now(), result=note)
    _journal(job_dir, "outbox_executed", a, note)
    _record(job_dir, a, "approved" + (" after editing" if a.edited else "") + f"; {note}")
    return a


# ---------------------------------------------------------------------------
# Skills the Lead proposes at the end of a job. They wait here
# like any staged action, but reach nobody outside: approving one makes it live.

SKILL_TOOL = "skills.propose"
# What may be edited in a skill proposal's arguments: its name, and the roles it is for.
# The text itself is the SKILL.md kept beside the action.
SKILL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$", "maxLength": 64},
        "for": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
}


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kept_skill(a: Action) -> Path:
    """The proposed SKILL.md kept with the action, checked to be its own copy."""
    own = a.path.with_suffix("")
    kept = a.job_dir / str(a.meta.get("file") or "")
    if kept.is_symlink() or kept.resolve().parent != own.resolve() or kept.name != "SKILL.md":
        raise OutboxError(f"{a.ref}: its `file` must be the SKILL.md kept in {own.relative_to(a.job_dir)}/")
    return kept


def skill_text(a: Action) -> str:
    try:
        return _kept_skill(a).read_text(encoding="utf-8")
    except OSError as e:
        raise OutboxError(f"{a.ref}: the proposed SKILL.md could not be read: {e}") from None


def stage_skill(
    job_dir: Path,
    *,
    name: str,
    text: str,
    for_roles: list[str],
    reason: str,
    base_sha: str,
    flags: list[str],
    adds: str = "",
    jobs: list[str] | None = None,
) -> tuple[Action, bool]:
    """Stage a skill the Lead proposes, keeping its SKILL.md beside the action. `reason` says why it
    is suggested (what in the job showed it); `adds`, what it gives later jobs that a brief would not;
    `jobs`, the earlier jobs of the same kind it rests on."""
    job_dir = Path(job_dir)
    with lock(job_dir, wait=STAGE_WAIT):
        for a in actions(job_dir):
            if a.tool == SKILL_TOOL and a.meta.get("file_sha256") == _text_sha(text):
                return a, True
        a, _ = _stage_locked(
            job_dir,
            tool=SKILL_TOOL,
            effect="skill",
            arguments={"name": name, "for": for_roles},
            reason=reason,
            role="lead",
            input_schema=SKILL_SCHEMA,
        )
        keep = a.path.with_suffix("") / "SKILL.md"
        mdfile.write_atomic(keep, text)
        _save(
            a,
            file=keep.relative_to(job_dir).as_posix(),
            file_sha256=_text_sha(text),
            base_sha256=base_sha,
            change="update" if base_sha else "new",
            flags=list(flags),
            **({"adds": adds} if adds else {}),
            **({"jobs": list(jobs)} if jobs else {}),
        )
    return a, False


def skill_view(home: Home, a: Action) -> dict[str, Any]:
    """What the user needs to decide: the text, or the diff against today's version, and
    whether the live skill has changed since the proposal was made."""
    from ombro import skills

    args = a.arguments if isinstance(a.arguments, dict) else {}
    name = str(args.get("name") or "")
    text = skill_text(a)
    live = skills.load(home, name)
    now = live.text if live else ""
    out: dict[str, Any] = {
        "name": name,
        "for": list(args.get("for") or []),
        "text": text,
        "flags": list(a.meta.get("flags") or []),
        "live": bool(live),
        "why": str(a.meta.get("reason") or ""),
        "adds": str(a.meta.get("adds") or ""),
        "jobs": [str(j) for j in a.meta.get("jobs") or []],
    }
    if live:
        out["diff"] = skills.diff(now, text, name)
    out["changed_since"] = (skills.sha(now) if now else "") != str(a.meta.get("base_sha256") or "")
    return out


def _approve_skill(home: Home, a: Action) -> Action:
    from datetime import date

    from ombro import skills

    job_dir = a.job_dir
    args = a.arguments if isinstance(a.arguments, dict) else {}
    name = str(args.get("name") or "")
    text = skill_text(a)
    try:
        skills.check(text, name, max_chars=None)
    except skills.SkillError as e:
        raise OutboxError(f"{a.ref} cannot be approved: {e}. Fix it with `ombro approvals edit {a.ref}`") from None
    if skills.live_sha(home, name) != str(a.meta.get("base_sha256") or ""):
        raise OutboxError(
            f"{a.ref}: the skill {name} has changed since this was proposed. See the diff against "
            f"today's version with `ombro approvals show {a.ref}`; to approve it after all, open it "
            f"with `ombro approvals edit {a.ref}`, save, then approve"
        )
    _save(a, status="executing", executing_at=utc_now(), executing_pid=os.getpid())
    _journal(job_dir, "outbox_approved", a, "approved" + (" (edited)" if a.edited else ""), edited=a.edited)
    try:
        kept = skills.write(home, name, text, source=f"job {a.job_id}", accepted=date.today().isoformat())
    except (OSError, skills.SkillError) as e:
        _save(a, status="failed", error=str(e)[:RESULT_CHARS], decided_at=utc_now())
        _journal(job_dir, "outbox_failed", a, str(e)[:300])
        _record(job_dir, a, f"approved; the skill could not be written: {str(e)[:200]}")
        return a
    given = []
    for who in args.get("for") or []:
        try:
            given.append(skills.give(home, name, str(who)))
        except (OSError, mdfile.FrontMatterError, skills.SkillError) as e:
            given.append(f"not given to {who}: {e}")
    note = f"skill {name} written to skills/{name}/SKILL.md" + (f"; old version kept in {kept}" if kept else "")
    if given:
        note += "; " + "; ".join(given)
    _save(a, status="executed", executed_at=utc_now(), decided_at=utc_now(), result=note)
    _journal(job_dir, "outbox_executed", a, note)
    _record(job_dir, a, "approved" + (" after editing" if a.edited else "") + f"; {note}")
    return a


def edit_skill(home: Home, a: Action, text: str) -> Action:
    """Replace a proposal's text after checking it. Saving also makes it current against
    today's version of the skill, so it can be approved after a change. It stays waiting."""
    from ombro import skills

    job_dir = a.job_dir
    with lock(job_dir):
        _settle_locked(job_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be edited")
        name = str((a.arguments or {}).get("name") or "") if isinstance(a.arguments, dict) else ""
        try:
            skills.check(text, name, max_chars=None)
        except skills.SkillError as e:
            raise OutboxError(f"the edit was not saved: {e}") from None
        mdfile.write_atomic(_kept_skill(a), text)
        _save(a, base_sha256=skills.live_sha(home, name), edited_at=utc_now())
        _journal(job_dir, "outbox_edited", a, "skill text edited; still waiting")
        _record(job_dir)
        return a


def skills_waiting(home: Home) -> int:
    return len(skill_proposals_waiting(home))


def skill_proposals_waiting(home: Home) -> list[tuple[str, str, str]]:
    """(name, description, ref) of each skill proposal waiting for the user."""
    out = []
    for a in waiting(home):
        if a.tool != SKILL_TOOL or a.status != "pending":
            continue
        name = str((a.arguments or {}).get("name") or "") if isinstance(a.arguments, dict) else ""
        try:
            desc = " ".join(str(mdfile.parse(skill_text(a))[0].get("description") or "").split())
        except (OutboxError, mdfile.FrontMatterError):
            desc = ""
        out.append((name, desc, a.ref))
    return out


# ---------------------------------------------------------------------------
# Deciding (during a job or after it)


@contextmanager
def lock(job_dir: Path, wait: float = 0.0):
    """One change to a job's outbox at a time: staging by the job, or a decision.
    Waits up to `wait` seconds, then raises OutboxBusy."""
    folder = outbox_dir(job_dir)
    folder.mkdir(exist_ok=True)
    import fcntl

    with open(folder / ".lock", "a") as f:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OutboxBusy(
                        f"another outbox command is working on job {Path(job_dir).name}; wait for it to finish"
                    ) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _save(a: Action, **fields: Any) -> None:
    a.meta.update(fields)
    _, body = mdfile.read(a.path)
    mdfile.write(a.path, a.meta, body)


def _settle_locked(job_dir: Path) -> list[Action]:
    """With the lock held: an action still `executing` was left by a process that
    died mid-call. It may or may not have happened, so it becomes uncertain."""
    out = []
    for a in actions(job_dir):
        if a.status == "executing":
            _save(
                a,
                status="uncertain",
                note="Ombro stopped while this action was running, so it may or may not "
                "have happened. It is never retried; settle it with `ombro approvals resolve`.",
            )
            _journal(job_dir, "outbox_uncertain", a, "Ombro stopped while it was running")
            out.append(a)
    if out:
        _record(job_dir)
    return out


def settle(job_dir: Path) -> None:
    """Settle orphaned `executing` actions if no outbox command is running."""
    if not any(a.status == "executing" for a in actions(job_dir)):
        return
    try:
        with lock(job_dir):
            _settle_locked(job_dir)
    except OutboxBusy:
        pass


def _journal(job_dir: Path, action: str, a: Action, note: str, **extra: Any) -> None:
    note = " ".join(str(note).split())  # one line, whatever the server's answer looked like
    Journal(Path(job_dir) / "journal.jsonl").write(
        "you", action, f"{a.ref} {a.tool}: {note}", ref=a.ref, tool=a.tool, **extra
    )


def _record(job_dir: Path, a: Action | None = None, line: str | None = None) -> None:
    """Counts in job.md, and a line in the report's Approvals section."""
    try:
        jobs.update_job(job_dir, outbox=counts(job_dir))
    except (OSError, mdfile.FrontMatterError):
        pass
    report = Path(job_dir) / "report.md"
    if line and a and report.is_file():
        text = report.read_text(encoding="utf-8")
        if DECISIONS_HEADING not in text:
            text = text.rstrip() + f"\n\n{DECISIONS_HEADING}\n\nRecorded by Ombro as you decide.\n"
        text = text.rstrip() + f"\n- {utc_now()} {a.ref} `{a.tool}`: {line}\n"
        mdfile.write_atomic(report, text)


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


def _default_pool(home: Home, job_dir: Path):
    from ombro.outward import McpPool

    meta, _ = jobs.read_job(job_dir)
    return McpPool(home, ToolSettings.from_dict(meta.get("tools"), "job.md tools"), log_dir=job_dir)


def approve(home: Home, a: Action, *, pool_factory: PoolFactory | None = None) -> Action:
    """Run one action, at most once. Returns it with its outcome recorded."""
    from ombro.outward import split_name

    job_dir = a.job_dir
    with lock(job_dir):
        _settle_locked(job_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(
                f"{a.ref} is {a.status}, so it cannot be approved"
                + (" (settle it with `ombro approvals resolve`)" if a.status == "uncertain" else "")
            )
        if _refused_now(home, a.tool):
            raise OutboxError(f"{a.tool} is set to `permission: refuse` in tools.md, so it cannot run")
        problem = check_arguments(a)
        if problem:
            raise OutboxError(f"{a.ref} cannot run: {problem}. Fix it with `ombro approvals edit {a.ref}`")
        if a.tool == PLACE_TOOL:
            return _approve_place(home, a)
        if a.tool == SKILL_TOOL:
            return _approve_skill(home, a)
        if a.tool.startswith("api."):
            return _approve_api(home, a)
        if a.tool.startswith("composio."):
            return _approve_api(home, a, composio=True)
        if a.tool == "browser.submit":
            return _approve_api(home, a, browser=True)
        parts = split_name(a.tool)
        if parts is None:
            raise OutboxError(f"{a.ref} names {a.tool!r}, which is not an MCP tool")
        try:
            pool = (pool_factory or _default_pool)(home, job_dir)
        except SetupError as e:
            raise OutboxError(f"{a.ref} cannot run: the job's tool settings have a problem: {e}") from None
        _save(a, status="executing", executing_at=utc_now(), executing_pid=os.getpid())
        _journal(job_dir, "outbox_approved", a, "approved" + (" (edited)" if a.edited else ""), edited=a.edited)
        sent: list = []
        try:
            result = pool.call(parts[0], parts[1], a.arguments, sent=sent)
        except McpNotReached as e:
            _save(a, status="pending", last_error=str(e), executing_at=None, executing_pid=None)
            _journal(job_dir, "outbox_not_reached", a, f"not sent: {e}")
            _record(job_dir)
            raise OutboxError(
                f"{a.ref} was not sent, because the server could not be reached: {e}. "
                "It is still waiting; try again later"
            ) from None
        except McpCallLost as e:
            _save(
                a,
                status="uncertain",
                note=f"{e}; it may or may not have happened. It is never retried.",
                decided_at=utc_now(),
            )
            _journal(job_dir, "outbox_uncertain", a, str(e))
            _record(job_dir, a, f"approved, but no answer came back ({e}): uncertain")
            return a
        except McpError as e:
            _save(a, status="failed", error=str(e)[:RESULT_CHARS], decided_at=utc_now())
            _journal(job_dir, "outbox_failed", a, str(e)[:300])
            _record(job_dir, a, f"approved; the service refused it: {str(e)[:200]}")
            return a
        finally:
            pool.close()
        from ombro.mcp import result_text

        text = result_text(result, RESULT_CHARS)
        if result.get("isError"):
            _save(a, status="failed", error=text or "the service reported an error", decided_at=utc_now())
            _journal(job_dir, "outbox_failed", a, (text or "error")[:300])
            _record(job_dir, a, f"approved; the service reported an error: {(text or 'no detail')[:200]}")
        else:
            _save(a, status="executed", executed_at=utc_now(), decided_at=utc_now(), result=text)
            _journal(job_dir, "outbox_executed", a, (text or "done")[:300])
            _record(job_dir, a, "approved" + (" after editing" if a.edited else "") + "; done")
        return a


def _approve_api(home: Home, a: Action, composio: bool = False, browser: bool = False) -> Action:
    """Run an approved web API request (or Composio tool), under the lock approve() holds, at most once."""
    from ombro import apitool
    from ombro import composio as composio_mod
    from ombro.tools import ToolError

    job_dir = a.job_dir
    meta, _ = jobs.read_job(job_dir)
    _save(a, status="executing", executing_at=utc_now(), executing_pid=os.getpid())
    _journal(job_dir, "outbox_approved", a, "approved" + (" (edited)" if a.edited else ""), edited=a.edited)
    from ombro import browser as browser_mod

    run = (
        composio_mod.run_approved
        if composio
        else (lambda h, t, _tool, args: browser_mod.run_approved(h, t, args))
        if browser
        else apitool.run_approved
    )
    what = "Composio" if composio else "the website" if browser else "the API"
    try:
        text = run(home, meta.get("tools"), a.tool, a.arguments)
    except (apitool.ApiNotReached, composio_mod.ComposioNotReached, browser_mod.BrowserUnavailable) as e:
        _save(a, status="pending", last_error=str(e), executing_at=None, executing_pid=None)
        _journal(job_dir, "outbox_not_reached", a, f"not sent: {e}")
        _record(job_dir)
        raise OutboxError(
            f"{a.ref} was not sent, because {what} could not be reached: {e}. It is still waiting; try again later"
        ) from None
    except (apitool.ApiCallLost, composio_mod.ComposioCallLost, browser_mod.SubmitUncertain) as e:
        _save(
            a,
            status="uncertain",
            note=f"{e}; it may or may not have happened. It is never retried.",
            decided_at=utc_now(),
        )
        _journal(job_dir, "outbox_uncertain", a, str(e))
        _record(job_dir, a, f"approved, but no answer came back ({e}): uncertain")
        return a
    except (ToolError, SetupError) as e:
        _save(a, status="failed", error=str(e)[:RESULT_CHARS], decided_at=utc_now())
        _journal(job_dir, "outbox_failed", a, str(e)[:300])
        _record(job_dir, a, f"approved; {what} refused it: {str(e)[:200]}")
        return a
    text = text[:RESULT_CHARS]
    _save(a, status="executed", executed_at=utc_now(), decided_at=utc_now(), result=text)
    _journal(job_dir, "outbox_executed", a, text[:300])
    _record(job_dir, a, "approved" + (" after editing" if a.edited else "") + "; done")
    return a


def decline(home: Home, a: Action) -> Action:
    job_dir = a.job_dir
    with lock(job_dir):
        _settle_locked(job_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be declined")
        _save(a, status="declined", decided_at=utc_now())
        _journal(job_dir, "outbox_declined", a, "declined")
        _record(job_dir, a, "declined; it will never run")
    if a.tool == SKILL_TOOL:
        _remember_declined_skill(home, a)
    return a


def _remember_declined_skill(home: Home, a: Action) -> None:
    """So the Lead never proposes it again (skills.declined)."""
    from datetime import date

    from ombro import skills

    args = a.arguments if isinstance(a.arguments, dict) else {}
    name = str(args.get("name") or "")
    try:
        meta, _ = mdfile.parse(skill_text(a))
    except (OutboxError, mdfile.FrontMatterError):
        meta = {}
    try:
        skills.record_declined(
            home,
            name=name,
            description=str(meta.get("description") or ""),
            change=str(a.meta.get("change") or "new"),
            base_sha=str(a.meta.get("base_sha256") or ""),
            job=a.job_id,
            date=date.today().isoformat(),
        )
    except (OSError, mdfile.FrontMatterError):
        pass  # the decline stands; only the reminder to the Lead is lost


def edit(home: Home, a: Action, arguments: Any) -> Action:
    """Replace an action's arguments after checking them. It stays waiting."""
    job_dir = a.job_dir
    with lock(job_dir):
        _settle_locked(job_dir)
        a = _reload(a)
        if a.status != "pending":
            raise OutboxError(f"{a.ref} is {a.status}, so it cannot be edited")
        trial = Action(a.path, {**a.meta, "arguments": arguments})
        problem = check_arguments(trial)
        if problem:
            raise OutboxError(f"the edit was not saved: {problem}")
        _save(a, arguments=arguments, edited_at=utc_now())
        _journal(job_dir, "outbox_edited", a, "arguments edited; still waiting")
        _record(job_dir)
        return a


def resolve(home: Home, a: Action, sent: bool) -> Action:
    """Settle an uncertain action: it happened (executed) or it did not (back to waiting)."""
    job_dir = a.job_dir
    with lock(job_dir):
        _settle_locked(job_dir)
        a = _reload(a)
        if a.status != "uncertain":
            raise OutboxError(f"{a.ref} is {a.status}; only an uncertain action needs settling")
        if sent:
            _save(a, status="executed", resolved_at=utc_now(), result="marked as done by you")
            _journal(job_dir, "outbox_resolved", a, "you marked it as done")
            _record(job_dir, a, "you marked it as done")
        else:
            _save(a, status="pending", resolved_at=utc_now(), executing_at=None, executing_pid=None)
            _journal(job_dir, "outbox_resolved", a, "you marked it as not done; waiting again")
            _record(job_dir, a, "you marked it as not done; waiting again")
        return a
