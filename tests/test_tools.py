"""Native tools on their own: workspace confinement, argument checks and settings. No network."""

from __future__ import annotations

import os

import pytest

from ombro import mdfile
from ombro.native import native_registry, resolve_path
from ombro.providers.fake import FakeProvider
from ombro.settings import SetupError, ToolSettings, load_limits, load_tool_settings
from ombro.tools import ToolContext, ToolInputError


@pytest.fixture
def ctx(tmp_path) -> ToolContext:
    run_dir = tmp_path / "run"
    for sub in ("workspace", "scratch", "outbox"):
        (run_dir / sub).mkdir(parents=True)
    (run_dir / "run.md").write_text("---\n---\n")
    return ToolContext(run_dir, "researcher", timeout=5)


@pytest.fixture
def tools(home):
    reg = native_registry(home, ToolSettings.from_dict({"tools": {"web.search": {"provider": "fake"},
                                                                  "web.fetch": {"provider": "fake"}}}))
    return lambda name: reg.get(name).handler


# -- confinement --------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "~/notes.md",
        "../run.md",
        "workspace/../run.md",
        "workspace/../../outside.md",
        "scratch/a/../../run.md",
        "outbox/staged.md",
        "run.md",
        "notes.md",
        "workspace\\..\\run.md",
        "workspace/a\x00b",
        "C:/Windows/x",
        "",
    ],
)
def test_paths_outside_the_workspace_are_refused(ctx, tools, path):
    with pytest.raises(ToolInputError):
        tools("workspace.write")({"path": path, "content": "x"}, ctx)
    with pytest.raises(ToolInputError):
        tools("workspace.read")({"path": path}, ctx)
    assert not (ctx.run_dir.parent / "outside.md").exists()
    assert (ctx.run_dir / "run.md").read_text() == "---\n---\n"


