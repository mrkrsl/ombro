"""General chat with the Lead: `ombro chat`.

Two modes, always shown in the prompt. Chat is an ordinary conversation;
Brief is the task interview, entered when the user accepts the Lead's offer
or types /brief, and left with /chat.

Chat reads across every crew (past reports, specs, notes, indexes and
deliverables), plus web search and fetch, and works in the user's shared
folders as the user would: reading them, and writing in those shared for
writing, each write shown to the user and confirmed first. It cannot brief
roles, start jobs, approve what waits for approval or use outward tools. Its only other
write is a remember the user has confirmed, into a crew's notes.md
("From you").

It has its own gate: a token cap, per-tool call caps and a cap on model
calls per message. Once the conversation passes a set length the oldest
turns stop being sent. Each chat is saved in chats/; transcripts are never
loaded into later chats or jobs, and never searched.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from ombro import keys, material, mdfile, memory, prompts, jobs, skills
from ombro import carry as carry_mod
from ombro.client import ChatClient, ModelError, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home, crew_phrase, crew_phrases, crew_title, find_crew, words_title
from ombro.ui import (
    UI,
    ChatCleared,
    PastTurn,
    ConsoleUI,
    Failure,
    Field,
    LeadSaid,
    ModeChanged,
    Note,
    Option,
    SetAside,
    Usage,
)
from ombro.journal import utc_now
from ombro.roles import load_lead
from ombro.settings import (
    ModelSpec,
    SetupError,
    format_tokens,
    load_limits,
    load_models,
    load_preferences,
    load_tool_settings,
)
from ombro.tools import LOAD_NAME, ToolContext, ToolError, ToolRegistry, is_deferred, load_tool_schema, load_tools

IMAGE_TOKENS = 1_600  # a generous estimate for one image in a message
TITLE_MAX_OUTPUT = 600  # tokens for the title call, thinking included
QUIT_WORDS = ("/quit", "/q", "/exit", "/end")
TOOL_RESULT_CAP = 12_000
BRIEF_CONTEXT_CHARS = 4_000
# A long chat (past chat_history_chars) is trimmed rarely and in one step, so what is sent stays
# the same from one call to the next and the provider's cache keeps working:
TRIM_TO = 0.6  # trimmed down to this share of chat_history_chars
HEAD_CHARS = 12_000  # the opening turns, which say what the chat is for: always kept
RECENT_EXCHANGES = 2  # the latest exchanges keep their tool results whole
STUB_OVER = 1_500  # an older tool result longer than this is cut to a note first
LEFT_OUT = "[Ombro note: "  # how the note on left-out turns starts
# What the Lead said and read in the chat, carried into a brief so the interview knows what the
# chat found (a report, a file's name and contents): the most recent first, within these caps.
BRIEF_FOUND_CHARS = 16_000
BRIEF_FOUND_EACH = 3_000
# What comes back from a brief: its transcript (questions, answers, the spec), the latest part kept.
BRIEF_BACK_CHARS = 12_000
# The chat's tools a brief may use too: reading only.
BRIEF_LOOKUPS = (
    "memory__search",
    "memory__read",
    "memory__list",
    "folders__list",
    "folders__read",
    "skills__read",
    "web__search",
    "web__fetch",
)


class BriefLookups:
    """The chat's read-only lookups, lent to a brief held in it: the same tools, run by the chat,
    so the Lead knows and can find the same things in Chat and Brief."""

    def __init__(self, chat: "Chat"):
        self.chat = chat
        self._schemas: list[dict] | None = None

    def schemas(self) -> list[dict]:
        if self._schemas is None:
            c = self.chat
            tools = prompts.CHAT_TOOLS + [t.schema() for n in WEB_TOOLS.values() if (t := c.registry.get(n))]
            if not skills.library(c.home):  # nothing to read: no tool for it, as in chat
                tools = [t for t in tools if t["function"]["name"] != "skills__read"]
            self._schemas = [t for t in tools if t["function"]["name"] in BRIEF_LOOKUPS]
        seen: set[str] = set()
        out = []
        for t in self._schemas + self.chat.lookup_schemas(read_only=True):  # as the chat has loaded them
            if t["function"]["name"] not in seen:
                seen.add(t["function"]["name"])
                out.append(t)
        return out

    @property
    def names(self) -> set[str]:
        return {t["function"]["name"] for t in self.schemas()}

    def run(self, tc) -> dict:
        result, _ = self.chat._tool(tc)
        return result if isinstance(result, dict) else {"result": result}


MEMORY_TOOLS = {"memory__search": "memory.search", "memory__read": "memory.read", "memory__list": "memory.list"}
WEB_TOOLS = {"web__search": "web.search", "web__fetch": "web.fetch"}
FOLDER_TOOLS = {
    "folders__list": "folders.list",
    "folders__read": "folders.read",
    "folders__write": "folders.write",
    "folders__set_aside": "folders.set_aside",
    "folders__copy": "folders.copy",
    "folders__edit": "folders.edit",
}
EDIT_MAX_CHARS = 2_000  # folders.edit is a correction or a filled-in detail, not new writing
SKILL_TOOLS = {"skills__read": "skills.read"}
# The crew's own tools the Lead uses in chat too, from the registry: the browser and code.run. They work
# in a folder of the chat's own (Files/chats/<id>/workspace), next to the shared folders.
NATIVE_TOOLS = {
    "browser__open": "browser.open",
    "browser__click": "browser.click",
    "browser__type": "browser.type",
    "browser__screenshot": "browser.screenshot",
    "browser__submit": "browser.submit",
    "code__run": "code.run",
}
NOT_NOW = r"(?i)(not now|no|n|nope|not yet|later)[.!]?"  # a choice's "not now", typed; other words set it aside
# What the Lead is told when the user sent a new message instead of answering: the message comes next.
SET_ASIDE_RESULT = {
    "declined": "the user set this question aside and sent a new message instead, so nothing was done; "
    "their message comes next. Do not reply now, and do not offer this again unless they "
    "ask."
}
OFFERS = ("offer_brief", "offer_work", "offer_remember", "offer_start", "offer_skill", "offer_role", "offer_schedule")

HELP = """\
A chat is a conversation with the Lead. It can look at every crew's reports,
notes, specs and deliverables, search the web, and work in your shared folders.
It changes a file only after you say yes.

  /brief [@crew] [text]     brief a task; the chat so far is its starting point
  /live [@crew] <text>      do some work live, here: the crew starts at once and reports in this chat
  /chat                     stop a brief and keep chatting (nothing is written)
  /new                      save this chat and start a fresh one; nothing carries over
  /chats                    your past chats, newest first; reopen one and carry on
  /rename title             give this chat a title of your own
  /remember [@crew] text    add a dated lesson to a crew's notes.md, after you confirm the wording
  /restart <job> [more]     restart a job that ran out of tokens, building on what it did (more: twice the tokens)
  /resume <job> [more]      resume a job that stopped or failed, from where it got to (more: twice the time)
  /skills                   list the skills library
  /help                     this list
  /quit                     end the chat (it is saved in chats/)

For several lines, start and end with \"\"\"."""


def rest_named(text: str) -> bool:
    """Whether a /brief or /remember line names a crew with @crew."""
    return text.partition(" ")[2].strip().startswith("@")


def _size(exchanges: list[list[dict]]) -> int:
    from ombro.client import public

    return len(json.dumps([public(ex) for ex in exchanges], ensure_ascii=False))  # kept "_blocks" don't count


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


DOCUMENT_LINES = 15  # beyond this (or DOCUMENT_CHARS), a new file or a rewrite is a document: work for a crew
DOCUMENT_CHARS = 1_000


def is_document_write(content: str, old: str | None, *, exists: bool) -> bool:
    """Whether a chat write makes a document rather than a small edit: a new file (or text added
    to one) beyond about 15 lines or 1,000 characters, or a rewrite that changes most of a file."""
    import difflib

    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if lines <= DOCUMENT_LINES and len(content) <= DOCUMENT_CHARS:
        return False
    if old is None:
        return True  # a new file, an append, or a file that can't be read as text
    a, b = old.splitlines(), content.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    kept = sum(m.size for m in sm.get_matching_blocks())
    changed = max(len(a), len(b)) - kept
    return changed > DOCUMENT_LINES and changed * 2 > max(len(a), len(b))


def _folder_of(ctx: ToolContext, target: Path) -> str | None:
    """The shared folder a path is in: the innermost, when one is inside another."""
    best: tuple[int, str] | None = None
    real = target.resolve()
    for name, f in ctx.shared.items():
        root = Path(f.path)
        if real == root or real.is_relative_to(root):
            if best is None or len(root.parts) > best[0]:
                best = (len(root.parts), name)
    return best[1] if best else None


def _offered_folders(raw: Any) -> dict[str, str]:
    """offer_work's `folders` as {shared folder name: read or write}, for a live job's spec. What
    is not a shared folder's name is kept, so the job notes it as left out."""
    out: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            name, access = item.strip(), "read"
        elif isinstance(item, dict):
            name = str(item.get("folder") or item.get("name") or "").strip()
            access = "write" if item.get("access") == "write" else "read"
        else:
            continue
        name = name.removeprefix("shared/").strip("/")
        if name and "/" not in name:
            out[name] = "write" if "write" in (access, out.get(name)) else "read"
    return out


def _tool_msg(call_id: str, payload: Any, src: str | None = None) -> dict:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(content) > TOOL_RESULT_CAP:
        content = content[:TOOL_RESULT_CAP] + "…[truncated]"
    if src:
        content = material.fence(content, src)  # from outside: assessed, never obeyed
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# An entry in a saved chat: your words (in Chat, or answering a brief or role interview held in it),
# the Lead's (in the chat, or asking in an interview), or Ombro's (a job's outcome, set down for both).
ENTRY = re.compile(r"^\*\*(You \((Chat|Brief|Role)\)|Lead(?: \((Brief|Role)\))?|Ombro):\*\* ?")


def _is_note(chunk: str) -> bool:
    """One of Ombro's notes in a saved chat (`_Reopened …_`): kept in the file, never shown. The
    file's last chunk ends with a newline, so that is set aside first."""
    chunk = chunk.strip()
    return chunk.startswith("_") and chunk.endswith("_") and "\n" not in chunk


def parse_entries(body: str) -> list[str]:
    """A saved chat's entries: each turn, yours or the Lead's, and Ombro's notes. A turn
    may run over several paragraphs; it ends where the next entry begins."""
    entries: list[str] = []
    for chunk in body.split("\n\n"):
        if ENTRY.match(chunk) or _is_note(chunk):
            entries.append(chunk)
        elif entries:
            entries[-1] += "\n\n" + chunk
    return [e.rstrip() for e in entries]


def turns(entries: list[str]):
    """(user or lead, text) for each turn in Chat mode; interviews and notes are left out."""
    for e in entries:
        m = ENTRY.match(e)
        if not m:
            continue
        text = e[m.end() :].strip()
        if m.group(1).startswith("You"):
            if m.group(2) == "Chat":
                yield "user", text
        elif m.group(1) == "Lead":
            yield "lead", text


def all_turns(entries: list[str]):
    """(user, lead or ombro, text) for everything said in a chat, in order: its turns, the
    interviews held in it, and Ombro's notices of what came back (a job's outcome)."""
    for e in entries:
        m = ENTRY.match(e)
        if not m:
            continue
        text = e[m.end() :].strip()
        who = "user" if m.group(1).startswith("You") else "ombro" if m.group(1) == "Ombro" else "lead"
        yield who, text


def interview_entries(path: Path | None, mode: str) -> list[str]:
    """An interview's saved transcript, as entries of the chat it was held in."""
    try:
        body = mdfile.read(Path(path))[1] if path else ""
    except (OSError, mdfile.FrontMatterError):
        return []
    out: list[str] = []
    for chunk in body.split("\n\n"):
        if chunk.startswith("# Interview"):
            continue
        if chunk.startswith("**Lead:**"):
            out.append(f"**Lead ({mode}):**" + chunk[len("**Lead:**") :])
        elif chunk.startswith("**You:**"):
            out.append(f"**You ({mode}):**" + chunk[len("**You:**") :])
        elif chunk.startswith("**Lead proposed:**") or chunk.startswith("**You edited it to:**"):
            out.append(f"**Lead ({mode}):** {chunk.split(':**', 1)[0][2:]}:" + chunk.split(":**", 1)[1])
        elif _is_note(chunk):
            out.append(chunk.strip())
        elif out:
            out[-1] += "\n\n" + chunk
    return [e.rstrip() for e in out]


