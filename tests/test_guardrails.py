"""Acceptance tests for the guardrails: each limit stops a job, and a report is always written."""

from __future__ import annotations

import pytest

from conftest import LEAD_REPORT, echo_registry, write_role
from ombro import fake


def assert_reported(job, status: str, limit: str | None = None, by_lead: bool = True):
    report = job.report
    assert job.meta["status"] == status
    assert f"**Status:** {status}" in report
    for section in (
        "## Summary",
        "## What was asked",
        "## What was done",
        "## Deliverables",
        "## Outward actions",
        "## Changes to notes.md",
        "## Usage",
    ):
        assert section in report, section
    # "Decisions for you" only when something waits on the user, never a "None." for the sake of it.
    if "## Decisions for you" in report:
        decisions = report.split("## Decisions for you", 1)[1].split("\n## ", 1)[0].strip()
        assert decisions and decisions.rstrip(".").lower() not in ("none", "none recorded"), decisions
    if limit:
        assert job.meta["limit_hit"] == limit
        assert f"**Stopped by:** `{limit}`" in report
        assert f"Limit that stopped the job: `{limit}`" in report
    if by_lead:
        assert "LEAD-REPORT-MARKER" in report
        assert job.client.remaining == 0, "the Lead's report reply was not the last call"
    else:
        assert "written by the harness" in report
    assert job.actions()[-1] == "job_ended"


# -- the ordinary path --------------------------------------------------------


