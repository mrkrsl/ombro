"""The models in models.md, grouped by connection (an endpoint and its key), and
the changes an interface makes to them: adding a connection's models, choosing
the default, and choosing the model a role or the Lead uses (specification 14.1).

models.md stays the one record. Each model is an entry named after the model
(`deepseek-chat: {api, endpoint, model, key}`); `default` names the one used
unless a role says otherwise, and `cheap` the one helpers use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ombro import mdfile
from ombro.home import Home
from ombro.settings import SetupError, load_models
from ombro.ui import Field, Option

ALIASES = ("default", "cheap")


def entry_name(model_id: str) -> str:
    """A models.md name for a provider's model id: lower case, and only letters,
    digits, '.', '-' and '_' (an id such as org/model-7b becomes org-model-7b)."""
    return re.sub(r"[^a-z0-9._-]+", "-", model_id.strip().lower()).strip("-") or "model"


def _read(home: Home) -> tuple[dict, str, dict]:
    meta, body = mdfile.read(home.models)
    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    return meta, body, models


def _write(home: Home, meta: dict, body: str, models: dict) -> None:
    meta["models"] = models
    mdfile.write(home.models, meta, body)


def default_name(home: Home) -> str | None:
    """The entry `default` points at, or None when `default` is written out in full."""
    _, _, models = _read(home)
    target = models.get("default")
    return target if isinstance(target, str) else None


def add_models(home: Home, endpoint: str, key: str | None, ids: list[str]) -> list[str]:
    """Add an entry for each model a connection offers. An entry already there for the
    same endpoint is kept; a name taken by another endpoint's model is left alone.
    Returns the names of the entries for these ids, in order."""
    meta, body, models = _read(home)
    names = []
    for mid in ids:
        name = entry_name(mid)
        if name in ALIASES:
            name += "-model"
        have = models.get(name)
        if isinstance(have, dict) and (have.get("endpoint") != endpoint or have.get("model") != mid):
            continue
        entry: dict[str, Any] = {"api": "openai", "endpoint": endpoint, "model": mid}
        if key:
            entry["key"] = key
        models.setdefault(name, entry)
        names.append(name)
    models.setdefault("cheap", "default")
    _write(home, meta, body, models)
    return names


def set_default(home: Home, name: str) -> None:
    """Point `default` at a model entry. A default written out in full is first kept
    under its own name, so nothing it held is lost."""
    meta, body, models = _read(home)
    if name not in models or name in ALIASES:
        raise SetupError(f"no model named {name!r} in models.md")
    old = models.get("default")
    if isinstance(old, dict):
        keep = entry_name(str(old.get("model") or "previous-default"))
        if keep in ALIASES:
            keep += "-model"
        models.setdefault(keep, old)
    models["default"] = name
    models.setdefault("cheap", "default")
    _write(home, meta, body, models)


def _config_path(home: Home, who: str) -> Path:
    """who is `lead`, or crew/role."""
    if who == "lead":
        return home.lead_dir / "config.md"
    crew, _, role = who.partition("/")
    return home.roles_dir(crew) / role / "config.md"


def set_role_model(home: Home, who: str, name: str) -> None:
    """Set `model:` in the Lead's config (who = lead) or a role's (who = crew/role)."""
    if name not in load_models(home):
        raise SetupError(f"no model named {name!r} in models.md")
    path = _config_path(home, who)
    if not path.is_file():
        raise SetupError(f"there is no role {who!r}")
    meta, body = mdfile.read(path)
    meta["model"] = name
    mdfile.write(path, meta, body)


def users(home: Home) -> dict[str, list[str]]:
    """For each model name in a config, who uses it: 'the Lead', or 'Title (Crew)'."""
    from ombro.home import crew_title

    out: dict[str, list[str]] = {}

    def note(path: Path, label: str, fallback: str) -> None:
        try:
            meta, _ = mdfile.read(path)
        except (OSError, mdfile.FrontMatterError):
            return
        out.setdefault(str(meta.get("model") or fallback), []).append(label)

    if (home.lead_dir / "config.md").is_file():
        note(home.lead_dir / "config.md", "the Lead", "default")
    for crew in home.crews():
        rdir = home.roles_dir(crew)
        if not rdir.is_dir():
            continue
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            cfg = folder / "config.md"
            if not cfg.is_file():
                continue
            try:
                meta, _ = mdfile.read(cfg)
            except mdfile.FrontMatterError:
                continue
            title = str(meta.get("title") or folder.name)
            fallback = "cheap" if meta.get("kind") == "helper" else "default"
            out.setdefault(str(meta.get("model") or fallback), []).append(f"{title} ({crew_title(home, crew)})")
    return out


KNOWN_PROVIDERS = {"deepseek": "DeepSeek", "anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter",
                   "mistral": "Mistral", "groq": "Groq", "together": "Together", "x": "xAI", "googleapis": "Google",
                   "fireworks": "Fireworks", "perplexity": "Perplexity", "cohere": "Cohere"}


# Providers people often use, all through their OpenAI-compatible API. Set-up offers them in the
# terminal, and Settings › General fills the form from them. `key` is the name the key is kept
# under in keys.env; "" means the provider needs none (it runs on this computer). `model` is the
# one suggested as the default when the provider lists several.
PRESETS: list[dict[str, Any]] = [
    {"label": "DeepSeek", "endpoint": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY",
     "model": "deepseek-chat"},
    {"label": "OpenAI", "endpoint": "https://api.openai.com/v1", "key": "OPENAI_API_KEY", "model": None},
    {"label": "Anthropic", "endpoint": "https://api.anthropic.com/v1", "key": "ANTHROPIC_API_KEY",
     "model": "claude-opus-5-5"},
    {"label": "Google Gemini", "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai",
     "key": "GEMINI_API_KEY", "model": None},
    {"label": "Mistral", "endpoint": "https://api.mistral.ai/v1", "key": "MISTRAL_API_KEY", "model": None},
    {"label": "xAI", "endpoint": "https://api.x.ai/v1", "key": "XAI_API_KEY", "model": None},
    {"label": "Groq", "endpoint": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY", "model": None},
    {"label": "OpenRouter", "endpoint": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY",
     "model": None},
    {"label": "Ollama, on this computer", "endpoint": "http://localhost:11434/v1", "key": "", "model": None},
    {"label": "LM Studio, on this computer", "endpoint": "http://localhost:1234/v1", "key": "", "model": None},
]

# Models a provider lists that can't hold a conversation: speech, pictures, embeddings and the like.
NOT_CHAT = re.compile(r"embed|whisper|tts|dall-e|audio|realtime|transcribe|moderation|image|"
                      r"davinci|babbage|sora|rerank|guard|search-preview", re.I)


def chat_models(ids: list[str]) -> list[str]:
    """The models in a provider's list that chat; all of them if none look like it."""
    kept = [m for m in ids if not NOT_CHAT.search(m)]
    return kept or list(ids)


