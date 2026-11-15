"""The visible folder: jobs and chats in ~/Ombro, settings and secrets in ~/.config/ombro.
Invented homes in temporary folders; no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import LEAD_REPORT, Runner
from ombro import fake, folders, mdfile, memory
from ombro.home import Home, ensure_home, files_root, tidy_layout


def test_the_usual_home_keeps_jobs_and_chats_in_a_visible_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OMBRO_HOME", raising=False)
    monkeypatch.delenv("OMBRO_FILES", raising=False)
    h = Home()
    assert h.root == (tmp_path / ".config" / "ombro").resolve()
    assert h.files == (tmp_path / "Ombro").resolve() and h.separate
    ensure_home(h)
    assert (tmp_path / "Ombro" / "Jobs").is_dir() and (tmp_path / "Ombro" / "Chats").is_dir()
    assert not (h.root / "jobs").exists() and not (h.root / "chats").exists()
    assert h.process_env() == {"OMBRO_HOME": str(h.root)}  # a child process works the folder out itself


def test_another_home_keeps_everything_in_one_place(tmp_path, monkeypatch):
    monkeypatch.delenv("OMBRO_FILES", raising=False)
    h = Home(tmp_path / "demo")
    assert not h.separate and h.jobs_dir == h.root / "jobs" and h.chats_dir == h.root / "chats"
    assert files_root(h.root) == h.root


def test_ombro_files_names_the_visible_folder_and_is_passed_on(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRO_FILES", str(tmp_path / "Visible"))
    h = Home(tmp_path / "hidden")
    assert h.jobs_dir == (tmp_path / "Visible" / "Jobs").resolve()
    monkeypatch.delenv("OMBRO_FILES")
    assert h.process_env()["OMBRO_FILES"] == str((tmp_path / "Visible").resolve())


def test_an_older_home_moves_its_jobs_and_chats_once(tmp_path):
    h = Home(tmp_path / "hidden", files=tmp_path / "Ombro")
    (h.root / "jobs" / "old-job").mkdir(parents=True)
    (h.root / "jobs" / "old-job" / "report.md").write_text("An invented report.")
    (h.root / "chats").mkdir()
    (h.root / "chats" / "20261101-090000.md").write_text("An invented chat.")
    lines = tidy_layout(h)
    assert lines and "now in" in lines[0]
    assert (h.jobs_dir / "old-job" / "report.md").read_text() == "An invented report."
    assert (h.chats_dir / "20261101-090000.md").is_file()
    assert not (h.root / "jobs").exists() and tidy_layout(h) == []


def test_a_move_never_overwrites_a_folder_in_use(tmp_path):
    h = Home(tmp_path / "hidden", files=tmp_path / "Ombro")
    (h.root / "jobs" / "old").mkdir(parents=True)
    (h.jobs_dir / "new").mkdir(parents=True)
    tidy_layout(h)
    assert (h.root / "jobs" / "old").is_dir() and (h.jobs_dir / "new").is_dir()


@pytest.fixture
def split(tmp_path, monkeypatch, home):
    """The test home, with its jobs and chats in a visible folder of their own."""
    monkeypatch.setenv("OMBRO_FILES", str(tmp_path / "Ombro"))
    h = Home()
    ensure_home(h)
    return h


def test_a_job_runs_in_the_visible_folder_and_chat_reads_its_report(split):
    job = Runner(split)
    job([fake.finish(), LEAD_REPORT])
    assert job.job_dir.parent == split.jobs_dir and (job.job_dir / "report.md").is_file()
    rel = split.rel(job.job_dir / "report.md")
    assert rel == f"jobs/{job.job_dir.name}/report.md"  # prompts name it as before
    assert memory.resolve_readable(split, rel) == (job.job_dir / "report.md").resolve()
    assert memory.resolve_readable(split, str(job.job_dir / "report.md")) == (job.job_dir / "report.md").resolve()
    with pytest.raises(memory.ReadRefused):
        memory.resolve_readable(split, f"jobs/{job.job_dir.name}/journal.jsonl")


def test_the_jobs_and_chats_folders_cannot_be_shared(split, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (split.files / "Files" / "Brand").mkdir(parents=True)
    assert folders.check_place(split.files / "Files" / "Brand", split)  # beside them is fine
    for place in (split.jobs_dir, split.chats_dir, split.files):
        with pytest.raises(folders.FolderError, match="Ombro keeps itself"):
            folders.check_place(place, split)


def test_new_roles_and_the_assistant_get_everything(tmp_path):
    h = Home(tmp_path / "fresh")
    ensure_home(h)
    meta = mdfile.read(h.roles_dir("default") / "general-assistant" / "config.md")[0]
    assert meta["tools"] == "all" and meta["skills"] == "all"
    assert Path(h.jobs_dir).is_dir()
