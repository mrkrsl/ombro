"""The MCP client and the permission rules. The only server is Ombro's fake
one (a local process); remote servers are stood in for by a mock transport."""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from conftest import fake_sent, use_fake_mcp
from ombro import mdfile, outward
from ombro.mcp import McpCallLost, McpClient, McpError, McpNotReached, ServerConfig, result_text
from ombro.settings import SetupError, ToolSettings, load_tool_settings


def fake_server(record, **env) -> ServerConfig:
    return ServerConfig(name="fake", command=sys.executable, args=("-m", "ombro.fake_mcp"),
                        env={"OMBRO_FAKE_MCP_RECORD": str(record), **env})


# -- the client ---------------------------------------------------------------


def test_stdio_client_lists_and_calls_tools(home, tmp_path):
    record = tmp_path / "sent.jsonl"
    with McpClient(home, fake_server(record)) as c:
        tools = {t.name: t for t in c.list_tools()}
        assert set(tools) == {"find_rows", "add_row", "post_message", "fire_hook"}
        assert tools["find_rows"].annotations == {"readOnlyHint": True}
        result = c.call_tool("post_message", {"channel": "news", "text": "Invented news."})
    assert "Would have posted to news" in result_text(result)
    assert fake_sent(record) == [{"ts": fake_sent(record)[0]["ts"], "tool": "post_message",
                                  "arguments": {"channel": "news", "text": "Invented news."}}]


def test_a_server_that_cannot_start_is_not_reached(home):
    cfg = ServerConfig(name="gone", command="/nonexistent/invented-mcp-server")
    with pytest.raises(McpNotReached):
        McpClient(home, cfg).start()


def test_a_server_that_fails_to_start_shows_its_last_output_with_keys_hidden(home):
    home.env.write_text("INVENTED_TOKEN=abc123secret\n")
    script = ("import os, sys; sys.stderr.write('starting\\nToken is ' + os.environ['T'] + '\\n'"
              " + 'ModuleNotFoundError: invented.module\\n'); sys.exit(1)")
    cfg = ServerConfig(name="broken", command=sys.executable, args=("-c", script), env={"T": "${INVENTED_TOKEN}"})
    with pytest.raises(McpNotReached) as caught:
        McpClient(home, cfg).start()
    msg = str(caught.value)
    assert "Its last output:" in msg and "ModuleNotFoundError: invented.module" in msg
    assert "abc123secret" not in msg and "[hidden]" in msg


def test_a_missing_key_stops_the_server_before_it_starts(home, tmp_path):
    cfg = ServerConfig(name="fake", command=sys.executable, args=("-m", "ombro.fake_mcp"),
                       env={"TOKEN": "${INVENTED_SERVER_KEY}"})
    with pytest.raises(McpNotReached, match="INVENTED_SERVER_KEY"):
        McpClient(home, cfg).start()


def test_a_call_with_no_answer_is_lost_not_unsent(home, tmp_path):
    record = tmp_path / "sent.jsonl"
    cfg = ServerConfig(name="fake", command=sys.executable, args=("-m", "ombro.fake_mcp"),
                       env={"OMBRO_FAKE_MCP_RECORD": str(record), "OMBRO_FAKE_MCP_SLEEP": "3"}, timeout=0.5)
    with McpClient(home, cfg) as c:
        with pytest.raises(McpCallLost):
            c.call_tool("fire_hook", {})
    assert [e["tool"] for e in fake_sent(record)] == ["fire_hook"]


def test_http_transport_with_json_and_event_stream_answers(home):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            seen.append(("DELETE", request.headers.get("mcp-session-id"), None))
            return httpx.Response(200)
        msg = json.loads(request.content)
        seen.append((msg.get("method"), request.headers.get("mcp-session-id"), request.headers.get("authorization")))
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "invented"}}},
                headers={"Mcp-Session-Id": "sess-1"})
        if msg["method"] == "tools/list":
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "note", "inputSchema": {}}]}}
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(body)}\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"],
                                         "result": {"content": [{"type": "text", "text": "noted"}]}})

    keys = home.env
    keys.write_text("INVENTED_TOKEN=abc123\n")
    cfg = ServerConfig(name="remote", url="https://mcp.invalid/mcp", headers={"Authorization": "Bearer ${INVENTED_TOKEN}"})
    with McpClient(home, cfg, transport=httpx.MockTransport(handle)) as c:
        assert [t.name for t in c.list_tools()] == ["note"]
        assert result_text(c.call_tool("note", {})) == "noted"
    assert seen[0] == ("initialize", None, "Bearer abc123")
    assert seen[-1] == ("DELETE", "sess-1", None)  # the session is closed
    assert all(s[1] == "sess-1" for s in seen[1:] if s[0] != "initialize")


