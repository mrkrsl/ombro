"""Composio (bring your own key): settings, listing the toolkits connected in the account, its tools'
classes, a job where lookups run and changes wait, Approvals running one, Composio's refusals in plain
words, and serve's connect and disconnect. Composio is a fake in this process; nothing reaches the
network. All data is invented."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import LEAD_REPORT
from ombro import composio, fake, keys, mdfile, outbox
from ombro.settings import SetupError, ToolSettings, load_tool_settings
from ombro.tools import ToolError
from test_serve_sessions import loopback, serve  # noqa: F401 - fixtures

KEY = "ak_INVENTED0123456789abcdef"
DOC_TOOLS = [
    {
        "slug": "GOOGLEDOCS_GET_DOCUMENT_BY_ID",
        "name": "Get document",
        "description": "Reads a document.",
        "toolkit": {"slug": "googledocs", "name": "Google Docs"},
        "tags": [],
        "version": "20261101_00",
        "input_parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "slug": "GOOGLEDOCS_CREATE_DOCUMENT",
        "name": "Create document",
        "description": "Creates a document.",
        "toolkit": {"slug": "googledocs", "name": "Google Docs"},
        "tags": ["important"],
        "version": "20261101_00",
        "input_parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    },
    {
        "slug": "GOOGLEDOCS_SHARE_DOCUMENT",
        "name": "Share document",
        "description": "Shares a document.",
        "toolkit": {"slug": "googledocs", "name": "Google Docs"},
        "tags": [],
        "version": "20261101_00",
        "input_parameters": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}, "email": {"type": "string"}},
            "required": ["document_id", "email"],
        },
    },
    {
        "slug": "GOOGLEDOCS_SUMMARISE",
        "name": "Summarise",
        "description": "Marked read-only by Composio.",
        "toolkit": {"slug": "googledocs", "name": "Google Docs"},
        "tags": ["readOnlyHint"],
        "version": "20261101_00",
        "input_parameters": {"type": "object", "properties": {}},
    },
]


class FakeComposio:
    """Answers like Composio's v3.1 API for one invented account, and keeps every request."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.execute_status = 200
        self.kit_tools = {"googledocs": DOC_TOOLS}
        self.accounts = [
            {
                "id": "ca_docs",
                "status": "ACTIVE",
                "user_id": "ada",
                "toolkit": {"slug": "googledocs"},
                "auth_config": {"id": "ac_1", "is_disabled": False},
            },
            {
                "id": "ca_old",
                "status": "EXPIRED",
                "user_id": "ada",
                "toolkit": {"slug": "gmail"},
                "auth_config": {"id": "ac_2", "is_disabled": False},
            },
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.headers.get("x-api-key") != KEY:
            return httpx.Response(401, json={"error": {"message": "Invalid API key", "status": 401}})
        path = request.url.path.removeprefix("/api/v3.1")
        if path == "/connected_accounts":
            return httpx.Response(200, json={"items": self.accounts, "next_cursor": None})
        if path == "/tools":
            kit = request.url.params.get("toolkit_slug")
            items = self.kit_tools.get(kit, [])
            limit = int(request.url.params.get("limit") or 1000)
            return httpx.Response(200, json={"items": items[:limit], "next_cursor": None, "total_items": len(items)})
        if path.startswith("/tools/execute/"):
            if self.execute_status != 200:
                return httpx.Response(self.execute_status, json={"message": "Rate limit exceeded."})
            body = json.loads(request.content)
            ran = path.rsplit("/", 1)[1]
            data = {"ran": ran, "got": body["arguments"], "echo": KEY}
            if "_CREATE_" in ran:
                data["documentId"] = "doc_made_0123456789"  # what a create call made
            return httpx.Response(200, json={"successful": True, "error": None, "log_id": "log_1", "data": data})
        return httpx.Response(404, json={"error": {"message": "not found"}})


@pytest.fixture
def fake_composio(home, monkeypatch):
    f = FakeComposio()
    monkeypatch.setattr(composio, "TRANSPORT", httpx.MockTransport(f))
    composio.forget()
    yield f
    composio.forget()


@pytest.fixture
def connected(home, fake_composio):
    composio.connect(home, KEY)
    return fake_composio


# -- settings and listing ---------------------------------------------------------------


def test_the_settings_are_checked():
    assert composio.check({}) == {"key": "COMPOSIO_API_KEY", "class": "external"}
    assert composio.check(None) is None
    for bad, words in [
        ({"key": KEY}, "never the key itself"),
        ({"class": "read-only"}, "internal or external"),
        ({"toolkits": []}, "unknown"),
    ]:
        with pytest.raises(SetupError, match=words):
            composio.check(bad)
    assert ToolSettings.from_dict({"composio": {}}).to_dict()["composio"] == {
        "key": "COMPOSIO_API_KEY",
        "class": "external",
    }
    assert composio.short_name("googledocs", "GOOGLEDOCS_CREATE_DOCUMENT") == "CREATE_DOCUMENT"
    assert composio.short_name("gmail", "SOMETHING_ELSE") == "SOMETHING_ELSE"


def test_a_key_that_does_not_work_is_never_kept(home, fake_composio):
    with pytest.raises(SetupError, match="refused the API key"):
        composio.connect(home, "ak_WRONG0123456789")
    with pytest.raises(SetupError, match="user key"):
        composio.connect(home, "uak_INVENTED0123456789")
    with pytest.raises(SetupError, match="Connect key"):
        composio.connect(home, "ck_INVENTED0123456789")
    assert keys.get_key(home, "COMPOSIO_API_KEY") is None
    assert "composio" not in mdfile.read(home.tools)[0]


def test_connecting_lists_each_toolkit_and_classes_its_tools(home, connected):
    assert keys.get_key(home, "COMPOSIO_API_KEY") == KEY
    assert mdfile.read(home.tools)[0]["composio"] == {"key": "COMPOSIO_API_KEY", "class": "external"}
    view = composio.view(home, load_tool_settings(home).composio)
    assert [(k["title"], k["active"], k["status"]) for k in view["toolkits"]] == [
        ("Gmail", False, "expired"),
        ("Google Docs", True, "active"),
    ]
    assert view["toolkits"][1]["tools"] == 4
    found, warnings = composio.tools(home, load_tool_settings(home))
    by = {t.name: t for t in found}
    assert by["composio.googledocs.GET_DOCUMENT_BY_ID"].effect == "read-only"  # by its name
    assert by["composio.googledocs.SUMMARISE"].effect == "read-only"  # by Composio's marking
    create = by["composio.googledocs.CREATE_DOCUMENT"]  # a change in the user's own documents: their work
    assert create.effect == "internal" and create.reason_key and "Google Docs, through Composio" in create.description
    assert by["composio.googledocs.SHARE_DOCUMENT"].effect == "external"  # it reaches other people
    assert any("Gmail (via Composio) isn't offered" in w and "expired" in w for w in warnings)
    assert keys.key_uses(home)["COMPOSIO_API_KEY"] == ["Composio"]


def test_toolsmd_can_class_a_tool_or_refuse_it(home, connected):
    meta, body = mdfile.read(home.tools)
    meta["tools"] = {
        **(meta.get("tools") or {}),
        "composio.googledocs.CREATE_DOCUMENT": {"class": "internal"},
        "composio.googledocs.SUMMARISE": {"permission": "refuse"},
    }
    mdfile.write(home.tools, meta, body)
    by = {t.name: t for t in composio.tools(home, load_tool_settings(home))[0]}
    assert by["composio.googledocs.CREATE_DOCUMENT"].effect == "internal"


# -- a call ---------------------------------------------------------------------------------


def test_a_run_uses_the_connected_account_and_never_returns_the_key(home, connected):
    kit = composio.toolkits(home, load_tool_settings(home).composio)[1]
    text = composio.run(
        home, load_tool_settings(home).composio, "GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": "d1"}, kit, "20261101_00"
    )
    sent = json.loads(connected.seen[-1].content)
    assert sent == {
        "arguments": {"id": "d1"},
        "connected_account_id": "ca_docs",
        "user_id": "ada",
        "version": "20261101_00",
    }
    assert KEY not in text and "[key removed]" in text


