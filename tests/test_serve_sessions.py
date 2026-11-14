"""`ombro serve`, stage 3: a chat, a brief, a started job, its progress and an approval, driven over
HTTP and the socket against a real serve on 127.0.0.1, with the fake model. All data is invented.

The chat talks to a scripted client in this process; the job runs detached, as it would from the app,
and reads its own script from the home."""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
from websockets.sync.client import connect

from conftest import LEAD_REPORT, fake_sent, use_fake_mcp, write_role
from ombro import fake, keys, mdfile, outbox
from ombro.fake import ScriptedClient

APP = "tauri://localhost"
TOKEN = "t0ken-for-session-tests"
_connect = socket.socket.connect
_create_connection = socket.create_connection
_getaddrinfo = socket.getaddrinfo


@pytest.fixture
def loopback(monkeypatch):
    """These tests talk to a serve they started, so they may reach 127.0.0.1 and nothing else."""
    def only_loopback(real):
        def call(*args, **kwargs):
            address = kwargs.get("address") or next((a for a in args if isinstance(a, tuple)), None)
            if not address or address[0] != "127.0.0.1":
                raise AssertionError("a test tried to use the network")
            return real(*args, **kwargs)
        return call

    def resolve(host, *args, **kwargs):
        if host != "127.0.0.1":
            raise AssertionError("a test tried to use the network")
        return _getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", only_loopback(_connect))
    monkeypatch.setattr(socket, "create_connection", only_loopback(_create_connection))
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


class Served:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}/events"
        self.http = httpx.Client(base_url=self.base, headers={"Authorization": f"Bearer {TOKEN}", "Origin": APP},
                                 timeout=10)

    def get(self, path: str):
        r = self.http.get(path)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, path: str, body: dict | None = None, status: int = 200):
        r = self.http.post(path, json=body or {})
        assert r.status_code == status, r.text
        return r.json()

    def socket(self) -> "Socket":
        return Socket(connect(self.ws_url, subprotocols=["ombro", TOKEN], origin=APP, open_timeout=5, legacy=True))


class Socket:
    def __init__(self, ws):
        self.ws = ws
        self.seen: list[dict] = []

    def send(self, message: dict) -> None:
        self.ws.send(json.dumps(message))

    def until(self, test, timeout: float = 20.0) -> dict:
        """Read messages until one passes the test; return it. Everything read is kept in `seen`."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                m = json.loads(self.ws.recv(timeout=max(0.05, end - time.monotonic())))
            except TimeoutError:
                break
            self.seen.append(m)
            if test(m):
                return m
        raise AssertionError(f"not seen in {timeout}s; saw: {[_brief(m) for m in self.seen[-15:]]}")

    def close(self) -> None:
        self.ws.close()


def _brief(m: dict) -> str:
    e = m.get("event") or {}
    return f"{m.get('type')}:{e.get('type', m.get('state', ''))}:{str(e.get('text') or m.get('line') or '')[:60]}"


def event(kind: str, sid: str | None = None):
    return lambda m: m.get("type") == "event" and m["event"]["type"] == kind and (sid is None or m["session"] == sid)


def state(s: str, sid: str):
    return lambda m: m.get("type") == "session" and m["session"] == sid and m["state"] == s


def question(sid: str):
    return lambda m: m.get("type") == "question" and m["session"] == sid


@pytest.fixture
def serve(home, loopback):
    """A real serve in this process, on a free port, with the chat's model scripted here."""
    import uvicorn

    from ombro.serve import bind
    from ombro.serve.app import build_app

    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED-0123456789")
    client = ScriptedClient([])
    app = build_app(TOKEN, home=home, client_factory=lambda spec: client, titles=False, poll_seconds=0.2)
    sock = bind(0)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", loop="asyncio", http="h11",
                                           ws="websockets-sansio", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    s = Served(sock.getsockname()[1])
    s.client = client
    s.app = app
    yield s
    s.http.close()
    server.should_exit = True
    thread.join(timeout=10)


def _script(client: ScriptedClient, entries: list) -> None:
    client._queue.extend(entries)


