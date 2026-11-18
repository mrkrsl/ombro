"""Shared folders, grants and placements. Invented files in temporary folders; no network.

HOME is pointed at a temporary folder, so "inside your home folder" means
inside it, and nothing outside the test's own folders is ever touched.
"""

from __future__ import annotations

import argparse
import platform

import pytest

from conftest import LEAD_REPORT, write_role
from ombro import fake, folders, mdfile, outbox, jobs
from ombro.folders_cmd import cmd_folders, offer_start_folder
from ombro.native import native_registry
from ombro.settings import SetupError, load_limits
from ombro.tools import NotGranted, SharedFolder, ToolContext, ToolInputError
from test_guardrails import assert_reported
from test_interviews import SPEC, Session


@pytest.fixture
def user(tmp_path, monkeypatch):
    """The user's home folder, with an invented documents folder and photos."""
    me = tmp_path / "user"
    (me / "Documents" / "Brand").mkdir(parents=True)
    (me / "Documents" / "Brand" / "guide.md").write_text("Invented brand guide: teal, never orange.\n")
    (me / "Pictures" / "Trip").mkdir(parents=True)
    for i in range(3):
        (me / "Pictures" / "Trip" / f"IMG_{i:04d}.jpg").write_text(f"invented photo {i}")
    (me / "Library" / "Mail").mkdir(parents=True)
    (me / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(me))
    return me.resolve()



def test_changes_someone_else_makes_while_a_job_runs_are_not_the_job_s(job, home, user):
    from ombro.settings import load_tool_settings
    from ombro.tools import Tool

    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    outside = user / "Pictures" / "Trip" / "plan.md"

    def elsewhere(args, ctx):  # the user saves a file in the folder while the job runs
        outside.write_text("Mark's own plan")
        return {"ok": True}

    reg = native_registry(home, load_tool_settings(home))
    reg.register(Tool(name="test.wait", description="Wait a moment.", handler=elsewhere))
    write_role(home, "researcher", max_steps=10, tools=["workspace.write", "test.wait"])
    job([
        fake.brief("researcher", "Note the colour."),
        fake.call("test__wait", {}),
        fake.call("workspace__write", {"path": "shared/photos/colour.md", "content": "teal\n"}),
        fake.result("Noted the colour."),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ], spec_meta={"folders": ["photos"]}, registry=reg)
    ev = next(e for e in job.events if e["action"] == "folder_changes")
    assert ev["created"] == ["colour.md"] and ev["outside"]["created"] == ["plan.md"]
    assert "Changed while the job ran, but not by the job (by you or another program): plan.md." in job.report


# ---------------------------------------------------------------------------
# What can be granted


def test_only_ordinary_folders_can_be_shared(home, user, tmp_path):
    assert folders.check_place(user / "Documents" / "Brand", home) == user / "Documents" / "Brand"
    assert folders.check_place("~/Pictures/Trip", home) == user / "Pictures" / "Trip"
    refused = {
        "/": "root folder",
        str(user): "whole home folder",
        str(user / ".ssh"): "hidden",
        str(user / "Library" / "Mail"): "Library",
        str(home.root): "Ombro's own home",
        str(home.root / "crews"): "Ombro's own home",
        str(tmp_path): "contains Ombro's own home",
        "/usr/lib": "not in your home folder",
        "Documents": "not a full path",
        str(user / "Nowhere"): "does not exist",
    }
    for path, why in refused.items():
        with pytest.raises(folders.FolderError, match=why):
            folders.check_place(path, home)


def test_a_symlink_into_a_hidden_folder_is_refused(home, user):
    (user / "keys-link").symlink_to(user / ".ssh")
    with pytest.raises(folders.FolderError, match="hidden"):
        folders.check_place(user / "keys-link", home)


def test_mounted_drives_can_be_shared(home, user, tmp_path, monkeypatch):
    volumes = tmp_path / "Volumes"
    (volumes / "Studio" / "Brand").mkdir(parents=True)
    monkeypatch.setattr(folders, "mount_roots", lambda: [volumes])
    assert folders.check_place(volumes / "Studio" / "Brand", home) == (volumes / "Studio" / "Brand").resolve()
    with pytest.raises(folders.FolderError):
        folders.check_place(volumes, home)


