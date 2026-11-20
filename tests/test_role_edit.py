"""A crew member's profile changed in one go: rename (the folder too, and
saved briefs that name them), specialist or helper, model and soul; over `ombro serve` and the
command line. All data is invented."""

from __future__ import annotations

import pytest

from conftest import write_role
from ombro import cli, mdfile
from ombro.home import ensure_crew
from ombro.role_edit import EditError, edit_role
from ombro.roles import load_role
from ombro.settings import load_limits
from test_serve_sessions import loopback, serve  # noqa: F401 - fixtures
from test_settings_pages import _running_job


def _role(home, crew, name):
    return load_role(home.roles_dir(crew) / name, load_limits(home))


def test_a_rename_moves_the_folder_and_follows_into_saved_briefs(home):
    ensure_crew(home, "legal", title="Legal Crew")
    write_role(home, "counsel", crew="legal", title="Legal Researcher", skills=["fact-check"])
    mdfile.write(home.specs_dir / "invented-terms.md", {"crews": ["legal"], "roles": ["counsel"]}, "# Terms\n")
    mdfile.write(
        home.specs_dir / "invented-both.md",
        {"crews": ["legal", "default"], "roles": ["legal/counsel", "default/researcher"]},
        "# Both\n",
    )
    mdfile.write(home.specs_dir / "invented-other.md", {"crews": ["default"], "roles": ["researcher"]}, "# Other\n")
    done = edit_role(home, "legal", "counsel", title="  Case   Reader ")
    assert (done.name, done.renamed_from, sorted(done.specs)) == (
        "case-reader",
        "counsel",
        ["invented-both", "invented-terms"],
    )
    assert not (home.roles_dir("legal") / "counsel").exists()
    r = _role(home, "legal", "case-reader")
    assert (r.title, r.skills) == ("Case Reader", ["fact-check"])
    assert mdfile.read(home.specs_dir / "invented-terms.md")[0]["roles"] == ["case-reader"]
    assert mdfile.read(home.specs_dir / "invented-both.md")[0]["roles"] == ["legal/case-reader", "default/researcher"]
    assert mdfile.read(home.specs_dir / "invented-other.md")[0]["roles"] == ["researcher"]


def test_a_new_title_with_the_same_folder_name_only_retitles(home):
    done = edit_role(home, "default", "researcher", title="RESEARCHER")
    assert done.renamed_from is None and _role(home, "default", "researcher").title == "RESEARCHER"


def test_a_rename_refuses_a_name_the_crew_has(home):
    write_role(home, "writer", title="Copy Editor")
    with pytest.raises(EditError, match="already has a member called Writer|already has a member called Copy Editor"):
        edit_role(home, "default", "researcher", title="Writer")  # another member's folder name
    with pytest.raises(EditError, match="already has a member called Copy Editor"):
        edit_role(home, "default", "researcher", title="copy editor")  # another member's title
    with pytest.raises(EditError, match="letters or digits"):
        edit_role(home, "default", "researcher", title="  !! ")
    assert (home.roles_dir("default") / "researcher").is_dir() and _role(
        home, "default", "writer"
    ).title == "Copy Editor"
    ensure_crew(home, "legal")
    write_role(home, "writer", crew="legal")  # another crew's member of the same name is no clash
    write_role(home, "counsel", crew="legal")
    assert edit_role(home, "default", "researcher", title="Counsel").name == "counsel"


def test_a_rename_waits_for_the_crews_running_job(home):
    jid = _running_job(home, "default")
    with pytest.raises(EditError, match="still running.*rename them"):
        edit_role(home, "default", "researcher", title="Analyst")
    assert edit_role(home, "default", "researcher", kind="helper").name == "researcher"  # no rename: fine
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "finished", "crews": ["default"]})
    assert edit_role(home, "default", "researcher", title="Analyst").name == "analyst"


def test_kind_model_and_soul_change_together_and_bad_values_change_nothing(home):
    before = (home.roles_dir("default") / "researcher" / "config.md").read_text()
    for bad in (
        {"kind": "lead"},
        {"model": "invented-missing"},
        {"soul": "  "},
        {"kind": "helper", "model": "invented-missing"},
    ):
        with pytest.raises(EditError):
            edit_role(home, "default", "researcher", **bad)
    assert (home.roles_dir("default") / "researcher" / "config.md").read_text() == before
    edit_role(home, "default", "researcher", kind="helper", model="default", soul="You check invented things.")
    r = _role(home, "default", "researcher")
    assert (r.kind, r.model, r.soul) == ("helper", "default", "You check invented things.")
    with pytest.raises(EditError, match="The Lead keeps"):
        edit_role(home, "", "lead", title="Boss")
    with pytest.raises(LookupError):
        edit_role(home, "default", "nobody", kind="helper")


def test_the_member_page_saves_over_http(serve, home):
    write_role(home, "writer", title="Copy Editor")
    mdfile.write(home.specs_dir / "invented-terms.md", {"crews": ["default"], "roles": ["researcher"]}, "# Terms\n")
    after = serve.http.put("/roles/default/researcher", json={"title": "Analyst", "kind": "helper", "model": "default"})
    assert after.status_code == 200, after.text
    body = after.json()
    assert (body["name"], body["title"], body["kind"], body["model"]) == ("analyst", "Analyst", "helper", "default")
    assert mdfile.read(home.specs_dir / "invented-terms.md")[0]["roles"] == ["analyst"]
    assert [r["name"] for r in serve.get("/crews")["crews"][0]["roles"]] == ["analyst", "writer"]
    clash = serve.http.put("/roles/default/analyst", json={"title": "Copy Editor"})
    assert clash.status_code == 400 and "already has a member called Copy Editor" in clash.text
    assert serve.http.put("/roles/default/analyst", json={}).status_code == 400
    assert serve.http.put("/roles/default/analyst", json={"soul": "You analyse."}).json()["soul"] == "You analyse."
    assert serve.http.put("/roles/default/nobody", json={"kind": "helper"}).status_code == 404


def test_rename_and_kind_on_the_command_line(home, capsys):
    mdfile.write(home.specs_dir / "invented-terms.md", {"crews": ["default"], "roles": ["researcher"]}, "# Terms\n")
    assert cli.main(["role", "rename", "researcher", "Market Analyst"]) == 0
    assert (
        "Renamed default/researcher to default/market-analyst. Saved briefs changed to the new name: invented-terms."
        in capsys.readouterr().out
    )
    assert cli.main(["role", "kind", "market-analyst", "helper"]) == 0
    assert "default/market-analyst is now a helper." in capsys.readouterr().out
    assert _role(home, "default", "market-analyst").kind == "helper"
    write_role(home, "writer")
    assert cli.main(["role", "rename", "writer", "Market Analyst"]) != 0
    assert "already has a member called Market Analyst" in capsys.readouterr().err
    assert cli.main(["role", "kind", "writer", "boss"]) != 0
