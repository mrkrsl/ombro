"""`ombro init`. Part 1 connects a model, by a scripted prompt, because no model
answers yet. Part 2 is the set-up interview with the Lead, which writes
preferences/instructions.md and the Lead's soul.md, then offers to add roles.

Keys are typed into a hidden prompt and written to secrets/keys.env, before the interview
starts. They never reach a model.
"""

from __future__ import annotations

from typing import Callable

from ombro import keys, mdfile
from ombro.client import ModelError, api_for, make_client
from ombro.home import Home, ensure_home
from ombro.models import PRESETS, chat_models
from ombro.settings import ModelSpec, SetupError, load_models, load_tool_settings
from ombro.ui import UI, ConsoleUI, Failure, Field, Form, Option, Progress

ENDPOINTS = [*PRESETS, {"label": "Another OpenAI-compatible endpoint", "endpoint": None, "model": None, "key": None}]
# How Ombro works, in the Lead's words: said at the end of set-up, and in the first chat.
WELCOME_WORK = (
    "Okay. One thing worth knowing: most of what I do happens while you're away. You brief me, "
    "the crew works in the background, and I report back. When you'd rather watch, we can do it "
    "live instead: the crew works here in the chat, and I'll check with you as we go. Just tell me "
    "what you want done, and I'll ask which you'd like."
)
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
    if spec.api in ("openai", "anthropic") and spec.key and not keys.get_key(home, spec.key):
        return None
    return spec


def _write_default_model(home: Home, entry: dict) -> None:
    meta, body = mdfile.read(home.models)
    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    models["default"] = entry
    models.setdefault("cheap", "default")
    meta["models"] = models
    mdfile.write(home.models, meta, body)


# A fresh install: the Lead's first words are this one question, under the home screen.
WELCOME = (
    "Welcome. I'm the Ombro Lead. You'll brief me about the work you want to get done. "
    "Let's get set up. What should I call you?"
)
GETTING_STARTED = (
    "First I'll create your home and working folders, then I'll ask you a few basic questions. "
    "After that we'll have a short interview so I understand you well. It should take no longer "
    "than five minutes. Later we'll set up your team."
)
CONNECT = (
    "Let's connect an AI model. API keys are stored in secrets/keys.env, readable only by you, and are "
    "never sent to a model."
)


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
    fresh = not home.root.is_dir() or connected_model(home) is None
    existed, files_existed = home.root.is_dir(), home.files.is_dir()
    # The home is made before the first question, so the home screen behind it shows the team.
    ensure_home(home)
    if fresh:
        name = ask_name(home, ui, WELCOME)
        ui.say((f"Thanks {name}. " if name else "Thanks. ") + GETTING_STARTED, level="lead")
        # What Ombro did behind the scenes: a plain note, not the Lead speaking.
        made = f"Ombro home {'found' if existed else 'created'}: {home.root}"
        if home.separate:
            made += f"\nOmbro working folder {'found' if files_existed else 'created'}: {home.files}"
        ui.say(made, spaced=True)
        if home.separate:
            ui.say("By default, your everyday work with Ombro will land in your working folder.", level="lead")
    else:
        ui.say(f"Ombro home found: {home.root}", level="lead")
        name = ask_name(home, ui)

    spec = connected_model(home)
    if spec is not None:
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            ui.say(f"A model is already connected: {spec.model} ({detail}).", level="lead")
            if not ui.yes("reconnect", "Connect a different model?"):
                _offer_search_key(home, ui)
                _set_up(home, ui, client_factory, next_steps)
                return True
        else:
            ui.say(f"The configured model did not answer: {detail}", level="lead")

    ui.say(CONNECT, spaced=True, level="lead")
    while True:
        before = Saved(home)  # nothing is kept until a model answers
        endpoint, model, key_name = connect_model(home, ui, client_factory)
        spec = load_models(home)["default"]
        ui.show(Progress(f"Testing {model} at {endpoint} …"))
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            ui.say(f"Perfect. We're connected: {detail}.", level="done")
            break
        before.restore()
        ui.show(Failure(f"The test call failed: {detail}. Nothing was saved."))
        ui.say("Let's try again.", level="lead")

    _offer_search_key(home, ui)
    _set_up(home, ui, client_factory, next_steps)
    return True


def name_field(current: str = "", label: str = "") -> Field:
    return Field(
        "name", label or "What should I call you? It's only used to greet you. (Enter to skip)", default=current or None
    )


def ask_name(home: Home, ui: UI, label: str = "") -> str:
    """The first question of set-up, before any model: what to call you. It can be skipped."""
    from ombro.settings import load_preferences, set_preference

    current = load_preferences(home).name
    name = " ".join(str(ui.ask(name_field(current, label)) or "").split())[:60]
    if name != current:
        set_preference(home, "name", name)
    return name


