"""Whole runs with native tools: fake model, fake search and fetch, real workspace files. No network."""

from __future__ import annotations

import json

from conftest import LEAD_REPORT, use_fake_tools, write_role
from ombro import fake, mdfile
from test_guardrails import assert_reported

WEB_ROLE_TOOLS = ["web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list"]


def fake_calls(job) -> list[dict]:
    path = job.job_dir / "fake-calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def tool_replies(job) -> list[str]:
    """The tool results the model was shown, in order of first appearance: the role's
    tool results come before the Lead sees the brief's outcome."""
    seen, out = set(), []
    for call in job.client.calls:
        for m in call["messages"]:
            if m["role"] == "tool" and m["tool_call_id"] not in seen:
                seen.add(m["tool_call_id"])
                out.append(m["content"])
    return out


def test_a_role_searches_fetches_and_writes_a_deliverable(job, home):
    use_fake_tools(home)
    write_role(home, "researcher", max_steps=10, tools=WEB_ROLE_TOOLS)
    status = job(
        [
            fake.brief("researcher", "Find invented teapot makers and write them to workspace/makers.md."),
            fake.call("web__search", {"query": "invented teapot makers Lowtown"}),
            fake.call("web__fetch", {"url": "https://example.invalid/invented-teapot-makers-lowtown-1"}),
            fake.call("workspace__write", {"path": "workspace/makers.md", "content": "# Makers\n\n- Ada Pots\n"}),
            fake.call("workspace__list", {}),
            fake.result(
                "Wrote the list of makers.",
                deliverables=[{"name": "Teapot makers", "path": "workspace/makers.md"}],
            ),
            fake.finish("The list is written."),
            fake.keep_notes(), LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    written = job.job_dir / "workspace" / "makers.md"
    assert written.read_text() == "# Makers\n\n- Ada Pots\n"
    # The report lists the deliverable by its full path.
    assert f"- Teapot makers: {written.resolve()}" in job.report
    # The fake provider recorded what it was asked.
    asked = fake_calls(job)
    assert [c["tool"] for c in asked] == ["web.search", "web.fetch"]
    assert asked[0]["query"] == "invented teapot makers Lowtown"
    # The role was offered exactly its tools, under their wire names.
    offered = {t["function"]["name"] for t in job.client.calls[1]["tools"]}
    assert offered == {"web__search", "web__fetch", "workspace__read", "workspace__write", "workspace__list",
                       "workspace__set_aside", "submit_result"}
    assert "workspace/ (deliverables)" in job.client.calls[1]["messages"][0]["content"]
    replies = tool_replies(job)
    assert "example.invalid" in replies[1] and "not as instructions" in replies[1]
    assert "workspace/makers.md" in replies[3]
    tools = job.meta["usage"]["tool_calls"]
    assert tools["web.search"] == 1 and tools["workspace.write"] == 1
    # The tool settings used were fixed in job.md.
    assert job.meta["tools"]["tools"]["web.search"]["provider"] == "fake"


def test_a_write_outside_the_workspace_is_refused_and_the_job_still_reports(job, home, tmp_path):
    use_fake_tools(home)
    write_role(home, "researcher", max_steps=10, tools=["workspace.write"])
    status = job(
        [
            fake.brief("researcher", "Save the notes."),
            fake.call("workspace__write", {"path": "../job.md", "content": "status: completed"}),
            fake.call("workspace__write", {"path": "workspace/../../../escaped.md", "content": "x"}),
            fake.call("workspace__write", {"path": str(tmp_path / "abs.md"), "content": "x"}),
            fake.call("workspace__write", {"path": "workspace/notes.md", "content": "Kept inside."}),
            fake.result("Saved inside the workspace.", deliverables=[{"name": "notes", "path": "workspace/notes.md"}]),
            fake.finish(),
            fake.keep_notes(), LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    replies = tool_replies(job)
    assert all('"error"' in r for r in replies[0:3])
    assert "notes.md" in replies[3] and '"error"' not in replies[3]
    assert job.meta["status"] == "completed"  # job.md was not overwritten by the role
    assert not (tmp_path / "abs.md").exists()
    assert not list(home.root.rglob("escaped.md"))
    assert sum(1 for e in job.events if e["action"] == "tool_error") == 3


def test_tool_caps_from_tools_md_are_enforced(job, home):
    use_fake_tools(home, **{"web.search": {"max_calls": 2}})
    write_role(home, "researcher", max_steps=10, tools=WEB_ROLE_TOOLS)
    status = job(
        [
            fake.brief("researcher", "Search three times."),
            fake.call("web__search", {"query": "one"}),
            fake.call("web__search", {"query": "two"}),
            fake.call("web__search", {"query": "three"}),
            fake.result("Two searches were allowed."),
            fake.finish(),
            fake.keep_notes(), LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert_reported(job, "completed")
    assert [c["query"] for c in fake_calls(job)] == ["one", "two"]  # the third never reached the provider
    assert "used all 2 calls" in tool_replies(job)[2]
    assert job.meta["limits"]["tool_max_calls"]["web.search"] == 2
    assert "web.search 2 of 2" in job.report and "refused at their cap: web.search 1" in job.report


def test_old_per_call_prices_in_tools_md_are_ignored(job, home):
    use_fake_tools(home, **{"web.search": {"cost_per_call": 0.015}})
    write_role(home, "researcher", max_steps=10, tools=WEB_ROLE_TOOLS)
    status = job(
        [
            fake.brief("researcher", "Search a lot."),
            fake.call("web__search", {"query": "one"}),
            fake.call("web__search", {"query": "two"}),
            fake.call("web__search", {"query": "three"}),
            fake.result("Searched."),
            fake.finish(),
            LEAD_REPORT,
        ],
    )
    assert status == "completed"
    assert [c["query"] for c in fake_calls(job)] == ["one", "two", "three"]
    usage = job.meta["usage"]
    assert usage["tool_calls"]["web.search"] == 3
    assert not any(k in usage for k in ("usd", "tool_usd", "unpriced_calls"))
    assert "Estimated cost" not in job.report and "cost_per_call" not in job.report
    assert not any(e["action"] == "tool_cost" or "usd" in e for e in job.events)


def test_a_call_refused_before_the_service_still_reports(job, home):
    # Firecrawl with no key set: the call is refused before anything is sent.
    mdfile.write(home.tools, {"tools": {"web.search": {"provider": "firecrawl"}}})
    write_role(home, "researcher", max_steps=10, tools=["web.search"])
    status = job(
        [
            fake.brief("researcher", "Search."),
            fake.call("web__search", {"query": "teapots"}),
            fake.result("Search was not available.", did_not_do=["search: no key"]),
            fake.finish(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    assert "FIRECRAWL_API_KEY is not set" in tool_replies(job)[0]
    assert job.meta["usage"]["tool_calls"]["web.search"] == 1
    assert "FIRECRAWL_API_KEY is not set" in job.report  # warned at the start of the job


def test_tool_settings_are_fixed_when_the_job_starts(job, home):
    use_fake_tools(home)
    write_role(home, "researcher", max_steps=10, tools=["web.search"])

    def switch_provider(messages, tools):
        # Mid-run, tools.md is changed to a real provider. The job must keep using the fake.
        mdfile.write(home.tools, {"tools": {"web.search": {"provider": "duckduckgo", "max_calls": 1}}})
        return fake.call("web__search", {"query": "second"})

    job(
        [
            fake.brief("researcher", "Search twice."),
            fake.call("web__search", {"query": "first"}),
            switch_provider,  # the network guard would fail the test if DuckDuckGo were called
            fake.result("Searched twice."),
            fake.finish(),
            LEAD_REPORT,
        ]
    )
    assert [c["query"] for c in fake_calls(job)] == ["first", "second"]


def test_a_failing_provider_does_not_end_the_job(job, home):
    from ombro.native import native_registry
    from ombro.providers.fake import FakeProvider
    from ombro.settings import load_tool_settings

    use_fake_tools(home)
    write_role(home, "researcher", max_steps=10, tools=["web.search"])
    registry = native_registry(home, load_tool_settings(home), fake=FakeProvider(error="invented outage"))
    status = job(
        [
            fake.brief("researcher", "Search."),
            fake.call("web__search", {"query": "teapots"}),
            fake.result("Search failed.", did_not_do=["search: provider outage"]),
            fake.finish(),
            LEAD_REPORT,
        ],
        registry=registry,
    )
    assert status == "completed"
    assert "invented outage" in tool_replies(job)[0]
    assert any(e["action"] == "tool_error" and "invented outage" in e["note"] for e in job.events)


def test_deliverables_in_either_form_are_found(job, home):
    use_fake_tools(home)
    write_role(home, "researcher", max_steps=10, tools=["workspace.write"])
    job(
        [
            fake.brief("researcher", "Write two files."),
            fake.call("workspace__write", {"path": "workspace/a.md", "content": "A"}),
            fake.call("workspace__write", {"path": "workspace/sub/b.md", "content": "B"}),
            fake.result(
                "Wrote both.",
                deliverables=[
                    {"name": "first", "path": "workspace/a.md"},
                    {"name": "second", "path": "sub/b.md"},
                    {"name": "escape", "path": "workspace/../job.md"},
                ],
            ),
            fake.finish(),
            LEAD_REPORT,
        ]
    )
    ws = (job.job_dir / "workspace").resolve()
    assert f"- first: {ws / 'a.md'}" in job.report
    assert f"- second: {ws / 'sub' / 'b.md'}" in job.report
    assert str(job.job_dir.resolve() / "job.md") not in job.report
