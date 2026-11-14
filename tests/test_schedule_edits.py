"""Schedules in words, a schedule's timing changed in place (C7), and a schedule removed to
removed/ rather than erased (C4). All data is invented."""

from __future__ import annotations

import pytest

from ombro import cron, mdfile, schedules


@pytest.mark.parametrize("text, words", [
    ("0 6 * * 1-5", "Runs weekdays at 6am"),
    ("0 18 * * 4", "Runs Thursdays at 6pm"),
    ("30 7 * * *", "Runs every day at 7:30am"),
    ("0 12 * * 0,6", "Runs weekends at noon"),
    ("0 9 * * 1,3", "Runs Mondays and Wednesdays at 9am"),
    ("0 0 1 * *", "Runs on the 1st of each month at midnight"),
    ("*/15 * * * *", "Runs at */15 * * * *"),
])
def test_when_a_schedule_runs_in_words(text, words):
    assert cron.describe(cron.parse(text)) == words


def _schedule(home):
    mdfile.write(home.specs_dir / "invented-news.md", {"name": "invented-news"}, "# Invented news\n\nCheck it.\n")
    path = schedules.write_schedule(home, "news-briefing", "0 6 * * 1-5", "invented-news", True)
    meta, body = mdfile.read(path)
    mdfile.write(path, {**meta, "grace": "2h"}, body + "\nA note kept by hand.\n")
    return path


def test_timing_changes_in_place_and_keeps_the_rest(home):
    path = _schedule(home)
    schedules.set_timing(path, "0 18 * * 4")
    meta, body = mdfile.read(path)
    assert meta["cron"] == "0 18 * * 4" and meta["grace"] == "2h" and "A note kept by hand." in body
    with pytest.raises(cron.CronError):
        schedules.set_timing(path, "61 * * * *")
    assert mdfile.read(path)[0]["cron"] == "0 18 * * 4"  # a bad one changes nothing


def test_removing_moves_it_aside(home):
    path = _schedule(home)
    moved = schedules.remove_schedule(home, schedules.find_schedule(home, "news-briefing"))
    assert not path.exists() and moved.parent == home.removed_dir / "schedules" and moved.is_file()
    _schedule(home)
    again = schedules.remove_schedule(home, schedules.find_schedule(home, "news-briefing"))
    assert again != moved and again.is_file()  # the first one is kept too


def test_schedule_list_says_when_and_what(home):
    _schedule(home)
    from ombro import views

    [row] = views.schedule_list(home)
    assert row["when"] == "Runs weekdays at 6am" and row["spec_title"] == "Invented news"
