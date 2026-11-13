"""Skills: one library of reusable methods, in the open Agent Skills format.

A skill is a folder in the home's skills/ with a SKILL.md: YAML front matter
holding `name` and `description`, then a Markdown body, the method. An
optional references/ folder holds longer material the method points to.
An older skill.md is read the same way.

Prompts carry only each skill's name and description; the method is read on
demand with `skills.read`, so a growing library does not grow the cost of a
run. A role uses the skills its `skills:` line names, or every skill with
`skills: all`.

A skill a model writes never becomes live without the user's yes: proposals
from a run wait in its outbox (ombro.outbox), and chat writes one only after
the user confirms it. Replacing a skill keeps the old version in
skills/Archive/<name>/<date-time>/.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ombro import mdfile
from ombro.home import Home

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1_024
FILE_NAMES = ("SKILL.md", "skill.md")
ARCHIVE = "Archive"
SHORT_DESCRIPTION = 300

# Defaults; limits.md can change each (settings.SKILL_DEFAULTS).
LIST_CHARS = 4_000
READ_CHARS = 20_000
MAX_CHARS = 10_000
PROPOSALS_WAITING = 10


class SkillError(Exception):
    """Shown to the user, or to the model, as it is."""


@dataclass
class Skill:
    name: str
    description: str
    folder: Path
    file: Path
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    warning: str = ""

    @property
    def source(self) -> str:
        md = self.meta.get("metadata")
        src = md.get("source") if isinstance(md, dict) else None
        return str(src) if src else "you"

    @property
    def text(self) -> str:
        return self.file.read_text(encoding="utf-8")


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:DESCRIPTION_MAX]


def skill_file(folder: Path) -> Path | None:
    """SKILL.md, or an older skill.md, spelt as it is on disk (a case-insensitive
    disk has one file under either name)."""
    try:
        names = set(os.listdir(folder))
    except OSError:
        return None
    for n in FILE_NAMES:
        if n in names and (folder / n).is_file():
            return folder / n
    return None


def load(home: Home, name: str) -> Skill | None:
    if not NAME.match(name or "") or len(name) > NAME_MAX:
        return None
    folder = home.skills_dir / name
    f = skill_file(folder)
    if f is None:
        return None
    try:
        meta, body = mdfile.read(f)
    except (OSError, mdfile.FrontMatterError) as e:
        return Skill(name, "", folder, f, {}, "", warning=f"{f} could not be read: {e}")
    body = mdfile.prompt_text(body)
    description = " ".join(str(meta.get("description") or "").split()) or _first_sentence(body)
    warning = ""
    if meta.get("name") and str(meta["name"]) != name:
        warning = f"{f}: its name {meta['name']!r} differs from its folder {name!r}; the folder's is used"
    return Skill(name, description, folder, f, meta, body, warning)


def library(home: Home) -> dict[str, Skill]:
    """Every skill, by name. Archive/ and folders whose names are not skill names are left out."""
    out: dict[str, Skill] = {}
    if not home.skills_dir.is_dir():
        return out
    for d in sorted(home.skills_dir.iterdir()):
        if d.is_dir() and NAME.match(d.name):
            s = load(home, d.name)
            if s is not None:
                out[d.name] = s
    return out


def names_for(home: Home, role) -> tuple[list[str], list[str]]:
    """(the skills a role may use that exist, those it lists that do not)."""
    lib = library(home)
    if getattr(role, "all_skills", False):
        return list(lib), []
    have = [s for s in role.skills if s in lib]
    return have, [s for s in role.skills if s not in lib]


def listing(skills: list[Skill], cap: int = LIST_CHARS) -> tuple[str, list[str]]:
    """Name and description of each skill, one per line, within `cap` characters.
    Past the cap descriptions are shortened, then skills left out. Returns (text, left out)."""
    def line(s: Skill, n: int | None) -> str:
        d = s.description
        if n is not None and len(d) > n:
            d = d[: n - 1].rstrip() + "…"
        return f"- {s.name}: {d or '(no description)'}"

    lines = [line(s, None) for s in skills]
    if sum(len(x) + 1 for x in lines) > cap:
        lines = [line(s, SHORT_DESCRIPTION) for s in skills]
    out, left, used = [], [], 0
    for s, x in zip(skills, lines):
        if used + len(x) + 1 > cap:
            left.append(s.name)
            continue
        out.append(x)
        used += len(x) + 1
    return "\n".join(out), left


def read(home: Home, allowed: list[str] | None, name: Any, file: Any = None, cap: int = READ_CHARS) -> dict:
    """A skill's method, or one of its references/ files. `allowed` None means any skill."""
    name = str(name or "").strip()
    if allowed is not None and name not in allowed:
        have = ", ".join(allowed) or "none"
        raise SkillError(f"{name!r} is not one of your skills. Yours: {have}")
    s = load(home, name)
    if s is None:
        raise SkillError(f"there is no skill named {name!r}")
    if not file:
        text = s.body
        out: dict[str, Any] = {"skill": s.name, "description": s.description}
        refs = s.folder / "references"
        if refs.is_dir() and not refs.is_symlink():
            out["references"] = sorted(p.relative_to(s.folder).as_posix() for p in refs.rglob("*")
                                       if p.is_file() and not p.is_symlink())[:50]
    else:
        rel = str(file).strip().lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or parts[0] != "references" or ".." in parts:
            raise SkillError("give a file under references/, as the skill's listing names it")
        target = s.folder.joinpath(*parts)
        real = target.resolve()
        if not real.is_relative_to((s.folder / "references").resolve()) or not real.is_file():
            raise SkillError(f"{name} has no file {rel}")
        try:
            text = real.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise SkillError(f"{rel} could not be read as text: {e}") from None
        out = {"skill": s.name, "file": "/".join(parts)}
    if len(text) > cap:
        text = text[:cap] + f"\n…[cut at {cap:,} characters]"
    out["text"] = text
    return out


