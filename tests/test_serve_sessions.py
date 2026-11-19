"""`ombro serve`: a chat, a brief, a started job, its progress and an approval, driven over
HTTP and the socket against a real serve on 127.0.0.1, with the fake model. All data is invented.

The chat talks to a scripted client in this process; the job runs detached, as it would from the app,
and reads its own script from the home."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

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
    """The whole round trip: brief a task in a chat, say yes, follow the job, approve what it asks."""
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
    # The counts may come just before or just after the job's end.
    waiting = lambda m: m.get("type") == "counts" and m["waiting"] == 1  # noqa: E731
    if not any(waiting(m) for m in ws.seen):
        ws.until(waiting)

    # The job and the chat know each other.
    row = next(j for j in serve.get("/jobs") if j["job_id"] == job_id)
    chat = serve.get(f"/sessions/{sid}")["chat"]
    assert row["chat"] == serve.get(f"/sessions/{sid}")["chat_id"]
    assert job_id in serve.get(f"/chats/{chat}")["jobs"]
    assert serve.get(f"/jobs/{job_id}/report")["report"]
    # Finished and not yet seen, for the menu bar; looking at the Jobs page sees it.
    assert serve.get("/summary")["counts"]["unseen_jobs"] == 1
    assert serve.post("/jobs/seen", {})["seen"] == 1
    assert serve.get("/summary")["counts"]["unseen_jobs"] == 0
    unseen_cleared = lambda m: m.get("type") == "counts" and m["unseen"] == 0  # noqa: E731
    if not any(unseen_cleared(m) for m in ws.seen[-3:]):
        ws.until(unseen_cleared)

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
    for path in ("/summary", "/chats", "/jobs", "/approvals", "/schedules", "/scheduler", "/specs", "/crews", "/preferences",
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
    assert [p.rsplit("/", 1)[-1] for p in edit["paths"]] == ["soul.md"]  # the soul only, as in Settings
    assert serve.get(f"/sessions/{sid}")["state"] == "waiting"
    serve.post(f"/sessions/{sid}/answer", {"id": edit["id"], "value": "saved"})
    ws.until(lambda m: m.get("type") == "event" and "Saved." in (m["event"].get("text") or ""))
    ws.close()


def test_deleting_a_chat_moves_it_to_the_archive_and_ends_its_session(serve, home):
    _script(serve.client, [fake.say("An invented answer.")])
    ws = serve.socket()
    ws.send({"type": "subscribe", "all": True})
    sid = serve.post("/sessions", status=201)["session"]["id"]
    serve.post(f"/sessions/{sid}/input", {"text": "An invented question to delete"})
    ws.until(state("idle", sid))
    name = serve.get(f"/sessions/{sid}")["chat"]
    text = (home.chats_dir / f"{name}.md").read_text(encoding="utf-8")

    out = serve.post(f"/chats/{name}/archive")
    assert name not in {c["name"] for c in out["chats"]} and name not in {c["name"] for c in serve.get("/chats")}
    assert not (home.chats_dir / f"{name}.md").exists()
    archived = home.chats_dir / "Archive" / f"{name}.md"
    assert out["moved_to"] == str(archived) and archived.read_text(encoding="utf-8").startswith(text[:200])
    assert serve.http.get(f"/sessions/{sid}").status_code == 404  # the open session ended first
    assert not (home.chats_dir / f"{name}.md").exists()  # and didn't write the chat back
    assert serve.http.post("/chats/no-such-chat/archive").status_code == 404
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


def test_schedules_over_http(serve, home):
    mdfile.write(home.specs_dir / "invented-news.md", {"name": "invented-news"}, "# Invented news\n\nCheck it.\n")
    rows = serve.post("/schedules", {"name": "news-briefing", "spec": "invented-news", "cron": "0 6 * * 1-5"},
                      status=201)
    assert rows[0]["when"] == "Runs weekdays at 6am" and rows[0]["enabled"] is True
    assert serve.http.post("/schedules", json={"name": "news-briefing", "spec": "invented-news",
                                                "cron": "0 6 * * 1-5"}).status_code == 400  # not new
    assert serve.http.post("/schedules", json={"name": "bad", "spec": "invented-news",
                                                "cron": "61 * * * *"}).status_code == 400
    assert serve.http.put("/schedules/news-briefing", json={"cron": "0 18 * * 4"}).json()[0]["when"] == \
        "Runs Thursdays at 6pm"
    assert serve.http.post("/schedules/news-briefing/disable").json() == {"name": "news-briefing", "enabled": False}
    gone = serve.http.delete("/schedules/news-briefing").json()
    assert gone["schedules"] == [] and "/removed/schedules/" in gone["moved_to"]
    assert serve.http.delete("/schedules/news-briefing").status_code == 404


def test_the_scheduler_service_over_http(serve, home, no_system_scheduler, monkeypatch):
    from ombro import scheduler

    state = serve.get("/scheduler")
    assert not state["ok"] and state["can_install"] and "not installed" in state["message"]
    assert serve.http.post("/scheduler/install").status_code == 200
    assert no_system_scheduler.loaded
    assert scheduler.make_installer(home).installed().home == str(home.root)
    # The packaged app puts it back itself when a schedule is added.
    scheduler.make_installer(home).uninstall()
    monkeypatch.setattr(scheduler, "may_install_itself", lambda h: True)
    mdfile.write(home.specs_dir / "invented-news.md", {"name": "invented-news"}, "# Invented news\n\nCheck it.\n")
    serve.post("/schedules", {"name": "news-briefing", "spec": "invented-news", "cron": "0 6 * * 1-5"}, status=201)
    assert scheduler.make_installer(home).installed() is not None


def test_the_feed_and_notifications_preferences(serve, home):
    prefs = serve.get("/preferences")
    assert prefs["background_feed"] is True and prefs["notifications"] is True
    assert serve.http.put("/preferences", json={"background_feed": False}).json()["background_feed"] is False
    assert serve.http.put("/preferences", json={"notifications": "no"}).status_code == 400


def test_a_new_crew_member_interview_is_a_chat_you_can_leave_and_come_back_to(serve, home):
    """Add crew member in the app: the chat is saved as the interview starts, so it is listed while
    it waits on you, and what the interview said is kept when the session ends mid-way."""
    _script(serve.client, [fake.say("What should your invented teapot watcher look out for?")])
    ws = serve.socket()
    sid = serve.post("/sessions", {"flow": "role.add", "crew": "default"}, status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    ws.until(question(sid))
    view = serve.get(f"/sessions/{sid}")
    assert view["chat"] and view["title"] == "New crew member"
    assert [c["title"] for c in serve.get("/chats")][0] == "New crew member"

    serve.http.delete(f"/sessions/{sid}")  # the window let it go, half-way
    body = mdfile.read(home.chats_dir / f"{view['chat']}.md")[1]
    assert "**Lead (Role):** What should your invented teapot watcher look out for?" in body

    ws.send({"type": "subscribe", "all": True})
    again = serve.post("/sessions", {"chat": view["chat"]}, status=201)["session"]["id"]
    past = ws.until(lambda m: event("past", again)(m) and m["event"]["who"] == "lead")
    assert past["event"]["text"] == "What should your invented teapot watcher look out for?"
    ws.close()


def test_a_jobs_brief_and_clearing_finished_jobs(serve, home):
    """The Jobs page: any job's brief, from the copy the job keeps; finished jobs cleared one at a time
    or all at once, only hidden (their folders stay) and put back; a running job can't be cleared."""
    from ombro import jobs
    from ombro.settings import load_limits

    def job(brief: str, status: str) -> str:
        d = jobs.create_job(home, ["default"], {"crews": ["default"]}, brief, load_limits(home), source="test")
        if status != "running":
            jobs.update_job(d, status=status, finished_at="2026-11-17T09:00:00Z")
            (d / "report.md").write_text("# Invented report\n", encoding="utf-8")
        return d.name

    done = job("# Invented teapots\n\nCount the **invented** teapots.", "completed")
    other = job("# Invented kettles\n\nAnother one.", "failed")
    busy = job("# Invented pans\n\nStill going.", "running")

    # A running job's brief opens too, as the Markdown the job ran from, front matter left out.
    brief = serve.get(f"/jobs/{busy}/brief")
    assert brief["spec"] == "# Invented pans\n\nStill going." and brief["active"] and brief["cleared_at"] is None
    assert "Count the **invented** teapots." in serve.get(f"/jobs/{done}/brief")["spec"]

    # A running job can't be cleared; a finished one can, and counts as seen.
    assert serve.http.post(f"/jobs/{busy}/clear").status_code == 409
    assert serve.post(f"/jobs/{done}/clear")["cleared"] is True
    assert serve.post(f"/jobs/{done}/clear")["cleared"] is False
    rows = {r["job_id"]: r for r in serve.get("/jobs")}
    assert rows[done]["cleared_at"] and rows[done]["seen_at"] and not rows[other]["cleared_at"]
    # Only hidden: the folder, its report and its files stay, and the job is still found by name.
    assert (home.jobs_dir / done / "report.md").is_file()
    assert serve.get(f"/jobs/{done}/report")["report"]

    # Clear all finished: the other finished job, never the running one; then one put back.
    assert serve.post("/jobs/clear")["cleared"] == 1
    rows = {r["job_id"]: r for r in serve.get("/jobs")}
    assert rows[other]["cleared_at"] and rows[busy]["cleared_at"] is None
    assert serve.get("/summary")["counts"]["unseen_jobs"] == 0
    assert serve.post(f"/jobs/{done}/restore")["restored"] is True
    assert {r["job_id"] for r in serve.get("/jobs") if not r["cleared_at"]} == {done, busy}


