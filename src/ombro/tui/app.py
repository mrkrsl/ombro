"""The terminal interface's shell: header, conversation, slash menu, input and
footer (specification 14.1), with chat wired to the Lead.

The core runs on a worker thread and talks to the screen only through
TuiUI, an implementation of ombro.ui.UI: events become lines in the
conversation, and a field waits for the next thing typed in the input box.
"""

from __future__ import annotations

import random
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
from textual.message import Message
from textual.widgets import Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option as ListOption

from ombro import commands as core_commands
from ombro import jobs, views
from ombro.home import Home
from ombro.session import JobWatcher, Session, setup_needed  # noqa: F401 - setup_needed kept importable here
from ombro.tui import look
from ombro.ui import (UI, ChatCleared, Event, Failure, Field, JobEnded, JobProgress, JobStarted, LeadSaid,
                      ModeChanged, Note, PastTurn, Progress, ProposalShown, SetAside, Usage, field_prompt,
                      parse_answer, sets_aside)

# The slash commands (specification 14.1), as every interface lists them.
SLASH = [(c.name, c.help) for c in core_commands.COMMANDS]
CHAT_COMMANDS = ("/chat", "/new", "/chats", "/rename", "/brief", "/remember")  # handled by chat itself
TUI_COMMANDS = ("/start", "/jobs", "/report", "/stop", "/open", "/approvals", "/schedule", "/crew", "/role", "/model",
                "/models",
                "/keys", "/folder", "/folders", "/limits",
                "/tools", "/skills", "/preferences")
QUIT_AGAIN_SECONDS = 2.5
THINKING_FRAMES = ("·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢")
THINKING_SECONDS = 0.12
PACE_SECONDS = (0.4, 1.2)  # the pause before each of set-up's lines, chosen at random
POLL_SECONDS = 2.0


def filter_slash(text: str) -> list[tuple[str, str]]:
    """The commands that start with what has been typed after the /."""
    return [(c.name, c.help) for c in core_commands.matching(text)]


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
        if not self.app.is_running:
            raise Interrupted()
        if self.app.pace and isinstance(event, Note) and event.level == "lead":
            # Set-up's own lines are written, not generated: a short, varied pause (with
            # the thinking glyph showing) keeps the conversation's rhythm.
            time.sleep(random.uniform(*PACE_SECONDS))
        self.app.call_from_thread(self.app.show_event, event)
        self._check()

    def ask(self, f: Field) -> Any:
        self._check()
        pending = Pending(f)
        self.app.call_from_thread(self.app.begin_ask, pending)
        while not pending.done.wait(0.25):
            if not self.app.is_running:  # the app has closed: nothing will answer
                raise Interrupted()
        if pending.error:
            raise pending.error
        return pending.value

    def edit(self, paths: list[Path]) -> None:
        if not self.app.is_running:
            raise Interrupted()
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
        chart_w = max(len(x) for x in look.chart_from_summary(s)) + 2
        stats_w = max(len(x) for x in look.stats_lines(s)) + 1
        from rich.align import Align

        text = Table.grid(padding=(0, 2))
        text.add_column(width=chart_w)
        text.add_column(width=stats_w)
        text.add_row(chart, stats)
        # Wide: the logo and the two text columns share the width half and half, each
        # centred in its half, when both fit there whole.
        half = (width - 2) // 2
        show_logo = which == "wide" and half >= max(look.LOGO_WIDTH + 2, chart_w + stats_w + 2)
        if show_logo:
            grid = Table.grid(expand=True, padding=(0, 2))
            grid.add_column(ratio=1, no_wrap=True)
            grid.add_column(ratio=1)
            grid.add_row(Align.center(look.logo_text(app.dark), vertical="middle"),
                         Align.center(text, vertical="middle"))
        else:
            grid = text
        return Group(rule, Text(""), grid, Text(""), Text("─" * width, style=p.dim))


NEWLINE_KEYS = ("shift+enter", "alt+enter", "ctrl+j")  # a new line in the input; Enter sends


