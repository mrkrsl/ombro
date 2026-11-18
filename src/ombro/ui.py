"""What the core says to an interface, and what it asks.

The core never prints or reads a terminal itself. It shows events (the Lead
said something, progress, a note, an error, a proposal, usage) and asks for
fields (a line of text, a hidden secret, a choice, a yes or no) or whole
forms. Everything here is plain data: `to_dict()` gives JSON, so the terminal
interface and a later graphical one can show it as they like.

`ConsoleUI` is the command line's interface: it prints the same text Ombro
has always printed and reads answers with input() and getpass.

Answering is blocking. Quitting is done by raising KeyboardInterrupt (or
EOFError) from `ask`, as at a terminal.
"""

from __future__ import annotations

import getpass
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar

# ---------------------------------------------------------------------------
# Events: what the core shows


@dataclass
class Event:
    kind: ClassVar[str] = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, **asdict(self)}


@dataclass
class LeadSaid(Event):
    """The Lead's own words. `asks` is true when it is waiting for an answer."""

    kind: ClassVar[str] = "lead"
    text: str
    asks: bool = False


@dataclass
class Progress(Event):
    """Where something stands: interview topics covered, a test call under way."""

    kind: ClassVar[str] = "progress"
    text: str


@dataclass
class Note(Event):
    """Something Ombro says. `items` is a list shown beneath it. `spaced` starts
    a new paragraph. `level` is info, warning or done."""

    kind: ClassVar[str] = "note"
    text: str
    items: list[str] = field(default_factory=list)
    spaced: bool = False
    level: str = "info"


@dataclass
class Failure(Event):
    """Something went wrong. Shown on its own, never mistaken for the Lead."""

    kind: ClassVar[str] = "error"
    text: str


@dataclass
class FileView:
    label: str  # how the file is named to the user, e.g. preferences/instructions.md
    path: str  # where it will be written
    text: str
    replaces: bool  # a file is already there
    diff: list[str] = field(default_factory=list)  # unified diff lines against it, if it has content


@dataclass
class ProposalShown(Event):
    """Files an interview proposes to write, waiting for the user's decision."""

    kind: ClassVar[str] = "proposal"
    files: list[FileView]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Usage(Event):
    """Tokens used so far against the cap, for a chat or an interview."""

    kind: ClassVar[str] = "usage"
    scope: str  # chat | interview
    mode: str  # Chat | Brief | Set-up | Role | ...
    total_tokens: int
    max_tokens: int | None
    tool_calls: int = 0
    final: bool = False  # the closing figure, once it has ended


@dataclass
class ModeChanged(Event):
    """Chat moved into Brief, or back."""

    kind: ClassVar[str] = "mode"
    mode: str
    text: str = ""


@dataclass
class PastTurn(Event):
    """A turn from a chat reopened to carry on, shown again: who said it, and what."""

    kind: ClassVar[str] = "past"
    who: str  # you or lead
    text: str


@dataclass
class ChatCleared(Event):
    """/new: a fresh chat began, so an interface may clear what is on screen."""

    kind: ClassVar[str] = "cleared"


@dataclass
class JobStarted(Event):
    """A job started in the background, after the user's yes."""

    kind: ClassVar[str] = "job_started"
    job_id: str
    name: str  # readable, from the job id
    text: str


@dataclass
class JobProgress(Event):
    """A step in a job's journal worth showing: a role briefed, a result back, an approval asked."""

    kind: ClassVar[str] = "job_progress"
    job_id: str
    name: str
    role: str
    action: str  # the journal's action: briefed, returned, staged, finish, failed...
    note: str
    text: str  # the line as an interface shows it
    live: bool = False  # a step of a live job, followed in the chat that started it


@dataclass
class JobFile:
    name: str
    path: str
    url: str  # file://
    exists: bool


@dataclass
class JobOffer:
    """A way to carry on with a job that didn't finish: a button's words and the chat command it sends."""

    label: str
    command: str  # /restart <job> [more] or /resume <job> [more]


@dataclass
class JobEnded(Event):
    """A job has ended: how, what it made, what waits for approval. Written by Ombro, not the model."""

    kind: ClassVar[str] = "job_ended"
    job_id: str
    name: str
    status: str  # completed, stopped_by_limit, stopped, insufficient_brief, failed...
    text: str  # how it ended, in a sentence
    brief: str = ""
    limit_hit: str | None = None
    files: list[JobFile] = field(default_factory=list)
    waiting: int = 0
    live: bool = False  # a live job: its report opens in the chat
    summary: str = ""  # the Lead's short paragraph from the report (its Summary)
    again: str = ""  # restart or resume, for a job that didn't finish; "" otherwise
    again_text: str = ""  # the Lead's offer to restart or resume it
    offers: list[JobOffer] = field(default_factory=list)


