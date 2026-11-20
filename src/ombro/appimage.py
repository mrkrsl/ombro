"""Ombro run from an AppImage (Linux): the one file the AppImage package is.

An AppImage runs from a mount of itself (/tmp/.mount_…), which goes when the app quits; the
sidecar's sys.executable is in that mount. So whatever must outlive the app's window (a job, the
scheduler's entry) runs the .AppImage file itself, with --ombro, which the app's binary hands to
the sidecar (gui/src-tauri/src/main.rs) instead of opening a window. The AppImage's runtime tells
its program where the file is in $APPIMAGE.
"""

from __future__ import annotations

import os
import sys

FLAG = "--ombro"


def path() -> str | None:
    """The .AppImage file this Ombro runs from, or None (not frozen, not Linux, or not an AppImage)."""
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return None
    found = os.environ.get("APPIMAGE")
    return os.path.abspath(found) if found and os.path.isfile(found) else None


def command() -> list[str] | None:
    """How to run Ombro's command line from the AppImage file: `<file> --ombro`, or None."""
    found = path()
    return [found, FLAG] if found else None
