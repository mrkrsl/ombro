"""Outward calls in runs, the outbox and the decisions on it (section 8.3).

Every outward tool is a tool of Ombro's fake MCP server, which writes what it
would have done to a file; nothing reaches a real service. Approval itself is
exercised for real."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import pytest

from conftest import LEAD_REPORT, SPEC, fake_sent, use_fake_mcp, write_role
from ombro import fake, mdfile, outbox, runs, schedules
from ombro.engine import Engine
from ombro.fake import ScriptedClient

ALL = ["mcp.fake.find_rows", "mcp.fake.add_row", "mcp.fake.post_message", "mcp.fake.fire_hook"]
POST = "mcp__fake__post_message"
ADD = "mcp__fake__add_row"


@pytest.fixture
def record(home, tmp_path):
    path = tmp_path / "fake-sent.jsonl"
    use_fake_mcp(home, path)
    write_role(home, "poster", kind="helper", tools=ALL, max_steps=12)
    return path


def post(text="Invented reservoir news.", reason="The spec asks for one post."):
    return fake.call(POST, {"channel": "news", "text": text, "reason": reason})


def add(values=("Invented Ltd",), reason="Record the lead."):
    return fake.call(ADD, {"sheet": "Leads", "values": list(values), "reason": reason})


def script(*role_steps, summary="Did it."):
    return [fake.brief("poster", "Post the news."), *role_steps, fake.result(summary), fake.finish(),
            fake.keep_notes(), LEAD_REPORT]


def tool_replies(run):
    """What the role was told after each of its tool calls."""
    out = []
    for call in run.client.calls:
        for m in call["messages"]:
            if m.get("role") == "tool" and m["content"] not in out:
                out.append(m["content"])
    return [json.loads(x) for x in out if x.startswith("{")]


def spec_with(outward_setting):
    return {"outward": outward_setting}


# -- during the run --------------------------------------------------------------


def test_a_send_is_staged_not_done_and_the_role_is_told(run, record):
    assert run(script(post())) == "completed"
    assert fake_sent(record) == []  # nothing reached the service
    [a] = outbox.actions(run.run_dir)
    assert a.status == "pending" and a.tool == "mcp.fake.post_message" and a.meta["class"] == "external"
    assert a.arguments == {"channel": "news", "text": "Invented reservoir news."}  # the reason is kept apart
    assert a.meta["reason"] == "The spec asks for one post." and a.meta["role"] == "poster"
    assert a.path.name == "001-fake-post-message.md"
    told = [r for r in tool_replies(run) if r.get("status") == "staged"]
    assert told and told[0]["outbox"] == "outbox/001-fake-post-message.md" and "Not done" in told[0]["note"]
    assert "staged" in run.actions()
    assert run.meta["outward"] == {"preset": "balanced", "source": "preferences", "allow": [], "stage": []}
    assert run.meta["outbox"] == {"pending": 1}  # counted when the run ends, before any decision


def test_journal_notes_stay_on_one_line(home, staged):
    a = outbox.actions(staged.run_dir)[0]
    outbox._journal(staged.run_dir, "outbox_executed", a, '{\n  "updatedRange": "Sheet1!A2:C2"\n}')
    last = staged.events[-1]
    assert "\n" not in last["note"] and last["note"].endswith('{ "updatedRange": "Sheet1!A2:C2" }')


def test_the_report_lists_staged_actions_from_the_harness_record(run, record):
    run(script(post(), add()))
    section = run.report.split("## Outward actions", 1)[1].split("## Changes to notes.md")[0]
    ref = outbox.actions(run.run_dir)[0].ref
    assert f"**{ref}** `mcp.fake.post_message` (poster)" in section
    assert "Reason: The spec asks for one post." in section
    assert "Work done during the run (1):" in section  # add_row is work: it runs
    assert "`mcp.fake.add_row` (poster): sheet: Leads" in section
    assert "Permissions for this run: balanced (from preferences)" in section
    assert "ombro outbox show" in section


def test_balanced_allows_changes_and_reads(run, record):
    find = fake.call("mcp__fake__find_rows", {"sheet": "Leads"})
    run(script(find, add()))
    assert [e["tool"] for e in fake_sent(record)] == ["find_rows", "add_row"]
    assert outbox.actions(run.run_dir) == []
    assert "outward_done" in run.actions()


def test_manual_stages_changes_but_reads_still_run(run, record):
    find = fake.call("mcp__fake__find_rows", {"sheet": "Leads"})
    run(script(find, add()), spec_meta=spec_with("manual"))
    assert [e["tool"] for e in fake_sent(record)] == ["find_rows"]
    assert [a.tool for a in outbox.actions(run.run_dir)] == ["mcp.fake.add_row"]


def test_auto_from_the_brief_runs_sends(run, record):
    run(script(post()), spec_meta=spec_with("auto"))
    assert [e["tool"] for e in fake_sent(record)] == ["post_message"]
    assert outbox.actions(run.run_dir) == []


def test_the_brief_can_allow_one_tool(run, record):
    run(script(post(), fake.call("mcp__fake__fire_hook", {"reason": "Tell the team."})),
        spec_meta=spec_with({"allow": ["mcp.fake.post_message"]}))
    assert [e["tool"] for e in fake_sent(record)] == ["post_message"]
    assert [a.tool for a in outbox.actions(run.run_dir)] == ["mcp.fake.fire_hook"]


def test_refuse_in_tools_md_beats_a_brief_that_allows(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, tools={"mcp.fake.post_message": {"permission": "refuse"}})
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post()), spec_meta=spec_with("auto"))
    assert fake_sent(record) == [] and outbox.actions(run.run_dir) == []
    assert any("refused by the user's settings" in r.get("error", "") for r in tool_replies(run))
    assert "Refused by tools.md (1)" in run.report


def test_an_allowed_call_with_no_answer_is_uncertain_and_the_role_is_told_not_to_retry(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, server_settings={"timeout": "1s"}, OMBRO_FAKE_MCP_SLEEP=3)
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(add()))  # balanced: add_row is a change, so it runs during the run
    assert [e["tool"] for e in fake_sent(record)] == ["add_row"]
    [told] = [r for r in tool_replies(run) if r.get("status") == "uncertain"]
    assert "Do not try it again" in told["error"]
    assert "outward_uncertain" in run.actions() and "outward_error" not in run.actions()
    section = run.report.split("## Outward actions", 1)[1].split("## Changes to notes.md")[0]
    assert "Uncertain: sent during the run, but no answer came back" in section
    assert "`mcp.fake.add_row` (poster): sheet: Leads" in section
    assert "service reported an error" not in section


def test_preferences_set_the_default(home, run, record):
    meta, body = mdfile.read(home.preferences_config)
    mdfile.write(home.preferences_config, {**meta, "outward": "auto"}, body)
    run(script(post()))
    assert [e["tool"] for e in fake_sent(record)] == ["post_message"]
    assert run.meta["outward"]["source"] == "preferences"


def test_a_duplicate_is_not_staged_twice(run, record):
    run(script(post(), post()))
    assert len(outbox.actions(run.run_dir)) == 1
    assert any(r.get("status") == "already_staged" for r in tool_replies(run))
    assert "identical repeat(s) were not staged again" in run.report


def test_staging_counts_towards_the_tools_cap(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, tools={"mcp.fake.post_message": {"max_calls": 2}})
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post("one"), post("two"), post("three")))
    assert [a.arguments["text"] for a in outbox.actions(run.run_dir)] == ["one", "two"]
    assert any("used all 2 calls" in r.get("error", "") for r in tool_replies(run))
    assert run.meta["spend"]["tool_refused"] == {"mcp.fake.post_message": 1}


def test_repeating_the_same_staging_ends_the_brief(run, record):
    status = run([fake.brief("poster", "Post."), post(), post(), post(), post(), fake.finish(),
                  fake.keep_notes(), LEAD_REPORT])
    assert status == "completed"
    returned = [e for e in run.events if e["action"] == "returned"][0]
    assert returned["limit"] == "loop_detection"
    assert len(outbox.actions(run.run_dir)) == 1


def test_a_call_without_a_reason_or_with_bad_arguments_is_refused(run, record):
    no_reason = fake.call(POST, {"channel": "news", "text": "x"})
    too_long = fake.call(POST, {"channel": "news", "text": "x" * 300, "reason": "Too long."})
    run(script(no_reason, too_long))
    errors = [r.get("error", "") for r in tool_replies(run)]
    assert any("give `reason`" in e for e in errors) and any("invalid text" in e for e in errors)
    assert outbox.actions(run.run_dir) == []


def test_a_server_that_cannot_start_leaves_its_tools_out(home, run, tmp_path):
    mdfile.write(home.tools, {"servers": {"fake": {"command": "/nonexistent/invented-server"}},
                              "tools": {"web.search": {"provider": "fake"}}})
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script())
    assert any("could not be used" in w for w in [e["note"] for e in run.events if e["action"] == "warning"])


def test_a_scheduled_run_stages_too(home, record):
    from ombro import keys

    keys.set_key(home, "TEST_MODEL_KEY", "invented-test-key")
    mdfile.write(home.crew_dir("default") / "specs" / "news.md", {"outward": "manual"}, SPEC)
    path = schedules.write_schedule(home, "default", "morning", "* * * * *", "news", True)
    os.utime(path, (0, 0))
    client = ScriptedClient(script(post()))

    def starter(home_, run_dir):
        Engine(home_, run_dir, client_factory=lambda spec: client).execute()

    res = schedules.tick(home, clock=lambda: time.time(), tz=ZoneInfo("Europe/London"), starter=starter)
    [run_dir] = res.started
    meta, _ = runs.read_run(run_dir)
    assert meta["schedule"] == "morning" and meta["outward"]["preset"] == "manual"
    assert [a.tool for a in outbox.actions(run_dir)] == ["mcp.fake.post_message"]
    assert fake_sent(record) == []


# -- deciding ---------------------------------------------------------------------


@pytest.fixture
def staged(run, record):
    run(script(post("one"), post("two"), post("three"), fake.call("mcp__fake__fire_hook", {"reason": "Ping."})))
    return run


def test_approve_runs_only_the_named_action_once(home, staged, record):
    a1, a2, a3, hook = outbox.actions(staged.run_dir)
    done = outbox.approve(home, a2)
    assert done.status == "executed" and "Would have posted" in done.meta["result"]
    assert [e["arguments"]["text"] for e in fake_sent(record)] == ["two"]
    with pytest.raises(outbox.OutboxError, match="executed"):
        outbox.approve(home, a2)  # never twice
    assert len(fake_sent(record)) == 1
    assert [a.status for a in outbox.actions(staged.run_dir)] == ["pending", "executed", "pending", "pending"]
    assert runs.read_run(staged.run_dir)[0]["outbox"] == {"pending": 3, "executed": 1}
    assert "## Outbox decisions" in staged.report and f"{a2.ref} `mcp.fake.post_message`: approved; done" in staged.report
    assert [e["action"] for e in staged.events][-2:] == ["outbox_approved", "outbox_executed"]


def test_decline_is_final(home, staged, record):
    a1 = outbox.actions(staged.run_dir)[0]
    outbox.decline(home, a1)
    with pytest.raises(outbox.OutboxError, match="declined"):
        outbox.approve(home, a1)
    assert fake_sent(record) == []
    assert "declined; it will never run" in staged.report


def test_an_action_with_no_arguments_can_be_approved(home, staged, record):
    hook = outbox.actions(staged.run_dir)[3]
    assert hook.arguments == {} and hook.summary() == "(no arguments)"
    assert outbox.approve(home, hook).status == "executed"
    assert fake_sent(record)[0]["tool"] == "fire_hook"


def test_edit_is_checked_and_the_edited_version_is_what_runs(home, staged, record):
    a1 = outbox.actions(staged.run_dir)[0]
    with pytest.raises(outbox.OutboxError, match="not saved"):
        outbox.edit(home, a1, {"channel": "news", "text": "x" * 300})
    edited = outbox.edit(home, a1, {"channel": "news", "text": "Better wording."})
    assert edited.edited and edited.status == "pending"
    outbox.approve(home, edited)
    assert fake_sent(record)[0]["arguments"]["text"] == "Better wording."
    assert "approved after editing; done" in staged.report


def test_a_hand_edit_is_noticed_and_checked(home, staged, record):
    a1 = outbox.actions(staged.run_dir)[0]
    meta, body = mdfile.read(a1.path)
    mdfile.write(a1.path, {**meta, "arguments": {"channel": "news"}}, body)  # text removed by hand
    a1 = outbox.load(a1.path)
    assert a1.edited
    with pytest.raises(outbox.OutboxError, match="'text' is a required property"):
        outbox.approve(home, a1)
    assert fake_sent(record) == [] and outbox.load(a1.path).status == "pending"


def test_nothing_is_decided_while_the_run_is_going(home, staged):
    runs.update_run(staged.run_dir, status="running", pid=os.getpid())
    with pytest.raises(outbox.OutboxError, match="still running"):
        outbox.approve(home, outbox.actions(staged.run_dir)[0])


def test_a_server_that_cannot_be_reached_leaves_the_action_waiting(home, staged, record):
    meta, _ = runs.read_run(staged.run_dir)
    tools = meta["tools"]
    tools["servers"]["fake"]["command"] = "/nonexistent/invented-server"
    runs.update_run(staged.run_dir, tools=tools)
    a1 = outbox.actions(staged.run_dir)[0]
    with pytest.raises(outbox.OutboxError, match="still waiting"):
        outbox.approve(home, a1)
    a1 = outbox.load(a1.path)
    assert a1.status == "pending" and "could not be started" in a1.meta["last_error"]


def test_a_refusal_set_after_the_run_still_holds(home, staged, record):
    meta, body = mdfile.read(home.tools)
    meta["tools"]["mcp.fake.post_message"] = {"permission": "refuse"}
    mdfile.write(home.tools, meta, body)
    with pytest.raises(outbox.OutboxError, match="refuse"):
        outbox.approve(home, outbox.actions(staged.run_dir)[0])
    assert fake_sent(record) == []


def test_a_service_error_is_failed_and_final(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, OMBRO_FAKE_MCP_FAIL="post_message")
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post()))
    done = outbox.approve(home, outbox.actions(run.run_dir)[0])
    assert done.status == "failed" and "invented failure" in done.meta["error"]
    with pytest.raises(outbox.OutboxError):
        outbox.approve(home, done)
    assert len(fake_sent(record)) == 1


def test_no_answer_makes_it_uncertain_and_it_is_never_retried(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, server_settings={"timeout": "1s"}, OMBRO_FAKE_MCP_SLEEP=3)
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post()))
    done = outbox.approve(home, outbox.actions(run.run_dir)[0])
    assert done.status == "uncertain"
    with pytest.raises(outbox.OutboxError, match="resolve"):
        outbox.approve(home, done)
    assert len(fake_sent(record)) == 1
    assert outbox.resolve(home, done, sent=True).status == "executed"


def test_an_action_left_executing_becomes_uncertain_and_is_settled_by_you(home, staged, record):
    a1 = outbox.actions(staged.run_dir)[0]
    meta, body = mdfile.read(a1.path)
    mdfile.write(a1.path, {**meta, "status": "executing", "executing_pid": 999999}, body)
    assert [a.status for a in outbox.waiting(home)][:1] == ["uncertain"]
    a1 = outbox.load(a1.path)
    with pytest.raises(outbox.OutboxError):
        outbox.approve(home, a1)
    assert fake_sent(record) == []
    back = outbox.resolve(home, a1, sent=False)
    assert back.status == "pending"
    assert outbox.approve(home, back).status == "executed"


def test_two_approvals_cannot_overlap(home, staged):
    with outbox.lock(staged.run_dir):
        with pytest.raises(outbox.OutboxBusy):
            outbox.approve(home, outbox.actions(staged.run_dir)[0])


def test_references_find_actions(home, staged):
    a3 = outbox.actions(staged.run_dir)[2]
    assert outbox.find(home, a3.ref).path == a3.path
    assert outbox.find(home, a3.long_ref).path == a3.path
    with pytest.raises(outbox.OutboxError, match="no staged action"):
        outbox.find(home, a3.ref.rsplit("-", 1)[0] + "-99")
    with pytest.raises(outbox.OutboxError, match="not an action reference"):
        outbox.find(home, "all")


# -- the command line, end to end ----------------------------------------------


def cli_env(home):
    return {**os.environ, "OMBRO_HOME": str(home.root)}


def ombro(home, *args, input=None, timeout=60):
    return subprocess.run([sys.executable, "-m", "ombro", *args], capture_output=True, text=True,
                          env=cli_env(home), input=input, timeout=timeout)


def test_outbox_commands_end_to_end(home, staged, record):
    a1, a2, a3, hook = outbox.actions(staged.run_dir)
    r = ombro(home, "outbox")
    assert r.returncode == 0 and "4 action(s) waiting" in r.stdout and a1.ref in r.stdout
    r = ombro(home, "outbox", "show", a2.ref)
    assert "Reason    The spec asks for one post." in r.stdout and "text: two" in r.stdout
    r = ombro(home, "outbox", "approve")
    assert r.returncode == 2 and "There is no 'all'" in r.stderr
    r = ombro(home, "outbox", "approve", a1.ref, a3.ref, input="n\n")
    assert "Run these 2 actions?" in r.stdout and "Nothing was done" in r.stdout and fake_sent(record) == []
    r = ombro(home, "outbox", "approve", a1.ref, a3.ref, input="y\n")
    assert r.returncode == 0 and f"{a1.ref}: done." in r.stdout
    r = ombro(home, "outbox", "decline", a2.ref, "--yes")
    assert f"{a2.ref}: declined" in r.stdout
    r = ombro(home, "outbox", "edit", hook.ref, "--arguments", '{"surprise": 1}')
    assert r.returncode == 0  # the hook's schema has no rules against extra fields
    assert [e["arguments"]["text"] for e in fake_sent(record)] == ["one", "three"]
    data = json.loads(ombro(home, "outbox", "--all", "--json").stdout)
    assert [d["status"] for d in data] == ["executed", "declined", "executed", "pending"]
    assert data[3]["edited"] is True
    r = ombro(home, "runs")
    assert "1 waiting" in r.stdout
    r = ombro(home, "status", staged.run_dir.name)
    assert "Outbox    1 waiting, 2 done, 1 declined" in r.stdout and "Outward   balanced" in r.stdout


def test_a_process_killed_mid_approval_leaves_it_uncertain_never_resent(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, OMBRO_FAKE_MCP_SLEEP=20)
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post()))
    [a] = outbox.actions(run.run_dir)
    proc = subprocess.Popen([sys.executable, "-m", "ombro", "outbox", "approve", a.ref, "--yes"],
                            env=cli_env(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while not fake_sent(record) and time.time() < deadline:  # the call has reached the server
        time.sleep(0.05)
    assert outbox.load(a.path).status == "executing"
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    r = ombro(home, "outbox")
    assert "uncertain" in r.stdout and "resolve" in r.stdout
    r = ombro(home, "outbox", "approve", a.ref, "--yes")
    assert r.returncode == 2 and "uncertain" in r.stderr
    assert len(fake_sent(record)) == 1  # sent once, never again
    r = ombro(home, "outbox", "resolve", a.ref, "sent")
    assert "marked as done" in r.stdout and outbox.load(a.path).status == "executed"
    events = [json.loads(x)["action"] for x in (run.run_dir / "journal.jsonl").read_text().splitlines()]
    assert "outbox_uncertain" in events and events[-1] == "outbox_resolved"


def test_ombro_tools_lists_a_servers_tools_with_their_permission(home, record):
    r = ombro(home, "tools", "fake")
    assert r.returncode == 0, r.stderr
    lines = {ln.split()[0]: ln.split()[1:4] for ln in r.stdout.splitlines()
             if ln.strip().startswith("mcp.") and not ln.split()[0].endswith(":")}
    assert lines == {"mcp.fake.find_rows": ["read", "marking", "allow"],
                     "mcp.fake.add_row": ["work", "marking", "allow"],
                     "mcp.fake.post_message": ["external", "default", "stage"],
                     "mcp.fake.fire_hook": ["external", "default", "stage"]}
    assert "class: work" in r.stdout  # it says how to class the defaulted ones
    assert "fake" in ombro(home, "tools").stdout


# -- briefing -------------------------------------------------------------------


def test_the_brief_records_what_may_happen_without_you(home, record):
    from ombro.interview import ProposalError
    from ombro.interviews import BriefPurpose
    from ombro.settings import load_limits

    purpose = BriefPurpose(home, "default", load_limits(home), "Tweet the top story.")
    ctx = dict(purpose.context())
    asked = ctx["External tools in the crew (they reach people or the public, or spend money)"]
    assert "mcp.fake.post_message" in asked and "mcp.fake.add_row" not in asked and "find_rows" not in asked
    assert "outward_preset" in json.dumps(purpose.tool())
    answers = {"name": "news-tweet", "title": "Tweet the top story", "done_looks_like": "One tweet staged.",
               "users": "Me, to approve.", "binding_constraint": "Accuracy.", "waste": "A wrong story.",
               "non_goals": "Replies."}
    with pytest.raises(ProposalError, match="outward_preset"):
        purpose.build(answers)
    proposal = purpose.check(purpose.build({**answers, "outward_preset": "balanced",
                                            "outward_stage": ["mcp.fake.post_message"]}))
    meta, _ = mdfile.parse(proposal.files[0].text)
    assert meta["outward"] == {"preset": "balanced", "stage": ["mcp.fake.post_message"]}
    assert any("outward tools: balanced (from spec); staged: mcp.fake.post_message" in w for w in proposal.warnings)


def test_a_crew_that_only_reads_and_works_is_not_asked_about_permissions(home, tmp_path):
    from ombro.interviews import BriefPurpose
    from ombro.settings import load_limits

    use_fake_mcp(home, tmp_path / "sent.jsonl")
    write_role(home, "poster", kind="helper", tools=["mcp.fake.find_rows", "mcp.fake.add_row"])
    purpose = BriefPurpose(home, "default", load_limits(home), "Fill in the sheet.")
    assert not any(k.startswith("External tools") for k, _ in purpose.context())
    assert "outward_preset" not in json.dumps(purpose.tool())
    answers = {"name": "fill-sheet", "title": "Fill in the sheet", "done_looks_like": "Rows added.",
               "users": "Me.", "binding_constraint": "Accuracy.", "waste": "Wrong rows.", "non_goals": "Charts."}
    meta, _ = mdfile.parse(purpose.check(purpose.build(answers)).files[0].text)
    assert "outward" not in meta


def test_class_work_on_a_server_makes_its_tools_run_and_be_reported_as_work(home, run, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, server_settings={"class": "work"})
    write_role(home, "poster", kind="helper", tools=ALL)
    run(script(post(), add()))  # balanced, and no outward line in the spec
    assert [e["tool"] for e in fake_sent(record)] == ["post_message", "add_row"]
    assert outbox.actions(run.run_dir) == []
    section = run.report.split("## Outward actions", 1)[1].split("## Changes to notes.md")[0]
    assert "Work done during the run (2):" in section and "Staged" not in section


def test_an_allowed_external_call_is_reported_as_sent(run, record):
    run(script(post()), spec_meta=spec_with("auto"))
    section = run.report.split("## Outward actions", 1)[1].split("## Changes to notes.md")[0]
    assert "Sent during the run, as your permissions allow (1):" in section and "Work done" not in section


def test_manual_stages_work_too(run, record):
    run(script(add()), spec_meta=spec_with("manual"))
    [a] = outbox.actions(run.run_dir)
    assert a.tool == "mcp.fake.add_row" and a.meta["class"] == "work"


def test_whole_flow_on_the_command_line(home, record):
    """A spec, a run, the outbox and approval, all through the real command, with a fake model."""
    (home.root / "script.json").write_text(json.dumps(script(post("first"), post("second"))))
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json"}, "cheap": "default"}})
    spec = home.root / "news.md"
    spec.write_text(mdfile.dump({"outward": "manual"}, SPEC))
    r = ombro(home, "run", "--spec", str(spec))
    assert r.returncode == 0, r.stderr
    assert "Staged, waiting for you (2)" in r.stdout and fake_sent(record) == []
    first, second = outbox.waiting(home)
    assert ombro(home, "outbox", "approve", first.ref, "--yes").returncode == 0
    assert ombro(home, "outbox", "decline", second.ref, "--yes").returncode == 0
    assert [e["arguments"]["text"] for e in fake_sent(record)] == ["first"]
    assert "Nothing is waiting" in ombro(home, "outbox").stdout