def test_a_chat_over_http_and_the_socket(serve):
    _script(serve.client, [fake.say("Hello. I'm the Lead, and this is invented.")])
    ws = serve.socket()
    assert ws.until(lambda m: m["type"] == "event")["event"]["text"].startswith("Ombro ")  # the hello
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    assert serve.post(f"/sessions/{sid}/input", {"text": "Hello there"}) == {"handled": True}
    said = ws.until(event("lead", sid))
    assert said["event"]["text"].startswith("Hello. I'm the Lead") and said["seq"] > 0
    ws.until(state("idle", sid))
    assert serve.get(f"/sessions/{sid}")["state"] == "idle"
    ws.close()


def test_a_screen_command_is_routed_not_run(serve):
    sid = serve.post("/sessions", status=201)["session"]["id"]
    assert serve.post(f"/sessions/{sid}/input", {"text": "/approvals"}) == {
        "handled": False, "route": {"where": "screen", "command": "/approvals", "screen": "approvals", "args": ""}}
    assert serve.post(f"/sessions/{sid}/input", {"text": "/theme"})["route"]["where"] == "app"
    names = [c["name"] for c in serve.get("/commands")]
    assert "/brief" in names and "/approvals" in names and "/setup" not in names


def test_a_question_survives_a_reconnect_and_a_bad_answer(serve):
    from ombro import mdfile as md

    md.write(serve.app.state.serve.home.specs_dir / "old-task.md", {"name": "old-task", "crews": ["default"]},
             "# An invented old task\n")
    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "/start old-task"})
    q = ws.until(question(sid))
    assert q["field"]["kind"] == "yesno" and q["field"]["name"] == "start_now"
    ws.close()

    # A window that comes back is sent what it missed, the open question included.
    again = serve.socket()
    again.send({"type": "subscribe", "sessions": {sid: 0}})
    replayed = again.until(question(sid))
    assert replayed["id"] == q["id"] and serve.get(f"/sessions/{sid}")["question"]["id"] == q["id"]

    bad = serve.post(f"/sessions/{sid}/answer", {"id": q["id"], "value": "perhaps"})
    assert bad["ok"] is False and "y or n" in bad["problem"]
    assert serve.get(f"/sessions/{sid}")["question"]["id"] == q["id"]  # still open
    assert serve.post(f"/sessions/{sid}/answer", {"id": q["id"], "value": "n"})["ok"] is True
    again.until(lambda m: m.get("type") == "question_closed" and m["id"] == q["id"])
    said = again.until(lambda m: m.get("type") == "event" and "Start it later" in (m["event"].get("text") or ""))
    assert "ombro start --spec old-task" in said["event"]["text"]
    assert serve.http.post(f"/sessions/{sid}/answer", json={"id": q["id"], "value": "y"}).status_code == 404
    again.close()


def test_interrupting_a_question_stops_the_work(serve):
    from ombro import mdfile as md

    md.write(serve.app.state.serve.home.specs_dir / "old-task.md", {"name": "old-task", "crews": ["default"]},
             "# An invented old task\n")
    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "/start old-task"})
    ws.until(question(sid))
    assert serve.http.post(f"/sessions/{sid}/input", json={"text": "Hello"}).status_code == 409  # busy
    serve.post(f"/sessions/{sid}/interrupt")
    ws.until(lambda m: m.get("type") == "event" and m["event"].get("text") == "Stopped.")
    ws.until(state("idle", sid))
    assert serve.http.delete(f"/sessions/{sid}").status_code == 200
    assert serve.http.get(f"/sessions/{sid}").status_code == 404
    ws.close()


SPEC = {"name": "invented-news", "title": "Post invented reservoir news", "crews": ["default"],
        "done_looks_like": "One post in the news channel.", "users": "Me, to see it went out.",
        "binding_constraint": "Invented news only.", "waste": "More than one post.", "non_goals": "No replies.",
        "outward_preset": "balanced"}
