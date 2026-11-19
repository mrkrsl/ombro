"""Live jobs: work you stay for. The Lead offers to report back or do it live; a live job starts at
once, its steps show in the chat, its Lead can ask you, and its report ends the chat's part.
Invented work, scripted models, no network: the job runs in a thread instead of its own process."""

from __future__ import annotations

import json
import threading

import pytest

from conftest import LEAD_REPORT
from ombro import fake, jobs, livejob, mdfile
from ombro.engine import Engine
from ombro.fake import ScriptedClient
from ombro.settings import load_limits, load_tool_settings
from test_api_tool import api  # noqa: F401 - a fixture
from test_chat import ChatSession

TASK = "List three invented bakeries in Lowtown, with one line on each. Done when the list is in the report."


def offer() -> dict:
    return fake.call("offer_work", {"summary": "Three bakeries in Lowtown", "title": "Bakeries in Lowtown",
                                    "task": TASK})


@pytest.fixture
def in_thread(monkeypatch, home):
    """Start jobs in a thread of this process, with the job's own scripted model."""
    from ombro import keys
    from ombro.settings import load_models

    key = load_models(home)["default"].key
    if key:
        keys.set_key(home, key, "sk-test-INVENTED-live")  # so the model counts as connected
    script: list = []
    threads: list[threading.Thread] = []

    def start(home, job_dir):
        client = ScriptedClient(list(script))
        engine = Engine(home, job_dir, client_factory=lambda spec: client)
        t = threading.Thread(target=engine.execute, daemon=True)
        t.start()
        threads.append(t)
        return 0

    monkeypatch.setattr(jobs, "start_detached", start)
    yield script
    for t in threads:
        t.join(timeout=20)


def test_the_lead_asks_report_back_or_live_and_report_back_starts_a_brief(home):
    chat = ChatSession(home)
    chat([offer(), fake.say("Fine.")], ["Find me bakeries in Lowtown.", "report", "/quit"])
    asked = chat.answers.asked
    how = next(a for a in asked if "take this away and report back, shall we do it live, here, or shall we keep chatting" in a)
    assert "I'd" not in how  # the Lead doesn't push either way; report back is the default
    assert any(a.startswith("[Brief]") for a in asked)  # the brief interview began
    assert not list(home.jobs_dir.iterdir())  # nothing started yet


def test_keep_chatting_starts_nothing_and_the_chat_carries_on(home):
    chat = ChatSession(home)
    chat([offer(), fake.say("Sure, let's talk it through.")],
         ["Find me bakeries in Lowtown.", "keep chatting", "/quit"])
    assert not list(home.jobs_dir.iterdir())
    assert not any(a.startswith("[Brief]") for a in chat.answers.asked)  # no brief either
    assert any("keep chatting" in r.get("status", "") for r in chat.tool_replies())
    assert "Sure, let's talk it through." in chat.output


def test_not_now_starts_nothing(home):
    chat = ChatSession(home)
    chat([offer(), fake.say("Fine.")], ["Find me bakeries in Lowtown.", "not now", "/quit"])
    assert not list(home.jobs_dir.iterdir())
    assert any("not now" in r.get("status", "") for r in chat.tool_replies())