# ---------------------------------------------------------------------------
# folders.md and the command line


def folders_args(action=None, target=None, **kw):
    return argparse.Namespace(action=action, target=target, name=kw.get("name"), access=kw.get("access"),
                              yes=kw.get("yes", True))


def test_grants_are_added_listed_and_removed(home, user, capsys):
    assert cmd_folders(folders_args("add", str(user / "Documents" / "Brand"))) == 0
    assert cmd_folders(folders_args("add", "~/Pictures/Trip", name="photos", access="write")) == 0
    grants, declined = folders.load(home)
    assert {n: (g.path, g.access) for n, g in grants.items()} == {
        "brand": ("~/Documents/Brand", "read"), "photos": ("~/Pictures/Trip", "write")}
    cmd_folders(folders_args())
    out = capsys.readouterr().out
    assert "brand" in out and "photos" in out and "backup:" in out
    assert cmd_folders(folders_args("add", str(user / "Pictures" / "Trip"), name="trip")) == 0
    assert "already shared as 'photos' (write)" in capsys.readouterr().out
    assert cmd_folders(folders_args("remove", "brand")) == 0
    assert set(folders.load(home)[0]) == {"photos"}
    assert "Folders on this machine" in home.folders.read_text()


def test_a_bad_folders_file_is_reported(home):
    mdfile.write(home.folders, {"folders": {"Bad Name": {"path": "~/x"}}})
    with pytest.raises(SetupError, match="lower-case"):
        folders.load(home)
    mdfile.write(home.folders, {"folders": {"x": {"path": "~/x", "access": "everything"}}})
    with pytest.raises(SetupError, match="read or write"):
        folders.load(home)


def test_the_start_folder_is_offered_once_and_a_no_is_remembered(home, user):
    said: list[str] = []
    here = user / "Documents" / "Brand"
    offer_start_folder(home, here, ask=lambda p: "n", say=said.append)
    assert "Not shared" in said[-1]
    assert folders.load(home)[1] == ["~/Documents/Brand"]
    asked = []
    offer_start_folder(home, here, ask=lambda p: asked.append(p) or "r", say=said.append)
    assert asked == []  # declined folders are not asked about again
    offer_start_folder(home, user / "Pictures" / "Trip", ask=lambda p: "w", say=said.append)
    assert folders.load(home)[0]["trip"].access == "write"
    offer_start_folder(home, user, ask=lambda p: asked.append(p) or "r", say=said.append)
    assert asked == []  # the home folder itself is never offered


# ---------------------------------------------------------------------------
# A job's folders


def test_a_spec_may_lower_write_to_read_and_names_what_is_not_shared(home, user):
    folders.add(home, user / "Documents" / "Brand", "brand", "read")
    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    got, left = folders.for_job(home, {"folders": {"brand": "write", "photos": "read", "secret": None}})
    assert [(f["name"], f["access"]) for f in got] == [("brand", "read"), ("photos", "read")]
    assert "not shared" in left[0] and "secret" in left[0]
    got, _ = folders.for_job(home, {"folders": ["photos"]})
    by_name = {f["name"]: f for f in got}
    assert by_name["photos"]["access"] == "write" and by_name["photos"]["path"] == str(user / "Pictures" / "Trip")
    assert by_name["brand"]["access"] == "read"  # named or not, every shared folder comes with the job


def test_every_job_gets_every_shared_folder_however_it_starts(home, user, monkeypatch):
    """17 Nov: a live job from a chat said "no folders are shared with this job" because its spec
    named none. A job gets all the shared folders, with their own access, whatever its spec says."""
    from ombro import livejob

    folders.add(home, user / "Documents" / "Brand", "brand", "write")
    folders.add(home, user / "Pictures" / "Trip", "photos", "read")
    want = [("brand", "write", str(user / "Documents" / "Brand")), ("photos", "read", str(user / "Pictures" / "Trip"))]
    assert folders.for_job(home, {}) == folders.for_job(home, None) == folders.for_chat(home)
    assert sorted((f["name"], f["access"], f["path"]) for f in folders.for_job(home, {})[0]) == want

    started = []
    monkeypatch.setattr(jobs, "start_detached", lambda h, d: started.append(d))
    live = livejob.start(home, ["default"], "Round two", "Revise shared/brand/copy.md.", chat="c1")
    plain = jobs.create_job(home, ["default"], {}, "# A follow-up", load_limits(home), source="test")
    for job_dir in (live, plain):
        meta, _ = jobs.read_job(job_dir)
        assert sorted((f["name"], f["access"], f["path"]) for f in meta["folders"]) == want
        assert "folders_left_out" not in meta
    assert started == [live]


