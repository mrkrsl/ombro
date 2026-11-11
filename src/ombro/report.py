"""The final report. Facts come from the journal and run.md; the Lead adds judgement.

Building the facts from files (not from memory) means the same code serves a
normal run, a stopped run and a run whose process died.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ombro import journal as journal_mod
from ombro import mdfile, runs
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.settings import format_duration, format_tokens, load_preferences

NARRATIVE_INPUT_CAP = 24_000
PER_BRIEF_CAP = 2_000


def _short(text: str, n: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _line(text: str, n: int) -> str:
    """One line for a list entry: whitespace collapsed, then shortened."""
    return _short(" ".join(str(text or "").split()), n)


CLOSING_NOTE_CAP = 400


def gather_facts(home: Home, run_dir: Path, status: str, failure: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    meta, _ = runs.read_run(run_dir)
    try:
        _, spec_body = mdfile.read(run_dir / "spec.md")
    except (OSError, mdfile.FrontMatterError):
        spec_body = "(spec.md could not be read)"
    events = journal_mod.read(run_dir / "journal.jsonl")

    briefs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    limit = None
    finish = None
    warnings: list[str] = []
    failures: list[str] = []
    brief_limits: list[dict[str, Any]] = []
    gaps: list[str] = []
    notes: dict[str, Any] | None = None
    for ev in events:
        action = ev.get("action")
        if action == "briefed":
            current = {"role": ev.get("target", "?"), "task": ev.get("task", ""), "status": "in_progress"}
            briefs.append(current)
        elif action == "returned" and current is not None:
            current["status"] = ev.get("status", "?")
            current["result"] = ev.get("result")
            current["reason"] = ev.get("reason")
            if ev.get("status") == "stopped_by_limit":
                brief_limits.append({"role": current["role"], "limit": ev.get("limit"), "reason": ev.get("reason")})
            current = None
        elif action == "limit_reached":
            limit = {"limit": ev.get("limit"), "detail": ev.get("note", "")}
        elif action == "finish":
            finish = {"outcome": ev.get("outcome"), "summary": ev.get("summary", "")}
        elif action == "warning":
            warnings.append(ev.get("note", ""))
        elif action == "failed":
            failures.append(ev.get("note", ""))
        elif action == "insufficient_brief":
            gaps = list(ev.get("gaps") or [])
        elif action == "notes":
            notes = ev
    if failure:
        failures.append(failure)

    spend = dict(meta.get("spend") or {})
    if not spend.get("elapsed_seconds") and meta.get("started_at") and events:
        try:
            start = datetime.strptime(meta["started_at"], "%Y-%m-%dT%H:%M:%SZ")
            end = datetime.strptime(events[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ")
            spend["elapsed_seconds"] = max(0.0, (end - start).total_seconds())
        except (KeyError, ValueError):
            pass

    workspace = (run_dir / "workspace").resolve()
    scratch = (run_dir / "scratch").resolve()
    # Only files in workspace/ are deliverables. Working files in scratch/ and
    # inline notes are listed apart, so the list says what the run produced.
    deliverables, working, inline, missing = [], [], [], []
    seen: set[str] = set()
    for b in briefs:
        for d in (b.get("result") or {}).get("deliverables") or []:
            entry = {"role": b["role"], **_resolve_deliverable(run_dir, d)}
            if entry.get("path"):
                if entry["path"] in seen:
                    continue
                seen.add(entry["path"])
                (working if Path(entry["path"]).is_relative_to(scratch) else deliverables).append(entry)
            elif entry.get("missing"):
                missing.append(entry)
            elif str(entry.get("inline") or "").strip():
                inline.append(entry)
    other = []
    if workspace.is_dir():
        for f in sorted(workspace.rglob("*")):
            if f.is_file() and not f.is_symlink() and str(f.resolve()) not in seen:
                other.append(str(f.resolve()))

    in_progress = briefs[-1] if briefs and briefs[-1]["status"] == "in_progress" else None
    return {
        "run_id": meta.get("run_id", run_dir.name),
        "crew": meta.get("crew", ""),
        "status": status,
        "brief": meta.get("brief", ""),
        "source": meta.get("source", ""),
        "started_at": meta.get("started_at") or meta.get("created_at"),
        "finished_at": utc_now(),
        "spec": spec_body.strip(),
        "limits": meta.get("limits") or {},
        "spend": spend,
        "briefs": briefs,
        "in_progress": in_progress,
        "limit": limit,
        "finish": finish,
        "warnings": warnings,
        "failures": failures,
        "brief_limits": brief_limits,
        "gaps": gaps,
        "interview_spend": meta.get("interview_spend") or {},
        "deliverables": deliverables,
        "working_files": working,
        "inline_notes": inline,
        "missing_deliverables": missing,
        "other_files": other,
        "notes": notes,
        "workspace": str(workspace),
        "display_currency": load_preferences(home).display_currency,
    }


def _resolve_deliverable(run_dir: Path, d: dict) -> dict[str, Any]:
    """A deliverable's path may be `workspace/x`, `scratch/x` or plain `x` (in the
    workspace). Anything that leads outside those folders is treated as inline text."""
    name, path = str(d.get("name", "")), str(d.get("path", ""))
    if path and "\n" not in path and len(path) < 300 and not path.startswith(("/", "~")):
        first = path.split("/", 1)[0]
        area = first if first in ("workspace", "scratch") and "/" in path else "workspace"
        rel = path.split("/", 1)[1] if area == first and "/" in path else path
        root = (run_dir / area).resolve()
        candidate = (root / rel).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return {"name": name, "path": str(candidate), "inline": None}
        if candidate.is_relative_to(root) and ("/" in path or "." in path.rsplit("/", 1)[-1]) and " " not in path:
            return {"name": name, "path": None, "inline": None, "missing": path}
    return {"name": name, "path": None, "inline": path}


# ---------------------------------------------------------------------------
# What the Lead is shown when asked for the report


def narrative_input(facts: dict[str, Any]) -> str:
    lines = [f"Spec:\n{_short(facts['spec'], 6000)}", "", f"Run status: {facts['status']}"]
    if facts["limit"]:
        lines.append(f"Stopped by limit `{facts['limit']['limit']}`: {facts['limit']['detail']}")
    for f in facts["failures"]:
        lines.append(f"Failure: {f}")
    if facts["finish"]:
        lines.append(f"You called finish ({facts['finish']['outcome']}): {facts['finish']['summary']}")
    lines.append("")
    if not facts["briefs"]:
        lines.append("No roles were briefed.")
    for i, b in enumerate(facts["briefs"], 1):
        entry = [f"Brief {i}: {b['role']} — status {b['status']}", f"Task: {_short(b['task'], 600)}"]
        res = b.get("result")
        if res:
            entry.append(f"Summary: {res.get('summary', '')}")
            for key in ("deliverables", "did_not_do", "open_questions", "requests"):
                if res.get(key):
                    entry.append(f"{key}: {res[key]}")
        if b.get("reason"):
            label = "Stopped" if b["status"] == "stopped_by_limit" else "Failure"
            entry.append(f"{label}: {b['reason']}")
        if b["status"] == "in_progress":
            entry.append("This brief was still in progress when the run stopped.")
        lines.append(_short("\n".join(entry), PER_BRIEF_CAP))
        lines.append("")
    return _short("\n".join(lines), NARRATIVE_INPUT_CAP)


def mechanical_narrative(facts: dict[str, Any]) -> str:
    if facts.get("gaps"):
        return "\n".join(
            [
                "## What was done",
                "No roles were briefed. The task interview ended without a confirmed spec.",
                "",
                "## What was not done and why",
                "The brief was insufficient. These answers were missing or contradicted each other:",
                *[f"- {g}" for g in facts["gaps"]],
                "",
                "## Decisions for you",
                "- Settle the gaps above, then brief the task again with `ombro brief` or `ombro run`.",
            ]
        )
    done, not_done, decisions = [], [], []
    for b in facts["briefs"]:
        res = b.get("result")
        if b["status"] == "ok" and res:
            done.append(f"- **{b['role']}**: {res.get('summary', '').strip()}")
            not_done += [f"- {b['role']}: {x}" for x in res.get("did_not_do") or []]
            decisions += [f"- {b['role']} asks: {q}" for q in res.get("open_questions") or []]
            decisions += [
                f"- {b['role']} requested helper work ({r.get('helper') or 'any helper'}): {r.get('task')}"
                for r in res.get("requests") or []
            ]
        elif b["status"] == "failed":
            not_done.append(f"- {b['role']} failed its brief: {b.get('reason') or 'no reason recorded'}")
        elif b["status"] == "stopped_by_limit":
            not_done.append(f"- {b['role']}'s brief was {b.get('reason') or 'stopped by a limit'}")
        elif b["status"] == "in_progress":
            not_done.append(f"- {b['role']} was working on this when the run stopped: {_short(b['task'], 300)}")
    if facts["limit"]:
        not_done.append(f"- The run was stopped by `{facts['limit']['limit']}`: {facts['limit']['detail']}")
    for f in facts["failures"]:
        not_done.append(f"- The run failed: {f}")
    return "\n".join(
        [
            "## What was done",
            "\n".join(done) or "No role completed a brief.",
            "",
            "## What was not done and why",
            "\n".join(not_done) or "Nothing recorded.",
            "",
            "## Decisions for you",
            "\n".join(decisions) or "None recorded.",
        ]
    )


def _clean_narrative(text: str) -> str:
    lines = text.strip().splitlines()
    while lines and lines[0].startswith("# "):
        lines.pop(0)
    text = "\n".join(lines).strip()
    if "## " not in text:
        text = "## The Lead's account\n" + text
    return text


# ---------------------------------------------------------------------------
# Rendering


def _money(usd: float, cur: dict | None) -> str:
    s = f"${usd:.4f}"
    if cur:
        s += f" (≈ {cur['code']} {usd * cur['rate']:.4f})"
    return s


def render(facts: dict[str, Any], narrative: str | None, note: str | None = None) -> str:
    f = facts
    lim = f["limits"]
    sp = f["spend"]
    out = [f"# Report: {f['run_id']}", ""]
    out.append(f"- **Status:** {f['status']}")
    out.append(f"- **Crew:** {f['crew']}")
    out.append(f"- **Started:** {f['started_at']}")
    out.append(f"- **Finished:** {f['finished_at']}")
    if f["limit"]:
        out.append(f"- **Stopped by:** `{f['limit']['limit']}`: {f['limit']['detail']}")
    for fail in f["failures"]:
        out.append(f"- **Failed:** {fail}")
    if f["finish"]:
        note_text = _line(f["finish"]["summary"], CLOSING_NOTE_CAP)
        if len(" ".join(str(f["finish"]["summary"] or "").split())) > CLOSING_NOTE_CAP:
            note_text += " (shortened; the full note is in the journal)"
        out.append(f"- **Lead's closing note ({f['finish']['outcome']}):** {note_text}")
    out += ["", "## What was asked", ""]
    out += ["> " + line if line else ">" for line in _short(f["spec"], 4000).splitlines()]
    if f["source"]:
        out += ["", f"_Source: {f['source']}_"]
    out.append("")

    if narrative:
        out.append(_clean_narrative(narrative))
    else:
        out.append(mechanical_narrative(f))
        out += ["", f"_This part was written by the harness from the journal{': ' + note if note else ''}._"]
    out.append("")

    out += ["## Briefs (harness record)", ""]
    if not f["briefs"]:
        out.append("No roles were briefed.")
    for i, b in enumerate(f["briefs"], 1):
        out.append(f"{i}. **{b['role']}** ({b['status']}): {_line(b['task'], 200)}")
        if b.get("result"):
            out.append(f"   - {_line(b['result'].get('summary', ''), 500)}")
        if b.get("reason"):
            out.append(f"   - {_line(b['reason'], 500)}")
    out.append("")

    out += ["## Deliverables", ""]
    if not f["deliverables"] and not f.get("other_files") and not f.get("missing_deliverables"):
        out.append("None.")
    for d in f["deliverables"]:
        out.append(f"- {d['name']} ({d['role']}): {d['path']}")
    for d in f.get("missing_deliverables") or []:
        out.append(f"- {d['name']} ({d['role']}): {d['missing']} was named but is not in the workspace")
    other = f.get("other_files") or []
    if other:
        out += ["", "Other files in the workspace that no role named as a deliverable:"]
        out += [f"- {p}" for p in other[:20]]
        if len(other) > 20:
            out.append(f"- and {len(other) - 20} more")
    if f.get("working_files"):
        out += ["", "Working files (scratch/, not deliverables): "
                + ", ".join(f"{d['path']} ({d['role']})" for d in f["working_files"])]
    if f.get("inline_notes"):
        out += ["", "### Notes from roles", ""]
        out += [f"- {d['name']} ({d['role']}): {_line(d['inline'], 300)}" for d in f["inline_notes"]]
    out += ["", f"Workspace: {f['workspace']}", ""]

    out += ["## Outbox", "", "Nothing staged.", ""]
    out += ["## Changes to notes.md", "", *notes_lines(f.get("notes")), ""]

    cur = f["display_currency"]
    out += ["## Cost", ""]
    cap_tokens = lim.get("max_tokens")
    allowance = lim.get("allowance")
    out.append(
        f"- Tokens: {sp.get('total_tokens', 0):,} ({sp.get('input_tokens', 0):,} in, "
        f"{sp.get('output_tokens', 0):,} out) of {format_tokens(cap_tokens)}"
        + (f" (allowance: {allowance})" if allowance and allowance != "custom" else "")
    )
    if sp.get("usd"):
        line = f"- Estimated cost: {_money(sp.get('usd', 0.0), cur)}, from the prices in models.md (not a limit)"
        out.append(line)
        if sp.get("unpriced_calls"):
            out.append(f"  - {sp['unpriced_calls']} model call(s) had no price in models.md and are not in the estimate")
    if sp.get("estimated_calls"):
        out.append(f"  - {sp['estimated_calls']} model call(s) reported no token counts; estimates were used")
    out.append(f"- Model calls: {sp.get('model_calls', 0)}")
    isp = f.get("interview_spend") or {}
    if isp:
        out.append(
            f"- Task interview (separate from the run's limits): {isp.get('total_tokens', 0):,} tokens, "
            f"{isp.get('model_calls', 0)} model calls"
        )
    if sp.get("tool_usd"):
        out.append(f"  - of which tools (cost_per_call): {_money(sp['tool_usd'], cur)}")
    tools = sp.get("tool_calls") or {}
    caps = lim.get("tool_max_calls") or {}

    def cap(name: str) -> Any:
        return caps.get(name, caps.get("default", "?"))

    out.append("- Tool calls: " + (", ".join(f"{k} {v} of {cap(k)}" for k, v in sorted(tools.items())) or "none"))
    refused = sp.get("tool_refused") or {}
    if refused:
        out.append(
            "  - refused at their cap: " + ", ".join(f"{k} {v}" for k, v in sorted(refused.items()))
        )
    out.append(f"- Rounds: {sp.get('rounds', 0)} of {lim.get('max_rounds', 0)}")
    out.append(
        f"- Time: {format_duration(sp.get('elapsed_seconds', 0.0))} of {format_duration(lim.get('run_timeout', 0))}"
    )
    out.append(f"- Limit that stopped the run: {('`' + f['limit']['limit'] + '`') if f['limit'] else 'none'}")
    if f.get("brief_limits"):
        out.append(
            "- Limits that ended a brief: "
            + ", ".join(f"`{x['limit']}` ({x['role']})" for x in f["brief_limits"])
        )
    if f["warnings"]:
        out += ["", "## Warnings", ""] + [f"- {w}" for w in f["warnings"]]
    return "\n".join(out).rstrip() + "\n"


def notes_lines(ev: dict[str, Any] | None) -> list[str]:
    """What the report says about notes.md, from the harness's own record."""
    if not ev:
        return ["None."]
    outcome, note = ev.get("outcome"), ev.get("note", "")
    if outcome == "changed":
        added, removed = ev.get("added") or [], ev.get("removed") or []
        lines = [f"The Lead rewrote its part (\"From runs\"). notes.md is now {ev.get('size', 0):,} of "
                 f"{ev.get('cap', 0):,} characters."]
        for label, items in (("Added", added), ("Removed", removed)):
            if items:
                lines += ["", f"{label}:"] + [f"- {_line(x.lstrip('-* '), 300)}" for x in items[:15]]
                if len(items) > 15:
                    lines.append(f"- and {len(items) - 15} more")
        if not added and not removed:
            lines += ["", "No lesson was added or removed; only the wording or order changed."]
        return lines
    if outcome == "kept":
        return [f"None. The Lead kept the notes as they were: {note}"]
    if outcome == "refused":
        return [f"Not changed: {note}. The previous notes are kept."]
    return [f"None. The notes were not rewritten: {note}."]


def write(run_dir: Path, text: str) -> Path:
    path = Path(run_dir) / "report.md"
    mdfile.write_atomic(path, text)
    return path


def write_mechanical_report(
    home: Home,
    run_dir: Path,
    status: str,
    failure: str | None = None,
    note: str = "the run's process ended before the Lead could write it",
) -> Path:
    """Report without the model, from the journal alone. Used when a process died
    or a run could not start."""
    facts = gather_facts(home, run_dir, status, failure=failure)
    path = write(run_dir, render(facts, None, note))
    Journal(Path(run_dir) / "journal.jsonl").write("harness", "report_written", "mechanical report from the journal")
    runs.update_run(run_dir, status=status, finished_at=utc_now())
    from ombro import memory  # local import: memory depends on runs, which imports this lazily

    memory.add_index_entry(home, run_dir)
    return path
