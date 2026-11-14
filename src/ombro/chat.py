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
import re
import secrets
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ombro import keys, material, mdfile, memory, prompts, jobs, skills
from ombro.client import ChatClient, ModelError, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home, crew_title, find_crew
from ombro.ui import UI, ChatCleared, PastTurn, ConsoleUI, Failure, Field, LeadSaid, ModeChanged, Note, Option, Usage
from ombro.journal import utc_now
from ombro.roles import load_lead
from ombro.settings import ModelSpec, SetupError, format_tokens, load_limits, load_models, load_preferences, load_tool_settings
from ombro.tools import ToolContext, ToolError, ToolRegistry

QUIT_WORDS = ("/quit", "/q", "/exit", "/end")
TOOL_RESULT_CAP = 12_000
BRIEF_CONTEXT_CHARS = 4_000

MEMORY_TOOLS = {"memory__search": "memory.search", "memory__read": "memory.read", "memory__list": "memory.list"}
WEB_TOOLS = {"web__search": "web.search", "web__fetch": "web.fetch"}
FOLDER_TOOLS = {"folders__list": "folders.list", "folders__read": "folders.read", "folders__write": "folders.write",
                "folders__set_aside": "folders.set_aside"}
SKILL_TOOLS = {"skills__read": "skills.read"}
OFFERS = ("offer_brief", "offer_remember", "offer_start", "offer_skill")

HELP = """\
Chat mode is a conversation with the Lead. It can look at every crew's reports,
notes, specs and deliverables, search the web, and work in your shared folders.
It changes a file only after you say yes.

  /brief [@crew] [text]     brief a task (Brief mode); the chat so far is its starting point
  /chat                     back to Chat mode from a brief (nothing is written)
  /new                      save this chat and start a fresh one; nothing carries over
  /chats                    your past chats, newest first; reopen one and carry on
  /rename title             give this chat a title of your own
  /remember [@crew] text    add a dated lesson to a crew's notes.md, after you confirm the wording
  /skills                   list the skills library
  /help                     this list
  /quit                     end the chat (it is saved in chats/)

For several lines, start and end with \"\"\"."""


def rest_named(text: str) -> bool:
    """Whether a /brief or /remember line names a crew with @crew."""
    return text.partition(" ")[2].strip().startswith("@")


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tool_msg(call_id: str, payload: Any, src: str | None = None) -> dict:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(content) > TOOL_RESULT_CAP:
        content = content[:TOOL_RESULT_CAP] + "…[truncated]"
    if src:
        content = material.fence(content, src)  # from outside: assessed, never obeyed (7.1)
    return {"role": "tool", "tool_call_id": call_id, "content": content}


ENTRY = re.compile(r"^\*\*(You \((Chat|Brief)\)|Lead):\*\* ?")


def parse_entries(body: str) -> list[str]:
    """A saved chat's entries: each turn, yours or the Lead's, and Ombro's notes. A turn
    may run over several paragraphs; it ends where the next entry begins."""
    entries: list[str] = []
    for chunk in body.split("\n\n"):
        if ENTRY.match(chunk) or (chunk.startswith("_") and chunk.rstrip().endswith("_") and "\n" not in chunk):
            entries.append(chunk)
        elif entries:
            entries[-1] += "\n\n" + chunk
    return [e.rstrip() for e in entries]


