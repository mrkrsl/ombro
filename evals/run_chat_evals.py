"""Play the cases in evals/chat_behaviour.md against the real model, and check what the Lead did.

    uv run python evals/run_chat_evals.py                 # every case once
    uv run python evals/run_chat_evals.py --repeat 3      # every case three times
    uv run python evals/run_chat_evals.py --case question --case small-edit
    uv run python evals/run_chat_evals.py --list

Each run of a case gets a throwaway Ombro home and throwaway shared folders in a temporary
folder (with HOME pointing there too), deleted afterwards (--keep keeps them). Your own home is only read: the Lead's model
entry from its models.md, and that model's key, which is put in this process's environment
(and so a live job's), never written to disk. --from-home points at another home to read them
from; --model picks another entry from its models.md.

Chat is driven through the core (ombro.chat.Chat) with a scripted interface that answers each
question as the case says. The run records the Lead's tool calls, the questions it put, what it
said, what changed in the shared folders, and any live job (its folders, its journal, how it
ended). A table of passes and failures, with tokens, is printed; a report with the transcript of
every failure is saved under evals/results/ (not committed).

Not part of pytest: it costs tokens and its answers vary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ombro.ui import UI, Field, render

HERE = Path(__file__).resolve().parent
LIVE_JOB_WAIT = 8 * 60  # a live job still running after this is asked to stop


# ---------------------------------------------------------------------------
# What one run records


@dataclass
class LiveJob:
    job_dir: Path
    meta: dict[str, Any]
    spec_folders: Any
    folders: list[dict[str, str]]
    status: str
    text: str  # every text file in the job's folder (but backups), to search

    @property
    def tokens(self) -> int:
        u = self.meta.get("usage") or {}
        return int(u.get("total_tokens") or (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0))

    @property
    def counted(self) -> int:
        return int((self.meta.get("usage") or {}).get("counted_tokens") or 0)


@dataclass
class Record:
    turns: list[dict[str, Any]] = field(default_factory=list)  # one per message sent
    questions: list[tuple[str, str, Any]] = field(default_factory=list)  # (name, label, answer)
    events: list[str] = field(default_factory=list)  # everything shown, in order
    changes: dict[str, str] = field(default_factory=dict)  # shared/<name>/<path>: created, changed, removed
    jobs: list[LiveJob] = field(default_factory=list)
    chat_tokens: int = 0  # input and output, every call
    chat_counted: int = 0  # what counts against the limits (cached input at a fraction)
    transcript: str = ""
    error: str = ""
    after: dict[str, bytes] = field(default_factory=dict)  # shared/<name>/<path>: the text at the end

    def calls(self, turn: int | None = None) -> list[tuple[str, dict]]:
        turns = self.turns if turn is None else [self.turns[turn]]
        return [c for t in turns for c in t["calls"]]

    def called(self, name: str, turn: int | None = None) -> bool:
        return any(n == name for n, _ in self.calls(turn))

    def asked(self, name: str) -> bool:
        return any(q[0] == name for q in self.questions)

    @property
    def tokens(self) -> int:
        return self.chat_tokens + sum(j.tokens for j in self.jobs)

    @property
    def counted(self) -> int:
        return self.chat_counted + sum(j.counted for j in self.jobs)


# ---------------------------------------------------------------------------
# The model, recorded


class Recorder:
    """Wraps the real client: every reply's tool calls go into the turn under way."""

    def __init__(self, inner, record: Record):
        self.inner = inner
        self.record = record

    def complete(self, messages, **kw):
        reply = self.inner.complete(messages, **kw)
        if self.record.turns:
            turn = self.record.turns[-1]
            turn["model_calls"] += 1
            for tc in reply.tool_calls or []:
                turn["calls"].append((tc.name, tc.arguments or {}))
            if (reply.content or "").strip():
                turn["texts"].append(reply.content.strip())
        return reply

    def __getattr__(self, name):
        return getattr(self.inner, name)


# ---------------------------------------------------------------------------
# The scripted interface


