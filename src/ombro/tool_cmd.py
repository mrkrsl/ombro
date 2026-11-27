"""Changing a native tool from an interface or the command line: which provider backs it
(web.search, web.fetch) and how many calls a job may make. Written to tools.md, keeping
everything else in it. MCP servers are added and removed in mcp_servers; which_kind asks which of the two a new tool is."""

from __future__ import annotations

from ombro import keys, mdfile
from ombro.home import Home
from ombro.settings import PROVIDER_KEYS, PROVIDER_TOOLS, SetupError, load_tool_settings
from ombro.ui import UI, Field, Option

NATIVE = (
    "web.search",
    "web.fetch",
    "workspace.read",
    "workspace.write",
    "workspace.edit",
    "workspace.list",
    "workspace.place",
    "workspace.set_aside",
    "code.run",
    "browser.open",
    "browser.click",
    "browser.type",
    "browser.screenshot",
    "browser.submit",
)
ABOUT = {
    "firecrawl": "Firecrawl: a paid service, needs a key; good results, billed in credits",
    "duckduckgo": "DuckDuckGo: free, no key or browser; unofficial and rate-limited, so best effort",
    "searxng": "SearXNG: free, a SearXNG of your own, by its address (its JSON output turned on)",
    "brave": "Brave Search API: needs a key",
    "tavily": "Tavily: needs a key",
    "exa": "Exa: needs a key",
    "serper": "Serper (Google results): needs a key",
    "custom": "another search API, by its address, with a key if it needs one",
    "jina": "Jina Reader: free, a key optional (for a higher rate limit); reads pages as Markdown",
    "direct": "direct: free, no key; downloads the page itself (refuses local and private addresses)",
    "browser": "browser: free, no key; opens the page in a headless browser on this machine, for pages that need "
    "JavaScript (refuses local and private addresses)",
}
# web.search in the browser: about each engine. Google often turns an automated browser away.
ENGINES = {
    "duckduckgo": "DuckDuckGo",
    "bing": "Bing",
    "ecosia": "Ecosia",
    "startpage": "Startpage",
    "kagi": "Kagi (needs your session link)",
    "brave": "Brave Search",
    "qwant": "Qwant",
    "google": "Google (may not work: it often turns away an automated browser)",
}
LABELS = {
    "firecrawl": "Firecrawl",
    "brave": "Brave Search",
    "tavily": "Tavily",
    "exa": "Exa",
    "serper": "Serper",
    "jina": "Jina Reader",
}


def use_key(home: Home, provider: str) -> None:
    """Name a provider's optional key in tools.md (providers: <name>: key:), so it is sent."""
    from ombro.settings import OPTIONAL_KEYS

    meta, body = mdfile.read(home.tools) if home.tools.is_file() else ({}, "")
    providers = meta.get("providers") if isinstance(meta.get("providers"), dict) else {}
    entry = dict(providers.get(provider) or {})
    entry["key"] = OPTIONAL_KEYS[provider]
    providers[provider] = entry
    meta["providers"] = providers
    mdfile.write(home.tools, meta, body)
    load_tool_settings(home)


def kagi_token(text: str) -> str:
    """Kagi's session token, from its session link (https://kagi.com/search?token=…) or as it is."""
    from urllib.parse import parse_qs, urlsplit

    text = str(text or "").strip()
    if text.startswith(("http://", "https://")):
        token = (parse_qs(urlsplit(text).query).get("token") or [""])[0]
        if not token:
            raise SetupError("that link has no token in it: copy the session link from Kagi's settings")
        return token
    return text