@dataclass
class OpenPage(Event):
    """Ombro suggests a page of the app, such as Scheduled with a brief picked. A terminal shows the text."""

    kind: ClassVar[str] = "open_page"
    screen: str  # scheduled, jobs, approvals...
    args: str
    text: str


# ---------------------------------------------------------------------------
# Fields and forms: what the core asks

FIELD_KINDS = ("text", "secret", "choice", "yesno", "wait")


@dataclass
class Option:
    key: str  # what the core gets back
    label: str  # what the user sees; the key is its first letter, or a word in it
    aliases: tuple[str, ...] = ()  # other answers taken to mean this one


@dataclass
class Field:
    """One question. In a form, one field of it.

    kind: text (a line, or several if `multiline`), secret (hidden, never shown
    or saved), choice (one of `options`), yesno, or wait (only to go on).
    `pattern` is a regular expression the answer must match in full, and
    `invalid` what to say when it does not (or when a required answer is
    empty). A choice with `other` also takes a free answer, described by it; with
    `pattern` too, only a free answer that matches it.

    `aside`: a question asked in the chat that a new message may set aside. When
    what the user sends is not an answer to it, the interface raises SetAside
    from `ask` and passes the message to the session, which sends it to the
    Lead once the chat has dealt with the question going unanswered.
    """

    name: str
    label: str
    kind: str = "text"
    default: Any = None
    options: list[Option] = field(default_factory=list)
    required: bool = False
    pattern: str | None = None
    invalid: str = ""
    help: str = ""
    multiline: bool = False
    other: str = ""
    mode: str = ""  # the conversation mode it is asked in (Chat, Brief), shown by some interfaces
    aside: bool = False

    def __post_init__(self) -> None:
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"unknown field kind {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def choose(self, answer: str) -> str | None:
        """The option key an answer means, or None."""
        low = answer.strip().lower()
        for o in self.options:
            if low in (o.key.lower(), o.label.lower(), *(a.lower() for a in o.aliases)):
                return o.key
        return None

    def free_answer(self, text: str) -> bool:
        """Whether a choice takes this as its free answer (`other`), within `pattern` if it has one."""
        text = text.strip()
        if not self.other or not text:
            return False
        return not self.pattern or re.fullmatch(self.pattern, text) is not None

    def check(self, value: Any) -> str | None:
        """Why an answer cannot be taken, or None. Any interface may call it; the core does too."""
        if self.kind in ("yesno", "wait"):
            return None
        text = "" if value is None else str(value)
        if self.kind == "choice":
            if self.choose(text) is not None or self.free_answer(text):
                return None
            return self.invalid or "Choose one of: " + ", ".join(o.key for o in self.options) + "."
        if not text.strip():
            if self.default not in (None, ""):
                return None  # the default is taken
            return (self.invalid or f"{self.label} is needed.") if self.required else None
        if self.pattern and not re.fullmatch(self.pattern, text.strip()):
            return self.invalid or f"{self.label} is not in the right form."
        return None


@dataclass
class Form:
    """Several fields asked together: one at a time in a terminal, on one page in a
    graphical interface. `intro` lines are shown first."""

    name: str
    title: str
    fields: list[Field]
    intro: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# The interface


class UI:
    """What every interface provides. Subclasses override show, ask and edit."""

    def show(self, event: Event) -> None:
        raise NotImplementedError

    def ask(self, f: Field) -> Any:
        """The answer to one field: text, the chosen option's key (or the free
        answer for `other`), or a bool for yesno. Raises KeyboardInterrupt or
        EOFError when the user quits."""
        raise NotImplementedError

    def form(self, form: Form) -> dict[str, Any]:
        for line in form.intro:
            self.show(Note(line))
        return {f.name: self.ask(f) for f in form.fields}

    def edit(self, paths: list[Path]) -> None:
        """Let the user edit these files by hand, and return once they have."""
        raise NotImplementedError

    # Shorthands for the core
    def say(self, text: str, **kw: Any) -> None:
        self.show(Note(text, **kw))

    def text(self, name: str, label: str, **kw: Any) -> str:
        return str(self.ask(Field(name, label, "text", **kw)) or "")

    def yes(self, name: str, label: str, default: bool = False, **kw: Any) -> bool:
        return bool(self.ask(Field(name, label, "yesno", default=default, **kw)))


