"""General chat with the Lead: `ombro chat`.

Two modes, always shown in the prompt. Chat is an ordinary conversation;
Brief is the task interview, entered when the user accepts the Lead's offer
or types /brief, and left with /chat.

Chat is read-only and reads across every crew: past reports, specs, notes,
indexes and deliverables, plus web search and fetch. It cannot brief roles,
start runs, approve the outbox, write files or use outward tools. Its only
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

from ombro import keys, mdfile, memory, prompts, runs
from ombro.client import ChatClient, ModelError, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home
from ombro.interview import read_answer
from ombro.journal import utc_now
from ombro.roles import load_lead
from ombro.settings import ModelSpec, SetupError, format_tokens, load_limits, load_models, load_preferences, load_tool_settings
from ombro.tools import ToolContext, ToolError, ToolRegistry

QUIT_WORDS = ("/quit", "/q", "/exit", "/end")
TOOL_RESULT_CAP = 12_000
BRIEF_CONTEXT_CHARS = 4_000

MEMORY_TOOLS = {"memory__search": "memory.search", "memory__read": "memory.read", "memory__list": "memory.list"}
WEB_TOOLS = {"web__search": "web.search", "web__fetch": "web.fetch"}
OFFERS = ("offer_brief", "offer_remember")

HELP = """\
Chat mode is a conversation with the Lead. It can look at every crew's reports,
notes, specs and deliverables, and search the web, but it changes nothing.

  /brief [@crew] [text]     brief a task (Brief mode); the chat so far is its starting point
  /chat                     back to Chat mode from a brief (nothing is written)
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


def _tool_msg(call_id: str, payload: Any) -> dict:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(content) > TOOL_RESULT_CAP:
        content = content[:TOOL_RESULT_CAP] + "…[truncated]"
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