# In the terminal, set-up can also sign in with ChatGPT (the browser opens here). The app signs in
# from Settings › General instead.
PLAN_CHOICE = {"label": "ChatGPT plan (sign in, no key)", "endpoint": None, "model": None, "key": None, "plan": True}


def endpoint_choices(ui: UI) -> list[dict]:
    return [*ENDPOINTS, PLAN_CHOICE] if isinstance(ui, ConsoleUI) else list(ENDPOINTS)


def endpoint_field(choices: list[dict] | None = None) -> Field:
    return Field(
        "endpoint",
        "First, choose a provider",
        "choice",
        default="1",
        options=[
            Option(str(i), e["label"] + (f" ({e['endpoint']})" if e["endpoint"] else ""))
            for i, e in enumerate(choices or ENDPOINTS, 1)
        ],
        invalid="Please choose one of the numbers shown.",
    )


def model_form(preset: dict) -> Form:
    """What is asked about an endpoint once it is chosen: for another endpoint, its URL
    and the name of its key. The model is chosen once the endpoint has said which it has."""
    fields = []
    if not preset["endpoint"]:
        fields.append(
            Field(
                "url",
                "Endpoint base URL (the part before /chat/completions)",
                required=True,
                pattern=r"https?://\S+",
                invalid="The URL must start with http:// or https://.",
            )
        )
    if preset["key"] is None:
        fields.append(
            Field(
                "key_name",
                "Name for its key in secrets/keys.env, e.g. MY_PROVIDER_API_KEY (Enter if it needs no key)",
                pattern=r"[A-Z][A-Z0-9_]*",
                invalid="Key names use capitals, digits and underscores, starting with a letter.",
            )
        )
    return Form("model", "Connect a model", fields)


def model_name_field(default: str | None) -> Field:
    return Field(
        "model",
        "What model will you use? You can change it later with /models",
        default=default,
        required=True,
        invalid="A model name is needed.",
    )


REFUSED = (401, 403)


class KeyRefused(SetupError):
    """The provider said no to the key: nothing is saved until one works."""


def offered_models(home: Home, endpoint: str, key_name: str | None, client_factory=make_client) -> list[str]:
    """The models an endpoint lists for this key, or [] if it does not say. Raises KeyRefused
    when the endpoint refuses the key (HTTP 401 or 403), which is never a guess's cue."""
    spec = ModelSpec(name="default", api=api_for(endpoint), model="", endpoint=endpoint, key=key_name or None)
    try:
        client = client_factory(spec, home)
        lister = getattr(client, "list_models", None)
        return chat_models(list(lister())) if lister else []
    except ModelError as e:
        if getattr(e, "status", None) in REFUSED:
            from ombro.models import provider_label

            raise KeyRefused(f"{provider_label(endpoint)} refused the key: {e}") from None
        return []


class Saved:
    """models.md and keys.env as they were, to put back when a connection didn't work."""

    def __init__(self, home: Home):
        self.home = home
        self.files = {p: (p.read_text(encoding="utf-8") if p.is_file() else None) for p in (home.models, home.env)}

    def restore(self) -> None:
        for path, text in self.files.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                mdfile.write_atomic(path, text, mode=0o600 if path == self.home.env else None)


def choose_model(ui: UI, offered: list[str], suggested: str | None) -> str:
    """One of the endpoint's models, the suggested one first; or a name typed in."""
    if not offered:
        return str(ui.ask(model_name_field(suggested)) or "").strip() or (suggested or "")
    first = suggested if suggested in offered else offered[0]
    order = [first] + [m for m in offered if m != first]
    ui.say(
        f"It offers {len(order)} model{'s' if len(order) != 1 else ''}. Which should be your default? You "
        "can give roles other models, and change this later with /models.",
        level="lead",
    )
    picked = ui.ask(
        Field(
            "model",
            "Default model",
            "choice",
            default="1",
            options=[Option(str(i), m) for i, m in enumerate(order, 1)],
            invalid="Please choose one of the numbers shown.",
        )
    )
    return order[int(picked) - 1]