def test_discussing_a_jobs_brief_starts_a_chat_where_the_lead_has_it(serve, home):
    """Discuss this brief, on a job's brief page: a new chat, the Lead's question first, and the brief
    (with how to start, change or schedule its saved spec) sent with the user's answer."""
    from ombro import jobs, mdfile
    from ombro.settings import load_limits

    spec = home.specs_dir / "invented-teapots.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    mdfile.write(spec, {"crews": ["default"]}, "# Invented teapots\n\nCount the invented teapots.")
    d = jobs.create_job(home, ["default"], {"crews": ["default"]}, "# Invented teapots\n\nCount the invented teapots.",
                        load_limits(home), source=f"spec file {spec}")
    jobs.update_job(d, status="completed", brief="Invented teapots")

    _script(serve.client, [fake.say("Let's change it.")])
    ws = serve.socket()
    ws.send({"type": "subscribe", "all": True})
    sid = serve.post("/sessions", {"discuss": d.name}, status=201)["session"]["id"]
    said = ws.until(lambda m: event("note", sid)(m) and m["event"].get("level") == "lead")
    assert said["event"]["text"].startswith("I've got the brief 'Invented teapots'. What would you like to do")
    ws.until(state("idle", sid))
    serve.post(f"/sessions/{sid}/input", {"text": "Make it count kettles too"})
    ws.until(state("idle", sid))
    sent = serve.client.calls[-1]["messages"]
    asked = [m["content"] for m in sent if m["role"] == "user"][-1]
    assert "Count the invented teapots." in asked and "offer_brief with revise `invented-teapots`" in asked
    assert asked.endswith("Make it count kettles too")
    name = serve.get(f"/sessions/{sid}")["chat"]
    assert serve.get(f"/chats/{name}")["title"] == "Brief: Invented teapots"
    assert serve.http.post("/sessions", json={"discuss": "no-such-job"}).status_code == 404
    ws.close()


