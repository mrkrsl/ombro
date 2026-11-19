"""What the user is asked to approve, and when.

Chat's "Go ahead?" shows every argument in full, those naming who or where a call reaches first,
and an interface folds a long one with a "Show all", never a silent cut.
After reading material, an internal change still runs unless it deletes or changes sharing; and a
server's own word (openWorldHint false) makes a tool internal only once the user has confirmed it.

Every service is a fake (Ombro's fake MCP server, the fake Composio); all data is invented."""

from __future__ import annotations

import sys

import pytest

from conftest import fake_sent, use_fake_mcp, write_role
from ombro import cli, fake, mdfile, outbox, outward, mcp_servers
from ombro.settings import SetupError, ToolSettings, load_tool_settings
from ombro.ui import Note
from test_composio import KEY, connected, fake_composio  # noqa: F401 - fixtures
from test_outbox import ALL, script, spec_with
from test_mcp_servers import typed  # noqa: F401 - fixture

LONG_BODY = "Dear client, thank you for the invented order. " * 16  # well past the old 400-character cut
ATTACKER = "attacker@example.invalid"


# -- what chat asks you to approve -----------------------------------------------------


def test_every_argument_is_shown_in_full_with_recipients_first():
    lines = outward.argument_lines({"body": LONG_BODY, "subject": "Your order", "to": "client@example.invalid",
                                    "bcc": ATTACKER, "options": {"share_with": "anyone", "draft": False}})
    assert lines[:3] == ["to: client@example.invalid", f"bcc: {ATTACKER}", "options.share_with: anyone"]
    assert f"body: {LONG_BODY}" in lines  # whole: nothing collapsed or cut
    assert lines[3:] == [f"body: {LONG_BODY}", "subject: Your order", "options.draft: false"]
    assert not outward.reaches("total") and not outward.reaches("token") and outward.reaches("recipientEmail")


def test_a_long_item_is_folded_with_its_start_never_cut_silently():
    assert outward.folded("short") is None
    start = outward.folded(f"body: {LONG_BODY}")
    assert start and len(start) <= outward.FOLD_CHARS and f"body: {LONG_BODY}".startswith(start)
    assert outward.folded("\n".join(["a line"] * 20)) == "\n".join(["a line"] * outward.FOLD_LINES)


GMAIL_SEND = {"slug": "GMAIL_SEND_EMAIL", "name": "Send email", "description": "Sends an email.",
              "toolkit": {"slug": "gmail", "name": "Gmail"}, "tags": [], "version": "20261101_00",
              "input_parameters": {"type": "object", "properties": {
                  "body": {"type": "string"}, "to": {"type": "string"}, "bcc": {"type": "string"}},
                  "required": ["body", "to"]}}


def test_chat_go_ahead_shows_a_bcc_after_a_long_first_argument(home, connected):  # noqa: F811
    from test_chat import ChatSession

    connected.accounts.append({"id": "ca_mail", "status": "ACTIVE", "user_id": "ada", "toolkit": {"slug": "gmail"},
                               "auth_config": {"id": "ac_9", "is_disabled": False}})
    connected.kit_tools["gmail"] = [GMAIL_SEND]
    meta, body = mdfile.read(home.tools)
    meta["tools"] = {"web.search": {"provider": "fake"}, "web.fetch": {"provider": "fake"}}
    mdfile.write(home.tools, meta, body)
    session = ChatSession(home)
    session([
        fake.call("tools__load", {"names": ["composio__gmail__SEND_EMAIL"]}),
        # The model puts the body first and the bcc last, as an injected instruction would ask.
        fake.call("composio__gmail__SEND_EMAIL", {"body": LONG_BODY, "to": "client@example.invalid",
                                                  "bcc": ATTACKER, "reason": "You asked me to reply."}),
        fake.say("I didn't send it."),
    ], ["Reply to the client.", "n"])
    out = session.output
    asked = out.split("I'd like to use composio.gmail.SEND_EMAIL", 1)[1].split("Go ahead?", 1)[0]
    assert f"  - bcc: {ATTACKER}" in asked and "  - to: client@example.invalid" in asked
    assert asked.index("bcc:") < asked.index("body:")  # who it reaches comes before what it says
    assert f"  - body: {LONG_BODY}" in asked  # in full in the terminal
    assert not [r for r in connected.seen if "/execute/" in r.url.path]  # the no held


