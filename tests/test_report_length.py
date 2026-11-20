"""The report stays short: no empty sections, no lists that repeat themselves."""

from __future__ import annotations

from ombro import report


def test_a_reworded_lesson_is_counted_not_listed_twice():
    added = [
        "- Corroborate single-sourced figures from a second source; label the rest unconfirmed.",
        "- Invented bakeries in Lowtown close on Mondays.",
    ]
    removed = [
        "- Corroborate single-sourced figures from a second source (the invented round needed two).",
        "- The fake directory is out of date.",
    ]
    new, reworded, gone = report._lesson_changes(added, removed)
    assert new == ["- Invented bakeries in Lowtown close on Mondays."]
    assert reworded == 1
    assert gone == ["- The fake directory is out of date."]
    lines = report.notes_lines({"outcome": "changed", "added": added, "removed": removed, "size": 900, "cap": 10_000})
    assert lines[0].startswith('The Lead rewrote its part ("From jobs"): 1 new, 1 reworded, 1 removed.')
    assert "Corroborate" not in "\n".join(lines)


def test_only_five_new_lessons_are_listed():
    added = [f"- Invented lesson number {i} about a different Lowtown street, {'x' * i}." for i in range(8)]
    lines = report.notes_lines({"outcome": "changed", "added": added, "removed": [], "size": 1, "cap": 2})
    assert "- and 3 more" in lines


def test_a_workspace_copy_of_a_shared_deliverable_is_left_out(tmp_path):
    shared, ws = tmp_path / "Briefings", tmp_path / "workspace"
    shared.mkdir(), ws.mkdir()
    (shared / "brief.md").write_text("Same text.")
    (ws / "brief.md").write_text("Same text.")
    (ws / "other.md").write_text("Different.")
    items = [
        {"name": "Brief", "path": str(shared / "brief.md"), "shared": True},
        {"name": "Brief (copy)", "path": str(ws / "brief.md")},
        {"name": "Other", "path": str(ws / "other.md")},
    ]
    assert [d["name"] for d in report._without_copies(items)] == ["Brief", "Other"]
    (ws / "brief.md").write_text("Edited since.")
    assert len(report._without_copies(items)) == 3


def test_nothing_left_undone_means_no_section(job):
    from ombro import fake

    job(
        [
            fake.brief("researcher", "Find three invented teapot makers."),
            fake.result("Found three makers."),
            fake.finish("All done."),
            fake.keep_notes(),
            fake.say(
                "## Summary\nThe list is ready.\n\n## What was done\nThe researcher found them.\n\n"
                "## What was not done and why\nNothing."
            ),
        ]
    )
    assert "## What was done\n\nThe researcher found them." in job.report
    assert "What was not done" not in job.report
