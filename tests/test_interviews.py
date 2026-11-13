"""Interviews with a scripted model and scripted answers. All data is invented; no network."""

from __future__ import annotations

import json

import pytest

from conftest import LEAD_REPORT
from ombro import fake, keys, mdfile, runs
from ombro.fake import ScriptedClient
from ombro.home import LEAD_SOUL
from ombro.interviews import BriefPurpose, RolePurpose, SetupPurpose, record_insufficient_brief, start
from ombro.roles import crew_roles, load_lead
from ombro.settings import load_limits


class Answers:
    """Scripted user input. Entries may be callables, run at the moment of asking."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            raise AssertionError(f"no scripted answer left for {prompt!r}")
        a = self.answers.pop(0)
        return a(prompt) if callable(a) else a


class Session:
    def __init__(self, home):
        self.home = home
        self.said: list[str] = []
        self.client: ScriptedClient | None = None
        self.edits: list = []

    def __call__(self, purpose, script, answers, *, edit=None, crew="default"):
        self.client = ScriptedClient(script)
        self.answers = Answers(answers)
        limits = load_limits(self.home)
        self.outcome = start(
            self.home, purpose, crew, limits,
            ask=self.answers, say=self.said.append, edit=edit, client_factory=lambda spec: self.client,
        )
        return self.outcome

    @property
    def output(self) -> str:
        return "\n".join(self.said)

    def sent_to_model(self) -> str:
        return json.dumps([c["messages"] for c in self.client.calls])

    def tool_replies(self) -> list[str]:
        """What the harness told the Lead in reply to each of its tool calls."""
        seen, out = set(), []
        for c in self.client.calls:
            for m in c["messages"]:
                if m["role"] == "tool" and m["tool_call_id"] not in seen:
                    seen.add(m["tool_call_id"])
                    out.append(m["content"])
        return out


@pytest.fixture
def talk(home):
    return Session(home)


SETUP = {
    "name": "Ada",
    "work": "I run an invented bakery in Lowtown.",
    "working_style": "Check in only when something is ambiguous.",
    "reports": "Short, with the answer first.",
    "standing_rules": "- Never guess a figure.",
    "soul": "You are brisk and kind, and you say plainly when something went wrong.",
}


# -- set-up ------------------------------------------------------------------------


def test_setup_writes_nothing_until_accepted(talk, home):
    soul_path = home.lead_dir / "soul.md"
    before = home.instructions.read_text()

    def unchanged_so_far(messages, tools):
        assert home.instructions.read_text() == before and soul_path.read_text() == LEAD_SOUL
        return fake.call("propose", {**SETUP, "soul": "You are brisk, kind and short."})

    out = talk(
        SetupPurpose(home),
        [
            fake.say("Who are you, and what do you do?"),
            fake.say("Here is what I understood: you bake. How do you like reports?"),
            fake.call("propose", SETUP),
            unchanged_so_far,
        ],
        ["I run an invented bakery.", "Short, answer first.", "c", "Make the soul shorter.", "a"],
    )
    assert out.status == "confirmed"
    assert "I run an invented bakery in Lowtown." in home.instructions.read_text()
    assert soul_path.read_text() == "You are brisk, kind and short.\n"
    assert (soul_path.parent / "soul.previous.md").read_text() == LEAD_SOUL  # the old text is kept
    # The change request went back to the Lead as the tool's result.
    assert "Make the soul shorter." in talk.tool_replies()[0]
    # The new preferences are read the way a run reads them.
    from ombro.settings import load_preferences

    assert "invented bakery" in load_preferences(home).instructions
    assert "Who are you" in out.transcript.read_text()


def test_quitting_at_the_proposal_writes_nothing(talk, home):
    before = home.instructions.read_text()
    out = talk(SetupPurpose(home), [fake.call("propose", SETUP)], ["q"])
    assert out.status == "quit"
    assert home.instructions.read_text() == before
    assert mdfile.read(out.transcript)[0]["outcome"] == "quit"


def test_slash_quit_during_questions_writes_nothing(talk, home):
    before = home.instructions.read_text()
    out = talk(SetupPurpose(home), [fake.say("Who are you?")], ["/quit"])
    assert out.status == "quit" and home.instructions.read_text() == before


def test_edit_by_hand_then_accept_writes_the_edited_version(talk, home):
    opened = []

    def edit(paths):
        assert len(paths) == 1  # one file at a time, so every one is opened
        opened.append(paths[0].name)
        if paths[0].name.endswith("soul.md"):
            paths[0].write_text("You are an invented Lead, edited by hand.\n")

    out = talk(SetupPurpose(home), [fake.call("propose", SETUP)], ["e", "a"], edit=edit)
    assert out.status == "confirmed" and out.edited
    assert opened == ["1-preferences-instructions.md", "2-lead-soul.md"]
    assert "Opening lead/soul.md in your editor (2 of 2)." in talk.output
    soul = (home.lead_dir / "soul.md").read_text()
    assert soul == "You are an invented Lead, edited by hand.\n"
    assert "You edited it to" in out.transcript.read_text()


def test_an_edit_that_breaks_the_file_is_refused(talk, home):
    def edit(paths):
        paths[0].write_text("---\nnot: [closed\n---\nBroken.\n")


    out = talk(SetupPurpose(home), [fake.call("propose", SETUP)], ["e", "a"], edit=edit)
    assert out.status == "confirmed"
    assert "cannot be used" in talk.output
    assert "invented bakery" in home.instructions.read_text()  # the Lead's version, not the broken edit


# -- adding a role ------------------------------------------------------------------

ROLE = {
    "name": "fact-checker",
    "kind": "specialist",
    "description": "Checks claims in a workspace file against sources and returns a verdict per claim.",
    "tools": ["web.search", "web.fetch", "workspace.read", "workspace.write"],
    "soul": "You check invented claims. You never rewrite the draft.",
}


def test_role_interview_refuses_tools_and_skills_that_do_not_exist(talk, home):
    out = talk(
        RolePurpose(home, "default", load_limits(home)),
        [
            fake.call("propose", {**ROLE, "tools": ["web.search", "email.send"]}),
            fake.call("propose", {**ROLE, "skills": ["invented-skill"]}),
            fake.call("propose", ROLE),
        ],
        ["a"],
    )
    replies = talk.tool_replies()
    assert "no such tool: email.send" in replies[0] and "workspace.read" in replies[0]
    assert "no such skill: invented-skill" in replies[1]
    assert out.status == "confirmed"
    roles = crew_roles(home, "default", load_limits(home))
    assert roles["fact-checker"].tools == ROLE["tools"]
    assert "never rewrite" in roles["fact-checker"].soul


def test_role_interview_is_shown_the_tools_that_exist(talk, home):
    talk(RolePurpose(home, "default", load_limits(home), "checker"), [fake.say("What is it for?")], ["/quit"])
    system = talk.client.calls[0]["messages"][0]["content"]
    for tool in ("web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list"):
        assert f"- {tool}:" in system
    assert "researcher (specialist; tools: none)" in system  # the crew so far
    assert "'checker'" in talk.client.calls[0]["messages"][1]["content"]


def test_role_without_tools_comes_with_a_warning(talk, home):
    out = talk(RolePurpose(home, "default", load_limits(home)), [fake.call("propose", {**ROLE, "tools": []})], ["a"])
    assert out.status == "confirmed"
    assert "this role has no tools" in talk.output
    assert "tools" not in mdfile.read(home.roles_dir("default") / "fact-checker" / "config.md")[0]


def test_role_interview_refuses_an_existing_name_and_the_lead(talk, home):
    out = talk(
        RolePurpose(home, "default", load_limits(home)),
        [fake.call("propose", {**ROLE, "name": "researcher"}), fake.call("propose", {**ROLE, "name": "lead"})],
        [],
    )
    replies = talk.tool_replies()
    assert "already exists" in replies[0] and "one Lead" in replies[1]
    assert out.status == "failed"  # the script ran out; nothing was written
    assert not (home.roles_dir("default") / "fact-checker").exists()


# -- briefing a task ----------------------------------------------------------------

SPEC = {
    "name": "teapot-survey",
    "title": "A list of three invented teapot makers in Lowtown",
    "done_looks_like": "workspace/makers.md with three makers and one fact each",
    "users": "the user, reading it once to pick a supplier",
    "binding_constraint": "every fact has a source",
    "waste": "makers outside Lowtown",
    "non_goals": "prices, reviews",
}


def test_brief_refuses_a_spec_with_a_required_answer_missing(talk, home):
    out = talk(
        BriefPurpose(home, "default", load_limits(home), "Find invented teapot makers."),
        [fake.call("propose", {**SPEC, "waste": "", "non_goals": " "}), fake.call("propose", SPEC)],
        ["a"],
    )
    reply = talk.tool_replies()[0]
    assert "A waste would be" in reply and "Not wanted" in reply
    assert out.status == "confirmed"
    meta, body = mdfile.read(home.specs_dir / "teapot-survey.md")
    assert meta["crews"] == ["default"]
    for line in ("Done looks like: workspace/makers.md", "Who uses it and how:", "Binding constraint: every fact",
                 "A waste would be: makers outside", "Not wanted: prices"):
        assert line in body
    assert "Find invented teapot makers." in talk.client.calls[0]["messages"][1]["content"]


def test_insufficient_brief_goes_back_to_the_user_first(talk, home):
    out = talk(
        BriefPurpose(home, "default", load_limits(home), "Something about teapots."),
        [fake.call("insufficient", {"gaps": ["What done looks like is not stated."]}), fake.call("propose", SPEC)],
        ["A file with three makers.", "a"],
    )
    assert "The Lead says the brief has gaps" in talk.output
    assert "A file with three makers." in talk.tool_replies()[0]
    assert out.status == "confirmed"


def test_insufficient_brief_ends_in_a_report_with_no_roles_briefed(talk, home):
    gaps = ["Who uses the result is not stated.", "The deadline contradicts the scope."]
    out = talk(
        BriefPurpose(home, "default", load_limits(home), "Something about teapots.", then_run=True),
        [fake.call("insufficient", {"gaps": gaps})],
        ["/end"],
    )
    assert out.status == "insufficient" and out.gaps == gaps
    assert not list((home.specs_dir).glob("*.md"))
    run_dir = record_insufficient_brief(home, ["default"], load_limits(home), out, "Something about teapots.")
    meta = runs.read_run(run_dir)[0]
    assert meta["status"] == "insufficient_brief"
    actions = [json.loads(line)["action"] for line in (run_dir / "journal.jsonl").read_text().splitlines()]
    assert "briefed" not in actions and "insufficient_brief" in actions
    report = (run_dir / "report.md").read_text()
    assert "**Status:** insufficient_brief" in report and "No roles were briefed." in report
    for g in gaps:
        assert g in report
    assert (run_dir / "interview.md").is_file()


# -- limits -------------------------------------------------------------------------


def test_interview_usage_has_its_own_cap(talk, home):
    mdfile.write(home.limits, {"allowance": "moderate", "interview_max_tokens": 12000})
    out = talk(
        SetupPurpose(home),
        [fake.say("Who are you?", usage={"input": 8000, "output": 100}), fake.say("And reports?")],
        ["An invented baker."],
    )
    assert out.status == "limit" and "max_tokens" in out.detail
    assert out.usage["total_tokens"] <= 12000
    assert talk.client.remaining == 1  # the call that would pass the cap never happened
    # The run cap is untouched by the interview's.
    assert load_limits(home).max_tokens == 400_000


def test_interview_usage_is_kept_apart_from_the_run(home, monkeypatch, capsys):
    """`ombro run` with no spec: interview, confirm, then the run, each with its own usage."""
    from ombro import cli

    script = [
        fake.say("What should be done?", usage={"input": 2000, "output": 50}),
        fake.call("propose", SPEC, usage={"input": 2500, "output": 300}),
        fake.brief("researcher", "Find three invented teapot makers.", usage={"input": 100, "output": 10}),
        fake.result("Found them.", usage={"input": 100, "output": 10}),
        fake.finish(usage={"input": 100, "output": 10}),
        {**LEAD_REPORT, "usage": {"input": 100, "output": 10}},
    ]
    (home.root / "script.json").write_text(json.dumps(script))
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json"}, "cheap": "default"}})
    answers = Answers(["Find invented teapot makers.", "a"])
    monkeypatch.setattr("builtins.input", answers)
    assert cli.main(["run"]) == 0
    run_dir = runs.latest_run(home, "default")
    meta = runs.read_run(run_dir)[0]
    assert meta["status"] == "completed"
    assert meta["interview_usage"]["total_tokens"] == 4850
    assert meta["usage"]["total_tokens"] == 440  # the run counts only its own calls
    assert "interview; spec saved at" in meta["source"]
    assert (run_dir / "interview.md").is_file()
    assert "Task interview (separate from the run's limits): 4,850 tokens" in (run_dir / "report.md").read_text()
    assert (home.specs_dir / "teapot-survey.md").is_file()


def test_ombro_run_with_an_insufficient_brief(home, monkeypatch):
    from ombro import cli

    script = [fake.say("What is the task?"), fake.call("insufficient", {"gaps": ["No result was described."]})]
    (home.root / "script.json").write_text(json.dumps(script))
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json"}, "cheap": "default"}})
    monkeypatch.setattr("builtins.input", Answers(["Teapots, somehow.", "/end"]))
    assert cli.main(["run"]) == 1
    run_dir = runs.latest_run(home, "default")
    assert runs.read_run(run_dir)[0]["status"] == "insufficient_brief"
    assert "Teapots, somehow." in (run_dir / "spec.md").read_text()
    assert "No result was described." in (run_dir / "report.md").read_text()


def test_a_lead_that_never_asks_is_stopped(talk, home):
    out = talk(SetupPurpose(home), [fake.call("propose", {"instructions": "", "soul": f"x{i}"}) for i in range(8)], [])
    assert out.status == "limit" and "interview_steps" in out.detail


# -- keys ---------------------------------------------------------------------------


def test_keys_never_reach_the_model_or_the_transcript(talk, home):
    keys.set_key(home, "TEST_MODEL_KEY", "invented-secret-value-24680")
    pasted = "sk-INVENTEDinventedINVENTED1234567890"
    out = talk(
        SetupPurpose(home),
        [fake.say("Who are you?"), fake.say("Thanks."), fake.call("propose", SETUP)],
        ["My key is invented-secret-value-24680, use it.", f"Also {pasted}", "a"],
    )
    assert out.status == "confirmed"
    sent = talk.sent_to_model()
    transcript = out.transcript.read_text()
    for secret in ("invented-secret-value-24680", pasted):
        assert secret not in sent and secret not in transcript
    assert "[key removed]" in sent and "[key removed]" in transcript
    assert "removed before it reached the model" in talk.output
    # The rules tell the Lead never to ask for keys.
    assert "Never ask for API keys" in talk.client.calls[0]["messages"][0]["content"]


# -- init: the set-up interview after the connection step ---------------------------


def test_init_holds_the_setup_interview_and_offers_a_role(tmp_path):
    from ombro.home import Home
    from ombro.init_cmd import run_init

    h = Home(tmp_path / "fresh")
    client = ScriptedClient([
        fake.say("OK"),  # the connection test
        fake.say("Who are you?"),
        fake.call("propose", SETUP),
        fake.say("What should the role do?"),
        fake.call("propose", ROLE),
    ])
    said = []
    ask = Answers(["", "1", "deepseek-chat", "", "An invented baker.", "a", "", "y", "Check facts.", "a", "n", "n"])
    secret = Answers(["sk-test-INVENTED-model", ""])
    assert run_init(h, ask=ask, ask_secret=secret, say=said.append, client_factory=lambda s, home: client)
    assert "invented bakery" in h.instructions.read_text()
    assert (h.roles_dir("default") / "fact-checker" / "soul.md").is_file()
    assert "sk-test-INVENTED-model" not in json.dumps([c["messages"] for c in client.calls])
    assert len(list((h.interviews_dir).glob("*-setup.md"))) == 1


# -- keeping each interview to its purpose, and showing progress -------------------


def test_setup_takes_the_name_from_preferences_and_never_asks_for_it(talk, home):
    from ombro.settings import set_preference

    set_preference(home, "name", "Ada")
    out = talk(SetupPurpose(home), [fake.call("propose", {k: v for k, v in SETUP.items() if k != "name"})], ["a"])
    assert out.status == "confirmed"
    assert "name" not in [k for k, _ in SetupPurpose.topics]
    assert "The user's name ==\nAda" in talk.sent_to_model() or "Ada" in talk.sent_to_model()
    text = home.instructions.read_text()
    assert "# About me\n\nMy name is Ada." in text
    for heading in ("# How I like to work", "# How I want reports", "# Standing rules"):
        assert heading in text


def test_each_interview_is_told_to_keep_to_its_own_purpose(talk, home):
    talk(SetupPurpose(home), [fake.say("Name?")], ["/quit"])
    system = talk.client.calls[0]["messages"][0]["content"]
    assert "Do not gather the details of any one task" in system and "`ombro brief`" in system
    talk(RolePurpose(home, "default", load_limits(home)), [fake.say("What for?")], ["/quit"])
    system = talk.client.calls[0]["messages"][0]["content"]
    assert "briefed for many different tasks" in system and "Keep task details out" in system
    assert "standing preferences, not a task" in system or "User context" not in system


def test_ask_shows_how_far_through_the_interview_is(talk, home):
    out = talk(
        BriefPurpose(home, "default", load_limits(home), "Invented teapots."),
        [
            fake.call("ask", {"questions": ["What is the task, and why?"], "covered": [], "progress": "just started"}),
            fake.call("ask", {"questions": ["What would be a waste?", "What is not wanted?"],
                              "covered": ["task", "done", "users", "constraint"], "progress": "about halfway"}),
            fake.call("propose", SPEC),
        ],
        ["Three makers, for a supplier choice.", "Anything outside Lowtown; prices.", "a"],
    )
    assert out.status == "confirmed"
    assert "○○○○○○  0 of 6 covered" in talk.output
    assert "●●●●○○  4 of 6 covered  ·  still to cover: what would be a waste, non-goals  ·  about halfway" in talk.output
    # Two questions come as separate paragraphs after "Two questions:".
    assert "Lead: Two questions:\n\nWhat would be a waste?\n\nWhat is not wanted?" in talk.output
    assert '"answer": "Anything outside Lowtown; prices."' in talk.tool_replies()[1]
    assert "usually 5 to 8 questions" in talk.output
    assert "save it as 'teapot-survey'" in talk.answers.asked[-1]
    assert "about halfway" in out.transcript.read_text()


def test_standards_are_fixed_and_the_role_interview_chooses_tools_itself(talk, home):
    from ombro import prompts

    talk(RolePurpose(home, "default", load_limits(home)), [fake.say("What for?")], ["/quit"])
    system = talk.client.calls[0]["messages"][0]["content"]
    assert prompts.STANDARDS in system and "never ask the user whether they want them" in system
    assert "do not ask the user\n  about files" in system
    # The same standards reach every role and every Lead in a run.
    lead, roles = load_lead(home, load_limits(home)), crew_roles(home, "default", load_limits(home))
    assert prompts.STANDARDS in prompts.role_system(roles["researcher"], [], [])
    assert prompts.STANDARDS in prompts.lead_system(lead, "", roles, load_limits(home))


def test_a_brief_opens_with_the_users_own_description(talk, home):
    talk(BriefPurpose(home, "default", load_limits(home)), [fake.say("Tell me about it.")], ["/quit"])
    messages = talk.client.calls[0]["messages"]
    assert "describe it in their own words" in messages[1]["content"]
    assert "Do not open with the checklist" in messages[0]["content"]
