"""Tool registry. Roles name tools by what they do; the registry supplies them.

Native tools are built in ombro.native from tools.md; MCP tools come from the
servers tools.md lists (ombro.outward). Every call passes through the gate first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown to the role.

    `billable` says whether the service was actually called, and so whether a
    cost_per_call is charged for the attempt."""

    billable = True


class ToolInputError(ToolError):
    """The arguments were refused before anything was called. Never charged."""

    billable = False


@dataclass
class ToolContext:
    run_dir: Path
    role: str
    timeout: float = 60.0  # seconds this call may take: the lesser of a per-call cap and the run's time left

    @property
    def workspace(self) -> Path:
        return self.run_dir / "workspace"

    @property
    def scratch(self) -> Path:
        return self.run_dir / "scratch"


@dataclass
class Tool:
    name: str  # e.g. "web.search"
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Callable[[dict, ToolContext], Any] | None = None
    outward: bool = False  # reaches a service outside the run (every MCP tool)
    effect: str | None = None  # for outward tools: read, change or send
    input_schema: dict[str, Any] | None = None  # the arguments the service takes, without `reason`
    reason_key: str | None = None  # the argument the harness adds for the role's reason, if any

    @property
    def wire_name(self) -> str:
        return wire_name(self.name)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.wire_name, "description": self.description, "parameters": self.parameters},
        }


def wire_name(name: str) -> str:
    """Model APIs allow only [A-Za-z0-9_-] in function names, up to 64 characters."""
    import re

    return re.sub(r"[^A-Za-z0-9_-]", "_", name.replace(".", "__"))[:64]


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

