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
    empty). A choice with `other` also takes a free answer, described by it.
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

    def check(self, value: Any) -> str | None:
        """Why an answer cannot be taken, or None. Any interface may call it; the core does too."""
        if self.kind in ("yesno", "wait"):
            return None
        text = "" if value is None else str(value)
        if self.kind == "choice":
            if self.choose(text) is not None or (self.other and text.strip()):
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
                low = raw.strip().lower()
                return bool(f.default) if not low else low in ("y", "yes")
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


def render(event: Event) -> list[str]:
    """The lines the command line prints for an event."""
    if isinstance(event, LeadSaid):
        return [f"Lead: {event.text}\n"] if event.asks else [f"\nLead: {event.text}\n"]
    if isinstance(event, Progress):
        return [f"\n[{event.text}]"]
    if isinstance(event, Failure):
        return [f"\n{event.text}"]
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
            out.append(f"\n── {f.label} ({'replaces the current file' if f.replaces else 'new file'}) " + "─" * 8)
            out.append(f.text.rstrip())
            if f.diff:
                out.append("\n   Changes from the current file:")
                out += ["   " + line for line in f.diff]
        out += [f"\nNote: {w}" for w in event.warnings]
        out.append("─" * 40)
        return out
    if isinstance(event, Note):
        return [("\n" if event.spaced else "") + event.text] + [f"  - {i}" for i in event.items]
    return [str(event.to_dict())]