def connect_model(home: Home, ui: UI, client_factory=make_client) -> tuple[str, str, str]:
    """Part 1 of set-up, without a model: the endpoint and its key, then the model,
    chosen from those the endpoint lists. Every listed model is written to models.md,
    with `default` naming the chosen one; the key goes to secrets/keys.env.
    Returns (endpoint, model, key name)."""
    from ombro import models as model_list

    while True:
        choices = endpoint_choices(ui)
        preset = choices[int(ui.ask(endpoint_field(choices))) - 1]
        if preset.get("plan"):
            done = _plan_sign_in(home, ui)
            if done:
                return done
            continue
        answers = ui.form(model_form(preset)) if model_form(preset).fields else {}
        endpoint = preset["endpoint"] or str(answers.get("url") or "").strip()
        key_name = preset["key"] if preset["key"] is not None else str(answers.get("key_name") or "").strip()
        problems = [p for f in model_form(preset).fields if (p := f.check(answers.get(f.name)))]
        if problems:  # an interface that let a bad answer through
            ui.show(Failure(problems[0]))
            continue
        if key_name:
            existing = keys.get_key(home, key_name)
            hint = " (Enter keeps the current key)" if existing else ""
            who = preset["label"] if preset["key"] else key_name
            value = str(
                ui.ask(
                    Field(
                        "key",
                        f"What's your API key for {who}? I'll store it securely on this "
                        f"computer as soon as you give it to me. It's only ever sent to {who} to make "
                        f"calls, never to a model.{hint}",
                        "secret",
                    )
                )
                or ""
            ).strip()
            if value:
                keys.set_key(home, key_name, value)
            elif not existing:
                ui.say("A key is needed for this endpoint.", level="lead")
                continue
        try:
            offered = offered_models(home, endpoint, key_name, client_factory)
        except KeyRefused as e:
            if value:  # the refused key isn't kept
                if existing:
                    keys.set_key(home, key_name, existing)
                else:
                    keys.remove_key(home, key_name)
            ui.show(Failure(f"{e}. Nothing was saved."))
            ui.say("Check the key, or choose another provider.", level="lead")
            continue
        model = choose_model(ui, offered, preset["model"])
        if not model:
            ui.say("A model name is needed.", level="lead")
            continue
        names = model_list.add_models(home, endpoint, key_name or None, offered or [model])
        name = model_list.entry_name(model)
        if name in names:
            model_list.set_default(home, name)
        else:
            entry: dict = {"api": api_for(endpoint), "endpoint": endpoint, "model": model}
            if key_name:
                entry["key"] = key_name
            _write_default_model(home, entry)
        return endpoint, model, key_name


def _plan_sign_in(home: Home, ui: UI) -> tuple[str, str, str] | None:
    """Set-up's ChatGPT plan choice: sign in in the browser, and the plan's first model is the default."""
    import webbrowser

    from ombro import chatgpt, oauth
    from ombro import models as model_list

    ui.say(
        "Your browser will open to continue with ChatGPT. Allow Ombro to use your plan, then come back here.",
        level="lead",
    )
    try:
        done = chatgpt.sign_in(home, open_browser=webbrowser.open, say=lambda _t: None)
    except (oauth.SignInError, ModelError) as e:
        ui.show(Failure(f"Couldn't sign in with ChatGPT: {e}"))
        return None
    if not done["plan"] or not done["models"]:
        ui.show(Failure(chatgpt.NOT_ALLOWED))
        return None
    model_list.set_default(home, done["models"][0])
    if done["welcome"]:
        ui.say(done["welcome"], level="lead")
        chatgpt.mark_welcomed(home)
    return "ChatGPT plan", load_models(home)["default"].model, ""


def _offer_search_key(home: Home, ui: UI) -> None:
    """No search key is asked for in set-up: web search starts on DuckDuckGo, and one line
    says where to add better search and other tools later."""
    try:
        provider = load_tool_settings(home).provider_for("web.search")
    except (SetupError, mdfile.FrontMatterError):
        provider = "duckduckgo"
    where = {"duckduckgo": "DuckDuckGo, which is free but best-effort", "firecrawl": "Firecrawl"}.get(
        provider, provider
    )
    ui.say(
        f"Web search uses {where} for now. You'll probably want to add a search provider of your own, or "
        "other tools such as email or documents, later on: /tools shows and adds them.",
        level="lead",
    )


def _is_default_setup(home: Home) -> bool:
    if home.instructions.is_file() and mdfile.prompt_text(mdfile.read(home.instructions)[1]):
        return False
    return True


