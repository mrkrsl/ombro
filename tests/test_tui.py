"""The terminal interface: its rules on their own, then the app driven headless with
the fake model and a temporary home. All data is invented; no network."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from ombro import fake, keys, mdfile
from ombro.fake import ScriptedClient
from ombro.tui import look
from ombro.tui.detect import is_dark, parse_background

# -- the team chart --------------------------------------------------------------------


def test_the_fresh_install_chart():
    assert look.team_chart("Lead", [("Default Crew", ["General Assistant"])]) == [
        "Lead",
        "└ Default Crew",
        "  └ General Assistant",
    ]


def test_three_crews_all_shown_with_consistent_glyphs():
    chart = look.team_chart(
        "Lead",
        [
            ("Legal Crew", ["Legal Researcher", "Legal Assistant"]),
            ("Marketing Crew", ["Market Researcher", "Marketing Associate", "Fact Checker"]),
            ("Ops Crew", ["Finance Assistant"]),
        ],
    )
    assert chart == [
        "Lead",
        "└ Legal Crew",
        "│ └ Legal Researcher",
        "│ └ Legal Assistant",
        "└ Marketing Crew",
        "│ └ Market Researcher",
        "│ └ Marketing Associate",
        "│ └ Fact Checker",
        "└ Ops Crew",
        "  └ Finance Assistant",
    ]
    assert len(chart) == 10  # exactly ten still fits


def test_over_ten_lines_collapses_every_crew():
    crews = [(f"Crew {i}", [f"R{j}" for j in range(n)]) for i, n in enumerate([2, 3, 1, 3, 2, 1, 2, 1, 1])]
    chart = look.team_chart("Lead", crews)
    assert chart == [
        "Lead",
        "└ Crew 0",
        "│ └ 2 roles",
        "└ Crew 1",
        "│ └ 3 roles",
        "└ Crew 2",
        "│ └ 1 role",
        "└ Crew 3",
        "│ └ 3 roles",
        "+ 5 additional crews",
    ]


def test_eleven_lines_with_few_crews_collapses_without_hiding_any():
    chart = look.team_chart("Lead", [("A", ["1", "2", "3", "4", "5"]), ("B", ["1", "2", "3", "4"]), ("C", [])])
    assert chart == ["Lead", "└ A", "│ └ 5 roles", "└ B", "│ └ 4 roles", "└ C", "  └ no roles yet"]


def test_five_crews_show_four_and_one_more():
    chart = look.team_chart("Lead", [(f"C{i}", ["a", "b"]) for i in range(5)])
    assert len(chart) == 10 and chart[-1] == "+ 1 additional crew"


# -- stats, greeting, footer, width tiers, logo ------------------------------------------------


SUMMARY = {
    "name": "Ada",
    "version": "0.1.0",
    "counts": {"crews_ready": 1, "active_jobs": 0, "scheduled_jobs": 2, "waiting": 1},
}


def test_stats_greeting_and_footer():
    assert look.stats_lines(SUMMARY) == [
        "Working with Ada",
        "",
        "1 crew ready to work",
        "0 active jobs",
        "2 scheduled jobs",
        "1 item to approve",
        "",
        "Ombro v0.1.0",
    ]
    assert look.stats_line(SUMMARY) == "1 crew ready · 0 active jobs · 2 scheduled jobs · 1 to approve"
    assert look.welcome("Ada", first=False) == "Hey Ada, welcome back to Ombro"
    assert look.welcome("", first=True) == "Hey there, welcome to Ombro"
    assert look.greeting("Ada", datetime(2026, 11, 12, 9)) == "Good morning Ada. What would you like to get done today?"
    assert look.greeting("", datetime(2026, 11, 12, 19)).startswith("Good evening. ")
    assert look.part_of_day(datetime(2026, 11, 12, 12)) == "afternoon"
    assert (
        look.since_line({"finished": 2, "waiting": 3})
        == "Since you were last here: 2 jobs finished, 3 actions waiting."
    )
    assert look.since_line({"finished": 0, "waiting": 0}) is None
    assert look.usage_text(1_500, 150_000) == "Usage: 1%" and look.usage_text(10, None) == "Usage: 10 tokens"


@pytest.mark.parametrize(
    "w, h, expected",
    [
        (120, 40, "wide"),
        (100, 30, "wide"),
        (99, 40, "medium"),
        (70, 40, "medium"),
        (69, 40, "narrow"),
        (120, 29, "narrow"),
    ],
)
def test_width_tiers(w, h, expected):
    assert look.tier(w, h) == expected


def test_the_logo_is_the_wordmark_with_its_shadow():
    for dark in (False, True):
        p = look.palette(dark)
        rows = look.logo_cells(dark, "Apple_Terminal")
        assert len(rows) == 7 and {len(r) for r in rows} == {47}
        assert look.logo_text(dark).no_wrap
        assert {fg for r in rows for _, fg, _ in r} == {p.logo_box, p.logo_shadow, None}
    # the b's stem stands on the letters below it
    assert look.LOGO_ART[0].index("██") == look.LOGO_ART[2].index("██████")


def test_the_logo_keeps_its_columns_on_screen(home):
    """Each row starts where it is drawn: no row is centred on its own."""
    from rich.console import Console

    from ombro.tui.app import HeaderBlock

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            hb = app.query_one(HeaderBlock)
            c = Console(width=hb.size.width, record=True, file=open("/dev/null", "w"))
            c.print(hb.render())
            lines = c.export_text().splitlines()
            rows = [next(ln for ln in lines if art.strip() in ln) for art in look.LOGO_ART]
            starts = {ln.index(art.strip()) - (len(art) - len(art.lstrip())) for ln, art in zip(rows, look.LOGO_ART)}
            assert len(starts) == 1, starts

    drive(go())


def test_the_shadow_is_hatched_in_ghostty_and_shaded_elsewhere():
    ghostty = {ch for r in look.logo_cells(True, "ghostty") for ch, _, _ in r}
    other = {ch for r in look.logo_cells(True, "Apple_Terminal") for ch, _, _ in r}
    assert look.HATCH in ghostty and "░" not in ghostty
    assert "░" in other and look.HATCH not in other


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

    assert [c for c, _ in filter_slash("/re")] == ["/rename", "/restart", "/resume", "/report", "/remember"]
    assert [c for c, _ in filter_slash("/")][0] == "/chat"
    assert filter_slash("/zzz") == []


# -- the app, headless -------------------------------------------------------------------------


def _app(home, script=None, *, dark=False):
    from ombro.tui.app import OmbroApp

    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED-0123456789")
    client = ScriptedClient(script or [])
    return OmbroApp(home, dark=dark, client_factory=lambda spec: client, poll=False, titles=False), client


def _texts(app):
    """Every line in the conversation, in order: plain lines as drawn, Markdown lines
    (the Lead's words, reports) as their mark and source."""
    from ombro.tui.app import MdLine

    return [w.mark + w.source if isinstance(w, MdLine) else str(w.render()) for w in app.query("Line, MdLine")]


def _screen(app) -> str:
    """The screen as plain text, from the SVG screenshot."""
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def drive(coro):
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
            assert "Ombro Lead" in shot and "└ Default Crew" in shot and "└ Researcher" in shot and "Balanced" in shot
            # No usage line under the input; the hint and the model are on its right.
            assert "Usage:" not in shot
            foot = next(line for line in shot.splitlines() if "/ for commands" in line)
            assert (
                "Model:" in foot
                and foot.index("/ for commands") > foot.index("Balanced")
                and foot.index("Model:") > foot.index("/ for commands")
            )

    drive(go())


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

    drive(go())


def test_a_growing_input_keeps_the_latest_line_in_view(home):
    """At the end of a long conversation, a long message grows the input; the conversation
    stays at its end, so its latest line is not hidden behind the input."""
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            for i in range(40):
                app.add_markdown(f"An invented line {i}.", "lead", "•")
            await pilot.pause(0.2)
            conv = app.query_one("#conversation")
            assert conv.max_scroll_y > 0 and conv.scroll_y >= conv.max_scroll_y - 1
            before = conv.max_scroll_y
            app.query_one("#input").text = "\n".join(f"An invented long message, line {i}" for i in range(6))
            await pilot.pause(0.2)
            assert conv.max_scroll_y > before  # the input grew, so the conversation's box shrank
            assert conv.scroll_y >= conv.max_scroll_y - 1

    drive(go())


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

    drive(go())


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

    drive(go())
    assert load_preferences(home).theme == "dark"


def test_the_slash_menu_filters_and_takes_a_command(home):
    from textual.widgets import OptionList

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("/", "t", "h")
            menu = app.query_one("#slash", OptionList)
            assert menu.display and menu.option_count == 1
            await pilot.press("tab")
            assert app.query_one("#input").text == "/theme "

    drive(go())


def test_ctrl_c_twice_leaves(home):
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+c")
            assert "again to leave" in _screen(app)
            await pilot.press("ctrl+c")
            await pilot.pause()

    drive(go())
    assert app.return_code == 0


async def answer(pilot, app, text: str) -> None:
    """Wait for the core to ask, then type the answer and press Enter."""
    for _ in range(200):
        if app.pending is not None:
            break
        await pilot.pause(0.02)
    assert app.pending is not None, "nothing was asked"
    await pilot.pause(0.02)  # the focus moves to the hidden field for a secret
    if text:
        await pilot.press(*text)
    await pilot.press("enter")
    await pilot.pause(0.05)


def test_a_fresh_home_is_set_up_inside_the_interface(home):
    from ombro.tui.app import OmbroApp, setup_needed

    assert setup_needed(home) == "the model's key (TEST_MODEL_KEY) is not set."
    client = ScriptedClient([fake.say("OK")])
    app = OmbroApp(home, dark=False, client_factory=lambda spec: client, poll=False)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await answer(pilot, app, "Ada")  # your name
            await answer(pilot, app, "1")  # DeepSeek
            assert app.pending.field.kind == "secret"  # the key comes before the model
            assert app.query_one("#secret").display and not app.query_one("#input").display
            await answer(pilot, app, "sk-test-INVENTED-0123456789")
            await answer(pilot, app, "")  # the endpoint lists no models here: the suggested name
            await answer(pilot, app, "n")  # no set-up interview now
            for _ in range(100):
                await pilot.pause(0.05)
                if not app.busy:
                    break
            texts = _texts(app)
            assert texts[0].startswith("• Welcome. I'm the Ombro Lead.")  # the first question is the welcome
            assert texts[2].startswith("• Thanks Ada. First I'll create")  # set-up speaks in the Lead's voice
            assert not any("INVENTED-0123456789" in t for t in texts)
            assert any("What would you like to get done today?" in t for t in texts)

    drive(go())
    assert keys.get_key(home, "DEEPSEEK_API_KEY") == "sk-test-INVENTED-0123456789"
    assert setup_needed(home) is None
    from ombro.settings import load_preferences

    assert load_preferences(home).name == "Ada"


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

    drive(go())


def test_narrow_and_dark_render(home):
    app, _ = _app(home, dark=True)

    async def go():
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            shot = _screen(app)
            assert "ready" in shot and "Working with" not in shot  # one line of stats

    drive(go())


# -- 10c to 10e: roles, briefs, runs, notices, the outbox and settings -------------------


async def settle(pilot, app, rounds: int = 100) -> None:
    for _ in range(rounds):
        await pilot.pause(0.03)
        if not app.busy and app.pending is None:
            return


async def command(pilot, app, text: str) -> None:
    await pilot.press(*text)
    await pilot.press("escape")  # close the slash menu, so Enter sends the line
    await pilot.press("enter")
    await pilot.pause(0.05)


def test_adding_a_role_is_the_same_interview(home):
    from ombro.roles import crew_roles
    from ombro.settings import load_limits

    app, _ = _app(
        home,
        [
            fake.call(
                "propose",
                {
                    "title": "Invented Writer",
                    "kind": "specialist",
                    "description": "Writes invented copy and returns a file.",
                    "tools": ["workspace.read", "workspace.write", "workspace.list"],
                    "soul": "You write short, clear copy.",
                },
            )
        ],
    )

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/role add Invented Writer")
            await answer(pilot, app, "a")
            await settle(pilot, app)
            assert any("is ready to take on jobs" in t for t in _texts(app))

    drive(go())
    assert crew_roles(home, "default", load_limits(home))["invented-writer"].title == "Invented Writer"


def test_a_confirmed_brief_offers_to_start_it(home, monkeypatch):
    spec = {
        "name": "invented-teapots",
        "title": "Find invented teapot makers",
        "crews": ["default"],
        "done_looks_like": "A list of three.",
        "users": "Me, to pick one.",
        "binding_constraint": "Lowtown only.",
        "waste": "Makers outside Lowtown.",
        "non_goals": "No prices.",
    }
    app, _ = _app(home, [fake.call("propose", spec)])
    started = []
    monkeypatch.setattr(
        app.commands, "start_job", lambda name, roles=None: started.append(name) or "20261112-000000-abcd"
    )
    monkeypatch.setattr("ombro.tui.app.jobs.find_job", lambda h, rid: home.root / "jobs" / rid)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/brief")
            await answer(pilot, app, "a")  # accept the spec
            await answer(pilot, app, "y")  # run it now
            await settle(pilot, app)
            assert app.mode == "Chat"

    drive(go())
    assert started == ["invented-teapots"]


def test_a_job_ending_posts_a_notice_and_marks_it_seen(home, job):
    from conftest import LEAD_REPORT, write_role
    from ombro import jobs as jobs_mod
    from ombro.journal import Follower

    write_role(home, "writer", tools=["workspace.write"])
    job(
        [
            fake.brief("writer", "Write an invented note."),
            fake.call("workspace__write", {"path": "workspace/note.md", "content": "Invented.\n"}),
            fake.result("Wrote it.", deliverables=[{"name": "The note", "path": "workspace/note.md"}]),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    job_id = job.job_dir.name
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.active_jobs = {job_id}  # as if it was running when the interface opened
            app.following[job_id] = Follower(job.job_dir / "journal.jsonl")
            app.poll_jobs()
            for _ in range(100):
                await pilot.pause(0.03)
                if any("Your job has finished." in t for t in _texts(app)):
                    break
            texts = _texts(app)
            from ombro.jobs import job_name

            assert any(f"{job_name(job_id)} · lead briefed" in t for t in texts)  # by its readable name
            notice = next(t for t in texts if "Your job has finished." in t)
            assert "Here's what it made:" in notice and "The note:" in notice and f"/report {job_id}" in notice

    drive(go())
    assert jobs_mod.read_job(job.job_dir)[0].get("seen_at")


def test_jobs_and_report_commands(home, job):
    from conftest import LEAD_REPORT

    job(
        [
            fake.brief("researcher", "Look into an invented thing."),
            fake.result("Done."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/jobs")
            await settle(pilot, app)
            assert any(job.job_dir.name in t and "completed" in t for t in _texts(app))
            await command(pilot, app, "/report")
            await settle(pilot, app)
            assert any("## Summary" in t and "Workspace:" in t for t in _texts(app))

    drive(go())
    assert job.meta.get("seen_at")


def test_declining_from_the_outbox(home, job):
    from conftest import LEAD_REPORT
    from ombro import outbox

    job(
        [
            fake.brief("researcher", "Do an invented thing."),
            fake.result("Done."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    a, _ = outbox.stage(
        job.job_dir,
        tool="mcp.fake.post_message",
        effect="external",
        arguments={"channel": "news", "text": "Invented."},
        reason="Invented.",
        role="researcher",
        input_schema={},
    )
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/approvals")
            await settle(pilot, app)
            assert any(a.ref in t and "waiting for your decision" in t for t in _texts(app))
            await command(pilot, app, f"/approvals decline {a.ref}")
            await answer(pilot, app, "y")
            await settle(pilot, app)

    drive(go())
    assert outbox.load(a.path).status == "declined"


def test_settings_commands(home):
    from ombro.home import crew_title

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/keys set INVENTED_KEY")
            assert app.pending.field.kind == "secret"
            await answer(pilot, app, "sk-test-INVENTED-9876543210")
            await settle(pilot, app)
            await command(pilot, app, "/crew add legal Legal Team")
            await settle(pilot, app)
            await command(pilot, app, "/keys")
            await settle(pilot, app)
            await command(pilot, app, "/preferences")
            await settle(pilot, app)
            await command(pilot, app, "/schedule")
            await settle(pilot, app)
            await command(pilot, app, "/folders")
            await settle(pilot, app)
            texts = "\n".join(_texts(app))
            assert "INVENTED_KEY: set" in texts and "9876543210" not in texts
            assert "theme: auto" in texts and "No schedules yet" in texts and "No folders are shared" in texts

    drive(go())
    assert keys.get_key(home, "INVENTED_KEY") == "sk-test-INVENTED-9876543210"
    assert crew_title(home, "legal") == "Legal Team"


def test_the_input_box_wraps_and_grows(home):
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            box = app.query_one("#input")
            assert box.size.height == 1
            await pilot.press(*("A long, invented message that goes on " * 6))
            await pilot.pause()
            assert 1 < box.size.height <= 6 and box.soft_wrap

    drive(go())


# -- fixes from the first real test (10f) -------------------------------------------


def test_model_commands_show_and_change_who_uses_which(home):
    from ombro import mdfile, models

    models.add_models(home, "https://models.invalid/v1", "TEST_MODEL_KEY", ["invented-chat", "invented-pro"])
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/models")
            await settle(pilot, app)
            texts = "\n".join(_texts(app))
            assert "invented-pro" in texts and "(the default)" in texts
            await command(pilot, app, "/model")
            await answer(pilot, app, "1")  # the Lead
            f = app.pending.field if app.pending else None
            for _ in range(100):
                if app.pending is not None and app.pending.field.name == "model":
                    f = app.pending.field
                    break
                await pilot.pause(0.02)
            key = next(o.key for o in f.options if o.label.startswith("invented-pro"))
            await answer(pilot, app, key)
            await settle(pilot, app)

    drive(go())
    assert mdfile.read(home.lead_dir / "config.md")[0]["model"] == "invented-pro"


def test_a_yes_no_question_never_takes_a_command_as_no(home):
    from ombro.tui.app import Pending
    from ombro.ui import Field

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pending = Pending(Field("add_role", "Add another role?", "yesno"))
            app.begin_ask(pending)
            await pilot.press(*"/model")
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert app.pending is pending and not pending.done.is_set()
            assert "commands wait" in "\n".join(_texts(app))
            await pilot.press("y", "enter")
            await pilot.pause(0.05)
            assert pending.done.is_set() and pending.value is True

    drive(go())


def test_shift_return_starts_a_new_line(home):
    from ombro.tui.app import PromptArea

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"one")
            await pilot.press("shift+enter")
            await pilot.press(*"two")
            assert app.query_one("#input", PromptArea).text == "one\ntwo"

    drive(go())


def test_new_clears_the_screen_and_the_usage(home):
    app, _ = _app(home, [fake.say("Hello there.")])

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "Hi")
            await settle(pilot, app)
            assert app.usage[0] > 0
            await command(pilot, app, "/new")
            await settle(pilot, app)
            texts = "\n".join(_texts(app))
            # the chat had a title from its first words from the moment it was sent
            assert "Hello there." not in texts and "I've saved the last chat, 'Hi'. This is a new chat." in texts
            assert app.usage[0] == 0

    drive(go())


def test_switching_theme_redraws_lines_already_shown(home):
    from ombro.tui import look

    from ombro.ui import Failure

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.add_line(Failure("A legible reply."), "error")  # drawn in the light theme's colours
            await pilot.pause()
            app.set_dark(True)
            await pilot.pause()
            from ombro.tui.app import Line

            reply = next(w for w in app.query(Line) if "A legible reply." in w.text.plain)
            colours = {str(s.style).lower() for s in reply.text.spans} | {str(reply.text.style).lower()}
            assert any(look.DARK.text.lower() in c for c in colours)
            assert not any(look.LIGHT.text.lower() in c for c in colours)

    drive(go())


def test_a_thinking_glyph_turns_while_the_core_works(home):
    import threading

    app, _ = _app(home)
    release = threading.Event()

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert not app.query_one("#thinking").display
            app.start_work(lambda: release.wait(5))
            await pilot.pause(0.4)
            shown = app.query_one("#thinking")
            assert shown.display and "Thinking" in str(shown.render())
            release.set()
            await settle(pilot, app)
            await pilot.pause(0.3)
            assert not app.query_one("#thinking").display

    drive(go())


def test_limits_shows_and_sets_the_one_level(home):
    from ombro.settings import load_limits

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/limits")
            await settle(pilot, app)
            texts = "\n".join(_texts(app))
            assert "generous: jobs 2,000,000" in texts
            await command(pilot, app, "/limits set")
            await answer(pilot, app, "3")  # generous
            await settle(pilot, app)
            assert "now uses the generous level" in "\n".join(_texts(app))

    drive(go())
    assert load_limits(home).allowance == "generous" and load_limits(home).max_tokens == 2_000_000


def test_slash_start_offers_a_saved_spec_again(home, monkeypatch):
    from ombro import mdfile

    (home.specs_dir).mkdir(parents=True, exist_ok=True)
    mdfile.write(
        home.specs_dir / "old-task.md",
        {"name": "old-task", "crews": ["default"], "roles": ["general-assistant"]},
        "# An invented old task\n",
    )
    mdfile.write(home.specs_dir / "new-task.md", {"name": "new-task", "crews": ["default"]}, "# An invented task\n")
    app, _ = _app(home)
    started = []
    monkeypatch.setattr(app.commands, "start_job", lambda name, roles=None: started.append(name) or None)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/start old-task")
            await answer(pilot, app, "y")
            await settle(pilot, app)
            texts = "\n".join(_texts(app))
            assert "Saved spec old-task: An invented old task" in texts
            assert "the work goes to: general-assistant" in texts
            await command(pilot, app, "/start")
            await answer(pilot, app, "1")
            await answer(pilot, app, "n")
            await settle(pilot, app)

    drive(go())
    assert started == ["old-task"]


def test_the_leads_words_and_reports_render_as_markdown(home, job):
    from conftest import write_role
    from ombro.tui.app import MdLine

    write_role(home, "researcher")
    job(
        [
            fake.brief("researcher", "Do it."),
            fake.result("Done."),
            fake.finish(),
            fake.keep_notes(),
            fake.say(
                "## Summary\nAll **done**.\n\n## What was done\nIt.\n\n## What was not done and why\nNothing."
                "\n\n## Decisions for you\nNone."
            ),
        ]
    )
    app, _ = _app(home, [fake.say("Here is **bold** and a list:\n\n- one\n- two")])

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "Hi")
            await settle(pilot, app)
            lead = [w for w in app.query(MdLine) if "bold" in w.source][0]
            await pilot.pause()
            shown = " ".join(str(b.render()) for b in lead.query("MarkdownBlock"))
            assert "**" not in shown and "bold" in shown  # rendered, not raw asterisks
            await command(pilot, app, f"/report {job.job_dir.name}")
            await settle(pilot, app)
            report = [w for w in app.query(MdLine) if "## Summary" in w.source][-1].source
            assert "Open the full report" in report and "# Ombro's record" not in report
            assert f"ombro:report/{job.job_dir.name}/full" in report
            await command(pilot, app, f"/report {job.job_dir.name} full")
            await settle(pilot, app)
            assert "# Ombro's record" in [w for w in app.query(MdLine) if "## Summary" in w.source][-1].source

    drive(go())


def test_tools_changes_a_built_in_tool(home):
    from ombro.settings import load_tool_settings

    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/tools web.search")
            await answer(pilot, app, "2")  # DuckDuckGo
            await answer(pilot, app, "25")
            await settle(pilot, app)
            assert "Saved. web.search now:" in "\n".join(_texts(app))

    drive(go())
    assert load_tool_settings(home).provider_for("web.search") == "duckduckgo"


def test_skills_lists_the_library_and_gives_a_skill(home):
    folder = home.skills_dir / "kettle-survey"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: kettle-survey\ndescription: Surveys invented kettle makers.\n---\n1. Search.\n"
    )
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, "/skills")
            await settle(pilot, app)
            shown = "\n".join(_texts(app))
            assert "Surveys invented kettle makers." in shown
            assert "Approved (1)" in shown and "Waiting for your approval (0)" in shown
            await command(pilot, app, "/skills give kettle-survey default/researcher")
            await settle(pilot, app)
            assert "Gave kettle-survey to default/researcher." in "\n".join(_texts(app))

    drive(go())
    assert mdfile.read(home.roles_dir("default") / "researcher" / "config.md")[0]["skills"] == ["kettle-survey"]


def test_skills_add_imports_after_yes(home, tmp_path):
    src = tmp_path / "Downloads" / "kettle-survey"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: kettle-survey\ndescription: Surveys invented kettle makers.\n---\n1. Search.\n"
    )
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await command(pilot, app, f"/skills add {src}")
            await answer(pilot, app, "y")
            await settle(pilot, app)
            assert "Added kettle-survey." in "\n".join(_texts(app))

    drive(go())
    assert (home.skills_dir / "kettle-survey" / "SKILL.md").is_file()


def test_clicking_read_the_report_opens_it_at_its_top(home, job):
    from conftest import write_role
    from ombro.tui.app import MdLine
    from ombro.tui.commands import notice_text

    write_role(home, "researcher")
    long = (
        "## Summary\n"
        + "\n\n".join(f"Paragraph {i} of an invented summary." for i in range(40))
        + "\n\n## What was done\nIt.\n\n## What was not done and why\nNothing.\n\n## Decisions for you\nNone."
    )
    job([fake.brief("researcher", "Do it."), fake.result("Done."), fake.finish(), fake.keep_notes(), fake.say(long)])
    app, _ = _app(home)

    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            text = notice_text(app, job.job_dir.name)
            action = next(
                s.style.meta["@click"]
                for s in text.spans
                if not isinstance(s.style, str) and s.style.meta.get("@click")
            )
            assert action == f"app.run_command('/report {job.job_dir.name}')"
            app.add_text(text, "notice")
            await pilot.pause()
            app.action_run_command(f"/report {job.job_dir.name}")
            await settle(pilot, app)
            await pilot.pause(0.3)
            conv = app.query_one("#conversation")
            report = [w for w in app.query(MdLine) if w.has_class("report")][-1]
            # its title is on screen, at the top of the conversation
            assert conv.region.y <= report.region.y < conv.region.y + 3

    drive(go())


def test_a_new_message_sets_the_open_question_aside(home):
    """Mark writes something else while report back or live is asked: the Lead gets it."""
    offer = fake.call("offer_work", {"summary": "Invented work", "title": "Invented", "task": "Do invented work."})
    app, client = _app(home, [offer, fake.say("An invented weather report.")])

    async def go():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press(*"Do some invented work.")
            await pilot.press("enter")
            await answer(pilot, app, "Is it sunny")
            await settle(pilot, app)
            texts = _texts(app)
            assert any("set aside" in t for t in texts)
            assert any(t.startswith("• An invented weather report.") for t in texts)
            assert client.calls[-1]["messages"][-1]["content"].endswith("Is it sunny")

    drive(go())
