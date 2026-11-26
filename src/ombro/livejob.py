"""Live jobs: work you stay for.

Ombro's main way of working is the handed-off job: you brief it, the crew works in the
background, and the Lead reports back. A live job is the other way. From a chat, the Lead
writes a short spec, and the job starts at once with no interview. The chat follows it: each
step shows as it happens, the Lead can stop and ask you (a background job never waits for
you), and the report opens in the chat when it ends. Afterwards you can save it as a brief,
or schedule it.

A live job is an ordinary job in its own process, marked `live: true` in job.md, so the
limits, the journal, approvals and the report are the same, and /stop works as for any
job. If you leave the chat, it carries on in the background and stops waiting for answers.

Questions pass through files in the job's folder: the job writes live/question.json and
waits for live/answer.json, which the chat writes once you answer.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import time
from pathlib import Path
from typing import Any, Callable

from ombro import jobs, mdfile
from ombro.home import Home

ANSWER_WAIT = 15 * 60  # how long a job waits for an answer before carrying on without one
POLL = 0.4
NO_ANSWER = "No answer came (the user may have left the chat). Carry on with your best judgement and say so."

ASK_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_you",
        "description": "Live jobs only: ask the user, who is watching this job in the chat, one short question "
        "when the answer would change the work (a choice between approaches, a missing fact). Returns their "
        "answer, or that none came. Ask rarely; never to confirm what the spec already says.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, in one or two sentences."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: two to four short answers to choose from.",
                },
            },
            "required": ["question"],
        },
    },
}

LIVE_NOTE = """

# This is a live job
The user is watching it in the chat as it happens. You may ask them with
ask_you when an answer would change the work; keep questions few, short and
specific. If no answer comes, carry on with your best judgement and say so in
the report."""


def _dir(job_dir: Path) -> Path:
    return Path(job_dir) / "live"


# -- the job's side -------------------------------------------------------------------


def ask(
    job_dir: Path,
    question: str,
    options: list[str] | None = None,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    wait: float = ANSWER_WAIT,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Put a question to the user and wait for the answer. None if none came in time, or the job
    was told to stop."""
    folder = _dir(job_dir)
    folder.mkdir(exist_ok=True)
    qid = secrets.token_hex(6)
    (folder / "answer.json").unlink(missing_ok=True)
    mdfile.write_atomic(
        folder / "question.json",
        json.dumps({"id": qid, "question": question, "options": [o for o in (options or []) if o][:4]}),
    )
    deadline = clock() + wait
    try:
        while clock() < deadline and not cancelled():
            got = _read(folder / "answer.json")
            if got and got.get("id") == qid:
                return str(got.get("answer") or "").strip() or None
            sleep(POLL)
        return None
    finally:
        (folder / "question.json").unlink(missing_ok=True)
        (folder / "answer.json").unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- the chat's side ----------------------------------------------------------------


def pending(job_dir: Path) -> dict[str, Any] | None:
    """The question the job is waiting on, if any."""
    q = _read(_dir(job_dir) / "question.json")
    return q if q and q.get("id") and q.get("question") else None


def answer(job_dir: Path, qid: str, text: str) -> None:
    mdfile.write_atomic(_dir(job_dir) / "answer.json", json.dumps({"id": qid, "answer": text}))


def spec_text(title: str, task: str) -> str:
    title = " ".join(str(title).split()) or "Live job"
    return f"# {title}\n\n{str(task).strip()}\n"


def start(
    home: Home,
    crews: list[str],
    title: str,
    task: str,
    *,
    chat: str | None = None,
    inputs: list[str] | None = None,
    folders: dict[str, str] | None = None,
) -> Path:
    """Create a live job from what the Lead wrote in the chat, and start it in the background.
    `folders` are the shared folders the Lead named, as {name: read or write}: the job then gets
    only those. With none it may read every shared folder and change none."""
    from ombro.settings import load_limits, load_tool_settings

    spec_meta: dict[str, Any] = {
        "crews": crews,
        "live": True,
        **({"inputs": list(inputs)} if inputs else {}),
        **({"folders": dict(folders)} if folders else {}),
    }
    body = spec_text(title, task)
    tools = load_tool_settings(home)
    limits = load_limits(home, None, tools)
    extra: dict[str, Any] = {"live": True}
    if chat:
        extra["chat"] = chat
    job_dir = jobs.create_job(
        home, crews, spec_meta, body, limits, source="a live job from a chat", tools=tools.to_dict(), extra=extra
    )
    jobs.start_detached(home, job_dir)
    return job_dir


