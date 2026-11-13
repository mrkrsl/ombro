"""Running a saved spec again, changing one, and giving the work to named roles.
All invented; fake model; no network."""

from __future__ import annotations

from conftest import LEAD_REPORT, write_role
from ombro import fake, mdfile
from test_chat import ChatSession, SPEC_ARGS

SPEC = {"name": "teapot-makers", "crews": ["default"]}
BODY = "# Find invented teapot makers\n\nDone looks like: a list of three.\n"


def saved(home, name="teapot-makers", meta=None, body=BODY):
    path = home.specs_dir / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    mdfile.write(path, {**SPEC, "name": name, **(meta or {})}, body)
    return path


# -- roles: in a spec -----------------------------------------------------------------


def test_the_spec_gives_the_work_to_its_roles_and_the_report_says_who_else(run, home):
    write_role(home, "researcher")
    write_role(home, "checker")
    run([fake.brief("researcher", "Research."), fake.result("Found three."),
         fake.brief("checker", "Check them."), fake.result("Checked."),
         fake.finish(), fake.keep_notes(), LEAD_REPORT], spec_meta={"roles": ["researcher"]})
    lead = run.client.calls[0]["messages"][0]["content"]
    assert "=== Who the spec gives the work to ===" in lead and "The user chose: researcher" in lead
    assert "The spec gave the work to researcher." in run.report
    assert "Also briefed, outside the spec's roles: checker." in run.report


def test_a_role_the_crews_lack_is_a_warning_not_a_failure(run, home):
    write_role(home, "researcher")
    status = run([fake.finish(), fake.keep_notes(), LEAD_REPORT], spec_meta={"roles": ["marketing-researcher"]})
    assert status == "completed"
    assert any(e["action"] == "warning" and "marketing-researcher" in e["note"] for e in run.events)


def test_the_brief_interview_records_roles_and_refuses_unknown_ones(home):
    import pytest

    from ombro.interview import ProposalError
    from ombro.interviews import BriefPurpose
    from ombro.settings import load_limits

    write_role(home, "researcher")
    purpose = BriefPurpose(home, "default", load_limits(home))
    proposal = purpose.build({**SPEC_ARGS, "roles": ["researcher"]})
    assert mdfile.parse(proposal.files[0].text)[0]["roles"] == ["researcher"]
    with pytest.raises(ProposalError, match="no role named 'publisher'"):
        purpose.build({**SPEC_ARGS, "roles": ["publisher"]})


# -- running a saved spec again from chat -------------------------------------------------


def test_the_lead_offers_to_run_a_saved_spec_and_nothing_starts_without_yes(home, monkeypatch):
    saved(home)
    started = []
    monkeypatch.setattr("ombro.cli.main", lambda argv: started.append(argv) or 0)
    s = ChatSession(home)
    s([fake.call("offer_run", {"spec": "teapot-makers"}), fake.say("I've offered it.")],
      ["Restart the teapot task", "n"])
    assert started == []
    assert any("Run the saved spec 'teapot-makers' now?" in a for a in s.answers.asked)
    assert "Not started." in s.output
    s = ChatSession(home)
    s([fake.call("offer_run", {"spec": "teapot-makers"}), fake.say("I've offered it.")],
      ["Restart the teapot task", "y"])
    assert started == [["run", "--spec", "teapot-makers", "--detach"]]


def test_an_unknown_spec_is_refused_with_the_saved_ones(home):
    saved(home)
    s = ChatSession(home)
    s([fake.call("offer_run", {"spec": "kettles"}), fake.say("No such spec.")], ["Run kettles"])
    (reply,) = s.tool_replies()
    assert "no saved spec called 'kettles'" in reply["error"] and "teapot-makers" in reply["error"]


# -- changing a saved spec ----------------------------------------------------------------


def test_revising_a_saved_spec_starts_from_its_text_and_keeps_the_old_one(home):
    path = saved(home, body=BODY)
    write_role(home, "researcher")

    def interview(messages, tools):
        opening = messages[1]["content"]
        assert "change their saved spec 'teapot-makers'" in opening
        assert "Done looks like: a list of three." in opening  # the saved text, in full
        assert "count them all as covered" in opening
        return fake.call("propose", {**SPEC_ARGS, "name": "teapot-makers", "roles": ["researcher"]})

    s = ChatSession(home)
    s([fake.call("offer_brief", {"summary": "The researcher should do it", "revise": "teapot-makers"}),
       interview, fake.say("Changed.")],
      ["Same task, but the researcher should do it", "y", "a", "Thanks"])
    assert "offers to change your saved spec 'teapot-makers'" in s.output
    assert mdfile.read(path)[0]["roles"] == ["researcher"]
    assert (home.specs_dir / "teapot-makers.previous.md").read_text().count("a list of three") == 1
