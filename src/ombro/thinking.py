"""How much a model thinks before it answers: one preference for every model, in five words,
put to each provider in the form it takes.

    level     what is sent
    off       DeepSeek: thinking disabled. OpenRouter: reasoning.effort none. Other OpenAI-compatible
              endpoints and the ChatGPT plan: effort none. Anthropic: thinking disabled with effort low,
              or effort low alone on models whose thinking can't be turned off (Opus 5.5, Fable);
              Sonnet 5.5 thinks only between tool calls (between_tools).
    low       effort low everywhere (DeepSeek: low).
    medium    effort medium (DeepSeek has no medium and treats it as high, its own default).
    high      effort high.
    xhigh     effort xhigh (DeepSeek: max; OpenAI-compatible endpoints that lack it refuse it, and the
              call goes without).

Older Claude models that take a thinking budget get one instead (off: none at all). A model that
doesn't think is called without any of it: an endpoint that refuses the field is called again
without it, and Ombro remembers.

Where it comes from, first found: a model's own `thinking:` (or `reasoning_effort:`) in models.md;
`thinking` in preferences/config.md (Settings, `ombro preferences thinking <level>`); else DEFAULT.
A `reasoning_effort` word that isn't a level (a provider's own) is sent as it is.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

LEVELS = ("off", "low", "medium", "high", "xhigh")
LABELS = {"off": "No thinking", "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high"}
DEFAULT = "low"  # 23 Nov 2026: medium (high on DeepSeek) until jobs took 30 minutes on business copy
_ALIASES = {
    "none": "off",
    "no": "off",
    "disabled": "off",
    "false": "off",
    "minimal": "low",
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "x-high": "xhigh",
    "max": "xhigh",
}

# Claude models that take a thinking budget rather than effort (and think only when given one).
_BUDGET_CLAUDE = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-4-0",
    "claude-opus-4-2",
    "claude-sonnet-4-2",
    "claude-3-7",
)
_BUDGETS = {"low": 2_048, "medium": 8_000, "high": 16_000, "xhigh": 32_000}
# Claude models whose thinking can't be turned off: "off" is the least they think, effort low.
_ALWAYS_THINK = ("claude-opus-5-5", "claude-fable", "claude-mythos")
# Models known not to think: nothing is sent.
_NO_THINKING = ("gpt-4o", "gpt-4.1", "gpt-3.5", "gpt-4-")


def level_of(value: Any) -> str | None:
    """One of LEVELS for a word as people or providers write it, or None."""
    if value is None or value is True:
        return None
    if value is False:  # YAML reads `thinking: off` (or no) as false
        return "off"
    word = " ".join(str(value).strip().lower().split())
    word = _ALIASES.get(word, word)
    return word if word in LEVELS else None


def preference(home) -> str:
    """The level set in Settings (preferences/config.md), or DEFAULT."""
    from ombro.settings import load_preferences

    try:
        return level_of(load_preferences(home).thinking) or DEFAULT
    except Exception:  # noqa: BLE001 - a broken preferences file must not stop a model call
        return DEFAULT


def for_spec(spec, home) -> tuple[str | None, str | None]:
    """(level, raw): the level for this model, and a provider's own word from models.md to send as it is
    (then level is None)."""
    own = getattr(spec, "thinking", None) or getattr(spec, "reasoning_effort", None)
    if own:
        lv = level_of(own)
        return (lv, None) if lv else (None, str(own).lower())
    return (preference(home) if home is not None else DEFAULT), None


def _host(endpoint: str | None) -> str:
    return (urlparse(endpoint or "").hostname or "").lower()


def openai_fields(level: str | None, endpoint: str | None, model: str) -> dict[str, Any]:
    """Request fields for an OpenAI-compatible endpoint (/chat/completions)."""
    if not level:
        return {}
    host, m = _host(endpoint), (model or "").lower()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return {"reasoning": {"effort": "none" if level == "off" else level}}
    if host == "deepseek.com" or host.endswith(".deepseek.com"):
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        return {"reasoning_effort": {"low": "low", "medium": "high", "high": "high", "xhigh": "max"}[level]}
    if any(m.startswith(p) for p in _NO_THINKING):
        return {}
    return {"reasoning_effort": "none" if level == "off" else level}


def anthropic_fields(level: str | None, model: str, max_tokens: int) -> dict[str, Any]:
    """Request fields for Anthropic's Messages API."""
    if not level:
        return {}
    m = (model or "").lower()
    if any(m.startswith(p) for p in _BUDGET_CLAUDE):
        if level == "off":
            return {}
        budget = min(_BUDGETS[level], max_tokens - 1_024)
        return {"thinking": {"type": "enabled", "budget_tokens": budget}} if budget >= 1_024 else {}
    if level != "off":
        return {"output_config": {"effort": level}}
    if any(m.startswith(p) for p in _ALWAYS_THINK):
        return {"output_config": {"effort": "low"}}
    if m.startswith("claude-sonnet-5-5"):
        return {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
    return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}


def responses_effort(level: str | None) -> str | None:
    """reasoning.effort for OpenAI's Responses API (the ChatGPT plan)."""
    if not level:
        return None
    return "none" if level == "off" else level


def describe(home) -> list[dict[str, str]]:
    """Each model in models.md with the level it uses and where that comes from, for `ombro preferences`."""
    from ombro.settings import SetupError, load_models

    try:
        models = load_models(home)
    except SetupError:
        return []
    pref = preference(home)
    out = []
    for name, spec in models.items():
        own = getattr(spec, "thinking", None) or getattr(spec, "reasoning_effort", None)
        lv, raw = for_spec(spec, home)
        out.append({"model": name, "level": lv or raw or pref, "from": "models.md" if own else "your preference"})
    return out
