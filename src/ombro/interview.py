"""Interviews: the Lead's structured conversation with the user.

One mechanism with three uses (set-up, adding a role, briefing a task). The
Lead asks questions in plain text and proposes the files it would write with
the `propose` tool. The harness checks the proposal, shows it, and asks the
user to accept it, ask for a change, edit it by hand, or quit. Only the
harness writes files, and only after the user accepts.

Every model call goes through its own Gate, under the interview limits in
limits.md, so interview usage never touches a run's. Anything the user types
is scrubbed of keys before it reaches the model or the transcript.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ombro import keys, mdfile, prompts
from ombro.client import ChatClient, ModelError, estimate_tokens, make_client
from ombro.gate import Gate, LimitReached
from ombro.home import Home
from ombro.journal import utc_now
from ombro.settings import Limits, ModelSpec, format_tokens, load_models, load_preferences
from ombro.ui import BLOCK, UI, ConsoleUI, Failure, Field, FileView, LeadSaid, Note, Option, Progress, \
    ProposalShown, Usage, read_answer  # noqa: F401  (read_answer and BLOCK are used by chat)

# The Lead may make this many model calls in a row without the user answering.
MAX_CALLS_WITHOUT_ANSWER = 5
QUIT_WORDS = ("/quit", "/q")
END_WORD = "/end"


class ProposalError(ValueError):
    """A proposal the harness refuses. The message goes back to the Lead."""


@dataclass
class FileDraft:
    label: str  # how the file is named to the user, e.g. preferences/instructions.md
    path: Path  # where it will be written
    text: str


@dataclass
class Proposal:
    files: list[FileDraft]
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)  # anything the purpose needs after accepting


@dataclass
class Outcome:
    status: str  # confirmed | insufficient | quit | limit | failed
    proposal: Proposal | None = None
    gaps: list[str] = field(default_factory=list)
    detail: str = ""
    transcript: Path | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    edited: bool = False
    answers: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


class Purpose:
    """What one kind of interview asks for, and how its files are built and checked."""

    kind = ""
    title = ""
    intro = ""  # what the user is told as the interview starts; built from title and typical if empty
    proposal_intro = ("Here's what I've written. Have a read through. You can then choose one of the options "
                      "at the end to save it, ask me to change it, or edit it yourself.")
    rules = ""
    can_report_gaps = False
    accept_label = "write these files"
    topics: list[tuple[str, str]] = []  # (key, label) the Lead reports progress against
    typical = ""  # e.g. "usually 4 to 6 questions"

    def context(self) -> list[tuple[str, str]]:
        return []

    def accept_label_for(self, proposal: "Proposal") -> str:
        return self.accept_label

    def opening(self) -> str:
        return "Begin the interview."

    def tool(self) -> dict:
        raise NotImplementedError

    def build(self, args: dict) -> Proposal:
        """Turn the Lead's proposal into files. Raise ProposalError to refuse it."""
        raise NotImplementedError

    def check(self, proposal: Proposal) -> Proposal:
        """Check final file texts (also after a hand edit). Raise ProposalError to refuse."""
        return proposal

    def before_write(self, proposal: Proposal, ui: UI) -> Proposal:
        """Questions only the harness asks once the user has accepted (such as sharing a
        folder). Returns the proposal to write."""
        return proposal

    def write(self, proposal: Proposal) -> None:
        for f in proposal.files:
            keep_previous(f.path)
            mdfile.write_atomic(f.path, f.text)


def keep_previous(path: Path) -> Path | None:
    """Before a file is replaced, keep its old text next to it as name.previous.md."""
    if not path.is_file():
        return None
    backup = path.with_name(f"{path.stem}.previous{path.suffix}")
    shutil.copy2(path, backup)
    return backup


# ---------------------------------------------------------------------------
# Editors


def editor_command(home: Home) -> list[str] | None:
    pref = load_preferences(home).editor.strip()
    cmd = pref or os.environ.get("VISUAL", "").strip() or os.environ.get("EDITOR", "").strip()
    return shlex.split(cmd) if cmd else None


def open_in_editor(home: Home, paths: list[Path], ui: UI) -> None:
    """Open files in the editor from preferences (or $VISUAL / $EDITOR) and wait.
    With no editor set, show the paths and wait for Enter."""
    cmd = editor_command(home)
    if cmd:
        try:
            subprocess.run([*cmd, *map(str, paths)], check=False)
            return
        except OSError as e:
            ui.say(f"Could not start the editor {cmd[0]!r}: {e}")
    ui.say("No editor is set (`editor` in preferences/config.md, or $EDITOR). Edit these files yourself:")
    for p in paths:
        ui.say(f"  {p}")
    ui.ask(Field("saved", "Press Enter when you have saved them.", "wait"))


