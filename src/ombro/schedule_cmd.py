"""`ombro schedule ...` and `ombro tick`."""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path

from ombro import cron as cronmod
from ombro import mdfile, memory, runs, scheduler, schedules
from ombro.home import Home
from ombro.settings import SetupError, format_duration, format_tokens, load_limits

STALE_TICK_SECONDS = 5 * 60


def _cli():
    from ombro import cli

    return cli


def _ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _ago(ts: float, now: float) -> str:
    secs = max(0, now - ts)
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs // 60)} minutes ago"
    if secs < 2 * 86400:
        return f"{secs / 3600:.1f} hours ago"
    return f"{int(secs // 86400)} days ago"


def _limits_line(lim) -> str:
    return (f"allowance {lim.allowance}: {format_tokens(lim.max_tokens)} tokens, {lim.max_rounds} rounds, "
            f"{format_duration(lim.run_timeout)}")


def _above(home: Home, lim) -> list[str]:
    base = load_limits(home)
    out = []
    if base.max_tokens is not None and lim.max_tokens is not None and lim.max_tokens > base.max_tokens:
        out.append(f"tokens {lim.max_tokens:,} > {base.max_tokens:,}")
    if lim.max_rounds > base.max_rounds:
        out.append(f"rounds {lim.max_rounds} > {base.max_rounds}")
    if lim.run_timeout > base.run_timeout:
        out.append(f"time {format_duration(lim.run_timeout)} > {format_duration(base.run_timeout)}")
    return out


def scheduler_status(home: Home, *, enabled: int) -> list[str]:
    now = time.time()
    lines = []
    inst = scheduler.make_installer(home)
    try:
        cur = inst.installed()
    except OSError as e:
        cur, lines = None, [f"Scheduler: could not be checked ({e})."]
    if cur is None:
        lines.append("Scheduler: not installed, so no schedule fires. Install it with `ombro schedule install`.")
    else:
        lines.append(f"Scheduler: {cur.kind} entry installed, running {cur.detail}")
        if cur.home and Path(cur.home).resolve() != home.root:
            lines.append(f"  Warning: it ticks another home, {cur.home}, not {home.root}.")
        if cur.loaded is False:
            lines.append("  Warning: the launch agent file is there but launchd has not loaded it; "
                         "run `ombro schedule install` again.")
    last = schedules.last_tick(home)
    lines.append(f"Last tick: {_when(last)} ({_ago(last, now)})" if last else "Last tick: never")
    if enabled and cur is not None and (last is None or now - last > STALE_TICK_SECONDS):
        lines.append(f"  Warning: schedules are enabled but no tick has run in the last "
                     f"{STALE_TICK_SECONDS // 60} minutes. See {schedules.log_path(home)} and "
                     f"{home.scheduler_dir / 'scheduler.log'}.")
    return lines


def _offer_install(home: Home) -> None:
    inst = scheduler.make_installer(home)
    if inst.installed() is not None:
        return
    print("\nThe scheduler entry is not installed yet, so no schedule fires.")
    _install(home, inst)


def _install(home: Home, inst: scheduler.Installer) -> int:
    plan = inst.plan()
    print()
    if plan.kind == "launchd":
        print(f"This writes a launch agent to {plan.path} and loads it, so macOS runs `ombro tick` every minute")
        print("while you are logged in:\n")
        print(f"  runs      {' '.join(inst.program())}")
        print(f"  home      {home.root}")
        print(f"  logs      {schedules.log_path(home)} (tick), {inst.error_log} (errors)")
    else:
        print("This adds one line to your crontab, so cron runs `ombro tick` every minute:\n")
        print(f"  {plan.text}")
    if plan.replaces:
        print(f"\nIt replaces the entry installed now: {plan.replaces}")
    print("\nCommands: " + "; ".join(" ".join(c) for c in plan.commands))
    if not _cli()._confirm("Install it? [y/N] "):
        print("Nothing installed.")
        return 1
    try:
        inst.install()
    except OSError as e:
        raise _cli().CliError(str(e)) from None
    print("Installed. Ticks are logged in " + str(schedules.log_path(home)) + ".")
    print("Remove it with `ombro schedule uninstall`.")
    return 0


def _row(home: Home, s: schedules.Schedule, recent: list[tuple[Path, dict]], now: datetime) -> list[str]:
    nxt = "-"
    if s.cron and s.enabled:
        n = cronmod.next_slots(s.cron, now, 1)
        nxt = n[0].strftime("%a %d %b %H:%M") if n else "never"
    last = "-"
    if recent:
        rd, m = recent[-1]
        last = f"{str(m.get('due', ''))[:16].replace('T', ' ')} {m.get('status', '?')}"
    out = [f"{s.crew + '/' + s.name:<30} {'on' if s.enabled else 'off':<4} {s.cron_text or '?':<18} "
           f"{nxt:<17} {last}"]
    if s.error:
        out.append(f"  problem: {s.error}")
    else:
        try:
            schedules.prepare(home, s)
        except (SetupError, OSError, mdfile.FrontMatterError) as e:
            out.append(f"  problem: its runs would fail to start: {e}")
    return out


