"""The terminal interface's shell: header, conversation, slash menu, input and
footer (specification 14.1), with chat wired to the Lead.

The core runs on a worker thread and talks to the screen only through
TuiUI, an implementation of ombro.ui.UI: events become lines in the
conversation, and a field waits for the next thing typed in the input box.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option as ListOption

from ombro import views
from ombro.home import Home
from ombro.tui import look
from ombro.ui import UI, Event, Failure, Field, LeadSaid, ModeChanged, Note, Progress, ProposalShown, Usage

# The slash commands (specification 14.1). Each does what its command in section 13 does.
SLASH = [
    ("/chat", "back to Chat from a brief"),
    ("/new", "save this chat and start a fresh one"),
    ("/brief", "brief a task: the Lead interviews you for a spec"),
    ("/runs", "runs and their status"),
    ("/report", "a run's report and its deliverables"),
    ("/stop", "stop a run; it writes its report first"),
    ("/open", "open a run's workspace folder"),
    ("/outbox", "actions waiting for your decision"),
    ("/schedule", "scheduled runs"),
    ("/crew", "crews and their roles"),
    ("/role", "add or edit a role"),
    ("/keys", "which keys are set"),
    ("/folders", "folders shared with runs"),
    ("/tools", "tools and their permissions"),
    ("/remember", "add a lesson to a crew's notes"),
    ("/preferences", "your name, theme and defaults"),
    ("/theme", "switch between light and dark"),
    ("/help", "what each command does"),
    ("/quit", "leave Ombro"),
]
CHAT_COMMANDS = ("/chat", "/new", "/brief", "/remember")  # handled by chat itself
LATER = ("/runs", "/report", "/stop", "/open", "/outbox", "/schedule", "/crew", "/role", "/keys", "/folders",
         "/tools", "/preferences")
QUIT_AGAIN_SECONDS = 2.5


def filter_slash(text: str) -> list[tuple[str, str]]:
    """The commands that start with what has been typed after the /."""
    word = text.strip().split(" ", 1)[0].lower()
    return [c for c in SLASH if c[0].startswith(word)]


# ---------------------------------------------------------------------------
# The bridge between the core's thread and the screen


class Pending:
    """A field the core is waiting on, answered from the input box."""

    def __init__(self, f: Field):
        self.field = f
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class Interrupted(KeyboardInterrupt):
    pass


class TuiUI(UI):
    """ombro.ui.UI for the terminal interface. Called from a worker thread."""

    def __init__(self, app: "OmbroApp"):
        self.app = app
        self.stop_requested = False

    def _check(self) -> None:
        if self.stop_requested:
            self.stop_requested = False
            raise Interrupted()

    def show(self, event: Event) -> None:
        self.app.call_from_thread(self.app.show_event, event)
        self._check()

    def ask(self, f: Field) -> Any:
        self._check()
        pending = Pending(f)
        self.app.call_from_thread(self.app.begin_ask, pending)
        pending.done.wait()
        if pending.error:
            raise pending.error
        return pending.value

    def edit(self, paths: list[Path]) -> None:
        self.app.call_from_thread(self.app.edit_files, paths)


# ---------------------------------------------------------------------------
# Widgets


class HeaderBlock(Widget):
    """The welcome rule, the logo, the team chart and the stats. It is the first
    thing in the conversation, so it scrolls away (specification 14)."""

    DEFAULT_CSS = "HeaderBlock { height: auto; }"

    def __init__(self, app_: "OmbroApp"):
        super().__init__()
        self.ombro = app_

    def render(self):
        from rich.console import Group
        from rich.table import Table

        app = self.ombro
        p = app.colours
        s = app.summary
        width = max(20, self.size.width or app.size.width - 8)
        which = look.tier(app.size.width, app.size.height)
        title = look.welcome(s["name"], app.first_opening)
        rule = Text("── ", style=p.dim) + Text(title, style=p.muted) + Text(" ", style=p.dim)
        rule.append("─" * max(0, width - rule.cell_len), style=p.dim)
        if which == "narrow":
            return Group(rule, Text(look.stats_line(s), style=p.muted), Text("─" * width, style=p.dim))
        chart = Text()
        for i, line in enumerate(look.chart_from_summary(s)):
            if i:
                chart.append("\n")
            if i == 0:
                chart.append(line, style=f"bold {p.strong}")
                continue
            body = line.lstrip("│└+ ")
            glyphs = line[: len(line) - len(body)]
            chart.append(glyphs, style=p.dim)
            chart.append(body, style=p.muted)
        stats = Text()
        for i, line in enumerate(look.stats_lines(s)):
            if i:
                stats.append("\n")
            stats.append(line, style=f"bold {p.strong}" if i == 0 else p.muted)
        grid = Table.grid(expand=True, padding=(0, 2))
        if which == "wide":
            grid.add_column(ratio=1, justify="center", vertical="middle")
        grid.add_column(width=max(28, max(len(x) for x in look.chart_from_summary(s)) + 2))
        grid.add_column(width=26)
        from rich.align import Align

        logo = Align.center(look.logo_text(app.dark), vertical="middle")
        cells = ([logo] if which == "wide" else []) + [chart, stats]
        grid.add_row(*cells)
        return Group(rule, Text(""), grid, Text(""), Text("─" * width, style=p.dim))


class Line(Static):
    DEFAULT_CSS = "Line { height: auto; margin: 1 0 0 0; }"


class OmbroApp(App):
    CSS = """
    Screen { background: $ombro-bg; color: $ombro-text; padding: 1 4 0 4; layout: vertical; }
    #conversation { height: 1fr; scrollbar-size-vertical: 1; scrollbar-color: $ombro-dim;
                    scrollbar-color-hover: $ombro-muted; scrollbar-color-active: $ombro-muted;
                    scrollbar-background: $ombro-bg; scrollbar-background-hover: $ombro-bg;
                    scrollbar-background-active: $ombro-bg; }
    #messages { height: auto; }
    #spacer { height: 0; }
    .lead { color: $ombro-text; padding: 0 1; }
    .you { background: $ombro-band; color: $ombro-band-text; padding: 1 1; }
    .note { color: $ombro-muted; padding: 0 1; }
    .progress { color: $ombro-dim; padding: 0 1; }
    .error { color: $ombro-text; text-style: bold; padding: 0 1; }
    .proposal { color: $ombro-text; padding: 0 1; border-left: wide $ombro-dim; }
    .rule { height: 1; color: $ombro-dim; }
    #slash { height: auto; max-height: 12; display: none; background: $ombro-bg; color: $ombro-muted;
             border: none; padding: 0; scrollbar-size-vertical: 1; scrollbar-color: $ombro-dim;
             scrollbar-color-hover: $ombro-muted; scrollbar-color-active: $ombro-muted;
             scrollbar-background: $ombro-bg; scrollbar-background-hover: $ombro-bg;
             scrollbar-background-active: $ombro-bg; }
    #slash:focus { background-tint: $ombro-bg 0%; }
    #slash > .option-list--option-highlighted { background: $ombro-band; color: $ombro-band-text;
                                                text-style: none; }
    #slash-rule { display: none; }
    #entry { height: 1; }
    #chevron { width: 2; color: $ombro-text; }
    #input, #input:focus { border: none; height: 1; padding: 0; background: $ombro-bg; color: $ombro-text;
                           background-tint: $ombro-bg 0%; }
    #input > .input--placeholder { color: $ombro-dim; }
    #input > .input--cursor { background: $ombro-muted; color: $ombro-bg; }
    #footer { height: 1; margin: 0 0 1 0; }
    #footer-left { width: 1fr; color: $ombro-muted; }
    #footer-right { width: auto; color: $ombro-muted; }
    """

    BINDINGS = [Binding("ctrl+c", "interrupt", "Stop or leave", priority=True, show=False)]

    def __init__(self, home: Home, *, dark: bool, client_factory=None):
        self.dark = dark
        self.colours = look.palette(dark)  # before Textual reads the CSS variables
        super().__init__()
        self.home = home
        self.client_factory = client_factory
        self.summary = views.summary(home)
        self.first_opening = not any((home.root / "chats").glob("*.md")) and not self.summary["unseen"] \
            and not views.run_list(home)
        self.mode = "Chat"
        try:
            from ombro.settings import load_limits

            self.usage = (0, load_limits(home).for_chat().max_tokens)
        except Exception:  # noqa: BLE001
            self.usage = (0, None)
        self.bridge = TuiUI(self)
        self.pending: Pending | None = None
        self.busy = False
        self.chat = None
        self._quit_armed = 0.0
        self.setup_needed = setup_needed(home)

    # -- colours -------------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        p = self.colours
        return {**super().get_css_variables(), "ombro-bg": p.background, "ombro-text": p.text,
                "ombro-muted": p.muted, "ombro-dim": p.dim, "ombro-band": p.band, "ombro-band-text": p.band_text}

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        self.colours = look.palette(dark)
        self.refresh_css()
        self.query_one(HeaderBlock).refresh()
        self.update_footer()

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="conversation"):
            yield HeaderBlock(self)
            yield Static("", id="spacer")
            yield Vertical(id="messages")
        yield Static("", classes="rule", id="slash-rule")
        yield OptionList(id="slash")
        yield Static("", classes="rule", id="rule-above")
        with Horizontal(id="entry"):
            yield Static(">", id="chevron")
            yield Input(placeholder="Chat or /brief", id="input")
        yield Static("", classes="rule", id="rule-below")
        with Horizontal(id="footer"):
            yield Static("", id="footer-left")
            yield Static("", id="footer-right")

    def on_mount(self) -> None:
        self.query_one("#messages").styles.height = "auto"
        self.draw_rules()
        self.update_footer()
        if self.setup_needed:
            self.add_line(Note(f"Ombro is not set up here yet: {self.setup_needed} Set-up inside this screen "
                               "comes in a later build; for now, run `ombro init` in a shell, then open Ombro "
                               "again."), "note")
        else:
            self.add_line(LeadSaid(look.greeting(self.summary["name"])), "lead")
            since = look.since_line(views.since_last(self.home))
            if since:
                self.add_line(Note(since), "note")
        self.query_one("#input", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.draw_rules()
        self.update_footer()
        self.call_after_refresh(self.fit_spacer)

    def fit_spacer(self) -> None:
        """Keep the header at the top and the messages at the bottom until they meet;
        after that the conversation scrolls and the header goes off the top."""
        conv = self.query_one("#conversation")
        used = self.query_one(HeaderBlock).outer_size.height + self.query_one("#messages").outer_size.height
        self.query_one("#spacer").styles.height = max(0, conv.size.height - used)

    def draw_rules(self) -> None:
        width = max(10, self.size.width - 8)
        for rid in ("#rule-above", "#rule-below", "#slash-rule"):
            self.query_one(rid, Static).update("─" * width)

    def update_footer(self) -> None:
        p = self.colours
        model = self.summary.get("model") or "no model"
        left = Text(f"Model: {model}", style=p.muted)
        if self.size.width >= 70:
            left += Text("   / for commands", style=p.dim)
        right = Text()
        if self.mode == "Brief":
            right.append("Brief   ", style=f"bold {p.strong}")
        right.append(str(self.summary.get("outward") or "balanced").title() + "   ", style=p.dim)
        right.append(look.usage_text(*self.usage), style=p.muted)
        self.query_one("#footer-left", Static).update(left)
        self.query_one("#footer-right", Static).update(right)
        placeholder = "Complete the brief interview, or /chat" if self.mode == "Brief" else "Chat or /brief"
        self.query_one("#input", Input).placeholder = placeholder

    # -- the conversation ------------------------------------------------------

    def add_line(self, event: Event, kind: str) -> None:
        text = render_event(event, self.colours)
        if text is None:
            return
        box = self.query_one("#messages")
        box.mount(Line(text, classes=kind))
        self.call_after_refresh(self._after_line)

    def _after_line(self) -> None:
        self.fit_spacer()
        self.call_after_refresh(self.query_one("#conversation").scroll_end, animate=False)

    def show_event(self, event: Event) -> None:
        if isinstance(event, Usage):
            self.usage = (event.total_tokens, event.max_tokens)
            self.update_footer()
            return
        if isinstance(event, ModeChanged):
            self.mode = event.mode
            self.update_footer()
            if event.text:
                self.add_line(Note(event.text.strip("─ ").strip()), "note")
            return
        kind = {LeadSaid: "lead", Progress: "progress", Failure: "error", ProposalShown: "proposal"}.get(
            type(event), "note")
        self.add_line(event, kind)

    def begin_ask(self, pending: Pending) -> None:
        f = pending.field
        self.pending = pending
        inp = self.query_one("#input", Input)
        inp.password = f.kind == "secret"
        if f.kind in ("choice", "yesno") or (f.label and f.label not in ("You", "answer")):
            self.add_line(Note(field_prompt(f)), "note")
        inp.focus()

    def edit_files(self, paths: list[Path]) -> None:
        import subprocess

        from ombro.interview import editor_command

        cmd = editor_command(self.home)
        if not cmd:
            self.add_line(Note("No editor is set (`editor` in preferences, or $EDITOR). Edit these files "
                               "yourself, then answer here:", items=[str(p) for p in paths]), "note")
            return
        with self.suspend():
            subprocess.run([*cmd, *map(str, paths)], check=False)

    # -- input -----------------------------------------------------------------

    @on(Input.Changed, "#input")
    def typed(self, event: Input.Changed) -> None:
        menu = self.query_one("#slash", OptionList)
        value = event.value
        show = value.startswith("/") and " " not in value and self.pending is None
        matches = filter_slash(value) if show else []
        menu.clear_options()
        p = self.colours
        for cmd, help_ in matches:
            menu.add_option(ListOption(Text(cmd, style=p.text) + Text(f"  {help_}", style=p.muted), id=cmd))
        menu.display = bool(matches)
        self.query_one("#slash-rule").display = bool(matches)
        if matches:
            menu.highlighted = 0

    def on_key(self, event: events.Key) -> None:
        menu = self.query_one("#slash", OptionList)
        if not menu.display:
            return
        if event.key in ("down", "up"):
            menu.action_cursor_down() if event.key == "down" else menu.action_cursor_up()
            event.stop()
        elif event.key == "escape":
            menu.display = False
            self.query_one("#slash-rule").display = False
            event.stop()
        elif event.key == "tab":
            self._take_slash()
            event.stop()

    def _take_slash(self) -> str | None:
        menu = self.query_one("#slash", OptionList)
        if not menu.display or menu.highlighted is None:
            return None
        cmd = menu.get_option_at_index(menu.highlighted).id
        inp = self.query_one("#input", Input)
        inp.value = cmd + " "
        inp.cursor_position = len(inp.value)
        menu.display = False
        self.query_one("#slash-rule").display = False
        return cmd

    @on(Input.Submitted, "#input")
    def submitted(self, event: Input.Submitted) -> None:
        inp = self.query_one("#input", Input)
        text = event.value
        menu = self.query_one("#slash", OptionList)
        if menu.display and text.strip() == text.strip().split(" ")[0]:
            # Enter on the menu takes the highlighted command
            cmd = self._take_slash()
            if cmd and cmd != text.strip():
                return
            text = cmd or text
        inp.value = ""
        if self.pending is not None:
            self._answer(text)
            return
        if not text.strip():
            return
        self.add_line(Note(text), "you")
        self.handle(text.strip())

    def _answer(self, text: str) -> None:
        pending = self.pending
        f = pending.field
        shown = "•" * len(text) if f.kind == "secret" else text
        if f.kind != "secret" or text:
            self.add_line(Note(shown or "(Enter)"), "you")
        if f.kind == "yesno":
            low = text.strip().lower()
            value = bool(f.default) if not low else low in ("y", "yes")
        elif f.kind == "choice":
            raw = text if text.strip() or f.default in (None, "") else str(f.default)
            value = f.choose(raw)
            if value is None and f.other and raw.strip():
                value = raw.strip()
            if value is None:
                self.add_line(Note(f.invalid or f.check(raw) or "Choose one of those shown."), "note")
                return
        elif f.kind == "wait":
            value = None
        else:
            value = text if text.strip() or f.default in (None, "") else str(f.default)
            problem = f.check(value)
            if problem:
                self.add_line(Note(problem), "note")
                return
        self.pending = None
        self.query_one("#input", Input).password = False
        pending.value = value
        pending.done.set()

    # -- commands and chat -----------------------------------------------------

    def handle(self, text: str) -> None:
        word = text.split(" ", 1)[0].lower()
        if word in ("/quit", "/q", "/exit"):
            self.leave()
            return
        if word == "/theme":
            from ombro.settings import set_preference

            self.set_dark(not self.dark)
            set_preference(self.home, "theme", "dark" if self.dark else "light")
            self.add_line(Note(f"Theme: {'dark' if self.dark else 'light'}. Saved in preferences."), "note")
            return
        if word == "/help":
            self.add_line(Note("Commands:", items=[f"{c}  {h}" for c, h in SLASH]), "note")
            return
        if word in LATER:
            self.add_line(Note(f"{word} is not in this build of the terminal interface yet. For now, use "
                               f"`ombro {word[1:]}` in a shell."), "note")
            return
        if self.setup_needed:
            self.add_line(Note("Run `ombro init` in a shell first."), "note")
            return
        if self.busy:
            self.add_line(Note("The Lead is still working on your last message."), "note")
            return
        self.busy = True
        self.run_worker(lambda: self._chat_turn(text), thread=True, exclusive=False)

    def _chat_turn(self, text: str) -> None:
        try:
            if self.chat is None:
                from ombro.chat import Chat

                kw = {"client_factory": self.client_factory} if self.client_factory else {}
                self.chat = Chat(self.home, ui=self.bridge, **kw)
            go_on = self.chat.handle(text)
            if not go_on:
                self.call_from_thread(self.leave)
        except KeyboardInterrupt:
            if self.chat is not None and self.chat.exchanges and \
                    self.chat.exchanges[-1][-1].get("role") == "user":
                self.chat.exchanges.pop()
            self.call_from_thread(self.add_line, Note("Stopped."), "note")
        except Exception as e:  # noqa: BLE001 - shown, never a crash
            self.call_from_thread(self.add_line, Failure(f"Something went wrong: {e}"), "error")
        finally:
            self.busy = False
            self.bridge.stop_requested = False
            self.call_from_thread(self.refresh_summary)

    def refresh_summary(self) -> None:
        self.summary = views.summary(self.home)
        self.query_one(HeaderBlock).refresh()
        self.update_footer()

    def action_interrupt(self) -> None:
        inp = self.query_one("#input", Input)
        if self.pending is not None:
            pending, self.pending = self.pending, None
            inp.password = False
            pending.error = Interrupted()
            pending.done.set()
            return
        if self.busy:
            self.bridge.stop_requested = True
            self.add_line(Note("Stopping after the current step…"), "progress")
            return
        if inp.value:
            inp.value = ""
            return
        now = time.monotonic()
        if now - self._quit_armed < QUIT_AGAIN_SECONDS:
            self.leave()
            return
        self._quit_armed = now
        self.query_one("#footer-left", Static).update(Text("Press Ctrl+C again to leave", style=self.colours.text))
        self.set_timer(QUIT_AGAIN_SECONDS, self.update_footer)

    def leave(self) -> None:
        if self.chat is not None and self.chat.exchanges:
            try:
                self.chat._save()
            except OSError:
                pass
        self.exit(0)


# ---------------------------------------------------------------------------
# Rendering events as lines


def field_prompt(f: Field) -> str:
    from ombro.ui import option_text

    if f.kind == "yesno":
        return f"{f.label} (y or n{', Enter for ' + ('yes' if f.default else 'no')})"
    if f.kind == "choice":
        opts = "; ".join(f"{o.key}: {o.label}" for o in f.options)
        return (f"{f.label} " if f.label else "") + f"Type {opts}" + (f"; or {f.other}" if f.other else "") + "."
    if f.kind == "secret":
        return f"{f.label} (hidden as you type)"
    return f.label + (f" (Enter for {f.default})" if f.default not in (None, "") else "")


def render_event(event: Event, p: look.Palette) -> Text | None:
    if isinstance(event, LeadSaid):
        return Text("• ", style=p.muted) + Text(event.text, style=p.text)
    if isinstance(event, Progress):
        return Text(event.text, style=p.dim)
    if isinstance(event, Failure):
        return Text(event.text, style=f"bold {p.text}")
    if isinstance(event, ProposalShown):
        out = Text()
        for f in event.files:
            out.append(f"{f.label} ({'replaces the current file' if f.replaces else 'new file'})\n",
                       style=f"bold {p.strong}")
            out.append(f.text.rstrip() + "\n\n", style=p.text)
            if f.diff:
                out.append("Changes from the current file:\n", style=p.muted)
                out.append("\n".join(f.diff) + "\n\n", style=p.muted)
        for w in event.warnings:
            out.append(f"Note: {w}\n", style=p.muted)
        return out
    if isinstance(event, Note):
        if not event.text and not event.items:
            return None
        out = Text(event.text)
        for item in event.items:
            out.append(f"\n  - {item}")
        return out
    return Text(str(event.to_dict()))


# ---------------------------------------------------------------------------
# Starting


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


def run_app(home: Home | None = None) -> int:
    from ombro.settings import load_preferences
    from ombro.tui.detect import is_dark

    home = home or Home()
    if home.root.is_dir() and home.old_layout():
        print("ombro: This home was made by an earlier Ombro. Move it aside and run `ombro init`.")
        return 2
    try:
        theme = load_preferences(home).theme if home.root.is_dir() else "auto"
    except Exception:  # noqa: BLE001
        theme = "auto"
    OmbroApp(home, dark=is_dark(theme)).run()
    return 0
