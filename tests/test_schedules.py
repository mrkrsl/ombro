"""Schedules and `ombro tick` (section 11). Invented data, a fake clock and a fake
scheduler installer; nothing touches the network, launchctl or crontab."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import LEAD_REPORT, SPEC
from ombro import cli, fake, keys, mdfile, memory, jobs, scheduler, schedules
from ombro import cron as cronmod
from ombro.engine import Engine
from ombro.fake import ScriptedClient

L = ZoneInfo("Europe/London")


def ts(y, mo, d, h, mi, s=0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=L).timestamp()


LONG_AGO = ts(2026, 1, 1, 0, 0)


@pytest.fixture
def home(home):
    keys.set_key(home, "TEST_MODEL_KEY", "invented-test-key")  # so a model counts as connected
    save_spec(home)
    return home


def save_spec(home, name="teapots", body=SPEC, meta=None):
    path = home.specs_dir / f"{name}.md"
    mdfile.write(path, meta or {}, body)
    return path


def add_schedule(home, name="morning", cron="30 7 * * *", spec="teapots", *, enabled=True, limits=None,
                 grace=None, mtime=LONG_AGO):
    path = schedules.write_schedule(home, name, cron, spec, enabled, limits, grace)
    os.utime(path, (mtime, mtime))
    return path


class Starts:
    """A starter that records runs and leaves them as created (status running, no process)."""

    def __init__(self, fail: Exception | None = None):
        self.dirs = []
        self.fail = fail

    def __call__(self, home, job_dir):
        if self.fail:
            raise self.fail
        self.dirs.append(job_dir)


def tick(home, now, starter=None, **kw):
    return schedules.tick(home, clock=lambda: now, tz=L, starter=starter if starter is not None else Starts(), **kw)


def index_lines(home, crew="default"):
    return [ln for ln in memory.index_path(home, crew).read_text().splitlines() if ln.startswith("- ")]


def finish_job(job_dir, status="completed"):
    jobs.update_job(job_dir, status=status)


# ---------------------------------------------------------------------------
# cron


def test_cron_fields_names_steps_and_aliases():
    c = cronmod.parse("*/15 9-17/4 * jan,JUL mon-fri")
    assert c.minutes == {0, 15, 30, 45} and c.hours == {9, 13, 17}
    assert c.months == {1, 7} and c.weekdays == {1, 2, 3, 4, 5}
    assert cronmod.parse("0 0 * * 7").weekdays == {0} == cronmod.parse("0 0 * * sun").weekdays
    assert cronmod.parse("5/20 * * * *").minutes == {5, 25, 45}
    assert cronmod.parse("@daily").minutes == {0} and cronmod.parse("@weekly").weekdays == {0}


@pytest.mark.parametrize("text", ["", "* * * *", "61 * * * *", "5-1 * * * *", "* * * * fun", "*/0 * * * *",
                                  "@reboot", "0 0 30 2 *", "1,,2 * * * *"])
def test_cron_errors(text):
    with pytest.raises(cronmod.CronError):
        cronmod.parse(text)


def test_cron_day_of_month_or_day_of_week():
    either = cronmod.parse("0 9 13 * fri")  # both restricted: either matches
    assert either.matches(datetime(2026, 11, 20, 9, 0))  # a Friday, not the 13th
    assert either.matches(datetime(2026, 12, 13, 9, 0))  # the 13th, a Sunday
    assert not either.matches(datetime(2026, 11, 16, 9, 0))
    both = cronmod.parse("0 9 */2 * fri")  # day of month starts with *: both must match
    assert both.matches(datetime(2026, 11, 13, 9, 0))  # Friday the 13th (an odd day)
    assert not both.matches(datetime(2026, 11, 20, 9, 0))  # Friday the 20th (even)


def test_clocks_going_forward_start_the_skipped_slot_once_after_the_change():
    c = cronmod.parse("30 1 * * *")
    slots = cronmod.slots_between(c, ts(2026, 3, 28, 23, 0), ts(2026, 3, 29, 6, 0), L)
    assert [cronmod.slot_name(w) for w, _ in slots] == ["2026-03-29T01:30"]
    assert cronmod.to_local(slots[0][1], L) == datetime(2026, 3, 29, 2, 0)  # the first minute after the jump


def test_clocks_going_back_list_the_repeated_slot_once():
    c = cronmod.parse("30 1 * * *")
    slots = cronmod.slots_between(c, ts(2026, 10, 24, 23, 0), ts(2026, 10, 25, 6, 0), L)
    assert [cronmod.slot_name(w) for w, _ in slots] == ["2026-10-25T01:30"]


def test_clocks_going_back_never_start_a_slot_twice(home):
    add_schedule(home, cron="30 1 * * *")
    first = datetime(2026, 10, 25, 1, 30, 10, tzinfo=L).timestamp()  # BST, fold 0
    second = datetime(2026, 10, 25, 1, 30, 10, fold=1, tzinfo=L).timestamp()  # GMT, an hour later
    assert second - first == 3600
    a = tick(home, first)
    finish_job(a.started[0])
    b = tick(home, second)
    assert len(a.started) == 1 and not b.started and not b.noted


def test_next_slots_for_display():
    nxt = cronmod.next_slots(cronmod.parse("0 9 * * mon-fri"), datetime(2026, 11, 13, 10, 0), 2)
    assert nxt == [datetime(2026, 11, 16, 9, 0), datetime(2026, 11, 17, 9, 0)]


# ---------------------------------------------------------------------------
# tick: due, late, missed


def test_a_due_slot_starts_once_detached_with_limits_fixed(home):
    add_schedule(home, limits={"allowance": "conservative", "job_timeout": "15m"})
    starter = Starts()
    r = tick(home, ts(2026, 11, 12, 7, 30, 20), starter)
    assert len(r.started) == 1 and starter.dirs == r.started
    meta = jobs.read_job(r.started[0])[0]
    assert meta["schedule"] == "morning" and meta["due"] == "2026-11-12T07:30"
    assert meta["source"] == "schedule morning (schedules/morning.md), due 2026-11-12 07:30"
    assert meta["limits"]["max_tokens"] == 300_000 and meta["limits"]["job_timeout"] == 900
    assert "fictional teapot makers" in (r.started[0] / "spec.md").read_text()
    # The same minute again, and the next minute: nothing new.
    assert not tick(home, ts(2026, 11, 12, 7, 30, 50), starter).started
    assert not tick(home, ts(2026, 11, 12, 7, 31, 5), starter).started
    log = schedules.log_path(home).read_text()
    assert "morning due 2026-11-12 07:30: started job" in log
    assert log.count("tick: 1 schedule(s)") == 3


def test_a_late_tick_within_grace_still_starts(home):
    add_schedule(home)
    tick(home, ts(2026, 11, 12, 7, 20))
    r = tick(home, ts(2026, 11, 12, 7, 38))
    assert len(r.started) == 1 and jobs.read_job(r.started[0])[0]["due"] == "2026-11-12T07:30"


def test_a_slot_past_grace_is_missed_and_noted_not_started(home):
    add_schedule(home)
    tick(home, ts(2026, 11, 12, 7, 20))
    r = tick(home, ts(2026, 11, 12, 7, 50))
    assert not r.started
    [line] = index_lines(home)
    assert "schedule:morning@2026-11-12T07:30 · missed · 07:30:" in line
    assert "the machine was off or asleep (no tick from 12 Nov 07:20 to 12 Nov 07:50)" in line
    assert len(line) <= 200
    assert line.endswith("schedules/morning.md")
    assert not tick(home, ts(2026, 11, 12, 7, 51)).noted  # noted once


def test_a_long_sleep_is_one_entry_per_schedule(home):
    add_schedule(home)
    tick(home, ts(2026, 11, 9, 23, 0))
    r = tick(home, ts(2026, 11, 12, 9, 14))
    assert not r.started
    [line] = index_lines(home)
    assert "schedule:morning@2026-11-10T07:30..2026-11-12T07:30 · missed · 3 slots:" in line
    assert len(line) <= 200
    assert not tick(home, ts(2026, 11, 12, 9, 15)).noted
    assert len(index_lines(home)) == 1
    # The Lead of the crew's next job sees it among the recent entries.
    _, recent = memory.lead_context(home, ["default"], schedules.base_limits(home))
    assert "3 slots:" in recent


def test_without_a_heartbeat_only_the_grace_period_is_considered(home):
    add_schedule(home)
    r = tick(home, ts(2026, 11, 12, 9, 0))  # first tick ever, long after the slot
    assert not r.started and not r.noted


def test_a_disabled_schedule_never_starts_and_reenabling_reports_no_misses(home):
    path = add_schedule(home, enabled=False)
    tick(home, ts(2026, 11, 12, 7, 20))
    assert not tick(home, ts(2026, 11, 12, 7, 31)).started
    schedules.set_enabled(path, True)
    os.utime(path, (ts(2026, 11, 12, 7, 40),) * 2)
    r = tick(home, ts(2026, 11, 12, 7, 41))
    assert not r.started and not r.noted
    assert index_lines(home) == []
    assert len(tick(home, ts(2026, 11, 13, 7, 30, 5)).started) == 1


def test_a_new_schedule_takes_effect_from_its_next_slot(home):
    add_schedule(home, mtime=ts(2026, 11, 12, 7, 33))
    tick(home, ts(2026, 11, 12, 7, 32))
    r = tick(home, ts(2026, 11, 12, 7, 35))
    assert not r.started and not r.noted


def test_editing_the_file_takes_effect_at_the_next_tick(home):
    path = add_schedule(home)
    tick(home, ts(2026, 11, 12, 6, 50))
    meta, body = mdfile.read(path)
    meta["cron"] = "45 7 * * *"
    mdfile.write(path, meta, body)
    os.utime(path, (ts(2026, 11, 12, 7, 0),) * 2)
    assert not tick(home, ts(2026, 11, 12, 7, 30, 5)).started
    r = tick(home, ts(2026, 11, 12, 7, 45, 5))
    assert jobs.read_job(r.started[0])[0]["due"] == "2026-11-12T07:45"


def test_a_late_tick_starts_the_newest_slot_and_notes_the_ones_before(home):
    add_schedule(home, cron="* * * * *")
    first = tick(home, ts(2026, 11, 12, 7, 0, 5))
    finish_job(first.started[0])
    r = tick(home, ts(2026, 11, 12, 7, 3, 30))
    assert [jobs.read_job(d)[0]["due"] for d in r.started] == ["2026-11-12T07:03"]
    [line] = index_lines(home)
    assert "schedule:morning@2026-11-12T07:01..2026-11-12T07:02 · skipped" in line
    assert "a later slot, 2026-11-12 07:03, was started instead" in line
    finish_job(r.started[0])
    assert len(tick(home, ts(2026, 11, 12, 7, 4, 10)).started) == 1
    assert len(index_lines(home)) == 1  # 07:01 and 07:02 are not noted again


# ---------------------------------------------------------------------------
# overlap and caps


def test_overlap_waits_within_grace_then_is_skipped_with_the_reason(home):
    add_schedule(home)
    day1 = tick(home, ts(2026, 11, 11, 7, 30, 5)).started[0]  # left running
    assert tick(home, ts(2026, 11, 12, 7, 30, 5)).waiting == ["morning@2026-11-12T07:30"]
    assert tick(home, ts(2026, 11, 12, 7, 44)).waiting
    r = tick(home, ts(2026, 11, 12, 7, 46))
    assert not r.started and not r.waiting
    [line] = index_lines(home)
    assert f"skipped · 07:30: not started within the grace period: the previous job {day1.name}" in line
    finish_job(day1)
    assert len(tick(home, ts(2026, 11, 13, 7, 30, 5)).started) == 1


def test_overlap_that_clears_within_grace_starts_late(home):
    add_schedule(home)
    day1 = tick(home, ts(2026, 11, 11, 7, 30, 5)).started[0]
    assert tick(home, ts(2026, 11, 12, 7, 30, 5)).waiting
    finish_job(day1)
    r = tick(home, ts(2026, 11, 12, 7, 33))
    assert len(r.started) == 1 and index_lines(home) == []


def test_caps_on_running_and_starting_scheduled_runs(home):
    meta, body = mdfile.read(home.limits)
    mdfile.write(home.limits, {**meta, "schedule_max_running": 1}, body)
    add_schedule(home, "alpha")
    add_schedule(home, "beta")
    r = tick(home, ts(2026, 11, 12, 7, 30, 5))
    assert len(r.started) == 1 and r.waiting == ["beta@2026-11-12T07:30"]
    assert "1 scheduled jobs were already running (schedule_max_running)" in "\n".join(r.lines)

    mdfile.write(home.limits, {**meta, "schedule_max_running": 5, "schedule_max_starts": 1}, body)
    finish_job(r.started[0])
    r2 = tick(home, ts(2026, 11, 13, 7, 30, 5))
    assert len(r2.started) == 1 and len(r2.waiting) == 1
    assert "schedule_max_starts" in "\n".join(r2.lines)


def test_a_tick_that_finds_the_lock_held_does_nothing(home):
    add_schedule(home)
    with schedules.tick_lock(home) as held:
        assert held
        r = tick(home, ts(2026, 11, 12, 7, 30, 5))
    assert r.busy and not r.started
    assert "another tick is running" in schedules.log_path(home).read_text()


def test_two_ticks_at_once_start_a_slot_once(home):
    add_schedule(home)
    started = []

    def slow(home_, job_dir):
        time.sleep(0.3)
        started.append(job_dir)

    now = ts(2026, 11, 12, 7, 30, 5)
    threads = [threading.Thread(target=tick, args=(home, now, slow)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(started) == 1
    assert len(jobs.all_jobs(home)) == 1


# ---------------------------------------------------------------------------
# runs that cannot start, and limits


def test_a_missing_spec_becomes_a_failed_job_with_a_report(home):
    add_schedule(home, spec="no-such-spec")
    r = tick(home, ts(2026, 11, 12, 7, 30, 5))
    [job_dir] = r.failed
    meta = jobs.read_job(job_dir)[0]
    assert meta["status"] == "failed" and meta["due"] == "2026-11-12T07:30"
    rep = (job_dir / "report.md").read_text()
    assert "its spec specs/no-such-spec.md does not exist" in rep and "the job could not start" in rep
    assert any(job_dir.name in ln and "failed" in ln for ln in index_lines(home))
    assert not tick(home, ts(2026, 11, 12, 7, 31)).failed  # recorded once


def test_no_model_connected_becomes_a_failed_job(home):
    keys.remove_key(home, "TEST_MODEL_KEY")
    add_schedule(home)
    [job_dir] = tick(home, ts(2026, 11, 12, 7, 30, 5)).failed
    assert "no model is connected" in (job_dir / "report.md").read_text()


def test_a_process_that_cannot_be_started_is_reported(home):
    add_schedule(home)
    [job_dir] = tick(home, ts(2026, 11, 12, 7, 30, 5), Starts(fail=OSError("invented spawn failure"))).failed
    assert jobs.read_job(job_dir)[0]["status"] == "failed"
    assert "invented spawn failure" in (job_dir / "report.md").read_text()


def test_a_scheduled_job_must_have_a_token_cap(home):
    add_schedule(home, limits={"allowance": "unlimited"})
    [job_dir] = tick(home, ts(2026, 11, 12, 7, 30, 5)).failed
    assert "must have a token cap" in (job_dir / "report.md").read_text()


def test_schedule_limits_layer_over_the_spec_and_limits_md(home):
    save_spec(home, "big", meta={"limits": {"allowance": "generous", "max_rounds": 3}})
    add_schedule(home, spec="big", limits={"max_tokens": 50_000, "budget_usd": 0.5})
    [job_dir] = tick(home, ts(2026, 11, 12, 7, 30, 5)).started
    lim = jobs.read_job(job_dir)[0]["limits"]
    assert lim["max_tokens"] == 50_000 and lim["max_rounds"] == 3 and lim["job_timeout"] == 7200
    assert "budget_usd" not in lim


def test_a_broken_schedule_file_is_logged_once(home):
    add_schedule(home, cron="61 * * * *")
    tick(home, ts(2026, 11, 12, 7, 30, 5))
    tick(home, ts(2026, 11, 12, 7, 31, 5))
    assert schedules.log_path(home).read_text().count("problem in schedules/morning.md") == 1


def test_schedule_file_problems_are_found(home):
    path = home.schedules_dir / "odd.md"
    mdfile.write(path, {"name": "other", "cron": "30 7 * * *", "spec": "../etc/x", "enabled": "yes",
                        "colour": "blue"})
    err = schedules.load_schedule(home, path).error
    assert "unknown setting(s): colour" in err and "does not match the file name" in err
    assert "must be a saved spec" in err and "enabled must be true or false" in err


def test_a_scheduled_job_end_to_end_with_the_fake_model(home):
    add_schedule(home)
    script = [fake.brief("researcher", "List the teapot makers."), fake.result("Listed three."),
              fake.finish("Done."), fake.keep_notes(), LEAD_REPORT]

    def run_here(home_, job_dir):
        client = ScriptedClient(script)
        Engine(home_, job_dir, client_factory=lambda spec: client).execute()

    [job_dir] = tick(home, ts(2026, 11, 12, 7, 30, 5), run_here).started
    meta = jobs.read_job(job_dir)[0]
    assert meta["status"] == "completed"
    assert not (job_dir / "interview.md").exists()
    rep = (job_dir / "report.md").read_text()
    assert "LEAD-REPORT-MARKER" in rep and "_Source: schedule morning" in rep
    assert any(job_dir.name in ln and "completed" in ln for ln in index_lines(home))


def test_dry_run_changes_nothing(home):
    add_schedule(home)
    r = tick(home, ts(2026, 11, 12, 7, 30, 5), dry_run=True)
    assert "morning due 2026-11-12 07:30: would start a job" in "\n".join(r.lines)
    assert jobs.all_jobs(home) == [] and schedules.last_tick(home) is None
    assert not schedules.log_path(home).exists()


# ---------------------------------------------------------------------------
# The scheduler entry (fake installer only)


def test_launch_agent_file_and_commands(home, no_system_scheduler):
    system = no_system_scheduler
    inst = scheduler.make_installer(home)
    assert inst.installed() is None
    inst.install()
    data = plistlib.loads(inst.plist_path.read_bytes())
    assert inst.plist_path.parent == system.agents_dir and inst.plist_path.name == "local.ombro.tick.plist"
    assert data["Label"] == "local.ombro.tick"
    assert data["ProgramArguments"] == ["/opt/invented/bin/python3", "-m", "ombro", "tick"]
    assert data["EnvironmentVariables"]["OMBRO_HOME"] == str(home.root)
    assert data["EnvironmentVariables"]["PATH"].startswith("/opt/invented/bin:")
    assert data["StartInterval"] == 60 and data["RunAtLoad"] and data["AbandonProcessGroup"]
    assert data["StandardErrorPath"] == str(home.scheduler_dir / "scheduler.log")
    assert system.commands[-1] == ["launchctl", "bootstrap", "gui/501", str(inst.plist_path)]
    cur = inst.installed()
    assert cur.loaded and cur.home == str(home.root)
    inst.install()  # again: unloads the old one first
    assert system.commands[-2] == ["launchctl", "bootout", "gui/501/local.ombro.tick"]
    assert inst.uninstall() and not inst.plist_path.exists() and not system.loaded
    assert not inst.uninstall()


def test_the_desktop_app_runs_its_own_binary(home, no_system_scheduler):
    inst = scheduler.Installer(home, agents_dir=no_system_scheduler.agents_dir, run=no_system_scheduler.run,
                               python="/Applications/Ombro.app/Contents/MacOS/ombro-serve", uid=501, frozen=True,
                               platform="darwin")
    data = plistlib.loads(inst.plist().encode())
    assert data["ProgramArguments"] == ["/Applications/Ombro.app/Contents/MacOS/ombro-serve", "tick"]


def test_check_says_what_is_wrong_and_ensure_puts_it_right(home, no_system_scheduler, tmp_path):
    system = no_system_scheduler
    inst = scheduler.make_installer(home)
    state = scheduler.check(home, inst)
    assert state == {"ok": False, "can_install": True,
                     "message": "The scheduler service is not installed, so scheduled jobs don't run."}
    python = tmp_path / "bin" / "python3"
    python.parent.mkdir()
    python.write_text("")
    inst.python = str(python)
    assert scheduler.ensure(home, inst) and scheduler.check(home, inst)["ok"]
    assert not scheduler.ensure(home, inst)  # working: left alone
    system.loaded = False  # macOS dropped it
    assert scheduler.check(home, inst)["message"] == "The scheduler service is installed but isn't working."
    assert scheduler.ensure(home, inst) and scheduler.check(home, inst)["ok"]
    python.unlink()  # what it runs has gone, e.g. the app moved
    assert not scheduler.check(home, inst)["ok"] and scheduler.ensure(home, inst) is True


def test_ensure_leaves_another_homes_service_alone(home, no_system_scheduler, tmp_path):
    from ombro.home import Home

    other = Home(tmp_path / "other-home")
    scheduler.make_installer(other).install()
    inst = scheduler.make_installer(home)
    assert not scheduler.ensure(home, inst)
    state = scheduler.check(home, inst)
    assert not state["ok"] and state["can_install"] and str(other.root) in state["message"]


def test_the_app_installs_it_itself_only_for_the_usual_home_from_applications(home, monkeypatch):
    from ombro import home as home_mod

    monkeypatch.setattr(home_mod, "default_root", lambda: home.root)
    exe = "/Applications/Ombro.app/Contents/MacOS/ombro-serve"
    mac = {"platform": "darwin"}
    assert not scheduler.may_install_itself(home, exe, **mac)  # not the packaged app
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert scheduler.may_install_itself(home, exe, **mac)
    assert not scheduler.may_install_itself(home, "/Users/invented/ombro/gui/target/release/ombro-serve", **mac)
    assert not scheduler.may_install_itself(home, "/private/var/folders/x/AppTranslocation/y/Ombro.app/ombro-serve", **mac)
    # Linux: the .deb puts it in /usr/bin; an AppImage runs from a mount that goes when it quits.
    assert scheduler.may_install_itself(home, "/usr/bin/ombro-serve", platform="linux")
    assert not scheduler.may_install_itself(home, "/tmp/.mount_OmbroAbc/usr/bin/ombro-serve", platform="linux")
    assert not scheduler.may_install_itself(home, "/home/invented/ombro/gui/target/release/ombro-serve", platform="linux")
    monkeypatch.setattr(home_mod, "default_root", lambda: home.root / "elsewhere")
    assert not scheduler.may_install_itself(home, exe, **mac)  # a demo or test home


def test_crontab_line_is_added_once_and_removed(home, no_system_scheduler):
    system = no_system_scheduler
    system.platform = "linux"
    system.crontab = "0 1 * * * /usr/local/bin/invented-backup\n"
    inst = scheduler.make_installer(home)
    inst.install()
    inst.install()
    lines = system.crontab.splitlines()
    assert lines[0] == "0 1 * * * /usr/local/bin/invented-backup"
    assert len(lines) == 2 and lines[1].startswith("* * * * * OMBRO_HOME=")
    assert "/opt/invented/bin/python3 -m ombro tick" in lines[1] and lines[1].endswith("# ombro-tick")
    assert inst.installed().home == str(home.root)
    assert inst.uninstall()
    assert system.crontab == "0 1 * * * /usr/local/bin/invented-backup\n"


# ---------------------------------------------------------------------------
# ombro schedule (in process, scripted answers)


class Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_schedule_add_saves_the_file_and_offers_the_install(home, monkeypatch, capsys, no_system_scheduler):
    answers = Answers("y", "y")
    monkeypatch.setattr("builtins.input", answers)
    assert cli.main(["schedule", "add", "morning", "--spec", "teapots", "--cron", "30 7 * * 1-5",
                     "--allowance", "conservative", "--grace", "5m"]) == 0
    out = capsys.readouterr().out
    assert "allowance conservative: 300,000 tokens" in out and "not installed yet" in out
    assert "local.ombro.tick.plist" in out and "Installed." in out
    meta, _ = mdfile.read(home.schedules_dir / "morning.md")
    assert meta == {"name": "morning", "cron": "30 7 * * 1-5", "spec": "specs/teapots.md", "enabled": True,
                    "limits": {"allowance": "conservative"}, "grace": "5m"}
    assert no_system_scheduler.loaded
    assert answers.prompts == ["Save this schedule? [y/N] ", "Install it? [y/N] "]


def test_schedule_add_asks_for_what_is_missing_and_checks_it(home, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", Answers("teapots", "@daily", "y", "n"))
    assert cli.main(["schedule", "add", "nightly"]) == 0
    assert "Nothing installed." in capsys.readouterr().out
    assert cli.main(["schedule", "add", "x", "--spec", "nope", "--cron", "0 7 * * *"]) == 2
    assert "no saved spec named 'nope'" in capsys.readouterr().err
    assert cli.main(["schedule", "add", "y", "--spec", "teapots", "--cron", "0 25 * * *"]) == 2
    assert "hour: 25 is outside 0-23" in capsys.readouterr().err
    assert cli.main(["schedule", "add", "nightly", "--spec", "teapots", "--cron", "0 7 * * *"]) == 2


def test_schedule_list_show_enable_disable_remove(home, monkeypatch, capsys):
    add_schedule(home)
    add_schedule(home, "broken", cron="not a cron")
    assert cli.main(["schedule"]) == 0
    out = capsys.readouterr().out
    assert "morning" in out and "30 7 * * *" in out and "problem: cron:" in out
    assert "Scheduler: not installed" in out and "Last tick: never" in out

    assert cli.main(["schedule", "disable", "morning"]) == 0
    assert mdfile.read(home.schedules_dir / "morning.md")[0]["enabled"] is False
    monkeypatch.setattr("builtins.input", Answers("n"))
    assert cli.main(["schedule", "enable", "morning"]) == 0
    out = capsys.readouterr().out
    assert "takes effect from its next slot" in out and "Install it?" not in out or "Nothing installed" in out
    assert mdfile.read(home.schedules_dir / "morning.md")[0]["enabled"] is True

    assert cli.main(["schedule", "show", "morning"]) == 0
    out = capsys.readouterr().out
    assert "Next due:" in out and "allowance generous" in out and "No jobs yet." in out

    monkeypatch.setattr("builtins.input", Answers("y"))
    assert cli.main(["schedule", "remove", "morning"]) == 0
    assert not (home.schedules_dir / "morning.md").exists()
    assert (home.removed_dir / "schedules" / "morning.md").is_file()


def test_schedule_install_and_uninstall_ask_first(home, monkeypatch, capsys, no_system_scheduler):
    monkeypatch.setattr("builtins.input", Answers("n"))
    assert cli.main(["schedule", "install"]) == 1
    assert no_system_scheduler.commands == []
    monkeypatch.setattr("builtins.input", Answers("y"))
    assert cli.main(["schedule", "install"]) == 0
    assert cli.main(["schedule", "list"]) == 0
    assert "Scheduler: launchd entry installed" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", Answers("n"))
    assert cli.main(["schedule", "uninstall"]) == 1 and no_system_scheduler.loaded
    monkeypatch.setattr("builtins.input", Answers("y"))
    assert cli.main(["schedule", "uninstall"]) == 0 and not no_system_scheduler.loaded


# ---------------------------------------------------------------------------
# ombro tick as a real command, starting a real detached job (fake model)


def test_ombro_tick_starts_a_detached_job_that_completes(home):
    (home.root / "script.json").write_text(json.dumps([
        fake.brief("researcher", "List the teapot makers."), fake.result("Listed three."),
        fake.finish("Done."), fake.keep_notes(),
        fake.say("## What was done\nTICK-REPORT-MARKER.\n\n## What was not done and why\nNothing."),
    ]))
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json"}, "cheap": "default"}})
    path = add_schedule(home, cron="* * * * *")
    minute = int(time.time()) // 60 * 60
    os.utime(path, (minute - 1, minute - 1))
    r = subprocess.run([sys.executable, "-m", "ombro", "tick"], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "OMBRO_HOME": str(home.root)})
    assert r.returncode == 0, r.stderr
    assert "morning due" in r.stdout and "started job" in r.stdout
    [job_dir] = jobs.all_jobs(home)
    deadline = time.time() + 30
    while jobs.read_job(job_dir)[0]["status"] == "running" and time.time() < deadline:
        time.sleep(0.1)
    meta = jobs.read_job(job_dir)[0]
    assert meta["status"] == "completed" and meta["schedule"] == "morning" and meta["pid"]
    assert "TICK-REPORT-MARKER" in (job_dir / "report.md").read_text()
    assert "started job" in schedules.log_path(home).read_text()


def test_a_percent_sign_in_a_path_is_escaped_for_crontab(tmp_path, no_system_scheduler):
    from ombro.home import Home, ensure_home

    system = no_system_scheduler
    system.platform = "linux"
    system.crontab = ""
    home = Home(tmp_path / "100% invented home")
    ensure_home(home)
    inst = scheduler.make_installer(home)
    line = inst.cron_line()
    assert "%" not in line.replace("\\%", "")  # every % is escaped
    inst.install()
    assert inst.installed().home == str(home.root)


def test_a_path_with_a_line_break_is_refused_for_crontab(tmp_path, no_system_scheduler):
    from ombro.home import Home

    no_system_scheduler.platform = "linux"
    inst = scheduler.make_installer(Home(tmp_path / "invented\nhome"))
    with pytest.raises(OSError, match="line break"):
        inst.cron_line()