def provider_label(endpoint: str | None, api: str = "openai") -> str:
    """A provider's name for people, from its address: api.deepseek.com is DeepSeek; an address on
    this computer or the local network is Local; anything else is its host."""
    if not endpoint:
        return "Test model" if api == "fake" else api
    from urllib.parse import urlparse

    host = (urlparse(endpoint).hostname or endpoint).lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith((".local", ".lan")) or re.match(
            r"^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.", host):
        return "Local"
    parts = host.split(".")
    for p in parts:
        if p in KNOWN_PROVIDERS:
            return KNOWN_PROVIDERS[p]
    core = parts[-2] if len(parts) >= 2 else parts[0]
    return core[:1].upper() + core[1:]


def key_name_for(endpoint: str) -> str:
    """The name in keys.env for a new provider's key: DEEPSEEK_API_KEY for api.deepseek.com."""
    label = re.sub(r"[^A-Z0-9]+", "_", provider_label(endpoint).upper()).strip("_") or "MODEL"
    if not label[0].isalpha():
        label = "MODEL_" + label
    return f"{label}_API_KEY"


def connections(home: Home) -> list[dict[str, Any]]:
    """Each connection (endpoint and key) with its models: name, model id, whether it
    is the default, and who uses it."""
    specs = load_models(home)
    _, _, raw = _read(home)
    default = specs.get("default")
    used = users(home)
    groups: dict[tuple, dict[str, Any]] = {}

    def same(a, b) -> bool:
        return a is not None and b is not None and (a.model, a.endpoint, a.api) == (b.model, b.endpoint, b.api)

    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue  # a name for another entry, such as default: deepseek-chat
        spec = specs[name]
        if name == "default" and any(isinstance(e, dict) and n != "default" and same(specs[n], spec)
                                     for n, e in raw.items()):
            continue  # written out in full, and also listed under its own name
        g = groups.setdefault((spec.api, spec.endpoint, spec.key),
                              {"api": spec.api, "endpoint": spec.endpoint, "key": spec.key, "models": [],
                               "label": provider_label(spec.endpoint, spec.api)})
        who = list(used.get(name, []))
        for alias in ALIASES:
            if alias != name and same(specs.get(alias), spec):
                who += used.get(alias, [])
        g["models"].append({"name": name, "model": spec.model, "default": same(default, spec), "used_by": who})
    return list(groups.values())


