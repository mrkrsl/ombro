"""The `ombro` command. The complete interface to the core."""

from __future__ import annotations

import argparse
import json
import getpass
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ombro import __version__, journal, keys, mdfile, jobs
from ombro.home import Home, tidy_layout
from ombro.settings import SetupError, format_duration, format_tokens, load_limits, load_preferences, load_tool_settings


class CliError(Exception):
    pass


def _home(require: bool = True) -> Home:
    home = Home()
    if require and not home.exists():
        raise CliError(f"No Ombro home at {home.root}. Run `ombro init` first.")
    if home.exists() and home.old_layout():
        raise CliError(
            f"The Ombro home at {home.root} was made by an earlier Ombro, with a Lead in each crew. There is now "
            "one Lead above every crew, and jobs, specs and schedules are kept outside the crews. Move that folder "
            "aside (keep its secrets/ folder if you want your keys) and run `ombro init` for a new home."
        )
    if home.exists():
        from ombro import keys

        try:
            moved = keys.migrate(home)
        except OSError as e:
            moved = f"Could not move {home.legacy_env} into {home.env}: {e}. Keys are still read from it."
        if moved:
            print(moved, file=sys.stderr)
        for line in tidy_layout(home):
            print(line, file=sys.stderr)
    return home


def _crew(home: Home, name: str | None) -> str:
    crew = name or load_preferences(home).default_crew
    if not home.crew_dir(crew).is_dir():
        raise CliError(f"No crew named {crew!r}. Crews: {', '.join(home.crews()) or 'none'}")
    return crew


def _resolve_job(home: Home, job_id: str | None, crew: str | None) -> Path:
    try:
        if job_id:
            return jobs.find_job(home, job_id)
        return jobs.latest_job(home, _crew(home, crew))
    except jobs.JobNotFound as e:
        raise CliError(str(e)) from None


def _reap(home: Home) -> None:
    for job_dir in jobs.reap_dead_jobs(home):
        print(f"Job {job_dir.name} had stopped without finishing; marked failed and wrote its report.", file=sys.stderr)


# ---------------------------------------------------------------------------
# init


def _print_json(data) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_init(args) -> int:
    from ombro.init_cmd import run_init

    try:
        ok = run_init(Home())
    except (KeyboardInterrupt, EOFError):
        print("\nSet-up stopped. Run `ombro init` again to finish connecting a model.")
        return 1
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# start


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


def _spec_path(home: Home, value: str) -> Path:
    """A spec file, or the name of a spec saved in specs/."""
    path = Path(value).expanduser()
    if path.is_file():
        return path
    saved = home.specs_dir / (value if value.endswith(".md") else f"{value}.md")
    if "/" not in value and saved.is_file():
        return saved
    names = [n for n, _ in saved_specs(home)]
    listed = f" Saved specs: {', '.join(names)}." if names else " No specs are saved yet; make one with `ombro brief`."
    raise CliError(f"No spec file at {path}, and no saved spec named {value!r}.{listed}")


def saved_specs(home: Home, crew: str | None = None) -> list[tuple[str, Path]]:
    """Saved specs, newest first; with a crew, those that name it."""
    folder = home.specs_dir
    if not folder.is_dir():
        return []
    files = [f for f in folder.glob("*.md") if not f.name.endswith(".previous.md")]
    if crew:
        files = [f for f in files if crew in _spec_crews_or_none(home, f)]
    return [(f.stem, f) for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)]


def _spec_crews_or_none(home: Home, path: Path) -> list[str]:
    try:
        return jobs.spec_crews(home, mdfile.read(path)[0])
    except (OSError, mdfile.FrontMatterError, SetupError):
        return []


def cmd_specs(args) -> int:
    home = _home()
    crew = _crew(home, args.crew) if args.crew else None
    if args.json:
        from ombro import views

        return _print_json(views.spec_list(home, crew))
    rows = saved_specs(home, crew)
    if not rows:
        print("No specs saved" + (f" that name crew {crew}" if crew else "") + ". Make one with `ombro brief`.")
        return 0
    print("Saved specs" + (f" that name crew {crew}" if crew else "") + ", newest first:\n")
    for name, path in rows:
        _, body = mdfile.read(path)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        crews = ", ".join(_spec_crews_or_none(home, path)) or "?"
        print(f"  {name:<28} {when}  {crews:<20} {jobs.first_line(body, 60)}")
    print("\nRun one with `ombro start --spec <name>`.")
    return 0


def _require_model(home: Home) -> None:
    from ombro.init_cmd import connected_model

    if connected_model(home) is None:
        raise CliError("No model is connected. Run `ombro init`.")