def test_a_file_attached_in_the_app_goes_to_the_lead_with_the_message(serve, home):
    from ombro import attachments

    r = serve.http.post("/attachments", params={"name": "../Invented notes.md"}, content=b"# Three invented teapots")
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["name"] == "Invented notes.md" and got["kind"] == "text" and got["size"] == 24
    assert Path(got["path"]).parent.parent == attachments.files_dir(home)

    _script(serve.client, [fake.say("Three teapots.")])
    ws = serve.socket()
    ws.send({"type": "subscribe", "all": True})
    sid = serve.post("/sessions", status=201)["session"]["id"]
    serve.post(f"/sessions/{sid}/input", {"text": "What's in it?", "attachments": [got["path"]]})
    ws.until(state("idle", sid))
    asked = [m["content"] for m in serve.client.calls[-1]["messages"] if m["role"] == "user"][-1]
    assert "Three invented teapots" in asked and asked.endswith("What's in it?")
    # Only a file attached here is taken: a path elsewhere on the machine is refused.
    outside = serve.http.post(f"/sessions/{sid}/input", json={"text": "Hi", "attachments": [str(home.root / "keys.md")]})
    assert outside.status_code == 400 and "attach it again" in outside.json()["error"]
    assert serve.http.post("/attachments", content=b"x").status_code == 400  # no name
    ws.close()


