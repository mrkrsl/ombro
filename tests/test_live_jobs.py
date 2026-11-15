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
from test_chat import ChatSession

TASK = "List three invented bakeries in Lowtown, with one line on each. Done when the list is in the report."


def offer(suggest: str = "live") -> dict:
    return fake.call("offer_work", {"summary": "Three bakeries in Lowtown", "title": "Bakeries in Lowtown",
                                    "task": TASK, "suggest": suggest})


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
    chat([offer("report_back"), fake.say("Fine.")], ["Find me bakeries in Lowtown.", "report", "/quit"])
    asked = chat.answers.asked
    assert any("take this away and report back, or shall we do it live" in a for a in asked)
    assert any(a.startswith("[Brief]") for a in asked)  # the brief interview began
    assert not list(home.jobs_dir.iterdir())  # nothing started yet


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