def names(home: Home) -> list[str]:
    """The model entries a role can be given, the default first."""
    out = []
    for c in connections(home):
        for m in c["models"]:
            out.append((not m["default"], m["name"]))
    return [n for _, n in sorted(out)]


def choice_field(home: Home, name: str, label: str, current: str | None = None) -> Field | None:
    """A numbered choice of models, or None when there is only one to choose."""
    have = names(home)
    if len(have) < 2:
        return None
    options = [Option(str(i), n + ("  (the default)" if i == 1 else "")) for i, n in enumerate(have, 1)]
    default = str(have.index(current) + 1) if current in have else "1"
    return Field(name, label, "choice", default=default, options=options,
                 invalid="Please choose one of the numbers shown.")


def chosen(home: Home, key: str) -> str:
    return names(home)[int(key) - 1]


def current_model(home: Home, who: str) -> str:
    """The entry name a config's `model:` comes to, aliases followed to an entry
    listed by names() where one matches."""
    try:
        meta, _ = mdfile.read(_config_path(home, who))
    except (OSError, mdfile.FrontMatterError):
        meta = {}
    name = str(meta.get("model") or ("cheap" if meta.get("kind") == "helper" else "default"))
    have = names(home)
    if name in have:
        return name
    specs = load_models(home)
    spec = specs.get(name)
    for n in have:
        s = specs[n]
        if spec is not None and (s.model, s.endpoint) == (spec.model, spec.endpoint):
            return n
    return name


def offer_role_model(home: Home, ui, who: str, title: str) -> None:
    """After a role is added: which model it uses, when there is more than one."""
    current = current_model(home, who)
    f = choice_field(home, "role_model", f"Which model should the {title} use?", current)
    if f is None:
        return
    name = chosen(home, ui.ask(f))
    if name != current:
        set_role_model(home, who, name)
    ui.say(f"The {title} will use {name}.", level="done")


# ---------------------------------------------------------------------------
# The flows /models, /model, `ombro models` and `ombro model` share


def listing_lines(home: Home) -> list[str]:
    items = []
    for c in connections(home):
        items.append((c["endpoint"] or c["api"]) + (f" (key {c['key']})" if c["key"] else ""))
        for m in c["models"]:
            who = f": {', '.join(m['used_by'])}" if m["used_by"] else ""
            items.append(f"   {m['name']}" + ("  (the default)" if m["default"] else "") + who)
    return items or ["none yet"]


def default_flow(home: Home, ui, name: str | None = None) -> int:
    if not name:
        f = choice_field(home, "default_model", "Which model should be the default?",
                         default_name(home))
        if f is None:
            have = names(home)
            ui.say(f"There's only one model, {have[0] if have else 'none'}. `models refresh` or `models add` "
                   "finds more.")
            return 1
        name = chosen(home, ui.ask(f))
    try:
        set_default(home, name)
    except SetupError as e:
        ui.say(str(e))
        return 1
    ui.say(f"The default model is now {name}. The Lead and roles without a model of their own use it.",
           level="done")
    return 0


