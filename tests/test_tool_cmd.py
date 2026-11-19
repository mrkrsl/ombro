"""Changing a built-in tool (provider, call limit) from the terminal interface and the
command line. All invented; no network."""

from __future__ import annotations

import pytest

from ombro import keys, tool_cmd
from ombro.settings import SetupError, load_limits, load_tool_settings
from test_ui import ScriptedUI


def test_choosing_a_provider_that_needs_a_key_asks_for_it(home):
    ui = ScriptedUI([lambda f: next(o.key for o in f.options if o.label.startswith("Firecrawl")),
                     "fc-test-INVENTED-123", "15"])
    assert tool_cmd.flow(home, ui, "web.search") == 0
    assert load_tool_settings(home).provider_for("web.search") == "firecrawl"
    assert keys.get_key(home, "FIRECRAWL_API_KEY") == "fc-test-INVENTED-123"
    assert load_limits(home, None, load_tool_settings(home)).max_calls_for("web.search") == 15
    assert [f.kind for f in ui.fields] == ["choice", "secret", "text"]


def test_switching_back_to_duckduckgo_and_keeping_the_calls(home):
    tool_cmd.set_tool(home, "web.search", provider="firecrawl", max_calls=12)
    ui = ScriptedUI([lambda f: next(o.key for o in f.options if o.label.startswith("DuckDuckGo")), ""])
    assert tool_cmd.flow(home, ui, "web.search") == 0
    assert load_tool_settings(home).provider_for("web.search") == "duckduckgo"
    assert load_limits(home, None, load_tool_settings(home)).max_calls_for("web.search") == 12


def test_given_settings_change_without_questions_and_bad_ones_are_refused(home):
    ui = ScriptedUI([])
    assert tool_cmd.flow(home, ui, "code.run", max_calls=4) == 0 and not ui.fields
    assert load_limits(home, None, load_tool_settings(home)).max_calls_for("code.run") == 4
    with pytest.raises(SetupError, match="can use"):
        tool_cmd.set_tool(home, "web.fetch", provider="duckduckgo")
    with pytest.raises(SetupError, match="not a built-in tool"):
        tool_cmd.set_tool(home, "mcp.sheets.add_row", max_calls=3)


def test_the_command_line_changes_a_built_in_tool(home):
    from ombro.cli import main

    assert main(["tools", "web.fetch", "--max-calls", "9"]) == 0
    assert load_limits(home, None, load_tool_settings(home)).max_calls_for("web.fetch") == 9


def test_what_kind_of_tool_is_added_is_asked_unless_said():
    import pytest as _pytest

    from ombro import tool_cmd
    from ombro.settings import SetupError
    from test_ui import ScriptedUI

    assert tool_cmd.which_kind(ScriptedUI([]), api=True) == "api"
    assert tool_cmd.which_kind(ScriptedUI([]), mcp=True) == "mcp"
    ui = ScriptedUI(["mcp"])
    assert tool_cmd.which_kind(ui) == "mcp"
    with _pytest.raises(SetupError):
        tool_cmd.which_kind(ScriptedUI([]), api=True, mcp=True)
