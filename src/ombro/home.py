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
    def env(self) -> Path:
        return self.root / ".env"

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
    def preferences_config(self) -> Path:
        return self.root / "preferences" / "config.md"

    @property
    def instructions(self) -> Path:
        return self.root / "preferences" / "instructions.md"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def crews_dir(self) -> Path:
        return self.root / "crews"

    def crew_dir(self, crew: str) -> Path:
        return self.crews_dir / crew

    def roles_dir(self, crew: str) -> Path:
        return self.crew_dir(crew) / "roles"

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
    "tool_max_calls": {"default": 20},
    "tool_cost_per_call": {},
    "report_max_output_tokens": 2000,
    "report_timeout": "2m",
    "interview_max_turns": 40,
}

LIMITS_BODY = """\
Default caps for every run and interview in this home. Limits are fixed when
a run starts and recorded in that run's run.md; editing this file never
changes a run already in progress. A spec can override any of them for its
own run with a `limits:` block in its front matter.

allowance: how far a run or interview may go before it is stopped. One of

  level          run tokens   rounds   run time   each interview
  conservative     150,000       4       15m          40,000
  moderate         400,000       8       30m         100,000
  generous       1,500,000      16       1h          250,000
  unlimited        no cap       50       4h           no cap

Even `unlimited` keeps every other cap below: rounds, each role's steps,
loop detection and tool calls. `ombro limits` shows what is in force, and
`ombro limits set <level>` changes the level.

To set one value by hand instead, add it here; it wins over the level:

- max_tokens: total tokens for a run (`unlimited` for no cap).
- max_rounds: how many times the Lead may brief a role in one run.
- run_timeout: wall-clock limit for a run, e.g. 90s, 30m, 2h.
- interview_max_tokens: total tokens for one interview (set-up, role or brief).
- interview_max_turns: how many answers an interview may take.

Always in force:

- max_output_tokens: the most any single model call may produce.
- report_reserve: share of max_tokens held back so the final report can always be written.
- loop_threshold: how many times a role may make the same tool call with the same arguments.
- max_steps: model calls a role may make for one brief, by kind. A role's config.md can set its own.
- tool_max_calls: calls per tool per run. `default` applies to any tool not
  named; `max_calls` in tools.md sets one tool's cap.
- report_max_output_tokens, report_timeout: bounds on the final report call.

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
- key: the name of the variable in .env that holds the API key. Keys never go in this file.
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
  workspace/ and scratch/ folders only. No provider.

Settings for any tool:
- provider: which provider backs it.
- max_calls: calls allowed per run. Tools not named use `default` in limits.md.
- cost_per_call: optional US dollars per call, added to the estimated cost
  shown in reports. For services that bill in their own units, such as
  Firecrawl credits. Tools are limited by max_calls, not by cost.

providers: settings for each provider. `key` names the variable in .env that
holds its key (set it with `ombro keys set NAME`). `fake` returns invented
results and records every request in the run's fake-calls.jsonl.
"""

PREFERENCES_META = {"default_crew": DEFAULT_CREW, "editor": "", "display_currency": None}

PREFERENCES_BODY = """\
- default_crew: the crew commands use when --crew is not given.
- editor: command used to open files for editing. Empty means $EDITOR.
- display_currency: optional, e.g. `{code: GBP, rate: 0.79}`. Shown alongside
  US dollars; never used for enforcement.
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
    _write_if_missing(home.env, "", created, mode=0o600)
    _write_if_missing(home.limits, mdfile.dump(LIMITS_META, LIMITS_BODY), created)
    _write_if_missing(home.models, mdfile.dump(MODELS_META, MODELS_BODY), created)
    _write_if_missing(home.tools, mdfile.dump(TOOLS_META, TOOLS_BODY), created)
    _write_if_missing(home.preferences_config, mdfile.dump(PREFERENCES_META, PREFERENCES_BODY), created)
    _write_if_missing(home.instructions, mdfile.dump({}, INSTRUCTIONS_BODY), created)
    created += ensure_crew(home, DEFAULT_CREW, "The default crew.")
    return created
