"""The terminal interface: its rules on their own, then the app driven headless with
the fake model and a temporary home. All data is invented; no network."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from ombro import fake, keys
from ombro.fake import ScriptedClient
from ombro.tui import look
from ombro.tui.detect import is_dark, parse_background

# -- the team chart (specification 14.1) -----------------------------------------------


def test_the_fresh_install_chart():
    assert look.team_chart("Lead", [("Default Crew", ["General Assistant"])]) == [
        "Lead", "└ Default Crew", "  └ General Assistant"]


def test_three_crews_all_shown_with_consistent_glyphs():
    chart = look.team_chart("Lead", [("Legal Crew", ["Legal Researcher", "Legal Assistant"]),
                                     ("Marketing Crew", ["Market Researcher", "Marketing Associate", "Fact Checker"]),
                                     ("Ops Crew", ["Finance Assistant"])])
    assert chart == ["Lead", "└ Legal Crew", "│ └ Legal Researcher", "│ └ Legal Assistant", "└ Marketing Crew",
                     "│ └ Market Researcher", "│ └ Marketing Associate", "│ └ Fact Checker", "└ Ops Crew",
                     "  └ Finance Assistant"]
    assert len(chart) == 10  # exactly ten still fits


def test_over_ten_lines_collapses_every_crew():
    crews = [(f"Crew {i}", [f"R{j}" for j in range(n)]) for i, n in enumerate([2, 3, 1, 3, 2, 1, 2, 1, 1])]
    chart = look.team_chart("Lead", crews)
    assert chart == ["Lead", "└ Crew 0", "│ └ 2 roles", "└ Crew 1", "│ └ 3 roles", "└ Crew 2", "│ └ 1 role",
                     "└ Crew 3", "│ └ 3 roles", "+ 5 additional crews"]


def test_eleven_lines_with_few_crews_collapses_without_hiding_any():
    chart = look.team_chart("Lead", [("A", ["1", "2", "3", "4", "5"]), ("B", ["1", "2", "3", "4"]), ("C", [])])
    assert chart == ["Lead", "└ A", "│ └ 5 roles", "└ B", "│ └ 4 roles", "└ C", "  └ no roles yet"]


def test_five_crews_show_four_and_one_more():
    chart = look.team_chart("Lead", [(f"C{i}", ["a", "b"]) for i in range(5)])
    assert len(chart) == 10 and chart[-1] == "+ 1 additional crew"


# -- stats, greeting, footer, width tiers, logo ------------------------------------------------


SUMMARY = {"name": "Ada", "version": "0.1.0", "counts": {"crews_ready": 1, "active_runs": 0, "scheduled_runs": 2,
                                                        "waiting": 1}}


def test_stats_greeting_and_footer():
    assert look.stats_lines(SUMMARY) == ["Working with Ada", "", "1 crew ready to work", "0 active runs",
                                         "2 scheduled runs", "1 item to approve", "", "Ombro v0.1.0"]
    assert look.stats_line(SUMMARY) == "1 crew ready · 0 active runs · 2 scheduled runs · 1 to approve"
    assert look.welcome("Ada", first=False) == "Hey Ada, welcome back to Ombro"
    assert look.welcome("", first=True) == "Hey there, welcome to Ombro"
    assert look.greeting("Ada", datetime(2026, 11, 12, 9)) == \
        "Good morning Ada. What would you like to get done today?"
    assert look.greeting("", datetime(2026, 11, 12, 19)).startswith("Good evening. ")
    assert look.part_of_day(datetime(2026, 11, 12, 12)) == "afternoon"
    assert look.since_line({"finished": 2, "waiting": 3}) == "Since you were last here: 2 runs finished, 3 actions waiting."
    assert look.since_line({"finished": 0, "waiting": 0}) is None
    assert look.usage_text(1_500, 150_000) == "Usage: 1%" and look.usage_text(10, None) == "Usage: 10 tokens"


@pytest.mark.parametrize("w, h, expected", [(120, 40, "wide"), (100, 30, "wide"), (99, 40, "medium"),
                                            (70, 40, "medium"), (69, 40, "narrow"), (120, 29, "narrow")])
def test_width_tiers(w, h, expected):
    assert look.tier(w, h) == expected


def test_the_logo_is_the_wordmark_with_its_shadow():
    for dark in (False, True):
        p = look.palette(dark)
        rows = look.logo_cells(dark)
        assert len(rows) == 7 and {len(r) for r in rows} == {47}
        assert look.logo_text(dark).no_wrap
        assert {fg for r in rows for _, fg, _ in r} == {p.logo_box, p.logo_shadow, None}
    # the b's stem stands on the letters below it
    assert look.LOGO_ART[0].index("██") == look.LOGO_ART[2].index("██████")


def test_the_chart_says_ombro_lead():
    assert look.lead_label("Lead") == "Ombro Lead"
    assert look.lead_label("Chief of Staff") == "Chief of Staff"


def test_theme_detection():
    assert parse_background(b"\x1b]11;rgb:0303/1111/0b0b\x1b\\") is True
    assert parse_background(b"\x1b]11;rgb:ffff/ffff/ffff\x07") is False
    assert parse_background(b"nothing") is None
    assert is_dark("dark") is True and is_dark("light") is False


def test_slash_filtering():
    from ombro.tui.app import filter_slash

    assert [c for c, _ in filter_slash("/re")] == ["/report", "/remember"]
    assert [c for c, _ in filter_slash("/")][0] == "/chat"
    assert filter_slash("/zzz") == []


# -- the app, headless -------------------------------------------------------------------------


def _app(home, script=None, *, dark=False):
    from ombro.tui.app import OmbroApp

    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED-0123456789")
    client = ScriptedClient(script or [])
    return OmbroApp(home, dark=dark, client_factory=lambda spec: client), client


def _texts(app):
    from ombro.tui.app import Line

    return [str(w.render()) for w in app.query(Line)]


def _screen(app) -> str:
    """The screen as plain text, from the SVG screenshot."""
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def run(coro):
    return asyncio.run(coro)


def test_opens_with_the_header_and_the_greeting(home):
    from ombro.settings import set_preference

    set_preference(home, "name", "Ada")
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            texts = _texts(app)
            assert any("What would you like to get done today?" in t for t in texts)
            shot = _screen(app)
            assert "Hey Ada, welcome to Ombro" in shot and "Working with Ada" in shot
            assert "Ombro Lead" in shot and "└ Default Crew" in shot and "└ Researcher" in shot and "Usage: 0%" in shot and "Balanced" in shot

    run(go())


def test_a_chat_message_gets_the_leads_answer(home):
    app, client = _app(home, [fake.say("An invented answer.")])

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"Hello there")
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if any("An invented answer." in t for t in _texts(app)):
                    break
            texts = _texts(app)
            assert "Hello there" in texts and any(t.startswith("• An invented answer.") for t in texts)
            assert app.usage[0] > 0
            assert client.calls

    run(go())


def test_brief_mode_shows_in_the_footer_and_chat_leaves_it(home):
    app, _ = _app(home, [fake.call("ask", {"questions": ["What is the task?"]})])

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"/brief")
            await pilot.press("escape")
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if app.pending is not None:
                    break
            assert app.mode == "Brief" and app.pending is not None
            assert "Complete the brief interview" in app.query_one("#input").placeholder
            await pilot.press(*"/chat")
            await pilot.press("escape")
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if app.mode == "Chat" and not app.busy:
                    break
            assert app.mode == "Chat" and not app.busy

    run(go())


def test_theme_switches_and_is_saved(home):
    from ombro.settings import load_preferences

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"/theme")
            await pilot.press("escape")
            await pilot.press("enter")
            await pilot.pause()
            assert app.dark is True

    run(go())
    assert load_preferences(home).theme == "dark"


def test_the_slash_menu_filters_and_takes_a_command(home):
    from textual.widgets import Input, OptionList

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("/", "t", "h")
            menu = app.query_one("#slash", OptionList)
            assert menu.display and menu.option_count == 1
            await pilot.press("tab")
            assert app.query_one("#input", Input).value == "/theme "

    run(go())


def test_ctrl_c_twice_leaves(home):
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+c")
            assert "again to leave" in _screen(app)
            await pilot.press("ctrl+c")
            await pilot.pause()

    run(go())
    assert app.return_code == 0


def test_without_a_key_it_says_set_up_is_needed(home):
    from ombro.tui.app import OmbroApp, setup_needed

    assert setup_needed(home) == "the model's key (TEST_MODEL_KEY) is not set."
    app = OmbroApp(home, dark=False)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert any("ombro init" in t for t in _texts(app))

    run(go())


@pytest.mark.parametrize("width, expect", [(130, True), (100, False)])
def test_the_logo_shows_only_where_it_fits_whole(home, width, expect):
    app, _ = _app(home)

    async def go():
        if True:
            async with app.run_test(size=(width, 40)) as pilot:
                await pilot.pause()
                shot = _screen(app)
                assert ("██░░░░██" in shot) is expect, width
                assert "Ombro Lead" in shot

    run(go())


def test_narrow_and_dark_render(home):
    app, _ = _app(home, dark=True)

    async def go():
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            shot = _screen(app)
            assert "ready" in shot and "Working with" not in shot  # one line of stats

    run(go())
