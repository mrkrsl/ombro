"""The chat Lead puts a saved spec on a schedule: it offers, Ombro says when it
would run in the schedules' time zone, and on a yes sets it up as the Scheduled page does. All
invented; fake model; no network; the scheduler service is a fake (conftest)."""

from __future__ import annotations

from ombro import cron as cronmod
from ombro import fake, mdfile, prompts, scheduler, schedules
from test_chat import ChatSession
from test_saved_specs import saved

OFFER = {"spec": "teapot-makers", "cron": "30 8 * * *", "when": "every morning at 8.30"}


def test_the_lead_offers_a_schedule_and_a_yes_sets_it_up(home, monkeypatch):
    monkeypatch.setattr(cronmod, "zone_name", lambda: "Europe/Invented")
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_schedule", OFFER), fake.say("Done.")], ["Run the teapot task every morning at 8.30", "y"])
    assert "I can schedule 'teapot-makers' to run every day at 8:30am. The next runs would be" in s.output
    assert " at 8:30am and " in s.output
    # Plain words only: no cron and no time zone (schedules run in the machine's own time).
    assert "30 8 * * *" not in s.output and "Europe/Invented" not in s.output and "UTC" not in s.output
    assert any("Shall I set it up? [Y/n]" in a for a in s.answers.asked)
    sch = schedules.find_schedule(home, "teapot-makers")
    assert sch.enabled and sch.cron_text == "30 8 * * *" and sch.spec == "specs/teapot-makers.md"
    (reply,) = s.tool_replies()
    assert reply["status"] == "scheduled" and reply["zone"].startswith("Europe/Invented")
    # Not installed (the fake system has none, and a test home never installs it itself): the user is told.
    assert "The scheduler service is not installed" in s.output and "Install now on the Scheduled page" in s.output
    assert "won't run until" in reply["scheduler"]
    assert "Scheduled: teapot-makers starts spec 'teapot-makers'" in s.transcript


def test_no_leaves_nothing_scheduled(home):
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_schedule", OFFER), fake.say("All right.")], ["Schedule the teapot task", "n"])
    assert schedules.all_schedules(home) == []
    assert "the user said no" in s.tool_replies()[0]["status"]


def test_once_the_service_works_no_warning_is_given(home, monkeypatch):
    monkeypatch.setattr(scheduler, "check", lambda h, inst=None: {"ok": True, "message": "", "can_install": False})
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_schedule", OFFER), fake.say("Done.")], ["Schedule it", "y"])
    assert "not installed" not in s.output and "scheduler" not in s.tool_replies()[0]
    assert "Scheduled: 'teapot-makers' runs every day at 8:30am." in s.output


def test_the_app_installs_the_service_itself_when_it_may(home, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "may_install_itself", lambda h: True)
    monkeypatch.setattr(scheduler, "ensure", lambda h, inst=None: calls.append(h) or True)
    monkeypatch.setattr(scheduler, "check", lambda h, inst=None: {"ok": True, "message": "", "can_install": False})
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_schedule", OFFER), fake.say("Done.")], ["Schedule it", "y"])
    assert calls == [home] and "not installed" not in s.output


def test_bad_offers_are_refused_with_the_reason(home):
    saved(home)
    schedules.write_schedule(home, "teapot-makers", "0 6 * * 1-5", "teapot-makers", True)
    s = ChatSession(home)
    s([fake.call("offer_schedule", {**OFFER, "spec": "kettles"}),
       fake.call("offer_schedule", {**OFFER, "cron": "8.30 every day"}),
       fake.call("offer_schedule", OFFER),
       fake.say("Sorted.")], ["Schedule things"])
    unknown, bad_cron, taken = s.tool_replies()
    assert "no saved spec called 'kettles'" in unknown["error"] and "teapot-makers" in unknown["error"]
    assert bad_cron["error"].startswith("cron:")
    assert "already a schedule called teapot-makers" in taken["error"] and "0 6 * * 1-5" in taken["error"]
    assert len(schedules.all_schedules(home)) == 1


