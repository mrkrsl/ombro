"""What keeps a job quick (23 Nov 2026: a rewrite took 31 minutes): roles change part of a file with
workspace.edit rather than writing it all again, writes and edits give word counts so no script counts them,
the Lead makes its own corrections, thinking is low unless chosen, and a reply that ran out while thinking
is tried again without it."""

from __future__ import annotations


import pytest

from conftest import LEAD_REPORT, write_role
from ombro import fake, jobs, thinking
from ombro.native import edit, native_registry, word_counts
from ombro.settings import load_limits
from ombro.tools import SharedFolder, ToolContext, ToolInputError
from test_guardrails import echo_and_files
from test_results import submit
from test_folders import user  # noqa: F401 (fixture)

DOC = "# Hook\n\nPut a team on it.\n\n## Release\n\nLONDON. One two three four five.\n\n## Variants\n\nSix seven.\n"


def _ctx(home, user=None):
    job_dir = jobs.create_job(home, ["default"], {}, "# x", load_limits(home), source="test")
    shared = {"notes": SharedFolder("notes", user, "write")} if user is not None else {}
    return ToolContext(job_dir, "writer", shared=shared, archived=set()), job_dir


def test_word_counts_give_the_file_and_each_section():
    got = word_counts(DOC)
    assert got["words"] == 16  # headings count too
    assert got["sections"] == [
        {"heading": "Hook", "words": 5},
        {"heading": "Release", "words": 6},
        {"heading": "Variants", "words": 2},
    ]
    assert "sections" not in word_counts("Just a few words, no headings.")
    assert word_counts("```\n# not a heading\n```\n")["words"] == 3


def test_a_write_reports_word_counts(home):
    ctx, job_dir = _ctx(home)
    out = native_registry(home).get("workspace.write").handler({"path": "workspace/hook.md", "content": DOC}, ctx)
    assert out["words"] == 16 and [s["words"] for s in out["sections"]] == [5, 6, 2]


def test_an_edit_changes_passages_in_order_all_or_none(home):
    ctx, job_dir = _ctx(home)
    f = job_dir / "workspace" / "hook.md"
    f.write_text(DOC)
    tool = native_registry(home).get("workspace.edit")
    out = tool.handler(
        {
            "path": "workspace/hook.md",
            "edits": [{"old": "One two three four five.", "new": "One two."}, {"old": "Six", "new": "6"}],
        },
        ctx,
    )
    assert out["replaced"] == 2 and out["words"] == 13
    assert "LONDON. One two.\n" in f.read_text() and "6 seven." in f.read_text()
    before = f.read_text()
    with pytest.raises(ToolInputError, match="change 2: `old` occurs 0 times"):
        tool.handler(
            {"path": "workspace/hook.md", "edits": [{"old": "LONDON", "new": "PARIS"}, {"old": "absent", "new": "x"}]},
            ctx,
        )
    with pytest.raises(ToolInputError, match=r"occurs [2-9]\d* times"):
        tool.handler({"path": "workspace/hook.md", "old": "e", "new": "E"}, ctx)
    assert f.read_text() == before  # nothing written when any change fails
    with pytest.raises(ToolInputError, match="does not exist"):
        tool.handler({"path": "workspace/none.md", "old": "a", "new": "b"}, ctx)


def test_an_edit_in_a_shared_folder_keeps_the_version_before_once(home, user):
    folder = user / "Notes"
    folder.mkdir()
    (folder / "hook.md").write_text(DOC)
    ctx, _ = _ctx(home, folder)
    tool = native_registry(home).get("workspace.edit")
    first = tool.handler({"path": "shared/notes/hook.md", "old": "Six", "new": "6"}, ctx)
    second = tool.handler({"path": "shared/notes/hook.md", "old": "seven", "new": "7"}, ctx)
    assert "archived" in first and "archived" not in second
    assert (folder / "Archive" / "hook.md").read_text() == DOC
    assert "6 7." in (folder / "hook.md").read_text()


def test_the_lead_edit_takes_a_paragraph_but_not_a_rewrite(home):
    ctx, job_dir = _ctx(home)
    (job_dir / "workspace" / "a.md").write_text("x" * 3_000 + "\n")
    out = edit({"path": "workspace/a.md", "old": "x" * 3_000, "new": "y" * 3_500}, ctx, max_chars=4_000)
    assert out["replaced"] == 1
    with pytest.raises(ToolInputError, match="at most 4,000 characters"):
        edit({"path": "workspace/a.md", "old": "y" * 3_500, "new": "z" * 4_001}, ctx, max_chars=4_000)


def test_a_role_that_writes_is_offered_edit_and_uses_it(job, home):
    write_role(home, "writer", tools=["workspace.write"])
    job(
        [
            fake.brief("writer", "Write it, then fix one line."),
            fake.call("workspace__write", {"path": "workspace/hook.md", "content": DOC}),
            fake.call("workspace__edit", {"path": "workspace/hook.md", "old": "Six seven.", "new": "Six, seven."}),
            submit("Wrote workspace/hook.md."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        registry=echo_and_files(home),
    )
    offered = {t["function"]["name"] for t in job.client.calls[1]["tools"]}
    assert "workspace__edit" in offered
    assert (job.job_dir / "workspace" / "hook.md").read_text().endswith("Six, seven.\n")
    results = [m["content"] for c in job.client.calls for m in c["messages"] if m.get("role") == "tool"]
    assert any('"words": 16' in r for r in results)


def test_a_reply_that_ran_out_while_thinking_is_tried_again_without_it(job, home):
    write_role(home, "writer", tools=["workspace.write"])
    thought = {"content": "", "usage": {"input": 100, "output": 1000, "reasoning": 1000}, "finish_reason": "length"}
    cut_text = {"content": '{"summary": "A long', "usage": {"input": 100, "output": 1000}, "finish_reason": "length"}
    job(
        [
            fake.brief("writer", "Cut the release."),
            thought,
            cut_text,
            submit("Cut it."),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ],
        limits={"max_output_tokens": 1000},
    )
    job.client.thinking = None  # the fake's level, recorded with each call
    levels = [c["thinking"] for c in job.client.calls]
    # brief, thought out, then without thinking, then (cut off while writing, not thinking) as before
    assert levels[2] == "off" and levels[1] is None and levels[3] is None
    assert [e["action"] for e in job.events].count("thinking_off") == 1


def test_thinking_is_low_unless_chosen(home):
    assert thinking.DEFAULT == "low"
    assert thinking.preference(home) == "low"
    assert thinking.openai_fields("low", "https://api.deepseek.com", "deepseek-v4-pro") == {"reasoning_effort": "low"}


def test_turning_thinking_off_for_a_call_puts_each_client_back():
    from ombro.engine import _ThinkingOff

    class OpenAILike:
        thinking, reasoning_effort = "medium", "low"

    class ChatGPTLike:
        effort = "high"

    a, b = OpenAILike(), ChatGPTLike()
    with _ThinkingOff(a), _ThinkingOff(b):
        assert (a.thinking, a.reasoning_effort, b.effort) == ("off", None, "none")
    assert (a.thinking, a.reasoning_effort, b.effort) == ("medium", "low", "high")