def cmd_start(args) -> int:
    home = _home()
    _reap(home)
    if args.spec and args.skip_interview:
        raise CliError("Give either --spec or --skip-interview, not both.")
    interview = None
    given = [_crew(home, c) for c in (args.crew or [])]
    if args.spec:
        path = _spec_path(home, args.spec)
        spec_meta, spec_body = mdfile.read(path)
        source = f"spec file {path.resolve()}"
    elif args.skip_interview:
        spec_meta, spec_body = {}, args.skip_interview
        source = "--skip-interview (no interview was held)"
    else:
        _require_model(home)
        outcome = _brief_interview(home, given, "", then_start=True)
        if outcome.status == "insufficient" or outcome.status == "limit":
            from ombro.interviews import record_insufficient_brief

            tool_settings = load_tool_settings(home)
            job_dir = record_insufficient_brief(
                home, jobs.spec_crews(home, {}, given), load_limits(home, None, tool_settings), outcome,
                _first_answer(outcome),
                tools=tool_settings.to_dict(),
            )
            print(f"\nRun {job_dir.name} ended: insufficient_brief\n")
            print((job_dir / "report.md").read_text(encoding="utf-8"))
            return 1
        if not outcome.confirmed:
            return 1
        path = outcome.proposal.files[0].path
        spec_meta, spec_body = mdfile.read(path)
        source = f"interview; spec saved at {path}"
        interview = outcome
    if not spec_body.strip():
        raise CliError("The spec is empty.")

    try:
        crews = jobs.spec_crews(home, spec_meta, given)
    except SetupError as e:
        raise CliError(str(e)) from None
    if args.role:
        # For this job only: who does the work, in place of the spec's `roles:`. The saved spec is unchanged.
        spec_meta = {**spec_meta, "roles": list(dict.fromkeys(args.role))}
        source += f"; roles for this job: {', '.join(args.role)}"
    overrides = spec_meta.get("limits") or {}
    if not isinstance(overrides, dict):
        raise CliError("`limits` in a spec must be a mapping, e.g. limits: {allowance: generous}")
    tool_settings = load_tool_settings(home)
    limits = load_limits(home, overrides, tool_settings)
    _require_model(home)

    job_dir = jobs.create_job(home, crews, spec_meta, spec_body, limits, source=source, tools=tool_settings.to_dict())
    job_id = job_dir.name
    if interview is not None:
        from ombro.interviews import _interview_usage

        shutil.copy2(interview.transcript, job_dir / "interview.md")
        jobs.update_job(job_dir, interview_usage=_interview_usage(interview))
    if args.detach:
        pid = jobs.start_detached(home, job_dir)
        # The child records its own pid in job.md; writing it here too would race its updates.
        print(f"Started job {job_id} in the background (pid {pid}).")
        print(f"Follow it with `ombro status {job_id}` or `ombro journal {job_id}`.")
        return 0

    print(f"Job {job_id} (crew{'s' if len(crews) > 1 else ''} {', '.join(crews)}). "
          "Ctrl-C stops it and still writes the report.\n")
    status = _execute(home, job_dir, echo=_echo)
    print(f"\nRun {job_id} ended: {status}\n")
    print((job_dir / "report.md").read_text(encoding="utf-8"))
    return 0 if status == "completed" else 1


def _execute(home: Home, job_dir: Path, echo=None) -> str:
    from ombro.engine import Engine

    engine = Engine(home, job_dir, echo=echo)
    _install_stop_handlers(engine)
    return engine.execute()


def cmd_exec(args) -> int:
    home = _home()
    status = _execute(home, Path(args.job_dir))
    return 0 if status == "completed" else 1


# ---------------------------------------------------------------------------
# jobs, status, report, journal, stop


def cmd_jobs(args) -> int:
    home = _home()
    _reap(home)
    crew = _crew(home, args.crew) if args.crew else None
    if args.json:
        from ombro import views

        return _print_json(views.job_list(home, crew))
    rows = jobs.all_jobs(home, crew)
    if not rows:
        print("No jobs yet.")
        return 0
    from ombro import outbox

    print(f"{'JOB':<24} {'CREWS':<16} {'STATUS':<18} {'TOKENS':>9}  {'APPROVALS':<10} BRIEF")
    for job_dir in rows:
        meta, _ = jobs.read_job(job_dir)
        crew = ", ".join(jobs.job_crews(meta))
        tokens = jobs.usage(meta).get("total_tokens", 0)
        n = outbox.waiting_count(job_dir)
        box = f"{n} waiting" if n else "-"
        print(f"{job_dir.name:<24} {crew[:16]:<16} {meta.get('status', '?'):<18} {tokens:>9,}  {box:<10} "
              f"{meta.get('brief', '')[:60]}")
    if any(outbox.waiting_count(d) for d in rows):
        print("\nSee waiting actions with `ombro approvals`.")
    return 0


def cmd_status(args) -> int:
    home = _home()
    _reap(home)
    job_dir = _resolve_job(home, args.job_id, args.crew)
    if args.json:
        from ombro import views

        return _print_json(views.status(job_dir))
    meta, _ = jobs.read_job(job_dir)
    lim, sp = meta.get("limits") or {}, jobs.usage(meta)
    reserve = lim.get("report_reserve", 0)
    print(f"Job       {meta.get('job_id')}  (crews {', '.join(jobs.job_crews(meta))})")
    print(f"Status    {meta.get('status')}" + (f", stopped by {meta['limit_hit']}" if meta.get("limit_hit") else ""))
    print(f"Brief     {meta.get('brief', '')}")
    print(f"Started   {meta.get('started_at') or '-'}   Finished {meta.get('finished_at') or '-'}")
    if meta.get("status") == "running":
        print(f"Process   {meta.get('pid')}")
    print()
    print(f"Allowance {lim.get('allowance', 'custom')}")
    print()
    print(f"{'':<14}{'used':>16}  {'limit':>14}")
    print(f"{'Tokens':<14}{sp.get('counted_tokens', sp.get('total_tokens', 0)):>16,}  {format_tokens(lim.get('max_tokens')):>14}")  # as counted against the limit
    print(f"{'Rounds':<14}{sp.get('rounds', 0):>16}  {lim.get('max_rounds', 0):>14}")
    print(
        f"{'Time':<14}{format_duration(sp.get('elapsed_seconds', 0)):>16}  "
        f"{format_duration(lim.get('job_timeout', 0)):>14}"
    )
    print(f"{'Model calls':<14}{sp.get('model_calls', 0):>16}")
    tools = sp.get("tool_calls") or {}
    caps = lim.get("tool_max_calls") or {}
    refused = sp.get("tool_refused") or {}
    for name, n in sorted(tools.items()):
        cap = caps.get(name, caps.get("default", "-"))
        extra = f"  ({refused[name]} refused at the cap)" if refused.get(name) else ""
        print(f"{'  ' + name:<14}{n:>16}  {cap:>14}{extra}")
    from ombro import outbox, outward

    c = outbox.counts(job_dir)
    print(f"\nApprovals {outbox.describe_counts(c)}"
          + (" (see them with `ombro approvals`)" if outbox.waiting_count(job_dir) else ""))
    if meta.get("outward"):
        print(f"Outward   {outward.describe(meta['outward'])}")
    print(f"\n{int(reserve * 100)}% of the token limit is held back for the report.")
    return 0


