"""The final report. Facts come from the journal and job.md; the Lead adds judgement.

Building the facts from files (not from memory) means the same code serves a
normal job, a stopped job and a job whose process died.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ombro import journal as journal_mod
from ombro import mdfile, jobs
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.settings import format_duration, format_tokens
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


def gather_facts(home: Home, job_dir: Path, status: str, failure: str | None = None) -> dict[str, Any]:
    job_dir = Path(job_dir)
    meta, _ = jobs.read_job(job_dir)
    try:
        _, spec_body = mdfile.read(job_dir / "spec.md")
    except (OSError, mdfile.FrontMatterError):
        spec_body = "(spec.md could not be read)"
    events = journal_mod.read(job_dir / "journal.jsonl")

    briefs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    limit = None
    finish = None
    warnings: list[str] = []
    failures: list[str] = []
    brief_limits: list[dict[str, Any]] = []
    gaps: list[str] = []
    notes: list[dict[str, Any]] = []
    outward: dict[str, list[dict[str, Any]]] = {"staged": [], "done": [], "errors": [], "refused": [],
                                                 "duplicates": [], "uncertain": [], "grouped": []}
    code_runs: list[dict[str, Any]] = []
    folder_changes: dict[str, dict[str, Any]] = {}
    placed: list[dict[str, Any]] = []
    not_shared: list[str] = []
    material_flags: list[dict[str, Any]] = []
    spec_roles: list[str] = []
    set_aside: list[str] = []
    tainted: list[str] = []
    skill_events: list[dict[str, Any]] = []
    skill_reads: list[dict[str, Any]] = []
    for ev in events:
        action = ev.get("action")
        if action == "spec_roles":
            spec_roles = [str(r) for r in ev.get("roles") or []]
        elif action == "material_flag":
            material_flags.append({"source": ev.get("source", "?"), "signs": ev.get("signs") or [],
                                   "role": ev.get("reader", "")})
        elif action in ("set_aside", "archived"):
            set_aside.append(str(ev.get("note") or ""))
        elif action == "staged" and ev.get("tainted"):
            tainted.append(str(ev.get("ref") or ev.get("tool") or ""))
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
            notes.append(ev)
        elif action == "skills" and not ev.get("quiet"):
            skill_events.append(ev)
        elif action == "skill_read":
            skill_reads.append(ev)
        elif action == "staged":
            outward["staged"].append(ev)
        elif action == "staged_duplicate":
            outward["duplicates"].append(ev)
        elif action == "staged_grouped":
            outward["grouped"].append(ev)
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

    usage = jobs.usage(meta)
    if not usage.get("elapsed_seconds") and meta.get("started_at") and events:
        try:
            start = datetime.strptime(meta["started_at"], "%Y-%m-%dT%H:%M:%SZ")
            end = datetime.strptime(events[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ")
            usage["elapsed_seconds"] = max(0.0, (end - start).total_seconds())
        except (KeyError, ValueError):
            pass

    workspace = area_path(job_dir, "workspace")
    scratch = area_path(job_dir, "scratch")
    # A deliverable may be a file in a folder shared with the job: shared/<name>/...
    shared = {str(x.get("name")): Path(str(x.get("path"))) for x in meta.get("folders") or []
              if isinstance(x, dict) and x.get("name") and x.get("path")}
    # Only files in workspace/ are deliverables. Working files in scratch/ and
    # inline notes are listed apart, so the list says what the job produced.
    deliverables, working, inline, missing = [], [], [], []
    seen: set[str] = set()
    for b in briefs:
        for d in (b.get("result") or {}).get("deliverables") or []:
            entry = {"role": b["role"], **_resolve_deliverable(job_dir, d, shared)}
            if entry.get("path"):
                if entry["path"] in seen:
                    continue
                seen.add(entry["path"])
                (working if not entry.get("shared") and Path(entry["path"]).is_relative_to(scratch)
                 else deliverables).append(entry)
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
        "job_id": meta.get("job_id", job_dir.name),
        "job_dir": str(job_dir),
        "crew": ", ".join(str(c) for c in meta.get("crews") or []),
        "status": status,
        "brief": meta.get("brief", ""),
        "source": meta.get("source", ""),
        "schedule": meta.get("schedule") or "",
        "again_of": meta.get("again_of") or "",
        "started_at": meta.get("started_at") or meta.get("created_at"),
        "finished_at": utc_now(),
        "spec": spec_body.strip(),
        "limits": meta.get("limits") or {},
        "usage": usage,
        "outbox_now": _outbox_now(job_dir),
        "briefs": briefs,
        "in_progress": in_progress,
        "limit": limit,
        "finish": finish,
        "warnings": warnings,
        "failures": failures,
        "brief_limits": brief_limits,
        "gaps": gaps,
        "interview_usage": jobs.interview_usage(meta),
        "deliverables": deliverables,
        "working_files": working,
        "inline_notes": inline,
        "missing_deliverables": missing,
        "other_files": other,
        "notes": notes,
        "skills": skill_events,
        "skill_reads": skill_reads,
        "outward": outward,
        "outward_setting": meta.get("outward") or {},
        "code_runs": code_runs,
        "folders": list(meta.get("folders") or []),
        "folders_left_out": list(meta.get("folders_left_out") or []),
        "folder_changes": folder_changes,
        "placed": placed,
        "not_shared": not_shared,
        "material_flags": material_flags,
        "spec_roles": spec_roles,
        "set_aside": set_aside,
        "tainted_staged": tainted,
        "workspace": str(workspace),
    }


def _outbox_now(job_dir: Path) -> dict[str, str]:
    """Each staged action's status as it stands now: decisions can be made during a job."""
    from ombro import outbox

    try:
        return {a.ref: a.status for a in outbox.actions(job_dir)}
    except (OSError, mdfile.FrontMatterError):
        return {}


