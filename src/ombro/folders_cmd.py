"""`ombro folders`: list, grant and remove shared folders. And the offer to share
the folder a brief starts in.

Only the user grants. Every grant is asked for here, in the brief interview,
or written by hand in folders.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from ombro import folders
from ombro.home import Home
from ombro.settings import SetupError


def _cli():
    from ombro import cli

    return cli


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


def cmd_folders(args) -> int:
    cli = _cli()
    home = cli._home()
    action = args.action or "list"
    try:
        if action == "list":
            return _list(home)
        if action == "add":
            if not args.target:
                raise cli.CliError("Give the folder: `ombro folders add ~/Documents/Brand` (or `.` for this one).")
            return _add(home, args.target, args.name, args.access, args.yes)
        if action == "remove":
            if not args.target:
                raise cli.CliError("Name the shared folder: `ombro folders remove <name>`.")
            g = folders.remove(home, args.target)
            print(f"Removed {g.name!r} ({g.path}). Runs already going keep it; new runs do not get it.")
            return 0
    except folders.FolderError as e:
        raise cli.CliError(str(e)) from None
    except SetupError as e:
        raise cli.CliError(f"folders.md has a problem: {e}") from None
    raise cli.CliError(f"unknown action {action!r}; use list, add or remove")


def _list(home: Home) -> int:
    grants, declined = folders.load(home)
    if not grants:
        print("No folders are shared. Runs reach only their own workspace.")
        print("Share one with `ombro folders add <path>`; a spec then names it with `folders: [<name>]`.")
    else:
        print("Shared folders. A spec names the ones its run uses (`folders: [name]`).\n")
        for g in grants.values():
            try:
                folders.check_place(g.path, home)
                problem = ""
            except folders.FolderError as e:
                problem = f"\n      not usable: {e}"
            backup = folders.backup_hint(g.path, home) if g.access == "write" else "not needed (read only)"
            print(f"  {g.name:<16} {g.access:<6} {g.path}\n      backup: {backup}{problem}")
    if declined:
        print("\nDeclined (Ombro will not offer them again): " + ", ".join(declined))
    print(f"\nFile: {home.folders}")
    return 0


def _choose_access(ask: Callable[[str], str]) -> str | None:
    while True:
        a = ask("Access: [r]ead, or [w]rite (roles can change, add and remove files in it)? ").lower()
        if a in ("r", "read"):
            return "read"
        if a in ("w", "write"):
            return "write"
        if a in ("", "q", "n", "no"):
            return None
        print("Type r or w.")


def _add(home: Home, target: str, name: str | None, access: str | None, yes: bool) -> int:
    path = Path(os.path.expanduser(target))
    if not path.is_absolute():
        path = Path.cwd() / path
    real = folders.check_place(path, home)
    grants, _ = folders.load(home)
    name = name or folders.suggest_name(real, set(grants))
    if access is None:
        if yes:
            access = "read"
        else:
            access = _choose_access(_ask)
            if access is None:
                print("Nothing shared.")
                return 1
    if access == "write" and not yes:
        print(f"Backup before each run that can write to it: {folders.backup_hint(real, home)}.")
        if _ask(f"Let runs change files in {folders.display_path(real)}? [y/N] ").lower() not in ("y", "yes"):
            print("Nothing shared.")
            return 1
    g = folders.add(home, real, name, access)
    print(f"Shared {g.path} as {g.name!r} ({g.access}). A spec uses it with `folders: [{g.name}]`.")
    return 0


def offer_start_folder(home: Home, cwd: Path | None = None, ask: Callable[[str], str] | None = None,
                       say: Callable[[str], None] = print) -> None:
    """Before a brief in a folder that is not shared and could be, ask once. A no is
    remembered in folders.md and not asked again; just pressing Enter asks next time."""
    if ask is None:
        if not sys.stdin.isatty():
            return
        ask = _ask
    here = (cwd or Path.cwd()).resolve()
    if not folders.offer_wanted(home, here):
        return
    shown = folders.display_path(here)
    say(f"You are in {shown}, which is not shared with Ombro's runs.")
    choice = ask("Share it? [r]ead, [w]rite (roles can change files in it), [n]o (don't ask again), "
                 "or Enter to skip: ").lower()
    if choice in ("n", "no"):
        folders.decline(home, here)
        say("Not shared, and Ombro will not ask about this folder again (`ombro folders add` shares it later).")
        return
    if choice not in ("r", "read", "w", "write"):
        return
    access = "write" if choice.startswith("w") else "read"
    try:
        grants, _ = folders.load(home)
        g = folders.add(home, here, folders.suggest_name(here, set(grants)), access)
    except (folders.FolderError, SetupError) as e:
        say(f"It could not be shared: {e}")
        return
    say(f"Shared as {g.name!r} ({access})" + (f"; backup: {folders.backup_hint(here, home)}" if access == "write"
                                              else "") + ". The brief can now name it.")