def cmd_report(args) -> int:
    home = _home()
    _reap(home)
    job_dir = _resolve_job(home, args.job_id, args.crew)
    path = job_dir / "report.md"
    if args.json:
        from ombro import views

        data = views.report(home, job_dir)
        if path.is_file():
            jobs.mark_seen(job_dir)
        return _print_json(data)
    if not path.is_file():
        meta, _ = jobs.read_job(job_dir)
        print(f"Job {job_dir.name} has no report yet (status {meta.get('status')}).")
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    jobs.mark_seen(job_dir)
    return 0


def cmd_journal(args) -> int:
    home = _home()
    _reap(home)
    job_dir = _resolve_job(home, args.job_id, args.crew)
    if args.follow:
        return _follow_journal(job_dir, args)
    if args.json:
        from ombro import views

        return _print_json(views.journal_events(job_dir, args.all))
    events = journal.read(job_dir / "journal.jsonl")
    if not events:
        print("The journal is empty.")
    for ev in events:
        if ev.get("action") == "model_call" and not args.all:
            continue
        print(journal.format_event(ev))
    return 0


def _follow_journal(job_dir: Path, args, poll: float = 0.5, clock=time.time, sleep=time.sleep) -> int:
    """`ombro journal --follow`: the events so far, then new ones as they are written,
    until the job has ended and nothing more comes. With --json, one JSON object a line."""
    home = _home()
    follower = journal.Follower(job_dir / "journal.jsonl")

    def show(events: list[dict]) -> None:
        for ev in events:
            if ev.get("action") == "model_call" and not args.all:
                continue
            print(json.dumps(ev, ensure_ascii=False, default=str) if args.json else journal.format_event(ev),
                  flush=True)

    try:
        while True:
            show(follower.new_events())
            meta, _ = jobs.read_job(job_dir)
            if meta.get("status") != "running" or jobs.is_dead(meta):
                if jobs.is_dead(meta):
                    jobs.reap_dead_jobs(home)
                show(follower.new_events())
                if not args.json:
                    print(f"(The job has ended: {jobs.read_job(job_dir)[0].get('status')}.)")
                return 0
            sleep(poll)
    except KeyboardInterrupt:
        return 0


def cmd_summary(args) -> int:
    from ombro import views

    home = _home()
    _reap(home)
    s = views.summary(home)
    if args.json:
        return _print_json(s)
    c = s["counts"]

    def n(count: int, word: str) -> str:
        return f"{count} {word}" + ("" if count == 1 else "s")

    print(f"Ombro v{s['version']}" + (f", working with {s['name']}" if s["name"] else ""))
    print(f"Model: {s['model'] or '(none connected)'}   Outward: {s['outward']}")
    print()
    print(s["lead"]["title"])
    for crew in s["crews"]:
        print(f"└ {crew['title']}" + ("  (default)" if crew["default"] else ""))
        for role in crew["roles"]:
            print(f"   └ {role['title']}" + ("  (has a problem: see `ombro role list`)" if "problem" in role else ""))
        if not crew["roles"]:
            print("   (no roles yet)")
    print()
    print(", ".join([f"{n(c['crews_ready'], 'crew')} ready to work", n(c["active_jobs"], "active job"),
                     n(c["scheduled_jobs"], "scheduled job"), n(c["waiting"], "item") + " to approve"]))
    if s["unseen"]:
        print(f"\nFinished, and not yet looked at: " + ", ".join(r["job_id"] for r in s["unseen"])
              + ". See one with `ombro report <job_id>`.")
    return 0


def cmd_preferences(args) -> int:
    from ombro import views

    home = _home()
    if args.action == "edit":
        from ombro.interview import open_in_editor
        from ombro.ui import ConsoleUI

        open_in_editor(home, [home.preferences_config, home.instructions], ConsoleUI(home=home))
        load_preferences(home)  # read back, so a mistake shows now rather than at the next job
        print("Saved. They are used from the next chat, interview or job.")
        return 0
    p = views.preferences(home)
    if args.json:
        return _print_json(p)
    print(f"Name          {p['name'] or '(not set)'}")
    print(f"Theme         {p['theme']}")
    print(f"Default crew  {p['default_crew_title']} (--crew {p['default_crew']})")
    print(f"Editor        {p['editor'] or '(from $VISUAL or $EDITOR)'}")
    print(f"Outward       {p['outward']}")
    print(f"About you     {'set' if p['instructions_set'] else 'not set (the set-up interview writes it)'}")
    print(f"\nFiles: {p['files']['config']}\n       {p['files']['instructions']}")
    print("Change them with `ombro preferences edit`.")
    return 0


def cmd_stop(args) -> int:
    home = _home()
    _reap(home)
    job_dir = _resolve_job(home, args.job_id, None)
    meta, _ = jobs.read_job(job_dir)
    if meta.get("status") != "running":
        print(f"Job {job_dir.name} is not running (status {meta.get('status')}).")
        return 1
    pid = meta.get("pid")
    if not pid and not jobs.is_dead(meta):
        print(f"Job {job_dir.name} is still starting; try again in a moment.")
        return 1
    if not pid or not jobs.pid_alive(int(pid)):
        jobs.reap_dead_jobs(home)
        print(f"Job {job_dir.name} had no live process; marked failed and wrote its report.")
        return 0
    os.kill(int(pid), signal.SIGTERM)
    print(f"Asked job {job_dir.name} to stop. It finishes its current call, then writes its report.")
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if jobs.read_job(job_dir)[0].get("status") != "running":
            print(f"Stopped. Report: {job_dir / 'report.md'}")
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
    job_dir = _resolve_job(home, args.job_id, args.crew)
    folder = job_dir / "workspace"
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise CliError(f"{folder} is not a real folder (it has been replaced, for example by a symlink), so Ombro "
                       "will not open it. Look at it with care before opening anything in it.")
    folder.mkdir(exist_ok=True)
    print(folder)
    if not args.print_only and not _launch(folder):
        print("(No file manager could be opened here; the path is above.)")
    return 0


