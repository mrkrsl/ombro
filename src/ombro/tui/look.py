"""What the terminal interface shows, worked out without a terminal: the team
chart, the stats, the greeting, the footer and the logo. Plain functions,
so the rules are tested on their own (specification 14.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Colours (specification 14.1)

OMBRO = {100: "#F5FFFC", 200: "#E5FFF8", 300: "#BDDDD0", 400: "#699482", 500: "#234E3C", 600: "#0C2118",
         700: "#03110B"}


@dataclass(frozen=True)
class Palette:
    background: str
    text: str  # the conversation's own text
    strong: str  # headings, the chart's Lead, the stats' first line
    muted: str  # chart lines, stats, the welcome rule
    dim: str  # rules, glyphs, placeholder, "/ for commands", the preset
    band: str  # the background of your own lines
    band_text: str
    logo_box: str
    logo_text: str
    logo_shadow: str


LIGHT = Palette(background="#FFFFFF", text=OMBRO[700], strong=OMBRO[500], muted=OMBRO[500], dim=OMBRO[300],
                band=OMBRO[200], band_text=OMBRO[500], logo_box=OMBRO[500], logo_text=OMBRO[100],
                logo_shadow=OMBRO[300])
DARK = Palette(background=OMBRO[700], text=OMBRO[100], strong=OMBRO[300], muted=OMBRO[300], dim=OMBRO[500],
               band=OMBRO[500], band_text=OMBRO[200], logo_box=OMBRO[100], logo_text=OMBRO[700],
               logo_shadow=OMBRO[500])


def palette(dark: bool) -> Palette:
    return DARK if dark else LIGHT


# ---------------------------------------------------------------------------
# The team chart

CHART_LINES = 10


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def team_chart(lead: str, crews: list[tuple[str, list[str]]], max_lines: int = CHART_LINES) -> list[str]:
    """The Lead, then each crew with its roles as a tree, in at most `max_lines` lines.

    Everything is shown when it fits, exactly `max_lines` included. Otherwise every
    crew collapses to two lines (its title, then "└ N roles"), as many crews as fit
    are shown, and the last line is "+ N additional crews". Every item below the
    Lead starts with └; │ carries a crew's line down while more follows below it.
    """
    full = [lead]
    for i, (title, roles) in enumerate(crews):
        more = i < len(crews) - 1
        full.append(f"└ {title}")
        bar = "│ " if more else "  "
        full += [f"{bar}└ {r}" for r in roles]
    if len(full) <= max_lines:
        return full

    room = (max_lines - 1) // 2  # crews that fit with no "+ N" line
    shown = crews if len(crews) <= room else crews[: (max_lines - 2) // 2]
    hidden = len(crews) - len(shown)
    out = [lead]
    for i, (title, roles) in enumerate(shown):
        more = i < len(shown) - 1 or hidden > 0
        out.append(f"└ {title}")
        out.append(("│ " if more else "  ") + "└ " + (_n(len(roles), "role") if roles else "no roles yet"))
    if hidden:
        out.append(f"+ {_n(hidden, 'additional crew')}")
    return out


def lead_label(title: str) -> str:
    """The chart's first line: "Ombro Lead" for the stock title, any other title as typed."""
    return "Ombro Lead" if title.strip() in ("", "Lead") else title


def chart_from_summary(s: dict[str, Any]) -> list[str]:
    crews = [(c["title"], [r["title"] for r in c["roles"]]) for c in s["crews"]]
    return team_chart(lead_label(s["lead"]["title"]), crews)


# ---------------------------------------------------------------------------
# Stats, greeting, footer


def stats_lines(s: dict[str, Any]) -> list[str]:
    c = s["counts"]
    who = s["name"] or "you"
    return [
        f"Working with {who}",
        "",
        f"{_n(c['crews_ready'], 'crew')} ready to work",
        _n(c["active_runs"], "active run"),
        _n(c["scheduled_runs"], "scheduled run"),
        f"{_n(c['waiting'], 'item')} to approve",
        "",
        f"Ombro v{s['version']}",
    ]


def stats_line(s: dict[str, Any]) -> str:
    """The narrow tier's one line of stats."""
    c = s["counts"]
    return " · ".join([f"{_n(c['crews_ready'], 'crew')} ready", _n(c["active_runs"], "active run"),
                       _n(c["scheduled_runs"], "scheduled run"), f"{c['waiting']} to approve"])


def welcome(name: str, first: bool) -> str:
    who = name or "there"
    return f"Hey {who}, welcome {'to' if first else 'back to'} Ombro"


def part_of_day(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    return "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def greeting(name: str, now: datetime | None = None) -> str:
    return f"Good {part_of_day(now)}{' ' + name if name else ''}. What would you like to get done today?"


def since_line(since: dict[str, Any]) -> str | None:
    parts = []
    if since["finished"]:
        parts.append(f"{_n(since['finished'], 'run')} finished")
    if since["waiting"]:
        parts.append(f"{_n(since['waiting'], 'action')} waiting")
    return f"Since you were last here: {', '.join(parts)}." if parts else None


def usage_text(total: int, cap: int | None) -> str:
    if not cap:
        return f"Usage: {total:,} tokens"
    return f"Usage: {min(999, round(100 * total / cap))}%"


def tier(width: int, height: int) -> str:
    """wide (logo, chart and stats), medium (chart and stats) or narrow (one line)."""
    if width < 70 or height < 30:
        return "narrow"
    return "wide" if width >= 100 else "medium"


# ---------------------------------------------------------------------------
# The logo: the "ombro" wordmark, with its shadow, drawn in text as given:
# █ is the letters, ░ their shadow.

LOGO_ART = [
    "                       ██",
    " ██████   ██████████   ██░     ██████  ██████",
    "██░░░░██  ░██░░██░░██░ ██████  ░██░░█░██░░░░██",
    "██░   ██░  ██░ ██░ ██░ ██░░░██░ ██░ ░ ██░   ██░",
    "██░   ██░  ██░ ██░ ██░ ██░  ██░ ██░   ██░   ██░",
    " ██████░░ ███░ ██░ ██░ ██████░░███░    ██████░░",
    "   ░░░░░    ░░░  ░░  ░░  ░░░░░░  ░░░     ░░░░░",
]


def logo_cells(dark: bool) -> list[list[tuple[str, str | None, str | None]]]:
    """Rows of (character, foreground, background) for the logo."""
    p = palette(dark)
    width = max(len(r) for r in LOGO_ART)
    return [[(ch, p.logo_box if ch == "█" else p.logo_shadow if ch == "░" else None, None) for ch in row.ljust(width)]
            for row in LOGO_ART]


def logo_text(dark: bool):
    """The logo as a Rich Text."""
    from rich.style import Style
    from rich.text import Text

    out = Text()
    for i, row in enumerate(logo_cells(dark)):
        if i:
            out.append("\n")
        for ch, fg, bg in row:
            out.append(ch, Style(color=fg, bgcolor=bg))
    return out