class Chat:
    def __init__(
        self,
        home: Home,
        *,
        crew: str | None = None,
        ask: Callable[[str], str] | None = None,
        say: Callable[[str], None] | None = None,
        edit: Callable[[list[Path]], None] | None = None,
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
        self.ask = ask or (lambda prompt: input(prompt))
        self.say = say or (lambda text: print(text, flush=True))
        self.edit = edit
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
        self.ended = "you ended the chat"
        self.path = self._transcript_path()

    # -- plumbing ----------------------------------------------------------

    def _input(self, prompt: str) -> str:
        raw = read_answer(self.ask, prompt)
        text, removed = keys.scrub(self.home, (raw or "").strip())
        if removed:
            self.say("(Something that looked like a key was removed before it went any further. "
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
            mark = " (the default crew)" if c == self.crew else ""
            lines.append(f"- {c}{mark}: {meta.get('description', '')} Roles: {', '.join(roles) or 'none'}. "
                         f"Runs: {len(all_runs)}{when}.")
        lines.append("\nFiles are under the home directory: runs/<run_id>/report.md, spec.md and workspace/ "
                     "(a run may have used several crews); specs/<name>.md; crews/<crew>/memory/notes.md and "
                     "index.md.")
        return "\n".join(lines)

    def _limits_line(self) -> str:
        caps = ", ".join(f"{k} {v}" for k, v in self.limits.tool_max_calls.items() if k != "default")
        return (f"- tokens for this chat: {format_tokens(self.limits.max_tokens)}\n"
                f"- tool calls for this chat: {caps}\n"
                f"- model calls for one message: {self.limits.chat_calls_per_message}\n"
                f"- older turns stop being sent once the conversation passes "
                f"{self.limits.chat_history_chars:,} characters")

    def _messages(self) -> list[dict]:
        system = prompts.chat_system(self.lead.soul, self.instructions, self._crews_text(), self._limits_line(),
                                     self.today)
        # Past a set length, the oldest turns are dropped from what is sent.
        while len(self.exchanges) > 1 and len(json.dumps(self.exchanges, ensure_ascii=False)) > \
                self.limits.chat_history_chars:
            self.exchanges.pop(0)
            self.dropped += 1
            if self.dropped == 1:
                self.say("(The conversation is long, so its oldest turns are no longer sent to the Lead. "
                         "The transcript keeps everything.)")
                self.transcript.note("From here on, the oldest turns were no longer sent to the Lead.")
        return [{"role": "system", "content": system}] + [m for ex in self.exchanges for m in ex]

    def _usage_line(self) -> None:
        s = self.gate.snapshot()
        tools = sum((s.get("tool_calls") or {}).values())
        self.say(f"[{self.mode} · {s['total_tokens']:,} of {format_tokens(self.limits.max_tokens)} tokens · "
                 f"{tools} tool call(s)]")

    # -- the conversation --------------------------------------------------

    def run(self) -> "Chat":
        self.say(
            f"Chat with the Lead (default crew: {self.crew}). Mode: Chat. Read-only: nothing changes unless you "
            f"confirm a /remember. /brief turns this into a task brief, /help lists commands, /quit ends. "
            f"(Up to {format_tokens(self.limits.max_tokens)} tokens.)\n"
        )
        try:
            while True:
                try:
                    text = self._input(f"[{self.mode}] You: ")
                except (EOFError, KeyboardInterrupt):
                    self.say("")
                    break
                if not text:
                    continue
                if not self._command(text):
                    if text.lower() in QUIT_WORDS:
                        break
                    self._save()  # a remember or a brief is on record at once
                    continue
                try:
                    request = self._turn(text)
                except LimitReached as e:
                    self.ended = f"stopped by the chat limit {e.limit}: {e.detail}"
                    self.transcript.note(f"The chat stopped: {e.detail}")
                    self.say(f"\nThe chat has reached its limit: {e.detail}. It ends here.")
                    break
                except ModelError as e:
                    self.exchanges.pop()
                    self.transcript.note(f"The model call failed: {e}")
                    self.say(f"\nThe model call failed: {e}. Say it again, or /quit.")
                    continue
                self._usage_line()
                if request:
                    self._brief(request[0], self.brief_opening("", request[1]))
                self._save()
        finally:
            self._save()
            self.say(f"Chat saved: {self.path}")
        return self

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
            self.say(HELP)
        elif word == "/chat":
            self.say("You are in Chat mode.")
        elif word == "/brief":
            crew, rest = self._crew_arg(rest)
            if crew:
                self._brief([crew] if rest_named(text) else [], self.brief_opening(rest, None))
        elif word == "/remember":
            crew, rest = self._crew_arg(rest)
            if crew:
                if not rest.strip():
                    rest = self._input("[Chat] What should be remembered? ")
                if rest.strip():
                    result = self._remember(rest, crew, offered=False)
                    if result.get("status") == "saved":
                        self.pending_note += (f"[Ombro note: the user added this to crew {crew}'s notes.md: "
                                              f"{result['entry']}]\n")
        else:
            self.say(f"Unknown command {word}. /help lists them.")
        return False

    def _crew_arg(self, rest: str) -> tuple[str | None, str]:
        rest = rest.strip()
        if not rest.startswith("@"):
            return self.crew, rest
        name, _, remainder = rest[1:].partition(" ")
        if name not in self.home.crews():
            self.say(f"No crew named {name!r}. Crews: {', '.join(self.home.crews())}.")
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
            if said:
                self.say(f"\nLead: {said}\n")
                self.transcript.lead(said)
            if not reply.tool_calls:
                return None
            request = None
            for tc in reply.tool_calls:
                if request:
                    result = {"error": "ignored: the brief is starting"}
                else:
                    result, request = self._tool(tc)
                self.exchanges[-1].append(_tool_msg(tc.id, result))
            if request:
                return request
        self.say(f"(The Lead used all {self.limits.chat_calls_per_message} model calls allowed for one message "
                 "without answering. Ask again more narrowly.)")
        self.transcript.note("The Lead used all its model calls for this message without answering.")
        return None

    # -- tools -------------------------------------------------------------

    def _tool(self, tc) -> tuple[dict, tuple[list[str], str] | None]:
        name = tc.name
        internal = MEMORY_TOOLS.get(name) or WEB_TOOLS.get(name)
        if name not in OFFERS and (internal is None or (name in WEB_TOOLS and self.registry.get(internal) is None)):
            self.refused.append(name)
            self.transcript.note(f"Refused {name}: chat is read-only.")
            return {"error": f"{name} is not available in chat. Chat is read-only: it cannot brief roles, start "
                             "runs, approve the outbox, write files or use outward tools. To turn this into a task, "
                             "call offer_brief."}, None
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

    def _offer_brief(self, args: dict) -> tuple[dict, tuple[list[str], str] | None]:
        summary = _short(args.get("summary") or "", 300)
        raw = args.get("crews") or args.get("crew") or []
        crews = [str(c) for c in ([raw] if isinstance(raw, str) else raw if isinstance(raw, list) else [])
                 if str(c) in self.home.crews()]
        which = f" for crew{'s' if len(crews) > 1 else ''} {', '.join(crews)}" if crews else ""
        self.say(f"\nThe Lead offers to brief this as a task{which}: {summary}")
        answer = self._input("[Chat] Start a brief now? [y/N] ").lower()
        if answer in ("y", "yes"):
            self.transcript.note(f"The Lead offered a brief ({summary}); you accepted.")
            return {"status": "the user accepted; the task interview starts now, so do not reply"}, (crews, summary)
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
            self.say(f"\n{who} in crew {crew}'s notes.md (From you):\n  {entry}")
            answer = self._input("[Chat] [y]es, [e]dit the wording, [n]o, or @crew for another crew? ")
            low = answer.lower()
            if low in ("y", "yes"):
                break
            if low in ("e", "edit"):
                new = self._input("[Chat] The wording: ")
                if new.strip():
                    entry = memory.remember_entry(new, self.today_date)
                continue
            if answer.startswith("@"):
                name = answer[1:].strip()
                if name in self.home.crews():
                    crew = name
                else:
                    self.say(f"No crew named {name!r}. Crews: {', '.join(self.home.crews())}.")
                continue
            self.transcript.note(f"Not remembered: {entry}")
            self.say("Nothing was written.")
            return {"status": "the user declined; nothing was written"}

        cap = self.base_limits.memory_notes_max_chars
        notes = memory.read_notes(self.home, crew)
        while True:
            new = memory.with_your_entry(notes, entry)
            if new.size <= cap:
                memory.save_notes(self.home, crew, new)
                self.remembered.append({"crew": crew, "entry": entry})
                self.transcript.note(f"Remembered in crew {crew}'s notes.md: {entry}")
                self.say(f"Remembered in {memory.notes_path(self.home, crew)}.")
                return {"status": "saved", "crew": crew, "entry": entry}
            yours = notes.your_entries()
            self.say(
                f"\nThat would make crew {crew}'s notes.md {new.size:,} characters, over the cap of {cap:,}. "
                f"Your part is {len(notes.from_you):,} characters and the Lead's is {len(notes.from_runs):,}."
            )
            if yours:
                self.say("Your entries:")
                for i, ln in enumerate(yours, 1):
                    self.say(f"  {i}. {_short(ln, 150)}")
            answer = self._input(
                "[Chat] Remove your entries by number (e.g. 2 5), [s]horten the wording, [e]dit notes.md by hand, "
                "or [c]ancel? "
            ).lower()
            numbers = [int(x) for x in answer.replace(",", " ").split() if x.isdigit()]
            if numbers and len(numbers) == len(answer.replace(",", " ").split()):
                notes = memory.without_your_entries(notes, numbers)
                continue
            if answer in ("s", "shorten"):
                new_text = self._input("[Chat] The shorter wording: ")
                if new_text.strip():
                    entry = memory.remember_entry(new_text, self.today_date)
                continue
            if answer in ("e", "edit"):
                path = memory.notes_path(self.home, crew)
                if self.edit:
                    self.edit([path])
                else:
                    from ombro.interview import open_in_editor

                    open_in_editor(self.home, [path], self._input, self.say)
                notes = memory.read_notes(self.home, crew)
                continue
            self.transcript.note(f"Not remembered (over the cap): {entry}")
            self.say("Nothing was written.")
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

    def _brief(self, crews: list[str], opening: str) -> None:
        from ombro.interviews import BriefPurpose, start

        self.mode = "Brief"
        which = f" (starting from crew{'s' if len(crews) > 1 else ''} {', '.join(crews)})" if crews else ""
        self.say(f"\n── Brief mode: a task interview{which}. /chat goes back to Chat with nothing written. ──")
        self.transcript.note(f"Switched to Brief mode{which}.")

        def ask(prompt: str) -> str:
            raw = self.ask(f"[Brief] {prompt}")
            if (raw or "").strip().lower() == "/chat":
                raise KeyboardInterrupt
            return raw

        try:
            outcome = start(
                self.home, BriefPurpose(self.home, self.crew, self.base_limits, opening, crews=crews), self.crew,
                self.base_limits,
                ask=ask, say=self.say, edit=self.edit, client_factory=self._client_factory, today=self.today_date,
            )
        except (SetupError, ModelError) as e:
            self.say(f"The brief could not start: {e}")
            outcome = None
        self.mode = "Chat"
        if outcome is not None and outcome.confirmed:
            name = outcome.proposal.data["name"]
            self.specs.append(name)
            self.say(f"\nSpec saved as {name!r}. Run it with:  ombro run --spec {name}")
            self.transcript.note(f"Brief mode ended: spec {name!r} saved (transcript {outcome.transcript}).")
            self.pending_note += (f"[Ombro note: in Brief mode the user confirmed a spec, saved as {name!r}. "
                                  "It has not been run.]\n")
        else:
            detail = outcome.status if outcome is not None else "could not start"
            self.transcript.note(f"Brief mode ended without a spec ({detail}).")
            self.pending_note += "[Ombro note: the user left Brief mode; no spec was saved.]\n"
        self.say("Back in Chat mode.\n")

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
