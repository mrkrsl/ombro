"""`ombro init`. Part 1 connects a model, by a scripted prompt, because no model
answers yet. Part 2 is the set-up interview with the Lead, which writes
preferences/instructions.md and the Lead's soul.md, then offers to add roles.

Keys are typed into a hidden prompt and written to secrets/keys.env, before the interview
starts. They never reach a model.
"""

from __future__ import annotations

from typing import Callable

from ombro import keys, mdfile
from ombro.client import ModelError, make_client
from ombro.home import Home, ensure_home
from ombro.settings import ModelSpec, SetupError, load_models, load_tool_settings
from ombro.ui import UI, ConsoleUI, Failure, Field, Form, Option, Progress

ENDPOINTS = [
    {
        "label": "DeepSeek",
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key": "DEEPSEEK_API_KEY",
    },
    {"label": "Another OpenAI-compatible endpoint", "endpoint": None, "model": None, "key": None},
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
    ui: UI | None = None,
    ask: Callable[[str], str] | None = None,
    ask_secret: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    client_factory=make_client,
    edit=None,
    next_steps: bool = True,
) -> bool:
    ui = ui or ConsoleUI(ask=ask, ask_secret=ask_secret, say=say, edit=edit, home=home)
    created = ensure_home(home)
    ui.say(f"Ombro home: {home.root}" + (" (created)" if created else ""))
    ask_name(home, ui)

    spec = connected_model(home)
    if spec is not None:
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            ui.say(f"A model is already connected: {spec.model} ({detail}).")
            if not ui.yes("reconnect", "Connect a different model?"):
                _offer_search_key(home, ui)
                _set_up(home, ui, client_factory, next_steps)
                return True
        else:
            ui.say(f"The configured model did not answer: {detail}")

    ui.say("Connect a model. Keys are stored in secrets/keys.env with owner-only permissions and are never sent "
           "to a model.", spaced=True)
    while True:
        endpoint, model, key_name = connect_model(home, ui)
        spec = load_models(home)["default"]
        ui.show(Progress(f"Testing {model} at {endpoint} …"))
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            ui.say(f"Connected: {detail}.", level="done")
            break
        ui.show(Failure(f"The test call failed: {detail}\nLet's try again (Ctrl-C to give up).\n"))

    _offer_search_key(home, ui)
    _set_up(home, ui, client_factory, next_steps)
    return True


def name_field(current: str = "") -> Field:
    return Field("name", "Your name, used only to greet you (Enter to skip)", default=current or None)


def ask_name(home: Home, ui: UI) -> str:
    """The first question of set-up, before any model: what to call you. It can be skipped."""
    from ombro.settings import load_preferences, set_preference

    current = load_preferences(home).name
    name = " ".join(str(ui.ask(name_field(current)) or "").split())[:60]
    if name != current:
        set_preference(home, "name", name)
    return name


def endpoint_field() -> Field:
    return Field(
        "endpoint", "Endpoint", "choice", default="1",
        options=[Option(str(i), e["label"] + (f" ({e['endpoint']})" if e["endpoint"] else ""))
                 for i, e in enumerate(ENDPOINTS, 1)],
        invalid="Please choose one of the numbers shown.",
    )


def model_form(preset: dict) -> Form:
    """What is asked about a model once its endpoint is chosen."""
    fields = []
    if not preset["endpoint"]:
        fields.append(Field("url", "Endpoint base URL (the part before /chat/completions)", required=True,
                            pattern=r"https?://\S+", invalid="The URL must start with http:// or https://."))
    fields.append(Field("model", "Model name", default=preset["model"], required=True,
                        invalid="A model name is needed."))
    if not preset["key"]:
        fields.append(Field("key_name", "Name for its key in secrets/keys.env, e.g. MY_PROVIDER_API_KEY "
                            "(Enter if it needs no key)", pattern=r"[A-Z][A-Z0-9_]*",
                            invalid="Key names use capitals, digits and underscores, starting with a letter."))
    return Form("model", "Connect a model", fields)


