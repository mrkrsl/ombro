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
    "budget_usd": 1.00,
    "max_tokens": 400000,
    "max_rounds": 8,
    "run_timeout": "30m",
    "max_output_tokens": 4000,
    "report_reserve": 0.10,
    "loop_threshold": 3,
    "max_steps": {"lead": 6, "specialist": 15, "helper": 8},
    "tool_max_calls": {"default": 20},
    "report_max_output_tokens": 2000,
    "report_timeout": "2m",
}

LIMITS_BODY = """\
Default caps for every run in this home. A spec can override any of them for
its own run by adding a `limits:` block to its front matter. Limits are fixed
when a run starts and recorded in that run's run.md; editing this file never
changes a run already in progress.

- budget_usd: spend cap for a run, worked out from token counts and the prices in models.md.
- max_tokens: total tokens for a run. The hard limit that still holds if a price is wrong.
- max_rounds: how many times the Lead may brief a role in one run.
- run_timeout: wall-clock limit for a run, e.g. 90s, 30m, 2h.
- max_output_tokens: the most any single model call may produce.
- report_reserve: share of budget_usd and max_tokens held back so the final report can always be paid for.
- loop_threshold: how many times a role may make the same tool call with the same arguments.
- max_steps: model calls a role may make for one brief, by kind. A role's config.md can set its own.
- tool_max_calls: calls per tool per run. `default` applies to any tool not named.
- report_max_output_tokens, report_timeout: bounds on the final report call.
"""

MODELS_META = {
    "models": {
        "default": {
            "api": "openai",
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "key": "DEEPSEEK_API_KEY",
            "price_per_mtok": {"input": 0.28, "output": 0.42},
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
- price_per_mtok: US dollars per million input and output tokens. Budgets are
  enforced from these figures, so check them against the provider's current
  prices. A model without prices is still bounded by max_tokens.
- A name can point at another name, as `cheap: default` does.
"""

TOOLS_BODY = """\
Which provider backs each tool, MCP servers, and per-tool settings.
No tools are configured yet.
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
    for sub in ("roles/lead", "memory", "schedules", "specs", "runs"):
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
    _write_if_missing(home.tools, mdfile.dump({}, TOOLS_BODY), created)
    _write_if_missing(home.preferences_config, mdfile.dump(PREFERENCES_META, PREFERENCES_BODY), created)
    _write_if_missing(home.instructions, mdfile.dump({}, INSTRUCTIONS_BODY), created)
    created += ensure_crew(home, DEFAULT_CREW, "The default crew.")
    return created