def cmd_list(home: Home, crew: str | None) -> int:
    rows = schedules.all_schedules(home, crew)
    by = schedules.scheduled_runs(home)
    now = datetime.now()
    if not rows:
        print('No schedules yet. Add one with `ombro schedule add <name> --spec <spec> --cron "30 7 * * 1-5"`.')
    else:
        print(f"{'SCHEDULE':<30} {'ON':<4} {'CRON':<18} {'NEXT':<17} LAST RUN")
        for s in rows:
            for line in _row(home, s, by.get((s.crew, s.name), []), now):
                print(line)
    print()
    for line in scheduler_status(home, enabled=sum(1 for s in rows if s.enabled and not s.error)):
        print(line)
    return 0


def cmd_show(home: Home, s: schedules.Schedule) -> int:
    print(f"Schedule  {s.crew}/{s.name}")
    print(f"File      {s.path}")
    print(f"Enabled   {'yes' if s.enabled else 'no'}")
    print(f"Cron      {s.cron_text or '?'} (local time)")
    print(f"Spec      {s.spec}")
    if s.error:
        print(f"Problem   {s.error}")
    else:
        try:
            p = schedules.prepare(home, s)
            print(f"Limits    {_limits_line(p.limits)}")
            above = _above(home, p.limits)
            if above:
                print(f"          above limits.md: {', '.join(above)}")
            print(f"Grace     {format_duration(s.grace_for(p.limits))}")
        except (SetupError, OSError, mdfile.FrontMatterError) as e:
            print(f"Problem   its runs would fail to start: {e}")
    if s.cron:
        print("\nNext due:")
        for w in cronmod.describe_next(s.cron, datetime.now(), 5):
            print(f"  {w}")
    recent = schedules.scheduled_runs(home).get((s.crew, s.name), [])
    print("\nRecent runs:" if recent else "\nNo runs yet.")
    for rd, m in recent[-5:]:
        print(f"  {str(m.get('due', ''))[:16].replace('T', ' ')}  {rd.name}  {m.get('status')}")
    noted = memory.schedule_entries(home, s.crew, s.name)
    if noted:
        print("\nSkipped or missed (from index.md):")
        for ln in noted[-5:]:
            print(f"  {ln[2:]}")
    return 0


def _spec_name(value: str) -> str:
    v = value.strip()
    if v.startswith("specs/"):
        v = v[len("specs/"):]
    return v[:-3] if v.endswith(".md") else v


def cmd_add(home: Home, args) -> int:
    from ombro.roles import NAME

    cli = _cli()
    crew = cli._crew(home, args.crew)
    name = (args.name or "").strip().lower()
    if not NAME.match(name):
        raise cli.CliError("Give the schedule a name of lower-case letters, digits, - and _, "
                           "e.g. `ombro schedule add morning-prep`.")
    path = home.schedules_dir(crew) / f"{name}.md"
    if path.exists():
        raise cli.CliError(f"Crew {crew} already has a schedule named {name} ({path}).")
    saved = [n for n, _ in cli.saved_specs(home, crew)]
    spec = args.spec
    if not spec:
        if not saved:
            raise cli.CliError(f"Crew {crew} has no saved specs. Make one with `ombro brief` first; "
                               "scheduled runs never interview.")
        spec = _ask(f"Spec to run (saved in crew {crew}: {', '.join(saved)}): ")
        if not spec:
            print("Nothing saved.")
            return 1
    spec = _spec_name(spec)
    if spec not in saved:
        listed = f" Saved specs: {', '.join(saved)}." if saved else " Make one with `ombro brief`."
        raise cli.CliError(f"Crew {crew} has no saved spec named {spec!r}.{listed}")
    cron_text = args.cron
    if not cron_text:
        cron_text = _ask('When, as cron in local time (minute hour day month weekday, e.g. "30 7 * * 1-5"): ')
        if not cron_text:
            print("Nothing saved.")
            return 1
    try:
        cron = cronmod.parse(cron_text)
    except cronmod.CronError as e:
        raise cli.CliError(f"cron: {e}") from None
    limits: dict = {}
    if args.allowance:
        limits["allowance"] = args.allowance
    if args.max_tokens:
        limits["max_tokens"] = args.max_tokens
    if args.run_timeout:
        limits["run_timeout"] = args.run_timeout
    s = schedules.Schedule(crew=crew, name=name, path=path, cron_text=cron.text, cron=cron,
                           spec=f"specs/{spec}.md", enabled=not args.disabled, limits=limits)
    if args.grace:
        try:
            s.grace = schedules.parse_duration(args.grace)
        except SetupError as e:
            raise cli.CliError(f"grace: {e}") from None
    try:
        p = schedules.prepare(home, s)
    except (SetupError, mdfile.FrontMatterError) as e:
        raise cli.CliError(f"This schedule's runs could not start: {e}") from None

    print(f"\nSchedule {crew}/{name}, saved as {path}")
    print(f"  spec      {s.spec}: {runs.first_line(p.spec_body, 70)}")
    print(f"  cron      {cron.text} (local time)")
    print(f"  next      {'; '.join(cronmod.describe_next(cron, datetime.now(), 3))}")
    print(f"  limits    {_limits_line(p.limits)}")
    above = _above(home, p.limits)
    if above:
        print(f"            above limits.md: {', '.join(above)}")
    print(f"  grace     {format_duration(s.grace_for(p.limits))}; a slot not started by then is skipped and noted")
    print(f"  enabled   {'yes' if s.enabled else 'no'}")
    if not cli._confirm("Save this schedule? [y/N] "):
        print("Nothing saved.")
        return 1
    schedules.write_schedule(home, crew, name, cron.text, spec, s.enabled, limits or None, args.grace)
    print(f"Saved {path}. Edit it at any time; the next tick reads it.")
    if s.enabled:
        _offer_install(home)
    return 0