# ---------------------------------------------------------------------------
# Transcript


class Transcript:
    def __init__(self, kind: str, crew: str):
        self.kind = kind
        self.crew = crew
        self.started = utc_now()
        self.entries: list[str] = []

    def lead(self, text: str) -> None:
        self.entries.append(f"**Lead:** {text.strip()}")

    def user(self, text: str) -> None:
        self.entries.append(f"**You:** {text.strip()}")

    def note(self, text: str) -> None:
        self.entries.append(f"_{text.strip()}_")

    def proposal(self, proposal: Proposal, heading: str = "Lead proposed") -> None:
        parts = [f"**{heading}:**"]
        for f in proposal.files:
            parts += ["", f"`{f.label}`", "", "````markdown", f.text.rstrip(), "````"]
        self.entries.append("\n".join(parts))

    def render(self, meta: dict[str, Any]) -> str:
        return mdfile.dump(meta, f"# Interview: {self.kind}\n\n" + "\n\n".join(self.entries) + "\n")


def interviews_dir(home: Home, crew: str = "") -> Path:
    return home.interviews_dir


# ---------------------------------------------------------------------------
# The loop


class Interview:
    def __init__(
        self,
        home: Home,
        purpose: Purpose,
        limits: Limits,
        *,
        crew: str,
        ask: Callable[[str], str] | None = None,
        say: Callable[[str], None] | None = None,
        edit: Callable[[list[Path]], None] | None = None,
        ui: UI | None = None,
        client_factory: Callable[[ModelSpec], ChatClient] | None = None,
        clock: Callable[[], float] = time.time,
        today: date | None = None,
        lead_soul: str = "",
        instructions: str = "",
        model: str = "default",
    ):
        self.home = home
        self.purpose = purpose
        self.crew = crew
        self.ui = ui or ConsoleUI(ask=ask, say=say, edit=edit, home=home)
        self.limits = limits.for_interview()
        self.models = load_models(home)
        if model not in self.models:
            model = "default"
        self.spec = self.models[model]
        self._client_factory = client_factory or (lambda spec: make_client(spec, home))
        self.gate = Gate(self.limits, clock)
        self.today = prompts.format_date(today or date.today())
        self.lead_soul = lead_soul
        self.instructions = instructions
        self.transcript = Transcript(purpose.kind, crew)
        self.gaps: list[str] = []
        self._client: ChatClient | None = None
        self.answers: list[str] = []

    # -- helpers -----------------------------------------------------------

    def _limits_line(self) -> str:
        return (
            f"- tokens for the whole interview: {format_tokens(self.limits.max_tokens)}\n"
            f"- answers from the user: {self.limits.max_rounds}"
        )

    def _input(self, label: str) -> str:
        """One answer from the user, scrubbed of keys. Raises EOFError/KeyboardInterrupt to quit."""
        while True:
            raw = self.ui.ask(Field("answer", label, "text", multiline=True))
            text, removed = keys.scrub(self.home, (raw or "").strip())
            if removed:
                self.ui.say(
                    "(Something that looked like a key was removed before it reached the model. "
                    "Keys are set with `ombro keys set NAME`, never in an interview.)"
                )
            if text:
                return text

    def _answer(self, label: str = "You") -> str | None:
        """The user's next answer, counted against the interview's answer cap. None means quit."""
        text = self._input(label)
        if text.lower() in QUIT_WORDS:
            return None
        self.gate.check_round()
        self.gate.record_round()
        self.answers.append(text)
        return text

    def _model_call(self, messages: list[dict], tools: list[dict]):
        est = estimate_tokens(messages, tools)
        max_out, timeout = self.gate.check_model_call(self.spec.name, est, None)
        if self._client is None:
            self._client = self._client_factory(self.spec)
        try:
            reply = self._client.complete(messages, tools=tools, max_output_tokens=max_out,
                                          timeout=min(timeout, 300))
        except ModelError as e:
            self.gate.record_lost_calls(self.spec.name, e.lost_attempts, est, max_out)
            raise
        self.gate.record_lost_calls(self.spec.name, reply.lost_attempts, est, max_out)
        u = reply.usage
        in_tokens = u.input_tokens if u.input_tokens or not u.estimated else est
        out_tokens = min(u.output_tokens, max_out) if u.estimated else u.output_tokens
        self.gate.record_model_call(self.spec.name, in_tokens, out_tokens, estimated=u.estimated)
        snap = self.gate.snapshot()
        self.ui.show(Usage("interview", self.purpose.kind, snap.get("total_tokens", 0), self.limits.max_tokens,
                           sum((snap.get("tool_calls") or {}).values())))
        return reply

    def _show(self, proposal: Proposal) -> None:
        self.ui.show(proposal_view(proposal))

    # -- the conversation --------------------------------------------------

    def run(self) -> Outcome:
        p = self.purpose
        tools = [prompts.ask_tool([k for k, _ in p.topics]), p.tool()]
        tools += [prompts.INSUFFICIENT_TOOL] if p.can_report_gaps else []
        system = prompts.interview_system(
            p.rules,
            self.lead_soul,
            self.instructions,
            p.context(),
            self._limits_line(),
            self.today,
            can_report_gaps=p.can_report_gaps,
        )
        opening, removed = keys.scrub(self.home, p.opening())
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": opening}]
        self.ui.say(p.intro or (
            f"{p.title}" + (f" It is {p.typical}." if p.typical else "")
            + " Type /quit to stop; nothing is written unless you accept it."
        ))
        try:
            outcome = self._loop(messages, tools)
        except LimitReached as e:
            self.transcript.note(f"Stopped by the interview limit {e.limit}: {e.detail}")
            self.ui.show(Failure(f"The interview stopped: {e.detail}. Nothing was written."))
            outcome = Outcome("limit", gaps=list(self.gaps), detail=f"{e.limit}: {e.detail}")
        except ModelError as e:
            self.transcript.note(f"The model call failed: {e}")
            self.ui.show(Failure(f"The model call failed: {e}. Nothing was written."))
            outcome = Outcome("failed", detail=str(e))
        except (KeyboardInterrupt, EOFError):
            self.transcript.note("You stopped the interview.")
            self.ui.say("Interview stopped. Nothing was written.", spaced=True)
            outcome = Outcome("quit", detail="stopped by you")
        outcome.usage = self.gate.snapshot()
        outcome.answers = list(self.answers)
        outcome.transcript = self._save_transcript(outcome)
        self.ui.show(Usage("interview", self.purpose.kind, outcome.usage.get("total_tokens", 0),
                           self.limits.max_tokens, sum((outcome.usage.get("tool_calls") or {}).values()), final=True))
        return outcome

    def _loop(self, messages: list[dict], tools: list[dict]) -> Outcome:
        calls_without_answer = 0
        while True:
            if calls_without_answer >= MAX_CALLS_WITHOUT_ANSWER:
                raise LimitReached(
                    "interview_steps",
                    f"the Lead made {MAX_CALLS_WITHOUT_ANSWER} model calls in a row without a question for you",
                )
            reply = self._model_call(messages, tools)
            calls_without_answer += 1
            messages.append(reply.as_message())
            text = (reply.content or "").strip()
            if text:
                self.ui.show(LeadSaid(text))
                self.transcript.lead(text)
            if not reply.tool_calls:
                if not text:
                    messages.append({"role": "user", "content": prompts.INTERVIEW_NUDGE})
                    continue
                answer = self._answer()
                if answer is None:
                    raise KeyboardInterrupt
                self.transcript.user(answer)
                messages.append({"role": "user", "content": answer})
                calls_without_answer = 0
                continue
            for i, tc in enumerate(reply.tool_calls):
                if i > 0:
                    messages.append(_tool_reply(tc.id, {"error": "one tool call at a time; this one was ignored"}))
                    continue
                result, outcome = self._tool(tc)
                if outcome is not None:
                    return outcome
                if result.pop("_answered", False):
                    calls_without_answer = 0
                messages.append(_tool_reply(tc.id, result))

    def _tool(self, tc) -> tuple[dict, Outcome | None]:
        args_key = tc.raw_arguments if tc.arguments is None else json.dumps(tc.arguments, sort_keys=True)
        self.gate.check_tool_call("lead", tc.name, args_key, capped=False)  # loop detection
        if tc.arguments is None:
            return {"error": "the arguments were not a JSON object"}, None
        if tc.name == "ask":
            return self._ask(tc.arguments)
        if tc.name == "propose":
            return self._propose(tc.arguments)
        if tc.name == "insufficient" and self.purpose.can_report_gaps:
            return self._insufficient(tc.arguments)
        return {"error": f"unknown tool {tc.name!r}"}, None

    def progress_line(self, args: dict) -> str:
        """Topics covered, what is left, and how far through: worked out from the
        topics, so the words always agree with the count."""
        keys_ = [k for k, _ in self.purpose.topics]
        if not keys_:
            return ""
        covered = [k for k in keys_ if k in (args.get("covered") or [])]
        labels = dict(self.purpose.topics)
        bar = "●" * len(covered) + "○" * (len(keys_) - len(covered))
        left = [labels[k] for k in keys_ if k not in covered]
        parts = [f"{bar}  {len(covered)} of {len(keys_)} covered"]
        if left:
            parts.append("still to cover: " + ", ".join(left))
        parts.append(how_far(len(covered), len(keys_)))
        return "  ·  ".join(parts)

    @staticmethod
    def questions_text(args: dict) -> str:
        """The questions as the user reads them: one on its own, or two as separate
        paragraphs after "Two questions:", with any short lead-in line first."""
        raw = args.get("questions")
        if isinstance(raw, str) or raw is None:
            raw = [raw or args.get("question") or ""]
        items = [" ".join(str(q).split()) for q in raw if str(q or "").strip()]
        if not items:
            return ""
        lead_in = " ".join(str(args.get("lead_in") or "").split())
        if len(items) == 1:
            body = items[0]
        else:
            body = f"{'Two' if len(items) == 2 else len(items)} questions:\n\n" + "\n\n".join(items)
        return f"{lead_in}\n\n{body}" if lead_in else body

    def _ask(self, args: dict) -> tuple[dict, Outcome | None]:
        question = self.questions_text(args)
        if not question:
            return {"error": "give at least one question in `questions`"}, None
        line = self.progress_line(args)
        if line and line != getattr(self, "_last_progress", None):
            self.ui.show(Progress(line))
        self._last_progress = line
        self.ui.show(LeadSaid(question, asks=True))
        self.transcript.lead(question + (f"\n\n_[{line}]_" if line else ""))
        answer = self._answer()
        if answer is None:
            raise KeyboardInterrupt
        self.transcript.user(answer)
        return {"answer": answer, "_answered": True}, None

    def _insufficient(self, args: dict) -> tuple[dict, Outcome | None]:
        gaps = [str(g).strip() for g in (args.get("gaps") or []) if str(g).strip()]
        if not gaps:
            return {"error": "list at least one gap"}, None
        self.gaps = gaps
        self.ui.say("The Lead says the brief has gaps:", items=gaps, spaced=True)
        self.transcript.note("The Lead reported gaps: " + "; ".join(gaps))
        answer = self._answer(f"Answer them now, or type {END_WORD} to end the brief as insufficient")
        if answer is None:
            raise KeyboardInterrupt
        if answer.lower() == END_WORD:
            self.transcript.user(END_WORD)
            return {}, Outcome("insufficient", gaps=gaps, detail="you ended the brief with gaps unanswered")
        self.transcript.user(answer)
        return {"status": "the user answered", "answer": answer, "_answered": True}, None

    def _propose(self, args: dict) -> tuple[dict, Outcome | None]:
        try:
            proposal = self.purpose.check(self.purpose.build(args))
        except ProposalError as e:
            self.transcript.note(f"The harness refused a proposal: {e}")
            return {"error": f"refused: {e}"}, None
        self.transcript.proposal(proposal)
        edited = False
        self.ui.say(self.purpose.proposal_intro, spaced=True, level="lead")
        while True:
            self._show(proposal)
            self.ui.say("Choose one of these options:")
            choice = self.ui.ask(Field(
                "decision", "", "choice",
                options=[Option("a", f"accept and {self.purpose.accept_label_for(proposal)}", ("accept", "y", "yes")),
                         Option("c", "ask for a change", ("change",)),
                         Option("e", "edit by hand", ("edit",)),
                         Option("q", "quit", QUIT_WORDS)],
                invalid="Type a, c, e or q.",
            ))
            if choice == "a":
                proposal = self.purpose.before_write(proposal, self.ui)
                self.purpose.write(proposal)
                self.transcript.note("You accepted" + (" your edited version." if edited else " the proposal."))
                for f in proposal.files:
                    self.ui.say(f"Saved {f.path}", level="done")
                return {}, Outcome("confirmed", proposal=proposal, edited=edited)
            if choice == "q":
                raise KeyboardInterrupt
            if choice == "c":
                request = self._answer("What should change?")
                if request is None:
                    raise KeyboardInterrupt
                self.transcript.user(f"(change requested) {request}")
                note = {"status": "the user asked for a change", "request": request, "_answered": True}
                if edited:
                    note["their_edited_version"] = {f.label: f.text for f in proposal.files}
                return note, None
            proposal, ok = self._hand_edit(proposal)
            edited = edited or ok

    def _hand_edit(self, proposal: Proposal) -> tuple[Proposal, bool]:
        drafts = Path(tempfile.mkdtemp(prefix="ombro-draft-"))
        try:
            paths = []
            for i, f in enumerate(proposal.files):
                p = drafts / f"{i + 1}-{f.path.parent.name}-{f.path.name}"
                p.write_text(f.text, encoding="utf-8")
                paths.append(p)
            # One file at a time, so every file is opened, whatever the editor does with several.
            for i, (f, path) in enumerate(zip(proposal.files, paths), 1):
                if len(paths) > 1:
                    self.ui.say(f"Opening {f.label} in your editor ({i} of {len(paths)}).")
                self.ui.edit([path])
            texts = [p.read_text(encoding="utf-8") for p in paths]
        finally:
            shutil.rmtree(drafts, ignore_errors=True)
        if texts == [f.text for f in proposal.files]:
            self.ui.say("No changes were made.")
            return proposal, False
        edited = Proposal(
            [FileDraft(f.label, f.path, t) for f, t in zip(proposal.files, texts)],
            data=dict(proposal.data),
        )
        try:
            edited = self.purpose.check(edited)
        except ProposalError as e:
            self.ui.show(Failure(f"The edited version cannot be used: {e}\nShowing the previous version again."))
            return proposal, False
        self.transcript.proposal(edited, heading="You edited it to")
        return edited, True

    def _save_transcript(self, outcome: Outcome) -> Path:
        folder = interviews_dir(self.home, self.crew)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}-{self.purpose.kind}.md"
        n = 2
        while path.exists():
            path = folder / f"{stamp}-{self.purpose.kind}-{n}.md"
            n += 1
        meta = {
            "kind": self.purpose.kind,
            "crew": self.crew,
            "outcome": outcome.status,
            "started_at": self.transcript.started,
            "finished_at": utc_now(),
            "files_written": [str(f.path) for f in outcome.proposal.files] if outcome.confirmed and outcome.proposal else [],
            "gaps": outcome.gaps,
            "limits": {"max_tokens": self.limits.max_tokens, "max_answers": self.limits.max_rounds},
            "usage": {
                k: outcome.usage.get(k)
                for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls", "rounds")
            },
        }
        text = self.transcript.render(meta)
        text, _ = keys.scrub(self.home, text)  # belt and braces: no key is ever saved
        mdfile.write_atomic(path, text)
        return path