def test_a_live_job_runs_in_the_chat_asks_you_and_can_be_kept_as_a_brief(home, in_thread):
    in_thread[:] = [
        fake.call("ask_you", {"question": "Sourdough only, or any bread?", "options": ["Sourdough", "Any"]}),
        fake.finish("Listed three."),
        LEAD_REPORT,
    ]
    chat = ChatSession(home)
    chat([offer(), fake.say("Anything else?")],
         ["Find me bakeries in Lowtown.", "live", "Sourdough", "save", "/quit"])
    (job_dir,) = list(home.jobs_dir.iterdir())
    meta, _ = jobs.read_job(job_dir)
    assert meta["live"] is True and meta["chat"] == chat.chat.id and meta["status"] == "completed"
    assert TASK in (job_dir / "spec.md").read_text()
    events = [json.loads(line) for line in (job_dir / "journal.jsonl").read_text().splitlines()]
    assert {"asked", "answered"} <= {e["action"] for e in events}
    answered = next(e for e in events if e["action"] == "answered")
    assert answered["answer"] == "Sourdough"
    out = chat.output
    assert "Here's what the crew will do" in out and "Bakeries in Lowtown" in out
    assert "lead asked you: Sourdough only" in out  # the step shows in the chat
    asked = chat.answers.asked
    assert any("Sourdough only, or any bread?" in a for a in asked)
    assert any("Want to run this again later?" in a for a in asked)
    (saved,) = list(home.specs_dir.glob("bakeries-in-lowtown*.md"))
    smeta, body = mdfile.read(saved)
    assert "live" not in smeta and TASK in body and smeta["crews"] == ["default"]
    assert chat.chat.saved_briefs == [saved.stem] and chat.chat.specs == []  # not offered to start again


def test_the_lead_names_the_folders_a_live_job_writes_to(home, in_thread, tmp_path, monkeypatch):
    """The Lead names a shared folder in a live job's spec when the work writes there."""
    from ombro import folders
    from ombro.chat import _offered_folders

    me = tmp_path / "user"
    (me / "Documents" / "Brand").mkdir(parents=True)
    (me / "Documents" / "Notes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(me))
    folders.add(home, me / "Documents" / "Brand", "brand", "write")
    folders.add(home, me / "Documents" / "Notes", "notes", "write")
    in_thread[:] = [fake.finish("Wrote it."), LEAD_REPORT]
    chat = ChatSession(home)
    named = fake.call("offer_work", {"summary": "New copy", "title": "New copy", "task": "Write shared/brand/x.md.",
                                     "folders": [{"folder": "brand", "access": "write"},
                                                 {"folder": "shared/notes", "access": "read"}]})
    chat([named, fake.say("Done.")], ["Write the new copy into the brand folder.", "live", "no", "/quit"])
    (job_dir,) = list(home.jobs_dir.iterdir())
    smeta, _ = mdfile.read(job_dir / "spec.md")
    assert smeta["folders"] == {"brand": "write", "notes": "read"}
    meta, _ = jobs.read_job(job_dir)
    assert {f["name"]: f["access"] for f in meta["folders"]} == {"brand": "write", "notes": "read"}
    assert _offered_folders("not a list") == {} and _offered_folders(["brand", {"folder": "a/b"}]) == {"brand": "read"}


def test_slash_live_starts_one_with_your_words(home, in_thread):
    in_thread[:] = [fake.finish("Done."), LEAD_REPORT]
    chat = ChatSession(home)
    chat([], ["/live find three invented bakeries in Lowtown", "no", "/quit"])
    (job_dir,) = list(home.jobs_dir.iterdir())
    assert jobs.read_job(job_dir)[0]["live"] is True
    assert "find three invented bakeries in Lowtown" in (job_dir / "spec.md").read_text()


def test_a_question_nobody_answers_lets_the_job_carry_on(home, tmp_path):
    job_dir = home.jobs_dir / "invented"
    job_dir.mkdir(parents=True)
    t = [0.0]
    got = livejob.ask(job_dir, "Which?", clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + 60), wait=120)
    assert got is None and livejob.pending(job_dir) is None


def test_an_answer_reaches_the_waiting_job(home):
    job_dir = home.jobs_dir / "invented"
    job_dir.mkdir(parents=True)

    def reply(_):
        q = livejob.pending(job_dir)
        if q:
            livejob.answer(job_dir, q["id"], "Any")

    assert livejob.ask(job_dir, "Which?", ["Sourdough", "Any"], sleep=reply) == "Any"


