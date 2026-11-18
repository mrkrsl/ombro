"""Job folders and job.md: created, updated, listed, found and reaped."""

from __future__ import annotations

import re

import fcntl
import os
import secrets as _random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ombro import mdfile
from ombro.home import Home
from ombro.journal import utc_now
from ombro.settings import Limits, SetupError

STATUSES = ("running", "completed", "stopped", "stopped_by_limit", "insufficient_brief", "failed")
ORPHAN_GRACE_SECONDS = 120


class JobNotFound(LookupError):
    pass


def job_slug(spec_meta: dict[str, Any] | None, spec_body: str = "") -> str:
    """The friendly part of a job's name: the spec's name, else its title, made into
    lower-case words joined by hyphens."""
    raw = str((spec_meta or {}).get("name") or "") or first_line(spec_body or "", 80)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:48].rstrip("-") or "job"


def new_job_id(slug: str = "job", n: int = 1) -> str:
    """A job's name: what it is, then when it started (local time), so it can be found
    by either: indie-author-creator-research-20261113-1301. A second job of the same spec
    in the same minute gets -2, and so on."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{slug}-{stamp}" + (f"-{n}" if n > 1 else "")


WHEN = re.compile(r"(\d{8})-(\d{4,6})")
_STAMP_END = re.compile(r"-?\d{8}-\d{4,6}(-\d+)?$")
_STAMP_START = re.compile(r"^\d{8}-\d{4,6}-?")


def job_name(job_id: str) -> str:
    """A job's readable name, from its id: indie-author-research-20261113-1301 is
    "Indie author research". An old id that is only a time stays as it is."""
    if _STAMP_START.match(job_id):
        return job_id
    words = _STAMP_END.sub("", job_id).replace("-", " ").strip()
    return (words[:1].upper() + words[1:]) if words and not words.isdigit() else job_id


def ombro_argv() -> list[str]:
    """How to run Ombro's command line again from inside Ombro: the frozen app's own
    binary, or this Python with -m ombro."""
    import sys

    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "ombro"]
_WHEN = WHEN


def job_time(job_dir: Path) -> str:
    """When a job started, from its name (YYYYMMDDHHMMSS), for sorting; old names start
    with the time, new ones end with it."""
    m = _WHEN.search(job_dir.name)
    return (m.group(1) + m.group(2).ljust(6, "0")) if m else ""


def first_line(text: str, limit: int = 120) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def spec_crews(home: Home, spec_meta: dict[str, Any] | None, given: list[str] | None = None) -> list[str]:
    """The crews a job may use: the spec's `crews:`, else those given (--crew), else the default crew.
    Raises SetupError for a crew that does not exist."""
    from ombro.settings import load_preferences

    raw = (spec_meta or {}).get("crews")
    if isinstance(raw, str):
        raw = [raw]
    if raw is not None and (not isinstance(raw, list) or not all(isinstance(c, str) and c.strip() for c in raw)):
        raise SetupError("`crews` in a spec must be a list of crew names, like [marketing, ops]")
    crews = [c.strip() for c in raw] if raw else [c for c in (given or []) if c]
    if not crews:
        crews = [load_preferences(home).default_crew]
    out: list[str] = []
    for c in crews:
        if c not in home.crews():
            raise SetupError(f"no crew named {c!r}; crews: {', '.join(home.crews()) or 'none'}")
        if c not in out:
            out.append(c)
    return out


def job_crews(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("crews")
    return [str(c) for c in raw] if isinstance(raw, list) else []


JOB_BODY = """\
This file is written by Ombro while the job is in progress. Edits are overwritten.
Limits, tool settings, outward permissions and shared folders above were fixed when the job started and cannot change during it.
"""


def create_job(
    home: Home,
    crews: list[str],
    spec_meta: dict[str, Any],
    spec_body: str,
    limits: Limits,
    *,
    source: str,
    tools: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    outward: dict[str, Any] | None = None,
) -> Path:
    """A new job folder and job.md. `outward`, the permission for outward tools,
    comes from preferences and the spec unless given; it is fixed from here on."""
    if outward is None:
        from ombro import outward as outward_mod

        outward = outward_mod.resolve(home, spec_meta)
    from ombro import folders as folders_mod

    try:
        shared, left_out = folders_mod.for_job(home, spec_meta)
    except SetupError as e:
        shared, left_out = [], [f"no shared folder was given: {e}"]
    jobs_dir = home.jobs_dir
    jobs_dir.mkdir(parents=True, exist_ok=True)
    slug, n = job_slug(spec_meta, spec_body), 1
    while True:
        job_id = new_job_id(slug, n)
        n += 1
        job_dir = jobs_dir / job_id
        try:
            job_dir.mkdir()
            break
        except FileExistsError:
            continue
    for sub in ("workspace", "scratch", "outbox"):
        (job_dir / sub).mkdir()
    if spec_meta.get("inputs"):  # files attached to the brief: copied in, and named in the brief
        from ombro import attachments

        given = attachments.give_to_job(job_dir, [str(i) for i in spec_meta["inputs"]])
        spec_body = attachments.without_inputs_section(spec_body)
        if given:
            spec_body = spec_body.rstrip() + attachments.inputs_section(given)
    mdfile.write(job_dir / "spec.md", spec_meta, spec_body)
    (job_dir / "journal.jsonl").touch()
    meta = {
        "job_id": job_id,
        "crews": list(crews),
        "status": "running",
        "pid": None,
        "brief": first_line(spec_body),
        "source": source,
        "created_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "limit_hit": None,
        "limits": limits.to_dict(),
        "tools": tools or {},
        "outward": outward,
        "folders": shared,
        **({"folders_left_out": left_out} if left_out else {}),
        "usage": {},
        **(extra or {}),
    }
    mdfile.write(job_dir / "job.md", meta, JOB_BODY)
    return job_dir


def detached_env(home: Home) -> dict[str, str]:
    """The environment for a job process that outlives the one starting it.

    The app's binary is a PyInstaller one-file build: each run unpacks itself into a
    temporary folder and deletes it on exit. A child that inherits the parent's
    PyInstaller variables reuses that folder, so when the parent is short-lived (the
    scheduler's `tick`) the folder is gone before the job loads and the job dies at once.
    PYINSTALLER_RESET_ENVIRONMENT makes the child unpack its own copy."""
    import sys

    env = {**os.environ, **home.process_env()}
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def start_detached(home: Home, job_dir: Path) -> int:
    """Start `ombro _exec <job_dir>` in its own session, logging to process.log.
    The child records its own pid in job.md. Returns the child's pid."""
    import subprocess

    log = open(Path(job_dir) / "process.log", "ab")
    try:
        proc = subprocess.Popen(
            [*ombro_argv(), "_exec", str(job_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env=detached_env(home),
        )
    finally:
        log.close()
    return proc.pid


def usage(meta: dict[str, Any]) -> dict[str, Any]:
    """A job's usage so far. Jobs from before step 10a-0 kept it as `spend`."""
    return dict(meta.get("usage") or meta.get("spend") or {})


def interview_usage(meta: dict[str, Any]) -> dict[str, Any]:
    return dict(meta.get("interview_usage") or meta.get("interview_spend") or {})


def read_job(job_dir: Path) -> tuple[dict[str, Any], str]:
    return mdfile.read(Path(job_dir) / "job.md")


@contextmanager
def job_lock(job_dir: Path):
    """Held while job.md is read, changed and written back. The job, outbox
    decisions and interfaces all change job.md; under the lock none of their
    changes is lost. Each holds it only for a moment, so waiting is fine."""
    with open(Path(job_dir) / ".job.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def update_job(job_dir: Path, **fields: Any) -> dict[str, Any]:
    with job_lock(job_dir):
        meta, body = read_job(job_dir)
        meta.update(fields)
        mdfile.write(Path(job_dir) / "job.md", meta, body or JOB_BODY)
    return meta


def mark_seen(job_dir: Path) -> bool:
    """Record that a finished job's report, or the notice that it ended, has been
    shown. Only the first time counts. Returns True if it was not seen before."""
    with job_lock(job_dir):
        meta, body = read_job(job_dir)
        if meta.get("seen_at") or meta.get("status") == "running":
            return False
        meta["seen_at"] = utc_now()
        mdfile.write(Path(job_dir) / "job.md", meta, body or JOB_BODY)
    return True


def unseen_jobs(home: Home) -> list[Path]:
    """Finished jobs whose report has not been shown yet, oldest first."""
    out = []
    for d in all_jobs(home):
        try:
            meta, _ = read_job(d)
        except (OSError, mdfile.FrontMatterError):
            continue
        if meta.get("status") != "running" and not meta.get("seen_at") and (d / "report.md").is_file():
            out.append(d)
    return out


def all_jobs(home: Home, crew: str | None = None) -> list[Path]:
    """Every job, oldest first; with a crew, only the jobs that used it."""
    rdir = home.jobs_dir
    if not rdir.is_dir():
        return []
    out = []
    for d in rdir.iterdir():
        if not (d.is_dir() and (d / "job.md").is_file()):
            continue
        if crew:
            try:
                if crew not in job_crews(read_job(d)[0]):
                    continue
            except (OSError, mdfile.FrontMatterError):
                continue
        out.append(d)
    out.sort(key=lambda d: (job_time(d), d.name))
    return out


def find_job(home: Home, job_id: str) -> Path:
    """A job by its name, the start of it, or any part of it ("indie", "20261113").
    Where several match, the newest; an exact name always wins."""
    every = all_jobs(home)
    want = job_id.strip()
    for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want.lower() in n.lower()):
        matches = [d for d in every if test(d.name)]
        if matches:
            return matches[-1]
    raise JobNotFound(f"no job matching {job_id!r}")


def latest_job(home: Home, crew: str | None) -> Path:
    jobs = all_jobs(home, crew)
    if not jobs:
        raise JobNotFound("there are no jobs yet" + (f" that used crew {crew!r}" if crew else ""))
    return jobs[-1]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _iso_age(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return time.time() - dt.timestamp()


def is_dead(meta: dict[str, Any]) -> bool:
    if meta.get("status") != "running":
        return False
    pid = meta.get("pid")
    if pid:
        return pid != os.getpid() and not pid_alive(int(pid))
    return _iso_age(meta.get("created_at")) > ORPHAN_GRACE_SECONDS


def reap_dead_jobs(home: Home) -> list[Path]:
    """Write a mechanical report for any job whose process has died, and mark it failed."""
    from ombro import report  # local import: report depends on this module

    reaped = []
    for job_dir in all_jobs(home):
        try:
            meta, _ = read_job(job_dir)
        except (OSError, mdfile.FrontMatterError):
            continue
        if not is_dead(meta):
            continue
        if meta.get("pid"):
            failure = "The job's process ended without finishing; this report was written from the journal."
        else:  # the engine records its pid first thing, so the process never got going
            failure = ("The job's process stopped before it could start; process.log in the job's "
                       "folder says why. This report was written from the journal.")
        report.write_mechanical_report(home, job_dir, status="failed", failure=failure)
        reaped.append(job_dir)
    return reaped


def brief_text(job_dir: Path) -> str:
    """The brief a job ran from: the body of the copy of the spec it keeps (spec.md),
    else the one line job.md holds for jobs from before that copy."""
    path = Path(job_dir) / "spec.md"
    if path.is_file():
        try:
            return mdfile.read(path)[1].strip()
        except mdfile.FrontMatterError:
            return path.read_text(encoding="utf-8").strip()
    return str(read_job(job_dir)[0].get("brief") or "")


class StillRunning(ValueError):
    pass


def clear_job(job_dir: Path) -> bool:
    """Hide a finished job from the app's Jobs list. Nothing is deleted: its folder, report and
    files stay where chats, approvals and schedules may point. It counts as seen. Returns True if
    it was not cleared before. A running job can't be cleared."""
    with job_lock(job_dir):
        meta, body = read_job(job_dir)
        if meta.get("status") == "running" and not is_dead(meta):
            raise StillRunning(f"job {Path(job_dir).name} is still running; stop it first")
        if meta.get("cleared_at"):
            return False
        now = utc_now()
        meta["cleared_at"] = now
        meta["seen_at"] = meta.get("seen_at") or now
        mdfile.write(Path(job_dir) / "job.md", meta, body or JOB_BODY)
    return True


def restore_job(job_dir: Path) -> bool:
    """Put a cleared job back on the Jobs list. Returns True if it was cleared."""
    with job_lock(job_dir):
        meta, body = read_job(job_dir)
        if not meta.pop("cleared_at", None):
            return False
        mdfile.write(Path(job_dir) / "job.md", meta, body or JOB_BODY)
    return True


def clear_finished(home: Home) -> int:
    """Clear every finished job from the Jobs list; running ones stay. Returns how many."""
    n = 0
    for d in all_jobs(home):
        try:
            n += clear_job(d)
        except (StillRunning, OSError, mdfile.FrontMatterError):
            continue
    return n
