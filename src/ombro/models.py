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
                              {"api": spec.api, "endpoint": spec.endpoint, "key": spec.key, "models": []})
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
