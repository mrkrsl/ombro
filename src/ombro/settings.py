"""Limits, models and preferences, read from the home directory."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ombro import mdfile
from ombro.home import DEFAULT_CREW, LIMITS_META, Home


class SetupError(Exception):
    """Something in the home directory stops a run from starting."""


# ---------------------------------------------------------------------------
# Limits

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs)?\s*$")


def parse_duration(value: Any) -> float:
    if isinstance(value, bool):
        raise SetupError(f"not a duration: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION.match(str(value))
    if not m:
        raise SetupError(f"not a duration: {value!r} (use e.g. 90s, 30m, 2h)")
    n, unit = float(m.group(1)), (m.group(2) or "s")[0]
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# Allowance levels. One word in limits.md sets the token, round and time caps
# for runs and interviews, so nothing runs away. Any single value can still be
# set by hand in limits.md or a spec, which wins over the level.
ALLOWANCES: dict[str, dict[str, Any]] = {
    "conservative": {"max_tokens": 300_000, "max_rounds": 4, "run_timeout": "15m", "interview_max_tokens": 50_000,
                     "chat_max_tokens": 100_000},
    "moderate": {"max_tokens": 800_000, "max_rounds": 8, "run_timeout": "30m", "interview_max_tokens": 100_000,
                 "chat_max_tokens": 200_000},
    "generous": {"max_tokens": 2_000_000, "max_rounds": 16, "run_timeout": "1h", "interview_max_tokens": 250_000,
                 "chat_max_tokens": 1_000_000},
    "unlimited": {"max_tokens": None, "max_rounds": 50, "run_timeout": "4h", "interview_max_tokens": None,
                  "chat_max_tokens": None},
}
DEFAULT_ALLOWANCE = "moderate"
LEVEL_KEYS = ("max_tokens", "max_rounds", "run_timeout", "interview_max_tokens", "chat_max_tokens")
# Memory and chat settings, in characters where they bound text. Defaults for
# runs recorded before they existed, and for limits.md files that leave them out.
MEMORY_CHAT_DEFAULTS: dict[str, Any] = {
    "memory_notes_max_chars": 10_000,
    "memory_index_entries": 20,
    "memory_index_entry_chars": 200,
    "memory_search_max_chars": 4_000,
    "chat_max_tokens": 200_000,
    "chat_history_chars": 30_000,
    "fetch_max_query": 200,
    "chat_calls_per_message": 8,
    "chat_tool_max_calls": {"memory.search": 20, "memory.read": 30, "memory.list": 30, "web.search": 10,
                            "web.fetch": 15, "folders.list": 30, "folders.read": 40, "folders.write": 10,
                            "default": 10},
}
# Scheduler settings: how late a tick may start a slot, and how many scheduled
# runs may start in one tick or run at once.
SCHEDULE_DEFAULTS: dict[str, Any] = {"schedule_grace": "10m", "schedule_max_running": 2, "schedule_max_starts": 3}
# Code execution: limits on each script code.run runs.
CODE_DEFAULTS: dict[str, Any] = {"code_timeout": "60s", "code_max_memory": "1GB", "code_output_chars": 8_000,
                                 "code_max_total": "2GB"}
CODE_DEFAULT_CALLS = 10  # code.run's max_calls when neither limits.md nor tools.md sets one
CHAT_MAX_MESSAGES = 10_000  # chat is bounded by tokens and tool calls, not by how many messages are sent
# Settings from earlier versions that are accepted and ignored.
RETIRED_LIMITS = ("budget_usd", "tool_cost_per_call")
INTERVIEW_TIMEOUT = 6 * 3600.0  # interviews wait on a person; the token and turn caps bound them


_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)?\s*$", re.IGNORECASE)


def parse_size(value: Any) -> int:
    """Bytes from 1GB, 512MB, 800kb or a plain number of bytes."""
    if isinstance(value, bool):
        raise SetupError(f"not a size: {value!r}")
    if isinstance(value, (int, float)):
        return int(value)
    m = _SIZE.match(str(value))
    if not m:
        raise SetupError(f"not a size: {value!r} (use e.g. 512MB or 1GB)")
    n, unit = float(m.group(1)), (m.group(2) or "b").lower()
    return int(n * {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}[unit])


def format_size(n: int) -> str:
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= size:
            v = n / size
            return f"{v:.0f} {unit}" if v == int(v) else f"{v:.1f} {unit}"
    return f"{n} bytes"


def format_tokens(n: int | None) -> str:
    return "no cap" if n is None else f"{n:,}"


@dataclass(frozen=True)
class Limits:
    max_tokens: int | None
    max_rounds: int
    run_timeout: float
    max_output_tokens: int
    report_reserve: float
    loop_threshold: int
    allowance: str = DEFAULT_ALLOWANCE
    max_steps: dict[str, int] = field(default_factory=dict)
    tool_max_calls: dict[str, int] = field(default_factory=dict)
    report_max_output_tokens: int = 2000
    report_timeout: float = 120.0
    interview_max_tokens: int | None = 100_000
    interview_max_turns: int = 40
    memory_notes_max_chars: int = 10_000
    memory_index_entries: int = 20
    memory_index_entry_chars: int = 200
    memory_search_max_chars: int = 4_000
    chat_max_tokens: int | None = 200_000
    chat_history_chars: int = 30_000
    fetch_max_query: int = 200  # characters after ? in an address web.fetch will fetch (7.1)
    chat_calls_per_message: int = 8
    chat_tool_max_calls: dict[str, int] = field(default_factory=dict)
    schedule_grace: float = 600.0
    schedule_max_running: int = 2
    schedule_max_starts: int = 3
    code_timeout: float = 60.0
    code_max_memory: int = 1024**3
    code_output_chars: int = 8_000
    code_max_total: int = 2 * 1024**3  # all the files in workspace/ and scratch/ together

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Limits":
        known = set(cls.__dataclass_fields__)
        # Absent from runs made before they existed.
        d = {
            "allowance": "custom",
            "interview_max_tokens": ALLOWANCES[DEFAULT_ALLOWANCE]["interview_max_tokens"],
            "interview_max_turns": 40,
            **copy.deepcopy(MEMORY_CHAT_DEFAULTS),
            **SCHEDULE_DEFAULTS,
            **CODE_DEFAULTS,
            **{k: v for k, v in d.items() if k not in RETIRED_LIMITS},
        }
        unknown = set(d) - known
        if unknown:
            raise SetupError(f"unknown limit(s): {', '.join(sorted(unknown))}")
        missing = known - set(d)
        if missing:
            raise SetupError(f"missing limit(s): {', '.join(sorted(missing))}")

        def pos_num(name: str, v: Any, integer: bool = False) -> Any:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise SetupError(f"limit {name} must be a positive number, not {v!r}")
            if integer:
                if int(v) != v:
                    raise SetupError(f"limit {name} must be a whole number, not {v!r}")
                return int(v)
            return float(v)

        def token_cap(name: str, v: Any) -> int | None:
            if v is None or (isinstance(v, str) and v.strip().lower() in ("unlimited", "none")):
                return None
            return pos_num(name, v, integer=True)

        def int_map(name: str, v: Any) -> dict[str, int]:
            if not isinstance(v, dict):
                raise SetupError(f"limit {name} must be a mapping")
            return {str(k): pos_num(f"{name}.{k}", n, integer=True) for k, n in v.items()}

        reserve = d["report_reserve"]
        if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not 0 <= reserve <= 0.5:
            raise SetupError("limit report_reserve must be between 0 and 0.5")
        tool_calls = int_map("tool_max_calls", d["tool_max_calls"])
        tool_calls.setdefault("default", 20)
        tool_calls.setdefault("code.run", CODE_DEFAULT_CALLS)
        chat_calls = int_map("chat_tool_max_calls", d["chat_tool_max_calls"])
        chat_calls.setdefault("default", 10)
        return cls(
            allowance=str(d["allowance"]),
            max_tokens=token_cap("max_tokens", d["max_tokens"]),
            max_rounds=pos_num("max_rounds", d["max_rounds"], integer=True),
            run_timeout=pos_num("run_timeout", parse_duration(d["run_timeout"])),
            max_output_tokens=pos_num("max_output_tokens", d["max_output_tokens"], integer=True),
            report_reserve=float(reserve),
            loop_threshold=pos_num("loop_threshold", d["loop_threshold"], integer=True),
            max_steps=int_map("max_steps", d["max_steps"]),
            tool_max_calls=tool_calls,
            report_max_output_tokens=pos_num("report_max_output_tokens", d["report_max_output_tokens"], integer=True),
            report_timeout=pos_num("report_timeout", parse_duration(d["report_timeout"])),
            interview_max_tokens=token_cap("interview_max_tokens", d["interview_max_tokens"]),
            interview_max_turns=pos_num("interview_max_turns", d["interview_max_turns"], integer=True),
            memory_notes_max_chars=pos_num("memory_notes_max_chars", d["memory_notes_max_chars"], integer=True),
            memory_index_entries=pos_num("memory_index_entries", d["memory_index_entries"], integer=True),
            memory_index_entry_chars=pos_num("memory_index_entry_chars", d["memory_index_entry_chars"], integer=True),
            memory_search_max_chars=pos_num("memory_search_max_chars", d["memory_search_max_chars"], integer=True),
            chat_max_tokens=token_cap("chat_max_tokens", d["chat_max_tokens"]),
            chat_history_chars=pos_num("chat_history_chars", d["chat_history_chars"], integer=True),
            fetch_max_query=pos_num("fetch_max_query", d["fetch_max_query"], integer=True),
            chat_calls_per_message=pos_num("chat_calls_per_message", d["chat_calls_per_message"], integer=True),
            chat_tool_max_calls=chat_calls,
            schedule_grace=pos_num("schedule_grace", parse_duration(d["schedule_grace"])),
            schedule_max_running=pos_num("schedule_max_running", d["schedule_max_running"], integer=True),
            schedule_max_starts=pos_num("schedule_max_starts", d["schedule_max_starts"], integer=True),
            code_timeout=pos_num("code_timeout", parse_duration(d["code_timeout"])),
            code_max_memory=pos_num("code_max_memory", parse_size(d["code_max_memory"]), integer=True),
            code_output_chars=pos_num("code_output_chars", d["code_output_chars"], integer=True),
            code_max_total=pos_num("code_max_total", parse_size(d["code_max_total"]), integer=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def max_calls_for(self, tool: str) -> int:
        return self.tool_max_calls.get(tool, self.tool_max_calls["default"])

    def for_interview(self) -> "Limits":
        """The limits one interview runs under: its own token cap, a turn cap, no reserve."""
        from dataclasses import replace

        return replace(
            self,
            max_tokens=self.interview_max_tokens,
            max_rounds=self.interview_max_turns,
            run_timeout=INTERVIEW_TIMEOUT,
            report_reserve=0.0,
        )

    def for_chat(self) -> "Limits":
        """The limits one chat session runs under: its own token cap and tool-call caps."""
        from dataclasses import replace

        return replace(
            self,
            max_tokens=self.chat_max_tokens,
            max_rounds=CHAT_MAX_MESSAGES,
            run_timeout=INTERVIEW_TIMEOUT,
            report_reserve=0.0,
            tool_max_calls=dict(self.chat_tool_max_calls),
        )


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _layer(acc: dict, layer: dict) -> dict:
    """Merge one layer of settings. A layer that names an allowance replaces the
    level-set values from earlier layers; values it sets by hand still win."""
    layer = layer or {}
    if "allowance" in layer:
        acc = {k: v for k, v in acc.items() if k not in LEVEL_KEYS}
    return _deep_merge(acc, layer)


def resolve_allowance(merged: dict) -> dict:
    level = str(merged.get("allowance") or DEFAULT_ALLOWANCE).strip().lower()
    if level not in ALLOWANCES:
        raise SetupError(f"allowance must be one of {', '.join(ALLOWANCES)}, not {merged.get('allowance')!r}")
    return {**ALLOWANCES[level], **merged, "allowance": level}


def load_limits(
    home: Home,
    overrides: dict | None = None,
    tools: "ToolSettings | None" = None,
    schedule: dict | None = None,
) -> Limits:
    """limits.md, then per-tool caps from tools.md, then a spec's
    overrides, then a schedule's."""
    meta = mdfile.read(home.limits)[0] if home.limits.is_file() else {}
    merged = _layer(copy.deepcopy(LIMITS_META), meta)
    tools = tools if tools is not None else load_tool_settings(home)
    per_tool: dict[str, dict] = {"tool_max_calls": {}}
    for name, cfg in tools.tools.items():
        if "max_calls" in cfg:
            per_tool["tool_max_calls"][name] = cfg["max_calls"]
    merged = _layer(_layer(_layer(merged, per_tool), overrides or {}), schedule or {})
    return Limits.from_dict(resolve_allowance(merged))


