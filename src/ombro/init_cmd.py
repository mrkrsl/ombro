"""`ombro init`. Part 1 connects a model, by a scripted prompt, because no model
answers yet. Part 2 is the set-up interview with the Lead, which writes
preferences/instructions.md and the Lead's soul.md, then offers to add roles.

Keys are typed into a hidden prompt and written to .env, before the interview
starts. They never reach a model.
"""

from __future__ import annotations

import getpass
from typing import Callable

from ombro import keys, mdfile
from ombro.client import ModelError, make_client
from ombro.home import Home, ensure_home
from ombro.settings import ModelSpec, SetupError, load_models, load_tool_settings

ENDPOINTS = [
    {
        "label": "DeepSeek",
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key": "DEEPSEEK_API_KEY",
        "price": {"input": 0.30, "output": 1.20},
    },
    {"label": "Another OpenAI-compatible endpoint", "endpoint": None, "model": None, "key": None, "price": None},
]
SEARCH_KEY = "FIRECRAWL_API_KEY"
TEST_PROMPT = [{"role": "user", "content": "Reply with the single word OK."}]


def test_connection(home: Home, spec: ModelSpec, client_factory=make_client) -> tuple[bool, str]:
    try:
        reply = client_factory(spec, home).complete(TEST_PROMPT, tools=None, max_output_tokens=16, timeout=60)
    except ModelError as e:
        return False, str(e)
    text = (reply.content or "").strip()
    return True, f"the model replied {text[:40]!r}" if text else "the model replied"


def connected_model(home: Home) -> ModelSpec | None:
    """The default model if it is configured and has its key. (Does not call it.)"""
    try:
        spec = load_models(home).get("default")
    except (SetupError, mdfile.FrontMatterError):
        return None
    if spec is None:
        return None
    if spec.api == "openai" and spec.key and not keys.get_key(home, spec.key):
        return None
    return spec


def _write_default_model(home: Home, entry: dict) -> None:
    meta, body = mdfile.read(home.models)
    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    models["default"] = entry
    models.setdefault("cheap", "default")
    meta["models"] = models
    mdfile.write(home.models, meta, body)


def run_init(
    home: Home,
    *,
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    say: Callable[[str], None] = print,
    client_factory=make_client,
    edit=None,
) -> bool:
    created = ensure_home(home)
    say(f"Ombro home: {home.root}" + (" (created)" if created else ""))

    spec = connected_model(home)
    if spec is not None:
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            say(f"A model is already connected: {spec.model} ({detail}).")
            if ask("Connect a different model? [y/N] ").strip().lower() not in ("y", "yes"):
                _offer_search_key(home, ask, ask_secret, say)
                _set_up(home, ask, say, client_factory, edit)
                return True
        else:
            say(f"The configured model did not answer: {detail}")

    say("\nConnect a model. Keys are stored in .env with owner-only permissions and are never sent to a model.")
    while True:
        say("Endpoint:")
        for i, e in enumerate(ENDPOINTS, 1):
            say(f"  {i}) {e['label']}" + (f" ({e['endpoint']})" if e["endpoint"] else ""))
        choice = ask("Choose [1]: ").strip() or "1"
        if not choice.isdigit() or not 1 <= int(choice) <= len(ENDPOINTS):
            say("Please choose one of the numbers shown.")
            continue
        preset = ENDPOINTS[int(choice) - 1]

        endpoint = preset["endpoint"]
        if not endpoint:
            endpoint = ask("Endpoint base URL (the part before /chat/completions): ").strip()
            if not endpoint.startswith(("http://", "https://")):
                say("The URL must start with http:// or https://.")
                continue
        model = ask(f"Model name [{preset['model']}]: " if preset["model"] else "Model name: ").strip()
        model = model or preset["model"] or ""
        if not model:
            say("A model name is needed.")
            continue
        key_name = preset["key"]
        if not key_name:
            key_name = ask("Name for its key in .env, e.g. MY_PROVIDER_API_KEY (Enter if it needs no key): ").strip()
            if key_name and not keys.valid_name(key_name):
                say("Key names use capitals, digits and underscores, starting with a letter.")
                continue
        if key_name:
            existing = keys.get_key(home, key_name)
            hint = " (Enter keeps the current key)" if existing else ""
            value = ask_secret(f"API key for {key_name}{hint}: ").strip()
            if value:
                keys.set_key(home, key_name, value)
            elif not existing:
                say("A key is needed for this endpoint.")
                continue

        entry: dict = {"api": "openai", "endpoint": endpoint, "model": model}
        if key_name:
            entry["key"] = key_name
        if preset["price"] and model == preset["model"]:
            entry["price_per_mtok"] = dict(preset["price"])
        _write_default_model(home, entry)

        spec = load_models(home)["default"]
        say(f"Testing {model} at {endpoint} …")
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            say(f"Connected: {detail}.")
            if spec.priced:
                say(
                    f"Prices in models.md (for cost estimates only): ${spec.price_in}/M input, "
                    f"${spec.price_out}/M output tokens."
                )
            break
        say(f"The test call failed: {detail}\nLet's try again (Ctrl-C to give up).\n")

    _offer_search_key(home, ask, ask_secret, say)
    _set_up(home, ask, say, client_factory, edit)
    return True


