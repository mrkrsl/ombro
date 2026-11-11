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
from ombro.settings import SetupError, format_duration, load_limits, load_preferences, load_tool_settings


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


def cmd_run(args) -> int:
    home = _home()
    _reap(home)
    if args.spec and args.skip_interview:
        raise CliError("Give either --spec or --skip-interview, not both.")
    if args.spec:
        path = Path(args.spec).expanduser()
        if not path.is_file():
            raise CliError(f"No spec file at {path}")
        spec_meta, spec_body = mdfile.read(path)
        source = f"spec file {path.resolve()}"
    elif args.skip_interview:
        spec_meta, spec_body = {}, args.skip_interview
        source = "--skip-interview (no interview was held)"
    else:
        raise CliError("The task interview is not built yet. Use --spec <file> or --skip-interview \"<brief>\".")
    if not spec_body.strip():
        raise CliError("The spec is empty.")

    crew = _crew(home, args.crew or spec_meta.get("crew"))
    overrides = spec_meta.get("limits") or {}
    if not isinstance(overrides, dict):
        raise CliError("`limits` in a spec must be a mapping, e.g. limits: {budget_usd: 0.5}")
    tool_settings = load_tool_settings(home)
    limits = load_limits(home, overrides, tool_settings)

    from ombro.init_cmd import connected_model

    if connected_model(home) is None:
        raise CliError("No model is connected. Run `ombro init`.")

    run_dir = runs.create_run(home, crew, spec_meta, spec_body, limits, source=source, tools=tool_settings.to_dict())
    run_id = run_dir.name
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
    print(f"{'RUN':<24} {'CREW':<12} {'STATUS':<18} {'COST':>9}  BRIEF")
    for crew, run_dir in rows:
        meta, _ = runs.read_run(run_dir)
        usd = (meta.get("spend") or {}).get("usd", 0.0)
        print(f"{run_dir.name:<24} {crew:<12} {meta.get('status', '?'):<18} ${usd:>8.4f}  {meta.get('brief', '')[:60]}")
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
    print(f"{'':<14}{'used':>16}  {'limit':>14}")
    print(f"{'Dollars':<14}{money:>16}  {'$' + format(lim.get('budget_usd', 0), '.2f'):>14}")
    print(f"{'Tokens':<14}{sp.get('total_tokens', 0):>16,}  {lim.get('max_tokens', 0):>14,}")
    print(f"{'Rounds':<14}{sp.get('rounds', 0):>16}  {lim.get('max_rounds', 0):>14}")
    print(
        f"{'Time':<14}{format_duration(sp.get('elapsed_seconds', 0)):>16}  "
        f"{format_duration(lim.get('run_timeout', 0)):>14}"
    )
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
    print(f"\n{int(reserve * 100)}% of the dollar and token limits is held back for the report.")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro", description="Brief a crew of AI roles and get bounded, reported work back.")
    p.add_argument("--version", action="version", version=f"ombro {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("init", help="create the home directory and connect a model")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("run", help="start a run")
    s.add_argument("--spec", help="a confirmed spec file")
    s.add_argument("--skip-interview", metavar="BRIEF", help="run from a one-line brief with no interview (recorded)")
    s.add_argument("--detach", action="store_true", help="keep running after the terminal closes")
    s.add_argument("--crew", help="crew to use (default from preferences)")
    s.set_defaults(func=cmd_run)

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
