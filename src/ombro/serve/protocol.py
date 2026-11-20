"""The messages `ombro serve` sends on its socket and the shapes of its replies, as
TypedDicts. Like viewtypes.py they change nothing at run time; the desktop app's TypeScript
types are generated from them.

Server to client, each with a `type`:
- event: anything the core shows, in a session (`session`, `seq`) or not (the hello)
- question / question_closed: the core asks, and the question is answered or withdrawn
- edit_files: an interview wants files edited by hand; answered like a question once saved
- session: a session's state (working, waiting, idle, ended), for the thinking indicator
- job: progress from any job's journal, and its end
- counts: what the sidebar and tray show

Client to server: subscribe.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict  # as in viewtypes.py: pydantic refuses typing's before 3.12

from ombro import ui
from ombro import viewtypes as vt

# What a core event's to_dict() can be. Each also carries its `type` (the class's `kind`).
CoreEvent = (ui.LeadSaid | ui.Progress | ui.Note | ui.Failure | ui.ProposalShown | ui.Usage | ui.ModeChanged
             | ui.PastTurn | ui.ChatCleared | ui.JobStarted | ui.JobProgress | ui.JobEnded | ui.OpenPage)
FieldDict = ui.Field  # a field's to_dict()


class EventMessage(TypedDict):
    type: Literal["event"]
    session: str | None
    seq: int
    event: CoreEvent


class QuestionMessage(TypedDict):
    type: Literal["question"]
    session: str
    seq: int
    id: str
    field: FieldDict
    prompt: str  # the field as a line of conversation


class QuestionClosedMessage(TypedDict):
    type: Literal["question_closed"]
    session: str
    seq: int
    id: str


class EditFilesMessage(TypedDict):
    type: Literal["edit_files"]
    session: str
    seq: int
    id: str
    paths: list[str]


class SessionMessage(TypedDict):
    type: Literal["session"]
    session: str
    seq: int
    state: Literal["working", "waiting", "idle", "ended"]
    kind: str


class ChatSavedMessage(TypedDict):
    """A session's chat was written for the first time, or renamed with its title: list it now."""
    type: Literal["chat_saved"]
    session: str
    seq: int
    chat: str  # the chat's file name, without .md


class JobMessage(TypedDict):
    type: Literal["job"]
    job_id: str
    name: str
    status: str  # running, then how it ended
    line: str
    event: ui.JobProgress | ui.JobEnded


class CountsMessage(TypedDict):
    type: Literal["counts"]
    active: int
    running: list[str]  # the active jobs, by id: the sidebar's ring shows from the moment one starts
    scheduled: int
    waiting: int
    unseen: int
    crews_ready: int


ServerMessage = (EventMessage | QuestionMessage | QuestionClosedMessage | EditFilesMessage | SessionMessage
                 | ChatSavedMessage | JobMessage | CountsMessage)


class SubscribeMessage(TypedDict, total=False):
    type: Literal["subscribe"]
    sessions: dict[str, int]  # session id: the last sequence number seen
    all: bool  # every session, as it is opened


# -- replies -------------------------------------------------------------------------


class OpenQuestion(TypedDict):
    id: str
    kind: Literal["question", "edit_files"]
    field: FieldDict
    prompt: str
    paths: list[str]


class SessionView(TypedDict):
    id: str
    kind: str  # chat, setup, role.add
    state: str
    seq: int
    chat: str | None  # the chat's file name, once saved
    chat_id: str | None
    title: str | None
    mode: str | None  # Chat or Brief
    jobs: list[str]
    question: OpenQuestion | None


class Route(TypedDict):
    where: Literal["screen", "app"]
    command: str
    screen: str
    args: str


class InputReply(TypedDict, total=False):
    handled: bool
    route: Route


class AnswerReply(TypedDict):
    ok: bool
    problem: str | None


class CommandView(TypedDict):
    name: str
    help: str
    where: str  # chat, session, flow, screen, app
    screen: str


class Api(TypedDict):
    """Every type the app uses, gathered so one schema holds them all."""

    server_message: ServerMessage
    subscribe: SubscribeMessage
    session: SessionView
    input_reply: InputReply
    answer_reply: AnswerReply
    command: CommandView
    summary: vt.Summary
    crews: vt.Crews
    role: vt.RoleDetail
    job: vt.JobRow
    job_status: vt.JobStatus
    report: vt.Report
    brief: vt.Brief
    chat: vt.ChatRow
    chat_detail: vt.ChatDetail
    approval: vt.ApprovalRow
    approval_detail: vt.ApprovalDetail
    keys: vt.KeyList
    schedule: vt.ScheduleRow
    scheduler: vt.SchedulerState
    spec: vt.SpecRow
    folders: vt.FolderList
    preferences: vt.Preferences
    tools: vt.ToolList
    browser: vt.BrowserView
    models: vt.ModelList
    since_last: vt.SinceLast