def test_completed_job(job):
    status = job(
        [
            fake.brief("researcher", "Find three invented teapot makers."),
            fake.result(
                "Found three makers.", deliverables=[{"name": "makers", "path": "Ada Pots; Brin Ware; Coll Clay"}]
            ),
            fake.finish("All done."),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    assert "Found three makers." in job.report
    assert "Ada Pots" in job.report
    assert job.meta["usage"]["rounds"] == 1
    # Lead, role, Lead, notes, report: no skills call, as no earlier job is of its kind
    assert job.meta["usage"]["model_calls"] == 5
    # The report call had no tools.
    assert job.client.calls[-1]["tools"] is None


def test_report_keeps_closing_note_and_brief_lines_short(job):
    long_note = "Final text follows.\n\n" + ("An invented paragraph of guide text. " * 60)
    job(
        [
            fake.brief("researcher", "Line one of the task.\n\nLine two of the task."),
            fake.result("Summary over\ntwo lines."),
            fake.finish(long_note),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    assert "LEAD-REPORT-MARKER" in job.report
    # The Lead wrote a report, so its closing note and the roles' summaries aren't repeated in the record.
    assert "Lead's closing note" not in job.report
    finish_event = next(e for e in job.events if e["action"] == "finish")
    assert finish_event["summary"] == long_note.strip()  # nothing lost
    assert "1. **researcher** (ok): Line one of the task. Line two of the task." in job.report
    assert "   - Summary over two lines." in job.report


def test_a_report_without_the_lead_keeps_the_closing_note_short(job):
    long_note = "Final text follows.\n\n" + ("An invented paragraph of guide text. " * 60)
    job([fake.brief("researcher", "The task."), fake.result("Summary over\ntwo lines."), fake.finish(long_note)])
    note_line = next(line for line in job.report.splitlines() if line.startswith("- **Lead's closing note"))
    assert len(note_line) < 520 and "full note is in the journal" in note_line
    assert "   - Summary over two lines." in job.report


def test_lead_and_role_prompts(job, home):
    (home.instructions).write_text("---\n---\nI am an invented user who likes short reports.\n")
    job([fake.brief("researcher", "Do the thing."), fake.result(), fake.finish(), LEAD_REPORT])
    lead_system = job.client.calls[0]["messages"][0]["content"]
    assert "researcher (specialist)" in lead_system
    assert "invented user who likes short reports" in lead_system
    assert lead_system.index("fixed rules") < lead_system.index("Your soul")
    role_call = job.client.calls[1]
    # a role with no tools is offered only submit_result, to hand in its result
    assert [t["function"]["name"] for t in role_call["tools"]] == ["submit_result"]
    assert role_call["messages"][1]["content"].endswith("Do the thing.")
    assert SPEC_TITLE not in role_call["messages"][1]["content"]  # roles see the brief, not the spec


SPEC_TITLE = "Survey of invented teapot makers"


def test_every_prompt_gives_todays_date(job):
    from datetime import date

    job([fake.brief("researcher", "Do the thing."), fake.result(), fake.finish(), LEAD_REPORT], today=date(2031, 3, 4))
    lead, role, report = (job.client.calls[i]["messages"][0]["content"] for i in (0, 1, -1))
    for system in (lead, role, report):
        assert "Today's date is Tuesday 4 March 2031." in system
        assert system.index("fixed rules") < system.index("Today's date")  # after the rules, before the soul
        assert system.index("Today's date") < system.index("=== Your soul")


# -- loop detection -------------------------------------------------------------


def test_looping_role_ends_its_brief_and_the_lead_is_told(job, home):
    write_role(home, "researcher", max_steps=20, tools=["test.echo"])
    calls = []
    status = job(
        [
            fake.brief("researcher", "Look something up."),
            fake.call("test__echo", {"q": "same"}, times=4),
            fake.finish("The researcher got stuck."),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_registry(calls),
    )
    assert status == "completed"  # the job carried on after the brief ended
    assert_reported(job, "completed")
    assert len(calls) == 3  # the fourth identical call never ran
    lead_sees = job.client.calls[-3]["messages"][-1]  # the last work call, before notes and report
    assert lead_sees["role"] == "tool" and '"status": "stopped_by_limit"' in lead_sees["content"]
    assert '"limit": "loop_detection"' in lead_sees["content"]
    assert "**researcher** (stopped_by_limit)" in job.report
    assert "Limits that ended a brief: `loop_detection` (researcher)" in job.report
    assert "Limit that stopped the job: none" in job.report


def test_looping_lead_is_stopped(job):
    status = job(
        [fake.brief("researcher", "Same task."), fake.result()] * 3
        + [fake.brief("researcher", "Same task."), fake.keep_notes(), LEAD_REPORT]
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "loop_detection")
    assert job.meta["usage"]["rounds"] == 3


# -- run-level limits ---------------------------------------------------------------


@pytest.fixture
def short_character(monkeypatch):
    """A short built-in Lead character, so these token budgets don't depend on the length of Ombro's own."""
    monkeypatch.setattr("ombro.home.LEAD_SOUL", "You are the Lead.\n")


def test_over_token_limit_stops_and_reserve_pays_for_report(job, short_character):
    limits = {"max_tokens": 20000, "report_reserve": 0.25, "max_output_tokens": 1000, "report_max_output_tokens": 1000}
    status = job(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 10500, "output": 200}),
            LEAD_REPORT,  # no notes call: it would have left too little for the report
        ],
        limits=limits,
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "max_tokens")
    assert "The notes were not rewritten: not enough of the token limit was left" in job.report
    spend = job.meta["usage"]
    assert spend["total_tokens"] <= 20000
    # Work stopped inside the share left after the reserve; only the report used the reserve.
    report_call = [e for e in job.events if e["action"] == "model_call"][-1]
    work_tokens = spend["total_tokens"] - report_call["input_tokens"] - report_call["output_tokens"]
    assert work_tokens <= 15000


def test_without_a_reserve_the_report_falls_back_to_the_journal(job, short_character):
    limits = {"max_tokens": 20000, "report_reserve": 0.0, "max_output_tokens": 1000, "report_max_output_tokens": 1000}
    status = job(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 15500, "output": 200}),
            LEAD_REPORT,
        ],
        limits=limits,
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "max_tokens", by_lead=False)
    assert job.meta["usage"]["total_tokens"] <= 20000
    assert "Read it all." in job.report  # the harness still reports what was done


def test_dollars_are_an_estimate_not_a_limit(job):
    # A job that would have passed the old $1 budget many times over is not stopped for it.
    status = job(
        [
            fake.brief("researcher", "Read a great deal."),
            fake.result("Read it all.", usage={"input": 60000, "output": 500}),
            fake.finish(),
            LEAD_REPORT,
        ],
        limits={"max_tokens": 1_000_000, "budget_usd": 0.0001},  # a retired setting is ignored
    )
    assert status == "completed"
    assert "## Usage" in job.report and "$" not in job.report
    assert "budget_usd" not in job.meta["limits"]


