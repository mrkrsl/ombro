"""A job's folders stay where they were when it started. Invented files in temporary folders; no network.

A script may change what is inside workspace/, scratch/ and a writable shared
folder, never the folder itself. And whatever happens to a folder, the tools
never follow it somewhere else: each uses the path fixed at the start, and
refuses if that is no longer a real folder.
"""

from __future__ import annotations

import os

import pytest

from ombro import cli, coderun, folders, report, jobs
from ombro.native import native_registry
from ombro.settings import load_limits
from ombro.tools import LayoutChanged, SharedFolder, ToolContext, ToolInputError
from test_code import make_ctx, needs_sandbox, job_dir, run_script  # noqa: F401  (job_dir is a fixture)
from test_folders import user  # noqa: F401  (a fixture)

SECRET = "INVENTED_LAYOUT_KEY=not-a-real-secret-4471"


@pytest.fixture
def secret(home):
    home.env.write_text(SECRET + "\n")
    return SECRET


def swap_for_symlink(folder, target):
    """What a script could once do: move the folder aside and leave a symlink in its place."""
    folder.rename(folder.parent / f"{folder.name}-moved-aside")
    os.symlink(target, folder)


# ---------------------------------------------------------------------------
# The sandbox: a script cannot change the folders themselves


@needs_sandbox
def test_a_script_cannot_rename_remove_or_replace_its_own_folders(job_dir, tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "IMG_0001.jpg").write_text("invented photo")
    (job_dir / "workspace" / "a.txt").write_text("one")
    shared = {"photos": SharedFolder("photos", photos.resolve(), "write")}
    ctx, _ = make_ctx(job_dir, shared=shared)
    target = str(tmp_path)
    out = run_script(ctx, f"""
import os
def t(name, f):
    try:
        f(); print(name, "done", flush=True)
    except OSError as e:
        print(name, "refused", flush=True)
t("rename workspace", lambda: os.rename("workspace", "scratch/old"))
t("rename scratch", lambda: os.rename("scratch", "workspace/old"))
t("remove workspace", lambda: os.rmdir("workspace"))
t("chmod workspace", lambda: os.chmod("workspace", 0o777))
t("rename shared", lambda: os.rename({str(photos.resolve())!r}, "workspace/photos"))
t("symlink at shared", lambda: os.symlink({target!r}, {str(photos.resolve())!r}))
t("work inside", lambda: (os.rename("workspace/a.txt", "workspace/b.txt"), os.mkdir("workspace/sub"),
                          open("shared/photos/new.txt", "w").write("x")))
""")
    lines = dict(line.rsplit(" ", 1) for line in out["output"].strip().splitlines())
    assert lines.pop("work inside") == "done", out
    assert set(lines.values()) == {"refused"}, out
    for folder in (job_dir / "workspace", job_dir / "scratch", photos):
        assert folder.is_dir() and not folder.is_symlink()
    assert (job_dir / "workspace" / "b.txt").read_text() == "one"


# ---------------------------------------------------------------------------
# The harness: a replaced folder is never followed


def test_workspace_tools_refuse_a_workspace_that_has_become_a_symlink(home, secret):
    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    swap_for_symlink(job_dir / "workspace", home.root)
    reg = native_registry(home)
    ctx = ToolContext(job_dir, "researcher")
    calls = [
        ("workspace.read", {"path": "workspace/secrets/keys.env"}),
        ("workspace.write", {"path": "workspace/folders.md", "content": "invented"}),
        ("workspace.list", {"path": "workspace"}),
        ("workspace.place", {"from": "workspace/limits.md", "to": "~/Documents/x.md", "reason": "invented"}),
    ]
    for name, args in calls:
        with pytest.raises(LayoutChanged):
            reg.get(name).handler(args, ctx)
    with pytest.raises(ToolInputError):  # an absolute path is not shared either
        reg.get("workspace.read").handler({"path": str(home.env)}, ctx)
    assert not home.folders.exists()
    # scratch/ is untouched, so it still works.
    assert reg.get("workspace.write").handler({"path": "scratch/n.md", "content": "x"}, ctx)["path"] == "scratch/n.md"


def test_scratch_that_has_become_a_symlink_is_refused_too(home, secret):
    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    swap_for_symlink(job_dir / "scratch", job_dir)
    reg = native_registry(home)
    with pytest.raises(LayoutChanged):
        reg.get("workspace.write").handler({"path": "scratch/journal.jsonl", "content": "{}"},
                                           ToolContext(job_dir, "researcher"))


@needs_sandbox
def test_code_run_refuses_to_start_when_a_folder_has_become_a_symlink(job_dir, home, secret):
    swap_for_symlink(job_dir / "workspace", home.root)
    ctx, events = make_ctx(job_dir)
    with pytest.raises(LayoutChanged):
        run_script(ctx, f"print(open({str(home.env)!r}).read())")
    assert events == [] and not (job_dir / "code" / "001.py").exists()


def test_a_shared_folder_that_has_become_a_symlink_is_refused(home, user, tmp_path):
    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    trip = user / "Pictures" / "Trip"
    shared = {"photos": SharedFolder("photos", trip, "write")}
    swap_for_symlink(trip, user / "Documents")
    reg = native_registry(home)
    for name, args in [("workspace.list", {"path": "shared/photos"}),
                       ("workspace.write", {"path": "shared/photos/x.md", "content": "x"})]:
        with pytest.raises(LayoutChanged):
            reg.get(name).handler(args, ToolContext(job_dir, "researcher", shared=shared))
    assert folders.manifest(trip) == {}  # never walked through


def test_a_grant_whose_path_now_leads_elsewhere_is_left_out(home, user):
    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    swap_for_symlink(user / "Pictures" / "Trip", user / "Documents")
    got, left = folders.for_job(home, {"folders": ["photos"]})
    assert got == []
    assert "through a symlink" in left[0] and "ombro folder add" in left[0]


def test_the_report_lists_nothing_through_a_replaced_workspace(home, secret):
    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    swap_for_symlink(job_dir / "workspace", home.root)
    facts = report.gather_facts(home, job_dir, "completed")
    assert facts["other_files"] == []
    assert not any("keys.env" in str(d) for d in facts["deliverables"])


def test_ombro_open_refuses_a_workspace_that_is_not_a_real_folder(home, monkeypatch, capsys):
    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    jobs.update_job(job_dir, status="completed")
    swap_for_symlink(job_dir / "workspace", home.root)
    opened = []
    monkeypatch.setattr(cli, "_launch", lambda folder: opened.append(folder) or True)
    assert cli.main(["open", job_dir.name]) != 0
    assert opened == []
    assert "not a real folder" in capsys.readouterr().err