class PromptArea(TextArea):
    """The input box: wraps, grows to a few lines, and sends on Enter. Shift+Return
    (or Option+Return, Ctrl+J, or a backslash then Return) starts a new line. A
    pasted paragraph keeps its line breaks."""

    class Submitted(Message):
        def __init__(self, area: "PromptArea", text: str):
            super().__init__()
            self.area = area
            self.text = text

        @property
        def control(self) -> "PromptArea":
            return self.area

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        menu_open = getattr(app, "slash_open", lambda: False)()
        if menu_open and event.key in ("up", "down", "tab", "escape"):
            event.stop()
            event.prevent_default()
            app.slash_key(event.key)
            return
        if event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if self.text.endswith("\\") and self.cursor_location == self.document.end:
                # a backslash before Enter starts a new line, for terminals that
                # send Shift+Return as a plain Return
                self.replace("\n", (self.cursor_location[0], self.cursor_location[1] - 1), self.cursor_location)
                return
            self.post_message(self.Submitted(self, self.text))
            return
        await super()._on_key(event)

    @property
    def value(self) -> str:  # the same name as Input's, for the rest of the app
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text
        self.move_cursor(self.document.end)


class Line(Static):
    DEFAULT_CSS = "Line { height: auto; margin: 1 0 0 0; }"

    def __init__(self, text: Text, **kw: Any):
        super().__init__(text, **kw)
        self.text = text

    def retheme(self, old: look.Palette, new: look.Palette) -> None:
        self.text = look.recolour(self.text, old, new)
        self.update(self.text)