def _remote(answer_call):
    """A mock remote server that starts properly and answers tools/call with `answer_call`."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {}}})
        return answer_call(request)

    return httpx.MockTransport(handle)


def _raise(exc):
    def answer(request):
        raise exc("invented failure", request=request)
    return answer


@pytest.mark.parametrize("answer, expected", [
    (_raise(httpx.ConnectError), McpNotReached),  # never left the machine
    (_raise(httpx.ConnectTimeout), McpNotReached),
    (lambda r: httpx.Response(401), McpNotReached),  # refused before it was handled
    (lambda r: httpx.Response(404), McpNotReached),
    (_raise(httpx.ReadError), McpCallLost),  # sent; the answer was lost
    (_raise(httpx.RemoteProtocolError), McpCallLost),
    (_raise(httpx.ReadTimeout), McpCallLost),
    (lambda r: httpx.Response(500), McpCallLost),  # the server failed while handling it
    (lambda r: httpx.Response(503), McpCallLost),
])
def test_remote_failures_after_sending_are_lost_not_unsent(home, answer, expected):
    cfg = ServerConfig(name="remote", url="https://mcp.invalid/mcp")
    sent: list = []
    with McpClient(home, cfg, transport=_remote(answer)) as c:
        with pytest.raises(McpError) as caught:
            c.call_tool("note", {}, sent=sent)
    assert type(caught.value) is expected
    if expected is McpCallLost:
        assert sent  # so an approval marks it uncertain and never retries it


def test_server_errors_are_reported(home, tmp_path):
    with McpClient(home, fake_server(tmp_path / "r.jsonl", OMBRO_FAKE_MCP_FAIL="add_row")) as c:
        result = c.call_tool("add_row", {"sheet": "S", "values": ["x"]})
        assert result["isError"] and "invented failure" in result_text(result)
        with pytest.raises(McpError):
            c._call("no/such/method", {}, 5)


# -- tools.md ------------------------------------------------------------------


def test_tools_md_servers_and_mcp_settings_are_checked(home):
    good = {"servers": {"sheets": {"command": "uvx", "args": ["invented-sheets"], "env": {"K": "${K}"},
                                   "class": "work"}},
            "tools": {"mcp.sheets.add_rows": {"class": "external", "max_calls": 3},
                      "mcp.sheets.delete_sheet": {"permission": "refuse"}}}
    s = ToolSettings.from_dict(good)
    assert s.servers["sheets"]["command"] == "uvx" and s.tools["mcp.sheets.add_rows"]["class"] == "external"
    assert s.servers["sheets"]["class"] == "work"
    for bad, msg in [
        ({"servers": {"x": {}}}, "either `command`"),
        ({"servers": {"x": {"command": "a", "url": "https://b"}}}, "not both"),
        ({"servers": {"Bad Name": {"command": "a"}}}, "server name"),
        ({"tools": {"mcp.x.y": {"permission": "allow"}}}, "only be `refuse`"),
        ({"tools": {"mcp.x.y": {"class": "write"}}}, "class for"),
        ({"tools": {"mcp.x.y": {"class": "send"}}}, "renamed; use 'external'"),  # stage 6 names
        ({"tools": {"mcp.x.y": {"class": "change"}}}, "renamed; use 'work'"),
        ({"servers": {"x": {"command": "a", "class": "read"}}}, "work or external"),
        ({"tools": {"web.search": {"class": "read"}}}, "only to MCP tools"),
    ]:
        with pytest.raises(SetupError, match=msg):
            ToolSettings.from_dict(bad)


# -- permissions ---------------------------------------------------------------


def test_classes_follow_markings_then_the_server_then_the_tool():
    c = outward.classify_with_source
    assert c({"readOnlyHint": True}) == ("read", "marking")
    assert c({"openWorldHint": False}) == ("work", "marking")  # the server says it reaches nothing outside
    assert c({}) == ("external", "default")  # changes something, not classed: the safe default
    assert c({"readOnlyHint": False, "destructiveHint": False}) == ("external", "default")  # destructive is not the test
    assert c({}, server_class="work") == ("work", "server")
    assert c({"openWorldHint": False}, server_class="external") == ("external", "server")
    assert c({"readOnlyHint": True}, server_class="external") == ("read", "marking")  # reading stays reading
    assert c({"readOnlyHint": True}, "external", "work") == ("external", "tool")  # the tool's own line wins
    assert c({}, "work") == ("work", "tool")
    s = ToolSettings.from_dict({"servers": {"sheets": {"command": "x", "class": "work"}},
                                "tools": {"mcp.sheets.share": {"class": "external"}}})
    assert outward.tool_class(s, "mcp.sheets.update_cells", {"destructiveHint": True}) == ("work", "server")
    assert outward.tool_class(s, "mcp.sheets.share", {}) == ("external", "tool")
    assert outward.tool_class(s, "mcp.other.x", {}) == ("external", "default")


def test_presets_by_class():
    s = ToolSettings()
    for preset, expect in [("manual", ("allow", "stage", "stage")), ("balanced", ("allow", "allow", "stage")),
                           ("auto", ("allow", "allow", "allow"))]:
        got = tuple(outward.decide({"preset": preset}, "mcp.x.t", c, s) for c in ("read", "work", "external"))
        assert got == expect, preset


def test_layers_default_then_preferences_then_spec(home):
    assert outward.resolve(home, {})["preset"] == "balanced"
    meta, body = mdfile.read(home.preferences_config)
    mdfile.write(home.preferences_config, {**meta, "outward": "manual"}, body)
    assert outward.resolve(home, {}) == {"preset": "manual", "source": "preferences", "allow": [], "stage": []}
    r = outward.resolve(home, {"outward": {"allow": ["mcp.x.post"]}})
    assert r["preset"] == "manual" and r["allow"] == ["mcp.x.post"] and r["source"] == "spec"
    assert outward.resolve(home, {"outward": "auto"})["preset"] == "auto"
    with pytest.raises(SetupError, match="preset"):
        outward.resolve(home, {"outward": "yolo"})
    with pytest.raises(SetupError, match="both"):
        outward.resolve(home, {"outward": {"allow": ["mcp.x.a"], "stage": ["mcp.x.a"]}})


def test_refuse_in_tools_md_beats_every_layer(home):
    s = ToolSettings.from_dict({"tools": {"mcp.x.post": {"permission": "refuse"}}})
    assert outward.decide({"preset": "auto", "allow": ["mcp.x.post"]}, "mcp.x.post", "external", s) == "refuse"
    assert outward.decide({"preset": "auto"}, "mcp.x.other", "external", s) == "allow"


def test_spec_choices_beat_the_preset():
    s = ToolSettings()
    setting = {"preset": "balanced", "allow": ["mcp.x.post"], "stage": ["mcp.x.add"]}
    assert outward.decide(setting, "mcp.x.post", "external", s) == "allow"
    assert outward.decide(setting, "mcp.x.add", "work", s) == "stage"
    assert outward.decide({"preset": "balanced"}, "mcp.x.odd", "unknown", s) == "stage"  # unknown counts as external


def test_pool_offers_only_named_tools_and_adds_a_reason(home, tmp_path):
    use_fake_mcp(home, tmp_path / "r.jsonl")
    pool = outward.McpPool(home, load_tool_settings(home))
    try:
        tools, warnings = pool.tools(["mcp.fake.post_message", "mcp.fake.find_rows", "mcp.fake.nope",
                                      "mcp.other.x"])
    finally:
        pool.close()
    by = {t.name: t for t in tools}
    assert set(by) == {"mcp.fake.post_message", "mcp.fake.find_rows"}
    post = by["mcp.fake.post_message"]
    assert post.effect == "external" and post.reason_key == "reason"
    assert "reason" in post.parameters["required"] and "reason" not in post.input_schema["properties"]
    assert by["mcp.fake.find_rows"].reason_key is None
    assert any("no tool named 'nope'" in w for w in warnings)
    assert any("no MCP server named 'other'" in w for w in warnings)
