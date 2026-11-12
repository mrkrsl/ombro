"""The home directory: the one place everything variable lives."""

from __future__ import annotations

import os
from pathlib import Path

from ombro import mdfile

DEFAULT_CREW = "default"


def home_root() -> Path:
    env = os.environ.get("OMBRO_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".config" / "ombro").resolve()


class Home:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else home_root()

    # Top-level files
    @property
    def secrets(self) -> Path:
        """Every secret lives here: keys, key files and sign-ins. Owner-only."""
        return self.root / "secrets"

    @property
    def env(self) -> Path:
        """Single keys, one NAME=value per line."""
        return self.secrets / "keys.env"

    @property
    def legacy_env(self) -> Path:
        """Where keys lived before the secrets folder; moved on first use."""
        return self.root / ".env"

    @property
    def oauth_dir(self) -> Path:
        """Sign-ins for remote MCP servers, one file per server, written by Ombro."""
        return self.secrets / "oauth"

    def ensure_secrets(self) -> Path:
        """The secrets folder, created owner-only if missing."""
        self.secrets.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets, 0o700)
        except OSError:
            pass
        return self.secrets

    @property
    def limits(self) -> Path:
        return self.root / "limits.md"

    @property
    def models(self) -> Path:
        return self.root / "models.md"

    @property
    def tools(self) -> Path:
        return self.root / "tools.md"

    @property
    def folders(self) -> Path:
        """Folders the user has shared with runs, and folders they declined to share."""
        return self.root / "folders.md"

    @property
    def preferences_config(self) -> Path:
        return self.root / "preferences" / "config.md"

    @property
    def instructions(self) -> Path:
        return self.root / "preferences" / "instructions.md"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def scheduler_dir(self) -> Path:
        return self.root / "scheduler"

    @property
    def crews_dir(self) -> Path:
        return self.root / "crews"

    def crew_dir(self, crew: str) -> Path:
        return self.crews_dir / crew

    def roles_dir(self, crew: str) -> Path:
        return self.crew_dir(crew) / "roles"

    def schedules_dir(self, crew: str) -> Path:
        return self.crew_dir(crew) / "schedules"

    def runs_dir(self, crew: str) -> Path:
        return self.crew_dir(crew) / "runs"

    def crews(self) -> list[str]:
        if not self.crews_dir.is_dir():
            return []
        return sorted(p.name for p in self.crews_dir.iterdir() if p.is_dir() and not p.name.startswith("."))

    def exists(self) -> bool:
        return self.limits.is_file() and self.crews_dir.is_dir()


# ---------------------------------------------------------------------------
# Default contents of a fresh install. Written only when a file is missing.

LIMITS_META = {
    "allowance": "moderate",
    "max_output_tokens": 4000,
    "report_reserve": 0.10,
    "loop_threshold": 3,
    "max_steps": {"lead": 6, "specialist": 15, "helper": 8},
    "tool_max_calls": {"default": 20, "memory.search": 8},
    "tool_cost_per_call": {},
    "report_max_output_tokens": 2000,
    "report_timeout": "2m",
    "interview_max_turns": 40,
    "memory_notes_max_chars": 10_000,
    "memory_index_entries": 20,
    "memory_index_entry_chars": 200,
    "memory_search_max_chars": 4_000,
    "chat_history_chars": 30_000,
    "chat_calls_per_message": 8,
    "chat_tool_max_calls": {"memory.search": 20, "memory.read": 30, "memory.list": 30, "web.search": 10,
                            "web.fetch": 15, "default": 10},
    "schedule_grace": "10m",
    "schedule_max_running": 2,
    "schedule_max_starts": 3,
    "code_timeout": "60s",
    "code_max_memory": "1GB",
    "code_output_chars": 8_000,
    "code_max_total": "2GB",
}