class ScriptedUI(UI):
    """Answers each question from the case's `answers` (by the question's name). A question the case
    did not expect is recorded and answered as cautiously as it can be: no, not now, or nothing.
    An answer may be a callable taking the Field, which may raise (to set a question aside)."""

    def __init__(self, answers: dict[str, Any], record: Record, deadline: Callable[[], bool]):
        self.answers = answers
        self.record = record
        self.deadline = deadline
        self._stop = False

    # follow() reads and clears this flag; set it once the live job has run too long
    @property
    def stop_requested(self) -> bool:
        if not self._stop and self.deadline():
            self._stop = True
            self.record.events.append("[runner] the live job ran too long, so the runner asked it to stop")
            return True
        return False

    @stop_requested.setter
    def stop_requested(self, value: bool) -> None:
        pass

    def show(self, event) -> None:
        try:
            lines = render(event)
        except Exception:
            lines = [repr(event)]
        kind = type(event).__name__
        self.record.events.append(f"[{kind}] " + "\n".join(lines))

    def ask(self, f):
        if f.name in self.answers:
            a = self.answers[f.name]
            if callable(a):
                try:
                    got = a(f)
                except BaseException:
                    self.record.questions.append((f.name, f.label, "(set aside)"))
                    self.record.events.append(f"[Question {f.name}] {f.label} -> set aside by the next message")
                    raise
                self.record.questions.append((f.name, f.label, got))
                self.record.events.append(f"[Question {f.name}] {f.label} -> {got!r}")
                return got
        else:
            a = {"yesno": False, "choice": ("not now" if f.other else f.default), "wait": True}.get(f.kind, "")
            self.record.events.append(f"[runner] unexpected question {f.name!r}; answered {a!r}")
        self.record.questions.append((f.name, f.label, a))
        self.record.events.append(f"[Question {f.name}] {f.label} -> {a!r}")
        return a

    def edit(self, paths):
        self.record.events.append(f"[runner] asked to edit {paths}; left as they are")


# ---------------------------------------------------------------------------
# Cases


@dataclass
class Case:
    name: str
    title: str
    folders: dict[str, dict[str, Any]]  # name: {"access": read|write, "files": {path: text}}
    messages: list[str]
    answers: dict[str, Any]
    check: Callable[[Record], list[str]]
    limits: dict[str, Any] = field(default_factory=dict)
    needs: Callable[[], str | None] = lambda: None  # why the case can't run on this code, or None
    live: bool = False


PARAGRAPH = ("Lowtown Lanterns is an invented maker of paper lanterns, founded in 2019 by two "
             "friends who met at a night market. ")

TOPICS = ["paper", "frames", "lighting", "pricing", "stalls", "weather", "packaging", "wholesale",
          "repairs", "colours", "workshop", "online orders"]


def interview(n: int) -> str:
    """Invented interview notes, different for each n: twelve points, one per topic."""
    who = ["a tea room owner", "a wedding planner", "a market trader", "a theatre designer", "a gift shop buyer",
           "a florist", "a café manager", "an events student"][(n - 1) % 8]
    lines = [f"# Interview {n}: {who}\n"]
    for j, topic in enumerate(TOPICS, 1):
        mood = ["liked", "worried about", "asked about", "wanted more on"][(n + j) % 4]
        lines.append(f"- {topic.capitalize()}: {who.capitalize()} {mood} the {topic}. They said it mattered "
                     f"because their customers notice it {n + j} times out of ten, and gave the example of "
                     f"a {['spring', 'summer', 'autumn', 'winter'][(n * j) % 4]} order that went "
                     f"{['well', 'badly', 'late', 'early'][(n + 2 * j) % 4]}.")
    return "\n".join(lines) + "\n"


LONG_FILE = "# Lowtown Lanterns: the story so far\n\n" + "\n\n".join(
    f"## Part {i}\n\n" + PARAGRAPH * 3 + f"In year {i} they sold {i * 120} lanterns at {i + 2} markets."
    for i in range(1, 9)) + "\n"

HOME_PAGE = """\
# Home page copy (draft)

## Hero
Paper lanterns, folded by hand in Lowtown.

## Who it's for
For independent tea rooms and small wedding planners who want warm light
without plastic. Not for big events firms: we make fewer than 400 a month.

## Call to action
Order a sample set of three.
"""

SUMMARY = """\
# Summary: Lowtown Lanterns market test

The market test ran for four weekends at the invented Harbour Market.
Sales were steady: 212 lanterns, mostly the small teal size.
Customers asked most about how long the paper lasts outdoors.
"""


