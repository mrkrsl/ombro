"""The shapes of what views.py returns, as TypedDicts.

They change nothing at run time: views still return plain dicts. They say what
each dict holds, so a type checker can hold the views to it, and so the
desktop app's TypeScript types can be generated from them (gui/scripts/schema.py).
"""

from __future__ import annotations

from typing import Any, NotRequired

# Not typing's: pydantic (which reads these for the app's schema) refuses typing.TypedDict
# before Python 3.12, and Ombro supports 3.11.
from typing_extensions import TypedDict

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
    live: bool  # a live job: started from a chat and followed there
    cleared_at: str | None  # cleared from the app's Jobs list (hidden, not deleted)


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
    text: NotRequired[str]  # a Markdown file's contents, to read in the app (up to MARKDOWN_SHOWN bytes)


class Brief(JobRow):
    spec: str  # the brief the job ran from, as Markdown


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
    pinned: bool  # kept at the top of the list


class ChatTurn(TypedDict):
    who: str  # you or lead
    text: str


class ChatDetail(ChatRow):
    turns: list[ChatTurn]


# -- approvals ---------------------------------------------------------------------

ApprovalRow = TypedDict(
    "ApprovalRow",
    {
        "ref": str,
        "crews": list[str],
        "long_ref": str,
        "job_id": str,
        "job_name": str,
        "status": str,
        "tool": str,
        "summary": str,
        "role": str,
        "reason": str,
        "class": str,
        "edited": bool,
        "arguments": Any,
    },
)


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


class SchedulerState(TypedDict):
    """Whether scheduled jobs can run: the scheduler service (scheduler.check)."""

    ok: bool
    message: str  # why not, for the Scheduled page; empty when ok
    can_install: bool  # Install now would put it right


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
    thinking: str  # off, low, medium, high or xhigh: what models use unless models.md gives their own
    thinking_set: bool  # false while it is the built-in default
    files: dict[str, str]


class InstalledBrowser(TypedDict):
    name: str  # "Google Chrome"
    path: str


class BrowserView(TypedDict):
    """Settings › General › Browser: the choice, and what each would be."""

    choice: str  # "auto", "off", or the path of one installed browser
    using: str | None  # what jobs and chat use now ("Google Chrome", "the browser container"), or None
    installed: list[InstalledBrowser]  # on the machine Ombro runs on
    endpoint: str | None  # a browser elsewhere (the Docker container), if one is set
    problem: str | None  # why there is none now, in words


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


class ApiRow(TypedDict):
    """A web API: its address and what it may do. Its key by name only, and whether it is set."""

    name: str
    url: str
    about: str
    how_to: str
    access: str  # read-only, internal or external: what its changes are
    get_looks_up: bool  # whether its GET requests only look things up; a no gives them its class
    send_key: str  # bearer, header:<Name> or query:<param>
    key: str | None
    key_set: bool
    calls: int


class ComposioToolkit(TypedDict):
    """A service connected in the user's Composio account, such as Google Docs."""

    slug: str
    title: str  # Composio's name for it
    active: bool  # its connection works, so crews are offered its tools
    status: str  # the connection's state in Composio, in lower case: active, expired, failed…
    tools: int | None  # how many tools it has, if Composio said


class ComposioAccount(TypedDict):
    """The user's Composio account, once connected: its key by name only, and its toolkits."""

    key: str
    key_set: bool
    access: str  # internal or external: what its tools that change things count as
    problem: str | None  # why its toolkits couldn't be listed, in plain words
    toolkits: list[ComposioToolkit]
    dashboard: str  # where the user connects more services


class ToolList(TypedDict):
    native: list[NativeTool]
    servers: list[ServerRow]
    apis: list[ApiRow]
    composio: ComposioAccount | None
    sandbox: str | None  # why code.run can't work here, or None
    folders: list[str]
    browser: str | None  # the browser jobs use ("Google Chrome", or an address), or None when none is found


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


class ProviderPreset(TypedDict):
    label: str
    endpoint: str
    key: str  # the key's name in keys.env; "" when the provider needs none
    model: str | None  # suggested when the provider doesn't list its models


class ChatGPTPlan(TypedDict):
    """Signing in with ChatGPT, so model calls use the person's ChatGPT plan (no key)."""

    state: str  # "signed in", "plan not allowed", "not signed in"
    email: str | None
    signing_in: bool  # waiting for the browser
    problem: str | None  # why the last sign-in didn't finish
    welcome: str | None  # the first-time "You're using your ChatGPT plan", until it's been shown
    usage_url: str  # ChatGPT settings › Usage


class ModelList(TypedDict):
    connections: list[Connection]
    default: str | None
    presets: list[ProviderPreset]  # well-known providers, to fill the add form
    chatgpt: ChatGPTPlan


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
    active_ids: list[str]  # the jobs running now
    unseen: list[UnseenJob]


class SinceLast(TypedDict):
    finished: int
    waiting: int
    jobs: list[UnseenJob]
