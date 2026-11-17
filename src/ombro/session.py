"""A session: one live conversation, as the terminal and graphical interfaces both run it.

The chat (ombro.chat) talks with the Lead. Around it, a session does what an
interface used to do itself: it starts a job after the user's yes, notes which
chat started which job, and turns the jobs' journals into events (JobStarted,
JobProgress, JobEnded) that any interface can show. The terminal draws them as
lines; the graphical interface as cards.

`JobWatcher` follows every job in the home, including those started by the
scheduler or the command line: call `poll()` every couple of seconds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ombro import jobs, mdfile, views
from ombro.home import Home
from ombro.ui import UI, Event, Failure, JobEnded, JobFile, JobProgress, JobStarted


class StartError(Exception):
    pass


def start_saved_spec(home: Home, spec: str, roles: list[str] | None = None, *, chat: str | None = None) -> str:
    """Start a saved spec in the background, as `ombro start --spec <spec> --detach [--role ...]` does,
    and return the job's id. `roles` apply to this job only. `chat` is the id of the chat it was
    started from, recorded on the job."""
    from ombro.init_cmd import connected_model
    from ombro.settings import SetupError, load_limits, load_tool_settings

    name = str(spec).strip().removesuffix(".md")
    path = home.specs_dir / f"{name}.md"
    if not name or "/" in name or not path.is_file():
        raise StartError(f"there's no saved spec called {spec!r}")
    try:
        jobs.reap_dead_jobs(home)
        spec_meta, spec_body = mdfile.read(path)
        if not spec_body.strip():
            raise StartError("the spec is empty")
        crews = jobs.spec_crews(home, spec_meta, [])
        source = f"spec file {path.resolve()}"
        if roles:
            spec_meta = {**spec_meta, "roles": list(dict.fromkeys(roles))}
            source += f"; roles for this job: {', '.join(roles)}"
        overrides = spec_meta.get("limits") or {}
        if not isinstance(overrides, dict):
            raise StartError("`limits` in a spec must be a mapping, e.g. limits: {allowance: generous}")
        tool_settings = load_tool_settings(home)
        limits = load_limits(home, overrides, tool_settings)
        if connected_model(home) is None:
            raise StartError("no model is connected; set Ombro up first")
        job_dir = jobs.create_job(home, crews, spec_meta, spec_body, limits, source=source,
                                  tools=tool_settings.to_dict())
    except (SetupError, mdfile.FrontMatterError, OSError) as e:
        raise StartError(str(e)) from None
    if chat:
        jobs.update_job(job_dir, chat=chat)
    jobs.start_detached(home, job_dir)
    return job_dir.name


def setup_needed(home: Home) -> str | None:
    """Why this home needs set-up, found without a model call (specification 10.1), or None."""
    if not home.root.is_dir():
        return "there is no Ombro home."
    try:
        from ombro.settings import load_models

        models = load_models(home)
    except Exception:  # noqa: BLE001
        return "no model is set in models.md."
    spec = models.get("default")
    if spec is None:
        return "no model is set in models.md."
    if spec.key:
        from ombro import keys

        if not keys.get_key(home, spec.key):
            return f"the model's key ({spec.key}) is not set."
    return None


# ---------------------------------------------------------------------------
# Jobs as events

PROGRESS = {"briefed": "briefed", "returned": "returned", "staged": "asked for approval",
            "outward_done": "did an outward action", "limit_reached": "hit a limit", "finish": "finished the work",
            "report_written": "wrote the report", "failed": "failed", "asked": "asked you",
            "answered": "answered", "unanswered": "had no answer, so carried on"}


# How much of a step's note the conversation shows; the app's side panel shows it whole (the
# journal keeps notes to about 180 characters).
CHAT_NOTE_CHARS = 80


def progress_line(job_id: str, ev: dict, cut: int | None = CHAT_NOTE_CHARS) -> str | None:
    what = PROGRESS.get(str(ev.get("action")))
    if not what:
        return None
    note = " ".join(str(ev.get("note") or "").split())
    if cut is not None and len(note) > cut:
        note = note[: cut - 1].rstrip() + "…"
    return f"{jobs.job_name(job_id)} · {ev.get('role', '')} {what}" + (f": {note}" if note else "")


def full_line(e: JobProgress) -> str:
    """A step as the side panel shows it: the whole note."""
    return progress_line(e.job_id, {"action": e.action, "role": e.role, "note": e.note}, cut=None) or e.text


def job_progress(job_id: str, ev: dict) -> JobProgress | None:
    """A journal entry worth showing, as an event; None for the rest (model calls and the like)."""
    line = progress_line(job_id, ev)
    if line is None:
        return None
    return JobProgress(job_id, jobs.job_name(job_id), str(ev.get("role") or ""), str(ev.get("action")),
                       " ".join(str(ev.get("note") or "").split()), line)


def ended_line(status: str, limit: str | None) -> str:
    """How a job ended, as Ombro says it in a notice."""
    if status == "completed":
        return "Your job has finished."
    if status == "stopped_by_limit":
        return f"Your job stopped at its {limit or 'job'} limit before finishing."
    if status == "stopped":
        return "Your job was stopped, as you asked."
    if status == "insufficient_brief":
        return "Your job needs more from you before it can start."
    return "Your job didn't finish: something went wrong."


def job_ended(home: Home, job_id: str) -> JobEnded:
    """The notice for a job that has ended: outcome, what it made, what waits for approval."""
    name = jobs.job_name(job_id)
    try:
        job_dir = jobs.find_job(home, job_id)
    except (jobs.JobNotFound, LookupError):
        return JobEnded(job_id, name, "unknown", f"Job {job_id} has ended.")
    r = views.report(home, job_dir)
    files = [JobFile(d["name"], d["path"], d["url"], d["exists"]) for d in r["deliverables"]
             if d["kind"] == "deliverable"]
    status = str(r["status"] or "unknown")
    return JobEnded(job_id, name, status, ended_line(status, r.get("limit_hit")), brief=r["brief"],
                    limit_hit=r.get("limit_hit"), files=files, waiting=r["waiting"])


class JobWatcher:
    """Follows jobs: new journal lines of the jobs it follows, and every job that ends.
    `poll()` returns the events since the last poll and whether the counts changed."""

    def __init__(self, home: Home):
        from ombro.journal import Follower

        self.home = home
        self._follower = Follower
        self.following: dict[str, Any] = {}
        self.active: set[str] = set()
        self.last_counts: dict[str, Any] = {}

    def start(self) -> None:
        """Take note of what is running now, so only what ends from here on gets a notice."""
        try:
            self.active = {r["job_id"] for r in views.job_list(self.home) if r["active"]}
        except Exception:  # noqa: BLE001 - a job being written
            self.active = set()

    def follow(self, job_id: str, job_dir: Path | None = None) -> None:
        """Show this job's progress as it goes (every job's end is noticed anyway)."""
        job_dir = job_dir or jobs.find_job(self.home, job_id)
        self.following[job_id] = self._follower(job_dir / "journal.jsonl")
        self.active.add(job_id)

    def follow_all(self) -> None:
        """Follow every active job, from what it has written so far: for an interface that shows them all."""
        for r in views.job_list(self.home):
            if r["active"] and r["job_id"] not in self.following:
                self.follow(r["job_id"], Path(r["path"]))

    def poll(self, *, mark_seen: bool = True) -> tuple[list[Event], bool] | None:
        """(events, counts changed), or None when the jobs couldn't be read this time."""
        try:
            rows = views.job_list(self.home)
        except Exception:  # noqa: BLE001 - a job being written; try at the next tick
            return None
        events: list[Event] = []
        for job_id, follower in list(self.following.items()):
            for ev in follower.new_events():
                e = job_progress(job_id, ev)
                if e:
                    events.append(e)
        active = {r["job_id"] for r in rows if r["active"]}
        for job_id in sorted(self.active - active):
            self.following.pop(job_id, None)
            events.append(job_ended(self.home, job_id))
            if mark_seen:
                try:
                    jobs.mark_seen(jobs.find_job(self.home, job_id))
                except (LookupError, OSError):
                    pass
        # Unseen too: a job that died is marked failed and given its report a moment after it stops
        # counting as active, and the menu bar's alert must hear of that.
        counts = {"active": sorted(active), "waiting": sum(r["waiting"] for r in rows), "jobs": len(rows),
                  "unseen": sum(1 for r in rows if views.is_unseen(r))}
        changed = counts != self.last_counts or active != self.active
        self.active = active
        self.last_counts = counts
        return events, changed