def _server_cfg(home: Home, name: str) -> dict:
    from ombro.settings import load_tool_settings

    settings = load_tool_settings(home)
    if name not in settings.servers:
        raise CliError(f"No server named {name!r} in tools.md. Servers: {', '.join(settings.servers) or 'none'}")
    return settings.servers[name]


def cmd_connect(args) -> int:
    import webbrowser

    from ombro import oauth

    home = _home()
    cfg = _server_cfg(home, args.server)
    opener = (lambda url: None) if args.no_browser else webbrowser.open
    print(f"Waiting up to {oauth.CONNECT_TIMEOUT / 60:.0f} minutes for you to sign in. Ctrl-C cancels.")
    try:
        done = oauth.connect(home, args.server, cfg, open_browser=opener)
    except oauth.SignInError as e:
        raise CliError(f"Could not connect {args.server}: {e}") from None
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was saved.")
        return 1
    print(f"Connected {args.server}, signed in with {done.issuer}"
          + (f" (access: {done.scope})" if done.scope else "") + ".")
    print("It renews itself, so jobs can use it unattended." if done.refreshes else
          "The service gave no renewal token, so you will need to connect again when it expires.")
    print(f"The sign-in is kept in {oauth.store_path(home, args.server)}, readable only by you.")
    return 0


def cmd_disconnect(args) -> int:
    from ombro import oauth

    home = _home()
    if oauth.disconnect(home, args.server):
        print(f"Forgot the sign-in for {args.server}, and asked the service to revoke it where it can.")
    else:
        print(f"{args.server} has no sign-in.")
    return 0


def cmd_models(args) -> int:
    from ombro import models
    from ombro.ui import ConsoleUI

    home = _home()
    action = args.action or "list"
    if action == "list":
        if args.json:
            return _print_json(models.connections(home))
        print("Models, by connection:")
        for line in models.listing_lines(home):
            print("  " + line)
        print("\nChange who uses which with `ombro model`, and the default with `ombro models default`.")
        return 0
    ui = ConsoleUI()
    if action == "default":
        return models.default_flow(home, ui, args.name)
    if action == "refresh":
        return models.refresh_flow(home, ui)
    return models.add_flow(home, ui)


def cmd_model(args) -> int:
    from ombro import models
    from ombro.ui import ConsoleUI

    return models.model_flow(_home(), ConsoleUI(), args.who, args.name)


def cmd_keys(args) -> int:
    home = _home()
    action = args.action or "list"
    if action == "list" and args.json:
        from ombro import views

        return _print_json(views.key_list(home))
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
        from ombro import oauth
        from ombro.settings import load_tool_settings

        try:
            remote = [n for n, c in load_tool_settings(home).servers.items() if c.get("url")]
        except Exception:
            remote = []
        if remote:
            print("\nSign-ins for remote MCP servers (`ombro connect <server>`):")
            for n in remote:
                print(f"  {n:<24} {oauth.status(home, n)}")
        return 0
    if not args.name:
        raise CliError(f"Give the key's name, e.g. `ombro keys {action} FIRECRAWL_API_KEY`.")
    name = args.name.strip()
    if not keys.valid_name(name):
        raise CliError("Key names use capitals, digits and underscores, starting with a letter.")
    if action == "set":
        from ombro.ui import ConsoleUI

        return set_key_flow(home, name, ConsoleUI())
    if action == "remove":
        removed = keys.remove_key(home, name)
        print(f"Removed {name} from {home.env.name}." if removed else f"{name} was not in {home.env.name}.")
        if os.environ.get(name):
            print(f"Note: {name} is also set in your shell environment, and Ombro will still use that.")
        return 0 if removed else 1
    raise CliError(f"Unknown keys action {action!r}; use set or remove.")


# ---------------------------------------------------------------------------
# brief, role, crew, limits


def _first_answer(outcome) -> str:
    return outcome.answers[0] if outcome.answers else ""


def _brief_interview(home: Home, crews: list[str], task: str, *, then_start: bool):
    from ombro.folders_cmd import offer_start_folder
    from ombro.interviews import BriefPurpose, start

    offer_start_folder(home)
    limits = load_limits(home)
    default = _crew(home, None)
    purpose = BriefPurpose(home, default, limits, task, then_start=then_start, start_dir=Path.cwd(), crews=crews)
    return start(home, purpose, default, limits)


def cmd_brief(args) -> int:
    home = _home()
    _require_model(home)
    crews = [_crew(home, c) for c in (args.crew or [])]
    outcome = _brief_interview(home, crews, " ".join(args.text or []), then_start=False)
    if outcome.confirmed:
        name = outcome.proposal.data["name"]
        print(f"\nSpec saved as {name!r} ({outcome.proposal.files[0].path}).")
        if _confirm("Start it now? It works in the background; follow it with `ombro journal --follow`."):
            return main(["start", "--spec", name, "--detach"])
        print(f"Start it with:   ombro start --spec {name}")
        print("List saved specs with `ombro specs`.")
        return 0
    if outcome.status in ("insufficient", "limit"):
        print("\nThe brief is insufficient, so no spec was saved. The gaps:")
        for g in outcome.gaps or ["No spec was confirmed."]:
            print(f"  - {g}")
    print(f"Transcript: {outcome.transcript}")
    return 1


def cmd_chat(args) -> int:
    from ombro.chat import Chat

    home = _home()
    _reap(home)
    _require_model(home)
    chat = Chat(home, crew=_crew(home, args.crew))
    if args.resume is not None:
        from ombro import views

        want = args.resume.strip().lower()
        past = [c for c in views.chat_list(home) if not want or want in c["title"].lower() or want in c["name"]]
        if not past:
            raise CliError(f"No saved chat matches {args.resume!r}. `ombro chats` lists them.")
        chat.reopen(Path(past[0]["path"]))
        print(f"\nReopened '{past[0]['title']}'. Carry on where you left off.\n")
    chat.run()
    return 0


