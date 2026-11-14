"""One Lead above every crew: runs across crews, each crew's memory, the General Assistant,
and a home from before there was one Lead. All data here is invented."""

from __future__ import annotations

import os
import shutil

import pytest

from conftest import LEAD_REPORT, SPEC, write_role
from ombro import cli, fake, mdfile, memory, prompts, jobs, schedules
from ombro.home import ASSISTANT_NAME, Home, ensure_crew, ensure_home
from ombro.interviews import BriefPurpose
from test_interviews import talk  # noqa: F401  (a fixture)
from ombro.native import native_registry
from ombro.roles import RoleError, crew_roles, load_lead, load_roles, resolve_role
from ombro.settings import SetupError, load_limits, load_tool_settings


@pytest.fixture
def crews(home):
    """Two crews of the same kind of work for different invented projects, and an ops crew."""
    for crew in ("bakery", "garden", "ops"):
        ensure_crew(home, crew, f"Invented {crew} work.")
    write_role(home, "writer", crew="bakery", description="Writes copy for the invented bakery.")
    write_role(home, "writer", crew="garden", description="Writes copy for the invented garden centre.")
    write_role(home, "bookkeeper", crew="ops", description="Keeps invented accounts.")
    return home


# -- the one Lead and the General Assistant --------------------------------------------------