def how_far(covered: int, total: int) -> str:
    if total <= 0 or covered <= 0:
        return "just started"
    share = covered / total
    if covered >= total:
        return "ready to wrap up"
    if share < 0.4:
        return "about a third of the way"
    if share < 0.7:
        return "about halfway"
    return "nearly done"


def readable(text: str) -> str:
    """A file as the user reads it in a proposal: no empty header block, no hidden comments."""
    import re

    try:
        meta, body = mdfile.parse(text)
    except mdfile.FrontMatterError:
        return text.strip()
    shown = text if meta else body
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<!--.*?-->", "", shown, flags=re.S)).strip()


def proposal_view(proposal: Proposal) -> ProposalShown:
    """The proposal as data for an interface: each file, whether it replaces one,
    and the changes from the current file when it has content."""
    files = []
    for f in proposal.files:
        exists = f.path.is_file()
        diff: list[str] = []
        if exists:
            old = f.path.read_text(encoding="utf-8")
            if mdfile.prompt_text(mdfile.parse(old)[1]) and old != f.text:
                diff = list(difflib.unified_diff(
                    old.splitlines(), f.text.splitlines(), "current", "proposed", lineterm="", n=1))[2:60]
        files.append(FileView(f.label, str(f.path), readable(f.text), exists, diff))
    return ProposalShown(files, list(proposal.warnings))


def _tool_reply(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)}
