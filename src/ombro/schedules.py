"""Schedules: one file per scheduled task in crews/<crew>/schedules/, and `ombro tick`.

The schedule files are the source of truth. The system scheduler calls
`ombro tick` once a minute; tick reads every file and starts any job that is
due, detached, exactly as `ombro start --detach` does. Editing a file needs no
reinstallation: the next tick reads it.

What has started is read from the jobs themselves (job.md records the schedule
and the slot it was due for) and from the crew's index.md (slots that were
skipped or missed). The only other record is scheduler/tick.md, the time of
the last tick, which is how tick tells "the machine was off" from "not due".
Deleting it is harmless: the next tick looks back only one grace period.

Rules:
- A slot is started if a tick sees it within its grace period (10 minutes
  unless set). Slots older than that are skipped and noted in index.md, one
  entry per gap, never started late.
- A slot that cannot start yet (the schedule's previous job is still going,
  or the caps on scheduled jobs are reached) waits while it is within grace,
  then is skipped and noted with the reason.
- Slots that fell before the schedule file was last changed are ignored, so
  a new, edited or re-enabled schedule takes effect from its next slot.
- A slot that is due but cannot start (a missing spec, invalid limits, no
  model) becomes a failed job with a report and an index entry.
- Scheduled jobs never interview, and must have a token cap.
"""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable

from ombro import cron as cronmod
from ombro import mdfile, memory, report, jobs
from ombro.home import LIMITS_META, Home
from ombro.journal import Journal, utc_now
from ombro.settings import Limits, SetupError, ToolSettings, load_limits, load_tool_settings, parse_duration, resolve_allowance

FIELDS = ("name", "cron", "spec", "enabled", "limits", "grace")
LOOKBACK_SECONDS = 7 * 24 * 3600
LOG_MAX_BYTES = 1_000_000
MIN_GRACE, MAX_GRACE = 60.0, 24 * 3600.0

SCHEDULE_BODY = """\
A scheduled task. `ombro tick`, called once a minute by the system
scheduler, starts a job of the spec when the cron expression is due. Edit
this file freely; the next tick reads it, and nothing needs reinstalling.

- cron: five fields in local time: minute hour day-of-month month day-of-week.
- spec: a saved spec in specs/. The spec names the crews the job uses.
  Scheduled jobs never interview.
- enabled: true or false (`ombro schedule enable|disable <name>`).
- limits: optional, the same keys as a spec's limits (allowance, max_tokens,
  max_rounds, job_timeout, tool_max_calls…). They are fixed when each job
  starts and recorded in its job.md. A scheduled job must have a token cap.
- grace: optional, how late a tick may still start a slot (default
  schedule_grace in limits.md). Later than that, the slot is skipped and
  noted in index.md.
"""

STATE_BODY = """\
Written by `ombro tick`. The time of the last tick, so a tick can tell slots
missed while the machine was off or asleep. Safe to delete.
"""


@dataclass
class Schedule:
    name: str
    path: Path
    cron_text: str = ""
    cron: cronmod.Cron | None = None
    spec: str = ""
    enabled: bool = False
    limits: dict[str, Any] = field(default_factory=dict)
    grace: float | None = None
    error: str | None = None
    mtime: float = 0.0
    crews: list[str] = field(default_factory=list)  # from its spec: where missed slots are noted

    @property
    def rel(self) -> str:
        return f"schedules/{self.path.name}"

    def spec_path(self, home: Home) -> Path:
        return spec_ref_path(home, self.spec)

    def grace_for(self, limits: Limits) -> float:
        return self.grace if self.grace is not None else limits.schedule_grace


def spec_ref_path(home: Home, ref: Any) -> Path:
    """`specs/name.md` or `name`: a spec saved in specs/, nothing else."""
    text = str(ref or "").strip()
    if not text:
        raise SetupError("no spec is named (spec: specs/<name>.md)")
    name = text[len("specs/"):] if text.startswith("specs/") else text
    if not name.endswith(".md"):
        name += ".md"
    if "/" in name or "\\" in name or name.startswith("."):
        raise SetupError(f"spec {text!r} must be a saved spec in specs/, e.g. specs/my-task.md")
    return home.specs_dir / name


