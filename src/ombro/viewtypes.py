"""The shapes of what views.py returns (plan C3), as TypedDicts.

They change nothing at run time: views still return plain dicts. They say what
each dict holds, so a type checker can hold the views to it, and so the
desktop app's TypeScript types can be generated from them (gui/scripts/schema.py).
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

# -- the crew ----------------------------------------------------------------------


class LeadView(TypedDict):
    name: str
    title: str
    model: str | None
    path: str
    problem: NotRequired[str]


class RoleRow(TypedDict):
    """A role in a crew. One that can't be loaded has its name, title and problem only."""

    name: str
    title: str
    kind: NotRequired[str]
    model: NotRequired[str]
    tools: NotRequired[list[str]]
    skills: NotRequired[list[str]]
    description: NotRequired[str]
    problem: NotRequired[str]


class CrewRow(TypedDict):
    name: str
    title: str
    description: str
    default: bool
    roles: list[RoleRow]


class Crews(TypedDict):
    lead: LeadView
    default_crew: str
    crews: list[CrewRow]


class RoleDetail(TypedDict):
    name: str
    crew: str  # empty for the Lead
    title: str
    kind: str
    model: str
    description: str
    tools: list[str]
    skills: list[str]
    all_skills: bool
    soul: str
    path: str


# -- jobs --------------------------------------------------------------------------


class JobRow(TypedDict):
    job_id: str
    name: str  # readable, from the job id
    crews: list[str]
    status: str | None
    active: bool
    brief: str
    source: str
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    limit_hit: str | None
    total_tokens: int
    waiting: int
    seen_at: str | None
    path: str
    chat: str | None  # the id of the chat that started it


class JobStatus(JobRow):
    pid: int | None
    allowance: str
    usage: dict[str, Any]
    interview_usage: dict[str, Any]
    limits: dict[str, Any]
    outbox: dict[str, int]
    outward: dict[str, Any]
    folders: list[Any]
    tool_names: list[str]


class Deliverable(TypedDict):
    name: str
    role: str
    path: str
    url: str  # file://
    exists: bool
    kind: str  # deliverable, created, changed, other


class Report(JobRow):
    report_path: str
    report: str | None
    deliverables: list[Deliverable]
    workspace: str


# -- chats -------------------------------------------------------------------------


class ChatRow(TypedDict):
    name: str  # the file's name, without .md
    title: str
    path: str
    updated_at: str
    when: str
    id: str | None
    jobs: list[str]


class ChatTurn(TypedDict):
    who: str  # you or lead
    text: str


class ChatDetail(ChatRow):
    turns: list[ChatTurn]


# -- approvals ---------------------------------------------------------------------

ApprovalRow = TypedDict("ApprovalRow", {
    "ref": str, "crews": list[str], "long_ref": str, "job_id": str, "job_name": str, "status": str, "tool": str, "summary": str,
    "role": str, "reason": str, "class": str, "edited": bool, "arguments": Any,
})


class ApprovalDetail(ApprovalRow):
    skill: dict[str, Any] | None


# -- settings ----------------------------------------------------------------------


class KeyRow(TypedDict):
    name: str
    set: bool
    source: str | None  # keys.env or environment
    used_by: list[str]


class SignIn(TypedDict):
    server: str
    status: str


class KeyList(TypedDict):
    keys: list[KeyRow]
    sign_ins: list[SignIn]
    file: str


class LastJob(TypedDict):
    job_id: str
    due: str | None
    status: str | None
    waiting: int


class ScheduleRow(TypedDict):
    name: str
    enabled: bool
    cron: str
    spec: str
    crews: list[str]
    when: str  # in words where simple: "Runs weekdays at 6am"
    spec_title: str
    next: str | None
    last_job: LastJob | None
    problem: str | None
    path: str


class SpecRow(TypedDict):
    name: str
    path: str
    modified: str
    crews: list[str]
    title: str


class FolderRow(TypedDict):
    name: str
    path: str
    access: str  # read or write
    backup: str | None
    problem: str | None


class FolderList(TypedDict):
    folders: list[FolderRow]
    declined: list[str]
    file: str


class Preferences(TypedDict):
    name: str
    theme: str
    default_crew: str
    default_crew_title: str
    editor: str
    outward: str
    instructions_set: bool
    instructions: str  # the profile, as the Lead is given it
    background_feed: bool
    notifications: bool
    files: dict[str, str]


class NativeTool(TypedDict):
    tool: str
    provider: str | None
    max_calls: int | None  # as set in tools.md
    calls: int | None  # the calls a job may make, set or not


class ServerRow(TypedDict):
    server: str
    command: str | None
    args: list[str]
    url: str | None
    secrets: list[str]  # the names of its secret headers or environment values; never a value
    sign_in: str | None  # a remote server's sign-in, in words; None for a local one
    signing_in: bool  # a sign-in started from the app is waiting for the browser
    problem: str | None  # why the last sign-in from the app failed


class ToolList(TypedDict):
    native: list[NativeTool]
    servers: list[ServerRow]
    sandbox: str | None  # why code.run can't work here, or None
    folders: list[str]


class ModelEntry(TypedDict):
    name: str
    model: str
    default: bool
    used_by: list[str]


class Connection(TypedDict):
    api: str
    label: str  # the provider's name for people: DeepSeek, Local, or its host
    endpoint: str | None
    key: str | None  # the key's name, never its value
    models: list[ModelEntry]


class ModelList(TypedDict):
    connections: list[Connection]
    default: str | None


# -- the summary -------------------------------------------------------------------


class Counts(TypedDict):
    crews: int
    crews_ready: int
    active_jobs: int
    scheduled_jobs: int
    waiting: int
    unseen_jobs: int


class UnseenJob(TypedDict):
    job_id: str
    status: str | None
    brief: str
    finished_at: str | None
    waiting: int


class Summary(TypedDict):
    name: str
    version: str
    model: str | None
    outward: str
    lead: LeadView
    crews: list[CrewRow]
    counts: Counts
    unseen: list[UnseenJob]


class SinceLast(TypedDict):
    finished: int
    waiting: int
    jobs: list[UnseenJob]
