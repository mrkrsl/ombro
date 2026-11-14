"""The desktop app's types are generated from the views and the protocol (plan 3.5): when either
changes, gui/src/api/schema.json must be written again, or this fails."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "gui" / "scripts" / "schema.py"


def test_the_app_schema_is_up_to_date():
    pytest.importorskip("pydantic")
    spec = importlib.util.spec_from_file_location("gui_schema", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run `uv run python gui/scripts/schema.py`, then `npm run types` in gui/"


def test_every_core_event_is_in_the_protocol():
    from ombro import ui
    from ombro.serve import protocol

    events = {c for c in vars(ui).values() if isinstance(c, type) and issubclass(c, ui.Event) and c is not ui.Event}
    assert events == set(protocol.CoreEvent.__args__)