def set_search(
    home: Home,
    provider: str,
    *,
    engine: str | None = None,
    endpoint: str | None = None,
    send_key: str | None = None,
    uses_key: bool | None = None,
) -> None:
    """web.search's own settings in tools.md, for its provider: the browser's search engine (a name
    in SEARCH_ENGINES, or another engine's address), the address of a SearXNG or another search API,
    and how that API is sent its key. Keeps the rest of tools.md; the provider is set by set_tool."""
    from ombro.settings import OPTIONAL_KEYS, SEARCH_ENGINES

    meta, body = mdfile.read(home.tools) if home.tools.is_file() else ({}, "")
    providers = meta.get("providers") if isinstance(meta.get("providers"), dict) else {}
    if provider == "browser" and engine is not None:
        engine = engine.strip()
        if engine not in SEARCH_ENGINES:
            if not engine.startswith(("http://", "https://")):
                raise SetupError("give the search engine's address, starting https://")
            if not urlsplit_host(engine):
                raise SetupError("that address has no site in it")
        entry = dict(providers.get("browser") or {})
        entry["search_engine"] = engine
        providers["browser"] = entry
    if provider in ("searxng", "custom") and endpoint is not None:
        endpoint = endpoint.strip().rstrip("/") if provider == "searxng" else endpoint.strip()
        if not endpoint.startswith(("http://", "https://")) or not urlsplit_host(endpoint):
            raise SetupError("give the address, starting https:// (or http:// on your own network)")
        entry = dict(providers.get(provider) or {})
        entry["endpoint"] = endpoint
        if provider == "custom" and send_key is not None:
            if send_key != "bearer" and not (
                send_key.partition(":")[0] in ("header", "query") and send_key.partition(":")[2].strip()
            ):
                raise SetupError("the key is sent as bearer, header:<Name> or query:<param>")
            entry["send_key"] = send_key
        if uses_key is True:
            entry["key"] = OPTIONAL_KEYS[provider]
        elif uses_key is False:
            entry.pop("key", None)
        providers[provider] = entry
    meta["providers"] = providers
    mdfile.write(home.tools, meta, body)
    load_tool_settings(home)


def urlsplit_host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def is_native(name: str) -> bool:
    return name in NATIVE


def describe(home: Home, tool: str) -> list[str]:
    settings = load_tool_settings(home)
    lines = []
    if tool in PROVIDER_TOOLS:
        p = settings.provider_for(tool)
        lines.append(f"provider: {ABOUT.get(p, p)}")
        key = settings.key_name(p) if p in PROVIDER_KEYS or p in ("searxng", "custom", "jina") else None
        if key:
            lines.append(f"key {key}: {'set' if keys.get_key(home, key) else 'NOT SET'}")
        if tool == "web.search" and p == "browser":
            engine = settings.search_engine()
            lines.append(f"search engine: {ENGINES.get(engine, engine)}")
        if tool == "web.search" and p in ("searxng", "custom"):
            lines.append(f"address: {settings.providers.get(p, {}).get('endpoint') or 'NOT SET'}")
    if tool.startswith("browser."):
        from ombro import browser

        lines.append(f"browser: {browser.describe(settings.providers.get('browser'))}")
    from ombro.settings import load_limits

    lines.append(f"max calls in a job: {load_limits(home, None, settings).max_calls_for(tool)}")
    return lines


def set_browser(home: Home, choice: str) -> None:
    """Settings › General › Browser: "auto" (the first found), "off", the path of one installed
    browser, or ("Other…") the path of another Chromium-family browser on the machine Ombro runs
    on, which is started once to check Ombro can drive it. Written to tools.md as providers:
    browser:, keeping the rest of it."""
    from ombro import browser

    choice = str(choice or "").strip()
    paths = {b["path"] for b in browser.installed()}
    if not choice:
        raise SetupError("choose auto, off, or the path of a browser")
    if choice not in ("auto", "off") and choice not in paths:
        try:
            browser.check_other(choice)
        except browser.BrowserError as e:
            raise SetupError(str(e)) from None
        choice = browser.program(choice)
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
        pick = ui.ask(
            Field(
                "provider",
                f"Which provider should {tool} use?",
                "choice",
                default=str(choices.index(current) + 1) if current in choices else "1",
                options=[Option(str(i), ABOUT.get(p, p)) for i, p in enumerate(choices, 1)],
                invalid="Please choose one of the numbers shown.",
            )
        )
        provider = choices[int(pick) - 1]
    if tool == "web.search" and provider in ("browser", "searxng", "custom") and asked:
        if not _ask_search(home, ui, provider):
            return 1
    if provider and PROVIDER_KEYS.get(provider) and not keys.get_key(home, PROVIDER_KEYS[provider]):
        name = PROVIDER_KEYS[provider]
        value = str(
            ui.ask(
                Field(
                    "key",
                    f"{provider} needs a key ({name}). Paste it now (hidden), or press Enter "
                    "to keep the current provider",
                    "secret",
                )
            )
            or ""
        ).strip()
        if not value:
            ui.say(f"Nothing changed: {provider} has no key.")
            return 1
        keys.set_key(home, name, value)
    if asked:
        raw = str(
            ui.ask(
                Field(
                    "max_calls",
                    f"How many {tool} calls may one job make? (Enter keeps it as it is)",
                    pattern=r"\d*",
                    invalid="A whole number, or Enter.",
                )
            )
            or ""
        ).strip()
        max_calls = int(raw) if raw else None
    try:
        set_tool(home, tool, provider=provider, max_calls=max_calls)
    except SetupError as e:
        ui.say(str(e))
        return 1
    ui.say(f"Saved. {tool} now:", items=describe(home, tool), level="done")
    return 0