def option_text(options: list[Option], other: str = "") -> str:
    """'[a]ccept, ask for a [c]hange, or [q]uit': each key marked where its word starts."""
    parts = []
    for o in options:
        m = re.search(rf"(?<![\w]){re.escape(o.key)}", o.label, re.IGNORECASE)
        if m:
            i = m.start()
            parts.append(o.label[:i] + "[" + o.label[i:i + len(o.key)] + "]" + o.label[i + len(o.key):])
        else:
            parts.append(f"[{o.key}] {o.label}")
    if other:
        parts.append(other)
    if len(parts) < 2:
        return parts[0] if parts else ""
    return ", ".join(parts[:-1]) + ", or " + parts[-1]


def numbered(f: Field) -> bool:
    """A choice whose keys are all numbers is shown as a numbered list."""
    return f.kind == "choice" and bool(f.options) and all(o.key.isdigit() for o in f.options)


def prompt_text(f: Field) -> str:
    """How the command line asks a field."""
    mode = f"[{f.mode}] " if f.mode else ""
    if numbered(f):
        return f"{mode}Choose" + (f" [{f.default}]" if f.default not in (None, "") else "") + ": "
    if f.kind == "yesno":
        return f"{mode}{f.label} {'[Y/n]' if f.default else '[y/N]'} "
    if f.kind == "choice":
        opts = option_text(f.options, f.other)
        return f"{mode}{f.label} {opts}: " if f.label else f"{mode}{opts}? "
    label = f.label
    if f.default not in (None, "") and f.kind == "text":
        label += f" [{f.default}]"
    end = " " if label.endswith(("?", ".", ":")) else ": "
    return f"{mode}{label}{end}"


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


class ConsoleUI(UI):
    """The command line: prints what Ombro has always printed, reads with input()."""

    def __init__(
        self,
        *,
        ask: Callable[[str], str] | None = None,
        ask_secret: Callable[[str], str] | None = None,
        say: Callable[[str], None] | None = None,
        edit: Callable[[list[Path]], None] | None = None,
        home: Any = None,
    ):
        self._ask = ask or (lambda prompt: input(prompt))
        self._ask_secret = ask_secret or getpass.getpass
        self._say = say or (lambda text: print(text, flush=True))
        self._edit = edit
        self.home = home

    # -- showing -----------------------------------------------------------

    def show(self, event: Event) -> None:
        for line in render(event):
            self._say(line)

    # -- asking ------------------------------------------------------------

    def ask(self, f: Field) -> Any:
        prompt = prompt_text(f)
        if numbered(f):
            self._say(f.label if f.label.endswith(("?", ":")) else f"{f.label}:")
            for o in f.options:
                self._say(f"  {o.key}) {o.label}")
        while True:
            if f.kind == "secret":
                raw = self._ask_secret(prompt) or ""
            elif f.multiline:
                raw = read_answer(self._ask, prompt)
            else:
                raw = self._ask(prompt) or ""
            if f.kind == "wait":
                return None
            if f.kind == "yesno":
                answer = yes_no(raw, f.default, f.other)
                if answer is None:
                    self._say(YESNO_AGAIN)
                    continue
                return answer
            if f.kind == "choice":
                if not raw.strip() and f.default not in (None, ""):
                    raw = str(f.default)
                key = f.choose(raw)
                if key is not None:
                    return key
                if f.other and raw.strip():
                    return raw.strip()
                self._say(f.invalid or f.check(raw) or "")
                continue
            value = raw.strip() if f.kind == "secret" else raw
            if f.kind == "text" and not raw.strip() and f.default not in (None, ""):
                value = str(f.default)
            problem = f.check(value)
            if problem:
                self._say(problem)
                continue
            return value

    def edit(self, paths: list[Path]) -> None:
        if self._edit:
            self._edit(paths)
            return
        from ombro.interview import open_in_editor

        open_in_editor(self.home, paths, self)


YESNO_AGAIN = "Please answer y or n (Enter for the default)."


def yes_no(raw: str, default: Any, other: str | None = None) -> bool | str | None:
    """A yes/no answer, the default for Enter, or None for anything else: a stray
    command such as /model must never be taken as no. A yes/no field with `other`
    (such as "change it") also takes that answer, by its first word, and returns it."""
    low = (raw or "").strip().lower().strip(".!\"'")
    if not low:
        return bool(default)
    if other and low.split()[0] == other.lower().split()[0]:
        return other
    if low in ("y", "yes"):
        return True
    if low in ("n", "no"):
        return False
    return None