def stop(job_dir: Path) -> bool:
    """Ask the job to stop, as `ombro stop` does: it finishes its current step, then reports."""
    try:
        meta, _ = jobs.read_job(job_dir)
    except (OSError, mdfile.FrontMatterError):
        return False
    pid = meta.get("pid")
    if meta.get("status") != "running" or not pid or not jobs.pid_alive(int(pid)) or int(pid) == os.getpid():
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return False
    return True


def save_as_brief(home: Home, job_dir: Path) -> str:
    """Keep a live job's spec as a saved brief (specs/<name>.md), to start again or schedule.
    Returns its name."""
    import re

    meta, body = mdfile.read(Path(job_dir) / "spec.md")
    meta = {k: v for k, v in meta.items() if k not in ("live",)}
    from ombro.attachments import without_inputs_section

    body = without_inputs_section(body)  # the job adds it again, for the files it is given
    base = re.sub(r"[^a-z0-9]+", "-", (jobs.first_line(body, 60) or "live-job").lower()).strip("-")[:48] or "live-job"
    name, n = base, 2
    home.specs_dir.mkdir(parents=True, exist_ok=True)
    while (home.specs_dir / f"{name}.md").exists():
        name, n = f"{base}-{n}", n + 1
    meta["name"] = name
    mdfile.write(home.specs_dir / f"{name}.md", meta, body)
    return name


def follow(home: Home, ui, job_dir: Path, *, sleep: Callable[[float], None] = time.sleep, poll: float = POLL) -> str:
    """Show a live job in the chat until it ends: its steps, its questions (asked here, answered
    by you), and how it ended. Esc (an interrupt) asks it to stop; it then reports. Returns the
    job's status."""
    from dataclasses import replace

    from ombro.journal import Follower
    from ombro.session import job_ended, job_progress
    from ombro.ui import Field, Note, Option

    job_dir = Path(job_dir)
    job_id = job_dir.name
    follower = Follower(job_dir / "journal.jsonl")
    asked: set[str] = set()
    stopping = False
    quiet = {"report_written"}  # the report opens in the chat anyway

    def interrupted() -> None:
        nonlocal stopping
        if not stopping:
            stopping = True
            stop(job_dir)
            try:
                ui.show(Note("Stopping: the crew finishes its current step, then writes the handover."))
            except KeyboardInterrupt:
                pass

    while True:
        try:
            for ev in follower.new_events():
                e = job_progress(job_id, ev)
                if e is not None and e.action not in quiet:
                    ui.show(replace(e, live=True))
            q = pending(job_dir)
            if q and q["id"] not in asked and not stopping:
                asked.add(q["id"])
                options = [str(o) for o in q.get("options") or []]
                f = (
                    Field(
                        "live_answer",
                        str(q["question"]),
                        "choice",
                        mode="Chat",
                        other="your own answer",
                        options=[Option(o, o) for o in options],
                    )
                    if options
                    else Field("live_answer", str(q["question"]), "text", mode="Chat")
                )
                got = ui.ask(f)
                if pending(job_dir) and pending(job_dir)["id"] == q["id"]:
                    answer(job_dir, q["id"], str(got or ""))
            if getattr(ui, "stop_requested", False):
                ui.stop_requested = False
                interrupted()
        except KeyboardInterrupt:
            interrupted()
        try:
            meta, _ = jobs.read_job(job_dir)
        except (OSError, mdfile.FrontMatterError):
            meta = {"status": "running"}
        if meta.get("status") != "running" or jobs.is_dead(meta):
            for ev in follower.new_events():
                e = job_progress(job_id, ev)
                if e is not None and e.action not in quiet:
                    try:
                        ui.show(replace(e, live=True))
                    except KeyboardInterrupt:
                        pass
            break
        sleep(poll)
    jobs.reap_dead_jobs(home)
    ended = replace(job_ended(home, job_id), live=True)
    try:
        jobs.mark_seen(job_dir)
    except (LookupError, OSError):
        pass
    ui.show(ended)
    return ended.status