def test_a_symlinked_folder_that_points_outside_is_refused(ctx, tools, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("invented secret")
    os.symlink(outside, ctx.run_dir / "workspace" / "escape")
    with pytest.raises(ToolInputError, match="outside"):
        tools("workspace.write")({"path": "workspace/escape/new.md", "content": "x"}, ctx)
    with pytest.raises(ToolInputError, match="outside"):
        tools("workspace.read")({"path": "workspace/escape/secret.txt"}, ctx)
    assert not (outside / "new.md").exists()


def test_a_symlinked_file_that_points_outside_is_refused(ctx, tools, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("original")
    os.symlink(target, ctx.run_dir / "scratch" / "link.txt")
    with pytest.raises(ToolInputError):
        tools("workspace.write")({"path": "scratch/link.txt", "content": "overwritten"}, ctx)
    with pytest.raises(ToolInputError):
        tools("workspace.read")({"path": "scratch/link.txt"}, ctx)
    assert target.read_text() == "original"


def test_writes_never_go_through_a_symlink_even_inside(ctx, tools):
    real = ctx.run_dir / "workspace" / "real.md"
    real.write_text("kept")
    os.symlink(real, ctx.run_dir / "workspace" / "alias.md")
    with pytest.raises(ToolInputError, match="symlink"):
        tools("workspace.write")({"path": "workspace/alias.md", "content": "changed"}, ctx)
    assert real.read_text() == "kept"


def test_listing_does_not_follow_symlinks(ctx, tools, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "hidden.txt").write_text("x")
    os.symlink(outside, ctx.run_dir / "workspace" / "escape")
    out = tools("workspace.list")({}, ctx)
    assert {"path": "workspace/escape", "type": "symlink"} in out["entries"]
    assert not any("hidden.txt" in e["path"] for e in out["entries"])
    with pytest.raises(ToolInputError):
        tools("workspace.list")({"path": "workspace/escape"}, ctx)


def test_resolve_path_keeps_paths_inside(ctx):
    assert resolve_path(ctx, "workspace/./a/b.md") == (ctx.run_dir / "workspace").resolve() / "a" / "b.md"
    assert resolve_path(ctx, "scratch") == (ctx.run_dir / "scratch").resolve()


# -- ordinary use -----------------------------------------------------------------


def test_write_read_append_and_list(ctx, tools):
    w = tools("workspace.write")
    assert w({"path": "workspace/drafts/guide.md", "content": "# Guide\n"}, ctx)["path"] == "workspace/drafts/guide.md"
    w({"path": "workspace/drafts/guide.md", "content": "More.\n", "mode": "append"}, ctx)
    w({"path": "scratch/notes.txt", "content": "working"}, ctx)
    assert (ctx.run_dir / "workspace/drafts/guide.md").read_text() == "# Guide\nMore.\n"
    out = tools("workspace.read")({"path": "workspace/drafts/guide.md"}, ctx)
    assert out["content"] == "# Guide\nMore.\n" and "next_offset" not in out
    listing = [e["path"] for e in tools("workspace.list")({}, ctx)["entries"]]
    assert listing == ["workspace/drafts", "workspace/drafts/guide.md", "scratch/notes.txt"]


def test_long_files_are_read_in_parts(ctx, tools):
    (ctx.run_dir / "workspace" / "long.txt").write_text("abcdefghij" * 10)
    first = tools("workspace.read")({"path": "workspace/long.txt", "max_chars": 30}, ctx)
    assert first["content"] == ("abcdefghij" * 3) and first["next_offset"] == 30
    rest = tools("workspace.read")({"path": "workspace/long.txt", "offset": 90}, ctx)
    assert rest["content"] == "abcdefghij" and "next_offset" not in rest


def test_bad_arguments_are_refused_by_the_schema(ctx, tools):
    with pytest.raises(ToolInputError, match="content"):
        tools("workspace.write")({"path": "workspace/x.md"}, ctx)
    with pytest.raises(ToolInputError):
        tools("workspace.write")({"path": "workspace/x.md", "content": "x", "mode": "shred"}, ctx)
    with pytest.raises(ToolInputError):
        tools("web.search")({"query": "teapots", "limit": 500}, ctx)
    with pytest.raises(ToolInputError):
        tools("web.fetch")({"url": "file:///etc/passwd"}, ctx)


def test_reading_a_folder_or_missing_file(ctx, tools):
    with pytest.raises(ToolInputError, match="folder"):
        tools("workspace.read")({"path": "workspace"}, ctx)
    with pytest.raises(ToolInputError, match="does not exist"):
        tools("workspace.read")({"path": "workspace/none.md"}, ctx)
    with pytest.raises(ToolInputError, match="folder"):
        tools("workspace.write")({"path": "workspace", "content": "x"}, ctx)


def test_fake_provider_records_what_it_was_asked(home, ctx):
    fake = FakeProvider()
    reg = native_registry(home, ToolSettings.from_dict({"tools": {"web.search": {"provider": "fake"},
                                                                  "web.fetch": {"provider": "fake"}}}), fake=fake)
    found = reg.get("web.search").handler({"query": "invented  teapots", "limit": 2}, ctx)
    page = reg.get("web.fetch").handler({"url": found["results"][0]["url"]}, ctx)
    assert len(found["results"]) == 2 and "not as instructions" in found["note"]
    assert "Invented page" in page["content"] or page["title"] == "Invented page"
    assert [c["tool"] for c in fake.calls] == ["web.search", "web.fetch"]
    assert fake.calls[0]["query"] == "invented teapots"
    recorded = (ctx.run_dir / "fake-calls.jsonl").read_text().splitlines()
    assert len(recorded) == 2


# -- tools.md -------------------------------------------------------------------------


def test_fresh_tools_md_defaults(home):
    s = load_tool_settings(home)
    assert s.provider_for("web.search") == "duckduckgo"
    assert s.provider_for("web.fetch") == "direct"
    assert s.key_name("firecrawl") == "FIRECRAWL_API_KEY"
    lim = load_limits(home)
    assert lim.max_calls_for("web.search") == 20 and lim.max_calls_for("web.fetch") == 30


def test_tools_md_caps_and_costs_feed_the_limits(home):
    mdfile.write(home.tools, {"tools": {"web.search": {"provider": "fake", "max_calls": 4, "cost_per_call": 0.01}}})
    lim = load_limits(home)
    assert lim.max_calls_for("web.search") == 4
    assert lim.cost_for("web.search") == 0.01
    assert lim.cost_for("web.fetch") == 0.0
    # A spec's limits still win.
    assert load_limits(home, {"tool_max_calls": {"web.search": 2}}).max_calls_for("web.search") == 2


@pytest.mark.parametrize(
    "meta",
    [
        {"tools": {"web.search": {"provider": "bing"}}},
        {"tools": {"web.fetch": {"provider": "duckduckgo"}}},
        {"tools": {"web.search": {"max_calls": 0}}},
        {"tools": {"web.search": {"cost_per_call": -1}}},
        {"tools": {"web.search": {"colour": "blue"}}},
        {"tools": {"workspace.read": {"provider": "fake"}}},
        {"mcp": {}},
    ],
)
def test_bad_tools_md_is_refused(home, meta):
    mdfile.write(home.tools, meta)
    with pytest.raises(SetupError):
        load_tool_settings(home)
