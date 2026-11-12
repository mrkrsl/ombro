"""Views: what an interface shows, as plain data.

Each function reads the home's files and returns dicts and lists that can be
printed as JSON (`--json` on the commands) or shown by the terminal and
graphical interfaces. Nothing here writes, and no key's value is ever
included, masked or otherwise.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ombro import journal, keys, mdfile, outbox, runs
from ombro.home import Home, crew_title
from ombro.settings import SetupError, load_limits, load_preferences


def version() -> str:
    try:
        from importlib.metadata import version as v

        return v("ombro")
    except Exception:
        return "0.0.0"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# The crew


def crews(home: Home) -> dict[str, Any]:
    """The Lead, then every crew with its roles, by title. A role that cannot be
    loaded is listed with its problem rather than left out."""
    from ombro.roles import RoleError, load_lead, load_role

    limits = load_limits(home)
    default = load_preferences(home).default_crew
    try:
        lead = load_lead(home, limits)
        lead_view = {"name": "lead", "title": lead.title, "model": lead.model, "path": str(home.lead_dir)}
    except SetupError as e:
        lead_view = {"name": "lead", "title": "Lead", "model": None, "path": str(home.lead_dir), "problem": str(e)}
    out = []
    names = home.crews()
    for crew in sorted(names, key=lambda c: (c != default, crew_title(home, c).lower())):
        meta: dict[str, Any] = {}
        path = home.crew_dir(crew) / "crew.md"
        try:
            meta = mdfile.read(path)[0] if path.is_file() else {}
        except mdfile.FrontMatterError:
            pass
        roles = []
        rdir = home.roles_dir(crew)
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir() and not p.name.startswith(".")) \
                if rdir.is_dir() else []:
            try:
                r = load_role(folder, limits)
                roles.append({"name": r.name, "title": r.title, "kind": r.kind, "model": r.model,
                              "tools": list(r.tools), "skills": list(r.skills), "description": r.description})
            except RoleError as e:
                from ombro.home import tidy_title

                roles.append({"name": folder.name, "title": tidy_title(folder.name), "problem": str(e)})
        out.append({"name": crew, "title": crew_title(home, crew), "description": str(meta.get("description") or ""),
                    "default": crew == default, "roles": roles})
    return {"lead": lead_view, "default_crew": default, "crews": out}


# ---------------------------------------------------------------------------
# Runs


def _is_active(meta: dict[str, Any]) -> bool:
    return meta.get("status") == "running" and not runs.is_dead(meta)


def run_row(run_dir: Path) -> dict[str, Any]:
    meta, _ = runs.read_run(run_dir)
    return {
        "run_id": run_dir.name,
        "crews": runs.run_crews(meta),
        "status": meta.get("status"),
        "active": _is_active(meta),
        "brief": meta.get("brief", ""),
        "source": meta.get("source", ""),
        "created_at": meta.get("created_at"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "limit_hit": meta.get("limit_hit"),
        "total_tokens": runs.usage(meta).get("total_tokens", 0),
        "waiting": outbox.waiting_count(run_dir),
        "seen_at": meta.get("seen_at"),
        "path": str(run_dir),
    }


def run_list(home: Home, crew: str | None = None) -> list[dict[str, Any]]:
    return [run_row(d) for d in runs.all_runs(home, crew)]


def status(run_dir: Path) -> dict[str, Any]:
    meta, _ = runs.read_run(run_dir)
    lim = meta.get("limits") or {}
    return {
        **run_row(run_dir),
        "pid": meta.get("pid") if meta.get("status") == "running" else None,
        "allowance": lim.get("allowance", "custom"),
        "usage": runs.usage(meta),
        "interview_usage": runs.interview_usage(meta),
        "limits": lim,
        "outbox": outbox.counts(run_dir),
        "outward": meta.get("outward") or {},
        "folders": meta.get("folders") or [],
    }


def deliverables(home: Home, run_dir: Path) -> list[dict[str, Any]]:
    """The run's files, from the roles' results and the workspace: name, the
    role that made it, its full path, a file:// link, and whether it is still there."""
    from ombro import report

    meta, _ = runs.read_run(run_dir)
    try:
        facts = report.gather_facts(home, run_dir, str(meta.get("status") or "running"))
    except (OSError, mdfile.FrontMatterError):
        return []
    out = []
    for d in facts.get("deliverables") or []:
        p = Path(d["path"])
        out.append({"name": d.get("name") or p.name, "role": d.get("role", ""), "path": str(p),
                    "url": p.as_uri(), "exists": p.is_file(), "kind": "deliverable"})
    for path in facts.get("other_files") or []:
        p = Path(path)
        out.append({"name": p.name, "role": "", "path": str(p), "url": p.as_uri(), "exists": p.is_file(),
                    "kind": "other"})
    return out


def report(home: Home, run_dir: Path) -> dict[str, Any]:
    path = run_dir / "report.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    return {**run_row(run_dir), "report_path": str(path), "report": text,
            "deliverables": deliverables(home, run_dir), "workspace": str(run_dir / "workspace")}


