"""Interviews: the Lead's structured conversation with the user.

One mechanism with three uses (set-up, adding a role, briefing a task). The
Lead asks questions in plain text and proposes the files it would write with
the `propose` tool. The harness checks the proposal, shows it, and asks the
user to accept it, ask for a change, edit it by hand, or quit. Only the
harness writes files, and only after the user accepts.

Every model call goes through its own Gate, under the interview limits in
limits.md, so interview spend never touches a run's. Anything the user types
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

# The Lead may make this many model calls in a row without the user answering.
MAX_CALLS_WITHOUT_ANSWER = 5
QUIT_WORDS = ("/quit", "/q")
END_WORD = "/end"


BLOCK = '"""'


def read_answer(ask: Callable[[str], str], prompt: str) -> str:
    """One answer. A line that starts with three double quotes opens a block of
    several lines, closed by a line that ends with them, so a pasted paragraph
    stays one answer."""
    raw = ask(prompt) or ""
    if not raw.strip().startswith(BLOCK):
        return raw
    first = raw.strip()[len(BLOCK):]
    if first.endswith(BLOCK):
        return first[: -len(BLOCK)]
    lines = [first] if first.strip() else []
    while True:
        line = ask("... ") or ""
        if line.rstrip().endswith(BLOCK):
            last = line.rstrip()[: -len(BLOCK)]
            if last.strip():
                lines.append(last)
            return "\n".join(lines)
        lines.append(line)


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
    spend: dict[str, Any] = field(default_factory=dict)
    edited: bool = False
    answers: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


class Purpose:
    """What one kind of interview asks for, and how its files are built and checked."""

    kind = ""
    title = ""
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

    def before_write(self, proposal: Proposal, ask: Callable[[str], str], say: Callable[[str], None]) -> Proposal:
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


def open_in_editor(home: Home, paths: list[Path], ask: Callable[[str], str], say: Callable[[str], None]) -> None:
    """Open files in the editor from preferences (or $VISUAL / $EDITOR) and wait.
    With no editor set, show the paths and wait for Enter."""
    cmd = editor_command(home)
    if cmd:
        try:
            subprocess.run([*cmd, *map(str, paths)], check=False)
            return
        except OSError as e:
            say(f"Could not start the editor {cmd[0]!r}: {e}")
    say("No editor is set (`editor` in preferences/config.md, or $EDITOR). Edit these files yourself:")
    for p in paths:
        say(f"  {p}")
    ask("Press Enter when you have saved them. ")


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