ALL = ["mcp.fake.find_rows", "mcp.fake.add_row", "mcp.fake.post_message", "mcp.fake.fire_hook"]


def test_brief_job_progress_and_approval_end_to_end(serve, home, tmp_path):
    """The whole of stage 3's test: brief a task in a chat, say yes, follow the job, approve what it asks."""
    record = tmp_path / "fake-sent.jsonl"
    use_fake_mcp(home, record)
    write_role(home, "poster", kind="helper", tools=ALL, max_steps=12)
    job_script = [fake.brief("poster", "Post the news."),
                  fake.call("mcp__fake__post_message", {"channel": "news", "text": "Invented reservoir news.",
                                                        "reason": "The spec asks for one post."}),
                  fake.result("Asked to post it."), fake.finish(), fake.keep_notes(), LEAD_REPORT]
    (home.root / "script.json").write_text(json.dumps(job_script))
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json"}, "cheap": "default"}})
    _script(serve.client, [fake.call("propose", SPEC)])  # the brief interview, in this process

    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "/brief"})
    accept = ws.until(question(sid))
    assert accept["field"]["kind"] == "choice"
    serve.post(f"/sessions/{sid}/answer", {"id": accept["id"], "value": "a"})
    start = ws.until(lambda m: question(sid)(m) and m["field"]["name"] == "start_now")
    serve.post(f"/sessions/{sid}/answer", {"id": start["id"], "value": True})
    started = ws.until(event("job_started", sid))["event"]
    job_id = started["job_id"]
    assert job_id.startswith("invented-news-") and started["name"] == "Invented news"
    assert serve.get(f"/sessions/{sid}")["jobs"] == [job_id]

    # Progress, to every window and to the session that started it; then the notice.
    progress = ws.until(lambda m: m.get("type") == "job" and m["job_id"] == job_id and "approval" in m["line"],
                        timeout=60)
    assert progress["status"] == "running" and progress["event"]["role"] == "poster"
    ended = ws.until(event("job_ended", sid), timeout=60)["event"]
    assert ended["job_id"] == job_id and ended["waiting"] == 1
    ws.until(lambda m: m.get("type") == "counts" and m["waiting"] == 1)

    # The job and the chat know each other (C2).
    row = next(j for j in serve.get("/jobs") if j["job_id"] == job_id)
    chat = serve.get(f"/sessions/{sid}")["chat"]
    assert row["chat"] == serve.get(f"/sessions/{sid}")["chat_id"]
    assert job_id in serve.get(f"/chats/{chat}")["jobs"]
    assert serve.get(f"/jobs/{job_id}/report")["report"]

    # The approval: listed, opened, approved; only then is anything sent.
    [waiting] = serve.get("/approvals")
    assert waiting["job_id"] == job_id and waiting["tool"] == "mcp.fake.post_message" and fake_sent(record) == []
    detail = serve.get(f"/approvals/{waiting['ref']}")
    assert detail["arguments"]["text"] == "Invented reservoir news." and detail["reason"]
    done = serve.post("/approvals/approve", {"refs": [waiting["ref"]]})
    assert done["results"][0]["status"] == "executed"
    assert [e["arguments"]["text"] for e in fake_sent(record)] == ["Invented reservoir news."]
    assert serve.get("/approvals") == []
    ws.until(lambda m: m.get("type") == "counts" and m["waiting"] == 0)
    assert not outbox.waiting(home)
    ws.close()


def test_views_answer_and_keys_never_show(serve, home):
    keys.set_key(home, "INVENTED_KEY", "sk-INVENTED-never-shown-0000")
    for path in ("/summary", "/chats", "/jobs", "/approvals", "/schedules", "/specs", "/crews", "/preferences",
                 "/models", "/limits", "/keys", "/folders", "/tools", "/skills", "/sessions",
                 "/roles/default/researcher", "/roles/default/lead"):
        r = serve.http.get(path)
        assert r.status_code == 200, (path, r.text)
        assert "sk-INVENTED" not in r.text and "sk-test-INVENTED" not in r.text
    assert any(k["name"] == "INVENTED_KEY" and k["set"] for k in serve.get("/keys")["keys"])
    assert serve.http.get("/jobs/no-such-job").status_code == 404
    assert serve.http.get("/chats/no-such-chat").status_code == 404


