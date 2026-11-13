"""Acceptance tests for the guardrails: each limit stops a run, and a report is always written."""

from __future__ import annotations

import pytest

from conftest import LEAD_REPORT, echo_registry, write_role
from ombro import fake


def assert_reported(run, status: str, limit: str | None = None, by_lead: bool = True):
    report = run.report
    assert run.meta["status"] == status
    assert f"**Status:** {status}" in report
    for section in ("## What was asked", "## What was done", "## What was not done and why",
                    "## Decisions for you", "## Deliverables", "## Outward actions", "## Changes to notes.md", "## Usage"):
        assert section in report, section
    if limit:
        assert run.meta["limit_hit"] == limit
        assert f"**Stopped by:** `{limit}`" in report
        assert f"Limit that stopped the run: `{limit}`" in report
    if by_lead:
        assert "LEAD-REPORT-MARKER" in report
        assert run.client.remaining == 0, "the Lead's report reply was not the last call"
    else:
        assert "written by the harness" in report
    assert run.actions()[-1] == "run_ended"


# -- the ordinary path --------------------------------------------------------


def test_completed_run(run):
    status = run(
        [
            fake.brief("researcher", "Find three invented teapot makers."),
            fake.result("Found three makers.", deliverables=[{"name": "makers", "path": "Ada Pots; Brin Ware; Coll Clay"}]),
            fake.finish("All done."),
            fake.keep_notes(), LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(run, "completed")
    assert "Found three makers." in run.report
    assert "Ada Pots" in run.report
    assert run.meta["usage"]["rounds"] == 1
    assert run.meta["usage"]["model_calls"] == 5  # Lead, role, Lead, notes, report
    # The report call had no tools.
    assert run.client.calls[-1]["tools"] is None


def test_report_keeps_closing_note_and_brief_lines_short(run):
    long_note = "Final text follows.\n\n" + ("An invented paragraph of guide text. " * 60)
    run(
        [
            fake.brief("researcher", "Line one of the task.\n\nLine two of the task."),
            fake.result("Summary over\ntwo lines."),
            fake.finish(long_note),
            LEAD_REPORT,
        ]
    )
    note_line = next(l for l in run.report.splitlines() if l.startswith("- **Lead's closing note"))
    assert len(note_line) < 520 and "full note is in the journal" in note_line
    finish_event = next(e for e in run.events if e["action"] == "finish")
    assert finish_event["summary"] == long_note.strip()  # nothing lost
    assert "1. **researcher** (ok): Line one of the task. Line two of the task." in run.report
    assert "   - Summary over two lines." in run.report


def test_lead_and_role_prompts(run, home):
    (home.instructions).write_text("---\n---\nI am an invented user who likes short reports.\n")
    run([fake.brief("researcher", "Do the thing."), fake.result(), fake.finish(), LEAD_REPORT])
    lead_system = run.client.calls[0]["messages"][0]["content"]
    assert "researcher (specialist)" in lead_system
    assert "invented user who likes short reports" in lead_system
    assert lead_system.index("fixed rules") < lead_system.index("Your soul")
    role_call = run.client.calls[1]
    # a role with no tools is offered only submit_result, to hand in its result
    assert [t["function"]["name"] for t in role_call["tools"]] == ["submit_result"]
    assert role_call["messages"][1]["content"].endswith("Do the thing.")
    assert SPEC_TITLE not in role_call["messages"][1]["content"]  # roles see the brief, not the spec


SPEC_TITLE = "Survey of invented teapot makers"


def test_every_prompt_gives_todays_date(run):
    from datetime import date

    run([fake.brief("researcher", "Do the thing."), fake.result(), fake.finish(), LEAD_REPORT],
        today=date(2031, 3, 4))
    lead, role, report = (run.client.calls[i]["messages"][0]["content"] for i in (0, 1, -1))
    for system in (lead, role, report):
        assert "Today's date is Tuesday 4 March 2031." in system
        assert system.index("fixed rules") < system.index("Today's date")  # after the rules, before the soul
        assert system.index("Today's date") < system.index("=== Your soul")


# -- loop detection -------------------------------------------------------------


def test_looping_role_ends_its_brief_and_the_lead_is_told(run, home):
    write_role(home, "researcher", max_steps=20, tools=["test.echo"])
    calls = []
    status = run(
        [
            fake.brief("researcher", "Look something up."),
            fake.call("test__echo", {"q": "same"}, times=4),
            fake.finish("The researcher got stuck."),
            fake.keep_notes(), LEAD_REPORT,
        ],
        registry=echo_registry(calls),
    )
    assert status == "completed"  # the run carried on after the brief ended
    assert_reported(run, "completed")
    assert len(calls) == 3  # the fourth identical call never ran
    lead_sees = run.client.calls[-3]["messages"][-1]  # the last work call, before notes and report
    assert lead_sees["role"] == "tool" and '"status": "stopped_by_limit"' in lead_sees["content"]
    assert '"limit": "loop_detection"' in lead_sees["content"]
    assert "**researcher** (stopped_by_limit)" in run.report
    assert "Limits that ended a brief: `loop_detection` (researcher)" in run.report
    assert "Limit that stopped the run: none" in run.report


def test_looping_lead_is_stopped(run):
    status = run(
        [fake.brief("researcher", "Same task."), fake.result()] * 3
        + [fake.brief("researcher", "Same task."), fake.keep_notes(), LEAD_REPORT]
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "loop_detection")
    assert run.meta["usage"]["rounds"] == 3


# -- run-level limits ---------------------------------------------------------------


def test_over_token_limit_stops_and_reserve_pays_for_report(run):
    limits = {"max_tokens": 20000, "report_reserve": 0.25, "max_output_tokens": 1000, "report_max_output_tokens": 1000}
    status = run(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 12400, "output": 200}),
            LEAD_REPORT,  # no notes call: it would have left too little for the report
        ],
        limits=limits,
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "max_tokens")
    assert "The notes were not rewritten: not enough of the token limit was left" in run.report
    spend = run.meta["usage"]
    assert spend["total_tokens"] <= 20000
    # Work stopped inside the share left after the reserve; only the report used the reserve.
    report_call = [e for e in run.events if e["action"] == "model_call"][-1]
    work_tokens = spend["total_tokens"] - report_call["input_tokens"] - report_call["output_tokens"]
    assert work_tokens <= 15000


