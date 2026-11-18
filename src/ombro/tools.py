"""Tool registry. Roles name tools by what they do; the registry supplies them.

Native tools are built in ombro.native from tools.md; MCP tools come from the
servers tools.md lists (ombro.outward). Every call passes through the gate first.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown to the role."""


class ToolInputError(ToolError):
    """The arguments were refused before anything was called."""


class NotGranted(ToolInputError):
    """A path outside the job and outside every folder shared with it. Recorded,
    so the report can say which folders the job asked for."""

    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class LayoutChanged(ToolInputError):
    """A job folder (workspace/, scratch/) or a shared folder's root is no longer
    a real folder at the path fixed when the job started. Nothing is done there."""


@dataclass(frozen=True)
class SharedFolder:
    name: str
    path: Path  # the real path, fixed when the job started
    access: str  # read or write

    @property
    def writable(self) -> bool:
        return self.access == "write"


# The job's own folders and each shared folder have one path, fixed when the job
# starts. Tools never work a root out again by following symlinks: a script may
# change what is inside a folder, and if the folder itself were ever replaced,
# following it would lead anywhere. So every use checks the root is still a real
# folder at its fixed path, and refuses if it is not.


def area_path(job_dir: Path, area: str) -> Path:
    """The fixed path of workspace/ or scratch/. The job folder's own path cannot be
    changed by a role, so resolving it is safe; the area itself is never followed."""
    return Path(os.path.realpath(job_dir)) / area


def _check_root(root: Path, label: str) -> Path:
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return root  # not made yet; nothing can lead out through it
    if not stat.S_ISDIR(st.st_mode):  # lstat: a symlink is never a folder here
        raise LayoutChanged(f"{label}/ is no longer a real folder (it has been replaced, for example by a "
                            "symlink), so it cannot be used. Nothing was done. Say so in did_not_do.")
    return root


def checked_area(job_dir: Path, area: str) -> Path:
    return _check_root(area_path(job_dir, area), area)


def checked_shared(folder: SharedFolder) -> Path:
    return _check_root(folder.path, f"shared/{folder.name}")


@dataclass
class ToolContext:
    job_dir: Path
    role: str
    timeout: float = 60.0  # seconds this call may take: the lesser of a per-call cap and the job's time left
    shared: dict[str, SharedFolder] = field(default_factory=dict)  # folders this job was given, by name
    limits: Any = None  # the job's Limits, for tools with limits of their own (code.run)
    cancelled: Callable[[], bool] | None = None  # true once the job is stopping
    event: Callable[..., None] | None = None  # write a journal event as this role: event(action, note, **fields)
    # Files in shared folders whose old version this job has archived before replacing it.
    # None: archive before every replacement (chat, which has no backup clone).
    archived: set[str] | None = None

    def record(self, action: str, note: str, **fields: Any) -> None:
        if self.event is not None:
            self.event(action, note, **fields)

    def area(self, name: str) -> Path:
        """workspace/ or scratch/ at its fixed path; LayoutChanged if it is not a real folder."""
        return checked_area(self.job_dir, name)

    @property
    def workspace(self) -> Path:
        return self.job_dir / "workspace"

    @property
    def scratch(self) -> Path:
        return self.job_dir / "scratch"


@dataclass
class Tool:
    name: str  # e.g. "web.search"
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Callable[[dict, ToolContext], Any] | None = None
    outward: bool = False  # reaches a service outside the job (every MCP tool)
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

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)



# -- deferred tools ----------------------------------------------------------
# An outside service's tools (mcp.*) are not sent with every model call: their definitions can run to
# tens of thousands of tokens (Notion's 77 came to about 57,000) and most briefs never use them. The
# model sees their names in one small tool, tools__load, and gets the full definitions of the ones it
# asks for from the next call on.

LOAD_NAME = "tools__load"
LOAD_SHORT_CHARS = 90  # of each tool's description in the listing


def is_deferred(tool: "Tool") -> bool:
    return tool.name.startswith(("mcp.", "composio."))


def _first_sentence(text: str) -> str:
    line = " ".join((text or "").split())
    for end in (". ", "? ", "! "):
        if end in line:
            line = line.split(end, 1)[0] + end.strip()
            break
    return line if len(line) <= LOAD_SHORT_CHARS else line[: LOAD_SHORT_CHARS - 1].rstrip() + "…"


def load_tool_schema(deferred: list["Tool"]) -> dict[str, Any]:
    """The one tool that lists the deferred tools and loads the ones asked for."""
    listing = "\n".join(f"- {t.wire_name}: {_first_sentence(t.description)}" for t in deferred)
    return {
        "type": "function",
        "function": {
            "name": LOAD_NAME,
            "description": (
                "Load the full definitions of tools that reach outside services, so you can call them. They are "
                "listed here by name only, to keep each call small; load only the ones the task needs, then call "
                "them from your next step. Tools you can load:\n" + listing
            ),
            "parameters": {
                "type": "object",
                "properties": {"names": {"type": "array", "items": {"type": "string"},
                                         "description": "The tool names to load, as listed."}},
                "required": ["names"],
            },
        },
    }


def load_tools(arguments: dict | None, deferred: dict[str, "Tool"], loaded: set[str]) -> dict[str, Any]:
    """Handle a tools__load call: add the names asked for to `loaded` (wire names) and say what happened."""
    names = (arguments or {}).get("names")
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return {"error": "give `names`: a list of tool names from the listing"}
    done, unknown = [], []
    for n in names:
        key = wire_name(str(n))
        if key in deferred:
            loaded.add(key)
            done.append(key)
        else:
            unknown.append(str(n))
    out: dict[str, Any] = {"loaded": done}
    if unknown:
        out["unknown"] = unknown
    if done:
        out["note"] = "These tools can be called from your next step."
    return out