def parse_answer(f: Field, raw: Any) -> tuple[Any, str | None]:
    """An answer as typed (or, from a graphical interface, a bool for yes or no), turned into
    what the core gets back: (value, None), or (None, what to say) when it can't be taken and
    the question stays open. The terminal and graphical interfaces both answer through this."""
    if f.kind == "yesno":
        if isinstance(raw, bool):
            return raw, None
        text = "" if raw is None else str(raw)
        value = yes_no(text, f.default, f.other)
        if value is None:
            return None, ("That's a question waiting for an answer, so commands wait until it's done. "
                          if text.strip().startswith("/") else "") + YESNO_AGAIN
        return value, None
    text = "" if raw is None else str(raw)
    if f.kind == "choice":
        given = text if text.strip() or f.default in (None, "") else str(f.default)
        value = f.choose(given)
        if value is None and f.free_answer(given):
            value = given.strip()
        if value is None:
            return None, f.invalid or f.check(given) or "Choose one of those shown."
        return value, None
    if f.kind == "wait":
        return None, None
    value = text if text.strip() or f.default in (None, "") else str(f.default)
    problem = f.check(value)
    return (None, problem) if problem else (value, None)


class SetAside(BaseException):
    """Raised by `UI.ask` when the user sent a new message instead of answering a question that
    may be set aside (`Field.aside`): the question goes unanswered, and the message is the
    session's to send to the Lead next. Not an Exception, so that, like an interruption, it
    passes the handlers that turn a tool's failure into an error for the Lead."""

    def __init__(self, field: Field):
        super().__init__(field.label or field.name)
        self.field = field


def sets_aside(f: Field, raw: Any) -> bool:
    """Whether what the user sent sets this question aside rather than answering it: a message
    (not a command, not empty) to a question that allows it, which isn't an answer to it."""
    if not f.aside or not isinstance(raw, str):
        return False
    text = raw.strip()
    if not text or text.startswith("/"):
        return False
    return parse_answer(f, raw)[1] is not None


def field_prompt(f: Field) -> str:
    """A field as a line of conversation: its label, with the choices, the default, or a
    reminder that it is hidden."""
    if f.kind == "yesno":
        return f"{f.label} (y or n{', Enter for ' + ('yes' if f.default else 'no')})"
    if f.kind == "choice":
        if numbered(f):
            return f.label + ("" if f.label.endswith(("?", ":")) else ":") + "\n" + \
                "\n".join(f"  {o.key}) {o.label}" for o in f.options) + \
                (f"\n(Enter for {f.default})" if f.default not in (None, "") else "")
        return (f"{f.label} " if f.label else "") + option_text(f.options, f.other) + "?"
    if f.kind == "secret":
        return f.label + ("" if "hidden" in f.label.lower() else " (hidden as you type)")
    return f.label + (f" (Enter for {f.default})" if f.default not in (None, "") else "")


def render(event: Event) -> list[str]:
    """The lines the command line prints for an event."""
    if isinstance(event, LeadSaid):
        return [f"Lead: {event.text}\n"] if event.asks else [f"\nLead: {event.text}\n"]
    if isinstance(event, Progress):
        return [f"\n[{event.text}]"]
    if isinstance(event, Failure):
        return [f"\n{event.text}"]
    if isinstance(event, ChatCleared):
        return []
    if isinstance(event, PastTurn):
        return [f"{'You' if event.who == 'you' else 'Lead'}: {event.text}"]
    if isinstance(event, ModeChanged):
        if not event.text:
            return []
        return [f"\n{event.text}"] if event.mode == "Brief" else [f"{event.text}\n"]
    if isinstance(event, Usage):
        if event.scope == "interview":
            from ombro.settings import format_tokens

            if not event.final:
                return []  # the command line shows only the closing figure
            return [f"(Interview used {event.total_tokens:,} of {format_tokens(event.max_tokens)} tokens.)"]
        from ombro.settings import format_tokens

        return [f"[{event.mode} · {event.total_tokens:,} of {format_tokens(event.max_tokens)} tokens · "
                f"{event.tool_calls} tool call(s)]"]
    if isinstance(event, ProposalShown):
        out = []
        for f in event.files:
            out.append(f"\n── {f.label} ({'updates the current file' if f.replaces else 'new file'}) " + "─" * 8)
            out.append(f.text.rstrip())
            if f.diff:
                out.append("\n   Changes from the current file:")
                out += ["   " + line for line in f.diff]
        out += [f"\nNote: {w}" for w in event.warnings]
        out.append("─" * 40)
        return out
    if isinstance(event, Note):
        return [("\n" if event.spaced else "") + event.text] + [f"  - {i}" for i in event.items]
    if isinstance(event, (JobStarted, JobProgress)):
        return [event.text]
    if isinstance(event, JobEnded):
        return [f"\n{event.text}", event.summary or event.brief] + [f"  {f.name}: {f.path}" for f in event.files] + \
            ([f"{event.waiting} waiting for approval: ombro approvals"] if event.waiting else []) + \
            [f"Read the report: ombro report {event.job_id}"] + \
            ([event.again_text] + [f"  {o.label}: {o.command}" for o in event.offers] if event.offers else [])
    return [str(event.to_dict())]