def test_without_a_reserve_the_report_falls_back_to_the_journal(run):
    limits = {"max_tokens": 20000, "report_reserve": 0.0, "max_output_tokens": 1000, "report_max_output_tokens": 1000}
    status = run(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 17400, "output": 200}),
            LEAD_REPORT,
        ],
        limits=limits,
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "max_tokens", by_lead=False)
    assert run.meta["usage"]["total_tokens"] <= 20000
    assert "Read it all." in run.report  # the harness still reports what was done


def test_dollars_are_an_estimate_not_a_limit(run):
    # A run that would have passed the old $1 budget many times over is not stopped for it.
    status = run(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 60000, "output": 500}),
            fake.finish(),
            LEAD_REPORT,
        ],
        limits={"max_tokens": 1_000_000, "budget_usd": 0.0001},  # a retired setting is ignored
    )
    assert status == "completed"
    assert "## Usage" in run.report and "$" not in run.report
    assert "budget_usd" not in run.meta["limits"]


def test_unlimited_allowance_has_no_token_cap_but_keeps_rounds(run):
    status = run(
        [fake.brief("researcher", f"Task {i}.") if i % 2 == 0 else fake.result(usage={"input": 900_000, "output": 10})
         for i in range(2 * 50)] + [fake.brief("researcher", "One more."), fake.keep_notes(), LEAD_REPORT],
        limits={"allowance": "unlimited"},
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "max_rounds")
    assert run.meta["limits"]["max_tokens"] is None
    assert run.meta["usage"]["total_tokens"] > 40_000_000
    assert "of no cap (allowance: unlimited)" in run.report


def test_max_rounds(run):
    status = run(
        [
            fake.brief("researcher", "Task one."), fake.result(),
            fake.brief("researcher", "Task two."), fake.result(),
            fake.brief("researcher", "Task three."),
            fake.keep_notes(), LEAD_REPORT,
        ],
        limits={"max_rounds": 2},
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "max_rounds")
    assert run.meta["usage"]["rounds"] == 2


def test_timed_out_run(run):
    status = run(
        [
            fake.brief("researcher", "Take your time."),
            fake.result("Took ages.", advance=120),
            fake.keep_notes(), LEAD_REPORT,
        ],
        limits={"run_timeout": "60s"},
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "run_timeout")
    assert "Took ages." in run.report


