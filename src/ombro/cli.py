"""The `ombro` command. The complete interface to the core."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ombro import __version__, journal, keys, mdfile, runs
from ombro.home import Home
from ombro.settings import SetupError, format_duration, format_tokens, load_limits, load_preferences, load_tool_settings


class CliError(Exception):
    pass


def _home(require: bool = True) -> Home:
    home = Home()
    if require and not home.exists():
        raise CliError(f"No Ombro home at {home.root}. Run `ombro init` first.")
    return home


def _crew(home: Home, name: str | None) -> str:
    crew = name or load_preferences(home).default_crew
    if not home.crew_dir(crew).is_dir():
        raise CliError(f"No crew named {crew!r}. Crews: {', '.join(home.crews()) or 'none'}")
    return crew


def _resolve_run(home: Home, run_id: str | None, crew: str | None) -> Path:
    try:
        if run_id:
            return runs.find_run(home, run_id)
        return runs.latest_run(home, _crew(home, crew))
    except runs.RunNotFound as e:
        raise CliError(str(e)) from None


def _reap(home: Home) -> None:
    for run_dir in runs.reap_dead_runs(home):
        print(f"Run {run_dir.name} had stopped without finishing; marked failed and wrote its report.", file=sys.stderr)


# ---------------------------------------------------------------------------
# init


def cmd_init(args) -> int:
    from ombro.init_cmd import run_init

    try:
        ok = run_init(Home())
    except (KeyboardInterrupt, EOFError):
        print("\nSet-up stopped. Run `ombro init` again to finish connecting a model.")
        return 1
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# run


def _echo(event: dict) -> None:
    if event.get("action") in ("model_call",):
        return
    print(journal.format_event(event), flush=True)


def _install_stop_handlers(engine) -> None:
    def on_term(signum, frame):
        engine.request_stop("stopped with `ombro stop`")

    def on_int(signum, frame):
        if engine.gate.stop_requested:
            raise KeyboardInterrupt
        print("\nStopping after the current call; the report will be written. Ctrl-C again to abandon.", flush=True)
        engine.request_stop("stopped with Ctrl-C")

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_int)


def _spec_path(home: Home, value: str, crew: str | None) -> Path:
    """A spec file, or the name of a spec saved in the crew's specs/ folder."""
    path = Path(value).expanduser()
    if path.is_file():
        return path
    saved = home.crew_dir(_crew(home, crew)) / "specs" / (value if value.endswith(".md") else f"{value}.md")
    if "/" not in value and saved.is_file():
        return saved
    names = [n for n, _ in saved_specs(home, _crew(home, crew))]
    listed = f" Saved specs: {', '.join(names)}." if names else " No specs are saved yet; make one with `ombro brief`."
    raise CliError(f"No spec file at {path}, and no saved spec named {value!r}.{listed}")


def saved_specs(home: Home, crew: str) -> list[tuple[str, Path]]:
    folder = home.crew_dir(crew) / "specs"
    if not folder.is_dir():
        return []
    files = [f for f in folder.glob("*.md") if not f.name.endswith(".previous.md")]
    return [(f.stem, f) for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)]


def cmd_specs(args) -> int:
    home = _home()
    crew = _crew(home, args.crew)
    rows = saved_specs(home, crew)
    if not rows:
        print(f"No specs saved in crew {crew}. Make one with `ombro brief`.")
        return 0
    print(f"Specs in crew {crew}, newest first:\n")
    for name, path in rows:
        _, body = mdfile.read(path)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        print(f"  {name:<28} {when}  {runs.first_line(body, 70)}")
    print("\nRun one with `ombro run --spec <name>`.")
    return 0


def _require_model(home: Home) -> None:
    from ombro.init_cmd import connected_model

    if connected_model(home) is None:
        raise CliError("No model is connected. Run `ombro init`.")


