"""How a role hands in its result: submit_result, or one JSON object; a reply cut off at
the output limit is said so plainly; reading a file again after changing it is not a loop.
All invented; fake model."""

from __future__ import annotations

from conftest import LEAD_REPORT, write_role
from ombro import fake


def submit(summary="Done.", **extra):
    return fake.call("submit_result", {"summary": summary, "deliverables": [], "open_questions": [],
                                       "did_not_do": [], "requests": [], **extra})


def test_a_role_hands_in_its_result_with_submit_result(run, home):
    write_role(home, "researcher")
    run([fake.brief("researcher", "Do it."), submit("Found three invented makers."),
         fake.finish(), fake.keep_notes(), LEAD_REPORT])
    returned = [e for e in run.events if e["action"] == "returned"]
    assert returned[0]["status"] == "ok" and "Found three invented makers." in returned[0]["note"]


def test_a_reply_cut_off_at_the_output_limit_is_said_so_and_the_role_carries_on(run, home):
    write_role(home, "researcher", tools=["workspace.write"])
    cut = {"content": '{"summary": "A very long', "usage": {"input": 100, "output": 1000}, "finish_reason": "length"}
    run([fake.brief("researcher", "Write a long report."), cut, submit("Wrote it in parts."),
         fake.finish(), fake.keep_notes(), LEAD_REPORT], limits={"max_output_tokens": 1000})
    assert any(e["action"] == "reply_cut_off" for e in run.events)
    assert not any(e["action"] == "result_invalid" for e in run.events)
    told = [m["content"] for c in run.client.calls for m in c["messages"] if m["role"] == "user"]
    assert any("cut off because it reached the output limit" in t for t in told)
    assert [e["status"] for e in run.events if e["action"] == "returned"] == ["ok"]


def test_three_cut_off_replies_end_the_brief_with_a_reason(run, home):
    write_role(home, "researcher")
    cut = {"content": "{", "usage": {"input": 100, "output": 1000}, "finish_reason": "length"}
    run([fake.brief("researcher", "Write."), cut, cut, cut, fake.finish(), fake.keep_notes(), LEAD_REPORT],
        limits={"max_output_tokens": 1000})
    returned = [e for e in run.events if e["action"] == "returned"]
    assert returned[0]["status"] == "failed" and "cut off at the output limit three times" in returned[0]["note"]


def test_reading_a_file_again_after_writing_it_is_not_a_loop(run, home):
    write_role(home, "editor", tools=["workspace.read", "workspace.write"], max_steps=15)
    read = fake.call("workspace__read", {"path": "workspace/draft.md"})
    steps = [fake.call("workspace__write", {"path": "workspace/draft.md", "content": "v0"})]
    for v in range(1, 5):
        steps += [read, fake.call("workspace__write", {"path": "workspace/draft.md", "content": f"v{v}"})]
    run([fake.brief("editor", "Edit the draft four times."), *steps, submit("Edited it four times."),
         fake.finish(), fake.keep_notes(), LEAD_REPORT])
    assert [e["status"] for e in run.events if e["action"] == "returned"] == ["ok"]


def test_the_report_puts_the_reader_first_and_ombros_record_after(run, home):
    write_role(home, "researcher")
    narrative = ("## Summary\nThree invented makers found; one is closed.\n\n## What was done\nThe researcher "
                 "searched.\n\n## What was not done and why\nNothing.\n\n## Decisions for you\nNone.")
    run([fake.brief("researcher", "Do it."), submit("Found three."), fake.finish(), fake.keep_notes(),
         fake.say(narrative)])
    report = run.report
    order = [report.index(h) for h in ("## Summary", "## Deliverables", "## Decisions for you", "## What was done",
                                       "# Ombro's record", "## The run", "## Briefs (harness record)",
                                       "## Changes to notes.md", "## Usage")]
    assert order == sorted(order)
    assert report.startswith("# ") and "**Completed.**" in report.split("## Summary")[0]
    assert "Three invented makers found" in report.split("## Deliverables")[0]
    assert "> " not in report.split("# Ombro's record")[0]  # the spec is not quoted at the top


def test_a_run_is_named_for_its_spec_then_when_and_found_by_any_part(run, home):
    import re

    from ombro import runs

    write_role(home, "researcher")
    run([fake.finish(), fake.keep_notes(), LEAD_REPORT], spec_meta={"name": "teapot-survey"})
    name = run.run_dir.name
    assert re.fullmatch(r"teapot-survey-\d{8}-\d{4}(-\d+)?", name)
    assert runs.find_run(home, "teapot") == run.run_dir
    assert runs.find_run(home, name[-13:]) == run.run_dir  # the date and time alone
    assert runs.latest_run(home, None) == run.run_dir


def test_the_same_spec_twice_in_a_minute_gets_a_number(home):
    from ombro import runs
    from ombro.settings import load_limits

    a = runs.create_run(home, ["default"], {"name": "kettles"}, "# Kettles\n", load_limits(home), source="test")
    b = runs.create_run(home, ["default"], {"name": "kettles"}, "# Kettles\n", load_limits(home), source="test")
    assert b.name == a.name + "-2"


def test_a_models_own_output_maximum_is_learned_from_its_refusal():
    import httpx

    from ombro.client import OpenAICompatibleClient, allowed_max_tokens

    assert allowed_max_tokens("Invalid max_tokens value, the valid range of max_tokens is [1, 8192]") == 8192
    assert allowed_max_tokens("max_tokens must be less than or equal to `16384`") == 16384
    assert allowed_max_tokens("The model is overloaded") is None
    asked = []

    def handler(request):
        import json

        n = json.loads(request.content)["max_tokens"]
        asked.append(n)
        if n > 8192:
            return httpx.Response(400, json={"error": {"message": "Invalid max_tokens value, the valid range of "
                                                                  "max_tokens is [1, 8192]"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    c = OpenAICompatibleClient("https://models.invalid/v1", "m", lambda: "sk-test-INVENTED",
                               transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}], max_output_tokens=32000, timeout=10).content == "ok"
    c.complete([{"role": "user", "content": "again"}], max_output_tokens=32000, timeout=10)
    assert asked == [32000, 8192, 8192] and c.learned_max == 8192


def test_free_providers_get_more_calls_than_billed_ones(home):
    from ombro import tool_cmd
    from ombro.settings import load_limits, load_tool_settings

    def calls(tool):
        return load_limits(home, None, load_tool_settings(home)).max_calls_for(tool)

    assert (calls("web.search"), calls("web.fetch")) == (60, 100)  # DuckDuckGo and direct: free
    tool_cmd.set_tool(home, "web.search", provider="firecrawl")
    assert calls("web.search") == 20  # billed per call
    tool_cmd.set_tool(home, "web.search", max_calls=45)
    assert calls("web.search") == 45  # what tools.md says wins


def test_near_the_cap_the_reply_allowance_shrinks_rather_than_refusing(home):
    import pytest

    from dataclasses import replace

    from ombro.gate import Gate, LimitReached
    from ombro.settings import load_limits

    gate = Gate(replace(load_limits(home).for_chat(), max_tokens=50_000), lambda: 0.0)
    max_out, _ = gate.check_model_call("m", 1_000, None)
    assert max_out == 32_000  # plenty left: the full allowance
    gate.record_model_call("m", 30_000, 5_000)
    max_out, _ = gate.check_model_call("m", 1_000, None)
    assert max_out == 14_000  # 50,000 - 35,000 - 1,000 left for the reply
    gate.record_model_call("m", 12_000, 1_500)
    with pytest.raises(LimitReached):  # under 2,000 left: not worth a call
        gate.check_model_call("m", 1_000, None)
