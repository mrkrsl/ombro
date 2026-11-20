"""Changing a crew member's profile in one go: its title (a rename), its kind (specialist or
helper), its model and its soul. Used by `ombro serve` (the crew member edit page) and by
`ombro role rename` / `ombro role kind`.

A rename moves the member's folder too, since the folder name is the name jobs, saved briefs
and the Lead use for them. So it refuses a name another member of the crew has (by folder or
by title), refuses while a job of the crew is running, and changes saved briefs that hand the
work to the member by name. Past jobs keep the old name: they're a record of what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ombro import mdfile
from ombro.home import Home, crew_title, tidy_title
from ombro.settings import SetupError, load_models

EDITABLE_KINDS = ("specialist", "helper")


class EditError(SetupError):
    """Why the change can't be made, in words for the user."""


@dataclass
class Edited:
    name: str  # the member's folder name now (new after a rename)
    renamed_from: str | None = None
    specs: list[str] = field(default_factory=list)  # saved briefs changed to the new name


def _title_of(folder: Path) -> str:
    try:
        meta, _ = mdfile.read(folder / "config.md")
    except (OSError, mdfile.FrontMatterError):
        meta = {}
    return str(meta.get("title") or "").strip() or tidy_title(folder.name)


def _spec_roles(meta: dict) -> list:
    raw = meta.get("roles") or []
    return [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, list) else []


def _specs_naming(home: Home, crew: str, role: str) -> list[tuple[Path, dict, str, list]]:
    """Saved briefs of the crew whose `roles:` name the member, with their roles renamed later."""
    from ombro.cli import saved_specs

    out = []
    for _, path in saved_specs(home, crew):
        try:
            meta, body = mdfile.read(path)
        except (OSError, mdfile.FrontMatterError):
            continue
        roles = _spec_roles(meta)
        if any(str(r).strip() in (role, f"{crew}/{role}") for r in roles):
            out.append((path, meta, body, roles))
    return out


def write_soul(folder: Path, text: str) -> None:
    """The soul's new text; its front matter and guidance comments stay."""
    path = folder / "soul.md"
    meta, body = mdfile.read(path)
    if meta:
        mdfile.write(path, meta, mdfile.with_comments(body, text))
    else:
        mdfile.write_atomic(path, mdfile.with_comments(body, text))


def edit_role(
    home: Home,
    crew: str,
    role: str,
    *,
    title: str | None = None,
    kind: str | None = None,
    model: str | None = None,
    soul: str | None = None,
) -> Edited:
    """Change what's given and leave the rest. Everything is checked before anything is written."""
    from ombro.removing import check_role, role_folder
    from ombro.roles import NAME, folder_name

    lead = role == "lead"
    if lead:
        if title is not None or kind is not None:
            raise EditError("The Lead keeps its name and kind; only its model and soul change.")
        folder = home.lead_dir
    else:
        folder = role_folder(home, crew, role)
    cfg = folder / "config.md"
    try:
        meta, body = mdfile.read(cfg)
    except mdfile.FrontMatterError as e:
        raise EditError(str(e)) from None

    if kind is not None and kind not in EDITABLE_KINDS:
        raise EditError(f"A crew member is a specialist or a helper, not {kind!r}.")
    if model is not None and model not in ("default", "cheap") and model not in load_models(home):
        raise EditError(f"There's no model named {model!r}. See Settings › General for the models you have.")
    if soul is not None and not soul.strip():
        raise EditError("The soul can't be empty: it's how they know what they do.")

    new_name = role
    if title is not None:
        title = " ".join(title.split())
        new_name = folder_name(title)
        if not title or not NAME.match(new_name):
            raise EditError('Give them a name made of letters or digits, such as "Copy Editor".')
        others = [
            p for p in home.roles_dir(crew).iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != role
        ]
        for other in others:
            if other.name == new_name or _title_of(other).casefold() == title.casefold():
                raise EditError(
                    f"The {crew_title(home, crew)} already has a member called "
                    f"{_title_of(other)}. Give them another name."
                )
        if new_name != role:
            blocked = check_role(home, crew, role).blocked
            if blocked:
                raise EditError(blocked.replace("delete the role", "rename them"))

    changed = dict(meta)
    if title is not None:
        changed["title"] = title
        changed["name"] = new_name
    if kind is not None:
        changed["kind"] = kind
    if model is not None:
        changed["model"] = model
    if changed != meta:
        mdfile.write(cfg, changed, body)
    if soul is not None:
        write_soul(folder, soul)

    done = Edited(name=new_name)
    if new_name != role:
        specs = _specs_naming(home, crew, role)
        folder.rename(home.roles_dir(crew) / new_name)
        done.renamed_from = role
        for path, smeta, sbody, roles in specs:
            smeta["roles"] = [
                f"{crew}/{new_name}"
                if str(r).strip() == f"{crew}/{role}"
                else new_name
                if str(r).strip() == role
                else r
                for r in roles
            ]
            mdfile.write(path, smeta, sbody)
            done.specs.append(path.stem)
    return done