class MdLine(Horizontal):
    """A line written in Markdown (the Lead's words, a report): headings, bold, lists,
    quotes, code and links, in the theme's colours. `source` keeps the text."""

    DEFAULT_CSS = """
    MdLine { height: auto; margin: 1 0 0 0; padding: 0 1; }
    MdLine > .mark { width: 2; color: $ombro-muted; }
    MdLine > Markdown { width: 1fr; height: auto; margin: 0; padding: 0; background: $ombro-bg;
                        color: $ombro-text; }
    MdLine MarkdownBlock { color: $ombro-text; }
    MdLine MarkdownParagraph { margin: 0 0 1 0; }
    MdLine MarkdownHeader { margin: 1 0 0 0; padding: 0; content-align: left top; text-align: left; }
    MdLine MarkdownBlockQuote { background: $ombro-bg; border-left: wide $ombro-dim; margin: 0 0 1 0; }
    MdLine MarkdownFence { background: $ombro-bg; margin: 0 0 1 0; }
    MdLine MarkdownBulletList, MdLine MarkdownOrderedList { margin: 0 0 1 0; }
    MdLine MarkdownHorizontalRule { border-bottom: solid $ombro-dim; margin: 0 0 1 0; }
    MdLine MarkdownBlock:last-child { margin-bottom: 0; }
    MdLine Markdown > MarkdownBlock:first-child { margin-top: 0; }
    """

    def __init__(self, source: str, kind: str, mark: str = ""):
        super().__init__(classes=kind)
        self.source = source
        self.mark = mark

    def compose(self) -> ComposeResult:
        if self.mark:
            yield Static(self.mark, classes="mark")
        yield Markdown(self.source, open_links=False)

    def on_markdown_table_of_contents_updated(self, event: Markdown.TableOfContentsUpdated) -> None:
        """The Markdown has been laid out, so it has its full height: bring it into view."""
        self.call_after_refresh(self.app.show_line, self)


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
    .report { color: $ombro-text; padding: 0 1; border-left: wide $ombro-dim; }
    .notice { color: $ombro-text; padding: 0 1; border-left: wide $ombro-muted; }
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
    #entry { height: auto; }
    #chevron { width: 2; color: $ombro-text; }
    #input, #input:focus { border: none; height: auto; max-height: 6; min-height: 1; padding: 0;
                           background: $ombro-bg; color: $ombro-text; background-tint: $ombro-bg 0%;
                           scrollbar-size-vertical: 1; scrollbar-color: $ombro-dim;
                           scrollbar-background: $ombro-bg; }
    #input > .text-area--placeholder { color: $ombro-dim; }
    #input > .text-area--cursor { background: $ombro-muted; color: $ombro-bg; }
    #input > .text-area--cursor-line { background: $ombro-bg; }
    #input > .text-area--selection { background: $ombro-band; }
    #secret, #secret:focus { border: none; height: 1; padding: 0; background: $ombro-bg; color: $ombro-text;
                             background-tint: $ombro-bg 0%; display: none; }
    #secret > .input--placeholder { color: $ombro-dim; }
    #secret > .input--cursor { background: $ombro-muted; color: $ombro-bg; }
    #thinking { height: 1; padding: 0 1; margin: 1 0 0 0; display: none; }
    #footer { height: 1; margin: 0 0 1 0; }
    #footer-left { width: 1fr; color: $ombro-muted; }
    #footer-right { width: auto; color: $ombro-muted; }
    """

    BINDINGS = [Binding("ctrl+c", "interrupt", "Stop or leave", priority=True, show=False)]

    def __init__(self, home: Home, *, dark: bool, client_factory=None, poll: bool = True, pace: bool = False,
                 titles: bool = True):
        self.dark = dark
        self.pace = pace
        self.titles = titles  # chats are named after their first exchange
        self.thinking_frame = 0
        self.colours = look.palette(dark)  # before Textual reads the CSS variables
        super().__init__()
        self.home = home
        self.poll = poll
        self.init_client_factory = (lambda spec, h: client_factory(spec)) if client_factory else None
        self.watcher = JobWatcher(home)
        self.client_factory = client_factory
        self.summary = views.summary(home)
        self.first_opening = not any((home.chats_dir).glob("*.md")) and not self.summary["unseen"] \
            and not views.job_list(home)
        self.mode = "Chat"
        try:
            from ombro.settings import load_limits

            self.usage = (0, load_limits(home).for_chat().max_tokens)
        except Exception:  # noqa: BLE001
            self.usage = (0, None)
        self.bridge = TuiUI(self)
        self.session = Session(home, self.bridge, client_factory=client_factory, titles=titles,
                               start_job=lambda spec, roles: self.commands.start_job(spec, roles),
                               on_started=self._follow_started)
        from ombro.tui.commands import Commands

        self.commands = Commands(self)
        self.pending: Pending | None = None
        self.busy = False
        self._quit_armed = 0.0
        self.setup_needed = setup_needed(home)

    # -- the session and the jobs it follows ---------------------------------

    @property
    def chat(self):
        return self.session.chat

    @chat.setter
    def chat(self, value) -> None:
        self.session.chat = value

    @property
    def active_jobs(self) -> set[str]:
        return self.watcher.active

    @active_jobs.setter
    def active_jobs(self, value: set[str]) -> None:
        self.watcher.active = set(value)

    @property
    def following(self) -> dict[str, Any]:
        return self.watcher.following

    def background_feed(self) -> bool:
        """Whether jobs' progress lines show in the conversation (a preference, on unless turned off)."""
        from ombro.settings import load_preferences

        try:
            return load_preferences(self.home).background_feed
        except Exception:  # noqa: BLE001 - a preferences file being written
            return True

    def _follow_started(self, job_id: str) -> None:
        from ombro.journal import Follower

        self.following[job_id] = Follower(jobs.find_job(self.home, job_id) / "journal.jsonl")
        self.active_jobs.add(job_id)

    # -- colours -------------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        p = self.colours
        headings = {f"markdown-h{n}-{k}": v for n in range(1, 7)
                    for k, v in (("color", p.strong), ("background", "transparent"), ("text-style", "bold"))}
        return {**super().get_css_variables(), **headings, "ombro-bg": p.background, "ombro-text": p.text,
                "ombro-muted": p.muted, "ombro-dim": p.dim, "ombro-band": p.band, "ombro-band-text": p.band_text}

    def set_dark(self, dark: bool) -> None:
        old = self.colours
        self.dark = dark
        self.colours = look.palette(dark)
        if old != self.colours:
            for line in self.query(Line):
                line.retheme(old, self.colours)
        self.refresh_css()
        self.query_one(HeaderBlock).refresh()
        self.update_footer()

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="conversation"):
            yield HeaderBlock(self)
            yield Static("", id="spacer")
            yield Vertical(id="messages")
        yield Static("", id="thinking")
        yield Static("", classes="rule", id="slash-rule")
        yield OptionList(id="slash")
        yield Static("", classes="rule", id="rule-above")
        with Horizontal(id="entry"):
            yield Static(">", id="chevron")
            yield PromptArea(id="input", placeholder="Chat or /brief", soft_wrap=True, show_line_numbers=False,
                             tab_behavior="focus", compact=True)
            yield Input(id="secret", password=True, placeholder="Hidden as you type")
        yield Static("", classes="rule", id="rule-below")
        with Horizontal(id="footer"):
            yield Static("", id="footer-left")
            yield Static("", id="footer-right")

    def on_mount(self) -> None:
        self.query_one("#messages").styles.height = "auto"
        self.set_interval(THINKING_SECONDS, self.tick_thinking)
        self.draw_rules()
        self.update_footer()
        if self.setup_needed:
            self.start_work(self._set_up)
        else:
            self.greet()
        self.watcher.start()
        if self.poll:
            self.set_interval(POLL_SECONDS, self.poll_jobs)
        self.query_one("#input", PromptArea).focus()

    def greet(self) -> None:
        self.add_line(LeadSaid(look.greeting(self.summary["name"])), "lead")
        since = look.since_line(views.since_last(self.home))
        if since:
            self.add_line(Note(since), "note")

    def _set_up(self) -> None:
        self.commands.setup()
        self.setup_needed = setup_needed(self.home)
        self.call_from_thread(self._after_set_up)

    def _after_set_up(self) -> None:
        self.refresh_summary()
        if self.setup_needed:
            self.add_line(Note(f"Set-up is not finished: {self.setup_needed} Type /setup to try again."), "note")
        else:
            self.greet()

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

    def tick_thinking(self) -> None:
        """The thinking glyph: one cell that turns while the core works and nothing is
        being asked, so a pause reads as work rather than a hang."""
        try:
            box = self.query_one("#thinking", Static)
        except Exception:  # noqa: BLE001 - the screen is closing
            return
        on = self.busy and self.pending is None
        if on:
            self.thinking_frame = (self.thinking_frame + 1) % len(THINKING_FRAMES)
            p = self.colours
            box.update(Text(THINKING_FRAMES[self.thinking_frame], style=p.strong) + Text("  Thinking", style=p.dim))
        if box.display != on:
            box.display = on
            self.call_after_refresh(self.fit_spacer)

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
        self.query_one("#input", PromptArea).placeholder = placeholder

    # -- the conversation ------------------------------------------------------

    def add_markdown(self, source: str, kind: str, mark: str = "") -> None:
        self.query_one("#messages").mount(MdLine(source, kind, mark))
        # A report is brought into view at its top once laid out (MdLine.show_line), not scrolled past.
        self.call_after_refresh(self.fit_spacer if kind == "report" else self._after_line)

    def show_line(self, line: Widget) -> None:
        """Once a Markdown line has its full height: a report opens at its top, so you start
        reading at the title; anything else keeps the conversation at its end."""
        self.fit_spacer()
        conv = self.query_one("#conversation")
        if line.has_class("report"):
            conv.scroll_to_widget(line, top=True, animate=False)
        else:
            conv.scroll_end(animate=False)

    def action_run_command(self, command: str) -> None:
        """A click on a command in Ombro's own lines, such as "Read the report"."""
        if self.busy:
            self.add_line(Note("Still working on the last thing; try again in a moment."), "note")
            return
        self.add_line(Note(command), "you")
        self.handle(command)

    @on(Markdown.LinkClicked)
    def link_clicked(self, event: Markdown.LinkClicked) -> None:
        """A link in a report or a Lead line: a file opens; an `ombro:` link runs a command."""
        href = event.href
        if href.startswith("ombro:"):
            from urllib.parse import unquote

            self.action_run_command("/" + unquote(href.removeprefix("ombro:")).replace("/", " ").strip())
        elif href.startswith(("file:", "http:", "https:")):
            from ombro.cli import _launch

            if href.startswith("file:"):
                from urllib.parse import unquote, urlparse

                _launch(Path(unquote(urlparse(href).path)))
            else:
                self.open_url(href)

    def add_line(self, event: Event, kind: str) -> None:
        if isinstance(event, LeadSaid):
            self.add_markdown(event.text, "lead", "• ")
            return
        text = render_event(event, self.colours)
        if text is None:
            return
        self.add_text(text, kind)

    def add_text(self, text: Text, kind: str) -> None:
        box = self.query_one("#messages")
        box.mount(Line(text, classes=kind))
        self.call_after_refresh(self._after_line)

    def _after_line(self) -> None:
        self.fit_spacer()
        self.call_after_refresh(self.query_one("#conversation").scroll_end, animate=False)

    def show_event(self, event: Event) -> None:
        if isinstance(event, ChatCleared):
            self.query_one("#messages").remove_children()
            self.call_after_refresh(self.fit_spacer)
            return
        if isinstance(event, PastTurn):  # a reopened chat, shown again
            if event.who == "you":
                self.add_line(Note(event.text), "you")
            else:
                self.add_markdown(event.text, "lead", "• ")
            return
        if isinstance(event, Usage):
            self.usage = (event.total_tokens, event.max_tokens)
            self.update_footer()
            return
        if isinstance(event, JobStarted):
            self.add_line(Note(event.text, level="done"), "note")
            return
        if isinstance(event, JobProgress):
            if event.live:  # a live job's steps always show, in the chat that follows it
                self.add_line(Progress(f"Live: {event.text}"), "progress")
            elif self.background_feed():
                self.add_line(Progress(event.text), "progress")
            return
        if isinstance(event, JobEnded):
            from ombro.session import record_job_end_in_chat
            from ombro.tui.commands import render_notice

            self.add_text(render_notice(event, self), "notice")
            # Its outcome becomes part of the chat that started it: this one, or that chat's file.
            if not self.session.job_ended(event):
                record_job_end_in_chat(self.home, event)
            return
        if isinstance(event, ModeChanged):
            self.mode = event.mode
            self.update_footer()
            if event.text:
                self.add_line(Note(event.text.strip("─ ").strip()), "note")
            return
        kind = {LeadSaid: "lead", Progress: "progress", Failure: "error", ProposalShown: "proposal"}.get(
            type(event), "note")
        if isinstance(event, Note) and event.level == "lead":
            event, kind = LeadSaid(event.text), "lead"  # set-up's own lines, in the Lead's voice
        self.add_line(event, kind)

    def begin_ask(self, pending: Pending) -> None:
        f = pending.field
        self.pending = pending
        if f.kind in ("choice", "yesno") or (f.label and f.label not in ("You", "answer")):
            # The question in the Lead's voice, as the app shows it; the choices and hints under it.
            whole = field_prompt(f)
            if f.label and whole.startswith(f.label):
                self.add_line(LeadSaid(f.label), "lead")
                rest = whole[len(f.label):].lstrip(":").strip("\n")
                if rest.strip():
                    self.add_line(Note(rest.strip() if not rest.startswith("  ") else rest), "note")
            else:
                self.add_line(Note(whole), "note")
        self.show_secret(f.kind == "secret")

    def show_secret(self, on: bool) -> None:
        """A secret is typed into its own hidden field, never into the input box."""
        prompt, secret = self.query_one("#input", PromptArea), self.query_one("#secret", Input)
        prompt.display = not on
        secret.display = on
        if not on:
            secret.value = ""
        (secret if on else prompt).focus()

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

    @on(TextArea.Changed, "#input")
    def typed(self, event: TextArea.Changed) -> None:
        menu = self.query_one("#slash", OptionList)
        value = event.text_area.text
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

    def slash_open(self) -> bool:
        return bool(self.query_one("#slash", OptionList).display)

    def slash_key(self, key: str) -> None:
        menu = self.query_one("#slash", OptionList)
        if key in ("down", "up"):
            menu.action_cursor_down() if key == "down" else menu.action_cursor_up()
        elif key == "escape":
            menu.display = False
            self.query_one("#slash-rule").display = False
        elif key == "tab":
            self._take_slash()

    def _take_slash(self) -> str | None:
        menu = self.query_one("#slash", OptionList)
        if not menu.display or menu.highlighted is None:
            return None
        cmd = menu.get_option_at_index(menu.highlighted).id
        inp = self.query_one("#input", PromptArea)
        inp.value = cmd + " "
        menu.display = False
        self.query_one("#slash-rule").display = False
        return cmd

    @on(Input.Submitted, "#secret")
    def secret_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        self.query_one("#secret", Input).value = ""
        if self.pending is not None:
            self._answer(text)

    @on(PromptArea.Submitted, "#input")
    def submitted(self, event: PromptArea.Submitted) -> None:
        inp = self.query_one("#input", PromptArea)
        text = event.text
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
        if sets_aside(f, text):
            # A new message, not an answer: the question goes unanswered and the Lead gets this next.
            self.pending = None
            self.session.send_next(text.strip())
            pending.error = SetAside(f)
            pending.done.set()
            return
        value, problem = parse_answer(f, text)
        if problem:
            self.add_line(Note(problem), "note")
            return
        self.pending = None
        self.show_secret(False)
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
        if word == "/setup":
            self.start_work(self._set_up)
            return
        if self.setup_needed:
            self.add_line(Note("Ombro is not set up yet. Type /setup to set it up."), "note")
            return
        if word in TUI_COMMANDS:
            rest = text[len(word):].strip()
            self.start_work(lambda: self.commands.run(word, rest))
            return
        self.start_work(lambda: self._chat_turn(text))

    def start_work(self, fn) -> None:
        """Run core work on a worker thread, one piece at a time."""
        if self.busy:
            self.add_line(Note("Still working on the last thing; wait for it, or Ctrl+C to stop it."), "note")
            return
        self.busy = True

        def work() -> None:
            try:
                fn()
            except KeyboardInterrupt:
                self.session.undo_unanswered()
                if self.is_running:
                    self.call_from_thread(self.add_line, Note("Stopped."), "note")
            except Exception as e:  # noqa: BLE001 - shown, never a crash
                if self.is_running:
                    self.call_from_thread(self.add_line, Failure(f"Something went wrong: {e}"), "error")
            finally:
                self.busy = False
                self.bridge.stop_requested = False
                if self.is_running:
                    try:
                        self.call_from_thread(self.refresh_summary)
                    except RuntimeError:
                        pass

        self.run_worker(work, thread=True, exclusive=False)

    def _chat_turn(self, text: str) -> None:
        self.session.ensure_chat().take_paths = True  # a file dragged into the terminal is attached
        if not self.session.chat_turn(text):
            self.call_from_thread(self.leave)

    def offer_start(self, spec: str, shown: str | None = None, *, roles: list[str] | None = None,
                    ask: bool = True) -> None:
        self.session.offer_start(spec, shown, roles=roles, ask=ask)

    # -- jobs: progress, notices and the header's counts --------------------------

    def poll_jobs(self) -> None:
        self.run_worker(self._poll, thread=True, exclusive=True, group="poll")

    def _poll(self) -> None:
        polled = self.watcher.poll()
        if polled is None:
            return
        events, changed = polled
        for event in events:
            self.call_from_thread(self.show_event, event)
        if changed and self.is_running:
            try:
                self.call_from_thread(self.refresh_summary)
            except RuntimeError:  # closed in the meantime
                pass

    def refresh_summary(self) -> None:
        from textual.css.query import NoMatches

        self.summary = views.summary(self.home)
        try:
            self.query_one(HeaderBlock).refresh()
            self.update_footer()
        except NoMatches:  # the app is closing
            pass

    def action_interrupt(self) -> None:
        inp = self.query_one("#input", PromptArea)
        if self.pending is not None:
            pending, self.pending = self.pending, None
            self.show_secret(False)
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
        self.session.save()
        self.exit(0)


# ---------------------------------------------------------------------------
# Rendering events as lines


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
            out.append(f"{f.label} ({'updates the current file' if f.replaces else 'new file'})\n",
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


def run_app(home: Home | None = None) -> int:
    from ombro.settings import load_preferences
    from ombro.tui.detect import is_dark

    home = home or Home()
    if home.root.is_dir() and home.old_layout():
        print("ombro: This home was made by an earlier Ombro. Move it aside and run `ombro init`.")
        return 2
    if home.root.is_dir():
        from ombro.home import tidy_layout

        for line in tidy_layout(home):
            print(f"ombro: {line}")
    try:
        theme = load_preferences(home).theme if home.root.is_dir() else "auto"
    except Exception:  # noqa: BLE001
        theme = "auto"
    OmbroApp(home, dark=is_dark(theme), pace=True).run()
    return 0
