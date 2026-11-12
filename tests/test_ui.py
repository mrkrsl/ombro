"""The interface seam: typed events, fields and forms as plain data, and the
command line's adapter. All data is invented; no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ombro import fake
from ombro.fake import ScriptedClient
from ombro.interviews import SetupPurpose, start
from ombro.settings import load_limits
from ombro.ui import (
    UI, ConsoleUI, Failure, Field, FileView, Form, LeadSaid, ModeChanged, Note, Option, Progress, ProposalShown,
    Usage, option_text, prompt_text, render,
)


class ScriptedUI(UI):
    """An interface that records every event and answers from a script.
    Answers may be callables, given the field."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.events: list = []
        self.fields: list[Field] = []
        self.edited: list[list[Path]] = []

    def show(self, event):
        json.dumps(event.to_dict())  # every event is plain data
        self.events.append(event)

    def ask(self, f):
        json.dumps(f.to_dict())
        self.fields.append(f)
        if not self.answers:
            raise AssertionError(f"no scripted answer left for {f.label!r}")
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a(f) if callable(a) else a

    def edit(self, paths):
        self.edited.append(list(paths))

    def of(self, kind):
        return [e for e in self.events if isinstance(e, kind)]


# -- plain data ------------------------------------------------------------------


def test_events_are_plain_data_with_a_type():
    events = [
        LeadSaid("Hello", asks=True), Progress("2 of 5"), Note("Saved", items=["a"], level="done"),
        Failure("It broke"), ProposalShown([FileView("x.md", "/tmp/x.md", "text", False)], ["careful"]),
        Usage("chat", "Chat", 120, 150_000, 2), ModeChanged("Brief", "Brief mode"),
    ]
    data = json.loads(json.dumps([e.to_dict() for e in events]))
    assert [d["type"] for d in data] == ["lead", "progress", "note", "error", "proposal", "usage", "mode"]
    assert data[4]["files"][0]["label"] == "x.md"


def test_forms_and_fields_are_plain_data():
    form = Form("model", "Connect a model", [
        Field("endpoint", "Endpoint", "choice", default="1", options=[Option("1", "DeepSeek")]),
        Field("key", "API key", "secret", required=True),
    ], intro=["Keys never reach a model."])
    data = json.loads(json.dumps(form.to_dict()))
    assert data["fields"][1]["kind"] == "secret" and data["fields"][0]["options"][0]["label"] == "DeepSeek"
    with pytest.raises(ValueError):
        Field("x", "X", "colour")


def test_field_checks_are_the_same_for_every_interface():
    url = Field("url", "Endpoint base URL", pattern=r"https?://\S+", invalid="The URL must start with http:// or https://.")
    assert url.check("ftp://x") == "The URL must start with http:// or https://."
    assert url.check("https://models.invalid/v1") is None
    assert Field("m", "Model name", required=True, invalid="A model name is needed.").check("  ") == "A model name is needed."
    pick = Field("d", "", "choice", options=[Option("a", "accept", ("yes",)), Option("q", "quit")], invalid="Type a or q.")
    assert pick.choose("YES") == "a" and pick.choose("quit") == "q" and pick.check("x") == "Type a or q."
    assert Field("d", "", "choice", options=[Option("y", "yes")], other="@crew").check("@ops") is None


# -- the command line keeps its wording ----------------------------------------------


@pytest.mark.parametrize("f, expected", [
    (Field("a", "You", multiline=True), "You: "),
    (Field("a", "You", mode="Brief"), "[Brief] You: "),
    (Field("a", "What should change?"), "What should change? "),
    (Field("a", "Model name", default="deepseek-chat"), "Model name [deepseek-chat]: "),
    (Field("a", "Connect a different model?", "yesno"), "Connect a different model? [y/N] "),
    (Field("a", "Start it now?", "yesno", default=True), "Start it now? [Y/n] "),
    (Field("a", "Press Enter when you have saved them.", "wait"), "Press Enter when you have saved them. "),
    (Field("a", "", "choice", options=[Option("a", "accept and write these files"), Option("c", "ask for a change"),
                                         Option("e", "edit by hand"), Option("q", "quit")]),
     "[a]ccept and write these files, ask for a [c]hange, [e]dit by hand, or [q]uit? "),
    (Field("a", "Share it with Ombro's runs as 'brand'?", "choice",
           options=[Option("w", "write"), Option("r", "read only"), Option("n", "no")]),
     "Share it with Ombro's runs as 'brand'? [w]rite, [r]ead only, or [n]o: "),
    (Field("a", "", "choice", mode="Chat", options=[Option("y", "yes"), Option("e", "edit the wording"),
                                                      Option("n", "no")], other="@crew for another crew"),
     "[Chat] [y]es, [e]dit the wording, [n]o, or @crew for another crew? "),
])
def test_command_line_prompts(f, expected):
    assert prompt_text(f) == expected


