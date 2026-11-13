"""General chat with the Lead: `ombro chat`.

Two modes, always shown in the prompt. Chat is an ordinary conversation;
Brief is the task interview, entered when the user accepts the Lead's offer
or types /brief, and left with /chat.

Chat reads across every crew (past reports, specs, notes, indexes and
deliverables), plus web search and fetch, and works in the user's shared
folders as the user would: reading them, and writing in those shared for
writing, each write shown to the user and confirmed first. It cannot brief
roles, start runs, approve the outbox or use outward tools. Its only other
write is a remember the user has confirmed, into a crew's notes.md
("From you").

It has its own gate: a token cap, per-tool call caps and a cap on model
calls per message. Once the conversation passes a set length the oldest
turns stop being sent. Each chat is saved in chats/; transcripts are never
loaded into later chats or runs, and never searched.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ombro import keys, material, mdfile, memory, prompts, runs
from ombro.client import ChatClient, ModelError, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home, crew_title, find_crew
from ombro.ui import UI, ChatCleared, ConsoleUI, Failure, Field, LeadSaid, ModeChanged, Note, Option, Usage
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
OFFERS = ("offer_brief", "offer_remember", "offer_run")

HELP = """\
Chat mode is a conversation with the Lead. It can look at every crew's reports,
notes, specs and deliverables, search the web, and work in your shared folders.
It changes a file only after you say yes.

  /brief [@crew] [text]     brief a task (Brief mode); the chat so far is its starting point
  /chat                     back to Chat mode from a brief (nothing is written)
  /new                      save this chat and start a fresh one; nothing carries over
  /remember [@crew] text    add a dated lesson to a crew's notes.md, after you confirm the wording
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
    ):
        self.home = home
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
        self.run_offers: list[str] = []  # saved specs the Lead offered to run; the interface asks the user
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
        old = self.path
        self._fresh()
        try:
            shown = old.relative_to(self.home.root)
        except ValueError:
            shown = old
        self.ui.show(ChatCleared())
        self.ui.say(f"I've saved the last chat to {shown}. This is a new chat. How would you like me to help?",
                    level="lead")
        self._usage_line()

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
            all_runs = runs.all_runs(self.home, c)
            latest = all_runs[-1].name[:8] if all_runs else ""
            when = f", latest {latest[:4]}-{latest[4:6]}-{latest[6:8]}" if latest else ""
            mark = ", the default crew" if c == self.crew else ""
            lines.append(f"- the {crew_title(self.home, c)} (crew name `{c}` for tools{mark}): "
                         f"{meta.get('description', '')} Roles: {', '.join(roles) or 'none'}. "
                         f"Runs: {len(all_runs)}{when}.")
        lines.append("Speak of each crew by its title, never by its crew name.")
        lines.append("\nFiles are under the home directory: runs/<run_id>/report.md, spec.md and workspace/ "
                     "(a run may have used several crews); specs/<name>.md; crews/<crew>/memory/notes.md and "
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
                                     self.today, self._folders_text())
        # Past a set length, the oldest turns are dropped from what is sent.
        while len(self.exchanges) > 1 and len(json.dumps(self.exchanges, ensure_ascii=False)) > \
                self.limits.chat_history_chars:
            self.exchanges.pop(0)
            self.dropped += 1
            if self.dropped == 1:
                self.ui.say("(The conversation is long, so its oldest turns are no longer sent to the Lead. "
                         "The transcript keeps everything.)")
                self.transcript.note("From here on, the oldest turns were no longer sent to the Lead.")
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
                while self.run_offers:  # the user said yes in chat; the command line starts it here
                    self._run_offered(self.run_offers.pop(0))
        finally:
            self.close()
        return self

    def start(self) -> None:
        self.ui.say(
            f"Chat with the Lead (default crew: the {crew_title(self.home, self.crew)}). Mode: Chat. Nothing "
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
        if request:
            revise = request[2] if len(request) > 2 else None
            self._brief(request[0], self.brief_opening("", request[1]), revise=revise)
        self._save()
        return True

    def close(self) -> None:
        self._save()
        self.ui.say(f"Chat saved: {self.path}")

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
        elif word == "/brief":
            crew, rest = self._crew_arg(rest)
            if crew:
                self._brief([crew] if rest_named(text) else [], self.brief_opening(rest, None))
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
            offering = any(tc.name in ("offer_run", "offer_brief") for tc in reply.tool_calls or [])
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
                if tc.name not in OFFERS and tc.name not in ("folders__write", "folders__set_aside") and not (
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
        internal = MEMORY_TOOLS.get(name) or WEB_TOOLS.get(name) or FOLDER_TOOLS.get(name)
        if name not in OFFERS and (internal is None or (name in WEB_TOOLS and self.registry.get(internal) is None)):
            self.refused.append(name)
            self.transcript.note(f"Refused {name}: not available in chat.")
            return {"error": f"{name} is not available in chat. Chat cannot brief roles, start runs, approve the "
                             "outbox or use outward tools; files are written only in shared folders, with "
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
        if name == "offer_run":
            return self._offer_run(args)
        if name == "offer_remember":
            crew = str(args.get("crew") or self.crew)
            if crew not in self.home.crews():
                return {"error": f"no crew named {crew!r}; crews: {', '.join(self.home.crews())}"}, None
            return self._remember(str(args.get("text") or ""), crew, offered=True), None
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
                    run_id=str(args.get("run_id") or "") or None,
                    max_chars=self.limits.memory_search_max_chars,
                ), None
            if internal == "memory.read":
                return memory.read_file(self.home, args.get("path"), args.get("offset") or 0,
                                        args.get("max_chars") or memory.READ_DEFAULT_CHARS), None
            if internal in FOLDER_TOOLS.values():
                return self._folder_tool(internal, args), None
            if internal == "memory.list":
                return memory.listing(self.home, str(args.get("crew") or "") or None,
                                      str(args.get("run_id") or "") or None), None
            tool = self.registry.get(internal)
            ctx = ToolContext(self.home.root / "chats", "lead", timeout=60.0)
            return tool.handler(args, ctx), None
        except memory.ReadRefused as e:
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
        # No run folder: chat reaches only shared/<name>/..., never workspace/ or scratch/.
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

    def _offer_run(self, args: dict) -> tuple[dict[str, Any], tuple | None]:
        """The Lead offers to run a saved spec again, in its own voice: yes starts it (the
        interface does), "change it" opens the spec for changing, and no leaves it. Roles
        given here apply to this run only; the saved spec is unchanged (10.4)."""
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
        self.transcript.note(f"The Lead offered to run saved spec {name!r} again ({how}).")
        answer = self.ui.ask(Field(
            "run_now", 'If you prefer to read through the spec again and change it, just say "change it". '
                       "Otherwise, should I just start it?", "yesno", default=True, other="change it", mode="Chat"))
        if answer == "change it":
            self.transcript.note(f"You chose to change spec {name!r} first.")
            return ({"status": "the user wants to change the spec first; the interview starts now, so do not "
                               "reply"}, (crews, how, name))
        if answer:
            self.run_offers.append((name, roles))
            self.transcript.note(f"You said yes: spec {name!r} starts" + (f" with {', '.join(roles)}" if roles
                                                                            else "") + ".")
            return {"status": f"the user said yes; Ombro starts spec {name!r} now in the background. Say in "
                              "one short sentence that it is starting; do not describe the work again."}, None
        self.transcript.note(f"You said no: spec {name!r} was not started.")
        return {"status": "the user said no, so it was not started. Carry on the conversation; do not offer it "
                          "again unless they ask."}, None

    def _run_offered(self, offer) -> None:
        """The command line starts a run the user said yes to in chat."""
        from ombro.cli import main

        spec, roles = offer if isinstance(offer, tuple) else (offer, [])
        main(["run", "--spec", spec, "--detach", *[a for r in roles for a in ("--role", r)]])

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
                f"Your part is {len(notes.from_you):,} characters and the Lead's is {len(notes.from_runs):,}."
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
            self.ui.say(f"Spec saved as {name!r}. Run it with:  ombro run --spec {name}", spaced=True, level="done")
            self.transcript.note(f"Brief mode ended: spec {name!r} saved (transcript {outcome.transcript}).")
            self.pending_note += (f"[Ombro note: in Brief mode the user confirmed a spec, saved as {name!r}. "
                                  "It has not been run.]\n")
        else:
            detail = outcome.status if outcome is not None else "could not start"
            self.transcript.note(f"Brief mode ended without a spec ({detail}).")
            self.pending_note += "[Ombro note: the user left Brief mode; no spec was saved.]\n"
        self.ui.show(ModeChanged("Chat", "Back in Chat mode."))

    # -- transcript --------------------------------------------------------

    def _transcript_path(self) -> Path:
        folder = self.home.root / "chats"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}.md"
        n = 2
        while path.exists():
            path = folder / f"{stamp}-{n}.md"
            n += 1
        return path

    def _save(self) -> None:
        s = self.gate.snapshot()
        meta = {
            "kind": "chat",
            "default_crew": self.crew,
            "started_at": self.transcript.started,
            "updated_at": utc_now(),
            "ended": self.ended,
            "remembered": self.remembered,
            "specs_saved": self.specs,
            "refused_tools": sorted(set(self.refused)),
            "limits": {"max_tokens": self.limits.max_tokens, "tool_max_calls": self.limits.tool_max_calls},
            "usage": {k: s.get(k) for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls",
                                            "tool_calls")},
        }
        body = ("# Chat\n\nSaved, not remembered: this transcript is never loaded into a later chat or run.\n\n"
                + "\n\n".join(self.transcript.entries) + "\n")
        text, _ = keys.scrub(self.home, mdfile.dump(meta, body))
        mdfile.write_atomic(self.path, text)
