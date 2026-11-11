"""Shared fixtures. All data here is invented; nothing touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ombro import fake, mdfile, runs
from ombro.engine import Engine
from ombro.fake import FakeClock, ScriptedClient
from ombro.home import Home, ensure_home
from ombro.settings import load_limits
from ombro.tools import Tool, ToolRegistry

# One dollar per million tokens in and out, so dollar limits are easy to reason about.
TEST_MODELS = {
    "models": {
        "default": {
            "api": "openai",
            "endpoint": "https://models.invalid/v1",
            "model": "test-model",
            "key": "TEST_MODEL_KEY",
            "price_per_mtok": {"input": 1.0, "output": 1.0},
        },
        "cheap": "default",
    }
}

LEAD_REPORT = fake.say(
    "## What was done\nLEAD-REPORT-MARKER: the researcher looked into it.\n\n"
    "## What was not done and why\nNothing else.\n\n## Decisions for you\nNone."
)

SPEC = "# Survey of invented teapot makers\n\nList three fictional teapot makers in Lowtown with one fact each."


def write_role(home: Home, name: str, *, kind: str = "specialist", soul: str | None = None, crew: str = "default", **cfg):
    folder = home.roles_dir(crew) / name
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": kind, "description": cfg.pop("description", f"The {name} role, for tests.")}
    meta.update(cfg)
    mdfile.write(folder / "config.md", meta)
    (folder / "soul.md").write_text(soul or f"You are {name}, an invented role used in tests.\n")
    return folder


@pytest.fixture
def home(tmp_path, monkeypatch) -> Home:
    root = tmp_path / "ombro-test-home"
    monkeypatch.setenv("OMBRO_HOME", str(root))
    h = Home()
    ensure_home(h)
    mdfile.write(h.models, TEST_MODELS, "Test models.")
    write_role(h, "researcher", max_steps=10)
    return h


def echo_registry(calls: list | None = None) -> ToolRegistry:
    """A test-only tool, so loop detection and tool caps can be exercised."""

    def handler(args, ctx):
        if calls is not None:
            calls.append(args)
        return {"echo": args}

    return ToolRegistry(
        [
            Tool(
                name="test.echo",
                description="Echo the arguments back.",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                handler=handler,
            )
        ]
    )


class Runner:
    """Builds a run, executes it with a scripted model, and exposes the results."""

    def __init__(self, home: Home):
        self.home = home
        self.clock = FakeClock()
        self.client: ScriptedClient | None = None
        self.engine: Engine | None = None
        self.run_dir: Path | None = None

    def __call__(self, script, *, limits=None, registry=None, spec=SPEC, crew="default") -> str:
        lim = load_limits(self.home, limits)
        self.run_dir = runs.create_run(self.home, crew, {}, spec, lim, source="test")
        self.client = ScriptedClient(script, clock=self.clock)
        self.engine = Engine(
            self.home,
            self.run_dir,
            client_factory=lambda spec: self.client,
            registry=registry,
            clock=self.clock,
        )
        return self.engine.execute()

    @property
    def report(self) -> str:
        return (self.run_dir / "report.md").read_text()

    @property
    def meta(self) -> dict:
        return runs.read_run(self.run_dir)[0]

    @property
    def events(self) -> list[dict]:
        return [json.loads(line) for line in (self.run_dir / "journal.jsonl").read_text().splitlines() if line]

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


@pytest.fixture
def run(home) -> Runner:
    return Runner(home)