def test_with_no_folders_shared_a_job_gets_none_and_no_warning(home, user):
    assert folders.for_job(home, {}) == ([], [])


def ctx_for(job_dir, user, access="read"):
    shared = {"brand": SharedFolder("brand", user / "Documents" / "Brand", "read"),
              "photos": SharedFolder("photos", user / "Pictures" / "Trip", access)}
    return ToolContext(job_dir, "researcher", shared=shared)


def test_workspace_tools_reach_shared_folders_as_granted(home, user):
    job_dir = jobs.create_job(home, ["default"], {}, "# x", load_limits(home), source="test")
    reg = native_registry(home)
    ctx = ctx_for(job_dir, user, access="write")
    read = reg.get("workspace.read").handler
    write = reg.get("workspace.write").handler
    listing = reg.get("workspace.list").handler
    assert "teal" in read({"path": "shared/brand/guide.md"}, ctx)["content"]
    assert "teal" in read({"path": str(user / "Documents" / "Brand" / "guide.md")}, ctx)["content"]
    assert write({"path": "shared/photos/notes.md", "content": "invented"}, ctx)["path"] == "shared/photos/notes.md"
    with pytest.raises(ToolInputError, match="read-only"):
        write({"path": "shared/brand/new.md", "content": "x"}, ctx)
    with pytest.raises(NotGranted):
        read({"path": "shared/other/x.md"}, ctx)
    with pytest.raises(NotGranted):
        read({"path": str(user / "Library" / "Mail")}, ctx)
    with pytest.raises(ToolInputError, match=r"\.\."):
        read({"path": "shared/brand/../../x"}, ctx)
    (user / "Documents" / "Brand" / "out").symlink_to(user / ".ssh")
    with pytest.raises(ToolInputError, match="symlink"):
        listing({"path": "shared/brand/out"}, ctx)
    names = listing({"path": "shared"}, ctx)["entries"]
    assert {e["path"] for e in names} == {"shared/brand", "shared/photos"}
    assert "shared/brand (read)" in listing({}, ctx)["shared"]


def test_a_job_backs_up_and_lists_changes_in_a_writable_folder(job, home, user):
    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    folders.add(home, user / "Documents" / "Brand", "brand", "read")
    write_role(home, "researcher", max_steps=10, tools=["workspace.read", "workspace.write", "workspace.list"])
    status = job([
        fake.brief("researcher", "Note the brand colour in the photos folder."),
        fake.call("workspace__read", {"path": "shared/brand/guide.md"}),
        fake.call("workspace__write", {"path": "shared/photos/colour.md", "content": "teal\n"}),
        fake.call("workspace__write", {"path": "shared/photos/IMG_0000.jpg", "content": "changed"}),
        fake.call("workspace__read", {"path": str(user / "Library" / "Mail" / "x")}),
        fake.result("Noted the colour.", did_not_do=["Could not read the mail folder."]),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ], spec_meta={"folders": ["photos", "brand", "missing"]})
    assert status == "completed"
    assert_reported(job, "completed")
    meta = job.meta
    by_name = {f["name"]: f for f in meta["folders"]}
    assert by_name["brand"]["access"] == "read" and "backup" not in by_name["brand"]
    backup = by_name["photos"].get("backup")
    if platform.system() == "Darwin":
        # The clone holds the folder as it was before the run.
        assert backup and (job.job_dir / "backup" / "photos" / "IMG_0000.jpg").read_text() == "invented photo 0"
        assert not (job.job_dir / "backup" / "photos" / "colour.md").exists()
    else:
        assert backup or "no backup" in by_name["photos"]["backup_note"]
    report = job.report
    assert "## Shared folders" in report
    assert "**shared/photos** (read and write)" in report and "**shared/brand** (read only)" in report
    # Replacing IMG_0000.jpg kept its old version in Archive/, with a note of why.
    assert "Created (3):" in report and "- colour.md" in report and "- Archive/IMG_0000.jpg" in report
    assert (user / "Pictures" / "Trip" / "Archive" / "IMG_0000.jpg").read_text() == "invented photo 0"
    assert "shared/photos/IMG_0000.jpg -> shared/photos/Archive/IMG_0000.jpg" in report
    assert "Changed (1):" in report and "- IMG_0000.jpg" in report
    assert "missing" in report and "not shared" in report
    assert str(user / "Library" / "Mail" / "x") in report.split("Refused because")[1]
    system = job.client.calls[1]["messages"][0]["content"]
    assert "shared/photos (read and write; on disk " in system and "shared/brand (read only; on disk " in system
    lead = job.client.calls[0]["messages"][0]["content"]
    assert "Folders the user shared with this job" in lead
    assert "shared/photos (read and write; on disk ~/Pictures/Trip)" in lead


