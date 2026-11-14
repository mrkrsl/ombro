"""Write the JSON Schema of everything the app gets from `ombro serve` (plan 3.5).

From the view types (ombro.viewtypes), the socket's messages and replies
(ombro.serve.protocol) and the core's events and fields (ombro.ui), to
gui/src/api/schema.json. `npm run types` then turns it into src/api/types.ts.

Run from the repository root:  uv run python gui/scripts/schema.py
With --check it writes nothing and fails if schema.json is out of date
(the Python tests run it so).

pydantic is only needed here, as a dev dependency; Ombro itself never imports it.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Union

OUT = Path(__file__).resolve().parent.parent / "src" / "api" / "schema.json"


def events() -> list[type]:
    from ombro import ui

    found = [c for c in vars(ui).values()
             if isinstance(c, type) and issubclass(c, ui.Event) and c is not ui.Event and c.kind]
    return sorted(found, key=lambda c: c.__name__)


def build() -> dict:
    from typing import TypedDict

    from pydantic import TypeAdapter

    from ombro import ui
    from ombro.serve.protocol import Api

    kinds = events()
    everything = TypedDict("OmbroApi", {"api": Api, "core_event": Union[tuple(kinds)],  # noqa: UP007
                                        "field": ui.Field, "form": ui.Form})
    schema = TypeAdapter(everything).json_schema(ref_template="#/$defs/{model}")
    defs = schema.setdefault("$defs", {})
    # An event's to_dict() starts with its type, which the dataclass holds as a ClassVar.
    for cls in kinds:
        d = defs[cls.__name__]
        d["properties"] = {"type": {"const": cls.kind, "type": "string"}, **d.get("properties", {})}
        d["required"] = ["type", *d.get("required", [])]
        assert all(f.name != "type" for f in dataclasses.fields(cls)), cls
    defs["CoreEvent"] = {"oneOf": [{"$ref": f"#/$defs/{c.__name__}"} for c in kinds]}
    from ombro.serve import protocol

    defs["ServerMessage"] = {"oneOf": [{"$ref": f"#/$defs/{t.__name__}"} for t in protocol.ServerMessage.__args__]}
    _untitle(schema)
    for name, d in defs.items():
        d["title"] = name  # each definition keeps its own name, and only it
    schema["title"] = "OmbroApi"
    return schema


def _untitle(node, *, names: bool = False) -> None:
    """pydantic titles every property; TypeScript would get a type for each. Only definitions keep
    theirs. In a map of names (properties, $defs) every key is a name, never a schema keyword."""
    if isinstance(node, dict):
        if not names:
            node.pop("title", None)
        for k, v in node.items():
            _untitle(v, names=not names and k in ("properties", "$defs"))
    elif isinstance(node, list):
        for v in node:
            _untitle(v)


def text() -> str:
    return json.dumps(build(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    new = text()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != new:
            print(f"{OUT} is out of date: run `uv run python gui/scripts/schema.py`, then `npm run types` in gui/",
                  file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(new)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
