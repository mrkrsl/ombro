"""Shared fixtures. All data here is invented; nothing touches the network."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from ombro import fake, mdfile, runs
from ombro.engine import Engine
from ombro.fake import FakeClock, ScriptedClient
from ombro.home import Home, ensure_home
from ombro.settings import load_limits, load_tool_settings
from ombro.tools import Tool, ToolRegistry

# One dollar per million tokens in and out, so dollar limits are easy to reason about.
TEST_MODELS = {
    "models": {
        "default": {
            "api": "openai",
            "endpoint": "https://models.invalid/v1",
            "model": "test-model",
            "key": "TEST_MODEL_KEY",
        },
        "cheap": "default",
    }
}

LEAD_REPORT = fake.say(
    "## What was done\nLEAD-REPORT-MARKER: the researcher looked into it.\n\n"
    "## What was not done and why\nNothing else.\n\n## Decisions for you\nNone."
)

SPEC = "# Survey of invented teapot makers\n\nList three fictional teapot makers in Lowtown with one fact each."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to resolve a name or open a connection fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def no_system_scheduler(monkeypatch, tmp_path):
    """No test may touch the real launchctl or crontab. Commands that would are
    refused, and the installer the CLI uses writes into a temporary folder."""
    import subprocess

    from ombro import scheduler

    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        name = Path(str(cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0])).name
        if name in ("launchctl", "crontab"):
            raise AssertionError(f"a test tried to run {name}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    fake = FakeSystem(tmp_path / "fake-launch-agents")
    monkeypatch.setattr(scheduler, "make_installer", lambda home: fake.installer(home))
    return fake


class FakeSystem:
    """Stands in for launchctl and crontab: records commands, keeps a crontab in memory."""

    def __init__(self, agents_dir: Path, platform: str = "darwin"):
        self.agents_dir = agents_dir
        self.platform = platform
        self.commands: list[list[str]] = []
        self.crontab: str | None = None
        self.loaded = False

    def run(self, cmd, input=None, **kwargs):
        import subprocess

        self.commands.append(list(cmd))
        out, code = "", 0
        if cmd[0] == "launchctl":
            if cmd[1] == "bootstrap":
                self.loaded = True
            elif cmd[1] == "bootout":
                code, self.loaded = (0 if self.loaded else 3), False
            elif cmd[1] == "print":
                code = 0 if self.loaded else 113
        elif cmd[:2] == ["crontab", "-l"]:
            if self.crontab is None:
                code = 1
            else:
                out = self.crontab
        elif cmd[:2] == ["crontab", "-"]:
            self.crontab = input or ""
        return subprocess.CompletedProcess(cmd, code, out, "")

    def installer(self, home):
        from ombro import scheduler

        return scheduler.Installer(home, platform=self.platform, agents_dir=self.agents_dir, run=self.run,
                                   python="/opt/invented/bin/python3", uid=501)


def write_role(home: Home, name: str, *, kind: str = "specialist", soul: str | None = None, crew: str = "default", **cfg):
    """A role in a crew; with kind lead, the one Lead (replacing the default one)."""
    folder = home.lead_dir if kind == "lead" and name == "lead" else home.roles_dir(crew) / name
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
    # The General Assistant that a fresh install brings is tested on its own; here the
    # default crew has just the one invented role.
    shutil.rmtree(h.roles_dir("default") / "general-assistant")
    write_role(h, "researcher", max_steps=10)
    # A short, fixed Lead soul, so token-budget tests do not depend on the length of the default one.
    (h.lead_dir / "soul.md").write_text(TEST_LEAD_SOUL)
    return h


TEST_LEAD_SOUL = """\
You are calm, exact and economical. You brief clearly, ask for no more than
the spec needs, and report plainly, including what went wrong.
"""


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


def use_fake_tools(home: Home, **tool_settings) -> None:
    """Point both web tools at the fake provider in tools.md, plus any extra settings."""
    tools = {"web.search": {"provider": "fake"}, "web.fetch": {"provider": "fake"}}
    for name, cfg in tool_settings.items():
        tools.setdefault(name.replace("__", "."), {}).update(cfg)
    mdfile.write(home.tools, {"tools": tools}, "Test tool settings.")


class Runner:
    """Builds a run, executes it with a scripted model, and exposes the results."""

    def __init__(self, home: Home):
        self.home = home
        self.clock = FakeClock()
        self.client: ScriptedClient | None = None
        self.engine: Engine | None = None
        self.run_dir: Path | None = None

    def __call__(self, script, *, limits=None, registry=None, spec=SPEC, crew="default", today=None,
                 spec_meta=None, crews=None) -> str:
        tools = load_tool_settings(self.home)
        lim = load_limits(self.home, limits, tools)
        self.run_dir = runs.create_run(self.home, crews or [crew], spec_meta or {}, spec, lim, source="test",
                                       tools=tools.to_dict())
        self.client = ScriptedClient(script, clock=self.clock)
        self.engine = Engine(
            self.home,
            self.run_dir,
            client_factory=lambda spec: self.client,
            registry=registry,
            clock=self.clock,
            today=today,
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


def use_fake_mcp(home: Home, record: Path, *, server: str = "fake", tools: dict | None = None,
                 server_settings: dict | None = None, **env) -> None:
    """Point both web tools at the fake provider and add Ombro's fake MCP server
    to tools.md. Everything the fake server would have done goes to `record`."""
    import sys

    cfg = {"command": sys.executable, "args": ["-m", "ombro.fake_mcp"],
           "env": {"OMBRO_FAKE_MCP_RECORD": str(record), **{k: str(v) for k, v in env.items()}},
           **(server_settings or {})}
    all_tools = {"web.search": {"provider": "fake"}, "web.fetch": {"provider": "fake"}, **(tools or {})}
    mdfile.write(home.tools, {"tools": all_tools, "servers": {server: cfg}}, "Test tool settings.")


def fake_sent(record: Path) -> list[dict]:
    """What the fake MCP server would have done, in order."""
    if not record.is_file():
        return []
    return [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