# ---------------------------------------------------------------------------
# Tools (tools.md)

# The native tools that use a provider, the providers each may use, and the default.
PROVIDER_TOOLS: dict[str, tuple[tuple[str, ...], str]] = {
    "web.search": (("firecrawl", "duckduckgo", "fake"), "duckduckgo"),
    "web.fetch": (("firecrawl", "direct", "fake"), "direct"),
}
PROVIDER_KEYS = {"firecrawl": "FIRECRAWL_API_KEY"}
_TOOL_FIELDS = {"provider", "max_calls", "class", "permission"}
# Accepted in tools.md from earlier versions, and ignored.
_RETIRED_TOOL_FIELDS = {"cost_per_call"}
_SERVER_FIELDS = {"command", "args", "env", "pass_env", "url", "headers", "timeout", "start_timeout", "class",
                  "client_id"}
TOOL_CLASSES = ("read-only", "internal", "external")
# A server-wide class covers its tools that are not read-only; reading stays reading.
SERVER_CLASSES = ("internal", "external")
# Earlier names: stage 6's change and send, and read and work before the rename.
_OLD_CLASSES = {"change": "internal", "send": "external", "work": "internal", "read": "read-only"}
SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


@dataclass(frozen=True)
class ToolSettings:
    """Per-tool settings from tools.md. `tools` maps a tool name to its settings;
    `providers` maps a provider name to its own settings (such as which key it uses);
    `servers` maps an MCP server's name to how to start or reach it."""

    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, where: str = "tools.md") -> "ToolSettings":
        d = d or {}
        unknown = set(d) - {"tools", "providers", "servers"}
        if unknown:
            raise SetupError(f"{where}: unknown setting(s): {', '.join(sorted(unknown))} "
                             "(use tools:, providers: and servers:)")
        raw_tools = d.get("tools") or {}
        raw_providers = d.get("providers") or {}
        raw_servers = d.get("servers") or {}
        if not all(isinstance(x, dict) for x in (raw_tools, raw_providers, raw_servers)):
            raise SetupError(f"{where}: `tools`, `providers` and `servers` must be mappings")
        tools: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_tools.items():
            name = str(name)
            cfg = cfg or {}
            if not isinstance(cfg, dict):
                raise SetupError(f"{where}: settings for {name} must be a mapping, e.g. {{provider: fake, max_calls: 10}}")
            bad = set(cfg) - _TOOL_FIELDS - _RETIRED_TOOL_FIELDS
            if bad:
                raise SetupError(f"{where}: {name} has unknown setting(s): {', '.join(sorted(bad))}")
            out: dict[str, Any] = {}
            if "provider" in cfg:
                provider = str(cfg["provider"])
                allowed = PROVIDER_TOOLS.get(name, ((), ""))[0]
                if provider not in allowed:
                    choices = ", ".join(allowed) if allowed else "none (this tool has no providers)"
                    raise SetupError(f"{where}: {name} cannot use provider {provider!r}; choose from {choices}")
                out["provider"] = provider
            if "max_calls" in cfg:
                v = cfg["max_calls"]
                if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                    raise SetupError(f"{where}: max_calls for {name} must be a positive whole number")
                out["max_calls"] = v
            if "class" in cfg or "permission" in cfg:
                if not name.startswith("mcp."):
                    raise SetupError(f"{where}: class and permission apply only to MCP tools (mcp.<server>.<tool>), "
                                     f"not {name}")
            if "class" in cfg:
                if cfg["class"] in _OLD_CLASSES:
                    raise SetupError(f"{where}: class {cfg['class']!r} for {name} has been renamed; use "
                                     f"{_OLD_CLASSES[cfg['class']]!r} (classes are read-only, internal and external)")
                if cfg["class"] not in TOOL_CLASSES:
                    raise SetupError(f"{where}: class for {name} must be one of {', '.join(TOOL_CLASSES)}")
                out["class"] = cfg["class"]
            if "permission" in cfg:
                if cfg["permission"] != "refuse":
                    raise SetupError(f"{where}: permission for {name} can only be `refuse` here; whether a tool "
                                     "runs or is staged is set by `outward:` in preferences or a spec")
                out["permission"] = "refuse"
            tools[name] = out
        providers: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_providers.items():
            if not isinstance(cfg, dict):
                raise SetupError(f"{where}: settings for provider {name} must be a mapping")
            providers[str(name)] = dict(cfg)
        servers: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_servers.items():
            name = str(name)
            if not SERVER_NAME.match(name):
                raise SetupError(f"{where}: server name {name!r} must use lower-case letters, digits, - and _")
            if not isinstance(cfg, dict):
                raise SetupError(f"{where}: settings for server {name} must be a mapping")
            bad = set(cfg) - _SERVER_FIELDS
            if bad:
                raise SetupError(f"{where}: server {name} has unknown setting(s): {', '.join(sorted(bad))}")
            if bool(cfg.get("command")) == bool(cfg.get("url")):
                raise SetupError(f"{where}: server {name} needs either `command` (a local server) or `url` "
                                 "(a remote one), not both")
            if cfg.get("url") and not str(cfg["url"]).startswith(("https://", "http://")):
                raise SetupError(f"{where}: the url for server {name} must start with https://")
            for key in ("env", "headers"):
                if key in cfg and not isinstance(cfg[key] or {}, dict):
                    raise SetupError(f"{where}: `{key}` for server {name} must be a mapping")
            if "args" in cfg and not isinstance(cfg["args"] or [], list):
                raise SetupError(f"{where}: `args` for server {name} must be a list")
            if "pass_env" in cfg and (not isinstance(cfg["pass_env"] or [], list) or not all(
                    isinstance(n, str) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n) for n in cfg["pass_env"] or [])):
                raise SetupError(f"{where}: `pass_env` for server {name} must be a list of variable names")
            if "class" in cfg:
                if cfg["class"] in _OLD_CLASSES:
                    raise SetupError(f"{where}: class {cfg['class']!r} for server {name} has been renamed; use "
                                     f"{_OLD_CLASSES[cfg['class']]!r}")
                if cfg["class"] not in SERVER_CLASSES:
                    raise SetupError(f"{where}: class for server {name} must be internal or external (it covers "
                                     "the server's tools that are not read-only)")
            for key in ("timeout", "start_timeout"):
                if key in cfg:
                    try:
                        cfg = {**cfg, key: parse_duration(cfg[key])}
                    except SetupError:
                        raise SetupError(f"{where}: {key} for server {name} must be a duration, e.g. 60s") from None
            servers[name] = dict(cfg)
        for name in tools:
            if name.startswith("mcp."):
                parts = name.split(".", 2)
                if len(parts) != 3 or not parts[2]:
                    raise SetupError(f"{where}: {name} should be mcp.<server>.<tool>")
        return cls(tools=tools, providers=providers, servers=servers)

    def to_dict(self) -> dict[str, Any]:
        return {"tools": copy.deepcopy(self.tools), "providers": copy.deepcopy(self.providers),
                "servers": copy.deepcopy(self.servers)}

    def provider_for(self, tool: str) -> str:
        return self.tools.get(tool, {}).get("provider") or PROVIDER_TOOLS[tool][1]

    def key_name(self, provider: str) -> str | None:
        name = self.providers.get(provider, {}).get("key") or PROVIDER_KEYS.get(provider)
        return str(name) if name else None


