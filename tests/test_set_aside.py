"""A question set aside: a new message, sent while the chat waits for an answer it isn't, goes to
the Lead as an ordinary message, and the question goes unanswered. Invented data, scripted models."""

from __future__ import annotations

from typing import Any

from ombro import fake
from ombro.fake import ScriptedClient
from ombro.session import Session
from ombro.ui import UI, Event, Field, Option, SetAside, sets_aside
from test_live_jobs import in_thread  # noqa: F401 - a fixture


def test_what_sets_a_question_aside():
    yes = Field("go", "Go ahead?", "yesno", aside=True)
    assert sets_aside(yes, "Actually, can you check the dates first?")
    assert not sets_aside(yes, "y") and not sets_aside(yes, "no") and not sets_aside(yes, "")
    assert not sets_aside(yes, "/help")  # commands still wait for the answer
    assert not sets_aside(Field("go", "Go ahead?", "yesno"), "Something else")  # only where allowed

    how = Field(
        "how",
        "Report back or live?",
        "choice",
        aside=True,
        other="not now",
        pattern=r"(?i)(not now|no|n)",
        options=[Option("report", "Report back"), Option("live", "Do it live")],
    )
    assert not sets_aside(how, "report") and not sets_aside(how, "Do it live") and not sets_aside(how, "not now")
    assert sets_aside(how, "What's the weather like?")
    assert not sets_aside(Field("t", "Anything?", "text", aside=True), "Some words")  # text is the answer

    remember = Field(
        "remember",
        "",
        "choice",
        aside=True,
        other="@crew for another crew",
        pattern=r"@\S.*",
        options=[Option("y", "yes"), Option("n", "no")],
    )
    assert not sets_aside(remember, "@writers") and sets_aside(remember, "Tell me more about it")


class AsideUI(UI):
    """Answers from a list; an entry ("aside", text) sends text as a new message instead."""

    def __init__(self, answers: list[Any]):
        self.answers = list(answers)
        self.asked: list[Field] = []
        self.shown: list[Event] = []
        self.session: Session | None = None

    def show(self, event: Event) -> None:
        self.shown.append(event)

    def ask(self, f: Field) -> Any:
        self.asked.append(f)
        a = self.answers.pop(0)
        if isinstance(a, tuple) and a[0] == "aside":
            assert sets_aside(f, a[1]), f"{a[1]!r} would answer {f.name}"
            self.session.send_next(a[1])
            raise SetAside(f)
        return a


def session(home, script, answers) -> tuple[Session, AsideUI, ScriptedClient]:
    client = ScriptedClient(script)
    ui = AsideUI(answers)
    s = Session(home, ui, client_factory=lambda spec: client, titles=False)
    ui.session = s
    return s, ui, client


def test_a_message_sets_aside_a_yes_or_no_mid_turn(home, tmp_path, monkeypatch):
    """A shared folder write waits for yes; the new message leaves it unwritten, any other tool call in
    the same reply is ignored, and the Lead hears the message next."""
    from conftest import use_fake_tools
    from ombro import folders

    me = tmp_path / "user"
    folder = me / "Documents" / "Invented"
    folder.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(me))
    folders.add(home, folder.resolve(), "invented", "write")
    use_fake_tools(home)
    first = fake.call("folders__write", {"path": "shared/invented/notes.md", "content": "Invented notes.\n"})
    second = fake.call("folders__write", {"path": "shared/invented/more.md", "content": "More.\n"})
    both = {"tool_calls": first["tool_calls"] + second["tool_calls"]}
    s, ui, client = session(
        home, [both, fake.say("Here are the dates.")], [("aside", "Hold on, which dates did we agree?")]
    )
    assert s.chat_turn("Write my invented notes.") is True
    assert not (folder / "notes.md").exists() and not (folder / "more.md").exists()
    assert ui.asked[0].name == "folder_write" and len(ui.asked) == 1
    last = client.calls[-1]["messages"]
    tools = [m for m in last if m["role"] == "tool"]
    assert "set this question aside" in tools[0]["content"]
    assert last[-1]["content"].endswith("Hold on, which dates did we agree?")
    assert any("set aside" in getattr(e, "text", "") for e in ui.shown)
    s.save()
    text = s.chat.path.read_text()
    assert "You set the question aside (Go ahead?)" in text and "Here are the dates." in text
    assert not s.queued


def test_after_a_live_job_a_message_sets_aside_keep_it_as_a_brief(home, in_thread):
    """A live job's own question takes what is typed as its answer (the job is waiting on it, and the
    chat can't talk to the Lead until the job ends); "keep it as a brief?" afterwards can be set aside."""
    from conftest import LEAD_REPORT
    from test_live_jobs import offer

    in_thread[:] = [
        fake.call("ask_you", {"question": "Sourdough only, or any bread?", "options": ["Sourdough", "Any"]}),
        fake.finish("Listed three."),
        LEAD_REPORT,
    ]
    s, ui, client = session(
        home,
        [offer(), fake.say("Rye is a good start.")],
        ["live", "Only ones that open early", ("aside", "What about rye bread?")],
    )
    assert s.chat_turn("Find me bakeries in Lowtown.") is True
    live_answer = next(f for f in ui.asked if f.name == "live_answer")
    assert not sets_aside(live_answer, "Only ones that open early")  # an answer, not a new message
    assert ui.asked[-1].name == "after_live"
    assert not home.specs_dir.is_dir() or not list(home.specs_dir.glob("*.md"))  # not kept as a brief
    assert client.calls[-1]["messages"][-1]["content"].endswith("What about rye bread?")
    s.save()
    assert "You set the question aside (Want to run this again later?" in s.chat.path.read_text()