def test_command_line_rendering_of_events():
    assert render(LeadSaid("Hi")) == ["\nLead: Hi\n"]
    assert render(LeadSaid("Who?", asks=True)) == ["Lead: Who?\n"]
    assert render(Progress("●○ 1 of 2 covered")) == ["\n[●○ 1 of 2 covered]"]
    assert render(Failure("The model call failed.")) == ["\nThe model call failed."]
    assert render(Note("Gaps:", items=["one"], spaced=True)) == ["\nGaps:", "  - one"]
    assert render(Usage("interview", "setup", 1200, 100_000, final=True)) == ["(Interview used 1,200 of 100,000 tokens.)"]
    assert render(Usage("chat", "Chat", 50, 150_000, 1)) == ["[Chat · 50 of 150,000 tokens · 1 tool call(s)]"]


def test_console_asks_again_until_an_answer_fits():
    said, answers = [], iter(["", "x", "https://models.invalid/v1"])
    ui = ConsoleUI(ask=lambda p: next(answers), say=said.append)
    f = Field("url", "Endpoint base URL", required=True, pattern=r"https?://\S+",
              invalid="The URL must start with http:// or https://.")
    assert ui.ask(f) == "https://models.invalid/v1"
    assert said == ["The URL must start with http:// or https://."] * 2


def test_console_secret_is_read_hidden_and_yes_no_takes_its_default():
    ui = ConsoleUI(ask=lambda p: "", ask_secret=lambda p: "  sk-INVENTED  ", say=lambda t: None)
    assert ui.ask(Field("k", "API key", "secret")) == "sk-INVENTED"
    assert ui.ask(Field("go", "Start it now?", "yesno", default=True)) is True
    assert ui.ask(Field("go", "Again?", "yesno")) is False
    assert option_text([Option("r", "read"), Option("n", "no")]) == "[r]ead, or [n]o"


def test_console_form_asks_each_field_in_turn():
    answers = iter(["2", "invented-model"])
    ui = ConsoleUI(ask=lambda p: next(answers), say=lambda t: None)
    got = ui.form(Form("f", "F", [Field("n", "N", "choice", options=[Option("1", "one"), Option("2", "two")]),
                                  Field("m", "Model name", required=True)]))
    assert got == {"n": "2", "m": "invented-model"}


# -- an interview through typed events ------------------------------------------------

SETUP = {
    "name": "Ada", "work": "I run an invented bakery in Lowtown.", "working_style": "Check in when unsure.",
    "reports": "Short.", "standing_rules": "- Never guess a figure.", "soul": "You are brisk and kind.",
}


def test_an_interview_speaks_only_in_events(home):
    ui = ScriptedUI(["I run an invented bakery.", "a"])
    client = ScriptedClient([
        fake.call("ask", {"questions": ["Who are you?"], "covered": ["work"]}),
        fake.call("propose", SETUP),
    ])
    out = start(home, SetupPurpose(home), "default", load_limits(home), ui=ui, client_factory=lambda s: client)
    assert out.status == "confirmed"
    asked = [e for e in ui.of(LeadSaid) if e.asks]
    assert asked and asked[0].text == "Who are you?"
    assert ui.of(Progress) and "covered" in ui.of(Progress)[0].text
    (proposal,) = ui.of(ProposalShown)
    assert {f.label for f in proposal.files} == {"preferences/instructions.md", "lead/soul.md"}
    assert all(f.replaces for f in proposal.files)
    decision = ui.fields[-1]
    assert decision.kind == "choice" and [o.key for o in decision.options] == ["a", "c", "e", "q"]
    assert any(n.level == "done" and n.text.startswith("Wrote ") for n in ui.of(Note))
    (usage,) = ui.of(Usage)
    assert usage.scope == "interview" and usage.final and usage.total_tokens > 0


def test_quitting_from_an_interface_writes_nothing(home):
    before = home.instructions.read_text()
    ui = ScriptedUI([KeyboardInterrupt()])
    client = ScriptedClient([fake.call("ask", {"questions": ["Who are you?"]})])
    out = start(home, SetupPurpose(home), "default", load_limits(home), ui=ui, client_factory=lambda s: client)
    assert out.status == "quit" and home.instructions.read_text() == before


def test_a_brief_inside_chat_asks_in_brief_mode_and_chat_leaves_it(home):
    from ombro.chat import Chat

    client = ScriptedClient([fake.call("ask", {"questions": ["What is the task?"]})])
    ui = ScriptedUI(["/brief", "/chat"])
    chat = Chat(home, ui=ui, client_factory=lambda s: client)
    chat.start()
    assert chat.handle(ui.ask(Field("message", "You")))
    assert ui.fields[-1].mode == "Brief"
    modes = [e.mode for e in ui.of(ModeChanged)]
    assert modes == ["Brief", "Chat"] and chat.mode == "Chat"
    chat.close()
    assert chat.path.is_file()