def _still_waiting(f: dict[str, Any], staged: list[dict]) -> tuple[list[dict], list[dict]]:
    """Staged actions split into those still waiting and those decided during the job."""
    now = f.get("outbox_now") or {}
    waiting = [ev for ev in staged if now.get(ev.get("ref"), "pending") in ("pending", "uncertain", "executing")]
    return waiting, [ev for ev in staged if ev not in waiting]


DECIDED = {"executed": "approved and done", "declined": "declined", "failed": "approved, but it failed"}


def _without_copies(deliverables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deliverables less a workspace copy of a file also delivered to a shared folder."""
    shared = [d for d in deliverables if d.get("shared")]
    out = []
    for d in deliverables:
        if not d.get("shared") and any(_same_file(d["path"], s["path"]) for s in shared):
            continue
        out.append(d)
    return out


def _same_file(a: str, b: str) -> bool:
    try:
        pa, pb = Path(a), Path(b)
        return pa.name == pb.name and pa.stat().st_size == pb.stat().st_size and pa.read_bytes() == pb.read_bytes()
    except OSError:
        return False


def _resolve_deliverable(job_dir: Path, d: dict, shared: dict[str, Path] | None = None) -> dict[str, Any]:
    """A deliverable's path may be `workspace/x`, `scratch/x`, plain `x` (in the
    workspace) or `shared/<name>/x` in a folder shared with the job. Anything that
    leads outside those folders is treated as inline text."""
    name, path = str(d.get("name", "")), str(d.get("path", ""))
    if path.startswith("shared/") and "\n" not in path and len(path) < 600:
        parts = path.split("/")
        root = (shared or {}).get(parts[1]) if len(parts) > 2 else None
        if root is not None and ".." not in parts:
            candidate = root.joinpath(*parts[2:])
            if candidate.is_file() and not candidate.is_symlink() and candidate.resolve().is_relative_to(root):
                return {"name": name, "path": str(candidate), "inline": None, "shared": True}
            return {"name": name, "path": None, "inline": None, "missing": path}
    if path and "\n" not in path and len(path) < 300 and not path.startswith(("/", "~")):
        first = path.split("/", 1)[0]
        area = first if first in ("workspace", "scratch") and "/" in path else "workspace"
        rel = path.split("/", 1)[1] if area == first and "/" in path else path
        root = area_path(job_dir, area)
        if root.is_symlink():  # replaced during the job: nothing in it is listed as a file
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
    lines = [f"Spec:\n{_short(facts['spec'], 6000)}", "", f"Job status: {facts['status']}"]
    if facts.get("schedule"):
        lines.append(f"This job ran on the schedule '{facts['schedule']}': it makes the same thing each time.")
    if facts.get("again_of"):
        lines.append(f"This job carried on from the earlier job {facts['again_of']}, which did not finish.")
    if facts["limit"]:
        lines.append(f"Stopped by limit `{facts['limit']['limit']}`: {facts['limit']['detail']}")
    for f in facts["failures"]:
        lines.append(f"Failure: {f}")
    if facts["finish"]:
        lines.append(f"You called finish ({facts['finish']['outcome']}): {facts['finish']['summary']}")
    ow = facts.get("outward") or {}
    if ow.get("staged") or ow.get("done") or ow.get("uncertain"):
        waiting, decided = _still_waiting(facts, ow.get("staged") or [])
        lines.append(f"Outward actions: {len(ow.get('done') or [])} done during the job "
                     "(internal changes, or external actions the user allowed), "
                     f"{len(waiting)} waiting for the user's approval"
                     + (f", {len(decided)} already decided by the user" if decided else "")
                     + (f", {len(ow['uncertain'])} uncertain (sent, but no answer came back)"
                        if ow.get("uncertain") else "") + ".")
    for ev in facts.get("skills") or []:
        if ev.get("outcome") == "proposed":
            lines.append(f"You proposed a skill, {ev.get('skill')}; it waits for the user's approval as {ev.get('ref')} "
                         "for the user's approval.")
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
            entry.append("This brief was still in progress when the job stopped.")
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
                "- Settle the gaps above, then brief the task again with `ombro brief` or `ombro start`.",
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
            not_done.append(f"- {b['role']} was working on this when the job stopped: {_short(b['task'], 300)}")
    if facts["limit"]:
        not_done.append(f"- The job was stopped by `{facts['limit']['limit']}`: {facts['limit']['detail']}")
    for f in facts["failures"]:
        not_done.append(f"- The job failed: {f}")
    if facts.get("finish") and facts["finish"].get("summary"):
        summary = _short(" ".join(str(facts["finish"]["summary"]).split()), 900)
    elif facts["limit"]:
        summary = f"The job stopped on its `{facts['limit']['limit']}` limit before it finished."
    else:
        summary = f"The job ended {facts['status'].replace('_', ' ')}."
    out = [
        "## Summary",
        summary,
        "",
        "## What was done",
        "\n".join(done) or "No role completed a brief.",
        "",
        "## What was not done and why",
        "\n".join(not_done) or "Nothing recorded.",
    ]
    if decisions:  # only a real question or request: never an empty section
        out += ["", "## Decisions for you", "\n".join(decisions)]
    return "\n".join(out)


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

NOTHING = re.compile(r"^(none|none recorded|nothing|no decisions?|n/?a)\b[^\n]{0,80}$", re.IGNORECASE)


def stopped_for_approval(f: dict[str, Any]) -> str:
    """The first words of the Summary when actions still wait for approval as the report is written: the
    job did not wait for them, so the work that needed them was not done. "" when nothing waits."""
    waiting, _ = _still_waiting(f, (f.get("outward") or {}).get("staged") or [])
    if not waiting:
        return ""
    what = ", ".join(f"`{ev.get('tool')}` ({ev.get('ref')})" for ev in waiting[:3]) + \
        (f" and {len(waiting) - 3} more" if len(waiting) > 3 else "")
    n = len(waiting)
    return (f"**Stopped for your approval:** {n} {'action' if n == 1 else 'actions'} ({what}) waited for your "
            "approval when the job ended. Jobs do not wait for approvals, so nothing that needed "
            f"{'it' if n == 1 else 'them'} was done. Approving now runs only the waiting "
            f"{'action' if n == 1 else 'actions'}, not the steps after; to finish the work, ask the Lead in a chat.")


def decision_lines(f: dict[str, Any], lead_text: str) -> str:
    """What waits on the user: the Lead's real decisions (a "None." is dropped), then, from the
    record, actions waiting for approval and skills waiting for review. "" when nothing does."""
    text = lead_text.strip()
    if NOTHING.match(text.strip("-* ").rstrip(".")):
        text = ""
    lines: list[str] = []
    waiting, _ = _still_waiting(f, (f.get("outward") or {}).get("staged") or [])
    if waiting:
        n = len(waiting)
        lines.append(f"- {n} {'action waits' if n == 1 else 'actions wait'} for your approval: see Approvals "
                     "(`ombro approvals`).")
    lines += [f"- A skill waits for your review: {_skill_words(ev)}. See it with "
              f"`ombro approvals show {ev.get('ref')}`, then approve or decline it." for ev in _skills_waiting(f)]
    return "\n\n".join(x for x in (text, "\n".join(lines)) if x)


def summary_of(report_text: str) -> str:
    """A report's Summary section: what the chat shows when the job ends."""
    m = re.search(r"^## Summary\s*\n(.*?)(?=^## |^---\s*$|^# |\Z)", report_text or "", re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    # Ombro's note that it wrote the report itself stays in the report, not in the chat's summary.
    return "\n".join(ln for ln in m.group(1).strip().splitlines()
                     if not ln.startswith("_This report was written by the harness")).strip()


STATUS_WORDS = {"completed": "Completed", "stopped_by_limit": "Stopped early", "failed": "Failed",
                "insufficient_brief": "Not started: the brief was insufficient", "running": "Still running"}
READER_SECTIONS = ("Summary", "What was done", "What was not done and why", "Decisions for you")


def _sections(narrative: str) -> tuple[dict[str, str], str]:
    """The Lead's account by heading, and anything under headings it was not asked for."""
    found: dict[str, list[str]] = {}
    extra: list[str] = []
    current: list[str] | None = None
    for line in _clean_narrative(narrative).splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if title in READER_SECTIONS:
                current = found.setdefault(title, [])
                continue
            current = extra
        if current is None:
            current = extra
        current.append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}, "\n".join(extra).strip()


