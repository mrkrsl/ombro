"""Changing a native tool from an interface or the command line: which provider backs it
(web.search, web.fetch) and how many calls a job may make. Written to tools.md, keeping
everything else in it. MCP servers are added and removed elsewhere (server_cmd)."""

from __future__ import annotations

from ombro import keys, mdfile
from ombro.home import Home
from ombro.settings import PROVIDER_KEYS, PROVIDER_TOOLS, SetupError, load_tool_settings
from ombro.ui import UI, Field, Option

NATIVE = ("web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list", "workspace.place",
          "workspace.set_aside", "code.run")
ABOUT = {
    "firecrawl": "Firecrawl: a paid service, needs a key; good results, billed in credits",
    "duckduckgo": "DuckDuckGo: free, no key; unofficial and rate-limited, so best effort",
    "direct": "direct: free, no key; downloads the page itself (refuses local and private addresses)",
}


def is_native(name: str) -> bool:
    return name in NATIVE


def describe(home: Home, tool: str) -> list[str]:
    settings = load_tool_settings(home)
    lines = []
    if tool in PROVIDER_TOOLS:
        p = settings.provider_for(tool)
        lines.append(f"provider: {ABOUT.get(p, p)}")
        key = PROVIDER_KEYS.get(p)
        if key:
            lines.append(f"key {key}: {'set' if keys.get_key(home, key) else 'NOT SET'}")
    from ombro.settings import load_limits

    lines.append(f"max calls in a job: {load_limits(home, None, settings).max_calls_for(tool)}")
    return lines


def set_tool(home: Home, tool: str, *, provider: str | None = None, max_calls: int | None = None) -> None:
    """Set a native tool's provider and/or max_calls in tools.md, keeping the rest."""
    if not is_native(tool):
        raise SetupError(f"{tool} is not a built-in tool. Built-in tools: {', '.join(NATIVE)}")
    if provider is not None:
        allowed = [p for p in PROVIDER_TOOLS.get(tool, ((), ""))[0] if p != "fake"]
        if provider not in allowed:
            raise SetupError(f"{tool} can use: {', '.join(allowed) or 'no provider'}")
    if max_calls is not None and (isinstance(max_calls, bool) or int(max_calls) < 1):
        raise SetupError("max calls must be a whole number, 1 or more")
    meta, body = mdfile.read(home.tools) if home.tools.is_file() else ({}, "")
    tools = meta.get("tools") if isinstance(meta.get("tools"), dict) else {}
    entry = dict(tools.get(tool) or {}) if isinstance(tools.get(tool), dict) else {}
    if provider is not None:
        entry["provider"] = provider
    if max_calls is not None:
        entry["max_calls"] = int(max_calls)
    tools[tool] = entry
    meta["tools"] = tools
    mdfile.write(home.tools, meta, body)
    load_tool_settings(home)  # refuse nothing silently: a bad file shows at once


def flow(home: Home, ui: UI, tool: str, provider: str | None = None, max_calls: int | None = None) -> int:
    """Show a built-in tool, then change what is given, or ask (provider, then calls)."""
    ui.say(f"{tool}:", items=describe(home, tool))
    asked = provider is None and max_calls is None
    if asked and tool in PROVIDER_TOOLS:
        current = load_tool_settings(home).provider_for(tool)
        choices = [p for p in PROVIDER_TOOLS[tool][0] if p != "fake"]
        pick = ui.ask(Field("provider", f"Which provider should {tool} use?", "choice",
                            default=str(choices.index(current) + 1) if current in choices else "1",
                            options=[Option(str(i), ABOUT.get(p, p)) for i, p in enumerate(choices, 1)],
                            invalid="Please choose one of the numbers shown."))
        provider = choices[int(pick) - 1]
    if provider and PROVIDER_KEYS.get(provider) and not keys.get_key(home, PROVIDER_KEYS[provider]):
        name = PROVIDER_KEYS[provider]
        value = str(ui.ask(Field("key", f"{provider} needs a key ({name}). Paste it now (hidden), or press Enter "
                                 "to keep the current provider", "secret")) or "").strip()
        if not value:
            ui.say(f"Nothing changed: {provider} has no key.")
            return 1
        keys.set_key(home, name, value)
    if asked:
        raw = str(ui.ask(Field("max_calls", f"How many {tool} calls may one job make? (Enter keeps it as it is)",
                               pattern=r"\d*", invalid="A whole number, or Enter.")) or "").strip()
        max_calls = int(raw) if raw else None
    try:
        set_tool(home, tool, provider=provider, max_calls=max_calls)
    except SetupError as e:
        ui.say(str(e))
        return 1
    ui.say(f"Saved. {tool} now:", items=describe(home, tool), level="done")
    return 0