def test_only_a_live_job_gets_ask_you(home, job):
    job([fake.finish(), LEAD_REPORT])
    tools = [t["function"]["name"] for t in job.client.calls[0]["tools"]]
    assert "ask_you" not in tools
    limits = load_limits(home)
    job_dir = jobs.create_job(home, ["default"], {}, "# Live\n\nInvented.\n", limits, source="test",
                              tools=load_tool_settings(home).to_dict(), extra={"live": True})
    client = ScriptedClient([fake.finish(), LEAD_REPORT])
    Engine(home, job_dir, client_factory=lambda spec: client).execute()
    first = client.calls[0]
    assert "ask_you" in [t["function"]["name"] for t in first["tools"]]
    assert "This is a live job" in first["messages"][0]["content"]


def test_one_fake_script_serves_a_chat_and_the_job_it_starts():
    """Entries that wait for their call: how the demo profile plays a live job with one script."""
    client = ScriptedClient([
        {**fake.say("For the job."), "needs": "brief"},
        {**fake.say("For the report."), "needs": "prompt:final report"},
        fake.say("For anyone."),
    ])
    chat_tools = [{"type": "function", "function": {"name": "offer_work"}}]
    job_tools = [{"type": "function", "function": {"name": "brief"}}]
    say = lambda tools, system="": client.complete(  # noqa: E731
        [{"role": "system", "content": system}], tools=tools, max_output_tokens=100, timeout=1).content
    assert say(chat_tools) == "For anyone."
    assert say(None, "Write the final report.") == "For the report."
    assert say(job_tools) == "For the job."


# -- quick lookups in chat ------------------------------------------------------------


@pytest.mark.parametrize("yes", [True, False])
def test_chat_looks_things_up_itself_and_asks_before_it_changes_anything(home, tmp_path, yes):
    from conftest import fake_sent, use_fake_mcp
    from ombro.tools import wire_name

    record = tmp_path / "sent.jsonl"
    use_fake_mcp(home, record)
    chat = ChatSession(home)
    chat([fake.call(wire_name("mcp.fake.find_rows"), {"query": "invented"}),
          fake.call(wire_name("mcp.fake.add_row"), {"sheet": "invented", "values": ["x"], "reason": "You asked."}),
          fake.call(wire_name("mcp.fake.post_message"), {"channel": "news", "text": "Added.", "reason": "To say so."}),
          fake.say("Done.")], ["Add a row about invented things and tell the channel.", "y" if yes else "n", "/quit"])
    from conftest import offered_tools

    sent = {t["function"]["name"] for t in chat.client.calls[0]["tools"]}
    offered = offered_tools(chat.client.calls[0]["tools"])
    assert wire_name("mcp.fake.find_rows") in offered  # read-only: a lookup
    assert wire_name("mcp.fake.find_rows") not in sent  # listed in tools__load, not sent in full
    assert wire_name("mcp.fake.add_row") in offered and wire_name("mcp.fake.post_message") in offered
    looked, added, posted = chat.tool_replies()
    assert "error" not in looked
    assert added["status"] == "done"  # internal, under the balanced mode: done, as in a job
    if yes:  # external: only with the user's yes
        assert posted["status"] == "done"
        assert [s["tool"] for s in fake_sent(record)] == ["find_rows", "add_row", "post_message"]
    else:
        assert "declined" in posted
        assert [s["tool"] for s in fake_sent(record)] == ["find_rows", "add_row"]
    chat.chat.close()


def test_chat_uses_a_web_apis_get(home, request):
    from ombro.tools import wire_name

    fake_api = request.getfixturevalue("api")
    chat = ChatSession(home)
    chat([fake.call(wire_name("api.podcasts.get"), {"path": "/episodes", "query": {"q": "reservoir"}}),
          fake.say("One episode.")], ["Any episodes about the reservoir?", "/quit"])
    offered = {t["function"]["name"] for t in chat.client.calls[0]["tools"]}
    assert wire_name("api.podcasts.get") in offered and wire_name("api.podcasts.request") in offered
    assert [r.method for r in fake_api.seen] == ["GET"]
    assert "pk-INVENTED" not in json.dumps(chat.tool_replies())  # the key is wiped from what comes back