def test_chat_removes_keys_from_what_an_interface_sends(home):
    from ombro import keys
    from ombro.chat import Chat

    keys.set_key(home, "TEST_SECRET_KEY", "sk-test-INVENTED-0123456789")
    client = ScriptedClient([fake.say("Noted.")])
    ui = ScriptedUI([])
    chat = Chat(home, ui=ui, client_factory=lambda s: client)
    assert chat.handle("my key is sk-test-INVENTED-0123456789")
    assert "INVENTED-0123456789" not in json.dumps(client.calls[0]["messages"])
    assert ui.of(LeadSaid)[0].text == "Noted." and ui.of(Usage)[0].scope == "chat"


# -- the scripted flows as forms ---------------------------------------------------


def _shown_text(ui: ScriptedUI) -> str:
    return json.dumps([e.to_dict() for e in ui.events])


def test_connecting_a_model_is_a_form_with_a_hidden_key(tmp_path):
    from ombro import keys
    from ombro.client import ModelReply
    from ombro.home import Home
    from ombro.init_cmd import connected_model, run_init

    class Client:
        def complete(self, *a, **k):
            return ModelReply(content="OK")

    home = Home(tmp_path / "fresh")
    ui = ScriptedUI(["2", "https://models.invalid/v1", "invented-model", "INVENTED_API_KEY",
                     "sk-test-INVENTED-0123456789", "", False])
    assert run_init(home, ui=ui, client_factory=lambda s, h: Client())
    kinds = [(f.name, f.kind) for f in ui.fields]
    assert kinds[:5] == [("endpoint", "choice"), ("url", "text"), ("model", "text"), ("key_name", "text"),
                         ("key", "secret")]
    assert ui.fields[0].options[0].label.startswith("DeepSeek")
    assert keys.get_key(home, "INVENTED_API_KEY") == "sk-test-INVENTED-0123456789"
    assert connected_model(home).model == "invented-model"
    assert "INVENTED-0123456789" not in _shown_text(ui)
    assert any(isinstance(e, Progress) and e.text.startswith("Testing invented-model") for e in ui.events)


def test_the_model_form_holds_its_own_checks():
    from ombro.init_cmd import ENDPOINTS, model_form

    form = model_form(ENDPOINTS[1])
    by_name = {f.name: f for f in form.fields}
    assert by_name["url"].check("not a url") and by_name["key_name"].check("lower") and not by_name["key_name"].check("")
    assert [f.name for f in model_form(ENDPOINTS[0]).fields] == ["model"]  # DeepSeek needs only the model name
    assert model_form(ENDPOINTS[0]).fields[0].check("") is None  # its default is taken


def test_setting_a_key_from_an_interface(home):
    from ombro import cli, keys

    ui = ScriptedUI(["sk-test-INVENTED-9876543210"])
    assert cli.set_key_flow(home, "INVENTED_KEY", ui) == 0
    assert ui.fields[0].kind == "secret" and keys.get_key(home, "INVENTED_KEY") == "sk-test-INVENTED-9876543210"
    assert "9876543210" not in _shown_text(ui)
    assert cli.set_key_flow(home, "OTHER_KEY", ScriptedUI([KeyboardInterrupt()])) == 1


def test_adding_a_server_from_an_interface(home):
    import argparse

    from ombro import keys
    from ombro.server_cmd import add_flow
    from ombro.settings import load_tool_settings

    args = argparse.Namespace(name=None, command=None, url=None, env=None, header=None, secret=None,
                              server_class=None, no_prompt=False, yes=False)
    ui = ScriptedUI(["notes", "l", "uvx invented-notes-mcp==1.2.3", "NOTES_TOKEN", "invented-secret-value-123", True])
    assert add_flow(home, args, ui) == 0
    assert [f.kind for f in ui.fields] == ["text", "choice", "text", "text", "secret", "yesno"]
    assert keys.get_key(home, "NOTES_NOTES_TOKEN") == "invented-secret-value-123"
    assert "invented-secret-value-123" not in _shown_text(ui) + home.tools.read_text()
    assert "notes" in load_tool_settings(home).servers


def test_granting_a_folder_from_an_interface(home, tmp_path, monkeypatch):
    from ombro import folders
    from ombro.folders_cmd import add_flow

    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "Invented Brand"
    target.mkdir()
    ui = ScriptedUI(["w", True])
    assert add_flow(home, str(target), None, None, False, ui) == 0
    assert [f.kind for f in ui.fields] == ["choice", "yesno"]
    (g,) = folders.load(home)[0].values()
    assert g.access == "write" and g.path == "~/Invented Brand"
    assert add_flow(home, str(target), "again", None, False, ScriptedUI(["n"])) == 1