def interviews_dir(home: Home, crew: str) -> Path:
    return home.crew_dir(crew) / "interviews"


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
        self.ask = ask or (lambda prompt: input(prompt))
        self.say = say or (lambda text: print(text, flush=True))
        self.edit = edit or (lambda paths: open_in_editor(home, paths, self.ask, self.say))
        self.limits = limits.for_interview()
        self.models = load_models(home)
        if model not in self.models:
            model = "default"
        self.spec = self.models[model]
        self._client_factory = client_factory or (lambda spec: make_client(spec, home))
        self.gate = Gate(self.limits, self._price, clock)
        self.today = prompts.format_date(today or date.today())
        self.lead_soul = lead_soul
        self.instructions = instructions
        self.transcript = Transcript(purpose.kind, crew)
        self.gaps: list[str] = []
        self._client: ChatClient | None = None
        self.answers: list[str] = []

    def _price(self, name: str):
        spec = self.models.get(name)
        return (spec.price_in, spec.price_out) if spec is not None and spec.priced else None

    # -- helpers -----------------------------------------------------------

    def _limits_line(self) -> str:
        return (
            f"- tokens for the whole interview: {format_tokens(self.limits.max_tokens)}\n"
            f"- answers from the user: {self.limits.max_rounds}"
        )

    def _input(self, prompt: str) -> str:
        """One answer from the user, scrubbed of keys. Raises EOFError/KeyboardInterrupt to quit."""
        while True:
            raw = read_answer(self.ask, prompt)
            text, removed = keys.scrub(self.home, (raw or "").strip())
            if removed:
                self.say(
                    "(Something that looked like a key was removed before it reached the model. "
                    "Keys are set with `ombro keys set NAME`, never in an interview.)"
                )
            if text:
                return text

    def _answer(self, prompt: str = "You: ") -> str | None:
        """The user's next answer, counted against the interview's answer cap. None means quit."""
        text = self._input(prompt)
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
        reply = self._client.complete(messages, tools=tools, max_output_tokens=max_out, timeout=min(timeout, 300))
        u = reply.usage
        in_tokens = u.input_tokens if u.input_tokens or not u.estimated else est
        out_tokens = min(u.output_tokens, max_out) if u.estimated else u.output_tokens
        self.gate.record_model_call(self.spec.name, in_tokens, out_tokens, estimated=u.estimated)
        return reply

    def _show(self, proposal: Proposal) -> None:
        for f in proposal.files:
            exists = f.path.is_file()
            self.say(f"\n── {f.label} ({'replaces the current file' if exists else 'new file'}) " + "─" * 8)
            self.say(f.text.rstrip())
            if exists:
                old = f.path.read_text(encoding="utf-8")
                if mdfile.prompt_text(mdfile.parse(old)[1]) and old != f.text:
                    diff = list(
                        difflib.unified_diff(
                            old.splitlines(), f.text.splitlines(), "current", "proposed", lineterm="", n=1
                        )
                    )
                    if diff:
                        self.say("\n   Changes from the current file:")
                        for line in diff[2:60]:
                            self.say("   " + line)
        for w in proposal.warnings:
            self.say(f"\nNote: {w}")
        self.say("─" * 40)

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
        self.say(
            f"{p.title}" + (f" It is {p.typical}." if p.typical else "")
            + " Type /quit to stop; nothing is written unless you accept it."
            + f" For several lines, start and end with {BLOCK}."
            + f" (Up to {format_tokens(self.limits.max_tokens)} tokens.)\n"
        )
        try:
            outcome = self._loop(messages, tools)
        except LimitReached as e:
            self.transcript.note(f"Stopped by the interview limit {e.limit}: {e.detail}")
            self.say(f"\nThe interview stopped: {e.detail}. Nothing was written.")
            outcome = Outcome("limit", gaps=list(self.gaps), detail=f"{e.limit}: {e.detail}")
        except ModelError as e:
            self.transcript.note(f"The model call failed: {e}")
            self.say(f"\nThe model call failed: {e}. Nothing was written.")
            outcome = Outcome("failed", detail=str(e))
        except (KeyboardInterrupt, EOFError):
            self.transcript.note("You stopped the interview.")
            self.say("\nInterview stopped. Nothing was written.")
            outcome = Outcome("quit", detail="stopped by you")
        outcome.spend = self.gate.snapshot()
        outcome.answers = list(self.answers)
        outcome.transcript = self._save_transcript(outcome)
        used = outcome.spend.get("total_tokens", 0)
        self.say(f"(Interview used {used:,} of {format_tokens(self.limits.max_tokens)} tokens.)")
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
                self.say(f"\nLead: {text}\n")
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
        keys_ = [k for k, _ in self.purpose.topics]
        covered = [k for k in keys_ if k in (args.get("covered") or [])]
        progress = args.get("progress") if args.get("progress") in prompts.PROGRESS else ""
        parts = []
        if keys_:
            labels = dict(self.purpose.topics)
            bar = "●" * len(covered) + "○" * (len(keys_) - len(covered))
            left = [labels[k] for k in keys_ if k not in covered]
            parts.append(f"{bar}  {len(covered)} of {len(keys_)} covered")
            if left:
                parts.append("still to cover: " + ", ".join(left))
        if progress:
            parts.append(progress)
        return "  ·  ".join(parts)

    @staticmethod
    def questions_text(args: dict) -> str:
        """The questions, each on its own line, numbered when there is more than one."""
        raw = args.get("questions")
        if isinstance(raw, str) or raw is None:
            raw = [raw or args.get("question") or ""]
        items = [" ".join(str(q).split()) for q in raw if str(q or "").strip()]
        if len(items) == 1:
            return items[0]
        return "\n".join(f"  {i}. {q}" for i, q in enumerate(items, 1))

    def _ask(self, args: dict) -> tuple[dict, Outcome | None]:
        question = self.questions_text(args)
        if not question:
            return {"error": "give at least one question in `questions`"}, None
        if "\n" in question:
            question = "\n" + question
        line = self.progress_line(args)
        if line:
            self.say(f"\n[{line}]")
        self.say(f"Lead: {question}\n")
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
        self.say("\nThe Lead says the brief has gaps:")
        for g in gaps:
            self.say(f"  - {g}")
        self.transcript.note("The Lead reported gaps: " + "; ".join(gaps))
        answer = self._answer(f"Answer them now, or type {END_WORD} to end the brief as insufficient: ")
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
        while True:
            self._show(proposal)
            choice = self._input(
                f"[a]ccept and {self.purpose.accept_label_for(proposal)}, ask for a [c]hange, [e]dit by hand, "
                "or [q]uit? "
            ).lower()
            if choice in ("a", "accept", "y", "yes"):
                proposal = self.purpose.before_write(proposal, self._input, self.say)
                self.purpose.write(proposal)
                self.transcript.note("You accepted" + (" your edited version." if edited else " the proposal."))
                for f in proposal.files:
                    self.say(f"Wrote {f.path}")
                return {}, Outcome("confirmed", proposal=proposal, edited=edited)
            if choice in ("q", "quit", *QUIT_WORDS):
                raise KeyboardInterrupt
            if choice in ("c", "change"):
                request = self._answer("What should change? ")
                if request is None:
                    raise KeyboardInterrupt
                self.transcript.user(f"(change requested) {request}")
                note = {"status": "the user asked for a change", "request": request, "_answered": True}
                if edited:
                    note["their_edited_version"] = {f.label: f.text for f in proposal.files}
                return note, None
            if choice in ("e", "edit"):
                proposal, ok = self._hand_edit(proposal)
                edited = edited or ok
                continue
            self.say("Type a, c, e or q.")

    def _hand_edit(self, proposal: Proposal) -> tuple[Proposal, bool]:
        drafts = Path(tempfile.mkdtemp(prefix="ombro-draft-"))
        try:
            paths = []
            for i, f in enumerate(proposal.files):
                p = drafts / f"{i + 1}-{f.path.parent.name}-{f.path.name}"
                p.write_text(f.text, encoding="utf-8")
                paths.append(p)
            self.edit(paths)
            texts = [p.read_text(encoding="utf-8") for p in paths]
        finally:
            shutil.rmtree(drafts, ignore_errors=True)
        if texts == [f.text for f in proposal.files]:
            self.say("No changes were made.")
            return proposal, False
        edited = Proposal(
            [FileDraft(f.label, f.path, t) for f, t in zip(proposal.files, texts)],
            data=dict(proposal.data),
        )
        try:
            edited = self.purpose.check(edited)
        except ProposalError as e:
            self.say(f"\nThe edited version cannot be used: {e}\nShowing the previous version again.")
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
            "spend": {
                k: outcome.spend.get(k)
                for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls", "rounds", "usd")
            },
        }
        text = self.transcript.render(meta)
        text, _ = keys.scrub(self.home, text)  # belt and braces: no key is ever saved
        mdfile.write_atomic(path, text)
        return path


def _tool_reply(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)}
