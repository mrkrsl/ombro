"""Carrying on with a job that didn't finish: restart after its token limit, resume otherwise, on the
same brief and building on what was done; and the Lead's offer of it in chat. All invented; fake model."""

from __future__ import annotations

import pytest

from conftest import LEAD_REPORT, write_role
from ombro import fake, jobs, mdfile
from ombro.session import StartError, again_offer, again_plan, job_ended, start_again
from test_chat import ChatSession


@pytest.fixture
def detached(monkeypatch):
    """Jobs that would start in the background are only noted."""
    started: list[str] = []
    monkeypatch.setattr("ombro.jobs.start_detached", lambda home, job_dir: started.append(job_dir.name) or 1)
    monkeypatch.setattr("ombro.init_cmd.connected_model", lambda home: "test")  # the scripted model stands in
    return started


def stopped_job(job, home, *, status="stopped_by_limit", limit="max_tokens"):
    """A job that wrote a file, then is recorded as having stopped the given way."""
    write_role(home, "researcher")
    job(
        [
            fake.brief("researcher", "Find three invented teapot makers."),
            fake.result("Found one so far.", deliverables=[{"name": "Makers", "path": "workspace/makers.md"}]),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        limits={"max_tokens": 40_000},
    )
    (job.job_dir / "workspace" / "makers.md").write_text("# Makers\n\n1. Ada Pots\n")  # what it got done
    jobs.update_job(job.job_dir, status=status, limit_hit=limit)
    return job.job_dir


# -- what is offered ------------------------------------------------------------------------


def test_a_job_that_ran_out_of_tokens_is_offered_a_restart_with_twice_the_tokens():
    plan = again_plan({"status": "stopped_by_limit", "limit_hit": "max_tokens", "limits": {"max_tokens": 2_000_000}})
    assert plan["kind"] == "restart" and plan["raise"]["max_tokens"] == 4_000_000
    text, offers = again_offer("teapots-20261117-1000", plan)
    assert text.startswith("It hit its token limit (2M) before it finished. I can restart it on the same brief")
    assert "Settings › General" in text
    assert [(o.label, o.command) for o in offers] == [
        ("Restart with 4M tokens", "/restart teapots-20261117-1000 more"),
        ("Restart as it was", "/restart teapots-20261117-1000"),
    ]


def test_a_job_that_ran_out_of_time_or_was_stopped_or_failed_is_offered_a_resume():
    timeout = again_plan({"status": "stopped_by_limit", "limit_hit": "job_timeout", "limits": {"job_timeout": 7200}})
    assert timeout["kind"] == "resume" and timeout["raise"]["job_timeout"] == 14_400
    text, offers = again_offer("t", timeout)
    assert text == (
        "It ran out of time (2 hours) before it finished. I can resume it on the same brief, picking up "
        "where it stopped."
    )
    assert [o.command for o in offers] == ["/resume t", "/resume t more"]
    stopped = again_plan({"status": "stopped", "limit_hit": None, "limits": {}})
    assert stopped["kind"] == "resume" and stopped["why"] == "It was stopped before it finished."
    assert [o.label for o in again_offer("t", stopped)[1]] == ["Resume it"]
    assert again_plan({"status": "failed", "limits": {}})["why"] == "Something went wrong before it finished."
    for done in ("completed", "insufficient_brief", "running"):
        assert again_plan({"status": done}) is None


def test_the_end_notice_carries_the_offer_and_the_summary(job, home):
    job_dir = stopped_job(job, home)
    e = job_ended(home, job_dir.name)
    assert e.again == "restart" and e.again_text.startswith("It hit its token limit")
    assert [o.command for o in e.offers][0] == f"/restart {job_dir.name} more"
    assert e.summary  # the Lead's paragraph from the report
    jobs.update_job(job_dir, status="completed", limit_hit=None)
    assert job_ended(home, job_dir.name).offers == []


# -- carrying it on -------------------------------------------------------------------------


def test_a_restart_follows_the_same_brief_and_builds_on_what_was_done(job, home, detached):
    from ombro.engine import Engine
    from ombro.fake import ScriptedClient

    earlier = stopped_job(job, home)
    new_id = start_again(home, earlier.name, more=True)
    assert detached == [new_id]
    new = jobs.find_job(home, new_id)
    meta = jobs.read_job(new)[0]
    assert meta["again_of"] == earlier.name and meta["again"] == "restart"
    assert meta["limits"]["max_tokens"] == 80_000  # twice the earlier job's
    assert meta["source"] == f"restarted from job {earlier.name}, with 80k tokens"
    assert meta["outward"] == jobs.read_job(earlier)[0]["outward"]  # the same permissions
    body = mdfile.read(new / "spec.md")[1]
    assert body.startswith(mdfile.read(earlier / "spec.md")[1].rstrip())
    assert "## Carrying on from an earlier run" in body
    assert "don't redo work that is already done" in body and "workspace/makers.md" in body
    assert (new / "workspace" / "makers.md").read_text().startswith("# Makers")
    assert (new / "scratch" / "earlier-report.md").read_text() == (earlier / "report.md").read_text()
    # The new job's Lead is given it, and its report says where it came from.
    client = ScriptedClient([fake.finish("Finished the list."), fake.keep_notes(), LEAD_REPORT])
    Engine(home, new, client_factory=lambda spec: client).execute()
    assert "Carrying on from an earlier run" in str(client.calls[0]["messages"])
    narrative = [c for c in client.calls if "writing the parts of the final report" in str(c["messages"])][0]
    assert f"carried on from the earlier job {earlier.name}" in str(narrative["messages"])


def test_a_resume_keeps_the_limits_unless_asked_and_a_finished_job_is_refused(job, home, detached):
    earlier = stopped_job(job, home, status="stopped_by_limit", limit="job_timeout")
    before = jobs.read_job(earlier)[0]["limits"]
    new = jobs.find_job(home, start_again(home, earlier.name))
    assert jobs.read_job(new)[0]["limits"] == before and jobs.read_job(new)[0]["again"] == "resume"
    jobs.update_job(earlier, status="completed", limit_hit=None)
    with pytest.raises(StartError, match="finished, so there's nothing to carry on"):
        start_again(home, earlier.name)
    with pytest.raises(StartError, match="no job matching"):
        start_again(home, "no-such-job")


# -- in chat ---------------------------------------------------------------------------------


def test_restart_typed_in_chat_starts_it_again(job, home, detached):
    earlier = stopped_job(job, home)
    s = ChatSession(home)
    s([], [f"/restart {earlier.name} more"])
    assert len(detached) == 1
    new = jobs.find_job(home, detached[0])
    assert jobs.read_job(new)[0]["limits"]["max_tokens"] == 80_000
    assert "building on what the last run did" in s.output


def test_the_lead_offers_to_carry_on_a_job_in_its_own_voice(job, home, detached):
    earlier = stopped_job(job, home, status="stopped", limit=None)
    s = ChatSession(home)
    s([fake.call("offer_start", {"job": earlier.name}), fake.say("All right.")], ["Carry on the teapots", "n"])
    assert detached == []
    assert f"I found it: the job '{jobs.job_name(earlier.name)}'." in s.output
    assert "I can resume it on the same brief, picking up where it stopped." in s.output
    s = ChatSession(home)
    s([fake.call("offer_start", {"job": earlier.name}), fake.say("Resuming it.")], ["Carry on the teapots", "y"])
    assert len(detached) == 1 and jobs.read_job(jobs.find_job(home, detached[0]))[0]["again"] == "resume"


def test_a_job_that_ends_unfinished_tells_the_lead_of_the_offer(job, home):
    from ombro.chat import Chat

    earlier = stopped_job(job, home)
    chat = Chat(home, ask=lambda *a, **k: "", say=lambda *a: None)
    chat.jobs.append(earlier.name)
    e = job_ended(home, earlier.name)
    chat.job_ended(e.job_id, e.text, e.status, e.again_text, e.again)
    assert f"call offer_start with job '{earlier.name}'" in chat.pending_note
    assert any(x.startswith("**Lead:** It hit its token limit") for x in chat.transcript.entries)