def test_a_new_message_sets_the_open_question_aside(serve):
    """Report back or live is waiting, and Mark writes something else: the question goes unanswered
    ("not now"), the transcript says so, and the message goes to the Lead as an ordinary one."""
    _script(serve.client, [
        fake.call("offer_work", {"summary": "Three invented bakeries in Lowtown", "title": "Bakeries",
                                 "task": "List three invented bakeries in Lowtown."}),
        fake.say("Of course: it's invented, so call it sunny."),
    ])
    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "Find me bakeries in Lowtown."})
    how = ws.until(question(sid))
    assert how["field"]["name"] == "how" and how["field"]["aside"] is True

    # The app sends what is typed mid-question as an answer; one that isn't an answer sets it aside.
    assert serve.post(f"/sessions/{sid}/answer", {"id": how["id"], "value": "What's the weather like?"})["ok"]
    ws.until(lambda m: m.get("type") == "question_closed" and m["id"] == how["id"])
    ws.until(lambda m: m.get("type") == "event" and "set aside" in (m["event"].get("text") or ""))
    said = ws.until(event("lead", sid))
    assert said["event"]["text"].startswith("Of course")
    ws.until(state("idle", sid))
    assert not list(serve.app.state.serve.home.jobs_dir.iterdir())  # nothing started

    # The Lead was told the question was set aside, then given the message, as Mark wrote it.
    last = serve.client.calls[-1]["messages"]
    tool = next(m for m in last if m["role"] == "tool")
    assert "set this question aside" in tool["content"]
    assert last[-1] == {"role": "user", "content": "What's the weather like?"} or \
        last[-1]["content"].endswith("What's the weather like?")
    chat = serve.get(f"/sessions/{sid}")["chat"]
    text = (serve.app.state.serve.home.chats_dir / f"{chat}.md").read_text()
    assert "You set the question aside (Shall I take this away" in text
    assert text.index("set the question aside") < text.index("What's the weather like?")
    ws.close()


