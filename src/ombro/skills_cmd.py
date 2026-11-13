"""`ombro skills`: the skills library, who uses each skill, and proposals waiting.

Proposals are decided in the outbox (`ombro outbox show|approve|decline|edit`),
like any staged action; this command lists them so the library and what waits
for it are seen together.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from ombro import outbox, skills
from ombro.home import Home


def _cli():
    from ombro import cli

    return cli


def library_view(home: Home) -> dict[str, Any]:
    """Every skill with where it came from and who uses it, and the proposals waiting."""
    named, every = skills.users(home)
    rows = []
    for s in skills.library(home).values():
        rows.append({"name": s.name, "description": s.description, "source": s.source,
                     "path": str(s.file), "used_by": named.get(s.name, []), "warning": s.warning or None})
    waiting = []
    for a in outbox.waiting(home):
        if a.tool == outbox.SKILL_TOOL and a.status == "pending":
            args = a.arguments if isinstance(a.arguments, dict) else {}
            waiting.append({"ref": a.ref, "name": args.get("name"), "for": list(args.get("for") or []),
                            "change": a.meta.get("change"), "reason": a.meta.get("reason"),
                            "flags": list(a.meta.get("flags") or [])})
    return {"skills": rows, "all_skills": every, "waiting": waiting}


def cmd_skills(args) -> int:
    cli = _cli()
    home = cli._home()
    action = args.action or "list"
    try:
        if action == "list":
            return _list(home, args)
        if action == "show":
            return _show(home, args)
        if action == "edit":
            return _edit(home, args)
        if action == "give":
            return _give(home, args)
    except skills.SkillError as e:
        raise cli.CliError(str(e)) from None
    raise cli.CliError(f"Unknown skills action {action!r}.")


def _list(home: Home, args) -> int:
    view = library_view(home)
    if args.json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0
    if not view["skills"]:
        print(f"No skills yet. A skill is a folder in {home.skills_dir} with a SKILL.md; the Lead can propose "
              "one after a run, or write one in chat when you ask it to remember how to do something.")
    else:
        print(f"Skills ({len(view['skills'])}), in {home.skills_dir}:\n")
        for r in view["skills"]:
            who = ", ".join(r["used_by"]) or ("no role by name" if view["all_skills"] else "no role yet")
            print(f"  {r['name']}  ({'yours' if r['source'] == 'you' else 'from ' + r['source']})")
            print(f"    {r['description'] or '(no description)'}")
            print(f"    Used by: {who}")
            if r["warning"]:
                print(f"    Warning: {r['warning']}")
        if view["all_skills"]:
            print(f"\nEvery skill is used by: {', '.join(view['all_skills'])} (skills: all).")
    if view["waiting"]:
        print(f"\nProposed, waiting for you ({len(view['waiting'])}):")
        for w in view["waiting"]:
            what = "a new version of" if w["change"] == "update" else "new:"
            print(f"  {w['ref']:<10} {what} {w['name']}" + (f", for {', '.join(w['for'])}" if w["for"] else ""))
        print("\nSee one with `ombro outbox show <ref>`, then `ombro outbox approve <ref>` or `decline <ref>`.")
    print("\nGive a skill to a role with `ombro skills give <skill> <crew/role>`.")
    return 0


def _one(home: Home, args) -> skills.Skill:
    if not args.name:
        raise skills.SkillError("name a skill, e.g. `ombro skills show podcast-outreach`")
    s = skills.load(home, args.name)
    if s is None:
        have = ", ".join(skills.library(home)) or "none yet"
        raise skills.SkillError(f"there is no skill named {args.name!r}. Skills: {have}")
    return s


def _show(home: Home, args) -> int:
    s = _one(home, args)
    named, every = skills.users(home)
    if args.json:
        print(json.dumps({"name": s.name, "description": s.description, "source": s.source, "path": str(s.file),
                          "used_by": named.get(s.name, []), "all_skills": every, "text": s.text},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{s.name}   {s.file}")
    print(f"Used by: {', '.join(named.get(s.name, [])) or 'no role by name'}"
          + (f"; and every role with skills: all ({', '.join(every)})" if every else ""))
    print()
    print(s.text.rstrip())
    return 0


def _edit(home: Home, args) -> int:
    cli = _cli()
    s = _one(home, args)
    from ombro.interview import editor_command

    cmd = editor_command(home)
    if not cmd:
        print(f"No editor is set (`editor` in preferences/config.md, or $EDITOR). The skill's file:\n  {s.file}")
        return 0
    try:
        subprocess.run([*cmd, str(s.file)], check=False)
    except OSError as e:
        raise cli.CliError(f"Could not start the editor {cmd[0]!r}: {e}") from None
    s = skills.load(home, s.name)
    if s is None or not s.description or not s.body:
        print("The skill needs a description and a method; roles will see it without one until it is fixed.")
        return 1
    print(f"{s.name} is valid.")
    return 0


def _give(home: Home, args) -> int:
    if not args.name or not args.roles:
        raise skills.SkillError("name a skill and one or more roles, e.g. "
                                "`ombro skills give podcast-outreach default/researcher`")
    for who in args.roles:
        print(skills.give(home, args.name, who).capitalize() + ".")
    return 0