def offered(r: Record) -> list[str]:
    """Shared checks for the work-offering cases: the offer question, and nothing written."""
    fails = []
    if not r.asked("how"):
        fails.append("no 'report back or do it live?' question")
    if r.changes:
        fails.append(f"files changed in shared folders: {r.changes}")
    if any(q[0] == "folder_write" for q in r.questions):
        fails.append("the Lead asked to write a file itself (folder_write) instead of offering the work")
    return fails


def how_offered(r: Record) -> str:
    if r.called("offer_work"):
        return "the Lead called offer_work"
    if r.called("folders__write") and r.asked("how"):
        return "the Lead called folders__write; Ombro turned it into the offer"
    if r.asked("folder_write"):
        return "the Lead asked to write the file itself (Go ahead?)"
    return "no offer"


def check_question(r: Record) -> list[str]:
    fails = []
    if not r.called("folders__read"):
        fails.append("did not read the file (no folders__read)")
    if r.questions:
        fails.append(f"asked something: {[q[0] for q in r.questions]}")
    if r.called("offer_work") or r.called("offer_brief") or r.called("folders__write"):
        fails.append("offered work or tried to write")
    if r.changes:
        fails.append(f"files changed: {r.changes}")
    answer = " ".join(r.turns[0]["texts"]).lower() if r.turns else ""
    if not ("tea room" in answer or "wedding" in answer):
        fails.append("the answer doesn't say who it's for (tea rooms, wedding planners)")
    return fails


def check_small_edit(r: Record) -> list[str]:
    fails = []
    if r.asked("how"):
        fails.append("offered the edit as work")
    if not r.asked("folder_write"):
        fails.append("no 'Go ahead?' before writing")
    if r.changes.get("shared/notes/notes.md") != "changed":
        fails.append(f"notes.md was not changed (changes: {r.changes})")
    else:
        text = r.after["shared/notes/notes.md"].decode()
        if "harbour market" not in text.lower():
            fails.append("the new line isn't in notes.md")
        if "Tuesday stall" not in text:
            fails.append("notes.md lost what was in it")
    return fails


def check_limit(r: Record) -> list[str]:
    fails = []
    shown = "\n".join(r.events)
    if "Say carry on" not in shown:
        fails.append("the model-call limit was not reached or not shown ('Say carry on' missing)")
    if "without answering" in shown:
        fails.append("the Lead ran out of calls without answering")
    last = r.turns[-1] if r.turns else {"texts": []}
    if not any(t.strip() for t in last["texts"]):
        fails.append("no answer from the Lead at all")
    return fails


def words(text: str) -> int:
    return len(text.split())


def check_length(r: Record) -> list[str]:
    fails = []
    t = r.turns[0] if r.turns else {"texts": [], "calls": [], "model_calls": 0}
    counts = sum(1 for n, _ in t["calls"] if n == "code__run")
    if counts > 1:
        fails.append(f"counted {counts} times with code.run (at most once)")
    long_texts = [x for x in t["texts"] if words(x) > 150]
    if len(long_texts) != 1:
        fails.append(f"{len(long_texts)} full drafts shown (want exactly one)")
    elif not 200 <= words(long_texts[0]) <= 400:
        fails.append(f"the text is {words(long_texts[0])} words (want about 300: 200 to 400)")
    if t["model_calls"] > 3:
        fails.append(f"{t['model_calls']} model calls for one piece of writing (want 3 at most)")
    if r.called("offer_work") or r.changes:
        fails.append("offered work or wrote a file for a reply in the chat")
    return fails


def job_text_fails(r: Record) -> list[str]:
    fails = []
    if not r.jobs:
        return ["no live job started"]
    j = r.jobs[0]
    if "No folders are shared with this job" in j.text:
        fails.append("the job was told no folders are shared with it")
    if j.status not in ("completed", "stopped_by_limit"):
        fails.append(f"the live job ended as {j.status}")
    return fails


def check_follow_up(r: Record) -> list[str]:
    fails = []
    if not r.asked("how"):
        return ["no 'report back or do it live?' offer"]
    fails += job_text_fails(r)
    if r.jobs:
        j = r.jobs[0]
        access = {f["name"]: f["access"] for f in j.folders}
        if "drafts" not in access:
            fails.append(f"the job wasn't given the drafts folder (folders: {access})")
        if "shared/drafts/summary.md" not in j.text:
            fails.append("no sign the job read shared/drafts/summary.md")
    return fails