def load_schedule(home: Home, path: Path) -> Schedule:
    """Never raises: a problem is recorded in `error`, so a broken file is shown, not hidden."""
    from ombro.roles import NAME

    s = Schedule(name=path.stem, path=path)
    try:
        s.mtime = path.stat().st_mtime
        meta, _ = mdfile.read(path)
    except (OSError, mdfile.FrontMatterError) as e:
        s.error = str(e)
        return s
    problems = []
    unknown = sorted(set(meta) - set(FIELDS))
    if unknown:
        problems.append(f"unknown setting(s): {', '.join(unknown)}")
    name = str(meta.get("name") or path.stem)
    if name != path.stem:
        problems.append(f"name {name!r} does not match the file name {path.stem!r}")
    if not NAME.match(path.stem):
        problems.append("schedule names use lower-case letters, digits, - and _")
    s.cron_text = str(meta.get("cron") or "")
    try:
        s.cron = cronmod.parse(s.cron_text)
    except cronmod.CronError as e:
        problems.append(f"cron: {e}")
    s.spec = str(meta.get("spec") or "")
    try:
        spec_path = spec_ref_path(home, s.spec)
        spec_meta = mdfile.read(spec_path)[0] if spec_path.is_file() else {}
        s.crews = jobs.spec_crews(home, spec_meta)
    except (SetupError, mdfile.FrontMatterError, OSError) as e:
        problems.append(str(e))
    enabled = meta.get("enabled")
    if not isinstance(enabled, bool):
        problems.append("enabled must be true or false")
    else:
        s.enabled = enabled
    lim = meta.get("limits")
    if lim is not None and not isinstance(lim, dict):
        problems.append("limits must be a mapping, e.g. limits: {allowance: conservative, job_timeout: 15m}")
    else:
        s.limits = dict(lim or {})
    if meta.get("grace") is not None:
        try:
            g = parse_duration(meta["grace"])
            if not MIN_GRACE <= g <= MAX_GRACE:
                raise SetupError("grace must be between 1m and 24h")
            s.grace = g
        except SetupError as e:
            problems.append(f"grace: {e}")
    if problems:
        s.error = "; ".join(problems)
    return s


def all_schedules(home: Home, crew: str | None = None) -> list[Schedule]:
    """Every schedule; with a crew, those whose spec names it."""
    folder = home.schedules_dir
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        if path.name.startswith(".") or path.name.endswith(".previous.md"):
            continue
        s = load_schedule(home, path)
        if crew is None or crew in s.crews:
            out.append(s)
    return out


def find_schedule(home: Home, name: str, crew: str | None = None) -> Schedule:
    matches = [s for s in all_schedules(home) if s.name == name]
    if not matches:
        raise SetupError(f"no schedule named {name!r}")
    return matches[0]


def base_limits(home: Home) -> Limits:
    """limits.md, or Ombro's defaults if limits.md cannot be read (the jobs then fail to start, visibly)."""
    try:
        return load_limits(home)
    except (SetupError, mdfile.FrontMatterError, OSError):
        return Limits.from_dict(resolve_allowance(copy.deepcopy(LIMITS_META)))


@dataclass
class Prepared:
    spec_meta: dict[str, Any]
    spec_body: str
    limits: Limits
    tools: ToolSettings


