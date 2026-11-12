"""Run directories and run.md: created, updated, listed, found and reaped."""

from __future__ import annotations

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


class RunNotFound(LookupError):
    pass


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + _random.token_hex(2)


def first_line(text: str, limit: int = 120) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def spec_crews(home: Home, spec_meta: dict[str, Any] | None, given: list[str] | None = None) -> list[str]:
    """The crews a run may use: the spec's `crews:`, else those given (--crew), else the default crew.
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


def run_crews(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("crews")
    return [str(c) for c in raw] if isinstance(raw, list) else []


RUN_BODY = """\
This file is written by Ombro while the run is in progress. Edits are overwritten.
Limits, tool settings, outward permissions and shared folders above were fixed when the run started and cannot change during it.
"""


def create_run(
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
    """A new run folder and run.md. `outward`, the permission for outward tools,
    comes from preferences and the spec unless given; it is fixed from here on."""
    if outward is None:
        from ombro import outward as outward_mod

        outward = outward_mod.resolve(home, spec_meta)
    from ombro import folders as folders_mod

    try:
        shared, left_out = folders_mod.for_run(home, spec_meta)
    except SetupError as e:
        shared, left_out = [], [f"no shared folder was given: {e}"]
    runs_dir = home.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = new_run_id()
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            continue
    for sub in ("workspace", "scratch", "outbox"):
        (run_dir / sub).mkdir()
    mdfile.write(run_dir / "spec.md", spec_meta, spec_body)
    (run_dir / "journal.jsonl").touch()
    meta = {
        "run_id": run_id,
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
    mdfile.write(run_dir / "run.md", meta, RUN_BODY)
    return run_dir


def start_detached(home: Home, run_dir: Path) -> int:
    """Start `python -m ombro _exec <run_dir>` in its own session, logging to process.log.
    The child records its own pid in run.md. Returns the child's pid."""
    import subprocess
    import sys

    log = open(Path(run_dir) / "process.log", "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ombro", "_exec", str(run_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env={**os.environ, "OMBRO_HOME": str(home.root)},
        )
    finally:
        log.close()
    return proc.pid


def usage(meta: dict[str, Any]) -> dict[str, Any]:
    """A run's usage so far. Runs from before step 10a-0 kept it as `spend`."""
    return dict(meta.get("usage") or meta.get("spend") or {})


def interview_usage(meta: dict[str, Any]) -> dict[str, Any]:
    return dict(meta.get("interview_usage") or meta.get("interview_spend") or {})


def read_run(run_dir: Path) -> tuple[dict[str, Any], str]:
    return mdfile.read(Path(run_dir) / "run.md")


@contextmanager
def run_lock(run_dir: Path):
    """Held while run.md is read, changed and written back. The run, outbox
    decisions and interfaces all change run.md; under the lock none of their
    changes is lost. Each holds it only for a moment, so waiting is fine."""
    with open(Path(run_dir) / ".run.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def update_run(run_dir: Path, **fields: Any) -> dict[str, Any]:
    with run_lock(run_dir):
        meta, body = read_run(run_dir)
        meta.update(fields)
        mdfile.write(Path(run_dir) / "run.md", meta, body or RUN_BODY)
    return meta


def mark_seen(run_dir: Path) -> bool:
    """Record that a finished run's report, or the notice that it ended, has been
    shown. Only the first time counts. Returns True if it was not seen before."""
    with run_lock(run_dir):
        meta, body = read_run(run_dir)
        if meta.get("seen_at") or meta.get("status") == "running":
            return False
        meta["seen_at"] = utc_now()
        mdfile.write(Path(run_dir) / "run.md", meta, body or RUN_BODY)
    return True


def unseen_runs(home: Home) -> list[Path]:
    """Finished runs whose report has not been shown yet, oldest first."""
    out = []
    for d in all_runs(home):
        try:
            meta, _ = read_run(d)
        except (OSError, mdfile.FrontMatterError):
            continue
        if meta.get("status") != "running" and not meta.get("seen_at") and (d / "report.md").is_file():
            out.append(d)
    return out


def all_runs(home: Home, crew: str | None = None) -> list[Path]:
    """Every run, oldest first; with a crew, only the runs that used it."""
    rdir = home.runs_dir
    if not rdir.is_dir():
        return []
    out = []
    for d in rdir.iterdir():
        if not (d.is_dir() and (d / "run.md").is_file()):
            continue
        if crew:
            try:
                if crew not in run_crews(read_run(d)[0]):
                    continue
            except (OSError, mdfile.FrontMatterError):
                continue
        out.append(d)
    out.sort(key=lambda d: d.name)
    return out


def find_run(home: Home, run_id: str) -> Path:
    matches = [d for d in all_runs(home) if d.name == run_id]
    if not matches:
        matches = [d for d in all_runs(home) if d.name.startswith(run_id)]
    if not matches:
        raise RunNotFound(f"no run matching {run_id!r}")
    if len(matches) > 1:
        raise RunNotFound(f"{run_id!r} matches {len(matches)} runs; give more of the ID")
    return matches[0]


def latest_run(home: Home, crew: str | None) -> Path:
    runs = all_runs(home, crew)
    if not runs:
        raise RunNotFound("there are no runs yet" + (f" that used crew {crew!r}" if crew else ""))
    return runs[-1]


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


def reap_dead_runs(home: Home) -> list[Path]:
    """Write a mechanical report for any run whose process has died, and mark it failed."""
    from ombro import report  # local import: report depends on this module

    reaped = []
    for run_dir in all_runs(home):
        try:
            meta, _ = read_run(run_dir)
        except (OSError, mdfile.FrontMatterError):
            continue
        if not is_dead(meta):
            continue
        report.write_mechanical_report(
            home,
            run_dir,
            status="failed",
            failure="The run's process ended without finishing; this report was written from the journal.",
        )
        reaped.append(run_dir)
    return reaped
