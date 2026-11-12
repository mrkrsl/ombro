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
from ombro.ui import UI, ConsoleUI, Field, Option


def _cli():
    from ombro import cli

    return cli


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
            return add_flow(home, args.target, args.name, args.access, args.yes, ConsoleUI())
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


def access_field() -> Field:
    return Field("access", "Access:", "choice", default="n",
                 options=[Option("r", "read"), Option("w", "write (roles can change, add and remove files in it)"),
                          Option("n", "no", ("q",))],
                 invalid="Type r or w, or n to stop.")


def add_flow(home: Home, target: str, name: str | None, access: str | None, yes: bool, ui: UI) -> int:
    """`ombro folders add`: grant a folder, asking for what is not given."""
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
            try:
                access = {"r": "read", "w": "write"}.get(ui.ask(access_field()))
            except (KeyboardInterrupt, EOFError):
                access = None
            if access is None:
                ui.say("Nothing shared.")
                return 1
    if access == "write" and not yes:
        ui.say(f"Backup before each run that can write to it: {folders.backup_hint(real, home)}.")
        try:
            go = ui.yes("write", f"Let runs change files in {folders.display_path(real)}?")
        except (KeyboardInterrupt, EOFError):
            go = False
        if not go:
            ui.say("Nothing shared.")
            return 1
    g = folders.add(home, real, name, access)
    ui.say(f"Shared {g.path} as {g.name!r} ({g.access}). A spec uses it with `folders: [{g.name}]`.", level="done")
    return 0


def offer_start_folder(home: Home, cwd: Path | None = None, ask: Callable[[str], str] | None = None,
                       say: Callable[[str], None] | None = None, ui: UI | None = None) -> None:
    """Before a brief in a folder that is not shared and could be, ask once. A no is
    remembered in folders.md and not asked again; just pressing Enter asks next time."""
    if ui is None:
        if ask is None and not sys.stdin.isatty():
            return
        ui = ConsoleUI(ask=ask, say=say)
    here = (cwd or Path.cwd()).resolve()
    if not folders.offer_wanted(home, here):
        return
    shown = folders.display_path(here)
    ui.say(f"You are in {shown}, which is not shared with Ombro's runs.")
    try:
        choice = ui.ask(Field(
            "share_here", "Share it?", "choice", default="s",
            options=[Option("r", "read"), Option("w", "write (roles can change files in it)"),
                     Option("n", "no (don't ask again)"), Option("s", "skip for now")]))
    except (KeyboardInterrupt, EOFError):
        return
    say = ui.say
    if choice == "n":
        folders.decline(home, here)
        say("Not shared, and Ombro will not ask about this folder again (`ombro folders add` shares it later).")
        return
    if choice not in ("r", "w"):
        return
    access = "write" if choice == "w" else "read"
    try:
        grants, _ = folders.load(home)
        g = folders.add(home, here, folders.suggest_name(here, set(grants)), access)
    except (folders.FolderError, SetupError) as e:
        say(f"It could not be shared: {e}")
        return
    say(f"Shared as {g.name!r} ({access})" + (f"; backup: {folders.backup_hint(here, home)}" if access == "write"
                                              else "") + ". The brief can now name it.")