def turns(entries: list[str]):
    """(user or lead, text) for each turn in Chat mode; Brief questions and notes are left out."""
    for e in entries:
        m = ENTRY.match(e)
        if not m:
            continue
        text = e[m.end():].strip()
        if m.group(1).startswith("You"):
            if m.group(2) == "Chat":
                yield "user", text
        else:
            yield "lead", text


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
        self.gate = Gate(self.limits, clock)
        self.today_date = today or date.today()
        self.today = prompts.format_date(self.today_date)
        if registry is None:
            from ombro.native import native_registry

            registry = native_registry(home, load_tool_settings(home))
        self.registry = registry
        self.mode = "Chat"
        self.exchanges: list[list[dict]] = []  # each starts with a user message
        self.user_said: list[str] = []
        self.dropped = 0
        self.pending_note = ""
        self.transcript = Transcript()
        self.refused: list[str] = []
        self.remembered: list[dict[str, str]] = []
        self.specs: list[str] = []
        self.start_offers: list[str] = []  # saved specs the Lead offered to start; the interface asks the user
        self._fresh()

    def _fresh(self) -> None:
        """Everything one chat keeps: none of it carries into the next."""
        self.gate = Gate(self.limits, self.clock)
        self.mode = "Chat"
        self.exchanges = []
        self.user_said = []
        self.dropped = 0
        self.pending_note = ""
        self.transcript = Transcript()
        self.refused = []
        self.remembered = []
        self.specs = []
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
        messages = [{"role": "user", "content": "Give this conversation a title of two to five words, in title "
                     "case, saying what it is about. Reply with the title only, no quotes.\n\n" + text}]
        try:
            spec = self.models.get("cheap") or self.spec
            est = estimate_tokens(messages, None)
            max_out, timeout = self.gate.check_model_call(spec.name, est, 30)
            reply = self._client_factory(spec).complete(messages, tools=None, max_output_tokens=max_out,
                                                         timeout=min(timeout, 30))
            u = reply.usage
            self.gate.record_model_call(spec.name, u.input_tokens or est, min(u.output_tokens, max_out),
                                        estimated=u.estimated)
        except (ModelError, LimitReached, OSError):
            return
        self.set_title(reply.content or "", "model")

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
        for kind, text in turns(entries):
            if kind == "user":
                self.exchanges.append([{"role": "user", "content": text}])
                self.user_said.append(text)
            elif self.exchanges:
                self.exchanges[-1].append({"role": "assistant", "content": text})
        self.transcript.note(f"Reopened {utc_now()}.")
        self.pending_note = ("[Ombro note: the user reopened this chat to carry on. Tool results from before are "
                             "not kept; look things up again if you need them.]\n")
        self.ui.show(ChatCleared())
        for who, text in turns(entries):
            self.ui.show(PastTurn("you" if who == "user" else "lead", text))
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
            self.ui.say("(Something that looked like a key was removed before it went any further. "
                     "Keys are set with `ombro keys set NAME`.)")
        return text

    def _model_call(self, messages: list[dict]):
        tools = prompts.CHAT_TOOLS + [t.schema() for n in WEB_TOOLS.values() if (t := self.registry.get(n))]
        if not skills.library(self.home):  # nothing to read: an empty library costs no tool
            tools = [t for t in tools if t["function"]["name"] != "skills__read"]
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
        return reply

    def _crews_text(self) -> str:
        lines = []
        for c in self.home.crews():
            meta = {}
            if (self.home.crew_dir(c) / "crew.md").is_file():
                meta = mdfile.read(self.home.crew_dir(c) / "crew.md")[0]
            rdir = self.home.roles_dir(c)
            roles = sorted(p.name for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")) \
                if rdir.is_dir() else []
            all_jobs = jobs.all_jobs(self.home, c)
            latest = jobs.job_time(all_jobs[-1])[:8] if all_jobs else ""
            when = f", latest {latest[:4]}-{latest[4:6]}-{latest[6:8]}" if latest else ""
            mark = ", the default crew" if c == self.crew else ""
            lines.append(f"- the {crew_title(self.home, c)} (crew name `{c}` for tools{mark}): "
                         f"{meta.get('description', '')} Roles: {', '.join(roles) or 'none'}. "
                         f"Jobs: {len(all_jobs)}{when}.")
        lines.append("Speak of each crew by its title, never by its crew name.")
        lines.append("\nFiles are under the home directory: jobs/<job_id>/report.md, spec.md and workspace/ "
                     "(a job may have used several crews); specs/<name>.md; crews/<crew>/memory/notes.md and "
                     "index.md.")
        return "\n".join(lines)

    def _folders_text(self) -> str:
        from ombro.folders import display_path

        ctx, left = self._shared()
        lines = [f"- shared/{n} ({'read and write' if f.writable else 'read only'}): {display_path(f.path)}"
                 for n, f in ctx.shared.items()]
        return "\n".join(lines + [f"- {x}" for x in left])

    def _limits_line(self) -> str:
        caps = ", ".join(f"{k} {v}" for k, v in self.limits.tool_max_calls.items() if k != "default")
        return (f"- tokens for this chat: {format_tokens(self.limits.max_tokens)}\n"
                f"- tool calls for this chat: {caps}\n"
                f"- model calls for one message: {self.limits.chat_calls_per_message}\n"
                f"- older turns stop being sent once the conversation passes "
                f"{self.limits.chat_history_chars:,} characters")

    def _messages(self) -> list[dict]:
        system = prompts.chat_system(self.lead.soul, self.instructions, self._crews_text(), self._limits_line(),
                                     self.today, self._folders_text(),
                                     skills.listing(list(skills.library(self.home).values()),
                                                    self.limits.skills_list_chars)[0])
        # Past a set length, the oldest turns are dropped from what is sent.
        while len(self.exchanges) > 1 and len(json.dumps(self.exchanges, ensure_ascii=False)) > \
                self.limits.chat_history_chars:
            self.exchanges.pop(0)
            self.dropped += 1
            if self.dropped == 1:
                self.ui.say("(This chat is long, so from here on only its more recent turns are kept in view. "
                         "The transcript keeps everything.)")
                self.transcript.note("From here on, only the more recent turns were kept in view.")
        return [{"role": "system", "content": system}] + [m for ex in self.exchanges for m in ex]

    def _usage_line(self) -> None:
        s = self.gate.snapshot()
        tools = sum((s.get("tool_calls") or {}).values())
        self.ui.show(Usage("chat", self.mode, s["total_tokens"], self.limits.max_tokens, tools))

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
            f"Chat with Ombro Lead (default crew: the {crew_title(self.home, self.crew)}). Mode: Chat. Nothing "
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
        if not self._command(text):
            if text.lower() in QUIT_WORDS:
                return False
            self._save()  # a remember or a brief is on record at once
            return True
        try:
            request = self._turn(text)
        except LimitReached as e:
            self.ended = f"stopped by the chat limit {e.limit}: {e.detail}"
            self.transcript.note(f"The chat stopped: {e.detail}")
            self.ui.show(Failure(f"The chat has reached its limit: {e.detail}. It ends here."))
            return False
        except ModelError as e:
            self.exchanges.pop()
            self.transcript.note(f"The model call failed: {e}")
            self.ui.show(Failure(f"I couldn't reach the model just now ({e}). Try again in a moment."))
            return True
        self._usage_line()
        if self.titles and not self.title and self.exchanges and self.mode == "Chat":
            self._auto_title()
        if request:
            revise = request[2] if len(request) > 2 else None
            self._brief(request[0], self.brief_opening("", request[1]), revise=revise)
        self._save()
        return True

    def close(self) -> None:
        self._save()
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
            self.ui.say("You are in Chat mode.")
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
        elif word == "/skills":
            lib = skills.library(self.home)
            self.ui.say("\n".join(f"- {s.name}: {s.description}" for s in lib.values())
                        if lib else "No skills yet. Ask me to remember how to do something, and I can write one.")
        elif word == "/remember":
            crew, rest = self._crew_arg(rest)
            if crew:
                if not rest.strip():
                    rest = self._input("What should be remembered?")
                if rest.strip():
                    result = self._remember(rest, crew, offered=False)
                    if result.get("status") == "saved":
                        self.pending_note += (f"[Ombro note: the user added this to crew {crew}'s notes.md: "
                                              f"{result['entry']}]\n")
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
            self.ui.say(f"There's no crew called {given}. The crews are "
                        f"{', '.join(crew_title(self.home, c) for c in self.home.crews())}.")
            return None, remainder
        return name, remainder.strip()

    def _turn(self, text: str) -> tuple[list[str], str] | None:
        """One message to the Lead and its answer. Returns (crews, summary) if the
        user accepted an offer to brief."""
        self.gate._seen.clear()  # loop detection counts repeats within one message
        self.exchanges.append([{"role": "user", "content": self.pending_note + text}])
        self.pending_note = ""
        self.user_said.append(text)
        self.transcript.user(text, self.mode)
        for _ in range(self.limits.chat_calls_per_message):
            reply = self._model_call(self._messages())
            self.exchanges[-1].append(reply.as_message())
            said = (reply.content or "").strip()
            offering = any(tc.name in ("offer_start", "offer_brief") for tc in reply.tool_calls or [])
            if said and offering:
                # The offer says it in the Lead's voice; the same words twice read as a stutter.
                self.transcript.note(f"The Lead's words with its offer, not shown: {_short(said, 300)}")
            elif said:
                self.ui.show(LeadSaid(said))
                self.transcript.lead(said)
            if not reply.tool_calls:
                return None
            request = None
            for tc in reply.tool_calls:
                if request:
                    result = {"error": "ignored: the brief is starting"}
                else:
                    result, request = self._tool(tc)
                src = None
                if tc.name not in OFFERS and tc.name not in ("folders__write", "folders__set_aside",
                                                             "skills__read") and not (
                        isinstance(result, dict) and ("error" in result or "declined" in result)):
                    src = material.source(MEMORY_TOOLS.get(tc.name) or WEB_TOOLS.get(tc.name)
                                          or FOLDER_TOOLS.get(tc.name) or tc.name, tc.arguments)
                    signs = material.flags(result)
                    if signs:
                        # Ombro's own words, not the Lead's: what to check, never a block (7.1).
                        line = (f"Ombro: {src} contains text that looks written to steer the Lead "
                                f"({', '.join(signs)}). It is treated as material, and nothing was done because "
                                "of it.")
                        self.ui.say(line)
                        self.transcript.note(line)
                self.exchanges[-1].append(_tool_msg(tc.id, result, src))
            if request:
                return request
        self.ui.say(f"(The Lead used all {self.limits.chat_calls_per_message} model calls allowed for one message "
                 "without answering. Ask again more narrowly.)")
        self.transcript.note("The Lead used all its model calls for this message without answering.")
        return None

    # -- tools -------------------------------------------------------------

    def _tool(self, tc) -> tuple[dict, tuple[list[str], str] | None]:
        name = tc.name
        internal = MEMORY_TOOLS.get(name) or WEB_TOOLS.get(name) or FOLDER_TOOLS.get(name) or SKILL_TOOLS.get(name)
        if name not in OFFERS and (internal is None or (name in WEB_TOOLS and self.registry.get(internal) is None)):
            self.refused.append(name)
            self.transcript.note(f"Refused {name}: not available in chat.")
            return {"error": f"{name} is not available in chat. Chat cannot brief roles, start jobs, approve the "
                             "waiting actions or use outward tools; files are written only in shared folders, with "
                             "folders__write. To turn this into a task, call offer_brief."}, None
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
        if name == "offer_start":
            return self._offer_start(args)
        if name == "offer_remember":
            crew = str(args.get("crew") or self.crew)
            if crew not in self.home.crews():
                return {"error": f"no crew named {crew!r}; crews: {', '.join(self.home.crews())}"}, None
            return self._remember(str(args.get("text") or ""), crew, offered=True), None
        if name == "offer_skill":
            return self._offer_skill(args), None
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
                return memory.read_file(self.home, args.get("path"), args.get("offset") or 0,
                                        args.get("max_chars") or memory.READ_DEFAULT_CHARS), None
            if internal == "skills.read":
                return skills.read(self.home, None, args.get("name"), args.get("file"),
                                   self.base_limits.skill_read_chars), None
            if internal in FOLDER_TOOLS.values():
                return self._folder_tool(internal, args), None
            if internal == "memory.list":
                return memory.listing(self.home, str(args.get("crew") or "") or None,
                                      str(args.get("job_id") or "") or None), None
            tool = self.registry.get(internal)
            ctx = ToolContext(self.home.root / "chats", "lead", timeout=60.0)
            return tool.handler(args, ctx), None
        except (memory.ReadRefused, skills.SkillError) as e:
            return {"error": str(e)}, None
        except ToolError as e:
            return {"error": str(e)}, None
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}, None

    # -- shared folders ------------------------------------------------------

    def _shared(self) -> tuple[ToolContext, list[str]]:
        from ombro.folders import for_chat
        from ombro.tools import SharedFolder

        given, left = for_chat(self.home)
        shared = {f["name"]: SharedFolder(f["name"], Path(f["path"]), f["access"]) for f in given}
        # No job folder: chat reaches only shared/<name>/..., never workspace/ or scratch/.
        return ToolContext(self.home.root / "chats" / ".none", "lead", timeout=60.0, shared=shared), left

    def _folder_tool(self, internal: str, args: dict) -> dict[str, Any]:
        from ombro import native

        ctx, left = self._shared()
        if not ctx.shared:
            return {"error": "No folders are shared yet. The user shares one with /folder add <path>."
                             + (" " + " ".join(left) if left else "")}
        path = str(args.get("path") or "").strip()
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
            return native._read({"path": path, "offset": args.get("offset") or 0,
                                 "max_chars": args.get("max_chars") or memory.READ_DEFAULT_CHARS}, ctx)
        if internal == "folders.set_aside":
            target = native.resolve_path(ctx, path, write=True)
            shelf = native.SET_ASIDE.get(str(args.get("as") or "for-deletion"), "For deletion")
            why = " ".join(str(args.get("why") or "").split())
            self.ui.say(f"I'd like to move {native._display(ctx, target.resolve())} into {shelf}/: {why}",
                        level="lead")
            if not self.ui.yes("folder_set_aside", "Go ahead?", False):
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
        self.ui.say(f"I'd like to {what} {shown}{kept} ({lines:,} lines, {len(content):,} characters).",
                    level="lead", items=[_short(content, 400)] if content else [])
        if not self.ui.yes("folder_write", "Go ahead?", False):
            self.transcript.note(f"The user said no to writing {shown}.")
            return {"declined": f"The user said no, so {shown} was not changed. Ask what they would like instead."}
        out = native._write({"path": path, "content": content, "mode": mode}, ctx)
        self.transcript.note(f"Wrote {shown} ({mode}), with the user's yes.")
        self.ui.say(f"Saved {shown}.", level="done")
        return out

    @staticmethod
    def _chat_path(ctx: ToolContext, path: str) -> None:
        if not path:
            raise ToolError("give a path such as shared/<name>/notes.md")
        if not path.startswith(("shared/", "/", "~")):
            raise ToolError("in chat, files outside Ombro's home are reached as shared/<name>/... "
                            "(folders: " + ", ".join(f"shared/{n} ({f.access})" for n, f in ctx.shared.items())
                            + ")")

    def _saved_spec(self, name: Any) -> Path | None:
        name = str(name or "").strip().removesuffix(".md").removeprefix("specs/")
        path = self.home.specs_dir / f"{name}.md"
        return path if name and "/" not in name and path.is_file() else None

    def _offer_start(self, args: dict) -> tuple[dict[str, Any], tuple | None]:
        """The Lead offers to start a saved spec again, in its own voice: yes starts it (the
        interface does), "change it" opens the spec for changing, and no leaves it. Roles
        given here apply to this job only; the saved spec is unchanged (10.4)."""
        path = self._saved_spec(args.get("spec"))
        if path is None:
            from ombro.cli import saved_specs

            names = [n for n, _ in saved_specs(self.home)][:15]
            return {"error": f"there is no saved spec called {args.get('spec')!r}. Saved specs, newest first: "
                             + (", ".join(names) or "none")}, None
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
        self.ui.say(f"I found it. It's the spec '{name}'. " + (f"{note} " if note else "")
                    + f"I can restart it {how}.", level="lead")
        self.transcript.note(f"The Lead offered to start saved spec {name!r} again ({how}).")
        answer = self.ui.ask(Field(
            "start_now", 'If you prefer to read through the spec again and change it, just say "change it". '
                       "Otherwise, should I just start it?", "yesno", default=True, other="change it", mode="Chat"))
        if answer == "change it":
            self.transcript.note(f"You chose to change spec {name!r} first.")
            return ({"status": "the user wants to change the spec first; the interview starts now, so do not "
                               "reply"}, (crews, how, name))
        if answer:
            self.start_offers.append((name, roles))
            self.transcript.note(f"You said yes: spec {name!r} starts" + (f" with {', '.join(roles)}" if roles
                                                                            else "") + ".")
            return {"status": f"the user said yes; Ombro starts spec {name!r} now in the background. Say in "
                              "one short sentence that it is starting; do not describe the work again."}, None
        self.transcript.note(f"You said no: spec {name!r} was not started.")
        return {"status": "the user said no, so it was not started. Carry on the conversation; do not offer it "
                          "again unless they ask."}, None

    def _start_offered(self, offer) -> None:
        """The command line starts a job the user said yes to in chat."""
        from ombro.cli import main

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
        crews = [str(c) for c in ([raw] if isinstance(raw, str) else raw if isinstance(raw, list) else [])
                 if str(c) in self.home.crews()]
        if revise and not crews:
            had = mdfile.read(self.home.specs_dir / f"{revise}.md")[0].get("crews") or []
            crews = [str(c) for c in ([had] if isinstance(had, str) else had) if str(c) in self.home.crews()]
        which = f" for the {' and the '.join(crew_title(self.home, c) for c in crews)}" if crews else ""
        summary = summary.rstrip(".")
        if revise:
            self.ui.say(f"I can open your saved spec '{revise}' and change it: {summary}.", level="lead")
            question = "Shall we change it now? You'll see the changes before anything is saved."
        else:
            self.ui.say(f"This sounds like a task. I can brief it{which}: {summary}.", level="lead")
            question = "Shall we start the brief?"
        if self.ui.yes("start_brief", question, True, mode="Chat"):
            self.transcript.note(f"The Lead offered a brief ({summary}); you accepted.")
            return ({"status": "the user accepted; the task interview starts now, so do not reply"},
                    (crews, summary, revise))
        self.transcript.note(f"The Lead offered a brief ({summary}); you declined.")
        return {"status": "the user declined; carry on the conversation and do not offer again for this"}, None

    # -- remember ----------------------------------------------------------

    def _remember(self, text: str, crew: str, *, offered: bool) -> dict[str, Any]:
        """Add a dated lesson to a crew's notes.md, only once the user confirms the wording."""
        text, _ = keys.scrub(self.home, text)
        if not text.strip():
            return {"error": "the text to remember is empty"}
        entry = memory.remember_entry(text, self.today_date)
        who = "The Lead offers to remember this" if offered else "To remember"
        while True:
            self.ui.say(f"{who} in the {crew_title(self.home, crew)}'s notes (From you):\n  {entry}", spaced=True)
            answer = self.ui.ask(Field(
                "remember", "", "choice", mode="Chat",
                options=[Option("y", "yes"), Option("e", "edit the wording", ("edit",)), Option("n", "no")],
                other="@crew for another crew"))
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
                f"That would make the {crew_title(self.home, crew)}'s notes.md {new.size:,} characters, "
                f"over the cap of {cap:,}. "
                f"Your part is {len(notes.from_you):,} characters and the Lead's is {len(notes.from_jobs):,}."
            )
            if yours:
                self.ui.say("Your entries:")
                for i, ln in enumerate(yours, 1):
                    self.ui.say(f"  {i}. {_short(ln, 150)}")
            answer = str(self.ui.ask(Field(
                "over_cap", "", "choice", mode="Chat",
                options=[Option("s", "shorten the wording", ("shorten",)),
                         Option("e", "edit notes.md by hand", ("edit",)), Option("c", "cancel")],
                other="the numbers of your entries to remove (e.g. 2 5)"))).lower()
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
        """Write a skill into the library, only once the user has seen it and said yes (4.4)."""
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
                line = (f"Ombro: this skill contains text that looks written to steer a model ({', '.join(signs)}). "
                        "Read it closely before you say yes.")
                self.ui.say(line)
                self.transcript.note(line)
            answer = self.ui.ask(Field(
                "skill", "Write it?", "choice", mode="Chat",
                options=[Option("y", "yes"), Option("e", "edit it first", ("edit",)), Option("n", "no")]))
            if answer == "y":
                break
            if answer == "e":
                draft = self.home.root / "chats" / f".skill-{name}.md"
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
        self.transcript.note(f"Skill written: {where}" + (f"; old version kept in {kept}" if kept else "")
                             + (f"; {'; '.join(notes)}" if notes else ""))
        self.ui.say(f"Written to {where}." + (f" The old version is kept in {kept}." if kept else "")
                    + (f" {'; '.join(notes).capitalize()}." if notes else ""), level="done")
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
            parts.append("What the user said in the chat before this brief, oldest first:\n"
                         + "\n".join("- " + m.replace("\n", "\n  ") for m in picked))
        if summary:
            parts.append(f"The Lead's one-line summary in the chat, which the user accepted: {summary}")
        return "\n\n".join(parts)

    def _brief(self, crews: list[str], opening: str, revise: str | None = None) -> None:
        from ombro.interviews import BriefPurpose, start

        self.mode = "Brief"
        which = f" (starting from crew{'s' if len(crews) > 1 else ''} {', '.join(crews)})" if crews else ""
        self.ui.show(ModeChanged("Brief", f"── Brief mode: a task interview{which}. /chat goes back to Chat "
                                          "with nothing written. ──"))
        self.transcript.note(f"Switched to Brief mode{which}.")
        try:
            outcome = start(
                self.home, BriefPurpose(self.home, self.crew, self.base_limits, opening, crews=crews, revise=revise),
                self.crew,
                self.base_limits,
                ui=BriefModeUI(self.ui), client_factory=self._client_factory, today=self.today_date,
            )
        except (SetupError, ModelError) as e:
            self.ui.show(Failure(f"The brief could not start: {e}"))
            outcome = None
        self.mode = "Chat"
        if outcome is not None and outcome.confirmed:
            name = outcome.proposal.data["name"]
            self.specs.append(name)
            spec_path = self.home.specs_dir / f"{name}.md"
            if spec_path.is_file():  # the chat takes the spec's title, unless you named it
                self.set_title(jobs.first_line(mdfile.read(spec_path)[1], 70) or name, "spec")
            self.ui.say(f"Spec saved as {name!r}. Start it with:  ombro start --spec {name}", spaced=True, level="done")
            self.transcript.note(f"Brief mode ended: spec {name!r} saved (transcript {outcome.transcript}).")
            self.pending_note += (f"[Ombro note: in Brief mode the user confirmed a spec, saved as {name!r}. "
                                  "It has not been started.]\n")
        else:
            detail = outcome.status if outcome is not None else "could not start"
            self.transcript.note(f"Brief mode ended without a spec ({detail}).")
            self.pending_note += "[Ombro note: the user left Brief mode; no spec was saved.]\n"
        # Stopped by you: the Lead has said so, back in Chat mode included, in one line.
        stopped = outcome is not None and outcome.status == "quit"
        self.ui.show(ModeChanged("Chat", "" if stopped else "Back in Chat mode."))

    # -- transcript --------------------------------------------------------

    def _transcript_path(self) -> Path:
        """chats/new-chat-<date-time>.md until the chat has a title; then the title's words."""
        folder = self.home.root / "chats"
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
        pick = self.ui.ask(Field("chat", "Which chat would you like to carry on? (newest first)", "choice",
                                 default="1", invalid="Please choose one of the numbers shown.", mode="Chat",
                                 options=[Option(str(i), f"{c['title']}  ({c['when']})")
                                          for i, c in enumerate(shown, 1)]))
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
            "refused_tools": sorted(set(self.refused)),
            "limits": {"max_tokens": self.limits.max_tokens, "tool_max_calls": self.limits.tool_max_calls},
            "usage": _add_usage(self.prior_usage, {k: s.get(k) for k in ("total_tokens", "input_tokens",
                                                                          "output_tokens", "model_calls",
                                                                          "tool_calls")}),
        }
        body = (f"# {self.title or 'New chat'}\n\nReopen this chat with /chats, or `ombro chat --resume`.\n\n"
                + "\n\n".join(self.transcript.entries) + "\n")
        text, _ = keys.scrub(self.home, mdfile.dump(meta, body))
        mdfile.write_atomic(self.path, text)
        self.last_used, self.turns_seen = updated, said
