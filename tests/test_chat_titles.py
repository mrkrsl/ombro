"""Chats have titles, never file names, and a past chat can be reopened to carry on.
All invented; fake model; no network."""

from __future__ import annotations

from ombro import fake, mdfile, views
from test_chat import SPEC_ARGS, ChatSession


def test_a_chat_is_named_after_its_first_exchange(home):
    s = ChatSession(home, titles=True)
    s([fake.say("Happy to help with the teapots."), fake.say("Invented Teapot Makers")], ["Find me teapot makers"])
    path = s.chat.path
    assert path.name.startswith("invented-teapot-makers-") and path.is_file()
    meta, body = mdfile.read(path)
    assert meta["title"] == "Invented Teapot Makers" and meta["titled_by"] == "model"
    assert body.startswith("# Invented Teapot Makers")
    assert not list((home.chats_dir).glob("new-chat-*.md"))  # renamed, not copied
    assert [c["title"] for c in views.chat_list(home)] == ["Invented Teapot Makers"]


def test_a_title_you_set_is_never_replaced(home):
    s = ChatSession(home, titles=True)
    s([fake.say("Fine."), fake.say("Something Else")], ["/rename Kettle Research", "Hello"])
    assert s.chat.title == "Kettle Research" and s.chat.path.name.startswith("kettle-research-")
    assert "This chat is now called 'Kettle Research'." in s.output


def test_a_confirmed_brief_gives_the_chat_its_specs_title(home):
    s = ChatSession(home)
    s([fake.call("offer_brief", {"summary": "Find teapot makers"}), fake.call("propose", SPEC_ARGS),
       fake.say("Saved.")], ["Find me teapot makers", "y", "a", "Thanks"])
    assert s.chat.title == "Find invented teapot makers" and s.chat.titled_by == "spec"


def test_new_names_the_saved_chat_not_its_file(home):
    s = ChatSession(home)
    s([fake.say("Hi.")], ["/rename Teapot Talk", "Hello", "/new"])
    assert "I've saved the last chat, 'Teapot Talk'. This is a new chat." in s.output
    assert "chats/" not in s.output.split("I've saved the last chat")[1].split("\n")[0]


def test_a_past_chat_is_reopened_and_the_lead_gets_the_conversation_again(home):
    first = ChatSession(home)
    first([fake.say("There are three invented makers:\n\nAda, Bo\n\nand Cy.")],
          ["/rename Teapot Makers", "Who makes teapots?"])
    old = first.chat.path

    second = ChatSession(home)
    second([fake.say("Ada is the oldest.")], ["/chats", "1", "Which is the oldest?"])
    assert second.chat.path == old
    sent = second.client.calls[-1]["messages"]
    said = [(m["role"], m["content"]) for m in sent if m["role"] in ("user", "assistant")]
    assert ("user", "Who makes teapots?") in said
    assert ("assistant", "There are three invented makers:\n\nAda, Bo\n\nand Cy.") in said  # paragraphs kept
    assert "reopened this chat" in said[-1][1] and said[-1][1].endswith("Which is the oldest?")
    body = mdfile.read(old)[1]
    assert body.count("**You (Chat):** Who makes teapots?") == 1 and "Which is the oldest?" in body
    assert "Lead: There are three invented makers" in second.output  # shown again
    assert len(list((home.chats_dir).glob("*.md"))) == 1  # no empty chat is left behind


def test_old_untitled_chats_are_listed_by_their_date(home):
    folder = home.chats_dir
    folder.mkdir(parents=True, exist_ok=True)
    mdfile.write(folder / "20261112-090000.md", {"kind": "chat", "started_at": "2026-11-12T09:00:00Z"},
                 "# Chat\n\n**Lead:** Hi.\n")
    assert views.chat_list(home)[0]["title"] == "Chat of 12/11/2026"


def test_an_untitled_chat_is_listed_by_what_you_first_said(home):
    folder = home.chats_dir
    folder.mkdir(parents=True, exist_ok=True)
    mdfile.write(folder / "new-chat-20261118-1109.md", {"kind": "chat", "title": ""},
                 "# New chat\n\n**You (Chat):** Can you connect to my invented mailbox and summarise the "
                 "top three emails?\n\n**Lead:** Done.\n")
    assert views.chat_list(home)[0]["title"] == "Connect to my invented mailbox"


def test_a_chat_the_model_gives_no_title_is_named_from_its_first_words(home):
    s = ChatSession(home, titles=True)
    s([fake.say("Happy to help."), fake.say("")], ["Could you please find me invented teapot makers in Lowtown?"])
    assert s.chat.title == "Find me invented teapot makers" and s.chat.titled_by == "first words"
    assert s.chat.path.name.startswith("find-me-invented-teapot-makers-")