def cmd_chats(args) -> int:
    from ombro import views

    rows = views.chat_list(_home())
    if args.json:
        return _print_json(rows)
    if not rows:
        print("No saved chats yet.")
        return 0
    for c in rows[:40]:
        print(f"{c['when']:<16} {c['title']}")
    print("\nCarry one on with `ombro chat --resume <part of its title>`.")
    return 0


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


def _find_role(home: Home, name: str, crew: str | None) -> tuple[str, Path]:
    """A role folder by crew/role, by name with --crew, or by a name only one crew has."""
    if "/" in name:
        crew, _, name = name.partition("/")
    if crew:
        crew = _crew(home, crew)
        folder = home.roles_dir(crew) / name
        if not folder.is_dir():
            raise CliError(f"Crew {crew} has no role named {name!r}.")
        return crew, folder
    found = [c for c in home.crews() if (home.roles_dir(c) / name).is_dir()]
    if not found:
        raise CliError(f"No crew has a role named {name!r}. See `ombro role list`.")
    if len(found) > 1:
        raise CliError(f"Crews {', '.join(found)} each have a role named {name!r}; give it as crew/{name}.")
    return found[0], home.roles_dir(found[0]) / name


def cmd_role(args) -> int:
    home = _home()
    action = args.action
    if action == "list" and args.json:
        from ombro import views

        team = views.crews(home)
        if args.crew:
            team["crews"] = [c for c in team["crews"] if c["name"] == _crew(home, args.crew)]
        return _print_json(team)
    if action == "list":
        crews = [_crew(home, args.crew)] if args.crew else home.crews()
        lead_model = "?"
        try:
            from ombro.roles import load_lead, tools_text

            lead_model = load_lead(home, load_limits(home)).model
        except SetupError:
            pass
        print(f"The Lead, above every crew: {home.lead_dir} (model {lead_model})")
        for crew in crews:
            rows = _role_rows(home, crew)
            print(f"\nCrew {crew}:")
            if not rows:
                print("  (no roles yet)")
                continue
            print(f"  {'NAME':<20} {'KIND':<11} {'MODEL':<9} TOOLS")
            for name, role in rows:
                if isinstance(role, str):
                    print(f"  {name:<20} {'(broken)':<11} {'':<9} {role}")
                    continue
                print(f"  {name:<20} {role.kind:<11} {role.model:<9} {tools_text(role)}")
                if role.description:
                    print(f"  {'':<20} {role.description}")
        return 0
    if action == "add":
        from ombro.interviews import RolePurpose, start
        from ombro.roles import NAME

        crew = _crew(home, args.crew)
        _require_model(home)
        from ombro.roles import folder_name

        title = " ".join((args.name or "").split()) or None
        name = folder_name(title) if title else None
        if title and not (name and NAME.match(name)):
            raise CliError("Give the role a title made of letters or digits, such as \"Marketing Assistant\".")
        if name and home.roles_dir(crew).joinpath(name).exists():
            raise CliError(f"Crew {crew} already has a role named {name}. "
                           f"Change it with `ombro role edit {crew}/{name}`.")
        limits = load_limits(home)
        outcome = start(home, RolePurpose(home, crew, limits, title), crew, limits)
        if outcome.confirmed:
            from ombro.init_cmd import _role_title
            from ombro.models import offer_role_model
            from ombro.home import crew_title
            from ombro.ui import ConsoleUI

            added = outcome.proposal.data["name"]
            print(f"\nAdded {added} to the {crew_title(home, crew)}.")
            offer_role_model(home, ConsoleUI(), f"{crew}/{added}", _role_title(home, crew, added))
            return 0
        return 1
    if not args.name:
        raise CliError(f"Give the role's name, e.g. `ombro role {action} writer`.")
    if args.name == "lead":
        if action == "remove":
            raise CliError("The Lead cannot be removed; there is one, above every crew. "
                           "Edit it with `ombro role edit lead`.")
        crew, folder = "", home.lead_dir
    else:
        crew, folder = _find_role(home, args.name, args.crew)
    label = f"{crew}/{folder.name}" if crew else "the Lead"
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
            print(f"The role has a problem, and jobs will refuse to start until it is fixed:\n  {e}")
            return 1
        print(f"{label} is valid.")
        return 0
    if action == "remove":
        answer = _confirm(f"Remove {label}? Its folder is moved to the crew's removed/ folder.")
        if not answer:
            print("Nothing changed.")
            return 1
        from ombro.removing import RemoveError, remove_role

        try:
            target = remove_role(home, crew, folder.name)
        except RemoveError as e:
            raise CliError(str(e)) from None
        print(f"Moved {label} to {target}.")
        return 0
    raise CliError(f"Unknown role action {action!r}.")


def set_key_flow(home: Home, name: str, ui) -> int:
    """`ombro keys set NAME`: the value is typed hidden and never shown again."""
    from ombro.ui import Field

    try:
        value = str(ui.ask(Field("value", f"Value for {name} (hidden; Enter to cancel)", "secret")) or "").strip()
    except (KeyboardInterrupt, EOFError):
        ui.say("Cancelled.", spaced=True)
        return 1
    if not value:
        ui.say("Cancelled; nothing changed.")
        return 1
    keys.set_key(home, name, value)
    ui.say(f"Saved {name} ({keys.mask(value)}). It is used from the next call, even in a job already going.",
           level="done")
    return 0


def _confirm(label: str) -> bool:
    """A yes or no on the command line; no unless the user says yes."""
    from ombro.ui import ConsoleUI

    try:
        return ConsoleUI().yes("confirm", label)
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def _set_default_crew(home: Home, crew: str) -> None:
    from ombro.settings import set_preference

    set_preference(home, "default_crew", crew)


