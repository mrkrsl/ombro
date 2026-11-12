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
from ombro.tools import area_path

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
    outward: dict[str, list[dict[str, Any]]] = {"staged": [], "done": [], "errors": [], "refused": [],
                                                 "duplicates": [], "uncertain": []}
    code_runs: list[dict[str, Any]] = []
    folder_changes: dict[str, dict[str, Any]] = {}
    placed: list[dict[str, Any]] = []
    not_shared: list[str] = []
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
        elif action == "staged":
            outward["staged"].append(ev)
        elif action == "staged_duplicate":
            outward["duplicates"].append(ev)
        elif action == "outward_done":
            outward["done"].append(ev)
        elif action == "outward_error":
            outward["errors"].append(ev)
        elif action == "outward_uncertain":
            outward["uncertain"].append(ev)
        elif action == "tool_refused" and ev.get("permission") == "refuse":
            outward["refused"].append(ev)
        elif action == "code_run":
            code_runs.append(ev)
        elif action == "folder_changes":
            folder_changes[str(ev.get("folder"))] = ev
        elif action == "placed":
            placed.append(ev)
        elif action == "tool_error" and ev.get("not_shared"):
            if ev["not_shared"] not in not_shared:
                not_shared.append(ev["not_shared"])
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

    workspace = area_path(run_dir, "workspace")
    scratch = area_path(run_dir, "scratch")
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
    if workspace.is_dir() and not workspace.is_symlink():
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
        "outward": outward,
        "outward_setting": meta.get("outward") or {},
        "code_runs": code_runs,
        "folders": list(meta.get("folders") or []),
        "folders_left_out": list(meta.get("folders_left_out") or []),
        "folder_changes": folder_changes,
        "placed": placed,
        "not_shared": not_shared,
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
        root = area_path(run_dir, area)
        if root.is_symlink():  # replaced during the run: nothing in it is listed as a file
            return {"name": name, "path": None, "inline": None, "missing": path}
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
    ow = facts.get("outward") or {}
    if ow.get("staged") or ow.get("done") or ow.get("uncertain"):
        lines.append(f"Outward actions: {len(ow.get('done') or [])} done during the run "
                     "(internal changes, or external actions the user allowed), "
                     f"{len(ow.get('staged') or [])} staged and waiting for the user's approval"
                     + (f", {len(ow['uncertain'])} uncertain (sent, but no answer came back)"
                        if ow.get("uncertain") else "") + ".")
    if facts.get("code_runs"):
        stopped = sum(1 for c in facts["code_runs"] if c.get("limit"))
        failed = sum(1 for c in facts["code_runs"] if not c.get("limit") and c.get("exit_code"))
        lines.append(f"Scripts run with code.run: {len(facts['code_runs'])}"
                     + (f" ({failed} ended with an error, {stopped} stopped by a limit)" if failed or stopped else "")
                     + ".")
    for name, ev in (facts.get("folder_changes") or {}).items():
        c = ev.get("counts") or {}
        lines.append(f"Shared folder {name}: {c.get('created', 0)} files created, {c.get('changed', 0)} changed, "
                     f"{c.get('removed', 0)} removed (the harness's own comparison).")
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

    out += ["## Outward actions", "", *outbox_lines(f), ""]
    if f.get("code_runs"):
        out += ["## Code", "", *code_lines(f), ""]
    if f.get("folders") or f.get("not_shared") or f.get("folders_left_out"):
        out += ["## Shared folders", "", *folder_lines(f), ""]
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


def _args_line(ev: dict[str, Any], n: int = 200) -> str:
    args = ev.get("arguments")
    if isinstance(args, dict) and args:
        return _line("; ".join(f"{k}: {v}" for k, v in args.items()), n)
    return "(no arguments)"