LIMITS_BODY = """\
Default caps for every run and interview in this home. Limits are fixed when
a run starts and recorded in that run's run.md; editing this file never
changes a run already in progress. A spec can override any of them for its
own run with a `limits:` block in its front matter.

allowance: how far a run or interview may go before it is stopped. One of

  level          run tokens   rounds   run time   each interview   each chat
  conservative     150,000       4       15m          40,000          60,000
  moderate         400,000       8       30m         100,000         150,000
  generous       1,500,000      16       1h          250,000         400,000
  unlimited        no cap       50       4h           no cap          no cap

Even `unlimited` keeps every other cap below: rounds, each role's steps,
loop detection and tool calls. `ombro limits` shows what is in force, and
`ombro limits set <level>` changes the level.

To set one value by hand instead, add it here; it wins over the level:

- max_tokens: total tokens for a run (`unlimited` for no cap).
- max_rounds: how many times the Lead may brief a role in one run.
- run_timeout: wall-clock limit for a run, e.g. 90s, 30m, 2h.
- interview_max_tokens: total tokens for one interview (set-up, role or brief).
- interview_max_turns: how many answers an interview may take.
- chat_max_tokens: total tokens for one `ombro chat` session.

Always in force:

- max_output_tokens: the most any single model call may produce.
- report_reserve: share of max_tokens held back so the final report can always be written.
- loop_threshold: how many times a role may make the same tool call with the same arguments.
- max_steps: model calls a role may make for one brief, by kind. A role's config.md can set its own.
- tool_max_calls: calls per tool per run. `default` applies to any tool not
  named; `max_calls` in tools.md sets one tool's cap.
- report_max_output_tokens, report_timeout: bounds on the final report call
  (and, separately, on the Lead's rewrite of notes.md just before it).

Memory, in characters so they can be checked in a text editor:

- memory_notes_max_chars: the cap on a crew's memory/notes.md, both parts
  together. A rewrite or a remember over the cap is refused.
- memory_index_entries, memory_index_entry_chars: how many of the most
  recent index.md entries a run's Lead is given, and how long each may be.
- memory_search_max_chars: the most text one memory.search returns.

Chat:

- chat_history_chars: once a conversation is longer than this, the oldest
  turns are no longer sent to the model (the transcript keeps them).
- chat_calls_per_message: model calls the Lead may make for one message.
- chat_tool_max_calls: calls per tool in one chat session.

Schedules (crews/<crew>/schedules/):

- schedule_grace: how late `ombro tick` may still start a slot, e.g. 10m.
  A slot not started by then is skipped and noted in the crew's index.md.
  A schedule file can set its own `grace`.
- schedule_max_running: scheduled runs that may be in progress at once.
- schedule_max_starts: scheduled runs one tick may start.

Code (the code.run tool), for each script a role runs:

- code_timeout: wall-clock time, e.g. 60s; never more than the run has left.
  CPU time is capped at the same.
- code_max_memory: memory, e.g. 1GB or 512MB.
- code_output_chars: how much of a script's output the role sees; the start
  and the end are kept and the middle cut. The run's code/ folder keeps more.
- code_max_total: all the files in the run's workspace/ and scratch/
  together, e.g. 2GB. Once scripts have written more, code.run runs no
  more scripts in that run, and the report says so.
- code.run has its own max_calls (10 unless tool_max_calls or tools.md says).

Dollars are not a limit. Where models.md has prices, reports show an estimate.
"""

MODELS_META = {
    "models": {
        "default": {
            "api": "openai",
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "key": "DEEPSEEK_API_KEY",
            "price_per_mtok": {"input": 0.30, "output": 1.20},
        },
        "cheap": "default",
    }
}

MODELS_BODY = """\
Model names used by roles (`model:` in a role's config.md), with endpoints and
prices. Any OpenAI-compatible chat endpoint with tool calling works.

- api: `openai` for an OpenAI-compatible endpoint, or `fake` for a scripted
  model used in tests and dry runs (`script:` names a JSON file of replies).
- key: the name of the variable in secrets/keys.env that holds the API key. Keys never go in this file.
- price_per_mtok: optional US dollars per million input and output tokens,
  used only to show an estimated cost in reports. Limits are in tokens.
- A name can point at another name, as `cheap: default` does.
"""

TOOLS_META = {
    "tools": {
        "web.search": {"provider": "duckduckgo", "max_calls": 20},
        "web.fetch": {"provider": "direct", "max_calls": 30},
        "workspace.write": {"max_calls": 40},
    },
    "providers": {"firecrawl": {"key": "FIRECRAWL_API_KEY"}},
}

TOOLS_BODY = """\
Which provider backs each tool, and per-tool settings. Roles name tools by
what they do (`web.search`), never by provider, so changing provider here is
a one-line edit and no role changes. Settings are fixed when a run starts and
recorded in its run.md.

Tools:
- web.search: providers `firecrawl` (needs a key), `duckduckgo` (no key;
  unofficial and rate-limited, so best effort) or `fake`.
- web.fetch: providers `firecrawl` (needs a key), `direct` (no key; a plain
  page download that refuses local and private network addresses) or `fake`.
- workspace.read, workspace.write, workspace.list: files in the run's
  workspace/ and scratch/ folders, and in shared folders the run was given
  (folders.md). No provider.
- workspace.place: copies a workspace file to a shared folder, or stages the
  copy in the outbox when the destination is not shared for writing.
- code.run: runs a Python script a role writes, in the operating system's
  sandbox: standard library only, no network, nothing outside the run's
  folders and its shared folders. Limits are in limits.md.

Settings for any tool:
- provider: which provider backs it.
- max_calls: calls allowed per run. Tools not named use `default` in limits.md.
- cost_per_call: optional US dollars per call, added to the estimated cost
  shown in reports. For services that bill in their own units, such as
  Firecrawl credits. Tools are limited by max_calls, not by cost.

providers: settings for each provider. `key` names the variable in secrets/keys.env that
holds its key (set it with `ombro keys set NAME`). `fake` returns invented
results and records every request in the run's fake-calls.jsonl.

servers: MCP servers, which supply every tool that reaches an outside
service. Each has either `command` and `args` (a server Ombro starts, which
handles its own sign-in) or `url` and optional `headers` (a remote server).
A remote server that asks for sign-in is connected once with `ombro connect
<server>`, in the browser; `client_id` gives one from the service's own
developer settings, for a service that does not let Ombro register itself.
`env` sets variables for a started server. Write a secret as ${NAME}: it is
read from secrets/keys.env when the server starts and never logged. A started
server gets only PATH, HOME, USER, LOGNAME, LANG, LC_ALL, LC_CTYPE and TMPDIR
from Ombro's own environment; `pass_env: [NAME, ...]` passes others. Put
secrets in `env`, not `command` or `args`: other users of this machine can
read a command line in the process list. Optional `timeout`
for each call, e.g. 60s. For example:

  servers:
    sheets:
      command: uvx
      args: [mcp-google-sheets@latest]
      env: {SERVICE_ACCOUNT_PATH: "${SHEETS_KEY_FILE}"}

A server's tools are named mcp.<server>.<tool>; `ombro tools <server>` lists
them. A role uses only the ones its `tools:` list names. Per-tool settings
for MCP tools, as well as max_calls and cost_per_call:
- class: read-only, internal or external, when the server's marking is
  wrong or missing. read-only changes nothing. internal changes things in
  your organisation's own tools (documents, sheets, databases, CRMs,
  Notion, Linear, files), even when colleagues see it; it runs without
  approval because the role is trusted, which is not the same as harmless.
  external reaches people outside your organisation, or the public, or
  spends money (email sent, social posts, publishing, purchases); a tool
  that can reach either side counts as external. A tool that changes
  something and is not classed counts as external. `class:` on a server
  (internal or external) covers all its tools that are not read-only; a
  tool's own line wins, so a team chat can be made internal if you wish.
- permission: refuse, a hard limit that no preference or spec can lift.
Whether other calls run or wait in the outbox is set by `outward:` in
preferences/config.md and in each spec.
"""