def test_an_answer_to_the_open_question_still_answers_it(serve):
    """Typed answers are still answers: "not now" declines, and nothing is queued."""
    _script(serve.client, [
        fake.call("offer_work", {"summary": "Invented work", "title": "Invented", "task": "Do invented work."}),
        fake.say("Fine, another time."),
    ])
    ws = serve.socket()
    sid = serve.post("/sessions", status=201)["session"]["id"]
    ws.send({"type": "subscribe", "sessions": {sid: 0}})
    serve.post(f"/sessions/{sid}/input", {"text": "Do some invented work."})
    how = ws.until(question(sid))
    assert serve.post(f"/sessions/{sid}/answer", {"id": how["id"], "value": "Not now"})["ok"]
    assert ws.until(event("lead", sid))["event"]["text"] == "Fine, another time."
    ws.until(state("idle", sid))
    tool = next(m for m in serve.client.calls[-1]["messages"] if m["role"] == "tool")
    assert "said not now" in tool["content"]
    ws.close()


def test_a_new_chat_is_saved_from_its_first_message_and_survives_clicking_away(serve, home):
    """A new chat, an opening message, the Lead still answering, a look at another chat: the new
    chat is on disk and in the list from its first message, before any reply, with a title from its
    first words; it is still there after the reply, and after the app closes mid-reply."""
    gate = threading.Event()
    real = serve.client.complete

    def slow(*args, **kwargs):
        assert gate.wait(10), "the test never let the Lead answer"
        return real(*args, **kwargs)

    serve.client.complete = slow
    _script(serve.client, [fake.say("An invented answer about the garden survey.")])
    ws = serve.socket()
    ws.send({"type": "subscribe", "all": True})
    new = serve.post("/sessions", status=201)["session"]["id"]
    serve.post(f"/sessions/{new}/input", {"text": "Help me brief an invented garden survey"})

    saved = ws.until(lambda m: m.get("type") == "chat_saved" and m["session"] == new)
    name = saved["chat"]
    assert (home.chats_dir / f"{name}.md").exists()
    assert serve.get(f"/sessions/{new}")["state"] == "working"  # no reply yet
    rows = {c["name"]: c for c in serve.get("/chats")}
    assert name in rows and rows[name]["title"] == "Brief an invented garden survey"
    assert serve.get(f"/chats/{name}")["turns"][0] == {"who": "you",
                                                        "text": "Help me brief an invented garden survey"}

    # Looking at another chat meanwhile: the new one stays listed, and comes back when clicked.
    other = serve.post("/sessions", status=201)["session"]["id"]
    assert serve.get(f"/sessions/{other}")["chat"] is None
    assert name in {c["name"] for c in serve.get("/chats")}
    gate.set()
    ws.until(state("idle", new))
    assert serve.get(f"/sessions/{new}")["chat"] == name
    turns = serve.get(f"/chats/{name}")["turns"]
    assert [t["who"] for t in turns] == ["you", "lead"]

    # The app closing while the Lead is answering a new chat's first message keeps that message.
    gate.clear()
    _script(serve.client, [fake.say("Never seen: the app closed first.")])
    closing = serve.post("/sessions", status=201)["session"]["id"]
    serve.post(f"/sessions/{closing}/input", {"text": "An invented second opener"})
    second = ws.until(lambda m: m.get("type") == "chat_saved" and m["session"] == closing)["chat"]
    threading.Timer(0.5, gate.set).start()
    serve.app.state.serve.sessions.close_all()  # what serve does as it shuts down
    body = mdfile.read(home.chats_dir / f"{second}.md")[1]
    assert "An invented second opener" in body
    ws.close()


def test_the_counts_name_the_running_jobs_so_the_sidebar_ring_shows_at_once(home):
    """The counts carry the running jobs' ids, before any step, live or not."""
    from ombro.serve.app import Serve

    import os

    from ombro.jobs import new_job_id

    jid = new_job_id("invented-report")
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "running", "pid": os.getpid(), "crews": ["default"],
                                                   "brief": "Invented", "created_at": "2026-11-14T09:00:00"})
    counts = Serve(home).counts()
    assert counts["running"] == [jid] and counts["active"] == 1
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "completed", "crews": ["default"], "brief": "Invented",
                                                   "created_at": "2026-11-14T09:00:00"})
    assert Serve(home).counts()["running"] == []