# ---------------------------------------------------------------------------
# Placements


def place_job(job, home, user, to, **kw):
    write_role(home, "researcher", max_steps=10, tools=["workspace.write", "workspace.place"])
    return job([
        fake.brief("researcher", "Write the summary and save it in the reports folder."),
        fake.call("workspace__write", {"path": "workspace/summary.md", "content": "Invented summary.\n"}),
        fake.call("workspace__place", {"from": "workspace/summary.md", "to": to,
                                       "reason": "The user keeps reports there.", **kw}),
        fake.call("workspace__place", {"from": "workspace/summary.md", "to": to,
                                       "reason": "The user keeps reports there.", **kw}),
        fake.result("Wrote the summary."),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ], spec_meta={"folders": ["photos"]} if folders.load(home)[0] else None)


def test_a_placement_outside_the_shared_folders_waits_for_approval(job, home, user):
    (user / "Documents" / "Reports").mkdir()
    status = place_job(job, home, user, "~/Documents/Reports/")
    assert status == "completed"
    dest = user / "Documents" / "Reports" / "summary.md"
    assert not dest.exists()
    (a,) = outbox.actions(job.job_dir)
    assert a.tool == "workspace.place" and a.status == "pending"
    assert a.arguments == {"to": "~/Documents/Reports/summary.md", "overwrite": False}
    assert "Waiting for your approval (1)" in job.report
    assert any(e["action"] == "staged_duplicate" for e in job.events)
    # Changing the workspace after staging does not change what is approved.
    (job.job_dir / "workspace" / "summary.md").write_text("changed later")
    outbox.approve(home, a)
    assert dest.read_text() == "Invented summary.\n"
    assert outbox.load(a.path).status == "executed"
    assert not folders.find(folders.load(home)[0], dest.parent)  # approving does not share the folder


def test_a_placement_never_replaces_a_file_unless_asked(job, home, user):
    (user / "Documents" / "Reports").mkdir()
    (user / "Documents" / "Reports" / "summary.md").write_text("the user's own file")
    place_job(job, home, user, "~/Documents/Reports/summary.md")
    (a,) = outbox.actions(job.job_dir)
    with pytest.raises(outbox.OutboxError, match="would replace"):
        outbox.approve(home, a)
    assert (user / "Documents" / "Reports" / "summary.md").read_text() == "the user's own file"
    outbox.edit(home, a, {"to": "~/Documents/Reports/summary.md", "overwrite": True})
    outbox.approve(home, outbox.load(a.path))
    assert (user / "Documents" / "Reports" / "summary.md").read_text() == "Invented summary.\n"


def test_a_placement_into_a_writable_shared_folder_happens_at_once(job, home, user):
    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    place_job(job, home, user, "shared/photos/summary.md")
    assert (user / "Pictures" / "Trip" / "summary.md").read_text() == "Invented summary.\n"
    assert outbox.actions(job.job_dir) == []
    assert "Copied into shared folders with workspace.place (1)" in job.report
    # The second, identical placement was refused rather than replacing the file.
    assert any("already exists" in e.get("note", "") for e in job.events if e["action"] == "tool_error")


