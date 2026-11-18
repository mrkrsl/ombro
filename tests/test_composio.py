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
    {"slug": "GOOGLEDOCS_GET_DOCUMENT_BY_ID", "name": "Get document", "description": "Reads a document.",
     "toolkit": {"slug": "googledocs", "name": "Google Docs"}, "tags": [], "version": "20261101_00",
     "input_parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"slug": "GOOGLEDOCS_CREATE_DOCUMENT", "name": "Create document", "description": "Creates a document.",
     "toolkit": {"slug": "googledocs", "name": "Google Docs"}, "tags": ["important"], "version": "20261101_00",
     "input_parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    {"slug": "GOOGLEDOCS_SUMMARISE", "name": "Summarise", "description": "Marked read-only by Composio.",
     "toolkit": {"slug": "googledocs", "name": "Google Docs"}, "tags": ["readOnlyHint"], "version": "20261101_00",
     "input_parameters": {"type": "object", "properties": {}}},
]


class FakeComposio:
    """Answers like Composio's v3.1 API for one invented account, and keeps every request."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.execute_status = 200
        self.accounts = [
            {"id": "ca_docs", "status": "ACTIVE", "user_id": "ada", "toolkit": {"slug": "googledocs"},
             "auth_config": {"id": "ac_1", "is_disabled": False}},
            {"id": "ca_old", "status": "EXPIRED", "user_id": "ada", "toolkit": {"slug": "gmail"},
             "auth_config": {"id": "ac_2", "is_disabled": False}},
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
            items = DOC_TOOLS if kit == "googledocs" else []
            limit = int(request.url.params.get("limit") or 1000)
            return httpx.Response(200, json={"items": items[:limit], "next_cursor": None, "total_items": len(items)})
        if path.startswith("/tools/execute/"):
            if self.execute_status != 200:
                return httpx.Response(self.execute_status, json={"message": "Rate limit exceeded."})
            body = json.loads(request.content)
            return httpx.Response(200, json={"successful": True, "error": None, "log_id": "log_1",
                                             "data": {"ran": path.rsplit("/", 1)[1], "got": body["arguments"],
                                                      "echo": KEY}})
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
    for bad, words in [({"key": KEY}, "never the key itself"), ({"class": "read-only"}, "internal or external"),
                       ({"toolkits": []}, "unknown")]:
        with pytest.raises(SetupError, match=words):
            composio.check(bad)
    assert ToolSettings.from_dict({"composio": {}}).to_dict()["composio"] == {"key": "COMPOSIO_API_KEY",
                                                                             "class": "external"}
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
        ("Gmail", False, "expired"), ("Google Docs", True, "active")]
    assert view["toolkits"][1]["tools"] == 3
    found, warnings = composio.tools(home, load_tool_settings(home))
    by = {t.name: t for t in found}
    assert by["composio.googledocs.GET_DOCUMENT_BY_ID"].effect == "read-only"  # by its name
    assert by["composio.googledocs.SUMMARISE"].effect == "read-only"  # by Composio's marking
    create = by["composio.googledocs.CREATE_DOCUMENT"]
    assert create.effect == "external" and create.reason_key and "Google Docs, through Composio" in create.description
    assert any("Gmail (via Composio) isn't offered" in w and "expired" in w for w in warnings)
    assert keys.key_uses(home)["COMPOSIO_API_KEY"] == ["Composio"]


def test_toolsmd_can_class_a_tool_or_refuse_it(home, connected):
    meta, body = mdfile.read(home.tools)
    meta["tools"] = {**(meta.get("tools") or {}),
                     "composio.googledocs.CREATE_DOCUMENT": {"class": "internal"},
                     "composio.googledocs.SUMMARISE": {"permission": "refuse"}}
    mdfile.write(home.tools, meta, body)
    by = {t.name: t for t in composio.tools(home, load_tool_settings(home))[0]}
    assert by["composio.googledocs.CREATE_DOCUMENT"].effect == "internal"


# -- a call ---------------------------------------------------------------------------------


def test_a_run_uses_the_connected_account_and_never_returns_the_key(home, connected):
    kit = composio.toolkits(home, load_tool_settings(home).composio)[1]
    text = composio.run(home, load_tool_settings(home).composio, "GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": "d1"}, kit,
                        "20261101_00")
    sent = json.loads(connected.seen[-1].content)
    assert sent == {"arguments": {"id": "d1"}, "connected_account_id": "ca_docs", "user_id": "ada",
                    "version": "20261101_00"}
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


def test_every_crew_member_gets_it_lookups_run_and_changes_wait(home, connected, job):
    load = fake.call("tools__load", {"names": ["composio__googledocs__GET_DOCUMENT_BY_ID",
                                               "composio__googledocs__CREATE_DOCUMENT"]})
    look = fake.call("composio__googledocs__GET_DOCUMENT_BY_ID", {"id": "d1"})
    make = fake.call("composio__googledocs__CREATE_DOCUMENT", {"title": "Invented notes",
                                                               "reason": "The brief asks for a document."})
    script = [fake.brief("researcher", "Read d1 and make a document."), load, look, make,
              fake.result("Read it; the new document waits for approval."), fake.finish(), fake.keep_notes(),
              LEAD_REPORT]
    assert job(script) == "completed"
    ran = [r.url.path for r in connected.seen if "/execute/" in r.url.path]
    assert ran == ["/api/v3.1/tools/execute/GOOGLEDOCS_GET_DOCUMENT_BY_ID"]  # the lookup ran; the change did not
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "composio.googledocs.CREATE_DOCUMENT" and a.status == "pending" and a.meta["class"] == "external"
    assert "composio_ready" in job.actions()
    assert KEY not in (job.job_dir / "journal.jsonl").read_text()

    done = outbox.approve(home, a)
    assert done.status == "executed", done.meta
    assert connected.seen[-1].url.path == "/api/v3.1/tools/execute/GOOGLEDOCS_CREATE_DOCUMENT"
    assert json.loads(connected.seen[-1].content)["arguments"] == {"title": "Invented notes"}


def test_an_approval_that_hits_a_limit_keeps_waiting(home, connected, job):
    make = fake.call("composio__googledocs__CREATE_DOCUMENT", {"title": "Invented notes",
                                                               "reason": "The brief asks for a document."})
    script = [fake.brief("researcher", "Make a document."),
              fake.call("tools__load", {"names": ["composio__googledocs__CREATE_DOCUMENT"]}), make,
              fake.result("It waits."), fake.finish(), fake.keep_notes(), LEAD_REPORT]
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
    assert serve.http.post("/tools/composio/refresh").json()["composio"]["toolkits"][1]["tools"] == 3
    check = serve.get("/tools/composio/check")
    assert check["blocked"] is None and any("COMPOSIO_API_KEY" in w for w in check["warnings"])
    assert serve.http.delete("/tools/composio").json()["composio"] is None
    assert "composio" not in mdfile.read(home.tools)[0]
    assert keys.get_key(home, "COMPOSIO_API_KEY") is None