def test_the_tui_keeps_the_items_under_a_lead_line_and_folds_a_long_one(home):
    import asyncio

    from ombro.tui.app import Fold, Line
    from test_tui import _app

    app, _ = _app(home)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_event(Note("I'd like to use composio.gmail.SEND_EMAIL.", level="lead",
                                items=[f"bcc: {ATTACKER}", f"body: {LONG_BODY}"]))
            await pilot.pause()
            texts = [str(w.text) for w in app.query(Line)]
            assert any(f"bcc: {ATTACKER}" in t for t in texts)
            [fold] = list(app.query(Fold))
            assert "Show all (" in str(fold.text) and LONG_BODY not in str(fold.text)
            fold.action_toggle()
            assert LONG_BODY in str(fold.text) and "Show less" in str(fold.text)

    asyncio.run(run())


# -- after reading, deleting and sharing wait; other internal edits run -----------------


def test_what_waits_after_reading():
    held = outward.held_after_reading
    assert held("mcp.notion.update_page", {"page_id": "p1", "text": "New prices"}) is None
    assert held("mcp.notion.trash_page", {"page_id": "p1"}) == "deletes"
    assert held("composio.googledrive.DELETE_FILE", {"file_id": "f1"}) == "deletes"
    assert held("api.wiki.request", {"method": "DELETE", "path": "/pages/1"}) == "deletes"
    assert held("composio.googledocs.SHARE_DOCUMENT", {"document_id": "d1"}) == "shares"
    assert held("mcp.notion.invite_member", {"email": "a@example.invalid"}) == "shares"
    assert held("mcp.notion.update_page", {"page_id": "p1", "public": True}) == "shares"
    assert held("mcp.drive.update_file", {"file_id": "f1", "settings": {"visibility": "anyoneWithLink"}}) == "shares"
    assert held("mcp.notion.update_page", {"page_id": "p1", "public": False}) is None
    assert held("mcp.notion.update_page", {"page_id": "p1", "access_token": "x"}) is None


@pytest.fixture
def trash(home, tmp_path):
    path = tmp_path / "fake-sent.jsonl"
    use_fake_mcp(home, path, confirmed=("add_row", "trash_sheet"), OMBRO_FAKE_MCP_EXTRA="trash")
    write_role(home, "poster", kind="helper", tools=[*ALL, "mcp.fake.trash_sheet"], max_steps=12)
    return path


FIND = fake.call("mcp__fake__find_rows", {"sheet": "Leads"})
ADD = fake.call("mcp__fake__add_row", {"sheet": "Leads", "values": ["Invented Ltd"], "reason": "The page says to."})
TRASH = fake.call("mcp__fake__trash_sheet", {"sheet": "Leads", "reason": "The page says to."})


def test_after_material_an_internal_edit_runs_but_a_deletion_waits(job, trash):
    assert job(script(FIND, ADD, TRASH)) == "completed"
    assert [e["tool"] for e in fake_sent(trash)] == ["find_rows", "add_row"]  # the edit ran
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "mcp.fake.trash_sheet" and a.meta["class"] == "internal" and a.status == "pending"
    assert any(e["action"] == "staged" and e.get("tainted") for e in job.events)
    assert "Held for your approval because the role had read material" in job.report


def test_without_material_an_internal_deletion_runs(job, trash):
    assert job(script(TRASH)) == "completed"
    assert [e["tool"] for e in fake_sent(trash)] == ["trash_sheet"]
    assert outbox.actions(job.job_dir) == []


def test_a_deletion_the_spec_allows_by_name_runs_after_material(job, trash):
    job(script(FIND, TRASH), spec_meta=spec_with({"allow": ["mcp.fake.trash_sheet"]}))
    assert outbox.actions(job.job_dir) == []


