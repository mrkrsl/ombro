"""Display titles for roles and crews, and the name and theme preferences. All data is invented."""

from __future__ import annotations

from conftest import write_role
from ombro import cli, fake, mdfile
from ombro.fake import ScriptedClient
from ombro.home import Home, crew_title, ensure_home, tidy_title
from ombro.interviews import RolePurpose, start
from ombro.roles import crew_roles, folder_name, load_lead
from ombro.settings import load_limits, load_preferences, set_preference


def test_titles_and_folder_names():
    assert tidy_title("market-researcher") == "Market Researcher"
    assert tidy_title("ops_team") == "Ops Team"
    assert folder_name("Marketing Assistant") == "marketing-assistant"
    assert folder_name("  R&D / Lab Helper ") == "r-d-lab-helper"


def test_a_fresh_home_has_titles(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    limits = load_limits(h)
    assert load_lead(h, limits).title == "Lead"
    assert load_lead(h, limits).soul.startswith("You are the Ombro Lead: the user's right hand.")
    assert crew_title(h, "default") == "Default Crew"
    assert crew_roles(h, "default", limits)["general-assistant"].title == "General Assistant"


def test_a_role_or_crew_without_a_title_shows_its_name_tidied(home):
    write_role(home, "fact-checker")
    assert crew_roles(home, "default", load_limits(home))["fact-checker"].title == "Fact Checker"
    (home.crews_dir / "ops").mkdir()
    mdfile.write(home.crews_dir / "ops" / "crew.md", {"name": "ops", "description": "Invented."})
    assert crew_title(home, "ops") == "Ops Crew"


def test_crew_add_takes_a_title_as_typed(home, capsys):
    assert cli.main(["crew", "add", "legal", "--title", "Legal Team"]) == 0
    assert cli.main(["crew", "add", "marketing-a"]) == 0
    assert crew_title(home, "legal") == "Legal Team"
    assert crew_title(home, "marketing-a") == "Marketing A Crew"
    assert "Legal Team" in capsys.readouterr().out


def test_the_role_interview_makes_the_folder_from_the_title(home):
    client = ScriptedClient([fake.call("propose", {
        "title": "Marketing Assistant", "kind": "specialist",
        "description": "Drafts invented marketing copy and returns it as a file.",
        "tools": ["workspace.read", "workspace.write", "workspace.list", "code.run"],
        "soul": "You draft clear, short marketing copy.",
    })])
    answers = iter(["a"])
    out = start(home, RolePurpose(home, "default", load_limits(home)), "default", load_limits(home),
                ask=lambda p: next(answers), say=lambda t: None, client_factory=lambda s: client)
    assert out.confirmed and out.proposal.data["name"] == "marketing-assistant"
    role = crew_roles(home, "default", load_limits(home))["marketing-assistant"]
    assert role.title == "Marketing Assistant" and "code.run" in role.tools


def test_name_and_theme_preferences(home):
    assert load_preferences(home).name == "" and load_preferences(home).theme == "auto"
    set_preference(home, "name", "  Ada   Invented ")
    set_preference(home, "theme", "dark")
    prefs = load_preferences(home)
    assert prefs.name == "Ada Invented" and prefs.theme == "dark"
    assert "default_crew" in mdfile.read(home.preferences_config)[0]  # the rest is kept
    set_preference(home, "theme", "purple")
    assert load_preferences(home).theme == "auto"


def test_a_sentence_names_a_crew_the_way_it_reads(home):
    """Testing log T22: a crew titled Ombro is "the Ombro crew", never "the Ombro"; a title that
    already says it is a group is left alone, never "the Marketing team crew"."""
    from ombro.home import crew_phrase, crew_phrases, ensure_crew, find_crew

    ensure_crew(home, "ombro", title="Ombro")
    ensure_crew(home, "marketing", title="Marketing team")
    ensure_crew(home, "legal", title="The Legal Department")
    assert crew_phrase(home, "ombro") == "the Ombro crew"
    assert crew_phrase(home, "marketing") == "the Marketing team"
    assert crew_phrase(home, "legal") == "the Legal Department"
    assert crew_phrase(home, "default") == "the Default Crew"
    assert crew_phrase(home, "ombro", start=True) == "The Ombro crew"
    assert crew_phrases(home, ["ombro", "marketing"]) == "the Ombro crew and the Marketing team"
    assert find_crew(home, "the Ombro crew") == "ombro" and find_crew(home, "the marketing team") == "marketing"


def test_the_leads_offer_names_the_crew_the_way_it_reads(home):
    from ombro import fake
    from ombro.home import ensure_crew
    from test_chat import ChatSession

    ensure_crew(home, "ombro", title="Ombro")
    ensure_crew(home, "marketing", title="Marketing team")
    s = ChatSession(home)
    s([fake.call("offer_brief", {"summary": "Define the launch plan", "crews": ["ombro"]}), fake.say("Fine."),
       fake.call("offer_brief", {"summary": "Draft the launch post", "crews": ["marketing"]}), fake.say("Fine.")],
      ["Define the launch plan", "n", "Draft the launch post", "n"])
    assert "I can brief it for the Ombro crew: Define the launch plan." in s.output
    assert "I can brief it for the Marketing team: Draft the launch post." in s.output
    assert "the Marketing team crew" not in s.output