# ---------------------------------------------------------------------------
# The session


class Session:
    """One conversation: the chat, and what happens around it.

    `start_job(spec, roles)` starts a job and returns its id (or None after saying why);
    an interface may pass its own. `on_started(job_id)` is called once a job is going,
    so the interface can follow it."""

    def __init__(self, home: Home, ui: UI, *, client_factory: Callable | None = None, titles: bool = True,
                 start_job: Callable[[str, list[str]], str | None] | None = None,
                 on_started: Callable[[str], None] | None = None):
        self.home = home
        self.ui = ui
        self.client_factory = client_factory
        # set-up and model flows build clients from (spec, home)
        self.init_client_factory = (lambda spec, h: client_factory(spec)) if client_factory else None
        self.titles = titles
        self.chat = None
        self.started: list[str] = []
        self._start_job = start_job or self.start_job
        self.on_started = on_started or (lambda job_id: None)

    def ensure_chat(self):
        if self.chat is None:
            from ombro.chat import Chat

            kw = {"client_factory": self.client_factory} if self.client_factory else {}
            self.chat = Chat(self.home, ui=self.ui, titles=self.titles, **kw)
        return self.chat

    def job_ended(self, e: JobEnded) -> bool:
        """A job started from this session's chat has ended: set down in the chat. True if it was."""
        if self.chat is None or e.job_id not in self.chat.jobs:
            return False
        self.chat.job_ended(e.job_id, e.text, e.status)
        return True

    def chat_turn(self, text: str) -> bool:
        """One message or chat command. Starts what the user said yes to. False when the chat has ended."""
        chat = self.ensure_chat()
        specs_before = len(chat.specs)
        go_on = chat.handle(text)
        if not go_on:
            return False
        if len(chat.specs) > specs_before:
            self.offer_start(chat.specs[-1])
        while chat.start_offers:  # the user already said yes in chat
            name, roles = chat.start_offers.pop(0)
            self.offer_start(name, roles=roles, ask=False)
        return True

    def offer_start(self, spec: str, shown: str | None = None, *, roles: list[str] | None = None,
                    ask: bool = True) -> str | None:
        """Start a saved spec now, in the background, after the user's yes: after a confirmed brief, on /start,
        or when the Lead offered it in chat and the user said yes there (specification 10.3, 10.4)."""
        if shown:
            self.ui.say(f"Saved spec {shown}")
        if ask and not self.ui.yes("start_now", "Start it now? It works in the background, and its progress "
                                                "shows here.", True):
            self.ui.say(f"Start it later with `ombro start --spec {spec}`.")
            return None
        job_id = self._start_job(spec, roles or [])
        if not job_id:
            return None
        self.started.append(job_id)
        if self.chat is not None:
            self.chat.note_job(job_id)
        self.on_started(job_id)
        name = jobs.job_name(job_id)
        self.ui.show(JobStarted(job_id, name,
                                f"Started '{name}' ({job_id}). Keep chatting; its progress and its notice "
                                "appear here."))
        return job_id

    def start_job(self, spec: str, roles: list[str] | None = None) -> str | None:
        """Start a saved spec; on failure, say why and return None."""
        chat_id = self.chat.id if self.chat is not None else None
        try:
            return start_saved_spec(self.home, spec, roles, chat=chat_id)
        except StartError as e:
            self.ui.show(Failure(f"The job could not start: {e}."))
            return None

    def undo_unanswered(self) -> None:
        """After an interruption: a message the Lead never answered is taken back."""
        chat = self.chat
        if chat is not None and chat.exchanges and chat.exchanges[-1][-1].get("role") == "user":
            chat.exchanges.pop()

    def save(self) -> None:
        if self.chat is not None:  # a chat with nothing in it is never written
            try:
                self.chat._save()
            except OSError:
                pass


def record_job_end_in_chat(home: Home, e: JobEnded) -> bool:
    """A job ended while the chat that started it isn't open: its outcome is added to that chat's file,
    so the chat has it when it is opened again. True if a chat was found."""
    if not home.chats_dir.is_dir():
        return False
    for path in sorted(home.chats_dir.glob("*.md"), reverse=True):
        try:
            meta, body = mdfile.read(path)
        except (OSError, mdfile.FrontMatterError):
            continue
        if e.job_id not in [str(j) for j in meta.get("jobs") or []]:
            continue
        line = (f"**Ombro:** {e.text} ('{jobs.job_name(e.job_id)}', job {e.job_id}; its report is "
                f"jobs/{e.job_id}/report.md.)")
        if line in body:
            return True
        mdfile.write(path, meta, body.rstrip("\n") + "\n\n" + line + "\n")
        return True
    return False