def prepare(home: Home, s: Schedule) -> Prepared:
    """Everything a scheduled job needs, checked. Raises SetupError with the reason."""
    if s.error:
        raise SetupError(f"the schedule file has a problem: {s.error}")
    path = s.spec_path(home)
    if not path.is_file():
        raise SetupError(f"its spec {s.spec} does not exist")
    meta, body = mdfile.read(path)
    if not body.strip():
        raise SetupError(f"its spec {s.spec} is empty")
    over = meta.get("limits") or {}
    if not isinstance(over, dict):
        raise SetupError(f"`limits` in the spec {s.spec} must be a mapping")
    tools = load_tool_settings(home)
    from ombro import outward

    outward.resolve(home, meta)  # raises SetupError if the spec's outward setting is wrong
    jobs.spec_crews(home, meta)  # and if it names a crew that does not exist
    lim = load_limits(home, over, tools, s.limits)
    if lim.max_tokens is None:
        raise SetupError(
            "a scheduled job must have a token cap, and its limits give none (allowance unlimited or "
            "max_tokens: unlimited). Set another allowance or a max_tokens in the schedule's limits"
        )
    return Prepared(meta, body, lim, tools)


# ---------------------------------------------------------------------------
# The heartbeat and the lock


@dataclass
class TickState:
    last_tick: float | None = None
    pending: dict[str, str] = field(default_factory=dict)
    reported: dict[str, float] = field(default_factory=dict)


def _state_path(home: Home) -> Path:
    return home.scheduler_dir / "tick.md"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso(text: Any) -> float | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def read_state(home: Home) -> TickState:
    try:
        meta, _ = mdfile.read(_state_path(home))
    except (OSError, mdfile.FrontMatterError):
        return TickState()
    pending = meta.get("pending") if isinstance(meta.get("pending"), dict) else {}
    reported = meta.get("reported") if isinstance(meta.get("reported"), dict) else {}
    return TickState(
        last_tick=_from_iso(meta.get("last_tick")),
        pending={str(k): str(v) for k, v in pending.items()},
        reported={str(k): float(v) for k, v in reported.items() if isinstance(v, (int, float))},
    )


def write_state(home: Home, state: TickState) -> None:
    meta: dict[str, Any] = {"last_tick": _iso(state.last_tick) if state.last_tick else None}
    if state.pending:
        meta["pending"] = dict(state.pending)
    if state.reported:
        meta["reported"] = dict(state.reported)
    mdfile.write(_state_path(home), meta, STATE_BODY)


def last_tick(home: Home) -> float | None:
    return read_state(home).last_tick


@contextmanager
def tick_lock(home: Home):
    """Yields True if this process holds the tick lock, False if another tick has it."""
    import fcntl

    home.scheduler_dir.mkdir(parents=True, exist_ok=True)
    with open(home.scheduler_dir / "tick.lock", "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def log_path(home: Home) -> Path:
    return home.scheduler_dir / "tick.log"


def write_log(home: Home, lines: list[str], now: float) -> None:
    path = log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_name("tick.log.1"))
    except FileNotFoundError:
        pass
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(f"{stamp} {line}\n")


# ---------------------------------------------------------------------------
# Scheduled jobs


def scheduled_jobs(home: Home) -> dict[str, list[tuple[Path, dict]]]:
    """schedule -> its jobs, oldest first."""
    out: dict[str, list[tuple[Path, dict]]] = {}
    for job_dir in jobs.all_jobs(home):
        try:
            meta, _ = jobs.read_job(job_dir)
        except (OSError, mdfile.FrontMatterError):
            continue
        if meta.get("schedule"):
            out.setdefault(str(meta["schedule"]), []).append((job_dir, meta))
    return out


def _fmt_slot(slot: str) -> str:
    return slot.replace("T", " ")


def _source(s: Schedule, slot: str, note: str = "") -> str:
    return f"schedule {s.name} ({s.rel}), due {_fmt_slot(slot)}{note}"


