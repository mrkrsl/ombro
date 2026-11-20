"""Files over the API ("Save to this Mac"): what can be read, listed and written back, and where
the app is told to keep its copies."""

from __future__ import annotations

import os
import urllib.parse
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from ombro import folders
from ombro.home import Home, ensure_home
from ombro.serve.app import build_app
from ombro.serve.live import Question

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def setup(tmp_path):
    home = Home(tmp_path / "home")
    ensure_home(home)
    job = home.jobs_dir / "rivers-1"
    (job / "workspace" / "maps").mkdir(parents=True)
    (job / "report.md").write_text("# Rivers\n")
    (job / "workspace" / "note.md").write_text("an invented note")
    (job / "workspace" / "maps" / "café.txt").write_text("a map")
    (job / "workspace" / ".hidden").write_text("x")
    shared = tmp_path / "shared-work"
    shared.mkdir()
    (shared / "plan.md").write_text("shared plan")
    # Written as `ombro folder add` would; its checks (inside your home folder) are tested elsewhere.
    folders.save(home, {"work": folders.Grant("work", str(shared), "read")}, [])
    outside = tmp_path / "private.txt"
    outside.write_text("not for jobs")
    app = build_app(TOKEN, home=home)
    return SimpleNamespace(
        home=home,
        job=job,
        shared=shared,
        outside=outside,
        c=TestClient(app, base_url="http://127.0.0.1"),
        serve=app.state.serve,
    )


def get(setup, path: str, endpoint: str = "/files"):
    return setup.c.get(f"{endpoint}?path={urllib.parse.quote(str(path))}", headers=AUTH)


def test_a_job_file_comes_with_where_to_keep_it(setup):
    r = get(setup, setup.job / "workspace" / "note.md")
    assert r.status_code == 200 and r.content == b"an invented note"
    assert urllib.parse.unquote(r.headers["x-ombro-save-as"]) == "rivers-1/workspace/note.md"
    # Named as prompts name it, too.
    assert get(setup, "jobs/rivers-1/report.md").content == b"# Rivers\n"


def test_shared_folders_can_be_read_and_kept_under_shared(setup):
    r = get(setup, setup.shared / "plan.md")
    assert r.status_code == 200
    assert urllib.parse.unquote(r.headers["x-ombro-save-as"]) == "shared/work/plan.md"


def test_anything_else_is_refused(setup):
    assert get(setup, setup.outside).status_code == 403
    assert get(setup, setup.home.root / "secrets" / "keys.env").status_code == 403
    assert get(setup, setup.job / ".." / ".." / ".." / "private.txt").status_code == 403
    link = setup.job / "workspace" / "escape.txt"
    os.symlink(setup.outside, link)
    assert get(setup, link).status_code == 403  # resolved first, so it points outside
    assert get(setup, setup.job / "workspace" / "gone.md").status_code == 404
    assert setup.c.get("/files", headers=AUTH).status_code == 400
    assert setup.c.get(f"/files?path={setup.job}/report.md").status_code == 403  # needs the token


def test_a_folder_is_listed_with_each_file_and_its_place(setup):
    os.symlink(setup.outside, setup.job / "workspace" / "escape.txt")
    r = get(setup, setup.job / "workspace", "/files/list")
    assert r.status_code == 200
    body = r.json()
    assert body["save_as"] == "rivers-1/workspace"
    assert [f["save_as"] for f in body["files"]] == ["rivers-1/workspace/note.md", "rivers-1/workspace/maps/café.txt"]
    assert get(setup, setup.outside.parent, "/files/list").status_code == 403


def test_only_a_draft_out_for_editing_can_be_written_back(setup):
    draft = setup.home.root / "crews" / "default" / "roles" / "writer" / "soul.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("before")

    def put(p, body):
        return setup.c.put(f"/files?path={urllib.parse.quote(str(p))}", content=body, headers=AUTH)

    assert put(draft, b"after").status_code == 403
    assert get(setup, draft).status_code == 403
    q = Question("q1", "edit_files", {"paths": [str(draft)]})
    setup.serve.sessions.by_id["s1"] = SimpleNamespace(question=q, closed=False)
    r = get(setup, draft)
    assert r.status_code == 200
    assert urllib.parse.unquote(r.headers["x-ombro-save-as"]) == "Drafts/writer-soul.md"
    assert put(draft, b"after").status_code == 200 and draft.read_text() == "after"
    assert put(setup.job / "report.md", b"x").status_code == 403  # readable, but not out for editing
    setup.serve.sessions.by_id["s1"].closed = True
    assert put(draft, b"again").status_code == 403