def test_composios_limits_are_said_plainly(home, connected):
    cfg = load_tool_settings(home).composio
    kit = composio.toolkits(home, cfg)[1]
    connected.execute_status = 429
    with pytest.raises(composio.ComposioNotReached, match="limit .* was reached"):
        composio.run(home, cfg, "GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": "d1"}, kit)
    connected.execute_status = 402
    with pytest.raises(ToolError, match="run out of allowance"):
        composio.run(home, cfg, "GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": "d1"}, kit)


# -- in a job -----------------------------------------------------------------------------


def test_every_crew_member_gets_it_lookups_and_own_work_run_and_sharing_waits(home, connected, job):
    load = fake.call(
        "tools__load",
        {
            "names": [
                "composio__googledocs__GET_DOCUMENT_BY_ID",
                "composio__googledocs__CREATE_DOCUMENT",
                "composio__googledocs__SHARE_DOCUMENT",
            ]
        },
    )
    look = fake.call("composio__googledocs__GET_DOCUMENT_BY_ID", {"id": "d1"})
    make = fake.call(
        "composio__googledocs__CREATE_DOCUMENT", {"title": "Invented notes", "reason": "The brief asks for a document."}
    )
    share = fake.call(
        "composio__googledocs__SHARE_DOCUMENT",
        {"document_id": "d1", "email": "bo@example.invalid", "reason": "Bo should see it."},
    )
    script = [
        fake.brief("researcher", "Read d1, make a document and share d1."),
        load,
        look,
        make,
        share,
        fake.result("Made it; sharing waits for approval."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    ran = [r.url.path for r in connected.seen if "/execute/" in r.url.path]
    assert ran == [
        "/api/v3.1/tools/execute/GOOGLEDOCS_GET_DOCUMENT_BY_ID",
        "/api/v3.1/tools/execute/GOOGLEDOCS_CREATE_DOCUMENT",
    ]  # the lookup and the work ran
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "composio.googledocs.SHARE_DOCUMENT" and a.status == "pending" and a.meta["class"] == "external"
    assert "composio_ready" in job.actions()
    assert KEY not in (job.job_dir / "journal.jsonl").read_text()

    done = outbox.approve(home, a)
    assert done.status == "executed", done.meta
    assert connected.seen[-1].url.path == "/api/v3.1/tools/execute/GOOGLEDOCS_SHARE_DOCUMENT"
    assert json.loads(connected.seen[-1].content)["arguments"] == {"document_id": "d1", "email": "bo@example.invalid"}


def test_an_approval_that_hits_a_limit_keeps_waiting(home, connected, job):
    make = fake.call(
        "composio__googledocs__SHARE_DOCUMENT",
        {"document_id": "d1", "email": "bo@example.invalid", "reason": "Bo should see it."},
    )
    script = [
        fake.brief("researcher", "Share a document."),
        fake.call("tools__load", {"names": ["composio__googledocs__SHARE_DOCUMENT"]}),
        make,
        fake.result("It waits."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    [a] = outbox.actions(job.job_dir)
    connected.execute_status = 429
    with pytest.raises(outbox.OutboxError, match="Composio could not be reached"):
        outbox.approve(home, a)
    assert outbox.actions(job.job_dir)[0].status == "pending"


# -- over serve ---------------------------------------------------------------------------


def test_connect_refresh_and_disconnect_over_http(serve, home, fake_composio):
    assert serve.get("/tools")["composio"] is None
    assert serve.http.put("/tools/composio", json={"key": "ak_WRONG0123456789"}).status_code == 400
    r = serve.http.put("/tools/composio", json={"key": KEY})
    assert r.status_code == 200, r.text
    assert KEY not in r.text
    account = r.json()["composio"]
    assert account["key_set"] and account["problem"] is None
    assert [k["title"] for k in account["toolkits"]] == ["Gmail", "Google Docs"]
    assert serve.http.post("/tools/composio/refresh").json()["composio"]["toolkits"][1]["tools"] == 4
    check = serve.get("/tools/composio/check")
    assert check["blocked"] is None and any("COMPOSIO_API_KEY" in w for w in check["warnings"])
    assert serve.http.delete("/tools/composio").json()["composio"] is None
    assert "composio" not in mdfile.read(home.tools)[0]
    assert keys.get_key(home, "COMPOSIO_API_KEY") is None


# -- approvals in briefed work --------------------------------------------------------------


def test_tools_are_classed_by_what_they_do_not_by_one_class_for_all_of_composio():
    sheets = [
        "CREATE_GOOGLE_SHEET1",
        "UPDATE_VALUES_BATCH",
        "FORMAT_CELL",
        "UPDATE_DIMENSION_PROPERTIES",
        "SET_DATA_VALIDATION_RULE",
        "UPDATE_SHEET_PROPERTIES",
        "SPREADSHEETS_VALUES_APPEND",
    ]
    for short in sheets:  # the calls once asked about one by one: the sheet the brief asked for, made and formatted
        assert composio.classify({}, short, "external", toolkit="googlesheets") == "internal", short
    for kit, short in [
        ("googlesheets", "DELETE_SHEET"),
        ("googledocs", "CREATE_PERMISSION"),
        ("googledrive", "SHARE_FILE"),
        ("gmail", "SEND_EMAIL"),
        ("gmail", "REPLY_TO_THREAD"),
        ("slack", "CHAT_POST_MESSAGE"),
        ("stripe", "CREATE_PAYMENT_INTENT"),
    ]:
        assert composio.classify({}, short, "internal", toolkit=kit) == "external", short  # risky, wherever
    assert composio.classify({}, "CREATE_EMAIL_DRAFT", "external", toolkit="gmail") == "internal"  # a draft
    assert composio.classify({}, "LIST_DRIVE_ITEM_PERMISSIONS", "external", toolkit="googledocs") == "read-only"
    assert composio.classify({}, "UPDATE_DEAL", "external", toolkit="hubspot") == "external"  # unknown: class
    assert composio.classify({}, "UPDATE_DEAL", "internal", toolkit="hubspot") == "internal"
    assert composio.classify({}, "FORMAT_CELL", "external", "external", "googlesheets") == "external"  # tools.md


def test_scope_counts_calls_on_one_item_as_one_piece_of_work():
    from ombro.outward import Scope, risky, target_of

    assert target_of({"spreadsheet_id": "s1", "range": "A1"}) == "s1" and target_of({"title": "x"}) is None
    assert risky("composio.googlesheets.DELETE_SHEET") and not risky("composio.googlesheets.FORMAT_CELL")
    assert risky("mcp.mail.sendMessage")  # camel case too
    s = Scope()
    s.done("composio.googlesheets.CREATE_GOOGLE_SHEET1", {"title": "M"}, {"spreadsheetId": "sheet_made_12345"})
    assert s.made == {"sheet_made_12345"}
    # Deleting inside what this job made is its own work; sharing it, or deleting elsewhere, is not.
    assert (
        s.effect("composio.googlesheets.DELETE_DIMENSION", {"spreadsheet_id": "sheet_made_12345"}, "external")
        == "internal"
    )
    assert (
        s.effect("composio.googlesheets.DELETE_DIMENSION", {"spreadsheet_id": "other_sheet_1"}, "external")
        == "external"
    )
    assert s.effect("composio.googledocs.SHARE_FILE", {"file_id": "sheet_made_12345"}, "external") == "external"
    # A yes in chat covers later calls on the same item in the same service, unless they are risky.
    s.done("composio.wiki.UPDATE_PAGE", {"page_id": "p1"}, {}, approved=True)
    assert s.covered("composio.wiki.FORMAT_PAGE", {"page_id": "p1"})
    assert not s.covered("composio.wiki.FORMAT_PAGE", {"page_id": "p2"})
    assert not s.covered("composio.other.FORMAT_PAGE", {"page_id": "p1"})
    assert not s.covered("composio.wiki.DELETE_PAGE", {"page_id": "p1"})


DELETE_DOC = {
    "slug": "GOOGLEDOCS_DELETE_DOCUMENT",
    "name": "Delete document",
    "description": "Deletes one.",
    "toolkit": {"slug": "googledocs", "name": "Google Docs"},
    "tags": [],
    "version": "20261101_00",
    "input_parameters": {
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
    },
}


def test_a_job_makes_and_edits_what_the_brief_asked_for_and_says_plainly_what_waits(home, connected, job):
    connected.kit_tools["googledocs"] = [*DOC_TOOLS, DELETE_DOC]
    composio.forget()
    names = ["CREATE_DOCUMENT", "DELETE_DOCUMENT", "SHARE_DOCUMENT"]
    load = fake.call("tools__load", {"names": [f"composio__googledocs__{n}" for n in names]})
    make = fake.call("composio__googledocs__CREATE_DOCUMENT", {"title": "Matrix", "reason": "The brief asks."})
    tidy = fake.call(
        "composio__googledocs__DELETE_DOCUMENT",
        {"document_id": "doc_made_0123456789", "reason": "A false start of my own."},
    )
    other = fake.call("composio__googledocs__DELETE_DOCUMENT", {"document_id": "d1", "reason": "Old."})
    share1 = fake.call(
        "composio__googledocs__SHARE_DOCUMENT",
        {"document_id": "d1", "email": "bo@example.invalid", "reason": "Bo asked."},
    )
    share2 = fake.call(
        "composio__googledocs__SHARE_DOCUMENT",
        {"document_id": "d1", "email": "cy@example.invalid", "reason": "Cy too."},
    )
    script = [
        fake.brief("researcher", "Make the matrix document."),
        load,
        make,
        tidy,
        other,
        share2,
        share1,
        fake.result("Made it; the rest waits."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    ran = [r.url.path.rsplit("/", 1)[1] for r in connected.seen if "/execute/" in r.url.path]
    assert ran == ["GOOGLEDOCS_CREATE_DOCUMENT", "GOOGLEDOCS_DELETE_DOCUMENT"]  # made it, and tidied its own
    # Deleting someone else's document waits; the two shares of that same document wait with it, as one.
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "composio.googledocs.DELETE_DOCUMENT" and a.arguments == {"document_id": "d1"}
    assert job.actions().count("staged_grouped") == 2
    text = (job.job_dir / "report.md").read_text()
    summary = text.split("## Summary", 1)[1].split("##", 1)[0]
    assert "**Stopped for your approval:**" in summary and a.ref in summary
    assert "Approving now runs only the waiting action, not the steps after" in summary
    assert "2 later call(s) on the same item were held with it" in text


def test_a_job_with_nothing_waiting_says_nothing_about_approval(home, connected, job):
    make = fake.call("composio__googledocs__CREATE_DOCUMENT", {"title": "Matrix", "reason": "The brief asks."})
    script = [
        fake.brief("researcher", "Make it."),
        fake.call("tools__load", {"names": ["composio__googledocs__CREATE_DOCUMENT"]}),
        make,
        fake.result("Made it."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    assert outbox.actions(job.job_dir) == []
    assert "Stopped for your approval" not in (job.job_dir / "report.md").read_text()


WIKI_TOOLS = [
    {
        "slug": f"WIKI_{n}",
        "name": n.title(),
        "description": f"{n.title()} a page.",
        "toolkit": {"slug": "wiki", "name": "Wiki"},
        "tags": [],
        "version": "20261101_00",
        "input_parameters": {
            "type": "object",
            "properties": {"page_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["page_id"],
        },
    }
    for n in ("UPDATE_PAGE", "FORMAT_PAGE", "DELETE_PAGE")
]


def test_in_chat_one_yes_covers_related_calls_on_the_same_item(home, connected):
    from test_chat import ChatSession

    connected.accounts.append(
        {
            "id": "ca_wiki",
            "status": "ACTIVE",
            "user_id": "ada",
            "toolkit": {"slug": "wiki"},
            "auth_config": {"id": "ac_3", "is_disabled": False},
        }
    )
    connected.kit_tools["wiki"] = WIKI_TOOLS
    composio.forget()
    meta, body = mdfile.read(home.tools)
    meta["tools"] = {"web.search": {"provider": "fake"}, "web.fetch": {"provider": "fake"}}
    mdfile.write(home.tools, meta, body)
    call = lambda n, page, why: fake.call(f"composio__wiki__{n}", {"page_id": page, "text": "x", "reason": why})  # noqa: E731
    session = ChatSession(home)
    session(
        [
            fake.call(
                "tools__load",
                {"names": [f"composio__wiki__{t}" for t in ("UPDATE_PAGE", "FORMAT_PAGE", "DELETE_PAGE")]},
            ),
            call("UPDATE_PAGE", "p1", "You asked."),
            call("FORMAT_PAGE", "p1", "Tidy it."),
            call("FORMAT_PAGE", "p1", "Tidy more."),
            call("DELETE_PAGE", "p1", "Remove it."),
            fake.say("Done."),
        ],
        ["Fill in the wiki page p1.", "y", "n"],
    )
    asked = [a for a in session.answers.asked if "Go ahead?" in a]
    assert len(asked) == 2  # once for the page, once more for deleting it
    ran = [r.url.path.rsplit("/", 1)[1] for r in connected.seen if "/execute/" in r.url.path]
    assert ran == ["WIKI_UPDATE_PAGE", "WIKI_FORMAT_PAGE", "WIKI_FORMAT_PAGE"]
    assert "covered by the user's earlier yes for the same item" in session.transcript


def test_what_a_create_call_made_is_read_from_its_result():
    from ombro.outward import targets_made

    assert targets_made(
        {"result": json.dumps({"spreadsheetId": "sheet_made_12345", "parents": [{"id": "folder_0123456"}]})}
    ) == {"sheet_made_12345"}
    assert targets_made('{"id": "page_made_123456", "parent": {"id": "parent_0123456"}}') == {"page_made_123456"}
    assert targets_made("no ids here") == set()


def test_an_unmarked_tools_name_makes_it_a_lookup_only_when_nothing_in_it_makes_or_changes_things():
    """GET_, FIND_ and the like count as lookups only where no other word says the
    tool also makes, shares, links or marks something; the rest keep their classing by name."""
    for short in ["GET_DOCUMENT_BY_ID", "LIST_DRIVE_ITEM_PERMISSIONS", "FETCH_EMAILS", "SEARCH_PAGES", "GET_POST"]:
        assert composio.class_of({}, short, "external", toolkit="gmail") == ("read-only", "name"), short
    for short, kit, want in [
        ("FIND_OR_CREATE_ROW", "googlesheets", "internal"),  # own documents: their work
        ("FIND_OR_CREATE_CONTACT", "hubspot", "external"),  # composio's class
        ("GET_SHARE_LINK", "googledrive", "external"),  # sharing reaches people
        ("GET_SHAREABLE_LINK", "dropbox", "internal"),
        ("FETCH_AND_MARK_READ", "gmail", "external"),
        ("FETCH_AND_MARK_READ", "slack", "internal"),
    ]:
        server = "internal" if kit == "slack" else "external"
        assert composio.classify({}, short, server, toolkit=kit) == want, short
    # Composio's own marking still wins, as does tools.md.
    assert composio.class_of({"tags": ["readOnlyHint"]}, "GET_SHARE_LINK", "external", toolkit="googledrive") == (
        "read-only",
        "marking",
    )
    assert composio.class_of({}, "GET_SHARE_LINK", "external", "read-only", "googledrive") == ("read-only", "tool")
    # A tool Composio marks (as not read-only) never counts as a lookup by its name.
    assert composio.class_of({"tags": ["destructiveHint"]}, "GET_DOCUMENT", "external", toolkit="gmail") == (
        "external",
        "default",
    )


def test_the_terminal_shows_each_composio_tools_class_and_where_it_came_from(home, connected):
    from ombro import cli

    rows = {r["tool"]: r for r in composio.tool_classes(home, load_tool_settings(home))}
    assert rows["composio.googledocs.GET_DOCUMENT_BY_ID"]["class_from"] == "name"
    assert rows["composio.googledocs.SUMMARISE"]["class_from"] == "marking"
    assert rows["composio.googledocs.CREATE_DOCUMENT"]["class"] == "internal"
    assert cli.main(["tools", "composio"]) == 0


def test_the_terminal_lists_composio_and_connects_refreshes_and_disconnects_it(home, fake_composio, capsys):
    """`ombro tools` and /tools show what the app's Tools page shows, and do what it does."""
    from ombro import cli, tool_cmd
    from test_ui import ScriptedUI

    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "Composio" in out and "not connected" in out

    ui = ScriptedUI(["ak_WRONG0123456789"])
    with pytest.raises(SetupError):
        tool_cmd.composio_flow(home, ui, "connect")
    assert load_tool_settings(home).composio is None

    ui = ScriptedUI([KEY])
    assert tool_cmd.composio_flow(home, ui, "connect") == 0
    assert ui.fields[0].kind == "secret"
    assert all(KEY not in e.text for e in ui.events)

    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "2 services connected" in out
    assert "Gmail (via Composio)" in out and "Google Docs (via Composio)" in out
    assert "4 tools, can change things or reach outside" in out

    ui = ScriptedUI([])
    assert tool_cmd.composio_flow(home, ui, "refresh") == 0
    assert any("Google Docs (via Composio)" in i for e in ui.events for i in e.items)

    ui = ScriptedUI([False])
    assert tool_cmd.composio_flow(home, ui, "disconnect") == 1
    assert load_tool_settings(home).composio is not None
    assert cli.main(["tools", "composio", "disconnect", "--yes"]) == 0
    assert load_tool_settings(home).composio is None
    assert keys.get_key(home, "COMPOSIO_API_KEY") is None
    with pytest.raises(SetupError):
        tool_cmd.composio_flow(home, ScriptedUI([]), "refresh")
