"""Tool registry. Roles name tools by what they do; the registry supplies them.

No role tools are built in yet. The registry is the seam where native tools,
providers and MCP tools are added; every call still passes through the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolContext:
    run_dir: Path
    role: str

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
    outward: bool = False  # sends, posts or changes something outside the run

    @property
    def wire_name(self) -> str:
        return wire_name(self.name)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.wire_name, "description": self.description, "parameters": self.parameters},
        }


def wire_name(name: str) -> str:
    """Model APIs allow only [A-Za-z0-9_-] in function names."""
    return name.replace(".", "__")


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


def default_registry() -> ToolRegistry:
    return ToolRegistry()