def test_the_lead_sees_the_time_zone_and_the_schedules(home, monkeypatch):
    monkeypatch.setattr(cronmod, "zone_name", lambda: "Europe/Invented")
    saved(home)
    schedules.write_schedule(home, "morning-teapots", "0 7 * * 1-5", "teapot-makers", False)
    s = ChatSession(home)
    s([fake.say("Hello.")], ["Hi"])
    system = s.client.calls[0]["messages"][0]["content"]
    assert "Schedules run in this machine's local time: Europe/Invented" in system
    assert "- morning-teapots: spec specs/teapot-makers.md, cron `0 7 * * 1-5`, off" in system
    rules = " ".join(prompts.CHAT_RULES.split())
    assert "call offer_schedule" in rules and "Never send them to the Scheduled page or the command line" in rules
    assert "offer_schedule" in [t["function"]["name"] for t in s.client.calls[0]["tools"]]


def test_the_brief_page_chat_offers_a_schedule_rather_than_the_page(home, job):
    from ombro import jobs

    saved(home)
    job([fake.finish("Done."), fake.keep_notes(), {"content": "Report."}])
    job_dir = jobs.all_jobs(home)[-1]
    meta, body = mdfile.read(job_dir / "job.md")
    mdfile.write(job_dir / "job.md", {**meta, "source": "spec file teapot-makers.md"}, body)
    s = ChatSession(home)
    s([fake.say("Hello.")], [])
    s.chat.discuss_brief(job_dir)
    note = s.chat.pending_note
    assert "offer_schedule with spec `teapot-makers`" in note and "Scheduled page" not in note


def test_the_zone_is_named_with_its_offset():
    from datetime import datetime

    text = cronmod.local_zone(datetime(2026, 7, 1, 12, 0))
    assert "UTC" in text and "(" in text


def test_the_zone_name_is_read_from_the_link_on_macos(monkeypatch):
    """macOS: /etc/localtime -> /var/db/timezone/zoneinfo/Europe/London, which itself ends up in
    /usr/share/zoneinfo.default/: either gives the name."""
    import os

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(os.path, "islink", lambda p: True)
    monkeypatch.setattr(os, "readlink", lambda p: "/var/db/timezone/zoneinfo/Europe/Invented")
    assert cronmod.zone_name() == "Europe/Invented"
    monkeypatch.setattr(os, "readlink", lambda p: "")
    monkeypatch.setattr(os.path, "realpath", lambda p: "/usr/share/zoneinfo.default/America/Invented")
    assert cronmod.zone_name() == "America/Invented"
    monkeypatch.setenv("TZ", "Asia/Invented")
    assert cronmod.zone_name() == "Asia/Invented"


def test_times_are_put_in_words_and_the_lead_s_words_cover_the_rest(home):
    from datetime import datetime

    assert cronmod.in_words(cronmod.parse("0 9 * * 1-5")) == "every weekday at 9am"
    assert cronmod.in_words(cronmod.parse("0 7 * * 1,3,5")) == "every Monday, Wednesday and Friday at 7am"
    assert cronmod.in_words(cronmod.parse("0 12 1 * *")) == "on the 1st of each month at noon"
    assert cronmod.in_words(cronmod.parse("*/15 * * * *")) == ""
    assert cronmod.next_in_words(cronmod.parse("0 9 * * 1-5"), datetime(2026, 11, 20, 10, 0), 1) == \
        ["Mon 23 Nov at 9am"]
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_schedule", {**OFFER, "cron": "*/15 9-17 * * 1-5",
                                    "when": "every quarter of an hour in working hours"}), fake.say("OK.")],
      ["Schedule it", "n"])
    assert "I can schedule 'teapot-makers' to run every quarter of an hour in working hours." in s.output
    assert "*/15" not in s.output