def connect_model(home: Home, ui: UI) -> tuple[str, str, str]:
    """Part 1 of set-up, without a model: the endpoint, the model name and its key,
    written to models.md and secrets/keys.env. Returns (endpoint, model, key name)."""
    while True:
        preset = ENDPOINTS[int(ui.ask(endpoint_field())) - 1]
        answers = ui.form(model_form(preset))
        endpoint = preset["endpoint"] or str(answers.get("url") or "").strip()
        model = str(answers.get("model") or "").strip() or preset["model"] or ""
        key_name = preset["key"] or str(answers.get("key_name") or "").strip()
        problems = [p for f in model_form(preset).fields if (p := f.check(answers.get(f.name)))]
        if problems:  # an interface that let a bad answer through
            ui.show(Failure(problems[0]))
            continue
        if key_name:
            existing = keys.get_key(home, key_name)
            hint = " (Enter keeps the current key)" if existing else ""
            value = str(ui.ask(Field("key", f"API key for {key_name}{hint}", "secret")) or "").strip()
            if value:
                keys.set_key(home, key_name, value)
            elif not existing:
                ui.say("A key is needed for this endpoint.")
                continue
        entry: dict = {"api": "openai", "endpoint": endpoint, "model": model}
        if key_name:
            entry["key"] = key_name
        _write_default_model(home, entry)
        return endpoint, model, key_name


def _offer_search_key(home: Home, ui: UI) -> None:
    if keys.get_key(home, SEARCH_KEY):
        ui.say(f"Search key {SEARCH_KEY} is set.")
    else:
        value = str(ui.ask(Field("search_key", f"Search key for Firecrawl, {SEARCH_KEY} (hidden; Enter to skip)",
                                 "secret")) or "").strip()
        if not value:
            ui.say("No search key: web.search uses DuckDuckGo (best effort) and web.fetch downloads pages directly.")
            return
        keys.set_key(home, SEARCH_KEY, value)
        ui.say("Search key saved.", level="done")
    try:
        tools = load_tool_settings(home)
    except (SetupError, mdfile.FrontMatterError):
        return
    if all(tools.provider_for(t) == "firecrawl" for t in ("web.search", "web.fetch")):
        return
    if ui.yes("firecrawl", "Use Firecrawl for web.search and web.fetch? (tools.md)", True):
        set_tool_providers(home, {"web.search": "firecrawl", "web.fetch": "firecrawl"})
        ui.say(f"tools.md now uses Firecrawl for web search and fetch. Edit {home.tools} to change it.")


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


def _set_up(home: Home, ui: UI, client_factory, next_steps: bool = True) -> None:
    """Part 2: the set-up interview, then an offer to add roles. Nothing is written without confirmation."""
    from ombro.interviews import RolePurpose, SetupPurpose, start
    from ombro.settings import load_limits, load_preferences

    crew = load_preferences(home).default_crew
    if not home.crew_dir(crew).is_dir():
        crew = "default"
    limits = load_limits(home)
    kwargs = {"ui": ui, "client_factory": lambda spec: client_factory(spec, home)}
    if _is_default_setup(home):
        ui.say("Next is the set-up interview: the Lead asks who you are, how you work and how you want reports, "
               "then proposes preferences/instructions.md and its own soul.md for you to accept or change.",
               spaced=True)
        go = ui.yes("setup", "Start it now?", True)
    else:
        ui.say("")
        go = ui.yes("setup", "Your preferences are already set. Hold the set-up interview again?")
    if go:
        outcome = start(home, SetupPurpose(home, crew), crew, limits, **kwargs)
        if outcome.confirmed:
            ui.say("Set-up is done. Roles are crew members you can brief for many tasks; "
                   "a task itself is briefed separately.", spaced=True)
            while ui.yes("add_role", "Add a role to the crew now?"):
                start(home, RolePurpose(home, crew, limits), crew, limits, **kwargs)
                limits = load_limits(home)
                ui.say("")
            if ui.yes("brief", "Brief a first task now? It is saved as a spec you can run."):
                from ombro.interviews import BriefPurpose

                brief = start(home, BriefPurpose(home, crew, limits), crew, limits, **kwargs)
                if brief.confirmed:
                    ui.say(f"Spec saved as {brief.proposal.data['name']!r}. "
                           f"Run it with:  ombro run --spec {brief.proposal.data['name']}", spaced=True, level="done")
    if next_steps:
        _next_steps(home, ui)


def _next_steps(home: Home, ui: UI) -> None:
    ui.say(
        "\nNext:\n"
        "  ombro role add          add a role (or `ombro role list` to see the crew)\n"
        "  ombro brief             describe a task; the Lead turns it into a spec\n"
        "  ombro specs             list saved specs, by name\n"
        "  ombro run               brief a task and run it straight away\n"
        "  ombro chat              talk with the Lead about past work (read-only)\n"
        "  ombro limits            see how far runs and interviews may go"
    )