def _ask_search(home: Home, ui: UI, provider: str) -> bool:
    """The terminal's way to web.search's own settings: the browser's engine, or an address."""
    from ombro.settings import KAGI_KEY, SEARCH_ENGINES

    settings = load_tool_settings(home)
    try:
        if provider == "browser":
            names = [*SEARCH_ENGINES, "other"]
            current = settings.search_engine()
            pick = ui.ask(
                Field(
                    "engine",
                    "Which search engine should the browser use?",
                    "choice",
                    default=str(names.index(current) + 1) if current in names else str(len(names)),
                    options=[Option(str(i), ENGINES.get(n, "Another, by its address")) for i, n in enumerate(names, 1)],
                    invalid="Please choose one of the numbers shown.",
                )
            )
            engine = names[int(pick) - 1]
            if engine == "other":
                engine = str(ui.ask(Field("address", "The search engine's home page (https://…)")) or "").strip()
            if engine == "kagi" and not keys.get_key(home, KAGI_KEY):
                link = str(
                    ui.ask(
                        Field(
                            "key",
                            "Kagi needs your session link (Kagi › Settings › Account). Paste it (hidden)",
                            "secret",
                        )
                    )
                    or ""
                )
                if not link.strip():
                    ui.say("Nothing changed: Kagi needs your session link.")
                    return False
                keys.set_key(home, KAGI_KEY, kagi_token(link))
            set_search(home, "browser", engine=engine)
        else:
            what = (
                "your SearXNG's address"
                if provider == "searxng"
                else "the search API's address ({query} where the words go)"
            )
            now = settings.providers.get(provider, {}).get("endpoint") or ""
            endpoint = str(ui.ask(Field("address", f"What is {what}?", default=now)) or "").strip()
            set_search(home, provider, endpoint=endpoint)
    except SetupError as e:
        ui.say(str(e))
        return False
    return True


# ---------------------------------------------------------------------------
# Web APIs: `ombro tools add <name>` and `ombro tools edit <name>`, the terminal's way to the
# app's Add a tool › API form. Both go through apitool.entry_from and apitool.save.

API_CLASSES = {
    "read-only": "read-only: it only looks things up",
    "internal": "internal: it changes things in your own tools",
    "external": "external: it can change things or reach outside (its changes wait for your approval)",
}
GET_QUESTION = "Do GET requests to this API only look things up?"
GET_ANSWERS = {True: "only look things up", False: "can send or change things, so they follow what it can do"}
SEND_KEY = {
    "bearer": "as a bearer token (Authorization: Bearer …), the usual way",
    "header": "in a header of its own, such as X-API-Key",
    "query": "in the address, such as ?api_key=…",
}


def _form_of(entry: dict) -> dict:
    """An entry in tools.md as the form's answers, for an edit to start from."""
    how, _, param = str(entry.get("send_key") or "bearer").partition(":")
    return {
        "url": entry.get("url", ""),
        "send_key": how,
        "key_param": param,
        "about": entry.get("about", ""),
        "how_to": entry.get("how_to", ""),
        "access": entry.get("class", "external"),
        "get_looks_up": entry.get("get_looks_up", True),
        "max_calls": entry.get("max_calls"),
    }