def test_a_placement_to_a_place_that_can_never_be_shared_is_refused(job, home, user):
    place_job(job, home, user, str(user / ".ssh" / "summary.md"))
    assert outbox.actions(job.job_dir) == []
    assert not (user / ".ssh" / "summary.md").exists()


# ---------------------------------------------------------------------------
# The brief interview


def test_the_brief_asks_before_sharing_a_folder_the_spec_needs(home, user):
    from ombro.interviews import BriefPurpose

    talk = Session(home)
    out = talk(
        BriefPurpose(home, "default", load_limits(home), "Use the brand guide.", start_dir=user),
        [fake.call("propose", {**SPEC, "folders": [
            {"folder": str(user / "Documents" / "Brand"), "access": "read", "why": "the brand guide is there"},
            {"folder": "~/Pictures/Trip", "access": "write", "why": "photos to rename"}]})],
        ["a", "r", "n"],
    )
    assert out.status == "confirmed"
    meta, _ = mdfile.read(home.specs_dir / "teapot-survey.md")
    assert meta["folders"] == {"brand": "read"}
    grants = folders.load(home)[0]
    assert set(grants) == {"brand"} and grants["brand"].access == "read"
    assert "Not shared. The spec leaves ~/Pictures/Trip out." in talk.output
    assert "you will be asked whether to share it" in talk.output
    context = talk.client.calls[0]["messages"][0]["content"]
    assert "Shared folders" in context and "you never share one" in context


def test_the_brief_refuses_a_folder_that_can_never_be_shared(home, user):
    from ombro.interviews import BriefPurpose

    talk = Session(home)
    talk(
        BriefPurpose(home, "default", load_limits(home), "Read my keys."),
        [fake.call("propose", {**SPEC, "folders": [{"folder": "~/.ssh", "access": "read"}]}),
         fake.call("propose", SPEC)],
        ["a"],
    )
    assert "hidden" in talk.tool_replies()[0]
    assert folders.load(home)[0] == {}


# ---------------------------------------------------------------------------
# Copying into place never writes through a symlink