def record_failed_start(home: Home, s: Schedule, slot: str, reason: str, limits: Limits, note: str = "") -> Path:
    """A due slot that could not start: a failed job with a report, so it is never silent."""
    spec_text = f"# Schedule {s.name} could not start\n\n{reason[0].upper() + reason[1:]}."
    try:
        _, body = mdfile.read(s.spec_path(home))
        if body.strip():
            spec_text = body
    except (OSError, mdfile.FrontMatterError, SetupError):
        pass
    job_dir = jobs.create_job(
        home, s.crews or jobs.spec_crews(home, {}), {}, spec_text, limits, source=_source(s, slot, note), tools={},
        extra={"schedule": s.name, "due": slot},
    )
    jobs.update_job(job_dir, started_at=utc_now())
    j = Journal(job_dir / "journal.jsonl")
    j.write("harness", "job_started", f"from schedule {s.name}, due {_fmt_slot(slot)}")
    j.write("harness", "failed", f"The scheduled job could not start: {reason}.")
    report.write_mechanical_report(home, job_dir, status="failed", note="the job could not start")
    return job_dir


Starter = Callable[[Home, Path], Any]


@dataclass
class TickResult:
    lines: list[str] = field(default_factory=list)
    started: list[Path] = field(default_factory=list)
    failed: list[Path] = field(default_factory=list)
    noted: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    busy: bool = False


def tick(
    home: Home,
    *,
    clock: Callable[[], float] = time.time,
    tz: tzinfo | None = None,
    starter: Starter | None = None,
    dry_run: bool = False,
) -> TickResult:
    """Start every scheduled job that is due, note slots that were missed. One at a time."""
    now = clock()
    res = TickResult()
    if dry_run:
        _tick(home, now, tz, starter or jobs.start_detached, True, res)
        return res
    with tick_lock(home) as held:
        if not held:
            res.busy = True
            res.lines.append("another tick is running; this one did nothing")
        else:
            _tick(home, now, tz, starter or jobs.start_detached, False, res)
    try:
        write_log(home, res.lines, now)
    except OSError:
        pass
    return res


def _model_problem(home: Home) -> str | None:
    from ombro.init_cmd import connected_model

    return None if connected_model(home) is not None else "no model is connected (run `ombro init`)"