def test_timeout_during_a_model_call(run):
    status = run(
        [
            fake.brief("researcher", "Take your time."),
            {"error": "the model call ran out of time", "timed_out": True, "advance": 120},
            fake.keep_notes(), LEAD_REPORT,
        ],
        limits={"run_timeout": "60s"},
    )
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "run_timeout")
    assert "in_progress" in run.report


def test_model_calls_get_the_remaining_time_as_timeout(run):
    run(
        [fake.brief("researcher", "Quick.", advance=40), fake.result(), fake.finish(), LEAD_REPORT],
        limits={"run_timeout": "60s", "report_timeout": "30s"},
    )
    timeouts = [c["timeout"] for c in run.client.calls]
    assert timeouts[0] == 60
    assert timeouts[1] == 20  # 40 seconds were used by the first call
    assert timeouts[-1] == 30  # the report call has its own bound


# -- per-brief and per-tool limits -----------------------------------------------------


def test_max_steps_ends_the_brief_and_the_lead_can_brief_again(run, home):
    write_role(home, "researcher", max_steps=3, tools=["test.echo"])
    status = run(
        [
            fake.brief("researcher", "Look things up."),
            fake.call("test__echo", {"q": "a"}),
            fake.call("test__echo", {"q": "b"}),
            fake.call("test__echo", {"q": "c"}),
            # the fourth model call is refused by the gate before it happens
            fake.brief("researcher", "Just the first thing, please."),
            fake.result("Found the first thing."),
            fake.finish(),
            fake.keep_notes(), LEAD_REPORT,
        ],
        registry=echo_registry(),
    )
    assert status == "completed"
    assert_reported(run, "completed")
    assert "Limits that ended a brief: `max_steps` (researcher)" in run.report
    assert "1. **researcher** (stopped_by_limit)" in run.report
    assert "2. **researcher** (ok)" in run.report
    assert run.meta["usage"]["rounds"] == 2


def test_lead_steps_still_stop_the_run(run, home):
    write_role(home, "lead", kind="lead", max_steps=2, soul="You are an invented Lead.")
    status = run([fake.call("brief", {"role": "nobody", "task": "x"}), fake.call("brief", {"role": "nobody", "task": "y"}),
                  LEAD_REPORT])
    assert status == "stopped_by_limit"
    assert_reported(run, "stopped_by_limit", "max_steps")


def test_max_calls_per_tool_refuses_the_call_and_the_role_carries_on(run, home):
    write_role(home, "researcher", max_steps=20, tools=["test.echo"])
    calls = []
    status = run(
        [
            fake.brief("researcher", "Look things up."),
            fake.call("test__echo", {"q": "a"}),
            fake.call("test__echo", {"q": "b"}),
            fake.call("test__echo", {"q": "c"}),
            fake.result("Looked up two of three."),
            fake.finish(),
            fake.keep_notes(), LEAD_REPORT,
        ],
        registry=echo_registry(calls),
        limits={"tool_max_calls": {"test.echo": 2}},
    )
    assert status == "completed"
    assert_reported(run, "completed")
    assert len(calls) == 2  # the third call never ran
    refused = run.client.calls[4]["messages"][-1]
    assert refused["role"] == "tool" and "used all 2 calls" in refused["content"]
    assert "test.echo 2 of 2" in run.report
    assert "refused at their cap: test.echo 1" in run.report
    assert run.meta["usage"]["tool_refused"] == {"test.echo": 1}


def test_unknown_tools_count_against_caps(run):
    # A role with no tools that keeps calling one anyway is still bounded.
    status = run(
        [fake.brief("researcher", "Search."), fake.call("web__search", {"q": "teapots"}, times=4),
         fake.finish(), fake.keep_notes(), LEAD_REPORT]
    )
    assert status == "completed"
    assert_reported(run, "completed")
    assert "Limits that ended a brief: `loop_detection` (researcher)" in run.report
    assert run.actions().count("tool_refused") == 3


def test_max_output_tokens_bounds_every_call(run, home):
    write_role(home, "researcher", max_steps=5)
    run(
        [fake.brief("researcher", "Write."), fake.result(usage={"input": 50, "output": 99999}), fake.finish(), LEAD_REPORT],
        limits={"max_output_tokens": 500, "report_max_output_tokens": 2000},
    )
    assert all(c["max_output_tokens"] <= 500 for c in run.client.calls)
    assert run.client.calls[-1]["max_output_tokens"] == 500  # the report call is bounded too
    out = [e["output_tokens"] for e in run.events if e["action"] == "model_call"]
    assert max(out) <= 500


