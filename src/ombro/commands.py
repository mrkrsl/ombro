"""The slash commands every interface offers, and where each one is handled.

The terminal and graphical interfaces list the same commands from here. `where`
says who answers one typed in a conversation:

- chat: the chat itself (ombro.chat.Chat)
- session: the conversation's session, around the chat (ombro.session)
- flow: a step-by-step flow run in the conversation (ombro.flows)
- screen: shown on a page of its own in the graphical interface; the terminal
  prints it as a list
- app: the interface itself (theme, help, quit)

A screen command typed with more after it (`/approvals approve 3`) is a flow:
it acts, rather than shows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Command:
    name: str  # with its slash: /brief
    help: str
    where: str  # chat, session, flow, screen, app
    screen: str = ""  # for a screen command: which page (jobs, report, approvals, scheduled, settings/...)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


COMMANDS = [
    Command("/chat", "back to Chat from a brief", "chat"),
    Command("/new", "save this chat and start a fresh one", "chat"),
    Command("/chats", "your past chats; reopen one and carry on", "chat"),
    Command("/rename", "give this chat a title", "chat"),
    Command("/brief", "brief a task: the Lead interviews you, and it's reported back", "chat"),
    Command("/live", "do it live, here: the crew starts now and works in this chat", "chat"),
    Command("/start", "start a saved spec again", "session"),
    Command("/jobs", "jobs and their status", "screen", "jobs"),
    Command("/report", "a job's report and its deliverables", "screen", "report"),
    Command("/stop", "stop a job; it writes its report first", "session"),
    Command("/open", "open a job's workspace folder", "screen", "open"),
    Command("/approvals", "what waits for your approval", "screen", "approvals"),
    Command("/schedule", "scheduled jobs", "screen", "scheduled"),
    Command("/crew", "crews and their roles", "screen", "settings/crews"),
    Command("/role", "add or edit a role", "flow"),
    Command("/model", "which model the Lead and each role use", "flow"),
    Command("/models", "your model connections and their models", "screen", "settings/general"),
    Command("/keys", "which keys are set", "screen", "settings/general"),
    Command("/folder", "folders shared with jobs and the Lead: list, add, remove", "screen", "settings/folders"),
    Command("/tools", "tools and their permissions", "screen", "settings/tools"),
    Command("/skills", "the skills library, who uses each, and proposals waiting", "screen", "settings/skills"),
    Command("/remember", "add a lesson to a crew's notes", "chat"),
    Command("/limits", "how far jobs, chats and interviews may go", "screen", "settings/general"),
    Command("/preferences", "your name, theme and defaults", "screen", "settings/general"),
    Command("/theme", "switch between light and dark", "app"),
    Command("/help", "what each command does", "app"),
    Command("/quit", "leave Ombro", "app"),
]

# Typed but not listed: other names for listed commands, and /setup.
ALIASES = {"/folders": "/folder", "/q": "/quit", "/exit": "/quit"}
UNLISTED = [Command("/setup", "set Ombro up again", "flow")]

_BY_NAME = {c.name: c for c in COMMANDS + UNLISTED}


def find(word: str) -> Command | None:
    """The command a typed word names, following aliases; None if it isn't one."""
    word = word.strip().split(" ", 1)[0].lower()
    return _BY_NAME.get(ALIASES.get(word, word))


def matching(text: str) -> list[Command]:
    """The listed commands that start with what has been typed after the /."""
    word = text.strip().split(" ", 1)[0].lower()
    return [c for c in COMMANDS if c.name.startswith(word)]


def route(text: str) -> tuple[str, Command | None]:
    """Where a line typed in a conversation goes: chat (a message, or a chat command), session,
    flow, screen or app. A screen command with more after it acts, so it is a flow."""
    if not text.startswith("/"):
        return "chat", None
    cmd = find(text)
    if cmd is None:
        return "chat", None  # the chat says it doesn't know it
    rest = text.strip().partition(" ")[2].strip()
    if cmd.where == "screen" and rest and cmd.screen not in ("report", "open"):
        return "flow", cmd
    return cmd.where, cmd