PREFERENCES_META = {"default_crew": DEFAULT_CREW, "editor": "", "display_currency": None, "outward": "balanced"}

PREFERENCES_BODY = """\
- default_crew: the crew commands use when --crew is not given.
- editor: command used to open files for editing. Empty means $EDITOR.
- display_currency: optional, e.g. `{code: GBP, rate: 0.79}`. Shown alongside
  US dollars; never used for enforcement.
- outward: what outward (MCP) tools may do without you, unless a spec says
  otherwise. manual: only read-only calls run; internal and external
  actions wait in the outbox. balanced: read-only and internal (changes in
  your organisation's own tools) run; external actions (reaching people outside your organisation, or the
  public, or spending money) wait. auto: everything runs. Waiting actions are reviewed with
  `ombro outbox`.
"""

INSTRUCTIONS_BODY = """\
<!--
Who you are and how you like to work. The Lead is given this text as user
context. Comments like this one are not sent to the model.
-->
"""

LEAD_CONFIG = {
    "name": "lead",
    "kind": "lead",
    "description": "Briefs the crew, keeps the run to the spec and writes the report.",
    "model": "default",
}

LEAD_SOUL = """\
You are calm, exact and economical. You brief clearly, ask for no more than
the spec needs, and report plainly, including what went wrong.
"""

NOTES_BODY = """\
# Notes

## From you

## From runs
"""


def _write_if_missing(path: Path, text: str, created: list[Path], mode: int | None = None) -> None:
    if path.exists():
        return
    mdfile.write_atomic(path, text, mode)
    created.append(path)


def ensure_crew(home: Home, crew: str, description: str = "") -> list[Path]:
    created: list[Path] = []
    cdir = home.crew_dir(crew)
    for sub in ("roles/lead", "memory", "schedules", "specs", "runs", "interviews"):
        (cdir / sub).mkdir(parents=True, exist_ok=True)
    _write_if_missing(
        cdir / "crew.md",
        mdfile.dump({"name": crew, "description": description or f"The {crew} crew."}),
        created,
    )
    _write_if_missing(cdir / "roles/lead/config.md", mdfile.dump(LEAD_CONFIG), created)
    _write_if_missing(cdir / "roles/lead/soul.md", LEAD_SOUL, created)
    _write_if_missing(cdir / "memory/index.md", "# Index\n", created)
    _write_if_missing(cdir / "memory/notes.md", NOTES_BODY, created)
    return created


def ensure_home(home: Home) -> list[Path]:
    """Create the layout and default files. Never overwrites anything."""
    created: list[Path] = []
    home.root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home.root, 0o700)
    except OSError:
        pass
    for sub in ("preferences", "skills", "chats", "crews"):
        (home.root / sub).mkdir(exist_ok=True)
    home.ensure_secrets()
    from ombro.keys import migrate

    migrate(home)
    _write_if_missing(home.env, "", created, mode=0o600)
    _write_if_missing(home.limits, mdfile.dump(LIMITS_META, LIMITS_BODY), created)
    _write_if_missing(home.models, mdfile.dump(MODELS_META, MODELS_BODY), created)
    _write_if_missing(home.tools, mdfile.dump(TOOLS_META, TOOLS_BODY), created)
    _write_if_missing(home.preferences_config, mdfile.dump(PREFERENCES_META, PREFERENCES_BODY), created)
    _write_if_missing(home.instructions, mdfile.dump({}, INSTRUCTIONS_BODY), created)
    created += ensure_crew(home, DEFAULT_CREW, "The default crew.")
    return created