def test_actions_change_the_home(serve, home):
    from ombro.home import crew_title
    from ombro.settings import load_preferences

    assert serve.http.put("/preferences", json={"name": "Ada"}).json()["name"] == "Ada"
    assert serve.http.put("/preferences", json={"secret_thing": "x"}).status_code == 400
    assert load_preferences(home).name == "Ada"
    assert serve.post("/crews", {"name": "legal", "title": "Legal Crew"}, status=201)
    assert serve.http.put("/crews/legal", json={"title": "The Legal Crew"}).status_code == 200
    assert crew_title(home, "legal") == "The Legal Crew"
    assert serve.http.put("/keys/ANOTHER_KEY", json={"value": "sk-INVENTED-1111"}).json() == {
        "name": "ANOTHER_KEY", "set": True}
    assert keys.get_key(home, "ANOTHER_KEY") == "sk-INVENTED-1111"
    assert serve.http.delete("/keys/ANOTHER_KEY").json()["removed"] is True
    assert serve.http.put("/limits", json={"allowance": "generous"}).json()["allowance"] == "generous"
    assert serve.http.post("/jobs/start", json={"spec": "nothing-saved"}).status_code == 400
    assert serve.http.post("/approvals/approve", json={"refs": ["ab12-3"]}).status_code == 404  # names nothing
    assert serve.http.post("/approvals/approve", json={"refs": ["not a ref"]}).status_code == 400
    assert serve.http.get("/approvals/ab12-3").status_code == 404


def test_two_processes_writing_preferences_keep_both_changes(home):
    """Plan 3.7: the TUI and the GUI may both write preferences at once; the file stays whole, and
    neither loses the other's change."""
    import subprocess
    import sys

    code = ("import sys; from ombro.home import Home; from ombro.settings import set_preference\n"
            "h = Home()\n"
            "for i in range(40): set_preference(h, sys.argv[1], f'{sys.argv[1]}-{i}')\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, key]) for key in ("name", "editor")]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    meta, _ = mdfile.read(home.preferences_config)  # parses: never half-written
    assert meta["name"] == "name-39" and meta["editor"] == "editor-39"


def test_editing_files_by_hand_waits_for_the_window(serve):
    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "/role edit researcher"})
    edit = ws.until(lambda m: m.get("type") == "edit_files" and m["session"] == sid)
    assert [p.rsplit("/", 1)[-1] for p in edit["paths"]] == ["config.md", "soul.md"]
    assert serve.get(f"/sessions/{sid}")["state"] == "waiting"
    serve.post(f"/sessions/{sid}/answer", {"id": edit["id"], "value": "saved"})
    ws.until(lambda m: m.get("type") == "event" and "Saved." in (m["event"].get("text") or ""))
    ws.close()


def test_reopening_a_saved_chat_shows_it_again(serve):
    _script(serve.client, [fake.say("An invented first answer.")])
    ws = serve.socket()
    ws.send({"type": "subscribe", "all": True})
    first = serve.post("/sessions", status=201)["session"]["id"]
    serve.post(f"/sessions/{first}/input", {"text": "An invented first question"})
    ws.until(state("idle", first))
    name = serve.get(f"/sessions/{first}")["chat"]
    assert name and serve.get(f"/chats/{name}")["turns"][0] == {"who": "you", "text": "An invented first question"}
    serve.http.delete(f"/sessions/{first}")

    again = serve.post("/sessions", {"chat": name}, status=201)["session"]["id"]
    past = ws.until(lambda m: event("past", again)(m) and m["event"]["who"] == "lead")
    assert past["event"]["text"] == "An invented first answer."
    ws.until(state("idle", again))
    assert serve.get(f"/sessions/{again}")["chat"] == name
    assert serve.http.post("/sessions", json={"chat": "no-such-chat"}).status_code == 404
    ws.close()
