"""Deleting a crew, a role or a skill (plan C4): never erased, always moved aside into
removed/, where it can be put back by hand.

Each has a check first, which an interface shows before asking: what stops it (a job of
that crew still running) and what to know (schedules and saved briefs that name the crew,
roles that use the skill). The remove functions run the same check and refuse what it
refuses, so a caller that skipped it is still safe.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ombro import jobs, mdfile
from ombro.home import Home, crew_phrase
from ombro.settings import SetupError, load_preferences, set_preference


class RemoveError(SetupError):
    """Why something can't be removed now, in words for the user."""


@dataclass
class Check:
    blocked: str | None = None  # why it can't be removed now
    warnings: list[str] = field(default_factory=list)  # what to know before removing it

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "warnings": list(self.warnings)}


def _aside(source: Path, folder: Path) -> Path:
    """Move a file or folder into `folder`, under its own name or, if that is taken, with the time."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if target.exists():
        target = folder / f"{source.stem}-{time.strftime('%Y%m%d-%H%M%S')}{source.suffix}"
    shutil.move(str(source), str(target))
    return target


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def running_jobs(home: Home, crew: str) -> list[str]:
    """The readable names of the crew's jobs that are running now."""
    out = []
    for d in jobs.all_jobs(home, crew):
        try:
            meta, _ = jobs.read_job(d)
        except (OSError, mdfile.FrontMatterError):
            continue
        if meta.get("status") == "running" and not jobs.is_dead(meta):
            out.append(jobs.job_name(d.name))
    return out


# ---------------------------------------------------------------------------
# Crews


def check_crew(home: Home, crew: str) -> Check:
    from ombro import schedules
    from ombro.cli import saved_specs

    if crew not in home.crews():
        raise LookupError(f"no crew called {crew!r}")
    c = Check()
    running = running_jobs(home, crew)
    if running:
        c.blocked = (f"A job of {crew_phrase(home, crew)} is still running ({', '.join(running)}). "
                     "Stop it, or wait for it to finish, then delete the crew.")
    elif len(home.crews()) == 1:
        c.blocked = "It's your only crew. Ombro needs at least one; add another crew first."
    names = [s.name for s in schedules.all_schedules(home, crew)]
    if names:
        c.warnings.append(f"{_plural(len(names), 'schedule')} run{'s' if len(names) == 1 else ''} work for this "
                          f"crew ({', '.join(names)}). They will fail until you change or delete them.")
    specs = [name for name, _ in saved_specs(home, crew)]
    if specs:
        c.warnings.append(f"{_plural(len(specs), 'saved brief')} name{'s' if len(specs) == 1 else ''} this crew "
                          f"({', '.join(specs[:5])}{'…' if len(specs) > 5 else ''}).")
    if crew == load_preferences(home).default_crew and len(home.crews()) > 1:
        other = sorted(c for c in home.crews() if c != crew)[0]
        c.warnings.append(f"It's your default crew; {crew_phrase(home, other)} becomes the default.")
    return c


def remove_crew(home: Home, crew: str) -> Path:
    """Move the crew's folder, roles and all, to removed/crews/. Returns where it went."""
    c = check_crew(home, crew)
    if c.blocked:
        raise RemoveError(c.blocked)
    if crew == load_preferences(home).default_crew:
        set_preference(home, "default_crew", sorted(x for x in home.crews() if x != crew)[0])
    return _aside(home.crew_dir(crew), home.removed_dir / "crews")


# ---------------------------------------------------------------------------
# Roles


def role_folder(home: Home, crew: str, role: str) -> Path:
    from ombro.roles import NAME

    if role == "lead":
        raise RemoveError("The Lead can't be removed; there is one, above every crew.")
    folder = home.roles_dir(crew) / role
    if crew not in home.crews() or not NAME.match(role) or not folder.is_dir():
        raise LookupError(f"no role {crew}/{role}")
    return folder


def check_role(home: Home, crew: str, role: str) -> Check:
    role_folder(home, crew, role)
    c = Check()
    running = running_jobs(home, crew)
    if running:
        c.blocked = (f"A job of {crew_phrase(home, crew)} is still running ({', '.join(running)}). "
                     "Stop it, or wait for it to finish, then delete the role.")
    return c


def remove_role(home: Home, crew: str, role: str) -> Path:
    """Move a role's folder to the crew's removed/ folder, as `ombro role remove` does."""
    folder = role_folder(home, crew, role)
    c = check_role(home, crew, role)
    if c.blocked:
        raise RemoveError(c.blocked)
    return _aside(folder, home.crew_dir(crew) / "removed")


# ---------------------------------------------------------------------------
# Skills


def check_skill(home: Home, name: str) -> Check:
    from ombro import skills

    if skills.load(home, name) is None:
        raise LookupError(f"no skill called {name!r}")
    named, _ = skills.users(home)
    c = Check()
    who = named.get(name, [])
    if who:
        c.warnings.append(f"{_plural(len(who), 'role')} use{'s' if len(who) == 1 else ''} it "
                          f"({', '.join(who)}). They carry on without it.")
    return c


def remove_skill(home: Home, name: str) -> Path:
    """Move a skill's folder to removed/skills/. Roles that list it carry on without it."""
    check_skill(home, name)
    return _aside(home.skills_dir / name, home.removed_dir / "skills")