def _crew_step(home: Home, ui: UI, crew: str) -> str:
    """After set-up: say what the crew is now, and offer to keep it, rename it or start a
    new one. Returns the crew roles are added to next."""
    from ombro.home import crew_phrase, crew_title, ensure_crew, set_crew_title
    from ombro.roles import NAME, crew_roles, folder_name
    from ombro.settings import load_limits, set_preference

    try:
        roles = [r.title for r in crew_roles(home, crew, load_limits(home)).values()]
    except Exception:  # noqa: BLE001
        roles = []
    title = crew_title(home, crew)
    members = (
        f"one team member, a {roles[0]}"
        if len(roles) == 1
        else f"{len(roles)} team members"
        if roles
        else "no team members yet"
    )
    ui.say(
        f"There's one crew in your Ombro team at the moment, the {title}, and it has {members}. "
        + (
            f"I can get the {roles[0]} working on your briefs straight away, but you'll probably want to set "
            "up your team properly. "
            if len(roles) == 1
            else ""
        )
        + "I can set up different crews, like Marketing or Legal, and add team members to them, for example "
        "a Market Researcher. I'll ask you a few questions about each one to get it set up.",
        level="lead",
    )
    choice = ui.ask(
        Field(
            "crew",
            "What would you like to do with your crew?",
            "choice",
            default="1",
            options=[
                Option("1", f"Keep the {title} as it is"),
                Option("2", f"Rename the {title}"),
                Option("3", "Start a new crew"),
            ],
            invalid="Please choose one of the numbers shown.",
        )
    )
    if choice == "2":
        new = " ".join(
            ui.text(
                "crew_title",
                "What should it be called? (for example Marketing Crew)",
                required=True,
                invalid="A name is needed.",
            ).split()
        )
        set_crew_title(home, crew, new)
        ui.say(f"Done. It's now the {new}.", level="done")
    elif choice == "3":
        new = " ".join(
            ui.text(
                "crew_title",
                "What should the new crew be called? (for example Marketing)",
                required=True,
                invalid="A name is needed.",
            ).split()
        )
        name = folder_name(new)
        if not NAME.match(name or "") or home.crew_dir(name).exists():
            ui.say(f"There's already a crew called that, so we'll keep using the {title}.", level="lead")
            return crew
        ensure_crew(home, name, f"The {new}.", new)
        ui.say(f"Created {crew_phrase(home, name)}.", level="done")
        if ui.yes("default_crew", "Make it your default crew, so briefs use it unless you say otherwise?", True):
            set_preference(home, "default_crew", name)
        return name
    return crew


def _set_up(home: Home, ui: UI, client_factory, next_steps: bool = True) -> None:
    """Part 2: the set-up interview, then an offer to add roles. Nothing is written without confirmation."""
    from ombro.interviews import RolePurpose, SetupPurpose, start
    from ombro.settings import load_limits, load_preferences

    crew = load_preferences(home).default_crew
    if not home.crew_dir(crew).is_dir():
        crew = "default"
    limits = load_limits(home)
    kwargs = {"ui": ui, "client_factory": lambda spec: client_factory(spec, home)}
    from ombro.settings import load_preferences

    name = load_preferences(home).name
    if _is_default_setup(home):
        ui.say(
            "Okay, next up is a short set-up interview. I'll ask a few questions about you, how you work, and "
            "how you want reports and feedback. Then I'll get you to confirm what I've learned.",
            spaced=True,
            level="lead",
        )
        go = ui.yes("setup", f"Start it now{', ' + name if name else ''}?", True)
    else:
        ui.say("")
        go = ui.yes("setup", "Your preferences are already set. Hold the set-up interview again?")
    if go:
        outcome = start(home, SetupPurpose(home, crew), crew, limits, **kwargs)
        if outcome.confirmed:
            ui.say(f"Thanks{', ' + name if name else ''}. Our basic set-up is done.", spaced=True, level="lead")
            crew = _crew_step(home, ui, crew)
            question = "Would you like to add a new role now?"
            while ui.yes("add_role", question):
                ui.say("Excellent. Let's get that set up.", level="lead")
                added = start(home, RolePurpose(home, crew, limits), crew, limits, **kwargs)
                if added.confirmed:
                    from ombro.models import offer_role_model

                    offer_role_model(
                        home,
                        ui,
                        f"{crew}/{added.proposal.data['name']}",
                        _role_title(home, crew, added.proposal.data["name"]),
                    )
                limits = load_limits(home)
                question = "Do you want to add another role to the crew now?"
            ui.say(WELCOME_WORK, spaced=True, level="lead")
            if ui.yes("brief", "Would you like to brief your first task now?"):
                from ombro.interviews import BriefPurpose

                brief = start(home, BriefPurpose(home, crew, limits), crew, limits, **kwargs)
                if brief.confirmed:
                    ui.say(
                        f"Spec saved as {brief.proposal.data['name']!r}. "
                        f"Start it with:  ombro start --spec {brief.proposal.data['name']}",
                        spaced=True,
                        level="done",
                    )
    if next_steps:
        _next_steps(home, ui)


def _role_title(home: Home, crew: str, name: str) -> str:
    try:
        return str(mdfile.read(home.roles_dir(crew) / name / "config.md")[0].get("title") or name)
    except (OSError, mdfile.FrontMatterError):
        return name


def _next_steps(home: Home, ui: UI) -> None:
    ui.say(
        "\nNext:\n"
        "  ombro role add          add a role (or `ombro role list` to see the crew)\n"
        "  ombro brief             describe a task; the Lead turns it into a spec\n"
        "  ombro specs             list saved specs, by name\n"
        "  ombro start             brief a task and start it straight away\n"
        "  ombro chat              talk with the Lead about past work (read-only)\n"
        "  ombro limits            see how far jobs and interviews may go"
    )
