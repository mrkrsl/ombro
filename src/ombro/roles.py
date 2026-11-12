"""Roles: a folder with config.md and soul.md. One mechanism for every kind."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ombro import mdfile
from ombro.home import Home, tidy_title
from ombro.settings import Limits, SetupError

KINDS = ("lead", "specialist", "helper")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
DEFAULT_MODEL = {"lead": "default", "specialist": "default", "helper": "cheap"}
FALLBACK_STEPS = {"lead": 6, "specialist": 15, "helper": 8}


class RoleError(SetupError):
    pass


@dataclass
class Role:
    name: str
    kind: str
    description: str
    model: str
    soul: str
    path: Path
    max_steps: int
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    crew: str = ""  # empty for the Lead
    title: str = ""  # the display name, e.g. Market Researcher


def folder_name(title: str) -> str:
    """The folder name made from a title: Marketing Assistant becomes marketing-assistant."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:63].rstrip("-")


def _str_list(value, label: str, where: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RoleError(f"{where}: `{label}` must be a list of names, like [a, b]")
    return [v.strip() for v in value]


def load_role(folder: Path, limits: Limits) -> Role:
    cfg = folder / "config.md"
    soul_path = folder / "soul.md"
    if not cfg.is_file():
        raise RoleError(f"{folder}: missing config.md")
    if not soul_path.is_file():
        raise RoleError(f"{folder}: missing soul.md")
    try:
        meta, _ = mdfile.read(cfg)
        _, soul_body = mdfile.read(soul_path)
    except mdfile.FrontMatterError as e:
        raise RoleError(str(e)) from None

    name = str(meta.get("name") or folder.name)
    if name != folder.name:
        raise RoleError(f"{cfg}: name {name!r} does not match its folder {folder.name!r}")
    if not NAME.match(name):
        raise RoleError(f"{cfg}: role names use lower-case letters, digits, - and _")
    kind = str(meta.get("kind") or "specialist")
    if kind not in KINDS:
        raise RoleError(f"{cfg}: kind must be one of {', '.join(KINDS)}, not {kind!r}")
    description = str(meta.get("description") or "").strip()
    if not description and kind != "lead":
        raise RoleError(f"{cfg}: a description is required; it is what the Lead sees when choosing whom to brief")
    steps = meta.get("max_steps", limits.max_steps.get(kind, FALLBACK_STEPS[kind]))
    if isinstance(steps, bool) or not isinstance(steps, int) or steps <= 0:
        raise RoleError(f"{cfg}: max_steps must be a positive whole number")
    soul = mdfile.prompt_text(soul_body)
    if not soul:
        raise RoleError(f"{soul_path}: soul.md is empty")
    return Role(
        name=name,
        kind=kind,
        description=description,
        model=str(meta.get("model") or DEFAULT_MODEL[kind]),
        soul=soul,
        path=folder,
        max_steps=steps,
        tools=_str_list(meta.get("tools"), "tools", cfg),
        skills=_str_list(meta.get("skills"), "skills", cfg),
        title=str(meta.get("title") or "").strip() or tidy_title(name),
    )


def load_lead(home: Home, limits: Limits) -> Role:
    """The one Lead, in lead/ at the top of the home."""
    if not home.lead_dir.is_dir():
        raise RoleError(f"{home.lead_dir} is missing: there is no Lead")
    lead = load_role(home.lead_dir, limits)
    if lead.kind != "lead":
        raise RoleError(f"{home.lead_dir / 'config.md'}: the Lead must have kind: lead")
    return lead


def crew_roles(home: Home, crew: str, limits: Limits) -> dict[str, Role]:
    """One crew's roles by their own name. A crew has no Lead of its own."""
    rdir = home.roles_dir(crew)
    if not home.crew_dir(crew).is_dir():
        raise RoleError(f"no crew named {crew!r} in {home.crews_dir}")
    if not rdir.is_dir():
        raise RoleError(f"{rdir} is missing")
    roles: dict[str, Role] = {}
    for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        role = load_role(folder, limits)
        if role.kind == "lead":
            raise RoleError(f"{folder}: a crew has no Lead of its own; the one Lead is in {home.lead_dir}")
        roles[role.name] = replace(role, crew=crew)
    return roles


def load_roles(home: Home, crews: list[str], limits: Limits) -> dict[str, Role]:
    """The roles of the given crews. With more than one crew each is named crew/role, so
    two crews can each have a role of the same name; with one, by its own name."""
    out: dict[str, Role] = {}
    for crew in crews:
        for name, role in crew_roles(home, crew, limits).items():
            full = f"{crew}/{name}" if len(crews) > 1 else name
            out[full] = replace(role, name=full)
    return out


def resolve_role(roles: dict[str, Role], name: str) -> tuple[Role | None, str]:
    """A role by the name it has in the run, by crew/role, or by its own name where only
    one crew has it. Returns (role, "") or (None, why not)."""
    name = name.strip()
    if name in roles:
        return roles[name], ""
    if "/" in name:
        crew, _, own = name.partition("/")
        matches = [r for r in roles.values() if r.crew == crew and r.path.name == own]
    else:
        matches = [r for r in roles.values() if r.path.name == name]
    if len(matches) == 1:
        return matches[0], ""
    known = ", ".join(roles) or "none"
    if matches:
        return None, f"more than one crew has a role named {name!r}; name it as crew/role: " + \
            ", ".join(r.name for r in matches)
    return None, f"there is no role named {name!r}. Roles you can brief: {known}"


def skill_texts(home: Home, skills: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(skill, text)], [missing skill names])."""
    found, missing = [], []
    for s in skills:
        path = home.skills_dir / s / "skill.md"
        if not path.is_file():
            missing.append(s)
            continue
        _, body = mdfile.read(path)
        found.append((s, mdfile.prompt_text(body)))
    return found, missing
