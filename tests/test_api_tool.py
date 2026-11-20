"""Web APIs as tools: settings, the guards on where a call may go,
the key, a job where lookups run and changes wait, Approvals sending one, and serve's form. The API
is a fake in this process; nothing reaches the network. All data is invented."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import LEAD_REPORT
from ombro import apitool, fake, keys, mdfile, outbox
from ombro.settings import SetupError, ToolSettings, load_limits, load_tool_settings
from ombro.tools import ToolError, ToolInputError
from test_serve_sessions import loopback, serve  # noqa: F401 - fixtures

KEY = "pk-INVENTED-0123456789"
BASE = "https://api.podcasts.invalid/v2"


class FakeApi:
    """Answers like a small podcast API and keeps every request it was sent."""

    def __init__(self):
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path.endswith("/moved"):
            return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"error": "no such episode"})
        if request.method == "GET":
            return httpx.Response(
                200, json={"episodes": [{"title": "Invented reservoir talk"}], "echo_key": KEY}
            )  # an API that echoes the key back
        return httpx.Response(201, json={"saved": json.loads(request.content or b"null")})


@pytest.fixture
def api(home, monkeypatch):
    fake_api = FakeApi()
    monkeypatch.setattr(apitool, "TRANSPORT", httpx.MockTransport(fake_api))
    keys.set_key(home, "PODCASTS_API_KEY", KEY)
    meta, body = mdfile.read(home.tools)
    meta["apis"] = {
        "podcasts": {
            "url": BASE,
            "key": "PODCASTS_API_KEY",
            "about": "Our invented podcast list.",
            "how_to": "GET /episodes?q=… searches episodes.",
            "class": "external",
            "max_calls": 5,
        }
    }
    mdfile.write(home.tools, meta, body)
    return fake_api


# -- settings -------------------------------------------------------------------------


def test_the_settings_are_checked():
    ok = apitool.check_apis({"podcasts": {"url": BASE + "/"}})
    assert ok["podcasts"] == {"url": BASE, "send_key": "bearer", "about": "", "class": "external"}
    for bad, words in [
        ({"url": "ftp://x"}, "https"),
        ({"url": BASE, "key": KEY}, "never the key itself"),
        ({"url": BASE, "class": "anything"}, "class"),
        ({"url": BASE + "?k=1"}, "just the address"),
        ({"url": BASE, "send_key": "cookie"}, "send_key"),
    ]:
        with pytest.raises(SetupError, match=words):
            apitool.check_apis({"podcasts": bad})
    # Any address is allowed, including one on your own network.
    assert apitool.check_apis({"nas": {"url": "http://192.168.1.20:8080/api"}})["nas"]["url"].startswith("http://192")
    assert ToolSettings.from_dict({"apis": {"podcasts": {"url": BASE}}}).to_dict()["apis"]["podcasts"]["url"] == BASE


def test_its_tools_and_call_limits(home, api):
    lim = load_limits(home)
    assert lim.max_calls_for("api.podcasts.get") == 5 and lim.max_calls_for("api.podcasts.request") == 5
    tools = {t.name: t for t in apitool.api_tools(home, load_tool_settings(home).apis)}
    assert tools["api.podcasts.get"].effect == "read-only"
    assert tools["api.podcasts.request"].effect == "external" and tools["api.podcasts.request"].reason_key
    assert "Our invented podcast list." in tools["api.podcasts.get"].description
    ro = apitool.api_tools(home, {"podcasts": {"url": BASE, "class": "read-only"}})
    assert [(t.name, t.effect) for t in ro] == [("api.podcasts.request", "read-only")]  # POST searches run too


# -- the guards --------------------------------------------------------------------------


def test_a_call_only_goes_under_the_apis_address():
    assert apitool.address(BASE, "/episodes", {"q": "reservoir"}) == BASE + "/episodes?q=reservoir"
    assert apitool.address(BASE, "episodes/12?full=1") == BASE + "/episodes/12?full=1"
    for path in ["https://evil.invalid/x", "//evil.invalid/x", "/../v1/admin", "/a/./b", "", "/x\\y", "/@evil"]:
        with pytest.raises(ToolInputError):
            apitool.address(BASE, path)


def test_the_key_is_added_and_never_comes_back(home, api):
    cfg = load_tool_settings(home).apis["podcasts"]
    out = apitool.call(home, "podcasts", cfg, "GET", "/episodes", {"q": "reservoir"})
    assert api.seen[-1].headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in out["reply"] and "[key removed]" in out["reply"] and out["status"] == 200
    query = {**cfg, "send_key": "query:api_key"}
    apitool.call(home, "podcasts", query, "GET", "/episodes")
    assert api.seen[-1].url.params["api_key"] == KEY and "Authorization" not in api.seen[-1].headers
    with pytest.raises(ToolError, match="redirect"):
        apitool.call(home, "podcasts", cfg, "GET", "/moved")
    with pytest.raises(ToolError, match="404"):
        apitool.call(home, "podcasts", cfg, "GET", "/missing")
    keys.remove_key(home, "PODCASTS_API_KEY")
    with pytest.raises(ToolError, match="isn't set"):
        apitool.call(home, "podcasts", cfg, "GET", "/episodes")


# -- in a job -----------------------------------------------------------------------------


def test_every_crew_member_gets_it_lookups_run_and_changes_wait(home, api, job):
    look = fake.call("api__podcasts__get", {"path": "/episodes", "query": {"q": "reservoir"}})
    save = fake.call(
        "api__podcasts__request",
        {
            "method": "POST",
            "path": "/playlists",
            "body": {"title": "Invented list"},
            "reason": "The brief asks for a playlist.",
        },
    )
    script = [
        fake.brief("researcher", "Find reservoir episodes and save a playlist."),
        look,
        save,
        fake.result("Found one; the playlist waits for approval."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    assert [r.method for r in api.seen] == ["GET"]  # the lookup ran; the change did not
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "api.podcasts.request" and a.status == "pending" and a.meta["class"] == "external"
    assert a.arguments == {"method": "POST", "path": "/playlists", "body": {"title": "Invented list"}}
    assert "apis_ready" in job.actions()
    assert KEY not in (job.job_dir / "journal.jsonl").read_text()

    done = outbox.approve(home, a)
    assert done.status == "executed" and api.seen[-1].method == "POST"
    assert api.seen[-1].url == httpx.URL(BASE + "/playlists")
    assert json.loads(api.seen[-1].content) == {"title": "Invented list"}


# -- in the brief ---------------------------------------------------------------------------

BRIEF = {
    "name": "podcast-playlist",
    "title": "Save a reservoir playlist",
    "done_looks_like": "One playlist.",
    "users": "Me.",
    "binding_constraint": "Invented episodes only.",
    "waste": "Duplicates.",
    "non_goals": "No new episodes.",
}


def test_the_brief_asks_what_an_external_api_may_do_without_you(home, api):
    from ombro.interview import ProposalError
    from ombro.interviews import BriefPurpose

    purpose = BriefPurpose(home, "default", load_limits(home), "Save a playlist of reservoir episodes.")
    asked = next(v for k, v in purpose.context() if k.startswith("External tools in the crew"))
    assert "- api.podcasts.request (the podcasts API: its calls that change things; lookups always run)" in asked
    assert "api.<name>.request" in json.dumps(purpose.tool())
    with pytest.raises(ProposalError, match="outward_preset"):
        purpose.build(BRIEF)
    # An API named on its own means its one tool that changes things.
    proposal = purpose.check(purpose.build({**BRIEF, "outward_preset": "balanced", "outward_allow": ["api.podcasts"]}))
    meta, _ = mdfile.parse(proposal.files[0].text)
    assert meta["outward"] == {"preset": "balanced", "allow": ["api.podcasts"]}
    from ombro import outward

    setting = outward.resolve(home, meta)
    assert setting["allow"] == ["api.podcasts.request"]
    assert outward.decide(setting, "api.podcasts.request", "external", load_tool_settings(home)) == "allow"


def test_an_api_that_only_reads_or_changes_your_own_things_is_not_asked_about(home, api):
    from ombro.interviews import BriefPurpose

    for cls in ("read-only", "internal"):
        meta, body = mdfile.read(home.tools)
        meta["apis"]["podcasts"]["class"] = cls
        mdfile.write(home.tools, meta, body)
        purpose = BriefPurpose(home, "default", load_limits(home), "Look up episodes.")
        assert not any(k.startswith("External tools") for k, _ in purpose.context()), cls


def test_the_briefs_answer_lets_an_api_change_run_in_the_job(home, api, job):
    """Allowed in the brief, the API's change runs at once rather than waiting in Approvals."""
    save = fake.call(
        "api__podcasts__request",
        {
            "method": "POST",
            "path": "/playlists",
            "body": {"title": "Invented list"},
            "reason": "The brief asks for a playlist.",
        },
    )
    script = [
        fake.brief("researcher", "Save a playlist."),
        save,
        fake.result("Saved it."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script, spec_meta={"outward": {"preset": "balanced", "allow": ["api.podcasts.request"]}}) == "completed"
    assert [r.method for r in api.seen] == ["POST"] and outbox.actions(job.job_dir) == []


# -- over serve ---------------------------------------------------------------------------


def test_an_api_is_added_edited_and_removed_over_http(serve, home):
    r = serve.http.post(
        "/tools/apis",
        json={
            "name": "Podcasts",
            "url": BASE,
            "key": KEY,
            "send_key": "header",
            "key_param": "X-API-Key",
            "about": "Invented list.",
            "access": "read-only",
            "max_calls": 12,
        },
    )
    assert r.status_code == 201, r.text
    assert KEY not in r.text
    row = r.json()["apis"][0]
    assert row == {
        "name": "podcasts",
        "url": BASE,
        "about": "Invented list.",
        "how_to": "",
        "access": "read-only",
        "get_looks_up": True,
        "send_key": "header:X-API-Key",
        "key": "PODCASTS_API_KEY",
        "key_set": True,
        "calls": 12,
    }
    assert keys.get_key(home, "PODCASTS_API_KEY") == KEY
    assert serve.http.post("/tools/apis", json={"name": "podcasts", "url": BASE}).status_code == 400  # taken
    assert serve.http.post("/tools/apis", json={"name": "x", "url": BASE, "send_key": "header"}).status_code == 400
    # An edit with the key left empty keeps it.
    edited = serve.http.put("/tools/apis/podcasts", json={"url": BASE + "/", "access": "internal"}).json()["apis"][0]
    assert edited["access"] == "internal" and edited["key"] == "PODCASTS_API_KEY" and edited["send_key"] == "bearer"
    assert edited["get_looks_up"] is True  # an API added before the question counts as yes
    assert (
        serve.http.put("/tools/apis/podcasts", json={"url": BASE, "access": "external", "get_looks_up": False}).json()[
            "apis"
        ][0]["get_looks_up"]
        is False
    )
    assert mdfile.read(home.tools)[0]["apis"]["podcasts"]["get_looks_up"] is False
    kept = serve.http.put("/tools/apis/podcasts", json={"url": BASE, "access": "external"}).json()["apis"][0]
    assert kept["get_looks_up"] is False  # an edit that doesn't say keeps the answer
    assert serve.get("/tools/apis/podcasts/check")["blocked"] is None
    assert serve.http.delete("/tools/apis/podcasts").json()["apis"] == []
    assert "apis" not in mdfile.read(home.tools)[0]


def test_the_terminal_lists_apis_too(home, api):
    from test_outbox import ombro

    out = ombro(home, "tools").stdout
    assert "Web APIs (tools.md `apis:`)" in out
    assert "podcasts" in out and "can change things or reach outside, max 5 calls, key PODCASTS_API_KEY" in out
    assert KEY not in out


# -- GETs that send ------------------------------------------------------------------------


def test_whether_gets_only_look_things_up_is_a_setting():
    assert "get_looks_up" not in apitool.check_apis({"bot": {"url": BASE}})["bot"]
    assert apitool.check_apis({"bot": {"url": BASE, "get_looks_up": False}})["bot"]["get_looks_up"] is False
    with pytest.raises(SetupError, match="true or false"):
        apitool.check_apis({"bot": {"url": BASE, "get_looks_up": "maybe"}})
    # A read-only API's every call is a lookup: the answer means nothing there.
    assert (
        "get_looks_up"
        not in apitool.check_apis({"bot": {"url": BASE, "class": "read-only", "get_looks_up": False}})["bot"]
    )
    assert apitool.entry_from("bot", {"url": BASE})["get_looks_up"] is True  # the default is yes
    assert apitool.entry_from("bot", {"url": BASE, "get_looks_up": "no"})["get_looks_up"] is False
    old = apitool.entry_from("bot", {"url": BASE, "get_looks_up": False})
    assert apitool.entry_from("bot", {"url": BASE}, old)["get_looks_up"] is False
    assert apitool.gets_look_up({"url": BASE}) is True  # stored before the question: yes


def test_an_api_whose_gets_send_has_no_lookup_tool():
    cfg = apitool.check_apis({"bot": {"url": BASE, "class": "external", "get_looks_up": False}})
    assert apitool.tool_names("bot", cfg["bot"]) == ["api.bot.request"]
    [tool] = apitool.api_tools(None, cfg)
    assert tool.name == "api.bot.request" and tool.effect == "external"
    assert "GET" in tool.input_schema["properties"]["method"]["enum"]
    assert "GET included" in tool.description


def test_a_get_that_names_a_sending_verb_or_carries_a_long_query_is_not_a_lookup():
    for path in [
        "/bot123/sendMessage?chat_id=1&text=hi",
        "/notify",
        "/hooks/abc",
        "/chat.postMessage",
        "/v1/trigger/build",
        "/webhook/T0/B0",
        "/api?action=send",
        "/sms/send",
    ]:
        assert apitool.get_check(path, None, 200), path
    for path in [
        "/episodes?q=reservoir",
        "/bot123/getUpdates",
        "/posts",
        "/messages/12",
        "/settings",
        "/updates",
        "/notifications",
    ]:
        assert apitool.get_check(path, None, 200) is None, path
    assert apitool.get_check("/episodes", {"q": "x" * 250}, 200).startswith("its query is")
    assert apitool.get_check("/episodes", {"q": "x" * 150}, 200) is None


def test_in_a_job_a_sending_get_goes_through_the_apis_class(home, api, job):
    send = fake.call("api__podcasts__get", {"path": "/bot1/sendMessage", "query": {"chat_id": "9", "text": "hi"}})
    leak = fake.call("api__podcasts__get", {"path": "/episodes", "query": {"q": "invented summary " * 20}})
    again = fake.call(
        "api__podcasts__request",
        {
            "method": "GET",
            "path": "/bot1/sendMessage",
            "query": {"chat_id": "9", "text": "hi"},
            "reason": "The brief asks to tell the user.",
        },
    )
    look = fake.call("api__podcasts__get", {"path": "/episodes", "query": {"q": "reservoir"}})
    script = [
        fake.brief("researcher", "Find reservoir episodes and tell me."),
        send,
        leak,
        again,
        look,
        fake.result("Found one; the message waits for approval."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    assert [r.url.path for r in api.seen] == ["/v2/episodes"]  # only the ordinary lookup ran
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "api.podcasts.request" and a.meta["class"] == "external"
    assert a.arguments["method"] == "GET" and a.arguments["path"] == "/bot1/sendMessage"
    done = outbox.approve(home, a)
    assert done.status == "executed" and api.seen[-1].method == "GET"
    assert api.seen[-1].url.path == "/v2/bot1/sendMessage" and api.seen[-1].url.params["text"] == "hi"


def test_in_a_job_an_api_whose_gets_send_waits_for_every_get(home, api, job):
    meta, body = mdfile.read(home.tools)
    meta["apis"]["podcasts"]["get_looks_up"] = False
    mdfile.write(home.tools, meta, body)
    get = fake.call(
        "api__podcasts__request",
        {"method": "GET", "path": "/episodes", "query": {"q": "reservoir"}, "reason": "The brief asks for episodes."},
    )
    script = [
        fake.brief("researcher", "Find reservoir episodes."),
        get,
        fake.result("It waits."),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ]
    assert job(script) == "completed"
    assert api.seen == []
    [a] = outbox.actions(job.job_dir)
    assert a.tool == "api.podcasts.request" and a.arguments["method"] == "GET"


def test_the_terminal_asks_whether_gets_only_look_things_up(home):
    from ombro import tool_cmd
    from test_ui import ScriptedUI

    def no(f):
        assert f.label == tool_cmd.GET_QUESTION and f.default is True
        return False

    ui = ScriptedUI(
        [
            BASE,
            "An invented bot.",
            lambda f: next(o.key for o in f.options if o.label.startswith("external")),
            no,
            "",
            "y",
        ]
    )
    assert tool_cmd.api_flow(home, ui, "add", "bot", {}) == 0
    assert any("GET requests: can send or change things" in str(e.to_dict()) for e in ui.events)
    assert mdfile.read(home.tools)[0]["apis"]["bot"]["get_looks_up"] is False