def _choice(ui: UI, name: str, label: str, choices: dict[str, str], current: str) -> str:
    keys_ = list(choices)
    pick = ui.ask(
        Field(
            name,
            label,
            "choice",
            default=str(keys_.index(current) + 1) if current in keys_ else "1",
            options=[Option(str(i), text) for i, text in enumerate(choices.values(), 1)],
            invalid="Please choose one of the numbers shown.",
        )
    )
    return keys_[int(pick) - 1]


def api_flow(home: Home, ui: UI, action: str, name: str | None, given: dict, yes: bool = False) -> int:
    """Add or edit an API. What `given` holds (from the command line) is used as it is; with
    nothing given, each setting is asked, the current one as the default."""
    from ombro import apitool

    apis = load_tool_settings(home).apis
    if action == "add":
        name = apitool.clean_name(
            name
            or ui.text(
                "name",
                "What should the API be called? (for example podcasts)",
                required=True,
                invalid="A name is needed.",
            )
        )
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
        form["url"] = ui.text(
            "url",
            "Its address, the part before the paths its docs list (https://…)",
            default=form.get("url") or None,
            required=True,
            pattern=r"https?://\S+",
            invalid="The address must start with https:// (or http:// on your own network).",
        )
    if asking:
        form["about"] = ui.text(
            "about",
            "What it's for, in a line: the Lead decides from this when it helps",
            default=form.get("about") or None,
        )
        form["access"] = _choice(ui, "access", "What can it do?", API_CLASSES, form.get("access", "external"))
        if form["access"] != "read-only":
            form["get_looks_up"] = ui.yes("get_looks_up", GET_QUESTION, form.get("get_looks_up") is not False)
    key_value = ""
    if asking or (old is None and not given.get("no_key")):
        have = bool(old and old.get("key") and keys.get_key(home, old["key"]))
        key_value = str(
            ui.ask(
                Field(
                    "key",
                    "Its key (hidden). " + ("Enter keeps the one set." if have else "Enter if it needs none."),
                    "secret",
                )
            )
            or ""
        ).strip()
    if key_value and asking:
        form["send_key"] = _choice(ui, "send_key", "How does it take the key?", SEND_KEY, form.get("send_key"))
        if form["send_key"] in ("header", "query"):
            form["key_param"] = ui.text(
                "key_param",
                f"What is the {form['send_key']} value called, as its docs say?",
                default=form.get("key_param") or None,
                required=True,
                invalid="A name is needed, such as X-API-Key.",
            )
    form["key"] = key_value
    entry = apitool.entry_from(name, form, old)
    ui.say(
        f"{'Adding' if old is None else 'Changing'} the API {name}:",
        items=[
            f"address: {entry['url']}",
            f"what it can do: {API_CLASSES[entry['class']]}",
            *([f"GET requests: {GET_ANSWERS[apitool.gets_look_up(entry)]}"] if entry["class"] != "read-only" else []),
            f"what it's for: {entry['about'] or '(not said)'}",
            f"key: {entry['key'] + (' (typed now)' if key_value else '') if entry.get('key') else 'none'}"
            + (f", sent {entry['send_key']}" if entry.get("key") else ""),
            f"max calls in a job: {entry.get('max_calls') or apitool.DEFAULT_CALLS}",
        ],
    )
    if not yes and not ui.yes("save", "Save it?", True):
        ui.say("Nothing changed.")
        return 1
    apitool.save(home, name, entry, key_value, old)
    ui.say(
        f"Saved {name} in {home.tools}." + (f" Its key is in secrets/keys.env as {entry['key']}." if key_value else ""),
        level="done",
    )
    if old is None:
        ui.say("Every crew member is offered it from the next job.")
    return 0