def _add_usage(before: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    out = dict(now)
    for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls"):
        out[k] = int(before.get(k) or 0) + int(now.get(k) or 0)
    tools = dict(before.get("tool_calls") or {})
    for k, v in (now.get("tool_calls") or {}).items():
        tools[k] = int(tools.get(k) or 0) + int(v or 0)
    out["tool_calls"] = tools
    return out


class Transcript:
    def __init__(self):
        self.started = utc_now()
        self.entries: list[str] = []

    def user(self, text: str, mode: str) -> None:
        self.entries.append(f"**You ({mode}):** {text.strip()}")

    def lead(self, text: str) -> None:
        self.entries.append(f"**Lead:** {text.strip()}")

    def ombro(self, text: str) -> None:
        self.entries.append(f"**Ombro:** {text.strip()}")

    def note(self, text: str) -> None:
        self.entries.append(f"_{text.strip()}_")


class BriefModeUI(UI):
    """The chat's interface while a brief is held inside it: every question is
    asked in Brief mode, and /chat leaves the brief with nothing written."""

    def __init__(self, inner: UI):
        self.inner = inner

    def show(self, event) -> None:
        self.inner.show(event)

    def ask(self, f: Field):
        from dataclasses import replace

        answer = self.inner.ask(replace(f, mode="Brief"))
        if isinstance(answer, str) and answer.strip().lower() == "/chat":
            raise KeyboardInterrupt
        return answer

    def edit(self, paths: list[Path]) -> None:
        self.inner.edit(paths)


class Chat:
    def __init__(
        self,
        home: Home,
        *,
        crew: str | None = None,
        ask: Callable[[str], str] | None = None,
        say: Callable[[str], None] | None = None,
        edit: Callable[[list[Path]], None] | None = None,
        ui: UI | None = None,
        client_factory: Callable[[ModelSpec], ChatClient] | None = None,
        clock: Callable[[], float] = time.time,
        today: date | None = None,
        registry: ToolRegistry | None = None,
        titles: bool = True,
    ):
        self.home = home
        self.take_paths = False  # the terminal: a file path dragged into a message attaches the file
        self.titles = titles  # name a chat after its first exchange, with one small model call
        prefs = load_preferences(home)
        self.instructions = prefs.instructions
        self.crew = crew or prefs.default_crew
        if self.crew not in home.crews():
            raise SetupError(f"no crew named {self.crew!r}; crews: {', '.join(home.crews()) or 'none'}")
        self.ui = ui or ConsoleUI(ask=ask, say=say, edit=edit, home=home)
        self.base_limits = load_limits(home)
        self.limits = self.base_limits.for_chat()
        self.models = load_models(home)
        self.lead = load_lead(home, self.base_limits)
        self.spec = self.models.get(self.lead.model) or self.models["default"]
        self._client_factory = client_factory or (lambda spec: make_client(spec, home))
        self._client: ChatClient | None = None
        self.clock = clock
        self.gate = Gate(self.limits, clock, what="chat")
        self.today_date = today or date.today()
        self.today = prompts.format_date(self.today_date)
        if registry is None:
            from ombro.native import native_registry

            registry = native_registry(home, load_tool_settings(home))
        self.registry = registry
        self._lookups: dict[str, Any] | None = None  # wire name: Tool, made on the first model call
        self._loaded: set[str] = set()  # wire names of the outside services' tools the Lead has loaded
        self._pool = None  # the MCP servers the lookups reach, started then
        self.mode = "Chat"
        self.exchanges: list[list[dict]] = []  # each starts with a user message
        self.user_said: list[str] = []
        self.dropped = 0
        self.pending_note = ""
        self.transcript = Transcript()
        self.scope = None  # outward.Scope: what this chat made and what you said yes to (see _act)
        self.carry = carry_mod.Carry()  # private material read in this chat, against what the browser carries
        self.refused: list[str] = []
        self.remembered: list[dict[str, str]] = []
        self.specs: list[str] = []
        self.start_offers: list[str] = []  # saved specs the Lead offered to start; the interface asks the user
        # Called with the chat's file when it is first written or renamed: a window lists it at once.
        self.on_saved: Callable[[Path], None] = lambda path: None
        self._announced: Path | None = None
        self._fresh()

    def _fresh(self) -> None:
        """Everything one chat keeps: none of it carries into the next."""
        self.gate = Gate(self.limits, self.clock, what="chat")
        self.mode = "Chat"
        self.exchanges = []
        self.user_said = []
        self.dropped = 0
        self.pending_note = ""
        self.transcript = Transcript()
        self.refused = []
        self.tainted = False  # set once the chat reads material (a page, an email...): see _act
        self.remembered = []
        self.specs = []
        self.saved_briefs: list[str] = []  # live jobs kept as briefs (not offered to start again)
        self.attached: list[str] = []  # files attached in this chat (under Files/): given to its briefs and jobs
        self.turn_images: list[Path] = []  # images attached to the message being answered: shown to a model that sees
        self.ended = "you ended the chat"
        # When the chat was last used, and how many turns (yours and the Lead's) it had then:
        # saving it with no new turn keeps that time, so opening or closing a chat, or Ombro's
        # own notes in it, don't move it up the list of chats.
        self.last_used = ""
        self.turns_seen = 0
        self.title: str | None = None
        self.titled_by: str | None = None  # model, spec or you; a title you set is never replaced
        self.prior_usage: dict[str, Any] = {}  # what the chat used before it was reopened
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M")
        self.id = f"{self.stamp}-{secrets.token_hex(3)}"  # stays when the file is renamed after its title
        self.jobs: list[str] = []  # jobs started from this chat
        self.path = self._transcript_path()

    def reload_limits(self) -> None:
        """The allowance changed: this chat's cap follows at once, keeping what it has used."""
        self.base_limits = load_limits(self.home)
        self.limits = self.base_limits.for_chat()
        self.gate.limits = self.limits
        self._usage_line()

    def new_chat(self) -> None:
        """/new: save this chat and start a fresh one, with its own token cap."""
        self.ended = "you started a new chat"
        self._save()
        old_title = self.title
        self._fresh()
        self.ui.show(ChatCleared())
        saved = f"I've saved the last chat, '{old_title}'." if old_title else "I've saved the last chat."
        self.ui.say(f"{saved} This is a new chat. How would you like me to help?", level="lead")
        self._usage_line()

    # -- titles ------------------------------------------------------------

    def set_title(self, title: str, by: str) -> bool:
        """Give the chat a title, and its file a name to match. A title you set stays."""
        title = " ".join(str(title or "").replace("\n", " ").split()).strip("\"'“”‘’ .:#*")[:70].strip()
        if not title or (self.titled_by == "you" and by != "you"):
            return False
        self.title, self.titled_by = title, by
        from ombro.jobs import job_slug

        folder = self.path.parent
        new = folder / f"{job_slug({'name': title})}-{self.stamp}.md"
        n = 2
        while new.exists() and new != self.path:
            new = folder / f"{job_slug({'name': title})}-{self.stamp}-{n}.md"
            n += 1
        if new != self.path:
            if self.path.exists():
                self.path.rename(new)
            self.path = new
        self._save()
        return True

    def _auto_title(self) -> None:
        """After the first exchange: a title of a few words, from one small model call.
        Its tokens count as the chat's. If it fails, the chat goes untitled for now."""
        said = [m for ex in self.exchanges for m in ex if m.get("role") in ("user", "assistant") and m.get("content")]
        if not said:
            return
        text = "\n".join(f"{'You' if m['role'] == 'user' else 'Lead'}: {_short(m['content'], 600)}" for m in said[:4])
        messages = [
            {
                "role": "user",
                "content": "Give this conversation a title of two to five words, in title "
                "case, saying what it is about. Reply with the title only, no quotes.\n\n" + text,
            }
        ]
        first = next((m["content"] for m in said if m["role"] == "user"), "")
        try:
            spec = self.models.get("cheap") or self.spec
            est = estimate_tokens(messages, None)
            # Room for a thinking model to think first: with 30 tokens DeepSeek's spent them all
            # thinking and the title came back empty, so most chats stayed "New chat".
            max_out, timeout = self.gate.check_model_call(spec.name, est, TITLE_MAX_OUTPUT)
            reply = self._client_factory(spec).complete(
                messages, tools=None, max_output_tokens=max_out, timeout=min(timeout, 30)
            )
            u = reply.usage
            self.gate.record_model_call(
                spec.name, u.input_tokens or est, min(u.output_tokens, max_out), estimated=u.estimated
            )
            title = (reply.content or "").strip().splitlines()[0] if (reply.content or "").strip() else ""
        except (ModelError, LimitReached, OSError):
            title = ""
        if not self.set_title(title, "model") and not self.title:
            self.set_title(words_title(first), "first words")  # the model is tried again after the next exchange

    # -- past chats --------------------------------------------------------

    def reopen(self, path: Path) -> None:
        """Carry on a past chat: its words (yours and the Lead's) are sent again, as any
        conversation's are, and new turns are added to the same file. Old tool results are
        not kept, so the Lead looks things up again if it needs them."""
        meta, body = mdfile.read(path)
        self.ended = "you opened another chat"
        self._save()
        self._fresh()
        entries = parse_entries(body)
        self.path = path
        self.title = meta.get("title") or None
        self.titled_by = meta.get("titled_by") or ("model" if self.title else None)
        self.prior_usage = dict(meta.get("usage") or {})
        if meta.get("default_crew") in self.home.crews():
            self.crew = meta["default_crew"]
        m = jobs.WHEN.search(path.stem)
        self.stamp = f"{m.group(1)}-{m.group(2)[:4]}" if m else self.stamp
        self.transcript.started = str(meta.get("started_at") or self.transcript.started)
        self.transcript.entries = entries
        self.last_used = str(meta.get("updated_at") or "")
        self.turns_seen = len(list(turns(entries)))
        self.remembered = list(meta.get("remembered") or [])
        self.specs = list(meta.get("specs_saved") or [])
        self.id = str(meta.get("id") or self.id)
        self.jobs = [str(j) for j in meta.get("jobs") or []]
        self.attached = [str(a) for a in meta.get("attached") or []]
        self.tainted = bool(meta.get("tainted"))  # what it read may still steer the Lead's words
        # Everything said in it comes back, for the Lead too: its turns, the interviews held in it
        # (your answers and its questions), and Ombro's notices of what came back.
        notes = ""
        for kind, text in all_turns(entries):
            if kind == "ombro":
                notes += f"[Ombro note: {text}]\n"
            elif kind == "user":
                self.exchanges.append([{"role": "user", "content": notes + text}])
                self.user_said.append(text)
                notes = ""
            elif self.exchanges:
                self.exchanges[-1].append({"role": "assistant", "content": text})
        self.transcript.note(f"Reopened {utc_now()}.")
        self.pending_note = notes + (
            "[Ombro note: the user reopened this chat to carry on. Tool results from "
            "before are not kept; look things up again if you need them.]\n"
        )
        self.ui.show(ChatCleared())
        for who, text in all_turns(entries):
            if who == "ombro":
                self.ui.show(Note(text, level="done"))
            else:
                self.ui.show(PastTurn("you" if who == "user" else "lead", text))
        self._save()

    def attach(self, path: Path) -> None:
        """A file you attached (kept under Files/ already): its text goes to the Lead with your next
        message, as material; a file with no text to read is named, and where it is. Briefs and live
        jobs made in this chat are given it, in their workspace/inputs/."""
        from ombro import attachments

        path = Path(path)
        got = attachments.read(path)
        size = attachments.size_text(path.stat().st_size) if path.is_file() else "gone"
        kinds = {"pdf": "PDF", "word": "Word document", "image": "image", "text": "text", "other": "file"}
        where = self._shown_path(path)
        if str(path) not in self.attached:
            self.attached.append(str(path))
        self.transcript.note(f"You attached {path.name} ({kinds[got.kind]}, {size}), kept at {where}.")
        given = f"If this becomes a brief or a live job, Ombro gives the job this file as workspace/inputs/{path.name}."
        if got.kind == "image" and attachments.seeable(path):
            self.turn_images.append(path)
            self.pending_note += (
                f"[Ombro note: the user attached the image {path.name} ({size}), shown to you with "
                f"this message if your model can see images. {given}]\n"
            )
        elif got.text is not None:
            cut = f" It is cut at {len(got.text):,} characters; the whole file goes to a job." if got.cut else ""
            self.pending_note += (
                f"[Ombro note: the user attached {path.name} ({kinds[got.kind]}, {size}). Its "
                f"text follows, as material from the user's file.{cut} {given}]\n"
                + material.fence(got.text, f"the file {path.name}, attached by the user")
                + "\n"
            )
        else:
            why = f" ({got.problem})" if got.problem else ""
            self.pending_note += (
                f"[Ombro note: the user attached {path.name} ({kinds[got.kind]}, {size}), kept "
                f"at {where}. You can't read it in the chat{why}. {given}]\n"
            )

    def _take_dragged(self, text: str) -> str:
        """In the terminal, files dragged into a message are attached (copied into Files/ first)."""
        from ombro import attachments

        rest, paths = attachments.dragged_paths(text)
        names = []
        for p in paths:
            try:
                kept = attachments.save_path(self.home, p)
            except (attachments.AttachError, OSError) as e:
                self.ui.say(f"{p.name} wasn't attached: {e}.")
                continue
            self.attach(kept)
            names.append(kept.name)
        if not names:
            return text
        self.ui.say(f"Attached {', '.join(names)}.", level="done")
        return f"I've attached {', '.join(names)}." if rest in (p.name for p in paths) or not rest else rest

    def _shown_path(self, path: Path) -> str:
        try:
            return "~/" + path.resolve().relative_to(Path.home()).as_posix()
        except ValueError:
            return str(path)

    def discuss_brief(self, job_dir: Path) -> None:
        """A new chat about the brief a job ran from (Discuss this brief, on its brief page): the Lead
        says it has it and asks what to do with it; the brief goes to the Lead with your answer."""
        meta = jobs.read_job(job_dir)[0]
        name = _short(meta.get("brief") or jobs.job_name(job_dir.name), 80)
        source = str(meta.get("source") or "")
        spec = Path(source.removeprefix("spec file ")).stem if source.startswith("spec file ") else ""
        if spec and not (self.home.specs_dir / f"{spec}.md").is_file():
            spec = ""
        text = jobs.brief_text(job_dir) or name
        self.set_title(f"Brief: {name}", "ombro")
        self._lead_says(
            f"I've got the brief '{name}'. What would you like to do with it? I can change it, "
            "start it again (as it is, or changed), or you can put it on a schedule."
        )
        how = (
            f"It is saved as the spec `{spec}`: to start it again use offer_start with spec `{spec}`; to "
            f"change it, offer_brief with revise `{spec}`; to put it on a schedule, offer_schedule with spec "
            f"`{spec}`."
            if spec
            else "It has no saved spec (it was a live job, or its spec was removed): to run it again or change it, "
            "use offer_brief with the brief as the starting point; once saved it can be scheduled with "
            "offer_schedule."
        )
        self.pending_note += (
            f"[Ombro note: the user opened this chat from the Jobs page to discuss the brief of "
            f"job {job_dir.name}, and you have asked what they would like to do with it. {how} "
            f"The brief, as the job ran it, follows.]\n\n{text[:BRIEF_FOUND_CHARS]}\n\n"
            "[Ombro note: end of the brief.]\n"
        )
        self._save()

    def _lead_says(self, text: str, **kw: Any) -> None:
        """Something the Lead says in the chat outside its replies (an offer, a question about a file):
        shown, and kept in the chat, so a reopened chat has it."""
        kw.pop("level", None)
        self.ui.say(text, level="lead", **kw)
        self.transcript.lead(text)

    def job_ended(self, job_id: str, text: str, status: str, again_text: str = "", again: str = "") -> None:
        """A job started from this chat has ended: set down in the chat, for you and the Lead. One
        that didn't finish comes with the Lead's offer to restart or resume it (the interface shows
        the buttons), which the Lead is told of, so it can make it again if asked."""
        if job_id not in self.jobs:
            return
        line = f"{text} ('{jobs.job_name(job_id)}', job {job_id}; its report is jobs/{job_id}/report.md.)"
        self.transcript.ombro(line)
        self.pending_note += f"[Ombro note: {line}]\n"
        if again_text:
            self.transcript.lead(again_text)
            self.pending_note += (
                f'[Ombro note: it did not finish, and you offered to {again} it: "{again_text}" '
                f"If the user wants that, call offer_start with job {job_id!r}.]\n"
            )
        self._save()

    def note_job(self, job_id: str) -> None:
        """A job was started from this chat: kept in its record, and the job keeps the chat's id."""
        if job_id not in self.jobs:
            self.jobs.append(job_id)
            self.transcript.note(f"Job {job_id} started.")
            self._save()

    def history(self) -> list[tuple[str, str]]:
        """The conversation so far, as (you or lead, text), for an interface to show again."""
        return list(turns(self.transcript.entries))

    # -- plumbing ----------------------------------------------------------

    def _input(self, label: str) -> str:
        return self._scrub(self.ui.ask(Field("message", label, "text", multiline=True, mode="Chat")))

    def _scrub(self, raw: str | None) -> str:
        text, removed = keys.scrub(self.home, (raw or "").strip())
        if removed:
            self.ui.say(
                "(Something that looked like a key was removed before it went any further. "
                "Keys are set with `ombro keys set NAME`.)"
            )
        return text

    def lookups(self) -> dict[str, Any]:
        """The outside services' tools the Lead may use itself in chat: every web API's, Composio's
        and MCP server's in tools.md, those that change things too (each asks the user first when a
        job's would wait for approval: see _act). A tool set to `permission: refuse` is left out.
        Made once per chat."""
        if self._lookups is not None:
            return self._lookups
        from ombro import outward
        from ombro.apitool import api_tools

        found: dict[str, Any] = {}
        try:
            settings = load_tool_settings(self.home)
        except (SetupError, mdfile.FrontMatterError):
            self._lookups = found
            return found
        for t in api_tools(self.home, settings.apis):
            if not outward.refused(t.name, settings):
                found[t.wire_name] = t
        if settings.composio:  # Composio's lookups, listed by name in tools__load like an MCP server's
            from ombro import composio

            listed, problems = composio.tools(self.home, settings)
            for p in problems:
                self.transcript.note(p)
            found.update({t.wire_name: t for t in listed if not outward.refused(t.name, settings)})
        if settings.servers:
            from ombro.mcp import McpError

            self._pool = outward.McpPool(self.home, settings)
            names = []
            for server in sorted(settings.servers):
                try:
                    listed = self._pool.client(server).list_tools()
                except (McpError, OSError) as e:
                    self.transcript.note(f"MCP server {server} could not be used for lookups: {e}")
                    continue
                for t in listed:
                    name = f"mcp.{server}.{t.name}"
                    if not outward.refused(name, settings):
                        names.append(name)
            tools, _ = self._pool.tools(names) if names else ([], [])
            found.update({t.wire_name: t for t in tools})
        self._lookups = found
        return found

    def lookup_schemas(self, read_only: bool = False) -> list[dict]:
        """The lookups' definitions for one model call: outside services' tools (mcp.*) only once the Lead has
        loaded them with tools__load, which lists them by name; everything else in full. read_only: only
        those that change nothing (a brief held in the chat)."""
        found = self.lookups()
        if read_only:
            found = {w: t for w, t in found.items() if t.effect == "read-only"}
        deferred = [t for t in found.values() if is_deferred(t)]
        out = [t.schema() for t in found.values() if not is_deferred(t)]
        if deferred:
            out.append(load_tool_schema(deferred))
            out += [found[w].schema() for w in sorted(self._loaded) if w in found]
        return out

    def _model_call(self, messages: list[dict]):
        tools = prompts.CHAT_TOOLS + [t.schema() for n in WEB_TOOLS.values() if (t := self.registry.get(n))]
        tools += [
            t.schema()
            for n in NATIVE_TOOLS.values()
            if (t := self.registry.get(n)) and not (n == "code.run" and self._no_code())
        ]
        tools += self.lookup_schemas()
        if not skills.library(self.home):  # nothing to read: an empty library costs no tool
            tools = [t for t in tools if t["function"]["name"] != "skills__read"]
        est = estimate_tokens(messages, tools) + IMAGE_TOKENS * len(self.turn_images)
        max_out, timeout = self.gate.check_model_call(self.spec.name, est, None)
        if self._client is None:
            self._client = self._client_factory(self.spec)
        from ombro import attachments

        sent = attachments.with_images(messages, self.turn_images) if self.turn_images else messages
        try:
            reply = self._client.complete(sent, tools=tools, max_output_tokens=max_out, timeout=min(timeout, 300))
        except ModelError as e:
            self.gate.record_lost_calls(self.spec.name, e.lost_attempts, est, max_out)
            if not self.turn_images:
                raise
            # Most likely the model can't see images: carry on without them, and say so.
            names = ", ".join(p.name for p in self.turn_images)
            self.turn_images = []
            self.transcript.note(f"The model couldn't take {names} as an image ({_short(str(e), 200)}).")
            self.ui.say(
                f"This model couldn't take {names} as an image, so the Lead answers without seeing it. A "
                "brief or live job from this chat still gets the file."
            )
            return self._model_call(messages)
        self.gate.record_lost_calls(self.spec.name, reply.lost_attempts, est, max_out)
        u = reply.usage
        in_tokens = u.input_tokens if u.input_tokens or not u.estimated else est
        out_tokens = min(u.output_tokens, max_out) if u.estimated else u.output_tokens
        self.gate.record_model_call(
            self.spec.name,
            in_tokens,
            out_tokens,
            estimated=u.estimated,
            cached=u.cached_input,
            reasoning=getattr(u, "reasoning", 0),
        )
        return reply

    def _crews_text(self) -> str:
        lines = []
        for c in self.home.crews():
            meta = {}
            if (self.home.crew_dir(c) / "crew.md").is_file():
                meta = mdfile.read(self.home.crew_dir(c) / "crew.md")[0]
            rdir = self.home.roles_dir(c)
            roles = (
                sorted(p.name for p in rdir.iterdir() if p.is_dir() and not p.name.startswith("."))
                if rdir.is_dir()
                else []
            )
            all_jobs = jobs.all_jobs(self.home, c)
            latest = jobs.job_time(all_jobs[-1])[:8] if all_jobs else ""
            when = f", latest {latest[:4]}-{latest[4:6]}-{latest[6:8]}" if latest else ""
            mark = ", the default crew" if c == self.crew else ""
            lines.append(
                f"- {crew_phrase(self.home, c)} (crew name `{c}` for tools{mark}): "
                f"{meta.get('description', '')} Roles: {', '.join(roles) or 'none'}. "
                f"Jobs: {len(all_jobs)}{when}."
            )
        lines.append("Speak of each crew by its title, never by its crew name.")
        lines.append(
            "\nFiles are under the home directory: jobs/<job_id>/report.md, spec.md and workspace/ "
            "(a job may have used several crews); specs/<name>.md; crews/<crew>/memory/notes.md and "
            "index.md."
        )
        return "\n".join(lines)

    def _folders_text(self) -> str:
        from ombro.folders import display_path

        ctx, left = self._shared()
        lines = [
            f"- shared/{n} ({'read and write' if f.writable else 'read only'}): {display_path(f.path)}"
            for n, f in ctx.shared.items()
        ]
        return "\n".join(lines + [f"- {x}" for x in left])

    def _limits_line(self) -> str:
        caps = ", ".join(f"{k} {v}" for k, v in self.limits.tool_max_calls.items() if k != "default")
        return (
            f"- tokens for this chat: {format_tokens(self.limits.max_tokens)}\n"
            f"- tool calls for this chat: {caps}\n"
            f"- model calls for one message: {self.limits.chat_calls_per_message}\n"
            f"- older turns stop being sent once the conversation passes "
            f"{self.limits.chat_history_chars:,} characters"
        )

    def _messages(self) -> list[dict]:
        system = prompts.chat_system(
            self.lead.soul,
            self.instructions,
            self._crews_text(),
            self._limits_line(),
            self.today,
            self._folders_text(),
            skills.listing(list(skills.library(self.home).values()), self.limits.skills_list_chars)[0],
            self._schedules_text(),
        )
        if _size(self.exchanges) > self.limits.chat_history_chars:
            self._trim()
        return [{"role": "system", "content": system}] + [m for ex in self.exchanges for m in ex]

    def _trim(self) -> None:
        """Bring a long chat down to TRIM_TO of chat_history_chars in one step: older tool results
        become a short note first, then turns are left out from the middle. The opening turns
        (HEAD_CHARS) and the latest exchange always stay. Until the chat fills up again, what is
        sent only grows at the end, so the provider's cache keeps serving it."""
        target = int(self.limits.chat_history_chars * TRIM_TO)
        keep_head = min(HEAD_CHARS, self.limits.chat_history_chars // 5)
        head, used = 0, 0
        for ex in self.exchanges[:-1]:
            used += _size([ex])
            if used > keep_head and head:
                break
            head += 1
        stubbed = 0
        for ex in self.exchanges[head : -RECENT_EXCHANGES or None]:
            for m in ex:
                text = m.get("content")
                if m.get("role") == "tool" and isinstance(text, str) and len(text) > STUB_OVER:
                    m["content"] = (
                        f"[Ombro note: this result ({len(text):,} characters) was cut to keep the "
                        f"chat in view; look it up again if you need it. It began: {text[:300]}…]"
                    )
                    stubbed += 1
        left_out = 0
        while _size(self.exchanges) > target and len(self.exchanges) > head + 1:
            self.exchanges.pop(head)
            left_out += 1
        if left_out:
            self.dropped += left_out
            first = self.exchanges[head][0]
            text = str(first.get("content") or "")
            if text.startswith(LEFT_OUT) and "turns were left out" in text.split("\n", 1)[0]:
                text = text.split("\n", 1)[1] if "\n" in text else ""
            first["content"] = (
                f"{LEFT_OUT}{self.dropped} earlier turns were left out here to keep this long "
                "chat in view; the opening and the recent turns are kept, and the transcript has "
                "everything.]\n" + text
            )
        if not (left_out or stubbed):
            return  # nothing more can go: the opening and the latest exchange are all there is
        self.ui.say(
            "(This chat is long, so I've trimmed what I keep in view: the start and the recent turns stay, "
            "older file contents are shortened, and the transcript keeps everything.)"
        )
        self.transcript.note(
            f"Trimmed what is kept in view: {stubbed} older results shortened, {left_out} turns left out."
        )

    def _usage_line(self) -> None:
        s = self.gate.snapshot()
        tools = sum((s.get("tool_calls") or {}).values())
        self.ui.show(Usage("chat", self.mode, s["counted_tokens"], self.limits.max_tokens, tools))

    # -- the conversation --------------------------------------------------

    def run(self) -> "Chat":
        """The command line's loop: start, one message at a time, close."""
        self.start()
        try:
            while True:
                try:
                    raw = self.ui.ask(Field("message", "You", "text", multiline=True, mode=self.mode))
                except (EOFError, KeyboardInterrupt):
                    self.ui.say("")
                    break
                if not self.handle(raw):
                    break
                while self.start_offers:  # the user said yes in chat; the command line starts it here
                    self._start_offered(self.start_offers.pop(0))
        finally:
            self.close()
        return self

    def start(self) -> None:
        self.ui.say(
            f"Chat with Ombro Lead (default crew: {crew_phrase(self.home, self.crew)}). Mode: Chat. Nothing "
            f"changes unless you say yes (a file in a shared folder, or a /remember). /brief turns this into a task "
            f"brief, /help lists commands, /quit ends. "
            f"(Up to {format_tokens(self.limits.max_tokens)} tokens.)\n"
        )

    def handle(self, raw: str) -> bool:
        """One message or command from the user, as typed: anything that looks
        like a key is removed first. Returns False when the chat has ended
        (/quit, or its limit)."""
        text = self._scrub(raw)
        if not text:
            return True
        if self.take_paths and not text.startswith("/"):
            text = self._take_dragged(text)
        try:
            is_message = self._command(text)
        except SetAside as e:
            self.set_aside(e)
            is_message = False
        if not is_message:
            if text.lower() in QUIT_WORDS:
                return False
            self._save()  # a remember or a brief is on record at once
            return True
        try:
            request = self._turn(text)
        except LimitReached as e:
            self.ended = f"stopped by the chat limit {e.limit}: {e.detail}"
            self.transcript.note(f"The chat stopped: {e.detail}")
            self.ui.show(Failure(f"This chat has reached its limit: {e.detail}. Start a new chat to carry on."))
            return False
        except ModelError as e:
            self.exchanges.pop()
            self.transcript.note(f"The model call failed: {e}")
            self.ui.show(Failure(f"I couldn't reach the model just now ({e}). Try again in a moment."))
            return True
        self._usage_line()
        if (
            self.titles
            and (not self.title or self.titled_by == "first words")
            and self.exchanges
            and self.mode == "Chat"
        ):
            self._auto_title()
        self._save()  # the reply and the title are on record before any brief or job begins
        try:
            if request and request[0] == "live":
                self._live(*request[1:])
            elif request and request[0] == "role":
                self._role(*request[1:])
            elif request:
                revise = request[2] if len(request) > 2 else None
                self._brief(request[0], self.brief_opening("", request[1]), revise=revise)
        except SetAside as e:  # after a live job: its "keep it as a brief?"
            self.set_aside(e)
        self._save()
        return True

    def set_aside(self, e: SetAside) -> None:
        """The user sent a new message instead of answering a question: it goes unanswered, and
        the transcript says so. The interface sends the message on next."""
        label = " ".join((e.field.label or e.field.name).split())
        self.transcript.note(f"You set the question aside ({_short(label, 120)}) and sent a new message instead.")
        self.ui.show(Note("Question set aside. Your message goes to the Lead."))

    def close(self) -> None:
        self._save()
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        close = getattr(self.registry, "close", None)
        if close is not None:
            close()  # the browser, if the Lead used it
        if self.path.exists():
            self.ui.say(f"Chat saved: {self.path}" + (f" ('{self.title}')" if self.title else ""))

    def _command(self, text: str) -> bool:
        """Handle a slash command. Returns True if the text is a message for the Lead."""
        low = text.lower()
        if not text.startswith("/"):
            return True
        if low in QUIT_WORDS:
            return False
        word, _, rest = text.partition(" ")
        word = word.lower()
        if word == "/help":
            self.ui.say(HELP)
        elif word == "/chat":
            self.ui.say("There's no brief open; we're just chatting.")
        elif word == "/new":
            self.new_chat()
        elif word == "/rename":
            if not rest.strip():
                self.ui.say("Give the title, for example: /rename Indie Publisher Market Research")
            elif self.set_title(rest, "you"):
                self.ui.say(f"This chat is now called '{self.title}'.", level="done")
        elif word == "/chats":
            self._pick_chat(rest.strip())
        elif word == "/brief":
            crew, rest = self._crew_arg(rest)
            if crew:
                self._brief([crew] if rest_named(text) else [], self.brief_opening(rest, None))
        elif word == "/live":
            crew, rest = self._crew_arg(rest)
            if crew and not rest.strip():
                self.ui.say("Say what to do, for example: /live find three bakeries in Leith that deliver")
            elif crew:
                self._live([crew], _short(rest, 70), rest.strip())
        elif word in ("/restart", "/resume"):
            self._again_command(word[1:], rest)
        elif word == "/skills":
            lib = skills.library(self.home)
            self.ui.say(
                "\n".join(f"- {s.name}: {s.description}" for s in lib.values())
                if lib
                else "No skills yet. Ask me to remember how to do something, and I can write one."
            )
        elif word == "/remember":
            crew, rest = self._crew_arg(rest)
            if crew:
                if not rest.strip():
                    rest = self._input("What should be remembered?")
                if rest.strip():
                    result = self._remember(rest, crew, offered=False)
                    if result.get("status") == "saved":
                        self.pending_note += (
                            f"[Ombro note: the user added this to crew {crew}'s notes.md: {result['entry']}]\n"
                        )
        else:
            self.ui.say(f"Unknown command {word}. /help lists them.")
        return False

    def _crew_arg(self, rest: str) -> tuple[str | None, str]:
        rest = rest.strip()
        if not rest.startswith("@"):
            return self.crew, rest
        given, _, remainder = rest[1:].partition(" ")
        name = find_crew(self.home, given)
        if name is None:
            self.ui.say(
                f"There's no crew called {given}. The crews are "
                f"{', '.join(crew_title(self.home, c) for c in self.home.crews())}."
            )
            return None, remainder
        return name, remainder.strip()

    def _turn(self, text: str) -> tuple[list[str], str] | None:
        """One message to the Lead and its answer. Returns (crews, summary) if the
        user accepted an offer to brief."""
        self.gate._seen.clear()  # loop detection counts repeats within one message
        self.gate.begin_turn()  # the time limit is per message: a chat left open overnight has not used it
        self.exchanges.append([{"role": "user", "content": self.pending_note + text}])
        self.pending_note = ""
        try:
            return self._answer_turn(text)
        finally:
            self.turn_images = []  # shown with the message they came with; the Lead's answer carries them on

    def _answer_turn(self, text: str) -> tuple[list[str], str] | None:
        self.user_said.append(text)
        self.transcript.user(text, self.mode)
        # On record before any reply: a chat whose Lead is still answering, or asking a
        # question, is in the list of chats, so clicking away or quitting never loses it.
        if not self.title and self.mode == "Chat":
            self.set_title(words_title(text), "first words")  # a model title follows after the reply
        self._save()
        for _ in range(self.limits.chat_calls_per_message):
            reply = self._model_call(self._messages())
            self.exchanges[-1].append(reply.as_message())
            said = (reply.content or "").strip()
            offering = any(
                tc.name in ("offer_start", "offer_brief", "offer_work", "offer_schedule")
                for tc in reply.tool_calls or []
            )
            if said and offering:
                # The offer says it in the Lead's voice; the same words twice read as a stutter.
                self.transcript.note(f"The Lead's words with its offer, not shown: {_short(said, 300)}")
            elif said:
                self.ui.show(LeadSaid(said))
                self.transcript.lead(said)
            if not reply.tool_calls:
                return None
            request, aside = None, None
            for tc in reply.tool_calls:
                if aside:
                    result = {"error": "ignored: the user sent a new message"}
                elif request:
                    result = {"error": "ignored: the brief is starting"}
                else:
                    try:
                        result, request = self._tool(tc)
                    except SetAside as e:
                        aside, result = e, dict(SET_ASIDE_RESULT)
                src = None
                if (
                    tc.name not in OFFERS
                    and tc.name
                    not in (
                        "folders__write",
                        "folders__set_aside",
                        "folders__copy",
                        "folders__edit",
                        LOAD_NAME,
                        "skills__read",
                        "browser__type",
                        "browser__screenshot",
                    )
                    and not (isinstance(result, dict) and ("error" in result or "declined" in result))
                ):
                    lookup = (self._lookups or {}).get(tc.name)
                    used = (
                        MEMORY_TOOLS.get(tc.name)
                        or WEB_TOOLS.get(tc.name)
                        or FOLDER_TOOLS.get(tc.name)
                        or NATIVE_TOOLS.get(tc.name)
                        or (lookup.name if lookup else tc.name)
                    )
                    src = material.source(used, tc.arguments)
                    self.tainted = True  # this chat has read material from outside
                    if carry_mod.is_private(
                        used,
                        outward=bool(lookup is not None and lookup.outward),
                        args=tc.arguments,
                        shared=tc.name == "code__run" and bool(self._shared()[0].shared),
                    ):
                        self.carry.saw(src, result)
                    signs = material.flags(result)
                    if signs:
                        # Ombro's own words, not the Lead's: what to check, never a block.
                        line = (
                            f"Ombro: {src} contains text that looks written to steer the Lead "
                            f"({', '.join(signs)}). It is treated as material, and nothing was done because "
                            "of it."
                        )
                        self.ui.say(line)
                        self.transcript.note(line)
                self.exchanges[-1].append(_tool_msg(tc.id, result, src))
            if aside:
                self.set_aside(aside)
                return None
            if request:
                return request
        self._last_word()
        return None

    def _last_word(self) -> None:
        """The calls for one message have run out: one more call, told to answer now with what it has
        (a draft, what it found, what is left), so a message never ends with nothing. Any tool it
        still asks for is not run."""
        n = self.limits.chat_calls_per_message
        said = ""
        try:
            reply = self._model_call(self._messages() + [{"role": "system", "content": prompts.CHAT_LAST_CALL}])
            said = (reply.content or "").strip()
        except (ModelError, LimitReached, OSError) as e:
            self.transcript.note(f"The Lead's last answer for this message could not be had: {_short(str(e), 200)}")
        if said:
            self.exchanges[-1].append({"role": "assistant", "content": said})
            self.ui.show(LeadSaid(said))
            self.transcript.lead(said)
            self.ui.say(
                f"(That used all {n} model calls allowed for one message, so the Lead stopped there. "
                "Say carry on to let it continue.)"
            )
            self.transcript.note("The Lead used all its model calls for this message and answered with what it had.")
            return
        self.ui.say(
            f"(The Lead used all {n} model calls allowed for one message without answering. Say carry on, "
            "or ask again more narrowly.)"
        )
        self.transcript.note("The Lead used all its model calls for this message without answering.")

    # -- tools -------------------------------------------------------------

    def _tool(self, tc) -> tuple[dict, tuple[list[str], str] | None]:
        name = tc.name
        if name == LOAD_NAME:
            deferred = {w: t for w, t in self.lookups().items() if is_deferred(t)}
            if deferred:
                return load_tools(tc.arguments, deferred, self._loaded), None
        lookup = (self._lookups or {}).get(name)
        internal = (
            MEMORY_TOOLS.get(name)
            or WEB_TOOLS.get(name)
            or FOLDER_TOOLS.get(name)
            or SKILL_TOOLS.get(name)
            or NATIVE_TOOLS.get(name)
            or (lookup.name if lookup else None)
        )
        if name not in OFFERS and (
            internal is None or ((name in WEB_TOOLS or name in NATIVE_TOOLS) and self.registry.get(internal) is None)
        ):
            self.refused.append(name)
            self.transcript.note(f"Refused {name}: not available in chat.")
            return {
                "error": f"{name} is not available in chat. Chat cannot brief roles, start jobs or approve the "
                "waiting actions; files are written only in shared folders, with folders__write. "
                "To give this to the crew, call offer_work."
            }, None
        key = tc.raw_arguments if tc.arguments is None else json.dumps(tc.arguments, sort_keys=True)
        try:
            self.gate.check_tool_call("lead", internal or name, key, brief=True, capped=internal is not None)
        except (BriefLimit, ToolRefused) as e:
            self.transcript.note(f"Refused {internal or name}: {e.detail}")
            return {"error": f"{e.detail}. Answer with what you have."}, None
        args = tc.arguments
        if args is None:
            return {"error": "the arguments were not a JSON object"}, None
        if name == "offer_brief":
            return self._offer_brief(args)
        if name == "offer_role":
            return self._offer_role(args)
        if name == "offer_work":
            return self._offer_work(args)
        if name == "offer_start":
            return self._offer_start(args)
        if name == "offer_remember":
            crew = str(args.get("crew") or self.crew)
            if crew not in self.home.crews():
                return {"error": f"no crew named {crew!r}; crews: {', '.join(self.home.crews())}"}, None
            return self._remember(str(args.get("text") or ""), crew, offered=True), None
        if name == "offer_skill":
            return self._offer_skill(args), None
        if name == "offer_schedule":
            return self._offer_schedule(args), None
        self.transcript.note(f"Lead used {internal}: {_short(json.dumps(args, ensure_ascii=False), 160)}")
        try:
            if internal == "memory.search":
                crew = str(args.get("crew") or "")
                if crew and crew not in self.home.crews():
                    return {"error": f"no crew named {crew!r}; crews: {', '.join(self.home.crews())}"}, None
                return memory.search(
                    self.home,
                    [crew] if crew else self.home.crews(),
                    str(args.get("query") or ""),
                    job_id=str(args.get("job_id") or "") or None,
                    max_chars=self.limits.memory_search_max_chars,
                ), None
            if internal == "memory.read":
                return memory.read_file(
                    self.home,
                    args.get("path"),
                    args.get("offset") or 0,
                    args.get("max_chars") or memory.READ_DEFAULT_CHARS,
                ), None
            if internal == "skills.read":
                return skills.read(
                    self.home, None, args.get("name"), args.get("file"), self.base_limits.skill_read_chars
                ), None
            if internal == "folders.write":
                offered = self._write_as_work(args)
                if offered is not None:
                    return offered
            if internal in FOLDER_TOOLS.values():
                return self._folder_tool(internal, args), None
            if internal == "memory.list":
                return memory.listing(
                    self.home, str(args.get("crew") or "") or None, str(args.get("job_id") or "") or None
                ), None
            tool = lookup or self.registry.get(internal)
            if tool.outward and tool.effect != "read-only":
                return self._act(tool, dict(args)), None
            return tool.handler(args, self._work_context(internal)), None
        except (memory.ReadRefused, skills.SkillError) as e:
            return {"error": str(e)}, None
        except ToolError as e:
            return {"error": str(e)}, None
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}, None

    def _no_code(self) -> bool:
        if not hasattr(self, "_code_reason"):
            from ombro import sandbox

            self._code_reason = sandbox.unavailable_reason()
        return bool(self._code_reason)

    def _work_context(self, tool: str = "") -> ToolContext:
        """Where the Lead's own work in chat goes: a folder of the chat's own (its workspace/ and
        scratch/, for screenshots and scripts), with the shared folders, under the chat's limits."""
        from ombro import attachments

        folder = attachments.files_dir(self.home) / "chats" / self.id
        for area in ("workspace", "scratch"):
            (folder / area).mkdir(parents=True, exist_ok=True)
        shared, _ = self._shared()
        timeout = self.limits.code_timeout if tool == "code.run" else 60.0
        return ToolContext(
            folder,
            "lead",
            timeout=timeout,
            shared=shared.shared,
            limits=self.limits,
            carry=self.carry,
            ask=self._carry_ask,
        )

    def _carry_ask(self, what: str, items: list[str]) -> bool:
        """The browser would carry what may be private material to a site (ombro.carry): you decide."""
        self._lead_says(f"I'd like to {what}.", level="lead", items=items)
        if self.ui.yes("tool_act", "Go ahead?", False, aside=True):
            return True
        self.transcript.note(f"The user said no to: {what}.")
        return False

    def _act(self, tool, args: dict) -> dict[str, Any]:
        """A tool that changes or sends something, used by the Lead in chat. The job's rules, with you
        here: what a job would stage for approval (and anything external) asks you first, in the chat.
        You are asked once per thing worked on: after a yes, later calls on the same sheet, document or
        page in the same service go ahead, unless they send, share, delete or spend (outward.Scope)."""
        from ombro import outward

        if self.scope is None:
            self.scope = outward.Scope()
        settings = load_tool_settings(self.home)
        reason = str(args.pop(tool.reason_key, "") or "").strip() if tool.reason_key else ""
        ctx = self._work_context(tool.name)
        if tool.prepare is not None:  # browser.submit: the page's refs become its address and fields
            args = tool.prepare(args, ctx)
        effect = str(args.pop("_effect", "") or tool.effect or "external")
        carried = bool(args.pop("_carry", False))  # would carry private material to a site: always asks
        effect = self.scope.effect(tool.name, args, effect)  # deleting in what this chat made is its own work
        setting = outward.resolve(self.home, None)
        decision = outward.decide(setting, tool.name, effect, settings)
        if decision == "refuse":
            return {"error": f"{tool.name} is refused by the user's settings."}
        if effect == "external" and (carried or tool.name not in (setting.get("allow") or [])):
            decision = "stage"  # reaches people outside, or spends: never without the user's yes in chat
        held = (
            decision == "allow"
            and effect == "internal"
            and self.tainted
            and tool.name not in (setting.get("allow") or [])
            and outward.held_after_reading(tool.name, args)
            and not self.scope.own(args)
        )
        if held:
            decision = "stage"  # after reading material, deleting or sharing asks, though other edits run
        covered = decision == "stage" and not held and not carried and self.scope.covered(tool.name, args)
        if covered:
            decision = "allow"  # the same thing you already said yes to: one yes covers the related calls
        problem = outward.validation_error(tool.input_schema, args)
        if problem:
            return {"error": problem}
        if decision == "stage":
            self._lead_says(
                f"I'd like to {self._act_words(tool.name, args)}" + (f": {reason}" if reason else "."),
                level="lead",
                items=self._act_items(tool.name, args),
            )
            if not self.ui.yes("tool_act", "Go ahead?", False, aside=True):
                self.transcript.note(f"The user said no to {tool.name}.")
                return {"declined": "The user said no, so nothing was done. Ask what they would like instead."}
        out = tool.handler(args, ctx) if tool.handler else {"ok": True}
        self.scope.done(tool.name, args, out, approved=decision == "stage")
        self.transcript.note(
            f"Used {tool.name}"
            + (
                " with the user's yes."
                if decision == "stage"
                else ", covered by the user's earlier yes for the same item."
                if covered
                else "."
            )
        )
        return {"status": "done", **(out if isinstance(out, dict) else {"result": out})}

    @staticmethod
    def _act_words(name: str, args: dict) -> str:
        if name == "browser.submit":
            from urllib.parse import urlsplit

            button = (args.get("button") or {}).get("label") or "its button"
            return f'submit the form on {urlsplit(str(args.get("url"))).netloc} ("{button}")'
        return f"use {name}"

    @staticmethod
    def _act_items(name: str, args: dict) -> list[str]:
        if name == "browser.submit":
            return [
                f"Page: {args.get('url')}",
                *[f"{f.get('field') or f.get('selector')}: {f.get('value')}" for f in args.get("fields") or []],
            ]
        from ombro import outward

        # Every argument in full, who or where it reaches first: the user approves all of what is sent.
        return outward.argument_lines(args)

    # -- shared folders ------------------------------------------------------

    def _shared(self) -> tuple[ToolContext, list[str]]:
        from ombro.folders import for_chat
        from ombro.tools import SharedFolder

        given, left = for_chat(self.home)
        shared = {f["name"]: SharedFolder(f["name"], Path(f["path"]), f["access"]) for f in given}
        # No job folder: chat reaches only shared/<name>/..., never workspace/ or scratch/.
        return ToolContext(self.home.chats_dir / ".none", "lead", timeout=60.0, shared=shared), left

    def _folder_tool(self, internal: str, args: dict) -> dict[str, Any]:
        from ombro import native

        ctx, left = self._shared()
        if not ctx.shared:
            return {
                "error": "No folders are shared yet. The user shares one with /folder add <path>."
                + (" " + " ".join(left) if left else "")
            }
        if internal == "folders.copy":
            return self._folder_copy(ctx, args)
        path = str(args.get("path") or "").strip()
        if internal == "folders.edit":
            self._chat_path(ctx, path)
            return self._folder_edit(ctx, path, args)
        if internal == "folders.list":
            if not path or path.strip("/") == "shared":
                out = native._list({"path": "shared"}, ctx)
                if left:
                    out["left_out"] = left
                return out
            self._chat_path(ctx, path)
            return native._list({"path": path}, ctx)
        self._chat_path(ctx, path)
        if internal == "folders.read":
            return native._read(
                {
                    "path": path,
                    "offset": args.get("offset") or 0,
                    "max_chars": args.get("max_chars") or memory.READ_DEFAULT_CHARS,
                },
                ctx,
            )
        if internal == "folders.set_aside":
            target = native.resolve_path(ctx, path, write=True)
            shelf = native.SET_ASIDE.get(str(args.get("as") or "for-deletion"), "For deletion")
            why = " ".join(str(args.get("why") or "").split())
            self._lead_says(
                f"I'd like to move {native._display(ctx, target.resolve())} into {shelf}/: {why}", level="lead"
            )
            if not self.ui.yes("folder_set_aside", "Go ahead?", False, aside=True):
                return {"declined": "The user said no, so nothing was moved."}
            out = native._set_aside({"path": path, "as": args.get("as") or "for-deletion", "why": why}, ctx)
            self.transcript.note(f"Moved {out['from']} to {out['to']}, with the user's yes.")
            self.ui.say(f"Moved it to {out['to']}.", level="done")
            return out
        # folders.write: the user sees what will be written, and says yes, before anything changes.
        target = native.resolve_path(ctx, path, write=True)
        content = args.get("content")
        if not isinstance(content, str):
            return {"error": "give the file's full text as content"}
        mode = "append" if args.get("mode") == "append" else "overwrite"
        shown = native._display(ctx, target.resolve())
        what = ("add to the end of" if mode == "append" else "replace") if target.exists() else "create"
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        kept = " and keep the current version in Archive/" if what == "replace" else ""
        self._lead_says(
            f"I'd like to {what} {shown}{kept} ({lines:,} lines, {len(content):,} characters).",
            level="lead",
            items=[content] if content else [],
        )  # in full; interfaces fold a long one
        if not self.ui.yes("folder_write", "Go ahead?", False, aside=True):
            self.transcript.note(f"The user said no to writing {shown}.")
            return {"declined": f"The user said no, so {shown} was not changed. Ask what they would like instead."}
        out = native._write({"path": path, "content": content, "mode": mode}, ctx)
        # Labelled by what was decided before the write: whether the file was there then, not the mode asked.
        done = {
            "create": f"Created {shown}",
            "replace": f"Replaced {shown} (the old version is in Archive/)",
            "add to the end of": f"Added to the end of {shown}",
        }[what]
        self.transcript.note(f"{done}, with the user's yes.")
        self.ui.say(f"Saved {shown}.", level="done")
        return out

    def _copy_source(self, ctx: ToolContext, path: str) -> tuple[Path, str]:
        """The file to copy or move: in a shared folder, this chat's own workspace/ or scratch/, or a job's
        files and reports as memory.read reaches them (jobs/<id>/...)."""
        from ombro import native

        if path.startswith(("shared/", "workspace/", "scratch/")):
            real = native.resolve_path(ctx, path)
            shown = native._display(ctx, real.resolve())
        else:
            try:
                real = memory.resolve_readable(self.home, path)
            except memory.ReadRefused as e:
                raise ToolError(str(e)) from None
            shown = self.home.rel(real)
        if real.is_symlink() or not real.is_file():
            raise ToolError(f"{path} is not a file Ombro can copy")
        return real, shown

    def _folder_copy(self, ctx: ToolContext, args: dict) -> dict[str, Any]:
        """Copy a file into a folder shared for writing, or move or rename one within such folders: a file
        operation, byte for byte, with the user's yes. Never a rewrite and never work for a crew."""
        import shutil

        from ombro import native

        src_arg, dst_arg = str(args.get("from") or "").strip(), str(args.get("to") or "").strip()
        if not src_arg or not dst_arg:
            return {"error": "give from (the file) and to (where it goes, shared/<name>/...)"}
        src, src_shown = self._copy_source(ctx, src_arg)
        self._chat_path(ctx, dst_arg)
        target = native.resolve_path(ctx, dst_arg, write=True)
        if dst_arg.endswith("/") or target.is_dir():
            target = native.resolve_path(ctx, dst_arg.rstrip("/") + "/" + src.name, write=True)
        if target.is_symlink():
            return {"error": f"{dst_arg} is a symlink; Ombro never writes through one"}
        move = bool(args.get("move"))
        if move:
            folder = _folder_of(ctx, src)
            if not folder or ctx.shared[folder].access != "write":
                return {
                    "error": f"only a file in a folder shared for writing can be moved; {src_shown} can be "
                    "copied instead (leave out move)"
                }
        if src.resolve() == target.resolve():
            return {"error": "from and to are the same file"}
        shown = native._display(ctx, target.resolve())
        replacing = target.exists()
        if replacing and not args.get("replace"):
            return {
                "error": f"{shown} exists already. Ask the user; to replace it, call again with replace: true "
                "(the current version is kept in Archive/)."
            }
        verb = "move" if move else "copy"
        kept_note = " and keep the current version in Archive/" if replacing else ""
        size = src.stat().st_size
        self._lead_says(
            f"I'd like to {verb} {src_shown} to {shown}{kept_note} ({size:,} bytes, as it is).", level="lead"
        )
        if not self.ui.yes("folder_write", "Go ahead?", False, aside=True):
            self.transcript.note(f"The user said no to {verb}ing {src_shown} to {shown}.")
            return {"declined": f"The user said no, so nothing was {'moved' if move else 'copied'}."}
        kept = native.archive_before_replacing(ctx, target) if replacing else None
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            os.replace(src, target)
        else:
            shutil.copy2(src, target)
        done = f"{'Moved' if move else 'Copied'} {src_shown} to {shown}"
        self.transcript.note(f"{done}, with the user's yes.")
        self.ui.say(f"{done}.", level="done")
        out: dict[str, Any] = {"from": src_shown, "to": shown, "bytes": target.stat().st_size, "moved": move}
        if kept:
            out["archived"] = f"The version before this one is kept in {kept}."
        return out

    def _folder_edit(self, ctx: ToolContext, path: str, args: dict) -> dict[str, Any]:
        """One exact replacement in a text file in a folder shared for writing: a correction or a detail
        filled in, with the user's yes. The file is not sent again, and this is never work for a crew."""
        from ombro import native

        old, new = args.get("old"), args.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            return {"error": "give path, old (the exact text to replace) and new"}
        if len(old) > EDIT_MAX_CHARS or len(new) > EDIT_MAX_CHARS:
            return {
                "error": f"an edit is a small correction, at most {EDIT_MAX_CHARS:,} characters each way; "
                "make several edits, or for a new document call offer_work"
            }
        target = native.resolve_path(ctx, path, write=True)
        if target.is_symlink() or not target.is_file():
            return {"error": f"{path} is not a file in a shared folder"}
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {"error": f"{path} can't be read as text"}
        n = text.count(old)
        if n != 1:
            return {"error": f"`old` occurs {n} times in {path}; give text that occurs exactly once"}
        shown = native._display(ctx, target.resolve())
        self._lead_says(
            f"I'd like to change this in {shown} (the current version is kept in Archive/):",
            level="lead",
            items=[f"Replace: {old}", f"With: {new}"],
        )  # in full, as for a write
        if not self.ui.yes("folder_write", "Go ahead?", False, aside=True):
            self.transcript.note(f"The user said no to editing {shown}.")
            return {"declined": f"The user said no, so {shown} was not changed."}
        out = native._write({"path": path, "content": text.replace(old, new, 1)}, ctx)
        self.transcript.note(f"Edited {shown}, with the user's yes.")
        self.ui.say(f"Saved {shown}.", level="done")
        return {**out, "replaced": 1}

    @staticmethod
    def _chat_path(ctx: ToolContext, path: str) -> None:
        if not path:
            raise ToolError("give a path such as shared/<name>/notes.md")
        if not path.startswith(("shared/", "/", "~")):
            raise ToolError(
                "in chat, files outside Ombro's home are reached as shared/<name>/... "
                "(folders: " + ", ".join(f"shared/{n} ({f.access})" for n, f in ctx.shared.items()) + ")"
            )

    def _saved_spec(self, name: Any) -> Path | None:
        name = str(name or "").strip().removesuffix(".md").removeprefix("specs/")
        path = self.home.specs_dir / f"{name}.md"
        return path if name and "/" not in name and path.is_file() else None

    def _offer_start(self, args: dict) -> tuple[dict[str, Any], tuple | None]:
        """The Lead offers to start a saved spec again, in its own voice: yes starts it (the
        interface does), "change it" opens the spec for changing, and no leaves it. Roles
        given here apply to this job only; the saved spec is unchanged."""
        if args.get("job") and not args.get("spec"):
            return self._offer_again(str(args["job"]), " ".join(str(args.get("note") or "").split()))
        path = self._saved_spec(args.get("spec"))
        if path is None:
            from ombro.cli import saved_specs

            names = [n for n, _ in saved_specs(self.home)][:15]
            return {
                "error": f"there is no saved spec called {args.get('spec')!r}. Saved specs, newest first: "
                + (", ".join(names) or "none")
            }, None
        name = path.stem
        meta = mdfile.read(path)[0]
        raw_crews = meta.get("crews") or [self.crew]
        crews = [str(c) for c in ([raw_crews] if isinstance(raw_crews, str) else raw_crews)]
        roles: list[str] = []
        if args.get("roles"):
            from ombro.roles import RoleError, load_roles, resolve_role

            try:
                available = load_roles(self.home, crews, self.base_limits)
            except (RoleError, SetupError) as e:
                return {"error": str(e)}, None
            for r in [args["roles"]] if isinstance(args["roles"], str) else list(args["roles"]):
                role, why = resolve_role(available, str(r))
                if role is None:
                    return {"error": f"roles: {why}"}, None
                roles.append(role.name)
        how = " ".join(str(args.get("how") or "").split()).rstrip(".")
        how = how or ("with " + ", ".join(roles) + " doing the work" if roles else "as it was")
        note = " ".join(str(args.get("note") or "").split())
        if note and not note.endswith((".", "!", "?")):
            note += "."
        self._lead_says(
            f"I found it. It's the spec '{name}'. " + (f"{note} " if note else "") + f"I can restart it {how}.",
            level="lead",
        )
        self.transcript.note(f"The Lead offered to start saved spec {name!r} again ({how}).")
        answer = self.ui.ask(
            Field(
                "start_now",
                'If you prefer to read through the spec again and change it, just say "change it". '
                "Otherwise, should I just start it?",
                "yesno",
                default=True,
                other="change it",
                mode="Chat",
                aside=True,
            )
        )
        if answer == "change it":
            self.transcript.note(f"You chose to change spec {name!r} first.")
            return (
                {"status": "the user wants to change the spec first; the interview starts now, so do not reply"},
                (crews, how, name),
            )
        if answer:
            self.start_offers.append((name, roles))
            self.transcript.note(
                f"You said yes: spec {name!r} starts" + (f" with {', '.join(roles)}" if roles else "") + "."
            )
            return {
                "status": f"the user said yes; Ombro starts spec {name!r} now in the background. Say in "
                "one short sentence that it is starting; do not describe the work again."
            }, None
        self.transcript.note(f"You said no: spec {name!r} was not started.")
        return {
            "status": "the user said no, so it was not started. Carry on the conversation; do not offer it "
            "again unless they ask."
        }, None

    def _unfinished_job(self, given: str) -> tuple[Path | None, str]:
        """The job to carry on: the one named, else this chat's latest that didn't finish. (dir, why not)."""
        from ombro.session import again_plan

        if given:
            try:
                job_dir = jobs.find_job(self.home, given)
            except (jobs.JobNotFound, LookupError):
                return None, f"There's no job matching {given!r}."
            return job_dir, ""
        for job_id in reversed(self.jobs):
            try:
                job_dir = jobs.find_job(self.home, job_id)
                if again_plan(jobs.read_job(job_dir)[0]):
                    return job_dir, ""
            except (jobs.JobNotFound, LookupError, OSError, mdfile.FrontMatterError):
                continue
        return None, "Say which job, for example: /resume " + (self.jobs[-1] if self.jobs else "<job>")

    def _again_command(self, kind: str, rest: str) -> None:
        """/restart or /resume <job> [more]: typed, or sent by a button under a job that didn't finish.
        It is the user's yes, so the job starts (the interface starts it)."""
        from ombro.session import again_plan

        words = rest.split()
        more = bool(words) and words[-1].lower() == "more"
        job_dir, why = self._unfinished_job(" ".join(words[:-1] if more else words))
        if job_dir is None:
            self.ui.say(why)
            return
        plan = again_plan(jobs.read_job(job_dir)[0])
        if plan is None:
            self.ui.say(
                f"Job {job_dir.name} finished, so there's nothing to {kind}. To run its brief again from "
                "the start, use /start."
            )
            return
        self.start_offers.append({"job": job_dir.name, "more": more and bool(plan.get("raise"))})
        self.transcript.note(f"You chose to {kind} job {job_dir.name}" + (" with more room" if more else "") + ".")
        self.pending_note += (
            f"[Ombro note: the user chose to {kind} job {job_dir.name}; Ombro starts it as a new "
            "job, on the same brief, building on what was done.]\n"
        )

    def _offer_again(self, given: str, note: str) -> tuple[dict[str, Any], tuple | None]:
        """The Lead offers to restart or resume a job that didn't finish (offer_start with `job`)."""
        from ombro.session import AGAIN_DONE, again_offer, again_plan

        job_dir, why = self._unfinished_job(given)
        if job_dir is None:
            return {"error": why}, None
        plan = again_plan(jobs.read_job(job_dir)[0])
        if plan is None:
            return {"error": f"job {job_dir.name} finished or is still running; offer a saved spec instead"}, None
        text, offers = again_offer(job_dir.name, plan)
        kind, name = plan["kind"], jobs.job_name(job_dir.name)
        self._lead_says(f"I found it: the job '{name}'. " + (f"{note} " if note else "") + text, level="lead")
        options = [
            Option("more", o.label, ("m", "more room"))
            if o.command.endswith(" more")
            else Option("yes", o.label, ("y", "ok", kind, f"{kind} it"))
            for o in offers
        ]
        answer = self.ui.ask(
            Field(
                "again",
                f"Shall I {kind} it?",
                "choice",
                default=options[0].key,
                mode="Chat",
                other="not now",
                pattern=NOT_NOW,
                options=options,
                aside=True,
            )
        )
        if answer in ("yes", "more"):
            self.start_offers.append({"job": job_dir.name, "more": answer == "more"})
            self.transcript.note(
                f"You said yes: job {job_dir.name} is {AGAIN_DONE[kind]}"
                + (" with more room." if answer == "more" else ".")
            )
            return {
                "status": "the user said yes; Ombro starts it again now in the background, building on what "
                "was done. Say in one short sentence that it is starting; do not describe the work "
                "again."
            }, None
        self.transcript.note(f"You said no: job {job_dir.name} was not started again.")
        return {
            "status": "the user said not now, so it was not started. Carry on the conversation; do not offer it "
            "again unless they ask."
        }, None

    def _start_offered(self, offer) -> None:
        """The command line starts a job the user said yes to in chat."""
        from ombro.cli import main

        if isinstance(offer, dict):  # carry on a job that didn't finish
            from ombro.session import StartError, start_again

            try:
                new_id = start_again(self.home, offer["job"], more=bool(offer.get("more")), chat=self.id)
            except StartError as e:
                self.ui.say(f"The job could not start again: {e}.")
                return
            self.note_job(new_id)
            self.ui.say(f"Started '{jobs.job_name(new_id)}' ({new_id}), building on what the last run did.")
            return

        spec, roles = offer if isinstance(offer, tuple) else (offer, [])
        main(["start", "--spec", spec, "--detach", *[a for r in roles for a in ("--role", r)]])

    def _offer_brief(self, args: dict) -> tuple[dict, tuple | None]:
        summary = _short(args.get("summary") or "", 300)
        revise = None
        if args.get("revise"):
            path = self._saved_spec(args.get("revise"))
            if path is None:
                return {"error": f"there is no saved spec called {args.get('revise')!r} to revise"}, None
            revise = path.stem
        raw = args.get("crews") or args.get("crew") or []
        crews = [
            str(c)
            for c in ([raw] if isinstance(raw, str) else raw if isinstance(raw, list) else [])
            if str(c) in self.home.crews()
        ]
        if revise and not crews:
            had = mdfile.read(self.home.specs_dir / f"{revise}.md")[0].get("crews") or []
            crews = [str(c) for c in ([had] if isinstance(had, str) else had) if str(c) in self.home.crews()]
        which = f" for {crew_phrases(self.home, crews)}" if crews else ""
        summary = summary.rstrip(".")
        if revise:
            self._lead_says(f"I can open your saved spec '{revise}' and change it: {summary}.", level="lead")
            question = "Shall we change it now? You'll see the changes before anything is saved."
        else:
            self._lead_says(f"This sounds like a task. I can brief it{which}: {summary}.", level="lead")
            question = "Shall we start the brief?"
        if self.ui.yes("start_brief", question, True, mode="Chat", aside=True):
            self.transcript.note(f"The Lead offered a brief ({summary}); you accepted.")
            return (
                {"status": "the user accepted; the task interview starts now, so do not reply"},
                (crews, summary, revise),
            )
        self.transcript.note(f"The Lead offered a brief ({summary}); you declined.")
        return {"status": "the user declined; carry on the conversation and do not offer again for this"}, None

    def _offer_role(self, args: dict) -> tuple[dict, tuple | None]:
        """A new crew member: the role interview, held here, starting from what was said in the chat."""
        crew = str(args.get("crew") or self.crew)
        if crew not in self.home.crews():
            return {"error": f"no crew named {crew!r}; crews: {', '.join(self.home.crews())}"}, None
        title = " ".join(str(args.get("title") or "").split()) or None
        summary = _short(args.get("summary") or "", 300).rstrip(".")
        who = f"a {title}" if title else "a new crew member"
        self._lead_says(f"I can set up {who} for {crew_phrase(self.home, crew)}: {summary}.", level="lead")
        if self.ui.yes(
            "start_role",
            "Shall we set it up now? You'll see it before anything is saved.",
            True,
            mode="Chat",
            aside=True,
        ):
            self.transcript.note(f"The Lead offered a new crew member ({summary}); you accepted.")
            return (
                {"status": "the user accepted; the role interview starts now, so do not reply"},
                ("role", crew, title, summary),
            )
        self.transcript.note(f"The Lead offered a new crew member ({summary}); you declined.")
        return {"status": "the user declined; carry on the conversation and do not offer again for this"}, None

    def _role(self, crew: str, title: str | None, summary: str | None) -> None:
        self.role_interview(crew, title, summary)

    def role_interview(self, crew: str, title: str | None = None, summary: str | None = None) -> None:
        """The role interview (a new crew member), held in this chat: from the Lead's offer, from
        /role add, or from Add crew member in the app. The chat is saved as it starts, so it is in
        the list of chats to come back to, and again once the interview has ended, however it ended."""
        from ombro.interviews import RolePurpose, role_added, start

        self.transcript.note(f"Role interview for {crew_phrase(self.home, crew)}.")
        if not self.title:
            self.set_title(f"New crew member: {title}" if title else "New crew member", "ombro")  # saves
        else:
            self._save()
        try:
            outcome = start(
                self.home,
                RolePurpose(self.home, crew, self.base_limits, title, task=self.brief_opening("", summary)),
                crew,
                self.base_limits,
                ui=self.ui,
                client_factory=self._client_factory,
                today=self.today_date,
                lookups=BriefLookups(self),
            )
        except (SetupError, ModelError) as e:
            self.ui.show(Failure(f"The role interview could not start: {e}"))
            return
        # Kept before anything more is shown: a window closed mid-interview stops what follows.
        self.transcript.entries += interview_entries(getattr(outcome, "transcript", None), "Role")
        self.pending_note += self.interview_back(outcome, "the role interview (adding a crew member)")
        self._save()
        role_added(self.home, self.ui, crew, outcome)

    def _write_as_work(self, args: dict) -> tuple[dict, tuple | None] | None:
        """folders.write that makes a document (a new file, or most of one rewritten, beyond a short note)
        is work for a crew: it becomes the report-back-or-live offer, never a direct write. None for a
        small edit, which goes ahead as before with the user's yes."""
        from ombro import native

        content = args.get("content")
        if not isinstance(content, str):
            return None
        try:
            ctx, _ = self._shared()
            self._chat_path(ctx, str(args.get("path") or "").strip())
            target = native.resolve_path(ctx, str(args.get("path") or "").strip(), write=True)
        except ToolError:
            return None  # refused as before, with the reason, by the folder tool
        old = None
        if target.is_file() and args.get("mode") != "append":
            try:
                old = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                old = None
        if not is_document_write(content, old, exists=target.exists()):
            return None
        shown = native._display(ctx, target.resolve())
        folder = _folder_of(ctx, target)
        asked = self.user_said[-1] if self.user_said else ""
        title = _short(target.stem.replace("-", " ").replace("_", " ").strip() or "New document", 70)
        task = (
            f"{asked.strip()}\n\n## Where it goes\n\nSave the result as {shown}"
            + (f" (shared/{folder}/{target.relative_to(ctx.shared[folder].path).as_posix()})" if folder else "")
            + ". Overwrite nothing else.\n\n## A draft from the chat\n\nThe Lead wrote this draft in the chat. "
            "Build on it, or start again where it falls short:\n\n" + _short(content, 20_000) + "\n"
        )
        self.transcript.note(f"The Lead's write to {shown} ({len(content):,} characters) was offered as work.")
        result, request = self._offer_work(
            {"summary": f"write {title} and save it as {shown}", "title": title, "task": task}, write_to=folder
        )
        result = {
            **result,
            "note": f"folders.write was not run: writing a document and saving it ({shown}) is "
            "work for the crew, so Ombro offered it as work instead. Do not call folders.write for it again. "
            "If the file already exists somewhere (a job's workspace, a shared folder), it was not needed: "
            "folders__copy puts it there as it is, and folders__edit corrects a line.",
        }
        return result, request

    def _offer_work(self, args: dict, write_to: str | None = None) -> tuple[dict, tuple | None]:
        """Report back, or do it live: the one question that says how Ombro works. `write_to` is
        the shared folder the work saves into, when known (from a write turned into this offer)."""
        summary = _short(args.get("summary") or "", 300).rstrip(".")
        title = _short(args.get("title") or summary or "Live job", 70)
        task, _ = keys.scrub(self.home, str(args.get("task") or "").strip())
        raw = args.get("crews") or args.get("crew") or []
        crews = [
            str(c)
            for c in ([raw] if isinstance(raw, str) else raw if isinstance(raw, list) else [])
            if str(c) in self.home.crews()
        ]
        folder_access = _offered_folders(args.get("folders")) or self._live_folders(task, write_to)
        which = f" for {crew_phrases(self.home, crews)}" if crews else ""
        self._lead_says(f"This sounds like work{which}: {summary}.", level="lead")
        # Reporting back is Ombro's main way of working; live is the other choice, never pushed.
        answer = self.ui.ask(
            Field(
                "how",
                "Shall I take this away and report back, shall we do it live, here, or shall we keep chatting for now?",
                "choice",
                default="report",
                mode="Chat",
                other="not now",
                pattern=NOT_NOW,
                aside=True,
                options=[
                    Option("report", "Report back"),
                    Option("live", "Do it live"),
                    Option("chat", "Let's keep chatting", ("keep chatting", "chat", "continue chatting")),
                ],
            )
        )
        if answer == "chat":
            self.transcript.note(f"The Lead offered work ({summary}); you chose to keep chatting.")
            return (
                {
                    "status": "the user chose to keep chatting rather than start any work yet. Carry on the "
                    "conversation about it, without starting or briefing anything; do not offer the "
                    "work again until they ask for it or the task clearly changes."
                },
                None,
            )
        if answer == "live" and task:
            self.transcript.note(f"The Lead offered work ({summary}); you chose to do it live.")
            return (
                {"status": "the user chose a live job; it starts now and its steps show in the chat, so do not reply"},
                ("live", crews, title, task, folder_access),
            )
        if answer in ("report", "live"):
            self.transcript.note(f"The Lead offered work ({summary}); you chose to have it reported back.")
            return (
                {"status": "the user chose a brief; the task interview starts now, so do not reply"},
                (crews, summary, None),
            )
        self.transcript.note(f"The Lead offered work ({summary}); you said not now.")
        return {"status": "the user said not now; carry on the conversation and do not offer again for this"}, None

    def _live_folders(self, task: str, write_to: str | None) -> dict[str, str]:
        """A live job whose folders the Lead didn't name: it reads every shared folder, and may write only
        to the one the task saves into (the intercepted write's, or the one the task names). Every folder
        is named in the spec, so the spec's list is the whole of what the job gets."""
        from ombro import folders

        target = write_to or folders.save_target(self.home, task)
        if not target:
            return {}
        names = folders.granted_names(self.home)
        if target not in names:
            return {}
        return {**{n: "read" for n in names}, target: "write"}

    def _live(self, crews: list[str], title: str, task: str, folder_access: dict[str, str] | None = None) -> None:
        """Do the work live: start the job, follow it here, then offer to keep it as a brief."""
        from ombro import livejob
        from ombro.session import StartError
        from ombro.ui import JobStarted, OpenPage

        crews = crews or [self.crew]
        self._lead_says(f"Here's what the crew will do: **{title}**\n\n{task.strip()}", level="lead", spaced=True)
        try:
            from ombro.init_cmd import connected_model

            if connected_model(self.home) is None:
                raise StartError("no model is connected; set Ombro up first")
            job_dir = livejob.start(
                self.home, crews, title, task, chat=self.id, inputs=self.attached, folders=folder_access
            )
        except (StartError, SetupError, mdfile.FrontMatterError, OSError) as e:
            self.ui.show(Failure(f"The live job could not start: {e}."))
            return
        job_id = job_dir.name
        self.note_job(job_id)
        name = jobs.job_name(job_id)
        self.ui.show(
            JobStarted(job_id, name, f"Live: '{name}' has started. Its steps show here as they happen; Esc stops it.")
        )
        status = livejob.follow(self.home, self.ui, job_dir)
        self.transcript.ombro(f"The live job '{name}' ended ({status}). Its report is jobs/{job_id}/report.md.")
        self.pending_note += (
            f"[Ombro note: the live job {job_id} ended ({status}); its report is "
            f"jobs/{job_id}/report.md and the user has it in the chat.]\n"
        )
        if status not in ("completed", "stopped_by_limit"):
            return
        what = self.ui.ask(
            Field(
                "after_live",
                "Want to run this again later? I can keep it as a brief.",
                "choice",
                default="no",
                mode="Chat",
                aside=True,
                options=[
                    Option("save", "Save as a brief"),
                    Option("schedule", "Schedule it"),
                    Option("no", "Not now", ("n",)),
                ],
            )
        )
        if what not in ("save", "schedule"):
            return
        brief = livejob.save_as_brief(self.home, job_dir)
        self.saved_briefs.append(brief)
        self.transcript.note(f"Saved the live job as brief {brief!r}.")
        if what == "schedule":
            self.ui.show(
                OpenPage(
                    "scheduled",
                    brief,
                    f"Saved as the brief '{brief}'. Choose when it runs on the "
                    f'Scheduled page (or: ombro schedule add {brief} --spec {brief} --cron "…").',
                )
            )
        else:
            self.ui.say(
                f"Saved as the brief '{brief}'. Start it again whenever you like, or schedule it.", level="done"
            )

    # -- schedules ---------------------------------------------------------

    def _schedules_text(self) -> str:
        """For the Lead: the zone schedules run in, and the schedules there are."""
        from ombro import cron as cronmod
        from ombro import schedules

        lines = [
            f"Schedules run in this machine's local time: {cronmod.local_zone()}. Give cron in that time. "
            "With the user, say times in plain words (every weekday at 9am), never as cron, and don't name "
            "the zone unless they ask or speak of another one."
        ]
        try:
            every = schedules.all_schedules(self.home)
        except (OSError, SetupError, mdfile.FrontMatterError):
            every = []
        for sch in every[:30]:
            state = "on" if sch.enabled else "off"
            lines.append(f"- {sch.name}: spec {sch.spec}, cron `{sch.cron_text}`, {state}")
        if not every:
            lines.append("No schedules yet.")
        return "\n".join(lines)

    def _offer_schedule(self, args: dict) -> dict[str, Any]:
        """The Lead offers to put a saved spec on a schedule: Ombro says when it would run, in the zone
        schedules use, and sets it up on a yes, as the Scheduled page does."""
        from datetime import datetime

        from ombro import cron as cronmod
        from ombro import scheduler, schedules

        path = self._saved_spec(args.get("spec"))
        if path is None:
            from ombro.cli import saved_specs

            names = [n for n, _ in saved_specs(self.home)][:15]
            return {
                "error": f"there is no saved spec called {args.get('spec')!r}. Saved specs, newest first: "
                + (", ".join(names) or "none")
                + ". Work that isn't saved yet is briefed first."
            }
        spec = path.stem
        name = str(args.get("name") or spec).strip().lower()
        try:
            cron = cronmod.parse(str(args.get("cron") or ""))
        except cronmod.CronError as e:
            return {"error": f"cron: {e}"}
        existing = self.home.schedules_dir / f"{name}.md"
        if existing.exists():
            try:
                old = schedules.load_schedule(self.home, existing)
                what = f" (spec {old.spec}, cron `{old.cron_text}`, {'on' if old.enabled else 'off'})"
            except (OSError, SetupError, mdfile.FrontMatterError):
                what = ""
            return {
                "error": f"there is already a schedule called {name}{what}. Give another `name`, or tell the "
                "user it exists; changing its times is done on the Scheduled page."
            }
        try:
            schedules.prepare(
                self.home,
                schedules.Schedule(
                    name=name, path=existing, cron_text=cron.text, cron=cron, spec=f"specs/{spec}.md", enabled=True
                ),
            )
        except (SetupError, mdfile.FrontMatterError) as e:
            return {"error": f"a scheduled job of this spec could not start: {e}"}
        # Ombro's own words for the times when it has them (so they can't differ from the cron); the
        # Lead's for anything more involved. No cron and no zone: schedules run in the machine's own time.
        when = (
            cronmod.in_words(cron)
            or " ".join(str(args.get("when") or "").split()).rstrip(".")
            or cronmod.describe(cron)
        )
        zone = cronmod.local_zone()
        nxt = cronmod.next_in_words(cron, datetime.now(), 3)
        self._lead_says(
            f"I can schedule '{spec}' to run {when}. The next runs would be {', '.join(nxt[:-1])} and {nxt[-1]}."
            if len(nxt) > 1
            else f"I can schedule '{spec}' to run {when}."
        )
        answer = self.ui.ask(Field("schedule", "Shall I set it up?", "yesno", default=True, mode="Chat", aside=True))
        if not answer:
            self.transcript.note(f"You said no: spec {spec!r} was not scheduled.")
            return {
                "status": "the user said no, so nothing was scheduled. Carry on the conversation; do not offer "
                "it again unless they ask."
            }
        try:
            schedules.add_schedule(self.home, name, spec, cron.text, True)
        except SetupError as e:
            return {"error": str(e)}
        self.transcript.note(f"Scheduled: {name} starts spec {spec!r} at cron `{cron.text}` ({zone}).")
        try:
            state = scheduler.schedule_turned_on(self.home)
        except OSError as e:
            state = {"ok": False, "message": f"Ombro couldn't check the scheduler service: {e}."}
        line = f"Scheduled: '{spec}' runs {when}. It's on the Scheduled page, where you can change or pause it."
        if not state.get("ok"):
            line += f" {state.get('message') or ''} Install now on the Scheduled page puts that right."
        self.ui.say(" ".join(line.split()), level="done")
        out: dict[str, Any] = {
            "status": "scheduled",
            "schedule": name,
            "spec": spec,
            "cron": cron.text,
            "zone": zone,
            "next": nxt,
        }
        if not state.get("ok"):
            out["scheduler"] = (
                f"{state.get('message')} Tell the user, in a sentence, that scheduled jobs won't "
                "run until it's put right with Install now on the Scheduled page."
            )
        else:
            out["note"] = (
                "Ombro has told the user it is set up; say nothing more than a short line, if anything, "
                "with no cron and no time zone."
            )
        return out

    # -- remember ----------------------------------------------------------

    def _remember(self, text: str, crew: str, *, offered: bool) -> dict[str, Any]:
        """Add a dated lesson to a crew's notes.md, only once the user confirms the wording."""
        text, _ = keys.scrub(self.home, text)
        if not text.strip():
            return {"error": "the text to remember is empty"}
        entry = memory.remember_entry(text, self.today_date)
        who = "The Lead offers to remember this" if offered else "To remember"
        while True:
            self.ui.say(f"{who} in {crew_phrase(self.home, crew)}'s notes (From you):\n  {entry}", spaced=True)
            answer = self.ui.ask(
                Field(
                    "remember",
                    "",
                    "choice",
                    mode="Chat",
                    options=[Option("y", "yes"), Option("e", "edit the wording", ("edit",)), Option("n", "no")],
                    other="@crew for another crew",
                    pattern=r"@\S.*",
                    aside=True,
                )
            )
            if answer == "y":
                break
            if answer == "e":
                new = self._input("The wording")
                if new.strip():
                    entry = memory.remember_entry(new, self.today_date)
                continue
            if answer.startswith("@"):
                name = answer[1:].strip()
                if name in self.home.crews():
                    crew = name
                else:
                    self.ui.say(f"No crew named {name!r}. Crews: {', '.join(self.home.crews())}.")
                continue
            self.transcript.note(f"Not remembered: {entry}")
            self.ui.say("Nothing was written.")
            return {"status": "the user declined; nothing was written"}

        cap = self.base_limits.memory_notes_max_chars
        notes = memory.read_notes(self.home, crew)
        while True:
            new = memory.with_your_entry(notes, entry)
            if new.size <= cap:
                memory.save_notes(self.home, crew, new)
                self.remembered.append({"crew": crew, "entry": entry})
                self.transcript.note(f"Remembered in crew {crew}'s notes.md: {entry}")
                self.ui.say(f"Remembered in {memory.notes_path(self.home, crew)}.", level="done")
                return {"status": "saved", "crew": crew, "entry": entry}
            yours = notes.your_entries()
            self.ui.say(
                f"That would make {crew_phrase(self.home, crew)}'s notes.md {new.size:,} characters, "
                f"over the cap of {cap:,}. "
                f"Your part is {len(notes.from_you):,} characters and the Lead's is {len(notes.from_jobs):,}."
            )
            if yours:
                self.ui.say("Your entries:")
                for i, ln in enumerate(yours, 1):
                    self.ui.say(f"  {i}. {_short(ln, 150)}")
            answer = str(
                self.ui.ask(
                    Field(
                        "over_cap",
                        "",
                        "choice",
                        mode="Chat",
                        options=[
                            Option("s", "shorten the wording", ("shorten",)),
                            Option("e", "edit notes.md by hand", ("edit",)),
                            Option("c", "cancel"),
                        ],
                        other="the numbers of your entries to remove (e.g. 2 5)",
                        pattern=r"[\d,\s]+",
                        aside=True,
                    )
                )
            ).lower()
            numbers = [int(x) for x in answer.replace(",", " ").split() if x.isdigit()]
            if numbers and len(numbers) == len(answer.replace(",", " ").split()):
                notes = memory.without_your_entries(notes, numbers)
                continue
            if answer == "s":
                new_text = self._input("The shorter wording")
                if new_text.strip():
                    entry = memory.remember_entry(new_text, self.today_date)
                continue
            if answer == "e":
                self.ui.edit([memory.notes_path(self.home, crew)])
                notes = memory.read_notes(self.home, crew)
                continue
            self.transcript.note(f"Not remembered (over the cap): {entry}")
            self.ui.say("Nothing was written.")
            return {"status": "cancelled: notes.md would have passed its cap; nothing was written"}

    # -- skills ------------------------------------------------------------

    def _offer_skill(self, args: dict) -> dict[str, Any]:
        """Write a skill into the library, only once the user has seen it and said yes."""
        name = str(args.get("name") or "").strip()
        source = f"chat {self.path.name}" if getattr(self, "path", None) else "chat"
        text = skills.compose(name, str(args.get("description") or ""), str(args.get("body") or ""))
        text, _ = keys.scrub(self.home, text)
        try:
            skills.check(text, name, self.base_limits.skill_max_chars)
        except skills.SkillError as e:
            return {"error": str(e)}
        raw = args.get("for") or []
        given: list[str] = []
        for who in raw if isinstance(raw, list) else [raw]:
            who = str(who).strip()
            try:
                if not skills._config_path(self.home, who).is_file():
                    raise skills.SkillError("")
            except skills.SkillError:
                return {"error": f"no role {who!r}; name roles as crew/role"}
            if who not in given:
                given.append(who)
        while True:
            live = skills.load(self.home, name)
            signs = material.flags(text)
            what = f"a new version of the skill {name}" if live else f"a new skill, {name}"
            self.ui.say(f"I can write {what}" + (f", for {', '.join(given)}" if given else "") + ".", spaced=True)
            if live:
                self.ui.say(skills.diff(live.text, text, name) or "(no change from today's version)")
            else:
                self.ui.say(text.rstrip())
            if signs:
                line = (
                    f"Ombro: this skill contains text that looks written to steer a model ({', '.join(signs)}). "
                    "Read it closely before you say yes."
                )
                self.ui.say(line)
                self.transcript.note(line)
            answer = self.ui.ask(
                Field(
                    "skill",
                    "Write it?",
                    "choice",
                    mode="Chat",
                    aside=True,
                    options=[Option("y", "yes"), Option("e", "edit it first", ("edit",)), Option("n", "no")],
                )
            )
            if answer == "y":
                break
            if answer == "e":
                draft = self.home.chats_dir / f".skill-{name}.md"
                draft.parent.mkdir(parents=True, exist_ok=True)
                draft.write_text(text, encoding="utf-8")
                self.ui.edit([draft])
                new = draft.read_text(encoding="utf-8")
                draft.unlink(missing_ok=True)
                try:
                    skills.check(new, name, None)
                    text = new
                except skills.SkillError as e:
                    self.ui.say(f"That edit was not kept: {e}.")
                continue
            self.transcript.note(f"Skill not written: {name}")
            self.ui.say("Nothing was written.")
            return {"status": "the user declined; nothing was written"}
        kept = skills.write(self.home, name, text, source=source, accepted=self.today_date.isoformat())
        notes = [skills.give(self.home, name, who) for who in given]
        where = self.home.skills_dir / name / "SKILL.md"
        self.transcript.note(
            f"Skill written: {where}"
            + (f"; old version kept in {kept}" if kept else "")
            + (f"; {'; '.join(notes)}" if notes else "")
        )
        self.ui.say(
            f"Written to {where}."
            + (f" The old version is kept in {kept}." if kept else "")
            + (f" {'; '.join(notes).capitalize()}." if notes else ""),
            level="done",
        )
        return {"status": "written", "skill": name, **({"given": notes} if notes else {})}

    # -- brief -------------------------------------------------------------

    def brief_opening(self, text: str, summary: str | None) -> str:
        """The task interview's starting point: the user's own chat messages
        (most recent first, within a cap, shown oldest first) and, if the user
        accepted the Lead's offer, its one-line summary."""
        parts = []
        if text.strip():
            parts.append(text.strip())
        picked: list[str] = []
        used = 0
        for msg in reversed(self.user_said):
            if used + len(msg) > BRIEF_CONTEXT_CHARS:
                break
            picked.insert(0, msg)
            used += len(msg)
        if picked:
            parts.append(
                "What the user said in the chat before this brief, oldest first:\n"
                + "\n".join("- " + m.replace("\n", "\n  ") for m in picked)
            )
        found = self.chat_findings()
        if found:
            parts.append(
                "What you (the Lead) said and read in the chat before this brief, oldest first. You "
                "already know this: use it, and don't ask the user for what is here (a file's name, "
                "what a report said):\n\n" + found
            )
        if self.attached:
            parts.append(
                "Files the user attached in the chat. The job is given them, in workspace/inputs/, so "
                "the brief can name them there and needn't ask for them again:\n"
                + "\n".join(f"- workspace/inputs/{Path(a).name}" for a in self.attached)
            )
        if summary:
            parts.append(f"The Lead's one-line summary in the chat, which the user accepted: {summary}")
        return "\n\n".join(parts)

    def chat_findings(self) -> str:
        """The Lead's replies and the results of what it read in this chat, newest kept first within
        BRIEF_FOUND_CHARS, each result cut to BRIEF_FOUND_EACH, returned oldest first."""
        items: list[str] = []
        calls: dict[str, str] = {}
        for exchange in self.exchanges:
            for m in exchange:
                if m.get("role") == "assistant":
                    for c in m.get("tool_calls") or []:
                        fn = c.get("function") or {}
                        calls[c.get("id", "")] = f"{fn.get('name', '?')} {fn.get('arguments', '')}".strip()
                    if (m.get("content") or "").strip():
                        items.append("You said: " + m["content"].strip())
                elif m.get("role") == "tool":
                    text = str(m.get("content") or "").strip()
                    if len(text) > BRIEF_FOUND_EACH:
                        text = text[:BRIEF_FOUND_EACH] + " […cut]"
                    items.append(f"You used {calls.get(m.get('tool_call_id', ''), 'a tool')}, which returned:\n{text}")
        kept: list[str] = []
        used = 0
        for item in reversed(items):
            if used + len(item) > BRIEF_FOUND_CHARS:
                break
            kept.insert(0, item)
            used += len(item)
        return "\n\n".join(kept)

    def _brief(self, crews: list[str], opening: str, revise: str | None = None) -> None:
        from ombro.interviews import BriefPurpose, start

        self.mode = "Brief"
        which = f" (starting from crew{'s' if len(crews) > 1 else ''} {', '.join(crews)})" if crews else ""
        self.ui.show(ModeChanged("Brief"))  # one conversation: the brief carries on in the chat, unannounced
        self.transcript.note(f"Switched to Brief mode{which}.")
        try:
            outcome = start(
                self.home,
                BriefPurpose(self.home, self.crew, self.base_limits, opening, crews=crews, revise=revise),
                self.crew,
                self.base_limits,
                ui=BriefModeUI(self.ui),
                client_factory=self._client_factory,
                today=self.today_date,
                lookups=BriefLookups(self),
            )
        except (SetupError, ModelError) as e:
            self.ui.show(Failure(f"The brief could not start: {e}"))
            outcome = None
        self.mode = "Chat"
        if outcome is not None:  # the interview is part of this chat: kept in it, in order
            self.transcript.entries += interview_entries(getattr(outcome, "transcript", None), "Brief")
        if outcome is not None and outcome.confirmed:
            name = outcome.proposal.data["name"]
            self.specs.append(name)
            spec_path = self.home.specs_dir / f"{name}.md"
            if self.attached and spec_path.is_file():  # the brief is given the files attached in this chat
                meta, body = mdfile.read(spec_path)
                meta["inputs"] = list(dict.fromkeys([*(meta.get("inputs") or []), *self.attached]))
                mdfile.write(spec_path, meta, body)
            if spec_path.is_file():  # the chat takes the spec's title, unless you named it
                self.set_title(jobs.first_line(mdfile.read(spec_path)[1], 70) or name, "spec")
            self.ui.say(f"Spec saved as {name!r}. Start it with:  ombro start --spec {name}", spaced=True, level="done")
            self.transcript.note(f"Brief mode ended: spec {name!r} saved (transcript {outcome.transcript}).")
            self.pending_note += (
                f"[Ombro note: in Brief mode the user confirmed a spec, saved as {name!r}. It has not been started.]\n"
            )
            self.pending_note += self.interview_back(outcome, "the brief")
        else:
            detail = outcome.status if outcome is not None else "could not start"
            self.transcript.note(f"Brief mode ended without a spec ({detail}).")
            self.pending_note += "[Ombro note: the user left Brief mode; no spec was saved.]\n"
            if outcome is not None:
                self.pending_note += self.interview_back(outcome, "the brief")
        # One conversation: the chat carries on, with no line to say so.
        self.ui.show(ModeChanged("Chat"))

    # -- transcript --------------------------------------------------------

    def interview_back(self, outcome, what: str) -> str:
        """An interview just held (a brief, a role), for the chat: it was you (the Lead) asking, so you
        know what was said, however it ended."""
        path = getattr(outcome, "transcript", None)
        try:
            text = mdfile.read(Path(path))[1].strip() if path else ""
        except (OSError, mdfile.FrontMatterError):
            text = ""
        if not text:
            return ""
        if len(text) > BRIEF_BACK_CHARS:
            text = "[…the start is cut]\n" + text[-BRIEF_BACK_CHARS:]
        ended = {"confirmed": "it was accepted and saved", "quit": "the user stopped it; nothing was written"}.get(
            getattr(outcome, "status", ""), f"it ended ({getattr(outcome, 'status', 'unknown')}); nothing was written"
        )
        return (
            f"[Ombro note: {what} you just held with the user, in full ({ended}). You asked these questions "
            "and heard these answers; carry on from here as one conversation. To hold it again, call "
            "offer_role or offer_brief, and the user won't need to repeat themselves.]\n"
            + text
            + f"\n[End of {what}.]\n"
        )

    def _transcript_path(self) -> Path:
        """chats/new-chat-<date-time>.md until the chat has a title; then the title's words."""
        folder = self.home.chats_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"new-chat-{self.stamp}.md"
        n = 2
        while path.exists():
            path = folder / f"new-chat-{self.stamp}-{n}.md"
            n += 1
        return path

    def _pick_chat(self, want: str) -> None:
        """/chats [part of a title]: past chats, newest first, to reopen one."""
        from ombro import views

        past = [c for c in views.chat_list(self.home) if Path(c["path"]) != self.path]
        if want:
            past = [c for c in past if want.lower() in c["title"].lower() or want.lower() in c["name"]]
        if not past:
            self.ui.say("There are no other chats" + (f" matching '{want}'." if want else " yet."))
            return
        shown = past[:20]
        pick = self.ui.ask(
            Field(
                "chat",
                "Which chat would you like to carry on? (newest first)",
                "choice",
                default="1",
                invalid="Please choose one of the numbers shown.",
                mode="Chat",
                aside=True,
                options=[Option(str(i), f"{c['title']}  ({c['when']})") for i, c in enumerate(shown, 1)],
            )
        )
        self.reopen(Path(shown[int(pick) - 1]["path"]))
        self.ui.say(f"Here's '{self.title or 'that chat'}' again. Carry on where you left off.", level="lead")

    def _save(self) -> None:
        if not self.transcript.entries and not self.path.exists():
            return  # nothing was said: no file, so the list of chats has no empty ones
        s = self.gate.snapshot()
        said = len(list(turns(self.transcript.entries)))
        unchanged = self.last_used and said == self.turns_seen
        updated = self.last_used if unchanged else utc_now()
        meta = {
            "kind": "chat",
            "title": self.title or "",
            "titled_by": self.titled_by or "",
            "default_crew": self.crew,
            "started_at": self.transcript.started,
            "updated_at": updated,
            "ended": self.ended,
            "remembered": self.remembered,
            "specs_saved": self.specs,
            "id": self.id,
            "jobs": self.jobs,
            **({"attached": self.attached} if self.attached else {}),
            **({"tainted": True} if self.tainted else {}),
            "refused_tools": sorted(set(self.refused)),
            "limits": {"max_tokens": self.limits.max_tokens, "tool_max_calls": self.limits.tool_max_calls},
            "usage": _add_usage(
                self.prior_usage,
                {k: s.get(k) for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls", "tool_calls")},
            ),
        }
        body = (
            f"# {self.title or 'New chat'}\n\nReopen this chat with /chats, or `ombro chat --resume`.\n\n"
            + "\n\n".join(self.transcript.entries)
            + "\n"
        )
        text, _ = keys.scrub(self.home, mdfile.dump(meta, body))
        mdfile.write_atomic(self.path, text)
        self.last_used, self.turns_seen = updated, said
        if self.path != self._announced:  # first written, or renamed with its title
            self._announced = self.path
            try:
                self.on_saved(self.path)
            except Exception:  # noqa: BLE001 - telling a window must never stop a save
                pass