def test_unlimited_allowance_has_no_token_cap_but_keeps_rounds(job):
    status = job(
        [
            fake.brief("researcher", f"Task {i}.")
            if i % 2 == 0
            else fake.result(usage={"input": 900_000, "output": 10})
            for i in range(2 * 50)
        ]
        + [fake.brief("researcher", "One more."), fake.keep_notes(), LEAD_REPORT],
        limits={"allowance": "unlimited"},
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "max_rounds")
    assert job.meta["limits"]["max_tokens"] is None
    assert job.meta["usage"]["total_tokens"] > 40_000_000
    assert "of no cap (allowance: unlimited)" in job.report


def test_max_rounds(job):
    status = job(
        [
            fake.brief("researcher", "Task one."),
            fake.result(),
            fake.brief("researcher", "Task two."),
            fake.result(),
            fake.brief("researcher", "Task three."),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        limits={"max_rounds": 2},
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "max_rounds")
    assert job.meta["usage"]["rounds"] == 2


def test_timed_out_job(job):
    status = job(
        [
            fake.brief("researcher", "Take your time."),
            fake.result("Took ages.", advance=120),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        limits={"job_timeout": "60s"},
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "job_timeout")
    assert "Took ages." in job.report


def test_timeout_during_a_model_call(job):
    status = job(
        [
            fake.brief("researcher", "Take your time."),
            {"error": "the model call ran out of time", "timed_out": True, "advance": 120},
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        limits={"job_timeout": "60s"},
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "job_timeout")
    assert "in_progress" in job.report


def test_model_calls_get_the_remaining_time_as_timeout(job):
    job(
        [fake.brief("researcher", "Quick.", advance=40), fake.result(), fake.finish(), LEAD_REPORT],
        limits={"job_timeout": "60s", "report_timeout": "30s"},
    )
    timeouts = [c["timeout"] for c in job.client.calls]
    assert timeouts[0] == 60
    assert timeouts[1] == 20  # 40 seconds were used by the first call
    assert timeouts[-1] == 30  # the report call has its own bound


# -- per-brief and per-tool limits -----------------------------------------------------


def test_max_steps_ends_the_brief_and_the_lead_can_brief_again(job, home):
    write_role(home, "researcher", max_steps=3, tools=["test.echo"])
    status = job(
        [
            fake.brief("researcher", "Look things up."),
            fake.call("test__echo", {"q": "a"}),
            fake.call("test__echo", {"q": "b"}),
            fake.call("test__echo", {"q": "c"}),
            # the fourth model call is refused by the gate before it happens
            fake.brief("researcher", "Just the first thing, please."),
            fake.result("Found the first thing."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_registry(),
    )
    assert status == "completed"
    assert_reported(job, "completed")
    assert "Limits that ended a brief: `max_steps` (researcher)" in job.report
    assert "1. **researcher** (stopped_by_limit)" in job.report
    assert "2. **researcher** (ok)" in job.report
    assert job.meta["usage"]["rounds"] == 2


def test_lead_steps_still_stop_the_job(job, home):
    write_role(home, "lead", kind="lead", max_steps=2, soul="You are an invented Lead.")
    status = job(
        [
            fake.call("brief", {"role": "nobody", "task": "x"}),
            fake.call("brief", {"role": "nobody", "task": "y"}),
            LEAD_REPORT,
        ]
    )
    assert status == "stopped_by_limit"
    assert_reported(job, "stopped_by_limit", "max_steps")


def test_max_calls_per_tool_refuses_the_call_and_the_role_carries_on(job, home):
    write_role(home, "researcher", max_steps=20, tools=["test.echo"])
    calls = []
    status = job(
        [
            fake.brief("researcher", "Look things up."),
            fake.call("test__echo", {"q": "a"}),
            fake.call("test__echo", {"q": "b"}),
            fake.call("test__echo", {"q": "c"}),
            fake.result("Looked up two of three."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_registry(calls),
        limits={"tool_max_calls": {"test.echo": 2}},
    )
    assert status == "completed"
    assert_reported(job, "completed")
    assert len(calls) == 2  # the third call never ran
    refused = job.client.calls[4]["messages"][-1]
    assert refused["role"] == "tool" and "used all 2 calls" in refused["content"]
    assert "test.echo 2 of 2" in job.report
    assert "refused at their cap: test.echo 1" in job.report
    assert job.meta["usage"]["tool_refused"] == {"test.echo": 1}


def test_unknown_tools_count_against_caps(job):
    # A role with no tools that keeps calling one anyway is still bounded.
    status = job(
        [
            fake.brief("researcher", "Search."),
            fake.call("web__search", {"q": "teapots"}, times=4),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    assert "Limits that ended a brief: `loop_detection` (researcher)" in job.report
    assert job.actions().count("tool_refused") == 3


def test_max_output_tokens_bounds_every_call(job, home):
    write_role(home, "researcher", max_steps=5)
    job(
        [
            fake.brief("researcher", "Write."),
            fake.result(usage={"input": 50, "output": 99999}),
            fake.finish(),
            LEAD_REPORT,
        ],
        limits={"max_output_tokens": 500, "report_max_output_tokens": 2000},
    )
    assert all(c["max_output_tokens"] <= 500 for c in job.client.calls)
    assert job.client.calls[-1]["max_output_tokens"] == 500  # the report call is bounded too
    out = [e["output_tokens"] for e in job.events if e["action"] == "model_call"]
    assert max(out) <= 500


# -- the result contract ----------------------------------------------------------


def test_invalid_result_is_retried_once_then_failed(job):
    status = job(
        [
            fake.brief("researcher", "Report back."),
            fake.say("Here is what I found: lots of teapots."),
            fake.say('{"summary": "Found teapots."}'),
            fake.finish("The researcher failed."),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    retry = job.client.calls[2]["messages"][-1]
    assert retry["role"] == "user" and "rejected" in retry["content"] and "last attempt" in retry["content"]
    lead_sees = job.client.calls[3]["messages"][-1]
    assert lead_sees["role"] == "tool" and '"status": "failed"' in lead_sees["content"]
    assert job.actions().count("result_invalid") == 2
    assert "**researcher** (failed)" in job.report


def test_invalid_result_then_valid_is_accepted(job):
    status = job(
        [
            fake.brief("researcher", "Report back."),
            fake.say('```json\n{"summary": 1}\n```'),
            fake.result("Second time lucky."),
            fake.finish(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert "Second time lucky." in job.report
    assert job.actions().count("result_invalid") == 1


# -- failures still end in a report -----------------------------------------------------


def test_report_call_failure_falls_back_to_journal(job):
    status = job(
        [fake.brief("researcher", "Do it."), fake.result("Did it."), fake.finish(), {"error": "service unavailable"}]
    )
    assert status == "completed"
    assert_reported(job, "completed", by_lead=False)
    assert "Did it." in job.report


def test_model_error_fails_the_job_with_a_report(job):
    status = job([{"error": "HTTP 401: invalid key"}, LEAD_REPORT])
    assert status == "failed"
    assert_reported(job, "failed")
    assert "invalid key" in job.report


def test_setup_error_in_a_role_is_reported_by_the_lead(job, home):
    write_role(home, "researcher", model="no-such-model")
    status = job([LEAD_REPORT])
    assert status == "failed"
    assert_reported(job, "failed")
    assert "no-such-model" in job.report


def test_setup_error_in_the_lead_still_writes_a_report(job, home):
    from ombro import mdfile

    mdfile.write(home.lead_dir / "config.md", {"name": "lead", "kind": "lead", "model": "nope"})
    status = job([LEAD_REPORT])
    assert status == "failed"
    assert_reported(job, "failed", by_lead=False)
    assert "nope" in job.report
    assert job.client.calls == []


def test_stop_request_ends_job_with_report(job):
    def stop_then_reply(messages, tools):
        job.engine.request_stop("stopped with `ombro stop`")
        return fake.result("Partly done.")

    status = job([fake.brief("researcher", "Work."), stop_then_reply, fake.keep_notes(), LEAD_REPORT])
    assert status == "stopped"
    assert_reported(job, "stopped")
    assert job.meta["limit_hit"] is None
    assert "Partly done." in job.report


def test_cannot_proceed_is_a_successful_job(job):
    status = job([fake.finish("No role can do this honestly.", outcome="cannot_proceed"), LEAD_REPORT])
    assert status == "completed"
    assert "cannot_proceed" in job.report


def test_lead_that_never_calls_a_tool_is_finished(job):
    status = job([fake.say("I think we're done."), fake.say("Really."), LEAD_REPORT])
    assert status == "completed"
    assert_reported(job, "completed")


def test_limits_are_fixed_at_start(job, home):
    from ombro import mdfile

    def edit_limits(messages, tools):
        meta, body = mdfile.read(home.limits)
        meta["max_rounds"] = 50
        mdfile.write(home.limits, meta, body)
        return fake.result()

    job(
        [fake.brief("researcher", "One."), edit_limits, fake.brief("researcher", "Two."), LEAD_REPORT],
        limits={"max_rounds": 1},
    )
    assert job.meta["limit_hit"] == "max_rounds"
    assert job.meta["limits"]["max_rounds"] == 1


@pytest.mark.parametrize(
    "bad",
    [{"max_tokens": -1}, {"report_reserve": 0.9}, {"nonsense": 1}, {"job_timeout": "soon"}, {"allowance": "lavish"}],
)
def test_bad_limits_are_refused(home, bad):
    from ombro.settings import SetupError, load_limits

    with pytest.raises(SetupError):
        load_limits(home, bad)


def test_cached_input_counts_at_a_tenth_against_the_token_limit():
    from ombro.gate import Usage

    u = Usage(input_tokens=100_000, output_tokens=2_000, cached_input_tokens=90_000)
    assert u.total_tokens == 102_000  # the record keeps every token
    assert u.counted_tokens == 10_000 + 9_000 + 2_000  # the limit counts cached input at a tenth


def test_the_client_reads_cached_input_from_deepseek_and_openai():
    import httpx

    from ombro.client import OpenAICompatibleClient

    def reply(usage: dict) -> httpx.Response:
        body = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}], "usage": usage}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "https://invented.example/v1"))

    parse = OpenAICompatibleClient._parse
    deepseek = parse(
        None, reply({"prompt_tokens": 1000, "completion_tokens": 10, "prompt_cache_hit_tokens": 900}), None
    )
    openai = parse(
        None,
        reply({"prompt_tokens": 1000, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 800}}),
        None,
    )
    plain = parse(None, reply({"prompt_tokens": 1000, "completion_tokens": 10}), None)
    assert (deepseek.usage.cached_input, openai.usage.cached_input, plain.usage.cached_input) == (900, 800, 0)


def test_the_client_reads_thinking_tokens_or_counts_the_thinking_text():
    import httpx

    from ombro.client import OpenAICompatibleClient

    def reply(usage: dict, **msg) -> httpx.Response:
        body = {"choices": [{"message": {"content": "OK", **msg}, "finish_reason": "stop"}], "usage": usage}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "https://invented.example/v1"))

    parse = OpenAICompatibleClient._parse
    said = parse(
        None,
        reply({"prompt_tokens": 10, "completion_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 420}}),
        None,
    ).usage
    shown = parse(
        None, reply({"prompt_tokens": 10, "completion_tokens": 500}, reasoning_content="Invented thoughts. " * 30), None
    ).usage
    plain = parse(None, reply({"prompt_tokens": 10, "completion_tokens": 500}), None).usage
    assert (said.reasoning, said.reasoning_estimated) == (420, False)
    assert shown.reasoning_estimated and 0 < shown.reasoning <= 500
    assert plain.reasoning == 0


def test_a_job_records_thinking_and_time_per_call(job):
    job([fake.finish(), fake.keep_notes(), LEAD_REPORT])
    call = next(e for e in job.events if e["action"] == "model_call")
    assert "seconds" in call and call["note"].endswith("s")


def test_a_chat_left_open_overnight_has_not_used_its_time(home):
    from ombro.gate import Gate, LimitReached
    from ombro.settings import load_limits

    now = [0.0]
    gate = Gate(load_limits(home).for_chat(), lambda: now[0], what="chat")
    now[0] = 10 * 3600.0  # ten hours idle, then a message
    gate.begin_turn()
    gate.check_model_call("default", 100)  # no time limit reached
    now[0] += gate.limits.job_timeout  # one message that runs for the whole limit is stopped
    with pytest.raises(LimitReached, match="this message reached the chat's time limit"):
        gate.check_model_call("default", 100)


def echo_and_files(home):
    """The echo tool beside the built-in workspace tools."""
    from ombro.native import native_registry
    from ombro.settings import load_tool_settings

    reg = native_registry(home, load_tool_settings(home))
    reg.register(echo_registry().get("test.echo"))
    return reg


def test_a_brief_s_last_call_asks_for_its_result_and_a_stopped_brief_lists_its_files(job, home):
    write_role(home, "researcher", max_steps=3, tools=["test.echo", "workspace.write"])
    status = job(
        [
            fake.brief("researcher", "Write it up."),
            fake.call("workspace__write", {"path": "workspace/notes.md", "content": "# Invented notes\n"}),
            fake.call("test__echo", {"q": "b"}),
            {
                "tool_calls": [  # the last call: an echo (not run), the rest of the draft (run), and the result
                    {"name": "test__echo", "arguments": {"q": "c"}},
                    {
                        "name": "workspace__write",
                        "arguments": {"path": "workspace/notes.md", "content": "More.\n", "mode": "append"},
                    },
                    {"name": "submit_result", "arguments": fake.result("Wrote workspace/notes.md.")["content"]},
                ]
            },
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_and_files(home),
    )
    assert status == "completed"
    calls = job.client.calls
    last = calls[3]  # the Lead's first call, then the role's three
    assert [t["function"]["name"] for t in last["tools"]] == [t["function"]["name"] for t in calls[2]["tools"]]
    assert "This is your last model call for this brief" in last["messages"][-1]["content"]
    assert (job.job_dir / "workspace" / "notes.md").read_text() == "# Invented notes\nMore.\n"
    assert [e["note"] for e in job.events if e.get("tool") == "test.echo"] == ['test.echo {"q": "b"}']
    assert "1. **researcher** (ok)" in job.report


def test_the_lead_reads_the_job_s_own_files_and_nothing_else(job, home):
    write_role(home, "researcher", max_steps=2, tools=["workspace.write"])
    job(
        [
            fake.brief("researcher", "Write it up."),
            fake.call("workspace__write", {"path": "workspace/notes.md", "content": "# Invented notes\n"}),
            fake.call("workspace__write", {"path": "workspace/more.md", "content": "more"}),
            fake.call("workspace__read", {"path": "workspace/notes.md"}),
            fake.call("workspace__read", {"path": "Library/Mail/secret.md"}),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_and_files(home),
    )
    replies = [m["content"] for c in job.client.calls for m in c["messages"] if m.get("role") == "tool"]
    stopped = next(r for r in replies if "stopped_by_limit" in r)
    assert "workspace/notes.md" in stopped and "files_written" in stopped
    assert any("# Invented notes" in r for r in replies)
    assert any("reaches only this job's files" in r for r in replies)


def test_the_lead_corrects_a_name_itself_with_one_exact_edit(job, home):
    write_role(home, "researcher", max_steps=5, tools=["workspace.write"])
    job(
        [
            fake.brief("researcher", "Write it up."),
            fake.call("workspace__write", {"path": "workspace/notes.md", "content": "For Heliotrope Atelier.\n"}),
            fake.result("Wrote workspace/notes.md."),
            fake.call("workspace__edit", {"path": "workspace/notes.md", "old": "Atelier", "new": "x" * 4001}),
            fake.call("workspace__edit", {"path": "workspace/notes.md", "old": "e", "new": "E"}),
            fake.call(
                "workspace__edit",
                {"path": "workspace/notes.md", "old": "Heliotrope Atelier", "new": "Helotrope Atelier Limited"},
            ),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_and_files(home),
    )
    replies = [m["content"] for c in job.client.calls for m in c["messages"] if m.get("role") == "tool"]
    assert (job.job_dir / "workspace" / "notes.md").read_text() == "For Helotrope Atelier Limited.\n"
    replies = [m["content"] for c in job.client.calls for m in c["messages"] if m.get("role") == "tool"]
    assert any("replaces at most 4,000 characters" in r for r in replies) and any(
        "give text that occurs exactly once" in r for r in replies
    )
    assert any(e.get("tool") == "workspace.edit" and e["role"] == "lead" for e in job.events)


def test_reasoning_effort_is_sent_and_dropped_where_an_endpoint_refuses_it():
    import json as _json

    import httpx

    from ombro.client import OpenAICompatibleClient

    sent = []

    def answer(refuse):
        def handler(request):
            body = _json.loads(request.content)
            sent.append(body.get("reasoning_effort"))
            if refuse and "reasoning_effort" in body:
                return httpx.Response(400, json={"error": {"message": "Unknown parameter: reasoning_effort"}})
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        return handler

    for refuse in (False, True):
        c = OpenAICompatibleClient(
            "https://invented.example/v1",
            "m",
            lambda: "k",
            reasoning_effort="low",
            transport=httpx.MockTransport(answer(refuse)),
        )
        assert c.complete([{"role": "user", "content": "Hi"}], max_output_tokens=10, timeout=5).content == "OK"
    assert sent == ["low", "low", None]
