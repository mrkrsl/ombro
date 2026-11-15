"""`ombro chat` with a scripted model and scripted input. All data is invented; no network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from conftest import LEAD_REPORT, use_fake_tools, write_role
from ombro.material import unfence
from ombro import fake, mdfile, memory
from ombro.chat import Chat
from ombro.fake import ScriptedClient
from ombro.home import ensure_crew
from ombro.native import native_registry
from ombro.settings import load_tool_settings

TODAY = date(2026, 11, 11)


class Answers:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            return "/quit"
        a = self.answers.pop(0)
        return a(prompt) if callable(a) else a


class ChatSession:
    def __init__(self, home, titles=False):
        self.home = home
        self.titles = titles  # off unless a test is about titles: the title call would use a scripted reply
        self.said: list[str] = []

    def __call__(self, script, answers, *, crew=None, edit=None) -> Chat:
        use_fake_tools(self.home) if not (self.home.tools.is_file() and "fake" in self.home.tools.read_text()) else None
        self.client = ScriptedClient(script)
        self.answers = Answers(answers)
        self.chat = Chat(
            self.home, crew=crew, ask=self.answers, say=self.said.append, edit=edit,
            client_factory=lambda spec: self.client, today=TODAY,
            registry=native_registry(self.home, load_tool_settings(self.home)), titles=self.titles,
        )
        return self.chat.run()

    @property
    def output(self) -> str:
        return "\n".join(self.said)

    def tool_replies(self) -> list[dict]:
        seen, out = set(), []
        for c in self.client.calls:
            for m in c["messages"]:
                if m["role"] == "tool" and m["tool_call_id"] not in seen:
                    seen.add(m["tool_call_id"])
                    out.append(json.loads(unfence(m["content"])))
        return out

    @property
    def transcript(self) -> str:
        return self.chat.path.read_text()


@pytest.fixture
def chat(home):
    return ChatSession(home)


def past_job(job, summary: str, crew: str = "default") -> str:
    job([fake.brief("researcher", "Research."), fake.result(summary), fake.finish(), fake.keep_notes(),
         fake.say(f"## What was done\n{summary}\n\n## What was not done and why\nNothing.\n\n"
                  "## Decisions for you\nNone.")], crew=crew)
    return job.meta["job_id"]


def snapshot(home) -> dict[str, bytes]:
    """Every file in the home except chat transcripts and fake-provider records."""
    return {str(p.relative_to(home.root)): p.read_bytes() for p in home.root.rglob("*")
            if p.is_file() and "chats" not in p.parts}


# -- answering about past work, across crews ---------------------------------------


def test_chat_answers_about_a_past_job_from_every_crew(chat, job, home):
    ensure_crew(home, "podcast")
    write_role(home, "researcher", crew="podcast")
    teapots = past_job(job, "Found the invented Copperkettle teapot works.")
    podcast = past_job(job, "Booked the invented Copperkettle podcast guest.", crew="podcast")
    chat([
        fake.call("memory__search", {"query": "Copperkettle"}),
        fake.call("memory__read", {"path": f"jobs/{teapots}/report.md"}),
        fake.say(f"Run {teapots} found the Copperkettle teapot works."),
    ], ["What did we find about Copperkettle?"])
    search, read = chat.tool_replies()
    assert {r["job_id"] for r in search["results"]} == {teapots, podcast}
    assert "Copperkettle teapot works" in read["content"]
    assert f"Lead: Run {teapots} found the Copperkettle teapot works." in chat.output
    system = chat.client.calls[0]["messages"][0]["content"]
    assert "- the Default Crew (crew name `default` for tools, the default crew)" in system
    assert "- the Podcast Crew" in system
    assert "Copperkettle" not in system  # memory is reached through tools, not loaded


def test_chat_reads_only_reports_notes_indexes_specs_and_deliverables(chat, job, home):
    rid = past_job(job, "Found invented teapots.")
    (home.chats_dir / "20261101-090000.md").write_text("An old chat.")
    paths = [f"jobs/{rid}/journal.jsonl", "chats/20261101-090000.md", ".env", "models.md",
             f"jobs/{rid}/../../../../.env", "crews/default/roles/lead/soul.md",
             "crews/default/memory/notes.md", f"jobs/{rid}/spec.md"]
    reads = {"tool_calls": [{"name": "memory__read", "arguments": {"path": p}} for p in paths]}
    chat([reads, fake.say("Done.")], ["Read things."])
    replies = chat.tool_replies()
    assert all("error" in r for r in replies[:6]), replies[:6]
    assert "content" in replies[6] and "content" in replies[7]


# -- read-only ---------------------------------------------------------------------


def test_chat_refuses_every_write_and_delegation_tool(chat, job, home):
    past_job(job, "Found invented teapots.")
    before = snapshot(home)
    attempts = [
        ("brief", {"role": "researcher", "task": "Do it now."}),
        ("finish", {"outcome": "done", "summary": "x"}),
        ("memory__write_notes", {"from_jobs": "- Overwritten."}),
        ("keep_notes", {"reason": "x"}),
        ("workspace__write", {"path": "workspace/x.md", "content": "x"}),
        ("workspace__read", {"path": "workspace/x.md"}),
        ("email__send", {"to": "someone@example.invalid"}),
    ]
    chat([{"tool_calls": [{"name": n, "arguments": a} for n, a in attempts]}, fake.say("I cannot do that here.")],
         ["Just do the task and save it."])
    replies = chat.tool_replies()
    assert len(replies) == len(attempts)
    assert all("not available in chat" in r["error"] for r in replies)
    assert sorted(set(chat.chat.refused)) == sorted(n for n, _ in attempts)
    assert snapshot(home) == before  # nothing outside chats/ changed
    assert mdfile.read(chat.chat.path)[0]["refused_tools"] == sorted(n for n, _ in attempts)


# -- moving into a brief -----------------------------------------------------------

SPEC_ARGS = {
    "name": "find-teapots", "title": "Find invented teapot makers",
    "done_looks_like": "A list of three.", "users": "Me, to pick one.", "binding_constraint": "Lowtown only.",
    "waste": "Makers outside Lowtown.", "non_goals": "No prices.",
}


def test_the_lead_offers_a_brief_and_the_chat_becomes_its_starting_point(chat, home):
    def brief_opening(messages, tools):
        opening = messages[1]["content"]
        assert "I want three invented teapot makers in Lowtown." in opening  # the user's own words
        assert "The Lead's one-line summary in the chat, which the user accepted: Find three teapot makers" in opening
        assert tools[0]["function"]["name"] == "ask"  # the task interview, not chat
        return fake.call("propose", SPEC_ARGS)

    chat([
        fake.call("offer_brief", {"summary": "Find three teapot makers in Lowtown"}),
        brief_opening,
        fake.say("Saved. Anything else?"),
    ], ["I want three invented teapot makers in Lowtown.", "y", "a", "Thanks."])
    assert (home.specs_dir / "find-teapots.md").is_file()
    assert "ombro start --spec find-teapots" in chat.output
    asked = chat.answers.asked
    assert asked[0].startswith("[Chat]") and asked[1].startswith("[Chat] Shall we start the brief?")
    assert asked[2].startswith("[Brief]")  # the mode is shown in Brief too
    assert asked[3] == "[Chat] You: "
    # After the brief the Lead is told what happened.
    assert "spec, saved as 'find-teapots'" in chat.client.calls[-1]["messages"][-1]["content"]


def test_the_lead_never_switches_on_its_own(chat, home):
    chat([fake.call("offer_brief", {"summary": "Find teapots"}), fake.say("All right, we can keep talking.")],
         ["I might want some teapot makers found.", "n"])
    assert chat.client.calls[-1]["tools"][0]["function"]["name"] == "memory__search"  # still chat
    assert all(not a.startswith("[Brief]") for a in chat.answers.asked)
    assert not list((home.specs_dir).glob("*.md"))


def test_slash_brief_then_slash_chat_writes_nothing(chat, home):
    chat([fake.say("Tell me about the task.")], ["/brief Find invented kettles.", "/chat", "/quit"])
    assert "Back in Chat mode." not in chat.output  # said once, in the Lead's line
    assert "I've stopped the interview. Nothing was written, and we're back in Chat mode." in chat.output
    assert not list((home.specs_dir).glob("*.md"))
    first = chat.client.calls[0]["messages"][1]["content"]
    assert "Find invented kettles." in first


def test_slash_brief_names_another_crew(chat, home):
    ensure_crew(home, "podcast")
    chat([fake.call("propose", SPEC_ARGS)], ["/brief @podcast Find teapots.", "a"])
    assert (home.specs_dir / "find-teapots.md").is_file()


# -- remember ----------------------------------------------------------------------


def test_remember_writes_a_dated_entry_only_once_confirmed(chat, home):
    before = memory.notes_path(home, "default").read_text()
    chat([], ["/remember Always cite invented sources.", "n"])
    assert memory.notes_path(home, "default").read_text() == before
    chat([], ["/remember Always cite invented sources.", "e", "Always cite sources, even invented ones.", "y"])
    notes = memory.read_notes(home, "default")
    assert notes.your_entries() == ["- 2026-11-11: Always cite sources, even invented ones."]
    assert notes.from_jobs == ""


def test_the_lead_offers_to_remember_and_the_user_picks_the_crew(chat, home):
    ensure_crew(home, "podcast")
    chat([
        fake.call("offer_remember", {"text": "Guests want questions a week ahead."}),
        fake.say("Noted in the podcast crew."),
    ], ["Tell the crew guests want questions a week ahead.", "@podcast", "y"])
    assert memory.read_notes(home, "podcast").your_entries() == ["- 2026-11-11: Guests want questions a week ahead."]
    assert memory.read_notes(home, "default").your_entries() == []
    assert chat.tool_replies()[0]["status"] == "saved"


def test_remember_names_a_crew(chat, home):
    ensure_crew(home, "podcast")
    chat([], ["/remember @podcast Record on Tuesdays.", "y"])
    assert memory.read_notes(home, "podcast").your_entries() == ["- 2026-11-11: Record on Tuesdays."]


def test_a_remember_over_the_cap_asks_what_to_trim(chat, home):
    mdfile.write(home.limits, {**mdfile.read(home.limits)[0], "memory_notes_max_chars": 300}, mdfile.read(home.limits)[1])
    notes = memory.read_notes(home, "default")
    for i in range(1, 6):
        notes = memory.with_your_entry(notes, f"- 2026-10-0{i}: An invented lesson number {i}.")
    memory.save_notes(home, "default", notes)
    chat([], ["/remember A new invented lesson that does not fit.", "y", "c"])
    assert "over the cap of 300" in chat.output and "1. - 2026-10-01" in chat.output
    assert memory.read_notes(home, "default").your_entries() == notes.your_entries()  # cancelled: unchanged
    chat([], ["/remember A new invented lesson that does not fit.", "y", "1 2 3"])
    entries = memory.read_notes(home, "default").your_entries()
    assert entries[0].startswith("- 2026-10-04") and entries[-1] == "- 2026-11-11: A new invented lesson that does not fit."
    assert memory.notes_path(home, "default").stat().st_size <= 300


# -- bounds ------------------------------------------------------------------------


def test_chat_usage_has_its_own_cap(chat, job, home):
    # The first call fits (its prompt and largest reply are under the cap); after it, the second cannot.
    mdfile.write(home.limits, {**mdfile.read(home.limits)[0], "chat_max_tokens": 12500}, mdfile.read(home.limits)[1])
    chat([fake.say("Hello.", usage={"input": 7000, "output": 100})] * 5, ["Hi.", "Again.", "And again."])
    assert "The chat has reached its limit" in chat.output
    assert len(chat.client.calls) == 1
    assert "[Chat · 7,100 of 12,500 tokens" in chat.output
    assert mdfile.read(chat.chat.path)[0]["ended"].startswith("stopped by the chat limit max_tokens")
    # A job keeps its own allowance.
    job([fake.finish(), LEAD_REPORT])
    assert job.meta["limits"]["max_tokens"] == 800_000


def test_chat_tool_calls_are_capped(chat, job, home):
    past_job(job, "Found invented teapots.")
    mdfile.write(home.limits, {**mdfile.read(home.limits)[0], "chat_tool_max_calls": {"memory.search": 1}},
                 mdfile.read(home.limits)[1])
    chat([fake.call("memory__search", {"query": "teapots"}), fake.call("memory__search", {"query": "kettles"}),
          fake.say("Done.")], ["Search twice."])
    assert "has used all 1 calls" in chat.tool_replies()[1]["error"]


def test_the_lead_gets_a_set_number_of_calls_per_message(chat, home):
    mdfile.write(home.limits, {**mdfile.read(home.limits)[0], "chat_calls_per_message": 2}, mdfile.read(home.limits)[1])
    chat([fake.call("memory__list", {}), fake.call("memory__list", {"crew": "default"}), fake.say("Late.")],
         ["List things.", "/quit"])
    assert len(chat.client.calls) == 2
    assert "used all 2 model calls allowed for one message" in chat.output


def test_old_turns_are_dropped_from_what_is_sent(chat, home):
    mdfile.write(home.limits, {**mdfile.read(home.limits)[0], "chat_history_chars": 1500}, mdfile.read(home.limits)[1])
    msgs = [f"FIRST-MARKER {'x' * 600}", f"second {'y' * 600}", f"third {'z' * 600}"]
    chat([fake.say("Noted.")] * 3, msgs)
    assert "FIRST-MARKER" in json.dumps(chat.client.calls[0]["messages"])
    assert "FIRST-MARKER" not in json.dumps(chat.client.calls[-1]["messages"])
    assert chat.client.calls[-1]["messages"][0]["role"] == "system"
    assert "FIRST-MARKER" in chat.transcript  # the transcript keeps everything
    assert "only its more recent turns are kept in view" in chat.output


# -- saved, not remembered ----------------------------------------------------------


def test_transcripts_are_saved_but_never_loaded_into_later_chats_or_jobs(chat, job, home):
    chat([fake.say("Understood.")], ["My invented secret word is PLUMTREE-MARKER."])
    assert "PLUMTREE-MARKER" in chat.transcript
    assert chat.chat.path.parent == home.chats_dir
    first = chat.chat.path
    chat([fake.call("memory__search", {"query": "PLUMTREE-MARKER"}), fake.say("I have no record of that.")],
         ["What is my secret word?"])
    assert chat.chat.path != first
    assert "PLUMTREE-MARKER" not in json.dumps(chat.client.calls[0]["messages"])
    assert chat.tool_replies()[0]["results"] == []
    job([fake.finish(), LEAD_REPORT])
    assert "PLUMTREE-MARKER" not in json.dumps(job.client.calls)


def test_keys_typed_in_chat_never_reach_the_model_or_the_transcript(chat, home):
    from ombro import keys

    keys.set_key(home, "TEST_MODEL_KEY", "sk-invented-0123456789abcdef")
    chat([fake.say("Never share keys.")], ["My key is sk-invented-0123456789abcdef"])
    assert "sk-invented-0123456789abcdef" not in json.dumps(chat.client.calls)
    assert "sk-invented-0123456789abcdef" not in chat.transcript


def test_a_message_of_several_lines_is_one_message(chat, home):
    chat([fake.say("Got all of it.")], ['"""', "Line one.", "Line two.", '"""'])
    assert chat.client.calls[0]["messages"][1]["content"] == "Line one.\nLine two."
    assert len(chat.client.calls) == 1


def test_web_tools_work_in_chat_through_the_fake_provider(chat, home):
    use_fake_tools(home)
    chat([fake.call("web__search", {"query": "invented teapots"}), fake.say("Found some.")], ["Search the web."])
    assert chat.tool_replies()[0]["results"]


def test_a_remember_is_in_the_transcript_before_the_chat_ends(chat, home):
    def check(prompt):
        assert "Remembered in crew default's notes.md" in chat.chat.path.read_text()
        return "/quit"

    chat([], ["/remember Keep invented reports short.", "y", check])