def test_a_fresh_home_has_one_lead_and_a_general_assistant(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    limits = load_limits(h)
    lead = load_lead(h, limits)
    assert lead.kind == "lead" and lead.path == h.lead_dir
    roles = crew_roles(h, "default", limits)
    assert list(roles) == [ASSISTANT_NAME]
    ga = roles[ASSISTANT_NAME]
    assert ga.kind == "specialist" and ga.model == "default" and ga.max_steps == 15
    assert "code.run" in ga.tools and "workspace.place" in ga.tools
    # Every tool it lists exists.
    registry = native_registry(h, load_tool_settings(h))
    assert [t for t in ga.tools if registry.get(t) is None] == []


def test_the_general_assistant_is_never_overwritten_or_brought_back(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    soul = h.roles_dir("default") / ASSISTANT_NAME / "soul.md"
    soul.write_text("You are my own invented assistant.\n")
    assert ensure_home(h) == []
    assert soul.read_text() == "You are my own invented assistant.\n"
    shutil.rmtree(soul.parent)
    ensure_home(h)
    assert not soul.parent.exists()  # removed by the user, it stays removed


def test_a_crew_cannot_have_a_lead_of_its_own(home):
    (home.roles_dir("default") / "lead").mkdir()
    shutil.copy(home.lead_dir / "config.md", home.roles_dir("default") / "lead" / "config.md")
    shutil.copy(home.lead_dir / "soul.md", home.roles_dir("default") / "lead" / "soul.md")
    with pytest.raises(RoleError, match="a crew has no Lead of its own"):
        crew_roles(home, "default", load_limits(home))


def test_an_old_home_with_a_lead_in_a_crew_is_refused(home, capsys):
    (home.roles_dir("default") / "lead").mkdir()
    assert home.old_layout()
    assert cli.main(["jobs"]) == 2
    err = capsys.readouterr().err
    assert "made by an earlier Ombro" in err and "ombro init" in err


# -- roles across crews ------------------------------------------------------------------------


def test_roles_of_several_crews_are_named_crew_slash_role(crews):
    limits = load_limits(crews)
    roles = load_roles(crews, ["bakery", "garden", "ops"], limits)
    assert sorted(roles) == ["bakery/writer", "garden/writer", "ops/bookkeeper"]
    assert roles["garden/writer"].crew == "garden"
    # One crew: roles keep their own names.
    assert list(load_roles(crews, ["ops"], limits)) == ["bookkeeper"]


def test_a_bare_role_name_is_accepted_only_where_one_crew_has_it(crews):
    roles = load_roles(crews, ["bakery", "garden", "ops"], load_limits(crews))
    role, _ = resolve_role(roles, "bookkeeper")
    assert role.name == "ops/bookkeeper"
    role, why = resolve_role(roles, "writer")
    assert role is None and "bakery/writer" in why and "garden/writer" in why
    role, _ = resolve_role(roles, "garden/writer")
    assert role.crew == "garden"
    role, why = resolve_role(roles, "default/researcher")
    assert role is None and "no role named" in why


def test_spec_crews_default_and_checks(crews):
    assert jobs.spec_crews(crews, {}) == ["default"]
    assert jobs.spec_crews(crews, {}, ["ops"]) == ["ops"]
    assert jobs.spec_crews(crews, {"crews": ["bakery", "ops", "bakery"]}, ["garden"]) == ["bakery", "ops"]
    assert jobs.spec_crews(crews, {"crews": "ops"}) == ["ops"]
    with pytest.raises(SetupError, match="no crew named 'nowhere'"):
        jobs.spec_crews(crews, {"crews": ["nowhere"]})


# -- a job across crews ------------------------------------------------------------------------


def two_crew_job(job):
    return job(
        [
            fake.brief("bakery/writer", "Draft an invented bakery notice."),
            fake.result("Drafted the notice."),
            fake.brief("bookkeeper", "Total the invented receipts."),
            fake.result("Totalled them."),
            fake.finish(),
            fake.write_notes("- The bakery likes short notices."),
            fake.keep_notes("Nothing new for ops."),
            LEAD_REPORT,
        ],
        crews=["bakery", "ops"],
    )


def test_a_job_briefs_roles_in_the_crews_its_spec_names(job, crews):
    assert two_crew_job(job) == "completed"
    assert job.meta["crews"] == ["bakery", "ops"]
    assert job.job_dir.parent == crews.jobs_dir
    briefed = [e for e in job.events if e["action"] == "briefed"]
    assert [(e["target"], e["crew"]) for e in briefed] == [("bakery/writer", "bakery"), ("ops/bookkeeper", "ops")]
    # The Lead was shown only the named crews' roles, by crew/role.
    system = job.client.calls[0]["messages"][0]["content"]
    assert "bakery/writer" in system and "ops/bookkeeper" in system and "garden/writer" not in system
    report = job.report
    assert "**Crews:** bakery, ops" in report and "**bakery/writer**" in report


def test_each_briefed_crew_gets_its_own_notes_and_every_named_crew_an_index_entry(job, crews):
    two_crew_job(job)
    notes = [e for e in job.events if e["action"] == "notes"]
    assert [(e["crew"], e["outcome"]) for e in notes] == [("bakery", "changed"), ("ops", "kept")]
    assert "The bakery likes short notices." in memory.read_notes(crews, "bakery").from_jobs
    assert memory.read_notes(crews, "ops").from_jobs.strip() == ""
    # The notes call names its crew.
    notes_calls = [c for c in job.client.calls if c.get("tools") and
                   c["tools"][0]["function"]["name"] == "memory__write_notes"]
    assert "These are the notes of crew bakery" in notes_calls[0]["messages"][1]["content"]
    assert "These are the notes of crew ops" in notes_calls[1]["messages"][1]["content"]
    report = job.report
    assert "### Crew bakery" in report and "### Crew ops" in report
    for crew in ("bakery", "ops"):
        lines = memory.index_path(crews, crew).read_text()
        assert f" · {job.job_dir.name} · completed · " in lines
    assert job.job_dir.name not in memory.index_path(crews, "garden").read_text()


def test_a_crew_named_but_not_briefed_gets_no_notes_call(job, crews):
    status = job(
        [fake.brief("bakery/writer", "Draft it."), fake.result(), fake.finish(), fake.keep_notes(), LEAD_REPORT],
        crews=["bakery", "garden"],
    )
    assert status == "completed"
    assert [e["crew"] for e in job.events if e["action"] == "notes"] == ["bakery"]
    assert job.job_dir.name in memory.index_path(crews, "garden").read_text()


def test_an_ambiguous_brief_is_refused_with_the_choices(job, crews):
    status = job(
        [fake.brief("writer", "Draft it."), fake.brief("garden/writer", "Draft it."), fake.result(),
         fake.finish(), fake.keep_notes(), LEAD_REPORT],
        crews=["bakery", "garden"],
    )
    assert status == "completed"
    refusal = job.client.calls[1]["messages"][-1]["content"]
    assert "more than one crew" in refusal and "bakery/writer" in refusal
    assert [e["target"] for e in job.events if e["action"] == "briefed"] == ["garden/writer"]


def test_a_role_outside_the_named_crews_cannot_be_briefed(job, crews):
    status = job([fake.brief("ops/bookkeeper", "Total it."), fake.finish(), LEAD_REPORT], crews=["bakery"])
    assert status == "completed"
    assert "no role named 'ops/bookkeeper'" in job.client.calls[1]["messages"][-1]["content"]
    assert not any(e["action"] == "briefed" for e in job.events)


def test_the_lead_starts_with_the_memory_of_the_named_crews_only(crews):
    for crew, lesson in (("bakery", "Bakery lesson."), ("garden", "Garden lesson."), ("ops", "Ops lesson.")):
        memory.replace_from_jobs(crews, crew, f"- {lesson}", 10_000)
    notes, index = memory.lead_context(crews, ["bakery", "ops"], load_limits(crews))
    assert "### Crew bakery" in notes and "Bakery lesson." in notes and "Ops lesson." in notes
    assert "Garden lesson." not in notes
    assert index == "(no earlier jobs in these crews)"


def test_memory_search_is_held_to_the_spec_crews(job, crews):
    job([fake.brief("garden/writer", "Write about invented ferns."), fake.result("Wrote about ferns."),
         fake.finish("Ferns of Lowtown were covered."), fake.keep_notes(), LEAD_REPORT], crews=["garden"])
    found = memory.search(crews, ["garden"], "ferns")
    assert found["results"] and found["results"][0]["crew"] == "garden"
    assert memory.search(crews, ["bakery", "ops"], "ferns")["results"] == []


# -- briefs and schedules ----------------------------------------------------------------------

SPEC_ARGS = {
    "name": "notice",
    "title": "An invented notice",
    "done_looks_like": "workspace/notice.md",
    "users": "the user, once",
    "binding_constraint": "under 100 words",
    "waste": "anything about prices",
    "non_goals": "design",
}


def test_the_brief_proposes_crews_and_shows_them(talk, crews):
    out = talk(
        BriefPurpose(crews, "default", load_limits(crews)),
        [fake.call("propose", {**SPEC_ARGS, "crews": ["nowhere"]}),
         fake.call("propose", {**SPEC_ARGS, "crews": ["bakery", "ops"]})],
        ["a"],
    )
    assert "no crew named 'nowhere'" in talk.tool_replies()[0]
    assert out.status == "confirmed"
    meta, _ = mdfile.read(crews.specs_dir / "notice.md")
    assert meta["crews"] == ["bakery", "ops"]
    assert "the job may brief roles in the Bakery Crew and the Ops Crew" in talk.output
    # The Lead was shown every crew's roles, and told to ask between look-alike crews.
    system = talk.client.calls[0]["messages"][0]["content"]
    assert "bakery/writer" in system and "garden/writer" in system and "ask the user which one" in system


def test_a_brief_started_for_a_crew_uses_it_when_the_lead_names_none(talk, crews):
    out = talk(BriefPurpose(crews, "default", load_limits(crews), crews=["garden"]),
               [fake.call("propose", SPEC_ARGS)], ["a"])
    assert out.status == "confirmed"
    assert mdfile.read(crews.specs_dir / "notice.md")[0]["crews"] == ["garden"]


def test_a_schedule_notes_a_missed_slot_in_every_crew_its_spec_names(crews):
    from test_schedules import Starts, tick, ts

    from ombro import keys

    keys.set_key(crews, "TEST_MODEL_KEY", "invented-test-key")
    mdfile.write(crews.specs_dir / "weekly.md", {"crews": ["bakery", "ops"]}, SPEC)
    path = schedules.write_schedule(crews, "weekly", "30 7 * * *", "weekly", True)
    os.utime(path, (ts(2026, 1, 1, 0, 0),) * 2)
    [s] = schedules.all_schedules(crews)
    assert s.crews == ["bakery", "ops"] and schedules.all_schedules(crews, "garden") == []
    tick(crews, ts(2026, 11, 9, 23, 0))
    tick(crews, ts(2026, 11, 12, 9, 14))
    for crew in ("bakery", "ops"):
        assert "schedule:weekly@" in memory.index_path(crews, crew).read_text()
    assert "schedule:weekly@" not in memory.index_path(crews, "garden").read_text()
    starts = Starts()
    tick(crews, ts(2026, 11, 13, 7, 31), starter=starts)
    [job_dir] = starts.dirs
    assert jobs.read_job(job_dir)[0]["crews"] == ["bakery", "ops"]


def test_the_chat_lead_is_the_one_lead(home):
    from ombro.chat import Chat

    chat = Chat(home, ask=lambda p: "/quit", say=lambda t: None, client_factory=lambda spec: None)
    assert chat.lead.path == home.lead_dir
    assert "one orchestrator above all the user's Ombro crews" in prompts.CHAT_RULES