def journal_events(run_dir: Path, include_model_calls: bool = False) -> list[dict[str, Any]]:
    return [e for e in journal.read(run_dir / "journal.jsonl")
            if include_model_calls or e.get("action") != "model_call"]


# ---------------------------------------------------------------------------
# Settings and the rest


def key_list(home: Home) -> dict[str, Any]:
    """Which keys are set and what uses them. Never a value, not even masked."""
    stored = keys.read_env(home)
    uses = keys.key_uses(home)
    rows = []
    for name in sorted(set(stored) | set(uses)):
        source = "keys.env" if stored.get(name) else "environment" if os.environ.get(name) else None
        rows.append({"name": name, "set": source is not None, "source": source, "used_by": uses.get(name, [])})
    sign_ins = []
    try:
        from ombro import oauth
        from ombro.settings import load_tool_settings

        for n, c in load_tool_settings(home).servers.items():
            if c.get("url"):
                sign_ins.append({"server": n, "status": oauth.status(home, n)})
    except Exception:
        pass
    return {"keys": rows, "sign_ins": sign_ins, "file": str(home.env)}


def schedule_list(home: Home, crew: str | None = None) -> list[dict[str, Any]]:
    from ombro import cron as cronmod
    from ombro import schedules

    by = schedules.scheduled_runs(home)
    now = datetime.now()
    out = []
    for s in schedules.all_schedules(home, crew):
        nxt = None
        if s.cron and s.enabled:
            n = cronmod.next_slots(s.cron, now, 1)
            nxt = n[0].isoformat(timespec="minutes") if n else None
        recent = by.get(s.name, [])
        last = None
        if recent:
            rd, m = recent[-1]
            last = {"run_id": rd.name, "due": m.get("due"), "status": m.get("status"),
                    "waiting": outbox.waiting_count(rd)}
        out.append({"name": s.name, "enabled": s.enabled, "cron": s.cron_text, "spec": s.spec, "crews": s.crews,
                    "next": nxt, "last_run": last, "problem": s.error, "path": str(s.path)})
    return out


def spec_list(home: Home, crew: str | None = None) -> list[dict[str, Any]]:
    from ombro.cli import saved_specs

    out = []
    for name, path in saved_specs(home, crew):
        try:
            meta, body = mdfile.read(path)
        except mdfile.FrontMatterError:
            meta, body = {}, ""
        raw = meta.get("crews") or []
        out.append({"name": name, "path": str(path), "modified": _when(path.stat().st_mtime),
                    "crews": [str(c) for c in raw] if isinstance(raw, list) else [str(raw)],
                    "title": runs.first_line(body, 120)})
    return out


def folder_list(home: Home) -> dict[str, Any]:
    from ombro import folders

    grants, declined = folders.load(home)
    rows = []
    for g in grants.values():
        try:
            folders.check_place(g.path, home)
            problem = None
        except folders.FolderError as e:
            problem = str(e)
        rows.append({"name": g.name, "path": str(g.path), "access": g.access,
                     "backup": folders.backup_hint(g.path, home) if g.access == "write" else None,
                     "problem": problem})
    return {"folders": rows, "declined": [str(d) for d in declined], "file": str(home.folders)}


def limit_values(home: Home) -> dict[str, Any]:
    return load_limits(home).to_dict()


def preferences(home: Home) -> dict[str, Any]:
    p = load_preferences(home)
    outward = p.outward
    preset = outward.get("preset") if isinstance(outward, dict) else outward
    return {"name": p.name, "theme": p.theme, "default_crew": p.default_crew, "editor": p.editor,
            "outward": str(preset or "balanced"), "instructions_set": bool(p.instructions.strip()),
            "files": {"config": str(home.preferences_config), "instructions": str(home.instructions)}}


# ---------------------------------------------------------------------------
# The summary an interface opens with


def summary(home: Home) -> dict[str, Any]:
    """Who you are, the crew, and what is going on: the opening header's data,
    and `ombro summary`."""
    from ombro import schedules

    team = crews(home)
    run_rows = run_list(home)
    try:
        enabled = sum(1 for s in schedules.all_schedules(home) if s.enabled and not s.error)
    except (SetupError, OSError):
        enabled = 0
    try:
        model = load_lead_model(home)
    except SetupError:
        model = None
    prefs = preferences(home)
    return {
        "name": prefs["name"],
        "version": version(),
        "model": model,
        "outward": prefs["outward"],
        "lead": team["lead"],
        "crews": team["crews"],
        "counts": {
            "crews": len(team["crews"]),
            "crews_ready": sum(1 for c in team["crews"] if any("problem" not in r for r in c["roles"])),
            "active_runs": sum(1 for r in run_rows if r["active"]),
            "scheduled_runs": enabled,
            "waiting": sum(r["waiting"] for r in run_rows),
        },
    }


def load_lead_model(home: Home) -> str | None:
    """The model the Lead talks with, as models.md names it (e.g. deepseek-chat)."""
    from ombro.roles import load_lead
    from ombro.settings import load_models

    lead = load_lead(home, load_limits(home))
    models = load_models(home)
    spec = models.get(lead.model) or models.get("default")
    return spec.model if spec else None