def load_tool_settings(home: Home) -> ToolSettings:
    if not home.tools.is_file():
        return ToolSettings()
    meta, _ = mdfile.read(home.tools)
    return ToolSettings.from_dict(meta, str(home.tools))


# ---------------------------------------------------------------------------
# Models


@dataclass(frozen=True)
class ModelSpec:
    name: str
    api: str
    model: str
    endpoint: str | None = None
    key: str | None = None
    script: str | None = None
    max_output_tokens: int | None = None  # the most this model may write in one reply, if less than the limit


def load_models(home: Home) -> dict[str, ModelSpec]:
    if not home.models.is_file():
        raise SetupError(f"{home.models} is missing. Run `ombro init`.")
    meta, _ = mdfile.read(home.models)
    raw = meta.get("models") or {}
    if not isinstance(raw, dict):
        raise SetupError(f"{home.models}: `models` must be a mapping of names")

    def resolve(name: str, seen: tuple[str, ...] = ()) -> ModelSpec:
        if name in seen:
            raise SetupError(f"{home.models}: model names point at each other in a loop: {' -> '.join(seen + (name,))}")
        if name not in raw:
            raise SetupError(f"{home.models}: no model named {name!r}")
        entry = raw[name]
        if isinstance(entry, str):
            target = resolve(entry, seen + (name,))
            return ModelSpec(**{**asdict(target), "name": name})
        if not isinstance(entry, dict):
            raise SetupError(f"{home.models}: model {name!r} must be a mapping or another model's name")
        api = str(entry.get("api", "openai"))
        if api not in ("openai", "fake"):
            raise SetupError(f"{home.models}: model {name!r} has unknown api {api!r} (use openai or fake)")
        if api == "openai" and not entry.get("endpoint"):
            raise SetupError(f"{home.models}: model {name!r} has no endpoint")
        if api == "fake" and not entry.get("script"):
            raise SetupError(f"{home.models}: fake model {name!r} has no script")
        return ModelSpec(
            name=name,
            api=api,
            model=str(entry.get("model") or name),
            endpoint=entry.get("endpoint"),
            key=entry.get("key") or None,
            script=entry.get("script"),
            max_output_tokens=int(entry["max_output_tokens"]) if entry.get("max_output_tokens") else None,
        )

    return {name: resolve(name) for name in raw}