def _offer_search_key(home: Home, ask, ask_secret, say) -> None:
    if keys.get_key(home, SEARCH_KEY):
        say(f"Search key {SEARCH_KEY} is set.")
    else:
        value = ask_secret(f"Search key for Firecrawl, {SEARCH_KEY} (hidden; Enter to skip): ").strip()
        if not value:
            say("No search key: web.search uses DuckDuckGo (best effort) and web.fetch downloads pages directly.")
            return
        keys.set_key(home, SEARCH_KEY, value)
        say("Search key saved.")
    try:
        tools = load_tool_settings(home)
    except (SetupError, mdfile.FrontMatterError):
        return
    if all(tools.provider_for(t) == "firecrawl" for t in ("web.search", "web.fetch")):
        return
    if ask("Use Firecrawl for web.search and web.fetch? (tools.md) [Y/n] ").strip().lower() in ("", "y", "yes"):
        set_tool_providers(home, {"web.search": "firecrawl", "web.fetch": "firecrawl"})
        say(f"tools.md now uses Firecrawl for web search and fetch. Edit {home.tools} to change it.")


def set_tool_providers(home: Home, providers: dict[str, str]) -> None:
    """Set the provider for some tools in tools.md, keeping everything else in it."""
    meta, body = mdfile.read(home.tools) if home.tools.is_file() else ({}, "")
    tools = meta.get("tools") if isinstance(meta.get("tools"), dict) else {}
    for tool, provider in providers.items():
        entry = tools.get(tool) if isinstance(tools.get(tool), dict) else {}
        entry["provider"] = provider
        tools[tool] = entry
    meta["tools"] = tools
    mdfile.write(home.tools, meta, body)


def _is_default_setup(home: Home) -> bool:
    if home.instructions.is_file() and mdfile.prompt_text(mdfile.read(home.instructions)[1]):
        return False
    return True


def _yes(answer: str, default: bool) -> bool:
    a = answer.strip().lower()
    return default if not a else a in ("y", "yes")


def _set_up(home: Home, ask, say, client_factory, edit) -> None:
    """Part 2: the set-up interview, then an offer to add roles. Nothing is written without confirmation."""
    from ombro.interviews import RolePurpose, SetupPurpose, start
    from ombro.settings import load_limits, load_preferences

    crew = load_preferences(home).default_crew
    if not home.crew_dir(crew).is_dir():
        crew = "default"
    limits = load_limits(home)
    kwargs = {"ask": ask, "say": say, "edit": edit, "client_factory": lambda spec: client_factory(spec, home)}
    if _is_default_setup(home):
        say("\nNext is the set-up interview: the Lead asks who you are, how you work and how you want reports, "
            "then proposes preferences/instructions.md and its own soul.md for you to accept or change.")
        go = _yes(ask("Start it now? [Y/n] "), True)
    else:
        go = _yes(ask("\nYour preferences are already set. Hold the set-up interview again? [y/N] "), False)
    if go:
        outcome = start(home, SetupPurpose(home, crew), crew, limits, **kwargs)
        if outcome.confirmed:
            say("\nSet-up is done. Roles are crew members you can brief for many tasks; "
                "a task itself is briefed separately.")
            while _yes(ask("Add a role to the crew now? [y/N] "), False):
                start(home, RolePurpose(home, crew, limits), crew, limits, **kwargs)
                limits = load_limits(home)
                say("")
            if _yes(ask("Brief a first task now? It is saved as a spec you can run. [y/N] "), False):
                from ombro.interviews import BriefPurpose

                brief = start(home, BriefPurpose(home, crew, limits), crew, limits, **kwargs)
                if brief.confirmed:
                    say(f"\nSpec saved as {brief.proposal.data['name']!r}. "
                        f"Run it with:  ombro run --spec {brief.proposal.data['name']}")
    _next_steps(home, say)


def _next_steps(home: Home, say) -> None:
    say(
        "\nNext:\n"
        "  ombro role add          add a role (or `ombro role list` to see the crew)\n"
        "  ombro brief             describe a task; the Lead turns it into a spec\n"
        "  ombro specs             list saved specs, by name\n"
        "  ombro run               brief a task and run it straight away\n"
        "  ombro limits            see how far runs and interviews may go"
    )