def refresh_flow(home: Home, ui, client_factory=None) -> int:
    from ombro.client import ModelError, make_client
    from ombro.settings import ModelSpec

    factory = client_factory or make_client
    for c in connections(home):
        if c["api"] != "openai":
            continue
        spec = ModelSpec(name="default", api="openai", model="", endpoint=c["endpoint"], key=c["key"])
        try:
            ids = factory(spec, home).list_models()
        except (ModelError, AttributeError) as e:
            ui.say(f"{c['endpoint']} did not list its models: {e}")
            continue
        before = {m["name"] for m in c["models"]}
        added = [n for n in add_models(home, c["endpoint"], c["key"], ids) if n not in before]
        ui.say(f"{c['endpoint']}: {len(ids)} model{'s' if len(ids) != 1 else ''}"
               + (f", added {', '.join(added)}." if added else ", nothing new."))
    return 0


def add_flow(home: Home, ui, client_factory=None) -> int:
    from ombro.client import make_client
    from ombro.init_cmd import connect_model, test_connection

    from ombro.init_cmd import Saved

    factory = client_factory or make_client
    old_default = default_name(home)
    before = Saved(home)
    endpoint, model, _ = connect_model(home, ui, factory)
    specs = load_models(home)
    ok, detail = test_connection(home, specs.get(entry_name(model)) or specs["default"], factory)
    if not ok:
        before.restore()
        ui.say(f"The test call to {model} failed: {detail}. Nothing was saved.")
        return 1
    if old_default and old_default != entry_name(model) and not ui.yes(
            "make_default", f"Make {model} your default model, in place of {old_default}?"):
        set_default(home, old_default)
    ui.say(f"Connected {endpoint}.", level="done")
    return 0


def people(home: Home) -> list[tuple[str, str]]:
    """Who can be given a model: ('lead', 'the Lead'), then ('crew/role', 'Title (Crew Title)')."""
    from ombro.home import crew_title

    out = [("lead", "the Lead")]
    for crew in home.crews():
        rdir = home.roles_dir(crew)
        if not rdir.is_dir():
            continue
        for p in sorted(rdir.iterdir()):
            if not p.is_dir() or p.name.startswith(".") or not (p / "config.md").is_file():
                continue
            try:
                title = str(mdfile.read(p / "config.md")[0].get("title") or p.name)
            except mdfile.FrontMatterError:
                title = p.name
            out.append((f"{crew}/{p.name}", f"{title} ({crew_title(home, crew)})"))
    return out


def model_flow(home: Home, ui, who: str | None = None, name: str | None = None) -> int:
    """Show who uses which model; then set one, asking for what is not given."""
    everyone = people(home)
    if not who:
        ui.say("Who uses which model:", items=[f"{label}: {current_model(home, w)}" for w, label in everyone])
        if len(names(home)) < 2:
            ui.say("There's only one model so far. `models refresh` or `models add` finds more.")
            return 0
        pick = ui.ask(Field("who", "Whose model would you like to change?", "choice", default="1",
                            options=[Option(str(i), label) for i, (_, label) in enumerate(everyone, 1)],
                            invalid="Please choose one of the numbers shown."))
        who, label = everyone[int(pick) - 1]
    else:
        match = [(w, lb) for w, lb in everyone if w == who]
        if not match:
            ui.say(f"There's no one called {who}. Use `lead`, or crew/role: "
                   + ", ".join(w for w, _ in everyone[1:]) + ".")
            return 1
        who, label = match[0]
    if not name:
        f = choice_field(home, "model", f"Which model should {label} use?", current_model(home, who))
        if f is None:
            ui.say(f"{label[0].upper() + label[1:]} uses {current_model(home, who)}, the only model.")
            return 0
        name = chosen(home, ui.ask(f))
    try:
        set_role_model(home, who, name)
    except SetupError as e:
        ui.say(str(e))
        return 1
    ui.say(f"{label[0].upper() + label[1:]} will use {name} from the next chat, interview or job.", level="done")
    return 0


# ---------------------------------------------------------------------------
# Providers: a connection (an endpoint and its key) as one thing to add, edit or remove (plan C5)


def _of(entry: Any, endpoint: str) -> bool:
    return isinstance(entry, dict) and entry.get("endpoint") == endpoint


def provider_names(home: Home, endpoint: str) -> list[str]:
    """The models.md entries that use an endpoint, aliases left out."""
    _, _, models = _read(home)
    return [n for n, e in models.items() if _of(e, endpoint)]