# -- the result contract ----------------------------------------------------------


def test_invalid_result_is_retried_once_then_failed(run):
    status = run(
        [
            fake.brief("researcher", "Report back."),
            fake.say("Here is what I found: lots of teapots."),
            fake.say('{"summary": "Found teapots."}'),
            fake.finish("The researcher failed."),
            fake.keep_notes(), LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(run, "completed")
    retry = run.client.calls[2]["messages"][-1]
    assert retry["role"] == "user" and "rejected" in retry["content"] and "last attempt" in retry["content"]
    lead_sees = run.client.calls[3]["messages"][-1]
    assert lead_sees["role"] == "tool" and '"status": "failed"' in lead_sees["content"]
    assert run.actions().count("result_invalid") == 2
    assert "**researcher** (failed)" in run.report


def test_invalid_result_then_valid_is_accepted(run):
    status = run(
        [
            fake.brief("researcher", "Report back."),
            fake.say("```json\n{\"summary\": 1}\n```"),
            fake.result("Second time lucky."),
            fake.finish(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert "Second time lucky." in run.report
    assert run.actions().count("result_invalid") == 1


# -- failures still end in a report -----------------------------------------------------


def test_report_call_failure_falls_back_to_journal(run):
    status = run(
        [fake.brief("researcher", "Do it."), fake.result("Did it."), fake.finish(), {"error": "service unavailable"}]
    )
    assert status == "completed"
    assert_reported(run, "completed", by_lead=False)
    assert "Did it." in run.report


def test_model_error_fails_the_run_with_a_report(run):
    status = run([{"error": "HTTP 401: invalid key"}, LEAD_REPORT])
    assert status == "failed"
    assert_reported(run, "failed")
    assert "invalid key" in run.report


def test_setup_error_in_a_role_is_reported_by_the_lead(run, home):
    write_role(home, "researcher", model="no-such-model")
    status = run([LEAD_REPORT])
    assert status == "failed"
    assert_reported(run, "failed")
    assert "no-such-model" in run.report


def test_setup_error_in_the_lead_still_writes_a_report(run, home):
    from ombro import mdfile

    mdfile.write(home.lead_dir / "config.md", {"name": "lead", "kind": "lead", "model": "nope"})
    status = run([LEAD_REPORT])
    assert status == "failed"
    assert_reported(run, "failed", by_lead=False)
    assert "nope" in run.report
    assert run.client.calls == []


def test_stop_request_ends_run_with_report(run):
    def stop_then_reply(messages, tools):
        run.engine.request_stop("stopped with `ombro stop`")
        return fake.result("Partly done.")

    status = run([fake.brief("researcher", "Work."), stop_then_reply, fake.keep_notes(), LEAD_REPORT])
    assert status == "stopped"
    assert_reported(run, "stopped")
    assert run.meta["limit_hit"] is None
    assert "Partly done." in run.report


def test_cannot_proceed_is_a_successful_run(run):
    status = run([fake.finish("No role can do this honestly.", outcome="cannot_proceed"), LEAD_REPORT])
    assert status == "completed"
    assert "cannot_proceed" in run.report


def test_lead_that_never_calls_a_tool_is_finished(run):
    status = run([fake.say("I think we're done."), fake.say("Really."), LEAD_REPORT])
    assert status == "completed"
    assert_reported(run, "completed")


def test_limits_are_fixed_at_start(run, home):
    from ombro import mdfile

    def edit_limits(messages, tools):
        meta, body = mdfile.read(home.limits)
        meta["max_rounds"] = 50
        mdfile.write(home.limits, meta, body)
        return fake.result()

    run(
        [fake.brief("researcher", "One."), edit_limits, fake.brief("researcher", "Two."), LEAD_REPORT],
        limits={"max_rounds": 1},
    )
    assert run.meta["limit_hit"] == "max_rounds"
    assert run.meta["limits"]["max_rounds"] == 1


@pytest.mark.parametrize("bad", [{"max_tokens": -1}, {"report_reserve": 0.9}, {"nonsense": 1}, {"run_timeout": "soon"}, {"allowance": "lavish"}])
def test_bad_limits_are_refused(home, bad):
    from ombro.settings import SetupError, load_limits

    with pytest.raises(SetupError):
        load_limits(home, bad)