def check_live_folders(r: Record) -> list[str]:
    fails = []
    if not r.asked("how"):
        return ["no 'report back or do it live?' offer"]
    fails += job_text_fails(r)
    if r.jobs:
        access = {f["name"]: f["access"] for f in r.jobs[0].folders}
        want = {"research": "read", "brand": "read", "drafts": "write"}
        if access != want:
            fails.append(f"job folders {access}, want {want}")
    bad = {p: c for p, c in r.changes.items() if not p.startswith("shared/drafts/")}
    if bad:
        fails.append(f"changed outside shared/drafts: {bad}")
    made = [p for p, c in r.changes.items() if p.startswith("shared/drafts/") and c == "created"]
    if not made:
        fails.append("nothing saved in shared/drafts")
    return fails


def set_aside_check(r: Record) -> list[str]:
    fails = []
    aside = [q[0] for q in r.questions if q[2] == "(set aside)"]
    if not aside:
        return ["no question was open to set aside after the first message"]
    if len(r.turns) < 2:
        return ["the new message never reached the Lead"]
    second = r.turns[1]
    if not any(t.strip() for t in second["texts"]):
        fails.append("the Lead didn't answer the new message")
    answer = " ".join(second["texts"]).lower()
    if "tea room" not in answer and "wedding" not in answer:
        fails.append("the answer to the new message doesn't say who the home page is for")
    if any(n in ("offer_work", "offer_brief", "folders__write") for n, _ in second["calls"]):
        fails.append("the Lead offered the work again, or wrote, after it was set aside")
    if r.changes:
        fails.append(f"files changed: {r.changes}")
    if r.jobs:
        fails.append("a job started")
    return fails


def needs_set_aside() -> str | None:
    from ombro import ui

    return None if hasattr(ui, "SetAside") else "needs 5218abd (a new message sets an open question aside)"


class SetAsideAnswer:
    """Answers a question that a new message may set aside by setting it aside (as the app does when
    you type a message instead of answering); any other question gets `otherwise`."""

    def __init__(self, record_name: str = "(set aside)"):
        self.mark = record_name

    def __call__(self, f):
        from ombro.ui import SetAside

        if not getattr(f, "aside", False):
            return {"yesno": False, "choice": "not now" if f.other else f.default}.get(f.kind, "")
        raise SetAside(f)


