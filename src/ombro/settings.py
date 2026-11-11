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


@dataclass(frozen=True)
class Limits:
    budget_usd: float
    max_tokens: int
    max_rounds: int
    run_timeout: float
    max_output_tokens: int
    report_reserve: float
    loop_threshold: int
    max_steps: dict[str, int] = field(default_factory=dict)
    tool_max_calls: dict[str, int] = field(default_factory=dict)
    report_max_output_tokens: int = 2000
    report_timeout: float = 120.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Limits":
        known = set(cls.__dataclass_fields__)
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

        def int_map(name: str, v: Any) -> dict[str, int]:
            if not isinstance(v, dict):
                raise SetupError(f"limit {name} must be a mapping")
            return {str(k): pos_num(f"{name}.{k}", n, integer=True) for k, n in v.items()}

        reserve = d["report_reserve"]
        if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not 0 <= reserve <= 0.5:
            raise SetupError("limit report_reserve must be between 0 and 0.5")
        tool_calls = int_map("tool_max_calls", d["tool_max_calls"])
        tool_calls.setdefault("default", 20)
        return cls(
            budget_usd=pos_num("budget_usd", d["budget_usd"]),
            max_tokens=pos_num("max_tokens", d["max_tokens"], integer=True),
            max_rounds=pos_num("max_rounds", d["max_rounds"], integer=True),
            run_timeout=pos_num("run_timeout", parse_duration(d["run_timeout"])),
            max_output_tokens=pos_num("max_output_tokens", d["max_output_tokens"], integer=True),
            report_reserve=float(reserve),
            loop_threshold=pos_num("loop_threshold", d["loop_threshold"], integer=True),
            max_steps=int_map("max_steps", d["max_steps"]),
            tool_max_calls=tool_calls,
            report_max_output_tokens=pos_num("report_max_output_tokens", d["report_max_output_tokens"], integer=True),
            report_timeout=pos_num("report_timeout", parse_duration(d["report_timeout"])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def max_calls_for(self, tool: str) -> int:
        return self.tool_max_calls.get(tool, self.tool_max_calls["default"])


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_limits(home: Home, overrides: dict | None = None) -> Limits:
    meta = mdfile.read(home.limits)[0] if home.limits.is_file() else {}
    merged = _deep_merge(_deep_merge(LIMITS_META, meta), overrides or {})
    return Limits.from_dict(merged)


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