def cmd_run(args) -> int:
    home = _home()
    _reap(home)
    if args.spec and args.skip_interview:
        raise CliError("Give either --spec or --skip-interview, not both.")
    interview = None
    if args.spec:
        path = _spec_path(home, args.spec, args.crew)
        spec_meta, spec_body = mdfile.read(path)
        source = f"spec file {path.resolve()}"
    elif args.skip_interview:
        spec_meta, spec_body = {}, args.skip_interview
        source = "--skip-interview (no interview was held)"
    else:
        _require_model(home)
        crew = _crew(home, args.crew)
        outcome = _brief_interview(home, crew, "", then_run=True)
        if outcome.status == "insufficient" or outcome.status == "limit":
            from ombro.interviews import record_insufficient_brief

            tool_settings = load_tool_settings(home)
            run_dir = record_insufficient_brief(
                home, crew, load_limits(home, None, tool_settings), outcome, _first_answer(outcome),
                tools=tool_settings.to_dict(),
            )
            print(f"\nRun {run_dir.name} ended: insufficient_brief\n")
            print((run_dir / "report.md").read_text(encoding="utf-8"))
            return 1
        if not outcome.confirmed:
            return 1
        path = outcome.proposal.files[0].path
        spec_meta, spec_body = mdfile.read(path)
        source = f"interview; spec saved at {path}"
        interview = outcome
    if not spec_body.strip():
        raise CliError("The spec is empty.")

    crew = _crew(home, args.crew or spec_meta.get("crew"))
    overrides = spec_meta.get("limits") or {}
    if not isinstance(overrides, dict):
        raise CliError("`limits` in a spec must be a mapping, e.g. limits: {allowance: generous}")
    tool_settings = load_tool_settings(home)
    limits = load_limits(home, overrides, tool_settings)
    _require_model(home)

    run_dir = runs.create_run(home, crew, spec_meta, spec_body, limits, source=source, tools=tool_settings.to_dict())
    run_id = run_dir.name
    if interview is not None:
        from ombro.interviews import _interview_spend

        shutil.copy2(interview.transcript, run_dir / "interview.md")
        runs.update_run(run_dir, interview_spend=_interview_spend(interview))
    if args.detach:
        log = open(run_dir / "process.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "ombro", "_exec", str(run_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env={**os.environ, "OMBRO_HOME": str(home.root)},
        )
        log.close()
        # The child records its own pid in run.md; writing it here too would race its updates.
        print(f"Started run {run_id} in the background (pid {proc.pid}).")
        print(f"Follow it with `ombro status {run_id}` or `ombro journal {run_id}`.")
        return 0

    print(f"Run {run_id} (crew {crew}). Ctrl-C stops it and still writes the report.\n")
    status = _execute(home, run_dir, echo=_echo)
    print(f"\nRun {run_id} ended: {status}\n")
    print((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0 if status == "completed" else 1


def _execute(home: Home, run_dir: Path, echo=None) -> str:
    from ombro.engine import Engine

    engine = Engine(home, run_dir, echo=echo)
    _install_stop_handlers(engine)
    return engine.execute()


def cmd_exec(args) -> int:
    home = _home()
    status = _execute(home, Path(args.run_dir))
    return 0 if status == "completed" else 1


# ---------------------------------------------------------------------------
# runs, status, report, journal, stop


def cmd_runs(args) -> int:
    home = _home()
    _reap(home)
    rows = runs.all_runs(home, _crew(home, args.crew) if args.crew else None)
    if not rows:
        print("No runs yet.")
        return 0
    print(f"{'RUN':<24} {'CREW':<12} {'STATUS':<18} {'TOKENS':>9}  BRIEF")
    for crew, run_dir in rows:
        meta, _ = runs.read_run(run_dir)
        tokens = (meta.get("spend") or {}).get("total_tokens", 0)
        print(f"{run_dir.name:<24} {crew:<12} {meta.get('status', '?'):<18} {tokens:>9,}  {meta.get('brief', '')[:60]}")
    return 0


def cmd_status(args) -> int:
    home = _home()
    _reap(home)
    run_dir = _resolve_run(home, args.run_id, args.crew)
    meta, _ = runs.read_run(run_dir)
    lim, sp = meta.get("limits") or {}, meta.get("spend") or {}
    cur = load_preferences(home).display_currency
    usd = sp.get("usd", 0.0)
    money = f"${usd:.4f}" + (f" (≈ {cur['code']} {usd * cur['rate']:.4f})" if cur else "")
    reserve = lim.get("report_reserve", 0)
    print(f"Run       {meta.get('run_id')}  (crew {meta.get('crew')})")
    print(f"Status    {meta.get('status')}" + (f", stopped by {meta['limit_hit']}" if meta.get("limit_hit") else ""))
    print(f"Brief     {meta.get('brief', '')}")
    print(f"Started   {meta.get('started_at') or '-'}   Finished {meta.get('finished_at') or '-'}")
    if meta.get("status") == "running":
        print(f"Process   {meta.get('pid')}")
    print()
    print(f"Allowance {lim.get('allowance', 'custom')}")
    print()
    print(f"{'':<14}{'used':>16}  {'limit':>14}")
    print(f"{'Tokens':<14}{sp.get('total_tokens', 0):>16,}  {format_tokens(lim.get('max_tokens')):>14}")
    print(f"{'Rounds':<14}{sp.get('rounds', 0):>16}  {lim.get('max_rounds', 0):>14}")
    print(
        f"{'Time':<14}{format_duration(sp.get('elapsed_seconds', 0)):>16}  "
        f"{format_duration(lim.get('run_timeout', 0)):>14}"
    )
    if usd:
        print(f"{'Est. cost':<14}{money:>16}  {'(not a limit)':>14}")
    if sp.get("tool_usd"):
        print(f"{'  of it tools':<14}{'$' + format(sp['tool_usd'], '.4f'):>16}")
    print(f"{'Model calls':<14}{sp.get('model_calls', 0):>16}")
    tools = sp.get("tool_calls") or {}
    caps = lim.get("tool_max_calls") or {}
    refused = sp.get("tool_refused") or {}
    for name, n in sorted(tools.items()):
        cap = caps.get(name, caps.get("default", "-"))
        extra = f"  ({refused[name]} refused at the cap)" if refused.get(name) else ""
        print(f"{'  ' + name:<14}{n:>16}  {cap:>14}{extra}")
    print(f"\n{int(reserve * 100)}% of the token limit is held back for the report.")
    return 0


def cmd_report(args) -> int:
    home = _home()
    _reap(home)
    run_dir = _resolve_run(home, args.run_id, args.crew)
    path = run_dir / "report.md"
    if not path.is_file():
        meta, _ = runs.read_run(run_dir)
        print(f"Run {run_dir.name} has no report yet (status {meta.get('status')}).")
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_journal(args) -> int:
    home = _home()
    _reap(home)
    run_dir = _resolve_run(home, args.run_id, args.crew)
    events = journal.read(run_dir / "journal.jsonl")
    if not events:
        print("The journal is empty.")
    for ev in events:
        if ev.get("action") == "model_call" and not args.all:
            continue
        print(journal.format_event(ev))
    return 0


def cmd_stop(args) -> int:
    home = _home()
    _reap(home)
    run_dir = _resolve_run(home, args.run_id, None)
    meta, _ = runs.read_run(run_dir)
    if meta.get("status") != "running":
        print(f"Run {run_dir.name} is not running (status {meta.get('status')}).")
        return 1
    pid = meta.get("pid")
    if not pid and not runs.is_dead(meta):
        print(f"Run {run_dir.name} is still starting; try again in a moment.")
        return 1
    if not pid or not runs.pid_alive(int(pid)):
        runs.reap_dead_runs(home)
        print(f"Run {run_dir.name} had no live process; marked failed and wrote its report.")
        return 0
    os.kill(int(pid), signal.SIGTERM)
    print(f"Asked run {run_dir.name} to stop. It finishes its current call, then writes its report.")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if runs.read_run(run_dir)[0].get("status") != "running":
            print(f"Stopped. Report: {run_dir / 'report.md'}")
            return 0
        time.sleep(0.25)
    print("Still finishing. Check with `ombro status`.")
    return 0


# ---------------------------------------------------------------------------
# open, keys


def _launch(folder: Path) -> bool:
    """Open a folder in the file manager. Returns False where there is no way to."""
    if sys.platform == "darwin":
        cmd = ["open", str(folder)]
    elif sys.platform.startswith("win"):
        os.startfile(str(folder))  # type: ignore[attr-defined]
        return True
    elif shutil.which("xdg-open"):
        cmd = ["xdg-open", str(folder)]
    else:
        return False
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def cmd_open(args) -> int:
    home = _home()
    run_dir = _resolve_run(home, args.run_id, args.crew)
    folder = run_dir / "workspace"
    folder.mkdir(exist_ok=True)
    print(folder)
    if not args.print_only and not _launch(folder):
        print("(No file manager could be opened here; the path is above.)")
    return 0


def cmd_keys(args) -> int:
    home = _home()
    action = args.action or "list"
    if action == "list":
        stored = keys.read_env(home)
        uses = keys.key_uses(home)
        names = sorted(set(stored) | set(uses))
        if not names:
            print("No keys are set or needed yet.")
            return 0
        print(f"{'KEY':<26} {'VALUE':<14} USED BY")
        for name in names:
            if stored.get(name):
                value = keys.mask(stored[name])
            elif os.environ.get(name):
                value = keys.mask(os.environ[name]) + " (env)"
            else:
                value = "(not set)"
            print(f"{name:<26} {value:<14} {', '.join(uses.get(name, [])) or '-'}")
        print(f"\nKeys live in {home.env}. Set one with `ombro keys set NAME`.")
        return 0
    if not args.name:
        raise CliError(f"Give the key's name, e.g. `ombro keys {action} FIRECRAWL_API_KEY`.")
    name = args.name.strip()
    if not keys.valid_name(name):
        raise CliError("Key names use capitals, digits and underscores, starting with a letter.")
    if action == "set":
        try:
            value = getpass.getpass(f"Value for {name} (hidden; Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 1
        if not value:
            print("Cancelled; nothing changed.")
            return 1
        keys.set_key(home, name, value)
        print(f"Saved {name} ({keys.mask(value)}). It is used from the next call, even in a run already going.")
        return 0
    if action == "remove":
        removed = keys.remove_key(home, name)
        print(f"Removed {name} from .env." if removed else f"{name} was not in .env.")
        if os.environ.get(name):
            print(f"Note: {name} is also set in your shell environment, and Ombro will still use that.")
        return 0 if removed else 1
    raise CliError(f"Unknown keys action {action!r}; use set or remove.")


# ---------------------------------------------------------------------------
# brief, role, crew, limits


def _first_answer(outcome) -> str:
    return outcome.answers[0] if outcome.answers else ""


def _brief_interview(home: Home, crew: str, task: str, *, then_run: bool):
    from ombro.interviews import BriefPurpose, start

    limits = load_limits(home)
    return start(home, BriefPurpose(home, crew, limits, task, then_run=then_run), crew, limits)


def cmd_brief(args) -> int:
    home = _home()
    _require_model(home)
    crew = _crew(home, args.crew)
    outcome = _brief_interview(home, crew, " ".join(args.text or []), then_run=False)
    if outcome.confirmed:
        name = outcome.proposal.data["name"]
        crew_flag = f" --crew {crew}" if args.crew else ""
        print(f"\nSpec saved as {name!r} ({outcome.proposal.files[0].path}).")
        print(f"Run it with:   ombro run --spec {name}{crew_flag}")
        print("List saved specs with `ombro specs`.")
        return 0
    if outcome.status in ("insufficient", "limit"):
        print("\nThe brief is insufficient, so no spec was saved. The gaps:")
        for g in outcome.gaps or ["No spec was confirmed."]:
            print(f"  - {g}")
    print(f"Transcript: {outcome.transcript}")
    return 1


def _role_rows(home: Home, crew: str) -> list[tuple[str, str]]:
    """(folder name, problem or '') for every role folder, without stopping at a broken one."""
    from ombro.roles import RoleError, load_role

    rows = []
    rdir = home.roles_dir(crew)
    limits = load_limits(home)
    for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            rows.append((folder.name, load_role(folder, limits)))
        except RoleError as e:
            rows.append((folder.name, str(e)))
    return rows


def cmd_role(args) -> int:
    home = _home()
    crew = _crew(home, args.crew)
    action = args.action
    if action == "list":
        rows = _role_rows(home, crew)
        print(f"Roles in crew {crew}:\n")
        print(f"{'NAME':<20} {'KIND':<11} {'MODEL':<9} TOOLS")
        for name, role in rows:
            if isinstance(role, str):
                print(f"{name:<20} {'(broken)':<11} {'':<9} {role}")
                continue
            print(f"{name:<20} {role.kind:<11} {role.model:<9} {', '.join(role.tools) or 'none'}")
            if role.description:
                print(f"{'':<20} {role.description}")
        return 0
    if action == "add":
        from ombro.interviews import RolePurpose, start
        from ombro.roles import NAME

        _require_model(home)
        name = (args.name or "").strip().lower() or None
        if name and not NAME.match(name):
            raise CliError("Role names use lower-case letters, digits, - and _.")
        if name and home.roles_dir(crew).joinpath(name).exists():
            raise CliError(f"Crew {crew} already has a role named {name}. Change it with `ombro role edit {name}`.")
        limits = load_limits(home)
        outcome = start(home, RolePurpose(home, crew, limits, name), crew, limits)
        if outcome.confirmed:
            print(f"\nAdded {outcome.proposal.data['name']} to crew {crew}.")
            return 0
        return 1
    if not args.name:
        raise CliError(f"Give the role's name, e.g. `ombro role {action} writer`.")
    folder = home.roles_dir(crew) / args.name
    if not folder.is_dir():
        raise CliError(f"Crew {crew} has no role named {args.name!r}.")
    if action == "edit":
        from ombro.interview import editor_command
        from ombro.roles import RoleError, load_role

        files = [folder / "config.md", folder / "soul.md"]
        cmd = editor_command(home)
        if not cmd:
            print("No editor is set (`editor` in preferences/config.md, or $EDITOR). The role's files:")
            for f in files:
                print(f"  {f}")
            return 0
        try:
            subprocess.run([*cmd, *map(str, files)], check=False)
        except OSError as e:
            raise CliError(f"Could not start the editor {cmd[0]!r}: {e}") from None
        try:
            load_role(folder, load_limits(home))
        except RoleError as e:
            print(f"The role has a problem, and runs will refuse to start until it is fixed:\n  {e}")
            return 1
        print(f"{args.name} is valid.")
        return 0
    if action == "remove":
        if args.name == "lead":
            raise CliError("The Lead cannot be removed; every crew has one. Edit it with `ombro role edit lead`.")
        answer = _confirm(f"Remove {args.name} from crew {crew}? Its folder is moved to the crew's removed/ folder. [y/N] ")
        if not answer:
            print("Nothing changed.")
            return 1
        removed = home.crew_dir(crew) / "removed"
        removed.mkdir(exist_ok=True)
        target = removed / args.name
        if target.exists():
            target = removed / f"{args.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(folder), str(target))
        print(f"Moved {args.name} to {target}.")
        return 0
    raise CliError(f"Unknown role action {action!r}.")


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def _set_default_crew(home: Home, crew: str) -> None:
    from ombro.home import PREFERENCES_BODY

    meta, body = mdfile.read(home.preferences_config) if home.preferences_config.is_file() else ({}, PREFERENCES_BODY)
    meta["default_crew"] = crew
    mdfile.write(home.preferences_config, meta, body)


def cmd_crew(args) -> int:
    from ombro.home import ensure_crew
    from ombro.roles import NAME

    home = _home()
    action = args.action or "list"
    default = load_preferences(home).default_crew
    if action == "list":
        for crew in home.crews():
            meta = {}
            if (home.crew_dir(crew) / "crew.md").is_file():
                meta, _ = mdfile.read(home.crew_dir(crew) / "crew.md")
            rdir = home.roles_dir(crew)
            n = len([p for p in rdir.iterdir() if p.is_dir() and p.name != "lead" and not p.name.startswith(".")]) if rdir.is_dir() else 0
            mark = "*" if crew == default else " "
            print(f"{mark} {crew:<16} {n} role(s)  {meta.get('description', '')}")
        print("\n* the default crew")
        return 0
    if not args.name:
        raise CliError(f"Give the crew's name, e.g. `ombro crew {action} podcast`.")
    name = args.name.strip()
    if not NAME.match(name):
        raise CliError("Crew names use lower-case letters, digits, - and _.")
    if action == "add":
        if home.crew_dir(name).exists():
            raise CliError(f"A crew named {name} already exists.")
        ensure_crew(home, name, args.description or "")
        print(f"Created crew {name} with a Lead. Add roles with `ombro role add --crew {name}`.")
        return 0
    if action == "use":
        if not home.crew_dir(name).is_dir():
            raise CliError(f"No crew named {name!r}. Crews: {', '.join(home.crews())}")
        _set_default_crew(home, name)
        print(f"The default crew is now {name}.")
        return 0
    raise CliError(f"Unknown crew action {action!r}.")


def cmd_limits(args) -> int:
    from ombro.settings import ALLOWANCES, LEVEL_KEYS, RETIRED_LIMITS

    home = _home()
    if args.action == "set":
        level = (args.level or "").strip().lower()
        if level not in ALLOWANCES:
            raise CliError(f"Choose one of: {', '.join(ALLOWANCES)}.")
        meta, body = mdfile.read(home.limits)
        dropped = [k for k in (*LEVEL_KEYS, *RETIRED_LIMITS) if k in meta]
        for k in dropped:
            del meta[k]
        meta = {"allowance": level, **{k: v for k, v in meta.items() if k != "allowance"}}
        mdfile.write(home.limits, meta, body)
        print(f"Allowance set to {level} in {home.limits}.")
        if dropped:
            print(f"Removed values set by hand, so the level applies: {', '.join(dropped)}.")
    lim = load_limits(home)
    meta, _ = mdfile.read(home.limits)
    by_hand = [k for k in LEVEL_KEYS if k in meta]
    print(f"Allowance: {lim.allowance}" + (f" (set by hand: {', '.join(by_hand)})" if by_hand else ""))
    print(f"  run tokens       {format_tokens(lim.max_tokens)}")
    print(f"  rounds           {lim.max_rounds}")
    print(f"  run time         {format_duration(lim.run_timeout)}")
    print(f"  each interview   {format_tokens(lim.interview_max_tokens)} tokens, {lim.interview_max_turns} answers")
    print(f"  per model call   {lim.max_output_tokens:,} output tokens")
    print(f"\nLevels: {', '.join(ALLOWANCES)}. Change with `ombro limits set <level>`; details in {home.limits}.")
    if any(k in meta for k in RETIRED_LIMITS):
        print("Note: budget_usd in limits.md is no longer used; dollars are only an estimate now.")
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro", description="Brief a crew of AI roles and get bounded, reported work back.")
    p.add_argument("--version", action="version", version=f"ombro {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("init", help="create the home directory and connect a model")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("run", help="start a run")
    s.add_argument("--spec", help="a confirmed spec: a file, or the name of one saved with `ombro brief`")
    s.add_argument("--skip-interview", metavar="BRIEF", help="run from a one-line brief with no interview (recorded)")
    s.add_argument("--detach", action="store_true", help="keep running after the terminal closes")
    s.add_argument("--crew", help="crew to use (default from preferences)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("brief", help="interview about a task and save a confirmed spec")
    s.add_argument("text", nargs="*", help="the task in your own words (optional)")
    s.add_argument("--crew")
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser("specs", help="list saved specs")
    s.add_argument("--crew")
    s.set_defaults(func=cmd_specs)

    s = sub.add_parser("role", help="list, add, edit or remove roles")
    s.add_argument("action", choices=["list", "add", "edit", "remove"])
    s.add_argument("name", nargs="?")
    s.add_argument("--crew")
    s.set_defaults(func=cmd_role)

    s = sub.add_parser("crew", help="list or add crews, or set the default")
    s.add_argument("action", nargs="?", choices=["list", "add", "use"], help="default: list")
    s.add_argument("name", nargs="?")
    s.add_argument("--description", help="for crew add")
    s.set_defaults(func=cmd_crew)

    s = sub.add_parser("limits", help="show the limits in force, or set the allowance level")
    s.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    s.add_argument("level", nargs="?", help="conservative, moderate, generous or unlimited")
    s.set_defaults(func=cmd_limits)

    s = sub.add_parser("runs", help="list runs and their status")
    s.add_argument("--crew")
    s.set_defaults(func=cmd_runs)

    for name, func, helptext in (
        ("status", cmd_status, "limits and spend for a run"),
        ("report", cmd_report, "show a run's report"),
        ("journal", cmd_journal, "show a run's journal"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("run_id", nargs="?", help="run ID or a unique prefix (default: latest)")
        s.add_argument("--crew")
        if name == "journal":
            s.add_argument("--all", action="store_true", help="include every model call")
        s.set_defaults(func=func)

    s = sub.add_parser("stop", help="stop a run; it writes its report first")
    s.add_argument("run_id")
    s.add_argument("--wait", type=float, default=15.0, help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("open", help="open a run's workspace folder")
    s.add_argument("run_id", nargs="?", help="run ID or a unique prefix (default: latest)")
    s.add_argument("--crew")
    s.add_argument("--print", dest="print_only", action="store_true", help="only print the folder's path")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("keys", help="list keys (masked), or set or remove one")
    s.add_argument("action", nargs="?", choices=["list", "set", "remove"], help="default: list")
    s.add_argument("name", nargs="?", help="the key's name, e.g. FIRECRAWL_API_KEY")
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("_exec")  # internal: the detached run process
    s.add_argument("run_dir")
    s.set_defaults(func=cmd_exec)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (CliError, SetupError, mdfile.FrontMatterError) as e:
        print(f"ombro: {e}", file=sys.stderr)
        return 2