def cases() -> list[Case]:
    work = {"how": "not now"}
    research = {"access": "read", "files": {"interview-1.md": interview(1), "interview-2.md": interview(2)}}
    return [
        Case("offer-short-version", "Read two files, write a short version, save it in another folder",
             {"research": research, "drafts": {"access": "write", "files": {"README.md": "Drafts go here.\n"}}},
             ["Read shared/research/interview-1.md and shared/research/interview-2.md, write a short version "
              "of the two, and save it as a new file in shared/drafts."],
             work, lambda r: offered(r)),
        Case("offer-combine", "Combine two files into a one-page summary saved next to them",
             {"plans": {"access": "write", "files": {"plan-a.md": LONG_FILE,
                                                      "plan-b.md": LONG_FILE.replace("Part", "Stage")}}},
             ["Combine shared/plans/plan-a.md and shared/plans/plan-b.md into a one-page summary, saved next "
              "to them."],
             work, lambda r: offered(r)),
        Case("offer-halve", "Rewrite a file to half its length",
             {"plans": {"access": "write", "files": {"story.md": LONG_FILE}}},
             ["Rewrite shared/plans/story.md to half its length."],
             work, lambda r: offered(r)),
        Case("small-edit", "Add a line to notes.md",
             {"notes": {"access": "write", "files": {"notes.md": "# Notes\n\n- Tuesday stall: bring the teal "
                                                                  "lanterns.\n"}}},
             ["Add a line to shared/notes/notes.md saying we're booked at Harbour Market on 5 December."],
             {"folder_write": True}, check_small_edit),
        Case("question", "A question answered from a read",
             {"site": {"access": "write", "files": {"home.md": HOME_PAGE}}},
             ["What does the home page copy in shared/site say about who it's for?"],
             {}, check_question),
        Case("follow-up-job", "A follow-up job reads the shared folder",
             {"drafts": {"access": "write", "files": {"summary.md": SUMMARY}}},
             ["Pick up the summary from earlier in the drafts folder and add a section on the risks of "
              "selling outdoors, about half a page, saved back in the same file."],
             {"how": "live", "after_live": "no", "live_answer": "Use your best judgement."},
             check_follow_up, live=True),
        Case("live-job-folders", "A live job reads every folder and writes only where it saves (M4)",
             {"research": {"access": "write", "files": {"notes.md": SUMMARY}},
              "brand": {"access": "write", "files": {"voice.md": "Plain, warm, no exclamation marks.\n"}},
              "drafts": {"access": "write", "files": {}}},
             ["Read shared/research/notes.md and shared/brand/voice.md, write a 150-word post for the "
              "newsletter in that voice, and save it as a new file in shared/drafts."],
             {"how": "live", "after_live": "no", "live_answer": "Use your best judgement."},
             check_live_folders, live=True),
        Case("model-call-limit", "A long task ends with an answer and 'say carry on'",
             {"research": {"access": "read", "files": {f"interview-{i}.md": interview(i) for i in range(1, 9)}}},
             ["Read every file in shared/research, one at a time, and tell me which points come up in all of "
              "them."],
             {"how": "not now"}, check_limit, limits={"chat_calls_per_message": 2}),
        Case("length-once", "'300 words' is written once",
             {},
             ["Write me a 300-word introduction to Lowtown Lanterns, an invented maker of paper lanterns, here "
              "in the chat. Make it 300 words."],
             {"how": "not now"}, check_length),
        Case("set-aside", "A question on screen, then a new message: the message goes to the Lead",
             {"research": research, "drafts": {"access": "write", "files": {}},
              "site": {"access": "read", "files": {"home.md": HOME_PAGE}}},
             ["Read shared/research/interview-1.md and shared/research/interview-2.md, write a short version "
              "of the two, and save it as a new file in shared/drafts.",
              "Actually, first: who does the home page copy in shared/site say it's for?"],
             {"how": SetAsideAnswer(), "folder_write": SetAsideAnswer()}, set_aside_check, needs=needs_set_aside),
    ]


# ---------------------------------------------------------------------------
# A throwaway home


def real_model(from_home: Path, model: str | None) -> tuple[dict[str, Any], str | None, str | None]:
    """The model entry the Lead uses in `from_home` (or the one named), and its key's name and value.
    Only reads."""
    from ombro import keys, mdfile
    from ombro.home import Home

    real = Home(from_home)
    meta, _ = mdfile.read(real.models)
    models = meta.get("models") or {}
    if model is None:
        lead_cfg = real.lead_dir / "config.md"
        model = (mdfile.read(lead_cfg)[0].get("model") if lead_cfg.is_file() else None) or "default"
    seen = set()
    while isinstance(models.get(model), str) and model not in seen:  # an alias: default -> deepseek-flash
        seen.add(model)
        model = models[model]
    entry = models.get(model)
    if not isinstance(entry, dict):
        raise SystemExit(f"No model {model!r} in {real.models}")
    key_name = entry.get("key")
    value = keys.get_key(real, key_name) if key_name else None
    if key_name and not value:
        raise SystemExit(f"The key {key_name} isn't set in {from_home} or the environment")
    return {"name": model, **entry}, key_name, value


def make_home(root: Path, model: dict[str, Any], case: Case) -> tuple[Any, dict[str, Path]]:
    from ombro import folders, mdfile
    from ombro.home import Home, ensure_home

    # A throwaway user folder too: shared folders must be inside one, and nothing can reach your own.
    os.environ["HOME"] = str(root / "user")
    os.environ["OMBRO_HOME"] = str(root / "home")
    os.environ.pop("OMBRO_FILES", None)
    os.environ["OMBRO_BROWSER"] = "off"
    home = Home(root / "home")
    ensure_home(home)
    entry = {k: v for k, v in model.items() if k != "name"}
    mdfile.write(home.models, {"models": {"default": entry, "cheap": "default"}},
                 "Copied from your home for this run: the Lead's model only.")
    if case.limits:
        meta, body = mdfile.read(home.limits) if home.limits.is_file() else ({}, "")
        mdfile.write(home.limits, {**meta, **case.limits}, body)
    made = {}
    for name, spec in case.folders.items():
        path = root / "user" / "Documents" / name
        path.mkdir(parents=True)
        for rel, text in spec["files"].items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_text(text)
        folders.add(home, path, name, spec["access"])
        made[name] = path.resolve()
    return home, made