def render(facts: dict[str, Any], narrative: str | None, note: str | None = None) -> str:
    """The report: what the user reads first (summary, deliverables, decisions, what was and
    was not done), then Ombro's own record of the job (specification 12)."""
    f = facts
    lim = f["limits"]
    sp = f["usage"]
    title = jobs.first_line(f["spec"], 100) or f"Job {f['job_id']}"
    out = [f"# {title}", ""]
    status = STATUS_WORDS.get(f["status"], f["status"])
    if f["limit"]:
        status += f": the `{f['limit']['limit']}` limit was reached"
    when = (f.get("finished_at") or "")[:10]
    out.append(f"**{status}.** {when} · job `{f['job_id']}` · {f['crew']}")
    for fail in f["failures"]:
        out.append(f"\n**Failed:** {fail}")
    out.append("")
    stopped = stopped_for_approval(f)

    parts, extra = _sections(narrative or mechanical_narrative(f))
    if not parts.get("Summary") and f.get("finish"):
        parts["Summary"] = _short(" ".join(str(f["finish"]["summary"] or "").split()), 900)
    if not parts.get("Summary"):  # the Lead wrote none: Ombro's own sentence on how it ended
        parts["Summary"] = _sections(mechanical_narrative(f))[0].get("Summary", "")
    out += ["## Summary", ""] + ([stopped, ""] if stopped else []) + \
        [parts.get("Summary") or "No summary was written.", ""]
    if not narrative:
        out += [f"_This report was written by the harness from the journal{': ' + note if note else ''}._", ""]

    out += ["## Deliverables", ""]
    if not f["deliverables"] and not f.get("missing_deliverables"):
        out.append("None.")
    for d in _without_copies(f["deliverables"]):
        out.append(f"- {d['name']}: {d['path']}")
    for d in f.get("missing_deliverables") or []:
        out.append(f"- {d['name']}: {d['missing']} was named but could not be found")
    other = f.get("other_files") or []
    if other:
        out += ["", "Other files in the workspace that no role named as a deliverable:"]
        out += [f"- {p}" for p in other[:20]]
        if len(other) > 20:
            out.append(f"- and {len(other) - 20} more")
    out += ["", f"Workspace: {f['workspace']}", ""]

    decisions = decision_lines(f, parts.get("Decisions for you") or "")
    if decisions:  # only when something waits on the user
        out += ["## Decisions for you", "", decisions, ""]
    out += ["## What was done", "", parts.get("What was done") or "None recorded.", ""]
    not_done = parts.get("What was not done and why") or ""
    if not_done and not NOTHING.match(not_done.strip("-* ").rstrip(".")):  # no "Nothing." section
        out += ["## What was not done and why", "", not_done, ""]
    if extra:
        out += ["## The Lead's other notes", "", extra, ""]

    out += ["---", "", "# Ombro's record", "",
            "What follows is Ombro's own record of the job, from its journal, for checking.", ""]
    out += ["## The job", ""]
    out.append(f"- **Status:** {f['status']}")
    out.append(f"- **{'Crews' if ', ' in f['crew'] else 'Crew'}:** {f['crew']}")
    out.append(f"- **Started:** {f['started_at']}")
    out.append(f"- **Finished:** {f['finished_at']}")
    if f["limit"]:
        out.append(f"- **Stopped by:** `{f['limit']['limit']}`: {f['limit']['detail']}")
    for fail in f["failures"]:
        out.append(f"- **Failed:** {fail}")
    if f["finish"] and not narrative:  # the Lead wrote no report: its closing note is all there is
        note_text = _line(f["finish"]["summary"], CLOSING_NOTE_CAP)
        if len(" ".join(str(f["finish"]["summary"] or "").split())) > CLOSING_NOTE_CAP:
            note_text += " (shortened; the full note is in the journal)"
        out.append(f"- **Lead's closing note ({f['finish']['outcome']}):** {note_text}")
    out += ["", "## What was asked", "", f"{title}. The full spec: {f['job_dir']}/spec.md"]
    if f["source"]:
        out += ["", f"_Source: {f['source']}_"]
    out.append("")

    out += ["## Briefs (harness record)", ""]
    if not f["briefs"]:
        out.append("No roles were briefed.")
    for i, b in enumerate(f["briefs"], 1):
        out.append(f"{i}. **{b['role']}** ({b['status']}): {_line(b['task'], 160)}")
        if b.get("result"):  # the role's own word, to check the Lead's account against
            out.append(f"   - {_line(b['result'].get('summary', ''), 200 if narrative else 500)}")
        if b.get("reason"):
            out.append(f"   - {_line(b['reason'], 500)}")
    if f.get("spec_roles"):
        chosen = f["spec_roles"]
        others = sorted({b["role"] for b in f["briefs"]} - set(chosen))
        unused = [r for r in chosen if r not in {b["role"] for b in f["briefs"]}]
        out += ["", f"The spec gave the work to {', '.join(chosen)}."]
        if others:
            out.append(f"Also briefed, outside the spec's roles: {', '.join(others)}. The Lead's account says why.")
        if unused:
            out.append(f"Not briefed, though the spec named them: {', '.join(unused)}.")
    if f.get("working_files"):
        out += ["", "Working files (scratch/, not deliverables): "
                + ", ".join(f"{d['path']} ({d['role']})" for d in f["working_files"])]
    if f.get("inline_notes"):
        out += ["", "Notes roles gave in place of a file:"]
        out += [f"- {d['name']} ({d['role']}): {_line(d['inline'], 300)}" for d in f["inline_notes"]]
    out.append("")

    out += ["## Outward actions", "", *outbox_lines(f), ""]
    if f.get("code_runs"):
        out += ["## Code", "", *code_lines(f), ""]
    if f.get("folders") or f.get("not_shared") or f.get("folders_left_out"):
        out += ["## Shared folders", "", *folder_lines(f), ""]
        if f.get("set_aside"):
            out += ["Kept rather than deleted or lost, in For deletion/ and Archive/ (only you empty them):", ""]
            out += [f"- {x}" for x in f["set_aside"]] + [""]
    if f.get("material_flags") or f.get("tainted_staged"):
        out += ["## Material", "", *material_lines(f), ""]
    if f.get("skills") or f.get("skill_reads"):
        out += ["## Skills", "", *skill_lines(f), ""]
    out += ["## Changes to notes.md", "",
            "Lessons the Lead kept in the crew's notes for future jobs: housekeeping, not results.", "",
            *all_notes_lines(f.get("notes")), ""]

    out += ["## Usage", ""]
    cap_tokens = lim.get("max_tokens")
    allowance = lim.get("allowance")
    out.append(
        f"- Tokens: {sp.get('counted_tokens', sp.get('total_tokens', 0)):,} of {format_tokens(cap_tokens)}"
        + (f" (allowance: {allowance})" if allowance and allowance != "custom" else "")
        + (f"; {sp.get('total_tokens', 0):,} sent and received ({sp.get('input_tokens', 0):,} in, "
           f"{sp.get('output_tokens', 0):,} out), with input the model had cached counted at a tenth"
           if sp.get("cached_input_tokens") else
           f" ({sp.get('input_tokens', 0):,} in, {sp.get('output_tokens', 0):,} out)")
    )
    if sp.get("estimated_calls"):
        out.append(f"  - {sp['estimated_calls']} model call(s) reported no token counts; estimates were used")
    if sp.get("reasoning_tokens"):
        out.append(f"  - of the {sp.get('output_tokens', 0):,} out, {sp['reasoning_tokens']:,} were the models' "
                   "thinking, where the provider reports it")
    out.append(f"- Model calls: {sp.get('model_calls', 0)}")
    isp = f.get("interview_usage") or {}
    if isp:
        out.append(
            f"- Task interview (separate from the job's limits): {isp.get('total_tokens', 0):,} tokens, "
            f"{isp.get('model_calls', 0)} model calls"
        )
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
        f"- Time: {format_duration(sp.get('elapsed_seconds', 0.0))} of {format_duration(lim.get('job_timeout', 0))}"
    )
    out.append(f"- Limit that stopped the job: {('`' + f['limit']['limit'] + '`') if f['limit'] else 'none'}")
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
        return ["None: nothing was done outside the job, and nothing is waiting for approval."]
    work = [ev for ev in done if ev.get("effect") in ("internal", "read-only")]
    sent = [ev for ev in done if ev not in work]
    lines: list[str] = []
    setting = f.get("outward_setting") or {}
    if setting:
        from ombro.outward import describe

        lines += [f"Permissions for this job: {describe(setting)}.", ""]
    if work:
        lines.append(f"Internal changes made during the job ({len(work)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in work]
        lines.append("")
    if sent:
        lines.append(f"Sent during the job, as your permissions allow ({len(sent)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in sent]
        lines.append("")
    staged, decided = _still_waiting(f, staged)
    now = f.get("outbox_now") or {}
    if decided:
        lines.append(f"Waited for approval, and decided by you while the job was going ({len(decided)}):")
        for ev in decided:
            what = DECIDED.get(now.get(ev.get("ref"), ""), now.get(ev.get("ref"), "decided"))
            lines.append(f"- **{ev.get('ref')}** `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}: {what}")
        lines.append("")
    if staged:
        lines.append(f"Waiting for your approval ({len(staged)}). Nothing here has happened:")
        for ev in staged:
            lines.append(f"- **{ev.get('ref')}** `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}")
            if ev.get("reason"):
                lines.append(f"  - Reason: {_line(ev['reason'], 200)}")
        if dups:
            lines.append(f"- ({len(dups)} identical repeat(s) were not added again.)")
        held = [ev for ev in ow.get("grouped") or [] if ev.get("ref") in {s.get("ref") for s in staged}]
        if held:
            lines.append(f"- ({len(held)} later call(s) on the same item were held with it, not added one by one, "
                         "and have not happened: " + ", ".join(sorted({f"`{ev.get('tool')}`" for ev in held})) + ".)")
        lines += ["", "See them with `ombro approvals`; approve or decline each by its reference, e.g. "
                  f"`ombro approvals show {staged[0].get('ref')}`.", ""]
    if uncertain:
        lines.append(f"Uncertain: sent during the job, but no answer came back, so it may or may not have "
                     f"happened ({len(uncertain)}). Check the service:")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_args_line(ev)}" for ev in uncertain]
        lines.append("")
    if errors:
        lines.append(f"Tried during the job, but the service reported an error ({len(errors)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')}): {_line(ev.get('note', ''), 200)}" for ev in errors]
        lines.append("")
    if refused:
        lines.append(f"Refused by tools.md ({len(refused)}):")
        lines += [f"- `{ev.get('tool')}` ({ev.get('role')})" for ev in refused]
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def material_lines(f: dict[str, Any]) -> list[str]:
    """Signs of injected instructions found in what roles read, and external calls staged
    because of it. Flags never stopped anything; they say what to check."""
    out = []
    flags = f.get("material_flags") or []
    if flags:
        out.append(f"Text that looked written to steer a model was found {len(flags)} time"
                   f"{'s' if len(flags) != 1 else ''}. Nothing was done because of it; check these sources:")
        out.append("")
        for x in flags[:30]:
            who = f" (read by {x['role']})" if x.get("role") and x["role"] != "lead" else ""
            out.append(f"- {_line(x['source'], 200)}{who}: {', '.join(x['signs'])}")
        if len(flags) > 30:
            out.append(f"- and {len(flags) - 30} more in the journal")
    if f.get("tainted_staged"):
        if out:
            out.append("")
        out.append("Held for your approval because the role had read material in that brief, though the job's permissions "
                   "would have allowed them: " + ", ".join(f["tainted_staged"]) + ".")
    return out


def code_lines(f: dict[str, Any]) -> list[str]:
    """Each script the job executed, from the harness's own record."""
    lines = [f"Scripts run with code.run, in the sandbox ({len(f['code_runs'])}). Each script and its output are "
             "kept in the job's code/ folder:"]
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
            lines.append(f"  - Backup: cloned before the job to {entry['backup']}. To restore a file, copy it "
                         "back from there.")
        elif entry.get("backup_note"):
            lines.append(f"  - Backup: {entry['backup_note']}.")
        ev = changes.get(str(name))
        if ev is None:
            lines.append("  - Changes: not recorded (the job ended before the folder could be compared); compare "
                         "it with the backup.")
        elif ev.get("too_large"):
            lines.append("  - Changes: the folder has too many files to compare before and after.")
        elif not ev.get("total"):
            lines.append("  - Changes: none.")
        else:
            c = ev.get("counts") or {}
            for label, key in (("Created", "created"), ("Changed", "changed"), ("Removed", "removed")):
                lines += ["  " + x for x in _change_list(label, ev.get(key) or [], int(c.get(key) or 0))]
        outside = (ev or {}).get("outside") or {}
        if any(outside.values()):
            names = [x for k in ("created", "changed", "removed") for x in outside.get(k) or []]
            lines.append("  - Changed while the job ran, but not by the job (by you or another program): "
                         + ", ".join(names) + ".")
    if f.get("placed"):
        lines += ["", f"Copied into shared folders with workspace.place ({len(f['placed'])}):"]
        lines += [f"- {ev.get('note')} ({ev.get('role')})" for ev in f["placed"]]
    for note in f.get("folders_left_out") or []:
        lines.append(f"- Left out: {note}")
    if f.get("not_shared"):
        lines += ["", "Refused because they are not in a folder shared with this job. Share the folder with "
                  "`ombro folder add <path>` if a later job should reach it:"]
        lines += [f"- {_line(p, 200)}" for p in f["not_shared"][:20]]
    if not lines:
        lines.append("None.")
    return lines


def _skill_words(ev: dict[str, Any]) -> str:
    what = "a new version of" if ev.get("change") == "update" else "a new skill,"
    words = f"{what} {ev.get('skill')}"
    if ev.get("for"):
        words += f", for {', '.join(ev['for'])}"
    if ev.get("adds"):
        words += f". What it adds: {str(ev['adds']).rstrip('.')}"
    if ev.get("jobs"):
        from ombro.jobs import job_name

        words += f". Earlier jobs of its kind: {', '.join(f'{job_name(j)} ({j})' for j in ev['jobs'])}"
    return words


def _skills_waiting(f: dict[str, Any]) -> list[dict[str, Any]]:
    now = f.get("outbox_now") or {}
    return [ev for ev in f.get("skills") or [] if ev.get("outcome") == "proposed"
            and now.get(str(ev.get("ref")), "pending") == "pending"]


def skill_lines(f: dict[str, Any]) -> list[str]:
    """Which skills each role read, and the Lead's proposal, from the harness's record."""
    lines: list[str] = []
    reads: dict[str, list[str]] = {}
    for ev in f.get("skill_reads") or []:
        label = str(ev.get("skill")) + (f" ({ev['file']})" if ev.get("file") else "")
        who = reads.setdefault(str(ev.get("role") or "?"), [])
        if label not in who:
            who.append(label)
    if reads:
        lines.append("Read during the job:")
        lines += [f"- {who}: {', '.join(names)}" for who, names in reads.items()]
    else:
        lines.append("No skill was read during the job.")
    now = f.get("outbox_now") or {}
    for ev in f.get("skills") or []:
        lines.append("")
        if ev.get("outcome") == "proposed":
            state = DECIDED.get(now.get(str(ev.get("ref")), ""), "waiting for you")
            lines.append(f"Proposed by the Lead: {_skill_words(ev)} (**{ev.get('ref')}**, {state}). "
                         f"Reason: {_line(ev.get('reason', ''), 200)}")
            if ev.get("flags"):
                lines.append(f"  - Signs of injected text in it: {', '.join(ev['flags'])}. Read it closely.")
            if ev.get("unknown_roles"):
                lines.append(f"  - Roles named that this job does not have: {', '.join(ev['unknown_roles'])}")
        else:
            lines.append(f"No skill proposed: {_line(ev.get('note', ''), 300)}")
    return lines


def all_notes_lines(events: list[dict[str, Any]] | None) -> list[str]:
    """The notes section: one part for each crew whose notes the Lead was asked to rewrite."""
    events = list(events or [])
    per_crew = [e for e in events if e.get("crew")]
    if not per_crew:
        return notes_lines(events[-1] if events else None)
    out: list[str] = []
    for ev in per_crew:
        crew = str(ev["crew"])
        note = str(ev.get("note") or "")
        if note.startswith(f"{crew}: "):
            ev = {**ev, "note": note[len(crew) + 2:]}
        out += [f"### Crew {crew}", "", *notes_lines(ev), ""]
    return out[:-1]


NOTES_LISTED = 5


def _lesson_changes(added: list[str], removed: list[str]) -> tuple[list[str], int, list[str]]:
    """Added and removed lessons, with a rewording (an added lesson sharing most of its words
    with a removed one) counted rather than listed twice: (new, number reworded, removed)."""

    def words(x: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", str(x).lower()) if len(w) > 2}

    def overlap(a: set[str], b: set[str]) -> float:
        return len(a & b) / max(1, min(len(a), len(b)))

    left = list(removed)
    new: list[str] = []
    reworded = 0
    for a in added:
        wa = words(a)
        scores = [overlap(wa, words(r)) for r in left]
        best = max(range(len(left)), key=scores.__getitem__, default=None)
        if best is not None and scores[best] >= 0.5:
            reworded += 1
            left.pop(best)
        else:
            new.append(a)
    return new, reworded, left


def notes_lines(ev: dict[str, Any] | None) -> list[str]:
    """What the report says about notes.md, from the harness's own record."""
    if not ev:
        return ["None."]
    outcome, note = ev.get("outcome"), ev.get("note", "")
    if outcome == "changed":
        added, removed = ev.get("added") or [], ev.get("removed") or []
        new, reworded, gone = _lesson_changes(added, removed)
        counts = [f"{n} {word}" for n, word in ((len(new), "new"), (reworded, "reworded"), (len(gone), "removed")) if n]
        lines = [f"The Lead rewrote its part (\"From jobs\"): {', '.join(counts) or 'no lesson changed'}. "
                 f"notes.md is now {ev.get('size', 0):,} of {ev.get('cap', 0):,} characters."]
        for label, items in (("Added", new), ("Removed", gone)):
            if items:
                lines += ["", f"{label}:"] + [f"- {_line(x.lstrip('-* '), 200)}" for x in items[:NOTES_LISTED]]
                if len(items) > NOTES_LISTED:
                    lines.append(f"- and {len(items) - NOTES_LISTED} more")
        if not added and not removed:
            lines += ["", "No lesson was added or removed; only the wording or order changed."]
        return lines
    if outcome == "kept":
        return [f"None. The Lead kept the notes as they were: {note}"]
    if outcome == "refused":
        return [f"Not changed: {note}. The previous notes are kept."]
    return [f"None. The notes were not rewritten: {note}."]


def write(job_dir: Path, text: str) -> Path:
    path = Path(job_dir) / "report.md"
    mdfile.write_atomic(path, text)
    return path


def write_mechanical_report(
    home: Home,
    job_dir: Path,
    status: str,
    failure: str | None = None,
    note: str = "the job's process ended before the Lead could write it",
) -> Path:
    """Report without the model, from the journal alone. Used when a process died
    or a job could not start."""
    facts = gather_facts(home, job_dir, status, failure=failure)
    path = write(job_dir, render(facts, None, note))
    Journal(Path(job_dir) / "journal.jsonl").write("harness", "report_written", "mechanical report from the journal")
    jobs.update_job(job_dir, status=status, finished_at=utc_now())
    from ombro import memory  # local import: memory depends on jobs, which imports this lazily

    memory.add_index_entry(home, job_dir)
    return path