# ---------------------------------------------------------------------------
# Preferences


THEMES = ("auto", "light", "dark")


@dataclass
class Preferences:
    name: str = ""
    theme: str = "auto"
    default_crew: str = DEFAULT_CREW
    editor: str = ""
    instructions: str = ""
    outward: Any = None


def set_preference(home: Home, key: str, value: Any) -> None:
    """Change one setting in preferences/config.md, keeping the rest and its notes."""
    from ombro.home import PREFERENCES_BODY

    path = home.preferences_config
    meta, body = mdfile.read(path) if path.is_file() else ({}, PREFERENCES_BODY)
    meta[key] = value
    mdfile.write(path, meta, body)


def load_preferences(home: Home) -> Preferences:
    prefs = Preferences()
    if home.preferences_config.is_file():
        meta, _ = mdfile.read(home.preferences_config)
        prefs.name = " ".join(str(meta.get("name") or "").split())[:60]
        theme = str(meta.get("theme") or "auto").strip().lower()
        prefs.theme = theme if theme in THEMES else "auto"
        prefs.default_crew = str(meta.get("default_crew") or DEFAULT_CREW)
        prefs.editor = str(meta.get("editor") or "")
        prefs.outward = meta.get("outward")
    if home.instructions.is_file():
        _, body = mdfile.read(home.instructions)
        prefs.instructions = mdfile.prompt_text(body)
    return prefs


def set_allowance(home, level: str) -> list[str]:
    """Set the allowance level in limits.md for the whole home. Values set by hand
    for what the level decides are removed, so the level applies; returns them."""
    from ombro import mdfile

    level = (level or "").strip().lower()
    if level not in ALLOWANCES:
        raise SetupError(f"Choose one of: {', '.join(ALLOWANCES)}.")
    meta, body = mdfile.read(home.limits)
    dropped = [k for k in (*LEVEL_KEYS, *RETIRED_LIMITS) if k in meta]
    for k in dropped:
        del meta[k]
    meta = {"allowance": level, **{k: v for k, v in meta.items() if k != "allowance"}}
    mdfile.write(home.limits, meta, body)
    return dropped


def allowance_table() -> list[str]:
    """One line per level: what each run, chat and interview may use."""
    out = []
    for name, v in ALLOWANCES.items():
        out.append(f"{name}: runs {format_tokens(v['max_tokens'])}, chats {format_tokens(v['chat_max_tokens'])}, "
                   f"interviews {format_tokens(v['interview_max_tokens'])} tokens")
    return out