def cmd_crew(args) -> int:
    from ombro.home import crew_title, ensure_crew
    from ombro.roles import NAME

    home = _home()
    action = args.action or "list"
    default = load_preferences(home).default_crew
    if action == "list" and args.json:
        from ombro import views

        return _print_json(views.crews(home))
    if action == "list":
        for crew in home.crews():
            meta = {}
            if (home.crew_dir(crew) / "crew.md").is_file():
                meta, _ = mdfile.read(home.crew_dir(crew) / "crew.md")
            rdir = home.roles_dir(crew)
            n = len([p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")]) if rdir.is_dir() else 0
            mark = "*" if crew == default else " "
            print(f"{mark} {crew:<16} {crew_title(home, crew):<24} {n} role(s)  {meta.get('description', '')}")
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
        ensure_crew(home, name, args.description or "", args.title or "")
        print(f"Created crew {name} ({crew_title(home, name)}). The Lead can brief its roles once it has some: "
              f"add them with `ombro role add --crew {name}`.")
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
    if args.action != "set" and args.json:
        from ombro import views

        return _print_json(views.limit_values(home))
    if args.action == "set":
        from ombro.settings import set_allowance

        level = (args.level or "").strip().lower()
        try:
            dropped = set_allowance(home, level)
        except SetupError as e:
            raise CliError(str(e)) from None
        print(f"Allowance set to {level} in {home.limits}.")
        if dropped:
            print(f"Removed values set by hand, so the level applies: {', '.join(dropped)}.")
    lim = load_limits(home)
    meta, _ = mdfile.read(home.limits)
    by_hand = [k for k in LEVEL_KEYS if k in meta]
    print(f"Allowance: {lim.allowance}" + (f" (set by hand: {', '.join(by_hand)})" if by_hand else ""))
    print(f"  job tokens       {format_tokens(lim.max_tokens)}")
    print(f"  rounds           {lim.max_rounds}")
    print(f"  job time         {format_duration(lim.job_timeout)}")
    print(f"  each interview   {format_tokens(lim.interview_max_tokens)} tokens, {lim.interview_max_turns} answers")
    print(f"  each chat        {format_tokens(lim.chat_max_tokens)} tokens")
    print(f"  notes.md         {lim.memory_notes_max_chars:,} characters; a job's Lead sees the last "
          f"{lim.memory_index_entries} index entries")
    print(f"  per model call   {lim.max_output_tokens:,} output tokens")
    from ombro.settings import format_size

    print(f"  each script      {format_duration(lim.code_timeout)}, {format_size(lim.code_max_memory)}, "
          f"{lim.code_output_chars:,} characters of output to the role (code.run)")
    print(f"  scripts in all   {format_size(lim.code_max_total)} in a job's workspace/ and scratch/ (code.run)")
    print(f"\nLevels: {', '.join(ALLOWANCES)}. Change with `ombro limits set <level>`; details in {home.limits}.")
    retired = [k for k in RETIRED_LIMITS if k in meta]
    if retired:
        print(f"Note: {', '.join(retired)} in limits.md no longer do anything (Ombro reports tokens only); "
              "you can delete them.")
    return 0


# ---------------------------------------------------------------------------
# schedule, tick


def cmd_schedule(args) -> int:
    from ombro.schedule_cmd import cmd_schedule as run

    return run(args)


def cmd_approvals(args) -> int:
    from ombro.approvals_cmd import cmd_approvals as run

    return run(args)


def cmd_skills(args) -> int:
    from ombro.skills_cmd import cmd_skills as run

    return run(args)


def cmd_tools(args) -> int:
    from ombro import tool_cmd

    if args.server and tool_cmd.is_native(args.server):
        from ombro.ui import ConsoleUI

        if args.provider is None and args.max_calls is None and args.json:
            return _print_json({"tool": args.server, "settings": tool_cmd.describe(_home(), args.server)})
        return tool_cmd.flow(_home(), ConsoleUI(), args.server, args.provider, args.max_calls)
    if args.provider or args.max_calls:
        raise CliError("--provider and --max-calls are for a built-in tool, such as `ombro tools web.search`.")
    from ombro.approvals_cmd import cmd_tools as run

    return run(args)


def cmd_server(args) -> int:
    from ombro.server_cmd import cmd_server as run

    return run(args)


def cmd_folders(args) -> int:
    from ombro.folders_cmd import cmd_folders as run

    return run(args)


def cmd_serve(args) -> int:
    from ombro.serve import dispatch

    try:
        return dispatch(args)
    except KeyboardInterrupt:
        return 0


def cmd_pair(args) -> int:
    """Pairing devices with this machine's Ombro server (pairing.py)."""
    from datetime import datetime

    from ombro.pairing import Devices, PairingError

    devices = Devices(_home(require=False))
    try:
        if args.device is not None:
            # For the app over SSH: a new device's token, as JSON, and nothing else on stdout.
            device, token = devices.add(args.device)
            return _print_json({"token": token, "device": device.to_dict()})
        if args.list:
            rows = devices.list()
            if not rows:
                print("No device is paired with this server.")
            for d in rows:
                print(f"{d.id}  {d.name}  (paired {datetime.fromtimestamp(d.paired):%d %b %Y %H:%M})")
            return 0
        if args.forget:
            gone = devices.forget(args.forget)
            if not gone:
                raise CliError(f"No paired device is called {args.forget!r}. `ombro pair --list` shows them.")
            print(f"Forgot {', '.join(d.name for d in gone)}. It can't connect any more.")
            return 0
        if args.forget_all:
            if not _confirm("Forget every paired device? Each will need pairing again."):
                print("Nothing forgotten.")
                return 1
            n = devices.forget_all()
            print(f"Forgot {n} device{'s' if n != 1 else ''}. Restart the server to see a new first code.")
            return 0
        code, expires = devices.later_code()
    except PairingError as e:
        raise CliError(str(e)) from None
    print(f"Pairing code: {code}\n")
    print("Type it into the Ombro app (Connect to a server). It works once, until "
          f"{datetime.fromtimestamp(expires):%a %d %b %H:%M}. A new code replaces this one.")
    return 0


def cmd_tick(args) -> int:
    from ombro.schedule_cmd import cmd_tick as run

    return run(args)


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro", description="Brief a crew of AI roles and get bounded, reported work back.")
    p.add_argument("--version", action="version", version=f"ombro {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("init", help="create the home directory and connect a model")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("start", help="start a job")
    s.add_argument("--spec", help="a confirmed spec: a file, or the name of one saved with `ombro brief`")
    s.add_argument("--skip-interview", metavar="BRIEF", help="start from a one-line brief with no interview (recorded)")
    s.add_argument("--detach", action="store_true", help="keep running after the terminal closes")
    s.add_argument("--crew", action="append",
                   help="a crew the job may use when the spec names none; repeat for several (default from preferences)")
    s.add_argument("--role", action="append",
                   help="for this job only, who does the work (crew/role or a role's name), in place of the spec's "
                   "roles:; repeat for several")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("brief", help="interview about a task and save a confirmed spec")
    s.add_argument("text", nargs="*", help="the task in your own words (optional)")
    s.add_argument("--crew", action="append", help="a crew to start from; repeat for several (the Lead proposes)")
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser("chat", help="talk with the Lead (read-only); /brief, /remember, /help inside")
    s.add_argument("--crew", help="the crew /remember and /brief use unless another is named (default from preferences)")
    s.add_argument("--resume", nargs="?", const="", metavar="TITLE",
                   help="carry on a saved chat: the newest whose title contains TITLE (the newest of all if none)")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("chats", help="saved chats by title, most recent first")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_chats)

    s = sub.add_parser("specs", help="list saved specs")
    s.add_argument("--crew")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_specs)

    s = sub.add_parser("role", help="list, add, edit or remove roles")
    s.add_argument("action", choices=["list", "add", "edit", "remove"])
    s.add_argument("name", nargs="?", help="for add: the role's title, e.g. \"Marketing Assistant\"")
    s.add_argument("--crew")
    s.add_argument("--json", action="store_true", help="for list: print JSON, for interfaces")
    s.set_defaults(func=cmd_role)

    s = sub.add_parser("crew", help="list or add crews, or set the default")
    s.add_argument("action", nargs="?", choices=["list", "add", "use"], help="default: list")
    s.add_argument("name", nargs="?")
    s.add_argument("--description", help="for crew add")
    s.add_argument("--title", help="for crew add: the title shown, e.g. \"Legal Crew\" (default: the name, then Crew)")
    s.add_argument("--json", action="store_true", help="for list: print JSON, for interfaces")
    s.set_defaults(func=cmd_crew)

    s = sub.add_parser("limits", help="show the limits in force, or set the allowance level")
    s.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    s.add_argument("level", nargs="?", help="conservative, moderate, generous or unlimited")
    s.add_argument("--json", action="store_true", help="for show: print JSON, for interfaces")
    s.set_defaults(func=cmd_limits)

    s = sub.add_parser("jobs", help="list jobs and their status")
    s.add_argument("--crew")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_jobs)

    s = sub.add_parser("summary", help="who you are, the crew, and what is running, scheduled and waiting")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("preferences", help="show your preferences, or edit them")
    s.add_argument("action", nargs="?", choices=["show", "edit"], default="show")
    s.add_argument("--json", action="store_true", help="for show: print JSON, for interfaces")
    s.set_defaults(func=cmd_preferences)

    for name, func, helptext in (
        ("status", cmd_status, "limits and usage for a job"),
        ("report", cmd_report, "show a job's report"),
        ("journal", cmd_journal, "show a job's journal"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("job_id", nargs="?", help="job ID or a unique prefix (default: latest)")
        s.add_argument("--crew")
        s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
        if name == "journal":
            s.add_argument("--all", action="store_true", help="include every model call")
            s.add_argument("--follow", "-f", action="store_true", help="keep showing new events until the job ends")
        s.set_defaults(func=func)

    s = sub.add_parser("stop", help="stop a job; it writes its report first")
    s.add_argument("job_id")
    s.add_argument("--wait", type=float, default=15.0, help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("open", help="open a job's workspace folder")
    s.add_argument("job_id", nargs="?", help="job ID or a unique prefix (default: latest)")
    s.add_argument("--crew")
    s.add_argument("--print", dest="print_only", action="store_true", help="only print the folder's path")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("models", help="model connections and their models: list, default, refresh, add")
    s.add_argument("action", nargs="?", choices=["list", "default", "refresh", "add"], help="default: list")
    s.add_argument("name", nargs="?", help="for default: the model's name in models.md")
    s.add_argument("--json", action="store_true", help="for list: print JSON, for interfaces")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("model", help="which model the Lead or a role uses: show, or set one")
    s.add_argument("who", nargs="?", help="lead, or crew/role")
    s.add_argument("name", nargs="?", help="the model's name in models.md")
    s.set_defaults(func=cmd_model)

    s = sub.add_parser("keys", help="list keys (masked), or set or remove one")
    s.add_argument("action", nargs="?", choices=["list", "set", "remove"], help="default: list")
    s.add_argument("name", nargs="?", help="the key's name, e.g. FIRECRAWL_API_KEY")
    s.add_argument("--json", action="store_true", help="for list: print JSON (names only, never values)")
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("connect", help="sign in to a remote MCP server in your browser")
    s.add_argument("server", help="the server's name under `servers:` in tools.md")
    s.add_argument("--no-browser", action="store_true", help="print the sign-in address instead of opening it")
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("server", help="add or remove an MCP server in tools.md")
    s.add_argument("action", choices=["add", "remove"])
    s.add_argument("name", nargs="?", help="the server's name, e.g. sheets")
    s.add_argument("--command", help='for add: a local server\'s command line, e.g. "uvx some-server==1.2.3"')
    s.add_argument("--url", help="for add: a remote server's address")
    s.add_argument("--env", action="append", metavar="NAME=VALUE", help="for add: a setting for a local server "
                   "(not a secret); repeat for several")
    s.add_argument("--header", action="append", metavar="NAME=VALUE", help="for add: a header for a remote "
                   "server (not a secret)")
    s.add_argument("--secret", action="append", metavar="NAME[=KEY]", help="for add: a variable (local) or header "
                   "(remote) that holds a secret, typed hidden and kept in secrets/keys.env as KEY")
    s.add_argument("--class", dest="server_class", choices=["internal", "external"],
                   help="for add: the class of its tools that are not read-only")
    s.add_argument("--no-prompt", action="store_true", help="for add: do not ask about secrets not given")
    s.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    s.set_defaults(func=cmd_server)

    s = sub.add_parser("disconnect", help="forget the sign-in for a remote MCP server")
    s.add_argument("server")
    s.set_defaults(func=cmd_disconnect)

    s = sub.add_parser("schedule", help="list, show, add, enable, disable or remove schedules; install the scheduler")
    s.add_argument("action", nargs="?",
                   choices=["list", "show", "add", "enable", "disable", "remove", "install", "uninstall"],
                   help="default: list")
    s.add_argument("name", nargs="?")
    s.add_argument("--crew")
    s.add_argument("--spec", help="for add: a spec saved in the crew (by name)")
    s.add_argument("--cron", help='for add: when, in local time, e.g. "30 7 * * 1-5"')
    s.add_argument("--allowance", choices=["conservative", "moderate", "generous"], help="for add: the jobs' allowance")
    s.add_argument("--max-tokens", type=int, help="for add: the jobs' token cap")
    s.add_argument("--job-timeout", help="for add: the jobs' time limit, e.g. 15m")
    s.add_argument("--grace", help="for add: how late a slot may still start, e.g. 15m")
    s.add_argument("--disabled", action="store_true", help="for add: save it switched off")
    s.add_argument("--json", action="store_true", help="for list: print JSON, for interfaces")
    s.set_defaults(func=cmd_schedule)

    s = sub.add_parser("approvals", help="what waits for your approval: list, show, approve, decline, edit, resolve")
    s.add_argument("action", nargs="?", choices=["list", "show", "approve", "decline", "edit", "resolve"],
                   help="default: list")
    s.add_argument("refs", nargs="*", help="action references, e.g. ab12-3 (see `ombro approvals`)")
    s.add_argument("--crew", help="for list: only this crew")
    s.add_argument("--all", action="store_true", help="for list: include actions already decided")
    s.add_argument("--yes", action="store_true", help="for approve and decline: do not ask (an interface "
                   "that has shown the actions passes this)")
    s.add_argument("--arguments", help="for edit: the new arguments as a JSON object, instead of the editor")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_approvals)

    s = sub.add_parser("skills", help="the skills library: list, show <name>, edit <name>, "
                       "give <skill> <crew/role>, add <folder>")
    s.add_argument("action", nargs="?", choices=["list", "show", "edit", "give", "add"], help="default: list")
    s.add_argument("name", nargs="?", help="a skill's name; for add, the folder of a skill to import")
    s.add_argument("--yes", action="store_true", help="for add: do not ask (an interface that has shown it "
                   "passes this)")
    s.add_argument("roles", nargs="*", help="for give: roles as crew/role")
    s.add_argument("--json", action="store_true", help="for list and show: print JSON, for interfaces")
    s.set_defaults(func=cmd_skills)

    s = sub.add_parser("tools", help="native tools and MCP servers; with a server, its tools, class and permission")
    s.add_argument("server", nargs="?", help="an MCP server named in tools.md, or a built-in tool such as "
                   "web.search to see or change it")
    s.add_argument("--provider", help="for a built-in tool: the provider to use (e.g. duckduckgo, firecrawl)")
    s.add_argument("--max-calls", type=int, help="for a built-in tool: the most calls one job may make")
    s.add_argument("--json", action="store_true", help="print JSON, for interfaces")
    s.set_defaults(func=cmd_tools)

    s = sub.add_parser("folder", aliases=["folders"],
                       help="folders shared with jobs and the Lead: list, add <path>, remove <name>")
    s.add_argument("action", nargs="?", choices=["list", "add", "remove"])
    s.add_argument("target", nargs="?", help="a folder's path (add) or a shared folder's name (remove)")
    s.add_argument("--name", help="the name jobs and specs use for it (add)")
    s.add_argument("--access", choices=["read", "write"], help="read, or write to let roles change files (add)")
    s.add_argument("--yes", action="store_true", help="do not ask (add): read unless --access write")
    s.add_argument("--json", action="store_true", help="for list: print JSON, for interfaces")
    s.set_defaults(func=cmd_folders)

    s = sub.add_parser("tick", help="start due scheduled jobs (called each minute by the system scheduler)")
    s.add_argument("--dry-run", action="store_true", help="show what would start or be noted, and change nothing")
    s.set_defaults(func=cmd_tick)

    s = sub.add_parser("pair", help="a code to pair the Ombro app with this server; --list, --forget, --forget-all")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="the devices paired now")
    g.add_argument("--forget", metavar="NAME", help="stop one device connecting (its name or id)")
    g.add_argument("--forget-all", action="store_true", help="stop every device connecting")
    g.add_argument("--device", metavar="NAME", help=argparse.SUPPRESS)  # the app, over SSH
    s.set_defaults(func=cmd_pair)

    s = sub.add_parser("serve", help="the server the desktop app connects to; `ombro serve install` starts it on its own")
    from ombro.serve import add_arguments

    add_arguments(s)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("_exec")  # internal: the detached job process
    s.add_argument("job_dir")
    s.set_defaults(func=cmd_exec)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None and len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        # `ombro` alone, in a terminal: the terminal interface (specification 13, 14.1)
        from ombro.tui import main as tui_main

        return tui_main()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (CliError, SetupError, mdfile.FrontMatterError) as e:
        print(f"ombro: {e}", file=sys.stderr)
        return 2