def cmd_schedule(args) -> int:
    cli = _cli()
    home = cli._home()
    action = args.action or "list"
    if action == "list":
        return cmd_list(home, cli._crew(home, args.crew) if args.crew else None)
    if action == "install":
        return _install(home, scheduler.make_installer(home))
    if action == "uninstall":
        inst = scheduler.make_installer(home)
        cur = inst.installed()
        if cur is None:
            print("No scheduler entry is installed.")
            return 1
        print(f"Installed: {cur.kind} entry running {cur.detail}")
        print("Commands: " + "; ".join(" ".join(c) for c in inst.uninstall_commands()))
        if not cli._confirm("Remove it? Schedules stop firing until it is installed again. [y/N] "):
            print("Nothing changed.")
            return 1
        try:
            inst.uninstall()
        except OSError as e:
            raise cli.CliError(str(e)) from None
        print("Removed. Schedule files are kept. If you install it again, slots that fell due while it "
              "was removed (up to a week back) are noted as missed in index.md, not run.")
        return 0
    if action == "add":
        return cmd_add(home, args)
    if not args.name:
        raise cli.CliError(f"Give the schedule's name, e.g. `ombro schedule {action} morning-prep`.")
    try:
        s = schedules.find_schedule(home, args.name, cli._crew(home, args.crew) if args.crew else None)
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if action == "show":
        return cmd_show(home, s)
    if action in ("enable", "disable"):
        on = action == "enable"
        if s.enabled == on and not s.error:
            print(f"{s.crew}/{s.name} is already {action}d.")
            return 0
        try:
            schedules.set_enabled(s.path, on)
        except (OSError, mdfile.FrontMatterError) as e:
            raise cli.CliError(f"Could not change {s.path}: {e}") from None
        s = schedules.load_schedule(home, s.crew, s.path)
        print(f"{s.crew}/{s.name} is {action}d.")
        if s.error:
            print(f"It has a problem, so it will not run until it is fixed: {s.error}")
        elif on and s.cron:
            nxt = cronmod.describe_next(s.cron, datetime.now(), 1)
            print(f"It takes effect from its next slot: {nxt[0] if nxt else 'never'}.")
            _offer_install(home)
        return 0
    if action == "remove":
        if not cli._confirm(f"Remove schedule {s.crew}/{s.name}? The file is moved to the crew's "
                            "removed/schedules/ folder; its runs are kept. [y/N] "):
            print("Nothing changed.")
            return 1
        folder = home.crew_dir(s.crew) / "removed" / "schedules"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / s.path.name
        if target.exists():
            target = folder / f"{s.path.stem}-{time.strftime('%Y%m%d-%H%M%S')}.md"
        shutil.move(str(s.path), str(target))
        print(f"Moved it to {target}.")
        return 0
    raise cli.CliError(f"Unknown schedule action {action!r}.")


def cmd_tick(args) -> int:
    home = _cli()._home()
    result = schedules.tick(home, dry_run=args.dry_run)
    for line in result.lines:
        print(line)
    return 0
