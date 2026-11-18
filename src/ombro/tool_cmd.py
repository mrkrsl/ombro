"""Changing a native tool from an interface or the command line: which provider backs it
(web.search, web.fetch) and how many calls a job may make. Written to tools.md, keeping
everything else in it. MCP servers are added and removed elsewhere (server_cmd)."""

from __future__ import annotations

from ombro import keys, mdfile
from ombro.home import Home
from ombro.settings import PROVIDER_KEYS, PROVIDER_TOOLS, SetupError, load_tool_settings
from ombro.ui import UI, Field, Option

NATIVE = ("web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list", "workspace.place",
          "workspace.set_aside", "code.run", "browser.open", "browser.click", "browser.type", "browser.screenshot",
          "browser.submit")
ABOUT = {
    "firecrawl": "Firecrawl: a paid service, needs a key; good results, billed in credits",
    "duckduckgo": "DuckDuckGo: free, no key; unofficial and rate-limited, so best effort",
    "direct": "direct: free, no key; downloads the page itself (refuses local and private addresses)",
    "browser": "browser: free, no key; opens the page in a headless browser on this machine, for pages that need "
               "JavaScript (refuses local and private addresses)",
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
    if tool.startswith("browser."):
        from ombro import browser

        lines.append(f"browser: {browser.describe(settings.providers.get('browser'))}")
    from ombro.settings import load_limits

    lines.append(f"max calls in a job: {load_limits(home, None, settings).max_calls_for(tool)}")
    return lines


def set_browser(home: Home, choice: str) -> None:
    """Settings › General › Browser: "auto" (the first found), "off", or the path of one installed
    browser. Written to tools.md as providers: browser:, keeping the rest of it."""
    from ombro import browser

    choice = str(choice or "").strip()
    paths = {b["path"] for b in browser.installed()}
    if choice not in ("auto", "off") and choice not in paths:
        raise SetupError(f"{choice!r} is not a browser on this machine; choose auto, off or one of: "
                         + (", ".join(sorted(paths)) or "none found"))
    meta, body = mdfile.read(home.tools) if home.tools.is_file() else ({}, "")
    providers = meta.get("providers") if isinstance(meta.get("providers"), dict) else {}
    entry = {k: v for k, v in (providers.get("browser") or {}).items() if k not in ("path", "off")}
    if choice == "off":
        entry["off"] = True
    elif choice != "auto":
        entry["path"] = choice
    if entry:
        providers["browser"] = entry
    else:
        providers.pop("browser", None)
    meta["providers"] = providers
    mdfile.write(home.tools, meta, body)
    load_tool_settings(home)


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


# ---------------------------------------------------------------------------
# Web APIs: `ombro tools add <name>` and `ombro tools edit <name>`, the terminal's way to the
# app's Add a tool › API form. Both go through apitool.entry_from and apitool.save.

API_CLASSES = {
    "read-only": "read-only: it only looks things up",
    "internal": "internal: it changes things in your own tools",
    "external": "external: it can change things or reach outside (its changes wait for your approval)",
}
SEND_KEY = {"bearer": "as a bearer token (Authorization: Bearer …), the usual way",
            "header": "in a header of its own, such as X-API-Key",
            "query": "in the address, such as ?api_key=…"}


def _form_of(entry: dict) -> dict:
    """An entry in tools.md as the form's answers, for an edit to start from."""
    how, _, param = str(entry.get("send_key") or "bearer").partition(":")
    return {"url": entry.get("url", ""), "send_key": how, "key_param": param, "about": entry.get("about", ""),
            "how_to": entry.get("how_to", ""), "access": entry.get("class", "external"),
            "max_calls": entry.get("max_calls")}


def _choice(ui: UI, name: str, label: str, choices: dict[str, str], current: str) -> str:
    keys_ = list(choices)
    pick = ui.ask(Field(name, label, "choice", default=str(keys_.index(current) + 1) if current in keys_ else "1",
                        options=[Option(str(i), text) for i, text in enumerate(choices.values(), 1)],
                        invalid="Please choose one of the numbers shown."))
    return keys_[int(pick) - 1]


def api_flow(home: Home, ui: UI, action: str, name: str | None, given: dict, yes: bool = False) -> int:
    """Add or edit an API. What `given` holds (from the command line) is used as it is; with
    nothing given, each setting is asked, the current one as the default."""
    from ombro import apitool

    apis = load_tool_settings(home).apis
    if action == "add":
        name = apitool.clean_name(name or ui.text("name", "What should the API be called? (for example podcasts)",
                                                  required=True, invalid="A name is needed."))
        if name in apis or name in load_tool_settings(home).servers:
            raise SetupError(f"There's already a tool called {name}. Change it with `ombro tools edit {name}`.")
        old = None
        form: dict = {"send_key": "bearer", "access": "external", "max_calls": apitool.DEFAULT_CALLS}
    else:
        if not name:
            raise SetupError("Name the API, e.g. `ombro tools edit podcasts`.")
        old = apis.get(name)
        if old is None:
            raise SetupError(f"There's no API called {name}. APIs: {', '.join(apis) or 'none yet'}.")
        form = _form_of(old)
    asking = not any(v is not None and v is not False for v in given.values())
    form.update({k: v for k, v in given.items() if v is not None and k != "send_key"})
    if given.get("send_key"):
        how, _, param = str(given["send_key"]).partition(":")
        form["send_key"], form["key_param"] = how, param or form.get("key_param", "")
    if asking or (old is None and not form.get("url")):
        form["url"] = ui.text("url", "Its address, the part before the paths its docs list (https://…)",
                              default=form.get("url") or None, required=True, pattern=r"https?://\S+",
                              invalid="The address must start with https:// (or http:// on your own network).")
    if asking:
        form["about"] = ui.text("about", "What it's for, in a line: the Lead decides from this when it helps",
                                default=form.get("about") or None)
        form["access"] = _choice(ui, "access", "What can it do?", API_CLASSES, form.get("access", "external"))
    key_value = ""
    if asking or (old is None and not given.get("no_key")):
        have = bool(old and old.get("key") and keys.get_key(home, old["key"]))
        key_value = str(ui.ask(Field("key", "Its key (hidden). " + ("Enter keeps the one set." if have else
                                     "Enter if it needs none."), "secret")) or "").strip()
    if key_value and asking:
        form["send_key"] = _choice(ui, "send_key", "How does it take the key?", SEND_KEY, form.get("send_key"))
        if form["send_key"] in ("header", "query"):
            form["key_param"] = ui.text("key_param", f"What is the {form['send_key']} value called, as its docs say?",
                                        default=form.get("key_param") or None, required=True,
                                        invalid="A name is needed, such as X-API-Key.")
    form["key"] = key_value
    entry = apitool.entry_from(name, form, old)
    ui.say(f"{'Adding' if old is None else 'Changing'} the API {name}:", items=[
        f"address: {entry['url']}", f"what it can do: {API_CLASSES[entry['class']]}",
        f"what it's for: {entry['about'] or '(not said)'}",
        f"key: {entry['key'] + (' (typed now)' if key_value else '') if entry.get('key') else 'none'}"
        + (f", sent {entry['send_key']}" if entry.get("key") else ""),
        f"max calls in a job: {entry.get('max_calls') or apitool.DEFAULT_CALLS}"])
    if not yes and not ui.yes("save", "Save it?", True):
        ui.say("Nothing changed.")
        return 1
    apitool.save(home, name, entry, key_value, old)
    ui.say(f"Saved {name} in {home.tools}." + (f" Its key is in secrets/keys.env as {entry['key']}."
                                                 if key_value else ""), level="done")
    if old is None:
        ui.say("Every crew member is offered it from the next job.")
    return 0