# ---------------------------------------------------------------------------
# Writing


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def live_sha(home: Home, name: str) -> str:
    """The live SKILL.md's hash, or "" if there is none."""
    s = load(home, name)
    try:
        return sha(s.text) if s else ""
    except OSError:
        return ""


def compose(name: str, description: str, body: str, metadata: dict[str, Any] | None = None) -> str:
    meta: dict[str, Any] = {"name": name, "description": " ".join(str(description).split())}
    if metadata:
        meta["metadata"] = metadata
    return mdfile.dump(meta, body.strip() + "\n")


def check(text: str, name: str, max_chars: int | None = MAX_CHARS) -> tuple[dict, str]:
    """A SKILL.md a model wrote, or the user edited, before it is staged or made live."""
    if not NAME.match(name or "") or len(name) > NAME_MAX:
        raise SkillError(f"{name!r} is not a skill name: lower-case letters, digits and single hyphens, "
                         f"at most {NAME_MAX} characters")
    if name == ARCHIVE.lower():
        raise SkillError(f"{name!r} is kept for the archive of old versions; choose another name")
    if max_chars is not None and len(text) > max_chars:
        raise SkillError(f"the skill is {len(text):,} characters, over the cap of {max_chars:,}; condense it")
    try:
        meta, body = mdfile.parse(text)
    except mdfile.FrontMatterError as e:
        raise SkillError(str(e)) from None
    if str(meta.get("name") or "") != name:
        raise SkillError(f"its front matter must say `name: {name}`")
    desc = " ".join(str(meta.get("description") or "").split())
    if not desc:
        raise SkillError("it needs a description: what it does and when to use it")
    if len(desc) > DESCRIPTION_MAX:
        raise SkillError(f"its description is {len(desc):,} characters, over {DESCRIPTION_MAX:,}")
    if not mdfile.prompt_text(body):
        raise SkillError("its method (the text after the front matter) is empty")
    return meta, body


def archive(home: Home, name: str) -> Path | None:
    """Copy a live skill's folder into skills/Archive/<name>/<date-time>/. Never replaces one there."""
    folder = home.skills_dir / name
    if skill_file(folder) is None:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = home.skills_dir / ARCHIVE / name / stamp
    n = 2
    while dest.exists():
        dest = home.skills_dir / ARCHIVE / name / f"{stamp}-{n}"
        n += 1
    shutil.copytree(folder, dest, symlinks=True)
    return dest


def write(home: Home, name: str, text: str, *, source: str, accepted: str) -> Path | None:
    """Make a skill live: archive the old version, then write SKILL.md with where it came from.
    Returns where the old version was kept, if there was one."""
    meta, body = check(text, name, max_chars=None)
    md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    meta["metadata"] = {**md, "source": source, "accepted": accepted}
    kept = archive(home, name)
    folder = home.skills_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    old = skill_file(folder)
    if old is not None and old.name != "SKILL.md":
        old.unlink()  # its text is in the archive; the new one is SKILL.md
    mdfile.write(folder / "SKILL.md", meta, body)
    return kept


def diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        f"{name} (now)", f"{name} (proposed)"))


# ---------------------------------------------------------------------------
# Who uses which


def _config_path(home: Home, who: str) -> Path:
    from ombro.roles import NAME as ROLE_NAME

    crew, _, role = who.strip().partition("/")
    if crew not in home.crews() or not ROLE_NAME.match(role):
        raise SkillError(f"there is no role {who!r}; name a role as crew/role")
    return home.roles_dir(crew) / role / "config.md"


def give(home: Home, skill: str, who: str) -> str:
    """Add a skill to a role's `skills:` line. Returns what was done, in words."""
    if load(home, skill) is None:
        raise SkillError(f"there is no skill named {skill!r}")
    path = _config_path(home, who)
    if not path.is_file():
        raise SkillError(f"there is no role {who!r}")
    meta, body = mdfile.read(path)
    current = meta.get("skills")
    if current == "all":
        return f"{who} already uses every skill"
    have = [str(s) for s in current] if isinstance(current, list) else []
    if skill in have:
        return f"{who} already has {skill}"
    meta["skills"] = [*have, skill]
    mdfile.write(path, meta, body)
    return f"gave {skill} to {who}"


def users(home: Home) -> tuple[dict[str, list[str]], list[str]]:
    """({skill: [crew/role that lists it]}, [crew/role with `skills: all`])."""
    named: dict[str, list[str]] = {}
    every: list[str] = []
    for crew in home.crews():
        rdir = home.roles_dir(crew)
        if not rdir.is_dir():
            continue
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                meta, _ = mdfile.read(folder / "config.md")
            except (OSError, mdfile.FrontMatterError):
                continue
            who = f"{crew}/{folder.name}"
            s = meta.get("skills")
            if s == "all":
                every.append(who)
            elif isinstance(s, list):
                for x in s:
                    named.setdefault(str(x), []).append(who)
    return named, every