def test_a_first_words_title_gives_way_to_the_models_next_time(home):
    s = ChatSession(home, titles=True)
    s([fake.say("Happy to help."), fake.say(""), fake.say("More."), fake.say("Lowtown Teapot Makers")],
      ["Find me teapot makers", "In Lowtown"])
    assert s.chat.title == "Lowtown Teapot Makers" and s.chat.titled_by == "model"


def test_the_title_call_leaves_room_for_a_thinking_model(home):
    s = ChatSession(home, titles=True)
    s([fake.say("Happy to help."), fake.say("Invented Teapot Makers")], ["Find me teapot makers"])
    assert s.client.calls[-1]["max_output_tokens"] >= 400


def test_an_interview_transcript_is_named_for_what_it_made(home):
    s = ChatSession(home)
    s([fake.call("offer_brief", {"summary": "Find teapot makers"}), fake.call("propose", SPEC_ARGS),
       fake.say("Saved.")], ["Find me teapot makers", "y", "a", "Thanks"])
    names = [p.name for p in home.interviews_dir.glob("*.md")]
    assert any(n.startswith("brief-find-teapots-") for n in names), names


def test_the_command_line_lists_chats_by_title(home, capsys):
    from ombro.cli import main

    s = ChatSession(home)
    s([fake.say("Hi.")], ["/rename Teapot Talk", "Hello"])
    assert main(["chats"]) == 0
    out = capsys.readouterr().out
    assert "Teapot Talk" in out and ".md" not in out


def test_opening_a_past_chat_without_saying_anything_keeps_its_place_in_the_list(home):
    first = ChatSession(home)
    first([fake.say("Hello.")], ["/rename Older Chat", "Hi"])
    older = first.chat.path
    meta = mdfile.read(older)[0]
    meta["updated_at"] = "2026-01-01T00:00:00Z"  # used long ago
    mdfile.write_atomic(older, mdfile.dump(meta, mdfile.read(older)[1]))

    second = ChatSession(home)
    second([], ["/chats", "1", "/quit"])  # opened and closed, nothing said
    assert second.chat.path == older
    assert mdfile.read(older)[0]["updated_at"] == "2026-01-01T00:00:00Z"


def test_a_reopened_chat_has_everything_said_in_it_the_brief_included(home):
    from ombro.session import record_job_end_in_chat
    from ombro.ui import JobEnded

    first = ChatSession(home)
    first([fake.call("offer_brief", {"summary": "Find teapot makers"}),
           fake.call("ask", {"questions": ["Which invented town?"], "covered": []}),
           fake.call("propose", SPEC_ARGS), fake.say("Saved.")],
          ["/rename Teapot Makers", "Find me teapot makers", "y", "Lowtown, please.", "a", "Thanks"])
    old = first.chat.path
    first.chat.jobs.append("invented-job-1")  # a job it started, which ends while the chat is closed
    first.chat._save()
    assert record_job_end_in_chat(home, JobEnded("invented-job-1", "Invented job", "stopped_by_limit",
                                                 "Your job stopped at its job_timeout limit before finishing."))
    body = mdfile.read(old)[1]
    assert "**Lead:** This sounds like a task. I can brief it: Find teapot makers." in body  # the offer
    assert "**Lead (Brief):**" in body and "Which invented town?" in body  # the brief's question…
    assert "**You (Brief):** Lowtown, please." in body  # …and the answer
    assert "**Ombro:** Your job stopped at its job_timeout limit" in body  # what came back

    second = ChatSession(home)
    second([fake.say("It stopped on time.")], ["/chats", "1", "What happened?"])
    sent = json_dump(second.client.calls[-1]["messages"])
    assert "Which invented town?" in sent and "Lowtown, please." in sent  # the Lead has the brief again
    assert "job_timeout limit" in sent and "I can brief it" in sent  # and the outcome, and its offer
    assert "Lowtown, please." in second.output and "job_timeout limit" in second.output  # shown again too


def json_dump(messages) -> str:
    import json

    return json.dumps(messages)


def test_a_reopened_chat_never_shows_its_reopened_notes(home):
    first = ChatSession(home)
    first([fake.say("Hi there.")], ["/rename Teapots", "Hello"])
    for i in range(2):  # the last note in the file is the one that used to stick to the turn before
        ChatSession(home)([fake.say(f"Reply {i}.")], ["/chats", "1", f"Question {i}"])
    again = ChatSession(home)
    again([], ["/chats", "1"])
    assert "Reply 1." in again.output and "Reopened" not in again.output
    body = mdfile.read(first.chat.path)[1]
    assert body.count("_Reopened ") == 3  # kept in the file
    from ombro.chat import parse_entries
    assert all(not e.startswith("**") or "_Reopened" not in e for e in parse_entries(body))
