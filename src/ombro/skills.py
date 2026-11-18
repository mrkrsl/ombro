"""Skills: one library of reusable methods, in the open Agent Skills format.

A skill is a folder in the home's skills/ with a SKILL.md: YAML front matter
holding `name` and `description`, then a Markdown body, the method. An
optional references/ folder holds longer material the method points to.
An older skill.md is read the same way.

Prompts carry only each skill's name and description; the method is read on
demand with `skills.read`, so a growing library does not grow the cost of a
job. A role uses the skills its `skills:` line names, or every skill with
`skills: all`.

A skill a model writes never becomes live without the user's yes: proposals
from a job wait in its outbox (ombro.outbox), and chat writes one only after
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


def pasted(body: str) -> tuple[dict[str, Any], str]:
    """A method typed or pasted into a form, which may be a whole SKILL.md: (its front matter, the
    method without it). Text with no front matter comes back as it is."""
    try:
        meta, rest = mdfile.parse(str(body or "").lstrip())
    except mdfile.FrontMatterError as e:
        raise SkillError(f"The instructions start with front matter (the lines between ---) that can't be read: "
                         f"{str(e).removeprefix('invalid front matter: ')}. Fix it, or take it out.") from None
    if not meta:
        return {}, str(body or "")
    return meta, rest


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
# Proposals the user declined. The Lead is shown them and never makes them again: a new skill
# under a declined name, or another change to a version of a skill whose change was declined.
# skills/declined.md is the user's to edit; taking an entry out lets it be proposed again.

DECLINED_FILE = "declined.md"
DECLINED_BODY = """\
Skill proposals you declined in Approvals. The Lead is shown this list after
each job and does not propose these again: not a new skill under a name here,
and not another change to the same version of a skill. Remove an entry to let
it be proposed again.
"""


def declined_path(home: Home) -> Path:
    return home.skills_dir / DECLINED_FILE


def declined(home: Home) -> list[dict[str, Any]]:
    try:
        meta, _ = mdfile.read(declined_path(home))
    except (OSError, mdfile.FrontMatterError):
        return []
    items = meta.get("declined")
    return [x for x in items if isinstance(x, dict) and x.get("name")] if isinstance(items, list) else []


def record_declined(home: Home, *, name: str, description: str, change: str, base_sha: str, job: str,
                    date: str) -> None:
    items = declined(home)
    entry = {"name": name, "description": " ".join(description.split())[:SHORT_DESCRIPTION], "change": change,
             "declined": date, "job": job}
    if change == "update":
        entry["base_sha256"] = base_sha
    if any(x.get("name") == name and x.get("change") == change and x.get("base_sha256", "") == entry.get(
            "base_sha256", "") for x in items):
        return
    home.skills_dir.mkdir(parents=True, exist_ok=True)
    mdfile.write(declined_path(home), {"declined": [*items, entry]}, DECLINED_BODY)


def declined_block(home: Home, name: str) -> str:
    """Why a proposal for `name` would only repeat one the user declined, or "" if it would not.
    A new skill under a declined name is refused while no skill of that name is live; a change
    to a live skill is refused while the live version is the one a declined change was made to."""
    live = live_sha(home, name)
    for x in declined(home):
        if x.get("name") != name:
            continue
        if not live and x.get("change") != "update":
            return f"the user declined a new skill called {name} on {x.get('declined')}; do not propose it again"
        if live and x.get("change") == "update" and x.get("base_sha256") == live:
            return (f"the user declined a change to {name} on {x.get('declined')} and the skill has not changed "
                    "since; do not propose another change to it")
    return ""


def declined_listing(home: Home) -> str:
    return "\n".join(f"- {x['name']} ({'a change to it' if x.get('change') == 'update' else 'new'}, declined "
                     f"{x.get('declined', '')}): {x.get('description') or '(no description)'}"
                     for x in declined(home) if declined_block(home, str(x["name"])))


# ---------------------------------------------------------------------------
# Importing a skill found elsewhere. It is someone else's instructions, which roles
# would follow unfenced, so the user sees it all, and any signs of injected text,
# before it is copied in, and it is recorded as imported, not as theirs.

REFERENCE_MAX_BYTES = 1_000_000


@dataclass
class Import:
    name: str
    text: str
    source: Path  # the folder it came from
    references: list[str]  # files under references/ that will be copied
    skipped: list[str]  # files that will not be (scripts/, assets/, links, binary or large files)
    flags: list[str]  # signs of injected text, in SKILL.md or a reference
    live: Skill | None  # the skill it would replace, if there is one


def inspect_import(home: Home, path: str | Path) -> Import:
    """Check a skill folder (or its SKILL.md) outside the home, without copying anything."""
    from ombro import material

    src = Path(os.path.expanduser(str(path))).resolve()
    folder = src.parent if src.is_file() else src
    f = src if src.is_file() else skill_file(folder)
    if f is None or not f.is_file():
        raise SkillError(f"no SKILL.md in {folder}")
    if folder == home.skills_dir.resolve() or folder.is_relative_to(home.skills_dir.resolve()):
        raise SkillError(f"{folder} is already in the skills library")
    try:
        text = f.read_text(encoding="utf-8")
        meta, _ = mdfile.parse(text)
    except (OSError, UnicodeDecodeError, mdfile.FrontMatterError) as e:
        raise SkillError(f"{f} could not be read: {e}") from None
    name = str(meta.get("name") or folder.name)
    check(text, name, max_chars=None)
    flags = list(material.flags(text))
    references, skipped = [], []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p == f or (p.is_dir() and not p.is_symlink()) or any(x.startswith(".") for x in rel.split("/")):
            continue
        if not rel.startswith("references/") or p.is_symlink() or not p.is_file() \
                or p.stat().st_size > REFERENCE_MAX_BYTES:
            skipped.append(rel)
            continue
        try:
            ref_text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped.append(rel)
            continue
        references.append(rel)
        flags += [x for x in material.flags(ref_text) if x not in flags]
    return Import(name, text, folder, references, skipped, flags, load(home, name))


def add(home: Home, imp: Import, accepted: str, label: str | None = None) -> Path | None:
    """Copy a checked import into the library. Returns where the old version was kept, if any.
    `label` says where it came from when the folder it was checked in is a copy (an upload)."""
    kept = write(home, imp.name, imp.text, source=f"imported from {label or imp.source}", accepted=accepted)
    old_refs = home.skills_dir / imp.name / "references"
    if kept is not None and old_refs.is_dir() and not old_refs.is_symlink():
        shutil.rmtree(old_refs)  # the old version's references are in the archive; the import brings its own
    for rel in imp.references:
        dest = home.skills_dir / imp.name / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(imp.source / rel, dest, follow_symlinks=False)
    return kept


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


def without(home: Home, name: str) -> list[str]:
    """crew/role for every role that doesn't get a skill: those with a list of skills (or none)
    that leaves it out. Roles with `skills: all` get every skill."""
    out = []
    for crew in home.crews():
        rdir = home.roles_dir(crew)
        if not rdir.is_dir():
            continue
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                meta, _ = mdfile.read(folder / "config.md")
            except (OSError, mdfile.FrontMatterError):
                continue
            have = meta.get("skills")
            if have == "all" or (isinstance(have, list) and name in [str(x) for x in have]):
                continue
            out.append(f"{crew}/{folder.name}")
    return out