def add_provider(home: Home, endpoint: str, key_name: str | None, model: str | None = None,
                 client_factory=None) -> list[str]:
    """Add a connection from a form: every model the endpoint lists, or the one model named
    when it lists none. Its key must already be in keys.env. The default is left as it is,
    unless there was none. Returns the names added."""
    from ombro.client import make_client
    from ombro.init_cmd import offered_models

    endpoint = (endpoint or "").strip().rstrip("/")
    if not re.match(r"^https?://\S+$", endpoint):
        raise SetupError("The address must start with https:// (or http:// for one on your own network).")
    key_name = (key_name or "").strip() or None
    from ombro import keys

    if key_name and not keys.valid_name(key_name):
        raise SetupError("Key names use capitals, digits and underscores, starting with a letter.")
    if provider_names(home, endpoint):
        raise SetupError(f"{endpoint} is already connected. Edit it instead.")
    offered = offered_models(home, endpoint, key_name, client_factory or make_client)
    ids = offered or ([model.strip()] if model and model.strip() else [])
    if not ids:
        raise SetupError(f"{endpoint} didn't list its models. Give the name of a model it offers.")
    had_default = default_name(home) is not None or isinstance(_read(home)[2].get("default"), dict)
    names = add_models(home, endpoint, key_name, ids)
    if not had_default and names:
        set_default(home, names[0])
    return names


def edit_provider(home: Home, endpoint: str, *, new_endpoint: str | None = None,
                  key_name: str | None = None) -> list[str]:
    """Change a connection's address or the name of its key, in every model of it.
    key_name "" means it needs no key. Returns the names changed."""
    meta, body, models = _read(home)
    names = [n for n, e in models.items() if _of(e, endpoint)]
    if not names:
        raise LookupError(f"no provider at {endpoint}")
    if new_endpoint is not None:
        new_endpoint = new_endpoint.strip().rstrip("/")
        if not re.match(r"^https?://\S+$", new_endpoint):
            raise SetupError("The address must start with https:// (or http:// for one on your own network).")
        if new_endpoint != endpoint and any(_of(e, new_endpoint) for e in models.values()):
            raise SetupError(f"{new_endpoint} is already connected.")
    if key_name:
        from ombro import keys

        if not keys.valid_name(key_name.strip()):
            raise SetupError("Key names use capitals, digits and underscores, starting with a letter.")
    for n in names:
        entry = dict(models[n])
        if new_endpoint is not None:
            entry["endpoint"] = new_endpoint
        if key_name is not None:
            if key_name.strip():
                entry["key"] = key_name.strip()
            else:
                entry.pop("key", None)
        models[n] = entry
    _write(home, meta, body, models)
    return names


def check_remove_provider(home: Home, endpoint: str) -> dict[str, Any]:
    """What stops a connection being removed (the default model is one of its models, or a role
    names one), and what to know first. As removing.Check: {blocked, warnings}."""
    _, _, models = _read(home)
    names = [n for n, e in models.items() if _of(e, endpoint)]
    if not names:
        raise LookupError(f"no provider at {endpoint}")
    specs = load_models(home)
    default = specs.get("default")
    blocked = None
    if default is not None and default.endpoint == endpoint:
        blocked = "Your default model comes from this provider. Choose a default model from another one first."
    else:
        used = users(home)
        who = sorted({w for n in names for w in used.get(n, [])})
        if who:
            blocked = (f"{', '.join(who)} {'uses' if len(who) == 1 else 'use'} a model from this provider. "
                       "Give them another model first.")
    warnings = []
    cheap = specs.get("cheap")
    if cheap is not None and cheap.endpoint == endpoint and not blocked:
        warnings.append("Helpers use one of its models; they'll use the default model instead.")
    return {"blocked": blocked, "warnings": warnings, "models": names}


def remove_provider(home: Home, endpoint: str) -> list[str]:
    """Remove every model of a connection from models.md. Its key stays in keys.env.
    Returns the names removed."""
    from ombro.removing import RemoveError

    c = check_remove_provider(home, endpoint)
    if c["blocked"]:
        raise RemoveError(c["blocked"])
    meta, body, models = _read(home)
    names = set(c["models"])
    for n in names:
        del models[n]
    if models.get("cheap") is None or (isinstance(models["cheap"], str) and models["cheap"] in names):
        models["cheap"] = "default"
    _write(home, meta, body, models)
    return sorted(names)