def which_kind(ui: UI, api: bool = False, mcp: bool = False) -> str:
    """ "api" or "mcp": what `ombro tools add` and `/tools add` add, from --api or --mcp, or asked."""
    if api and mcp:
        raise SetupError("Say --api or --mcp, not both.")
    if api or mcp:
        return "api" if api else "mcp"
    return str(
        ui.ask(
            Field(
                "kind",
                "What are you adding: a web API, or an MCP server?",
                "choice",
                default="api",
                options=[
                    Option("api", "a web API (an address and a key)", ("a", "web api")),
                    Option("mcp", "an MCP server (a program, or a remote server)", ("m", "server", "mcp server")),
                ],
            )
        )
        or "api"
    )


# -- Composio: what the app's Tools page shows and does, for the command line and the TUI ----------

COMPOSIO_ACTIONS = ("connect", "refresh", "disconnect")
COMPOSIO_ACCESS = {
    "read-only": "only looks things up",
    "internal": "changes things in your own tools",
    "external": "can change things or reach outside",
}


def composio_lines(view: dict | None) -> list[str]:
    """The Composio part of the tools listing: the account, then each service connected in it."""
    if view is None:
        return ["  not connected. Connect your account with `ombro tools composio connect`."]
    if view["problem"]:
        head = f"  Composio account: {view['problem']}"
    elif view["toolkits"]:
        n = len(view["toolkits"])
        head = f"  Composio account: {n} service{'' if n == 1 else 's'} connected"
    else:
        head = "  Composio account: no services connected in Composio yet"
    out = [head]
    access = COMPOSIO_ACCESS.get(view["access"], view["access"])
    for k in view["toolkits"]:
        name = f"{k['title']} (via Composio)"
        if k["active"]:
            what = (f"{k['tools']} tools" if k["tools"] else "connected") + f", {access}"
        else:
            what = f"connection {k['status']}: reconnect it in Composio"
        out.append(f"    {name:<30}{what}")
    out.append(f"  Connect more services in Composio: {view['dashboard']}")
    out.append(
        "  See each Composio tool and its permission with `ombro tools composio`; "
        "or `ombro tools composio refresh`, `connect` (change the key) or `disconnect`."
    )
    return out


def composio_flow(home: Home, ui: UI, action: str, yes: bool = False) -> int:
    """connect (or change the key), refresh, or disconnect the Composio account, as the app's Tools page does."""
    from ombro import composio

    cfg = load_tool_settings(home).composio
    if action == "connect":
        ui.say(
            "Composio connects services such as Google Docs, Gmail and Sheets for you. Paste your project's "
            f"API key (ak_…), from Settings › API Keys in the Composio dashboard: {composio.KEYS_PAGE}. "
            "The ck_ key under For You won't work here. It's checked with Composio before it's kept, and only "
            "ever sent to Composio."
        )
        key = str(ui.ask(Field("key", "Composio API key", "secret")) or "").strip()
        if not key:
            ui.say("Nothing changed.")
            return 1
        composio.connect(home, key)  # SetupError, with Composio's words, when the key doesn't work
        ui.say("Your Composio account is connected." if cfg is None else "Changed your Composio key.", level="done")
    elif cfg is None:
        raise SetupError("Composio isn't connected. Connect your account with `ombro tools composio connect`.")
    elif action == "disconnect":
        ui.say(
            "Every crew member stops being offered the tools from your Composio account. Nothing changes in "
            "Composio itself."
        )
        going, kept = keys.still_used(home, [cfg["key"]], "Composio")
        if going:
            ui.say(f"Its key ({', '.join(going)}) is removed from this computer too.")
        if kept:
            ui.say(f"{', '.join(kept)} stays: something else uses it too.")
        if not yes and not ui.yes("confirm", "Disconnect your Composio account?"):
            ui.say("Nothing changed.")
            return 1
        composio.disconnect(home)
        ui.say("Disconnected your Composio account.", level="done")
        return 0
    elif action != "refresh":
        raise SetupError(f"Say one of {', '.join(COMPOSIO_ACTIONS)}.")
    composio.forget()
    view = composio.view(home, load_tool_settings(home).composio, fresh=True)  # a problem is in the view
    ui.say("Composio:", items=[line.strip() for line in composio_lines(view)[:-1]])
    return 0