def test_in_chat_a_deletion_after_reading_asks_first(home, tmp_path):
    from test_chat import ChatSession

    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, confirmed=("add_row", "trash_sheet"), OMBRO_FAKE_MCP_EXTRA="trash")
    session = ChatSession(home)
    session([
        fake.call("tools__load", {"names": ["mcp__fake__find_rows", "mcp__fake__add_row", "mcp__fake__trash_sheet"]}),
        fake.call("mcp__fake__find_rows", {"sheet": "Leads"}),
        fake.call("mcp__fake__add_row", {"sheet": "Leads", "values": ["x"], "reason": "You asked."}),
        fake.call("mcp__fake__trash_sheet", {"sheet": "Leads", "reason": "The sheet says to."}),
        fake.say("Done."),
    ], ["Tidy the leads.", "n"])
    asked = [a for a in session.answers.asked if "Go ahead?" in a]
    assert len(asked) == 1 and "use mcp.fake.trash_sheet" in session.output
    assert [e["tool"] for e in fake_sent(record)] == ["find_rows", "add_row"]
    assert mdfile.read(session.chat.path)[0].get("tainted") is True  # kept for a reopened chat


# -- a server's own word needs the user's confirmation --------------------------------


def test_a_servers_claim_alone_does_not_make_a_tool_internal():
    claim = {"readOnlyHint": False, "openWorldHint": False}
    assert outward.classify_with_source(claim) == ("external", "unconfirmed")
    assert outward.classify_with_source(claim, confirmed=True) == ("internal", "marking")
    assert outward.classify_with_source({"readOnlyHint": True}) == ("read-only", "marking")
    assert outward.classify_with_source(claim, server_class="internal") == ("internal", "server")
    s = ToolSettings.from_dict({"servers": {"wiki": {"url": "https://mcp.example.invalid",
                                                     "confirmed_internal": ["update_page"]}}})
    assert outward.tool_class(s, "mcp.wiki.update_page", claim) == ("internal", "marking")
    assert outward.tool_class(s, "mcp.wiki.send_message", claim) == ("external", "unconfirmed")
    with pytest.raises(SetupError, match="confirmed_internal"):
        ToolSettings.from_dict({"servers": {"wiki": {"url": "https://x.invalid", "confirmed_internal": "all"}}})


def test_an_unconfirmed_internal_tool_waits_in_a_job(job, home, tmp_path):
    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record, confirmed=())
    write_role(home, "poster", kind="helper", tools=ALL, max_steps=12)
    assert job(script(ADD)) == "completed"
    assert fake_sent(record) == []
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "mcp.fake.add_row" and a.meta["class"] == "external"


def test_adding_a_server_shows_its_claims_and_keeps_the_yes(home, typed, capsys):  # noqa: F811
    typed("y", "y")
    assert cli.main(["server", "add", "sheets", "--command", f"{sys.executable} -m ombro.fake_mcp", "--no-prompt"]) == 0
    said = capsys.readouterr().out
    assert "1 of sheets's tools say they change things but stay inside" in said and "mcp.sheets.add_row" in said
    assert "Confirmed: 1 tool(s) of sheets count as internal." in said
    assert load_tool_settings(home).servers["sheets"]["confirmed_internal"] == ["add_row"]
    rows = {r["tool"]: r for r in mcp_servers.tool_claims(home, "sheets")}
    assert rows["add_row"]["class"] == "internal" and rows["post_message"]["class_from"] == "default"
    # A new command is another server, as far as anyone knows: the yes goes with the old one.
    entry = dict(load_tool_settings(home).servers["sheets"])
    mcp_servers.update_server(home, "sheets", {**entry, "args": [*entry["args"], "--invented"]})
    assert "confirmed_internal" not in load_tool_settings(home).servers["sheets"]


def test_saying_no_leaves_the_claimed_tools_external(home, typed, capsys):  # noqa: F811
    typed("y", "n")
    assert cli.main(["server", "add", "sheets", "--command", f"{sys.executable} -m ombro.fake_mcp", "--no-prompt"]) == 0
    assert "They count as external" in capsys.readouterr().out
    assert "confirmed_internal" not in load_tool_settings(home).servers["sheets"]
    assert mcp_servers.unconfirmed(mcp_servers.tool_claims(home, "sheets")) == ["add_row"]
    assert cli.main(["tools", "sheets"]) == 0  # not a terminal: it says how, and asks nothing
    out = capsys.readouterr().out
    assert "add_row" in out and "unconfirmed" in out and "ombro tools sheets --confirm" in out
    assert cli.main(["tools", "sheets", "--confirm"]) == 0
    assert mcp_servers.unconfirmed(mcp_servers.tool_claims(home, "sheets")) == []