def test_a_placement_never_writes_through_a_planted_symlink(home, user, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("the user's own file\n")
    job_dir = jobs.create_job(home, ["default"], {}, "# x", load_limits(home), source="test")
    trip = user / "Pictures" / "Trip"
    # What a script in an earlier job, or this one, could have left in the shared folder.
    for name in (".summary.md.ombro-tmp", ".summary.md.tmp", "summary.md.ombro-tmp"):
        (trip / name).symlink_to(victim)
    (job_dir / "workspace" / "summary.md").write_text("Invented summary.\n")
    ctx = ToolContext(job_dir, "researcher", shared={"photos": SharedFolder("photos", trip, "write")})
    out = native_registry(home).get("workspace.place").handler(
        {"from": "workspace/summary.md", "to": "shared/photos/summary.md", "reason": "invented"}, ctx)
    assert out["status"] == "done"
    assert victim.read_text() == "the user's own file\n"
    assert (trip / "summary.md").read_text() == "Invented summary.\n" and not (trip / "summary.md").is_symlink()
    assert not [p for p in trip.iterdir() if p.name.endswith(".ombro-tmp") and not p.is_symlink()]


def test_approving_a_placement_never_writes_through_a_planted_symlink(job, home, user, tmp_path):
    reports = user / "Documents" / "Reports"
    reports.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("the user's own file\n")
    place_job(job, home, user, "~/Documents/Reports/")
    (reports / ".summary.md.ombro-tmp").symlink_to(victim)
    (a,) = outbox.actions(job.job_dir)
    outbox.approve(home, a)
    assert victim.read_text() == "the user's own file\n"
    assert (reports / "summary.md").read_text() == "Invented summary.\n"


def test_safe_copy_refuses_a_folder_reached_through_a_symlink(user, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    (user / "Pictures" / "via").symlink_to(user / "Documents")
    with pytest.raises(OSError, match="symlink"):
        folders.safe_copy(src, user / "Pictures" / "via" / "x.txt")
    assert not (user / "Documents" / "x.txt").exists()
    os_link = user / "Pictures" / "Trip" / "link.txt"
    os_link.symlink_to(tmp_path / "elsewhere.txt")
    with pytest.raises(OSError, match="symlink"):
        folders.safe_copy(src, os_link)


# ---------------------------------------------------------------------------
# The fixed rules hold whatever the spelling, on a disk that ignores case


def _ignores_case(path) -> bool:
    import os

    return os.path.exists(str(path).swapcase()) if str(path).swapcase() != str(path) else False


def test_the_rules_see_through_a_different_spelling(home, user):
    if not _ignores_case(user / "Library"):
        pytest.skip("this disk tells upper and lower case apart, so the spellings are different folders")
    for spelling in ("~/library/Mail", "~/LIBRARY", "~/Library/../library/Mail"):
        with pytest.raises(folders.FolderError):
            folders.check_place(spelling, home, must_exist=False)
    # A placement's folder that does not exist yet, under a differently spelt ~/Library.
    with pytest.raises(folders.FolderError, match="Library"):
        folders.check_place("~/library/LaunchAgents", home, must_exist=False)
    # An ordinary folder comes back spelt as it is on disk.
    assert folders.check_place("~/documents/brand", home) == user / "Documents" / "Brand"


def test_the_ombro_home_is_refused_whatever_the_spelling(home, user, monkeypatch):
    if not _ignores_case(user / "Library"):
        pytest.skip("this disk tells upper and lower case apart")
    from ombro.home import Home

    visible = Home(user / "OmbroHome")  # a home that is not hidden, so only the home rule stops it
    visible.root.mkdir()
    for spelling in ("~/ombrohome", "~/OMBROHOME/crews"):
        with pytest.raises(folders.FolderError, match="Ombro's own home"):
            folders.check_place(spelling, visible, must_exist=False)


def test_approving_a_placement_rechecks_the_spelling(job, home, user, capsys):
    if not _ignores_case(user / "Library"):
        pytest.skip("this disk tells upper and lower case apart")
    from ombro import approvals_cmd

    (user / "Library" / "LaunchAgents").mkdir()
    place_job(job, home, user, "~/Documents/Reports/")  # staged somewhere ordinary...
    (a,) = outbox.actions(job.job_dir)
    outbox.edit(home, a, {"to": "~/library/LaunchAgents/x.plist", "overwrite": False})  # ...then sent elsewhere
    approvals_cmd._show(outbox.load(a.path), home)
    assert "Refused" in capsys.readouterr().out
    with pytest.raises(outbox.OutboxError, match="Library"):
        outbox.approve(home, outbox.load(a.path))
    assert not (user / "Library" / "LaunchAgents" / "x.plist").exists()


def test_approving_a_placement_copies_only_the_file_kept_with_it(job, home, user):
    (user / "Documents" / "Reports").mkdir()
    place_job(job, home, user, "~/Documents/Reports/")
    (a,) = outbox.actions(job.job_dir)
    other = job.job_dir / "spec.md"  # something else in the job, named by a hand edit of the action file
    meta, body = mdfile.read(a.path)
    meta.update(file="spec.md", file_sha256=outbox._file_sha(other))
    mdfile.write(a.path, meta, body)
    with pytest.raises(outbox.OutboxError, match="copy kept"):
        outbox.approve(home, outbox.load(a.path))
    assert not (user / "Documents" / "Reports" / "spec.md").exists()
    assert outbox.load(a.path).status == "pending"


def test_a_deliverable_in_a_shared_folder_is_listed_with_its_full_path(job, home, user):
    folders.add(home, user / "Pictures" / "Trip", "photos", "write")
    write_role(home, "researcher", max_steps=10, tools=["workspace.write"])
    job([
        fake.brief("researcher", "Write the colour note into the photos folder."),
        fake.call("workspace__write", {"path": "shared/photos/colour.md", "content": "teal\n"}),
        fake.result("Wrote the note.", deliverables=[{"name": "Colour note", "path": "shared/photos/colour.md"}]),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ], spec_meta={"folders": ["photos"]})
    report = job.report
    deliverables = report.split("## Deliverables", 1)[1].split("\n## ", 1)[0]
    assert f"- Colour note: {user / 'Pictures' / 'Trip' / 'colour.md'}" in deliverables
    assert "could not be found" not in deliverables
