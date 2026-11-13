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
