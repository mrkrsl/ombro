"""Set Ombro's version everywhere it is written: `python3 scripts/bump_version.py 0.2.0`.

The core's version in src/ombro/__init__.py is the one version (the wheel takes it from
there); the app's manifests and lockfiles carry copies, which tests/test_version.py holds
to it. This changes them all together, and nothing else. The app/server protocol version
is a separate number and isn't touched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-.]?\w+)?")

# (file, pattern whose group "v" is the version, how many matches to expect)
PLACES = [
    ("src/ombro/__init__.py", r'^__version__ = "(?P<v>[^"]+)"', 1),
    ("gui/package.json", r'^  "version": "(?P<v>[^"]+)"', 1),
    # The lockfile names the app twice: at its top, and as its own root package.
    ("gui/package-lock.json", r'^( *)"name": "ombro-gui",\n\1"version": "(?P<v>[^"]+)"', 2),
    ("gui/src-tauri/tauri.conf.json", r'^  "version": "(?P<v>[^"]+)"', 1),
    ("gui/src-tauri/Cargo.toml", r'^\[package\]\nname = "ombro-gui"\nversion = "(?P<v>[^"]+)"', 1),
    ("gui/src-tauri/Cargo.lock", r'^name = "ombro-gui"\nversion = "(?P<v>[^"]+)"', 1),
]


def bump(new: str, root: Path = ROOT) -> list[str]:
    """Write `new` into every place; return the files changed. Nothing is written unless
    every place is found as expected."""
    if not VERSION.fullmatch(new):
        raise SystemExit(f"not a version: {new!r} (expected e.g. 0.2.0 or 0.2.0-rc1)")
    edits = []
    for name, pattern, expected in PLACES:
        path = root / name
        text = path.read_text()
        matches = list(re.finditer(pattern, text, re.MULTILINE))
        if len(matches) != expected:
            raise SystemExit(f"{name}: found the version {len(matches)} times, expected {expected}; nothing changed")
        for m in reversed(matches):
            text = text[: m.start("v")] + new + text[m.end("v") :]
        edits.append((path, text))
    changed = []
    for path, text in edits:
        if path.read_text() != text:
            path.write_text(text)
            changed.append(str(path.relative_to(root)))
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0])
        return 2
    changed = bump(argv[0])
    print("\n".join(changed) if changed else f"already {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