def _tick(home: Home, now: float, tz: tzinfo | None, starter: Starter, dry_run: bool, res: TickResult) -> None:
    if not dry_run:
        for job_dir in jobs.reap_dead_jobs(home):
            res.lines.append(f"job {job_dir.name} had stopped without finishing; marked failed and reported")
    base = base_limits(home)
    state = read_state(home)
    last = state.last_tick
    by_schedule = scheduled_jobs(home)
    running_now = sum(1 for rs in by_schedule.values() for _, m in rs if m.get("status") == "running")
    started = 0
    pending: dict[str, str] = {}
    reported: dict[str, float] = {}
    schedules = all_schedules(home)
    noted_by_crew: dict[str, list[tuple[str, str, str]]] = {}

    for s in schedules:
        if s.error:
            reported[s.rel] = s.mtime
            if state.reported.get(s.rel) != s.mtime:
                res.lines.append(f"problem in {s.rel}: {s.error}")
            continue
        if not s.enabled or s.cron is None:
            continue
        grace = s.grace_for(base)
        start = now - grace if last is None else max(min(last, now) - grace, now - LOOKBACK_SECONDS)
        # Slots after the last tick, plus any the last tick left waiting; the rest it already decided.
        slots = [(name, fire) for name, fire in
                 ((cronmod.slot_name(w), fire) for w, fire in cronmod.slots_between(s.cron, start, now, tz))
                 if fire >= s.mtime and (last is None or fire > last or f"{s.name}@{name}" in state.pending)]
        if not slots:
            continue
        for c in s.crews:
            if c not in noted_by_crew:
                noted_by_crew[c] = memory.noted_slots(home, c)
        own = by_schedule.get(s.name, [])
        ran = {str(m.get("due")) for _, m in own}
        ranges = [(a, b) for c in s.crews for n, a, b in noted_by_crew[c] if n == s.name]
        todo = [(slot, fire) for slot, fire in slots
                if slot not in ran and not any(a <= slot <= b for a, b in ranges)]
        if not todo:
            continue
        stale = [(slot, fire) for slot, fire in todo if now - fire > grace]
        fresh = [(slot, fire) for slot, fire in todo if now - fire <= grace]
        if last is None:
            # No record of earlier ticks: start the newest slot, say nothing about the rest.
            stale, fresh = [], fresh[-1:]
        skipped: list[tuple[str, str]] = []  # (slot, reason) noted as skipped
        missed: list[str] = []
        for slot, fire in stale:
            why = state.pending.get(f"{s.name}@{slot}")
            if why:
                skipped.append((slot, f"not started within the grace period: {why}"))
            else:
                missed.append(slot)
        if fresh:
            slot, fire = fresh[-1]
            key = f"{s.name}@{slot}"
            previous = next((rd for rd, m in reversed(own) if m.get("status") == "running"), None)
            if previous is not None:
                reason = f"the previous job {previous.name} was still running"
            elif running_now >= base.schedule_max_running:
                reason = f"{running_now} scheduled jobs were already running (schedule_max_running)"
            elif started >= base.schedule_max_starts:
                reason = f"this tick had already started {started} scheduled jobs (schedule_max_starts)"
            else:
                reason = None
            if reason:
                for sl, _ in fresh:
                    pending[f"{s.name}@{sl}"] = reason
                res.waiting.append(key)
                res.lines.append(f"{s.name} due {_fmt_slot(slot)} is waiting: {reason}")
            else:
                note = ""
                if cronmod.slot_name(cronmod.to_local(fire, tz)) != slot:
                    note = " (the clocks went forward, so it started at the first minute after the change)"
                _start(home, s, slot, base, starter, dry_run, res, note)
                started += 1
                running_now += 1
                skipped += [(sl, f"a later slot, {_fmt_slot(slot)}, was started instead") for sl, _ in fresh[:-1]]
        if missed:
            if last is not None and last < now - grace:
                why = (f"the machine was off or asleep (no tick from "
                       f"{datetime.fromtimestamp(last).strftime('%d %b %H:%M')} to "
                       f"{datetime.fromtimestamp(now).strftime('%d %b %H:%M')})")
            else:
                why = "no tick ran within the grace period; the machine was off or asleep"
            _note(home, s, missed, "missed", why, base, dry_run, res)
        by_reason: dict[str, list[str]] = {}
        for slot, why in skipped:
            by_reason.setdefault(why, []).append(slot)
        for why, group in by_reason.items():
            _note(home, s, group, "skipped", why, base, dry_run, res)

    enabled = sum(1 for s in schedules if s.enabled and not s.error)
    counts = (f"would start {len(res.started) + len(res.failed)}, would note {len(res.noted)}" if dry_run else
              f"started {len(res.started) + len(res.failed)}, noted {len(res.noted)}")
    res.lines.append(f"tick: {len(schedules)} schedule(s), {enabled} enabled; {counts}, waiting {len(res.waiting)}")
    if not dry_run:
        write_state(home, TickState(last_tick=now, pending=pending, reported=reported))


def _note(home: Home, s: Schedule, slots: list[str], status: str, why: str, base: Limits, dry_run: bool,
          res: TickResult) -> None:
    what = _fmt_slot(slots[0]) if len(slots) == 1 else f"{len(slots)} slots from {_fmt_slot(slots[0])} to {_fmt_slot(slots[-1])}"
    if dry_run:
        res.lines.append(f"{s.name}: would note {what} as {status}: {why}")
        res.noted.append(slots[0])
        return
    added = False
    for c in s.crews:
        added = memory.add_schedule_entry(home, c, s.name, slots, status, why, base.memory_index_entry_chars) or added
    if added:
        res.noted.append(slots[0])
        res.lines.append(f"{s.name}: {what} {status}: {why}; noted in index.md")