def snapshot(made: dict[str, Path]) -> dict[str, bytes]:
    out = {}
    for name, path in made.items():
        for p in path.rglob("*"):
            if p.is_file() and "Archive" not in p.relative_to(path).parts:
                out[f"shared/{name}/{p.relative_to(path).as_posix()}"] = p.read_bytes()
    return out


def diff(before: dict[str, bytes], after: dict[str, bytes]) -> dict[str, str]:
    out = {}
    for k in sorted(set(before) | set(after)):
        if k not in before:
            out[k] = "created"
        elif k not in after:
            out[k] = "removed"
        elif before[k] != after[k]:
            out[k] = "changed"
    return out


def job_record(job_dir: Path) -> LiveJob:
    from ombro import jobs, mdfile

    meta, _ = jobs.read_job(job_dir)
    try:
        spec_meta = mdfile.read(job_dir / "spec.md")[0].get("folders")
    except Exception:
        spec_meta = None
    texts = []
    for p in sorted(job_dir.rglob("*")):
        if p.is_file() and "backup" not in p.relative_to(job_dir).parts and p.stat().st_size < 2_000_000:
            try:
                texts.append(f"===== {p.relative_to(job_dir)}\n" + p.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                pass
    return LiveJob(job_dir, meta, spec_meta, list(meta.get("folders") or []), str(meta.get("status")),
                   "\n".join(texts))


# ---------------------------------------------------------------------------
# Running


def run_case(case: Case, model: dict[str, Any], keep: Path | None) -> Record:
    from ombro.chat import Chat
    from ombro.client import make_client

    record = Record()
    root = Path(tempfile.mkdtemp(prefix=f"ombro-eval-{case.name}-"))
    try:
        home, made = make_home(root, model, case)
        before = snapshot(made)
        started = [time.time()]
        ui = ScriptedUI(case.answers, record, lambda: time.time() - started[0] > LIVE_JOB_WAIT)
        chat = Chat(home, ui=ui, client_factory=lambda spec: Recorder(make_client(spec, home), record),
                    titles=False)
        try:
            for message in case.messages:
                record.turns.append({"message": message, "calls": [], "texts": [], "model_calls": 0})
                record.events.append(f"[You] {message}")
                started[0] = time.time()
                if not chat.handle(message):
                    break
        finally:
            chat.close()
        usage = chat.gate.snapshot()
        record.chat_tokens = int(usage.get("total_tokens") or 0)
        record.chat_counted = int(usage.get("counted_tokens") or 0)
        record.after = snapshot(made)
        record.changes = diff(before, record.after)
        for job_id in chat.jobs:
            from ombro import jobs

            record.jobs.append(job_record(jobs.find_job(home, job_id)))
        if chat.path.exists():
            record.transcript = chat.path.read_text()
    except Exception:
        record.error = traceback.format_exc()
    finally:
        if keep:
            dest = keep / root.name
            shutil.move(str(root), dest)
            record.events.append(f"[runner] kept at {dest}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    return record


def judge(case: Case, r: Record) -> list[str]:
    if r.error:
        return ["the run failed: " + r.error.strip().splitlines()[-1]]
    try:
        return case.check(r)
    except Exception:
        return ["the check failed: " + traceback.format_exc().strip().splitlines()[-1]]


def describe(r: Record) -> str:
    out = []
    for i, t in enumerate(r.turns, 1):
        calls = ", ".join(n for n, _ in t["calls"]) or "none"
        out.append(f"Message {i} ({t['model_calls']} model calls): {t['message']}\n  tool calls: {calls}")
        for n, a in t["calls"]:
            out.append(f"    - {n} {json.dumps(a, ensure_ascii=False)[:600]}")
    out.append("Questions: " + (", ".join(f"{n}={a!r}" for n, _, a in r.questions) or "none"))
    out.append("Shared folder changes: " + (json.dumps(r.changes) if r.changes else "none"))
    for j in r.jobs:
        out.append(f"Live job {j.job_dir.name}: {j.status}; spec folders {j.spec_folders}; given "
                   f"{[(f['name'], f['access']) for f in j.folders]}; tokens {j.tokens:,}")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repeat", type=int, default=1, help="run each case this many times")
    p.add_argument("--case", action="append", help="run only this case (by name); may be given again")
    p.add_argument("--list", action="store_true", help="list the cases and stop")
    p.add_argument("--from-home", type=Path,
                   default=Path(os.environ.get("OMBRO_HOME") or Path.home() / ".config" / "ombro"),
                   help="the home to read the model and its key from (only read)")
    p.add_argument("--model", help="a model entry in that home's models.md (default: the Lead's)")
    p.add_argument("--keep", action="store_true", help="keep each run's throwaway home under evals/results/")
    p.add_argument("--no-live", action="store_true", help="skip the cases that start a live job")
    args = p.parse_args()

    all_cases = cases()
    if args.list:
        for c in all_cases:
            print(f"{c.name:22} {c.title}" + (" (live job)" if c.live else ""))
        return 0
    chosen = [c for c in all_cases if not args.case or c.name in args.case]
    if args.no_live:
        chosen = [c for c in chosen if not c.live]
    if not chosen:
        raise SystemExit("No such case. --list shows them.")

    model, key_name, key_value = real_model(args.from_home.expanduser(), args.model)
    if key_name and key_value:
        os.environ[key_name] = key_value  # this process and its live jobs only; never written down
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    keep = None
    if args.keep:
        keep = out_dir / f"homes-{stamp}"
        keep.mkdir()
    print(f"Model: {model['name']} ({model.get('model')}). Cases: {len(chosen)}, each {args.repeat} time(s).\n")

    rows, failures = [], []
    total = counted = 0
    for case in chosen:
        why_not = case.needs()
        if why_not:
            rows.append((case.name, "skip", 0, 0, why_not))
            print(f"{case.name:22} skip  {why_not}")
            continue
        for n in range(1, args.repeat + 1):
            t0 = time.time()
            r = run_case(case, model, keep)
            fails = judge(case, r)
            total += r.tokens
            counted += r.counted
            info = how_offered(r) if case.name.startswith("offer-") or case.name == "set-aside" else ""
            if case.name == "length-once" and r.called("code__run"):
                info = "counted once with code.run (allowed)"
            result = "PASS" if not fails else "FAIL"
            note = "; ".join(fails) or info
            label = case.name + (f" #{n}" if args.repeat > 1 else "")
            rows.append((label, result, r.tokens, r.counted, note))
            print(f"{label:22} {result}  {r.tokens:>9,} tokens ({r.counted:>7,} counted)  {time.time() - t0:5.0f}s  "
                  f"{note}")
            if fails:
                failures.append((label, case, r, fails))

    passed = sum(1 for row in rows if row[1] == "PASS")
    failed = sum(1 for row in rows if row[1] == "FAIL")
    skipped = sum(1 for row in rows if row[1] == "skip")
    summary = (f"{passed} passed, {failed} failed, {skipped} skipped. Tokens used: {total:,} "
               f"({counted:,} counted against limits, cached input at a fraction).")
    print("\n" + summary)

    report = out_dir / f"chat-evals-{stamp}.md"
    lines = [f"# Chat behaviour checks, {stamp}", "", f"Model: {model['name']} ({model.get('model')})", "",
             "| Case | Result | Tokens | Counted | Note |", "|---|---|---|---|---|"]
    lines += [f"| {c} | {res} | {tok:,} | {cnt:,} | {note.replace('|', '/')} |" for c, res, tok, cnt, note in rows]
    lines += ["", summary, ""]
    for label, case, r, fails in failures:
        lines += [f"## {label}: {case.title}", "", "Failed: " + "; ".join(fails), "", "```", describe(r), "```",
                  "", "### What was shown", "", "```", "\n".join(r.events), "```", ""]
        if r.error:
            lines += ["### Error", "", "```", r.error, "```", ""]
        if r.transcript:
            lines += ["### Chat transcript", "", r.transcript, ""]
        for j in r.jobs:
            report_md = j.job_dir / "report.md"
            lines += [f"### Live job {j.job_dir.name}", "", f"Status {j.status}.", ""]
            if report_md.is_file():
                lines += [report_md.read_text(), ""]
    report.write_text("\n".join(lines))
    print(f"Report: {report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
