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
    "conservative": {"max_tokens": 150_000, "max_rounds": 4, "run_timeout": "15m", "interview_max_tokens": 40_000},
    "moderate": {"max_tokens": 400_000, "max_rounds": 8, "run_timeout": "30m", "interview_max_tokens": 100_000},
    "generous": {"max_tokens": 1_500_000, "max_rounds": 16, "run_timeout": "1h", "interview_max_tokens": 250_000},
    "unlimited": {"max_tokens": None, "max_rounds": 50, "run_timeout": "4h", "interview_max_tokens": None},
}
DEFAULT_ALLOWANCE = "moderate"
LEVEL_KEYS = ("max_tokens", "max_rounds", "run_timeout", "interview_max_tokens")
# Settings from earlier versions that are accepted and ignored.
RETIRED_LIMITS = ("budget_usd",)
INTERVIEW_TIMEOUT = 6 * 3600.0  # interviews wait on a person; the token and turn caps bound them


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
    tool_cost_per_call: dict[str, float] = field(default_factory=dict)
    report_max_output_tokens: int = 2000
    report_timeout: float = 120.0
    interview_max_tokens: int | None = 100_000
    interview_max_turns: int = 40

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Limits":
        known = set(cls.__dataclass_fields__)
        # Absent from runs made before they existed.
        d = {
            "tool_cost_per_call": {},
            "allowance": "custom",
            "interview_max_tokens": ALLOWANCES[DEFAULT_ALLOWANCE]["interview_max_tokens"],
            "interview_max_turns": 40,
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
        costs = d["tool_cost_per_call"]
        if not isinstance(costs, dict):
            raise SetupError("limit tool_cost_per_call must be a mapping")
        for k, v in costs.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise SetupError(f"cost_per_call for {k} must be zero or a positive number of dollars, not {v!r}")
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
            tool_cost_per_call={str(k): float(v) for k, v in costs.items()},
            report_max_output_tokens=pos_num("report_max_output_tokens", d["report_max_output_tokens"], integer=True),
            report_timeout=pos_num("report_timeout", parse_duration(d["report_timeout"])),
            interview_max_tokens=token_cap("interview_max_tokens", d["interview_max_tokens"]),
            interview_max_turns=pos_num("interview_max_turns", d["interview_max_turns"], integer=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def max_calls_for(self, tool: str) -> int:
        return self.tool_max_calls.get(tool, self.tool_max_calls["default"])

    def cost_for(self, tool: str) -> float:
        return self.tool_cost_per_call.get(tool, 0.0)

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


def load_limits(home: Home, overrides: dict | None = None, tools: "ToolSettings | None" = None) -> Limits:
    """limits.md, then per-tool caps and costs from tools.md, then a spec's overrides."""
    meta = mdfile.read(home.limits)[0] if home.limits.is_file() else {}
    merged = _layer(copy.deepcopy(LIMITS_META), meta)
    tools = tools if tools is not None else load_tool_settings(home)
    per_tool: dict[str, dict] = {"tool_max_calls": {}, "tool_cost_per_call": {}}
    for name, cfg in tools.tools.items():
        if "max_calls" in cfg:
            per_tool["tool_max_calls"][name] = cfg["max_calls"]
        if "cost_per_call" in cfg:
            per_tool["tool_cost_per_call"][name] = cfg["cost_per_call"]
    merged = _layer(_layer(merged, per_tool), overrides or {})
    return Limits.from_dict(resolve_allowance(merged))


# ---------------------------------------------------------------------------
# Tools (tools.md)

# The native tools that use a provider, the providers each may use, and the default.
PROVIDER_TOOLS: dict[str, tuple[tuple[str, ...], str]] = {
    "web.search": (("firecrawl", "duckduckgo", "fake"), "duckduckgo"),
    "web.fetch": (("firecrawl", "direct", "fake"), "direct"),
}
PROVIDER_KEYS = {"firecrawl": "FIRECRAWL_API_KEY"}
_TOOL_FIELDS = {"provider", "max_calls", "cost_per_call"}


@dataclass(frozen=True)
class ToolSettings:
    """Per-tool settings from tools.md. `tools` maps a tool name to its settings;
    `providers` maps a provider name to its own settings (such as which key it uses)."""

    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, where: str = "tools.md") -> "ToolSettings":
        d = d or {}
        unknown = set(d) - {"tools", "providers"}
        if unknown:
            raise SetupError(f"{where}: unknown setting(s): {', '.join(sorted(unknown))} (use tools: and providers:)")
        raw_tools = d.get("tools") or {}
        raw_providers = d.get("providers") or {}
        if not isinstance(raw_tools, dict) or not isinstance(raw_providers, dict):
            raise SetupError(f"{where}: `tools` and `providers` must be mappings")
        tools: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_tools.items():
            name = str(name)
            cfg = cfg or {}
            if not isinstance(cfg, dict):
                raise SetupError(f"{where}: settings for {name} must be a mapping, e.g. {{provider: fake, max_calls: 10}}")
            bad = set(cfg) - _TOOL_FIELDS
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
            if "cost_per_call" in cfg:
                v = cfg["cost_per_call"]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                    raise SetupError(f"{where}: cost_per_call for {name} must be zero or more US dollars")
                out["cost_per_call"] = float(v)
            tools[name] = out
        providers: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_providers.items():
            if not isinstance(cfg, dict):
                raise SetupError(f"{where}: settings for provider {name} must be a mapping")
            providers[str(name)] = dict(cfg)
        return cls(tools=tools, providers=providers)

    def to_dict(self) -> dict[str, Any]:
        return {"tools": copy.deepcopy(self.tools), "providers": copy.deepcopy(self.providers)}

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
    price_in: float | None = None
    price_out: float | None = None
    script: str | None = None

    @property
    def priced(self) -> bool:
        return self.price_in is not None and self.price_out is not None


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
        price = entry.get("price_per_mtok") or {}
        p_in, p_out = price.get("input"), price.get("output")
        for label, p in (("input", p_in), ("output", p_out)):
            if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float)) or p < 0):
                raise SetupError(f"{home.models}: model {name!r} has an invalid {label} price")
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
            price_in=float(p_in) if p_in is not None else None,
            price_out=float(p_out) if p_out is not None else None,
            script=entry.get("script"),
        )

    return {name: resolve(name) for name in raw}


# ---------------------------------------------------------------------------
# Preferences


@dataclass
class Preferences:
    default_crew: str = DEFAULT_CREW
    editor: str = ""
    display_currency: dict | None = None
    instructions: str = ""


def load_preferences(home: Home) -> Preferences:
    prefs = Preferences()
    if home.preferences_config.is_file():
        meta, _ = mdfile.read(home.preferences_config)
        prefs.default_crew = str(meta.get("default_crew") or DEFAULT_CREW)
        prefs.editor = str(meta.get("editor") or "")
        cur = meta.get("display_currency")
        if isinstance(cur, dict) and cur.get("code") and isinstance(cur.get("rate"), (int, float)):
            prefs.display_currency = {"code": str(cur["code"]), "rate": float(cur["rate"])}
    if home.instructions.is_file():
        _, body = mdfile.read(home.instructions)
        prefs.instructions = mdfile.prompt_text(body)
    return prefs