def _start(home: Home, s: Schedule, slot: str, base: Limits, starter: Starter, dry_run: bool, res: TickResult,
           note: str) -> None:
    label = f"{s.name} due {_fmt_slot(slot)}"
    try:
        p = prepare(home, s)
        problem = _model_problem(home)
        if problem:
            raise SetupError(problem)
    except (SetupError, OSError, mdfile.FrontMatterError) as e:
        if dry_run:
            res.lines.append(f"{label}: would record a failed job: {e}")
            res.failed.append(Path("."))
            return
        job_dir = record_failed_start(home, s, slot, str(e), base, note)
        res.failed.append(job_dir)
        res.lines.append(f"{label}: could not start ({e}); recorded failed job {job_dir.name}")
        return
    if dry_run:
        res.lines.append(f"{label}: would start a job (allowance {p.limits.allowance}, "
                         f"{p.limits.max_tokens:,} tokens)")
        res.started.append(Path("."))
        return
    job_dir = jobs.create_job(
        home, jobs.spec_crews(home, p.spec_meta), p.spec_meta, p.spec_body, p.limits, source=_source(s, slot, note),
        tools=p.tools.to_dict(), extra={"schedule": s.name, "due": slot},
    )
    try:
        starter(home, job_dir)
    except Exception as e:  # the job is recorded and reported whatever happens
        Journal(job_dir / "journal.jsonl").write("harness", "failed", f"The job's process could not be started: {e}")
        report.write_mechanical_report(home, job_dir, status="failed", note="the job could not start")
        res.failed.append(job_dir)
        res.lines.append(f"{label}: the process could not be started ({e}); recorded failed job {job_dir.name}")
        return
    res.started.append(job_dir)
    res.lines.append(f"{label}: started job {job_dir.name}")


# ---------------------------------------------------------------------------
# Writing schedule files (for `ombro schedule add|enable|disable`)


def write_schedule(home: Home, name: str, cron_text: str, spec_name: str, enabled: bool,
                   limits: dict | None = None, grace: str | None = None) -> Path:
    meta: dict[str, Any] = {"name": name, "cron": cron_text, "spec": f"specs/{spec_name}.md", "enabled": enabled}
    if limits:
        meta["limits"] = limits
    if grace:
        meta["grace"] = grace
    path = home.schedules_dir / f"{name}.md"
    mdfile.write(path, meta, SCHEDULE_BODY)
    return path


def add_schedule(home: Home, name: str, spec: str, cron_text: str, enabled: bool = True,
                 limits: dict | None = None, grace: str | None = None) -> Path:
    """A new schedule, checked, for the Scheduled page, `ombro schedule add` and the chat's offer:
    a new name, a saved spec, and a cron expression. Raises SetupError with the reason."""
    from ombro.roles import NAME

    name, spec = str(name or "").strip(), str(spec or "").strip().removeprefix("specs/").removesuffix(".md")
    if not NAME.match(name):
        raise SetupError("schedule names use lower-case letters, digits, - and _")
    if (home.schedules_dir / f"{name}.md").exists():
        raise SetupError(f"there is already a schedule called {name}")
    if not spec or "/" in spec or not (home.specs_dir / f"{spec}.md").is_file():
        raise SetupError(f"there's no saved spec called {spec!r}")
    try:
        cron = cronmod.parse(cron_text)
    except cronmod.CronError as e:
        raise SetupError(f"cron: {e}") from None
    return write_schedule(home, name, cron.text, spec, enabled, limits, grace)


def set_timing(path: Path, cron_text: str) -> None:
    """Change when a schedule runs, in place, keeping the rest of its file (plan C7).
    The expression is checked first: a bad one changes nothing."""
    cronmod.parse(cron_text)
    meta, body = mdfile.read(path)
    meta["cron"] = " ".join(cron_text.split())
    mdfile.write(path, meta, body)


def remove_schedule(home: Home, s: Schedule) -> Path:
    """Move a schedule's file to removed/schedules/, never erase it (plan C4). Its jobs are kept.
    Returns where it went."""
    import shutil
    import time

    folder = home.removed_dir / "schedules"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / s.path.name
    if target.exists():
        target = folder / f"{s.path.stem}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    shutil.move(str(s.path), str(target))
    return target


def set_enabled(path: Path, enabled: bool) -> None:
    meta, body = mdfile.read(path)
    meta["enabled"] = enabled
    mdfile.write(path, meta, body)