def outbox_lines(f: dict[str, Any]) -> list[str]:
    """Outward actions, from the harness's own record (the journal), never from the Lead."""
    ow = f.get("outward") or {}
    staged, done = ow.get("staged") or [], ow.get("done") or []
    errors, refused, dups = ow.get("errors") or [], ow.get("refused") or [], ow.get("duplicates") or []
    uncertain = ow.get("uncertain") or []
    if not any((staged, done, errors, refused, uncertain)):
        return ["None: nothing was done outside the run, and nothing is staged."]
    work = [ev for ev in done if ev.get("effect") in ("internal", "read-only")]
    sent = [ev for ev in done if ev not in work]
    lines: list[str] = []
    setting = f.get("outward_setting") or {}
    if setting:
        from ombro.outward import describe

        lines += [f"Permissions for this run: {describe(setting)}.", ""]
    if work:
        lines.append(f"Internal changes made during the run ({len(work)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in work]
        lines.append("")
    if sent:
        lines.append(f"Sent during the run, as your permissions allow ({len(sent)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in sent]
        lines.append("")
    if staged:
        lines.append(f"Staged, waiting for you ({len(staged)}). Nothing here has happened:")
        for ev in staged:
            lines.append(f"- **{ev.get('ref')}** `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}")
            if ev.get("reason"):
                lines.append(f"  - Reason: {_line(ev['reason'], 200)}")
        if dups:
            lines.append(f"- ({len(dups)} identical repeat(s) were not staged again.)")
        lines += ["", "See them with `ombro outbox`; approve or decline each by its reference, e.g. "
                  f"`ombro outbox show {staged[0].get('ref')}`.", ""]
    if uncertain:
        lines.append(f"Uncertain: sent during the run, but no answer came back, so it may or may not have "
                     f"happened ({len(uncertain)}). Check the service:")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in uncertain]
        lines.append("")
    if errors:
        lines.append(f"Tried during the run, but the service reported an error ({len(errors)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_line(ev.get('note', ''), 200)}" for ev in errors]
        lines.append("")
    if refused:
        lines.append(f"Refused by tools.md ({len(refused)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')})" for ev in refused]
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def code_lines(f: dict[str, Any]) -> list[str]:
    """Each script the run executed, from the harness's own record."""
    lines = [f"Scripts run with code.run, in the sandbox ({len(f['code_runs'])}). Each script and its output are "
             "kept in the run's code/ folder:"]
    for ev in f["code_runs"]:
        status = (f"stopped by `{ev['limit']}`" if ev.get("limit") else f"exit {ev.get('exit_code')}")
        what = f": {_line(ev['purpose'], 160)}" if ev.get("purpose") else ""
        lines.append(f"- `{ev.get('script')}` ({ev.get('role')}){what}. {status}, "
                     f"{float(ev.get('seconds') or 0):.1f}s, {ev.get('changed', 0)} file(s) changed")
    return lines


def _change_list(label: str, items: list[str], total: int, n: int = 30) -> list[str]:
    if not total:
        return []
    out = [f"{label} ({total}):"] + [f"- {x}" for x in items[:n]]
    if total > len(items[:n]):
        out.append(f"- and {total - len(items[:n])} more")
    return out


def folder_lines(f: dict[str, Any]) -> list[str]:
    """Shared folders, their access, and what changed in them, from the harness's own record."""
    lines: list[str] = []
    changes = f.get("folder_changes") or {}
    for entry in f.get("folders") or []:
        name, access = entry.get("name"), entry.get("access")
        lines.append(f"- **shared/{name}** ({'read and write' if access == 'write' else 'read only'}): "
                     f"{entry.get('path')}")
        if access != "write":
            continue
        if entry.get("backup"):
            lines.append(f"  - Backup: cloned before the run to {entry['backup']}. To restore a file, copy it "
                         "back from there.")
        elif entry.get("backup_note"):
            lines.append(f"  - Backup: {entry['backup_note']}.")
        ev = changes.get(str(name))
        if ev is None:
            lines.append("  - Changes: not recorded (the run ended before the folder could be compared); compare "
                         "it with the backup.")
        elif ev.get("too_large"):
            lines.append("  - Changes: the folder has too many files to compare before and after.")
        elif not ev.get("total"):
            lines.append("  - Changes: none.")
        else:
            c = ev.get("counts") or {}
            for label, key in (("Created", "created"), ("Changed", "changed"), ("Removed", "removed")):
                lines += ["  " + x for x in _change_list(label, ev.get(key) or [], int(c.get(key) or 0))]
    if f.get("placed"):
        lines += ["", f"Copied into shared folders with workspace.place ({len(f['placed'])}):"]
        lines += [f"- {ev.get('note')} ({ev.get('role')})" for ev in f["placed"]]
    for note in f.get("folders_left_out") or []:
        lines.append(f"- Left out: {note}")
    if f.get("not_shared"):
        lines += ["", "Refused because they are not in a folder shared with this run. Share the folder with "
                  "`ombro folders add <path>` if a later run should reach it:"]
        lines += [f"- {_line(p, 200)}" for p in f["not_shared"][:20]]
    if not lines:
        lines.append("None.")
    return lines


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
