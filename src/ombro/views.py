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

from ombro import journal, keys, mdfile, outbox, jobs
from ombro import viewtypes as vt
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


def crews(home: Home) -> vt.Crews:
    """The Lead, then every crew with its roles, by title. A role that cannot be
    loaded is listed with its problem rather than left out."""
    from ombro.roles import RoleError, load_lead, load_role

    limits = load_limits(home)
    default = load_preferences(home).default_crew
    try:
        lead = load_lead(home, limits)
        lead_view: vt.LeadView = {"name": "lead", "title": lead.title, "model": lead.model,
                                  "path": str(home.lead_dir)}
    except SetupError as e:
        lead_view = {"name": "lead", "title": "Lead", "model": None, "path": str(home.lead_dir), "problem": str(e)}
    out: list[vt.CrewRow] = []
    names = home.crews()
    for crew in sorted(names, key=lambda c: (c != default, crew_title(home, c).lower())):
        meta: dict[str, Any] = {}
        path = home.crew_dir(crew) / "crew.md"
        try:
            meta = mdfile.read(path)[0] if path.is_file() else {}
        except mdfile.FrontMatterError:
            pass
        roles: list[vt.RoleRow] = []
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
# Jobs


def _is_active(meta: dict[str, Any]) -> bool:
    return meta.get("status") == "running" and not jobs.is_dead(meta)


def job_row(job_dir: Path) -> vt.JobRow:
    meta, _ = jobs.read_job(job_dir)
    return {
        "job_id": job_dir.name,
        "crews": jobs.job_crews(meta),
        "status": meta.get("status"),
        "active": _is_active(meta),
        "brief": meta.get("brief", ""),
        "source": meta.get("source", ""),
        "created_at": meta.get("created_at"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "limit_hit": meta.get("limit_hit"),
        "total_tokens": jobs.usage(meta).get("total_tokens", 0),
        "waiting": outbox.waiting_count(job_dir),
        "seen_at": meta.get("seen_at"),
        "path": str(job_dir),
        "name": jobs.job_name(job_dir.name),
        "chat": meta.get("chat"),
    }


def job_list(home: Home, crew: str | None = None) -> list[vt.JobRow]:
    return [job_row(d) for d in jobs.all_jobs(home, crew)]


def status(job_dir: Path) -> vt.JobStatus:
    meta, _ = jobs.read_job(job_dir)
    lim = meta.get("limits") or {}
    return {
        **job_row(job_dir),
        "pid": meta.get("pid") if meta.get("status") == "running" else None,
        "allowance": lim.get("allowance", "custom"),
        "usage": jobs.usage(meta),
        "interview_usage": jobs.interview_usage(meta),
        "limits": lim,
        "outbox": outbox.counts(job_dir),
        "outward": meta.get("outward") or {},
        "folders": meta.get("folders") or [],
        "tool_names": _tool_names(meta.get("tools") or {}),
    }


def _tool_names(tools: dict[str, Any]) -> list[str]:
    """The tools a job had, by name: built-in tools set up for it, then its MCP servers. Never their settings."""
    names = [str(t) for t in (tools.get("tools") or {})] if isinstance(tools.get("tools"), dict) else []
    servers = tools.get("servers") or {}
    return names + ([str(s) for s in servers] if isinstance(servers, dict) else [])


def deliverables(home: Home, job_dir: Path) -> list[vt.Deliverable]:
    """The job's files, from the roles' results and the workspace: name, the
    role that made it, its full path, a file:// link, and whether it is still there."""
    from ombro import report

    meta, _ = jobs.read_job(job_dir)
    try:
        facts = report.gather_facts(home, job_dir, str(meta.get("status") or "running"))
    except (OSError, mdfile.FrontMatterError):
        return []
    out: list[vt.Deliverable] = []
    for d in facts.get("deliverables") or []:
        p = Path(d["path"])
        out.append({"name": d.get("name") or p.name, "role": d.get("role", ""), "path": str(p),
                    "url": p.as_uri(), "exists": p.is_file(), "kind": "deliverable"})
    named = {d["path"] for d in out}
    for name, ev in (facts.get("folder_changes") or {}).items():
        root = Path(str(ev.get("path") or ""))
        for kind in ("created", "changed"):
            for rel in ev.get(kind) or []:
                if rel.split("/", 1)[0] in ("Archive", "For deletion") or not str(root):
                    continue  # Ombro's own keeping, not the job's output
                p = root / rel
                if str(p) in named:
                    continue
                out.append({"name": f"shared/{name}/{rel}", "role": "", "path": str(p), "url": p.as_uri(),
                            "exists": p.is_file(), "kind": kind})
    for path in facts.get("other_files") or []:
        p = Path(path)
        out.append({"name": p.name, "role": "", "path": str(p), "url": p.as_uri(), "exists": p.is_file(),
                    "kind": "other"})
    return out


def chat_list(home: Home) -> list[vt.ChatRow]:
    """Saved chats, most recently used first: title (never the file name), when, and where.
    A chat from before titles is shown by its date."""
    folder = home.root / "chats"
    out: list[vt.ChatRow] = []
    for p in folder.glob("*.md") if folder.is_dir() else []:
        try:
            meta, _ = mdfile.read(p)
        except (OSError, mdfile.FrontMatterError):
            continue
        updated = str(meta.get("updated_at") or meta.get("started_at") or "")
        title = str(meta.get("title") or "").strip()
        if not title:
            m = jobs.WHEN.search(p.stem)
            title = f"Chat of {m.group(1)[6:8]}/{m.group(1)[4:6]}/{m.group(1)[:4]}" if m else p.stem
        stamp = datetime.fromtimestamp(p.stat().st_mtime)
        out.append({"name": p.stem, "title": title, "path": str(p), "updated_at": updated,
                    "when": f"{stamp.day} {stamp:%b}, {stamp:%H:%M}", "id": meta.get("id"),
                    "jobs": [str(j) for j in meta.get("jobs") or []]})
    out.sort(key=lambda c: (c["updated_at"], c["name"]), reverse=True)
    return out


def report(home: Home, job_dir: Path) -> vt.Report:
    path = job_dir / "report.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    return {**job_row(job_dir), "report_path": str(path), "report": text,
            "deliverables": deliverables(home, job_dir), "workspace": str(job_dir / "workspace")}


def journal_events(job_dir: Path, include_model_calls: bool = False) -> list[dict[str, Any]]:
    return [e for e in journal.read(job_dir / "journal.jsonl")
            if include_model_calls or e.get("action") != "model_call"]


# ---------------------------------------------------------------------------
# Settings and the rest


def key_list(home: Home) -> vt.KeyList:
    """Which keys are set and what uses them. Never a value, not even masked."""
    stored = keys.read_env(home)
    uses = keys.key_uses(home)
    rows: list[vt.KeyRow] = []
    for name in sorted(set(stored) | set(uses)):
        source = "keys.env" if stored.get(name) else "environment" if os.environ.get(name) else None
        rows.append({"name": name, "set": source is not None, "source": source, "used_by": uses.get(name, [])})
    sign_ins: list[vt.SignIn] = []
    try:
        from ombro import oauth
        from ombro.settings import load_tool_settings

        for n, c in load_tool_settings(home).servers.items():
            if c.get("url"):
                sign_ins.append({"server": n, "status": oauth.status(home, n)})
    except Exception:
        pass
    return {"keys": rows, "sign_ins": sign_ins, "file": str(home.env)}


def schedule_list(home: Home, crew: str | None = None) -> list[vt.ScheduleRow]:
    from ombro import cron as cronmod
    from ombro import schedules

    by = schedules.scheduled_jobs(home)
    now = datetime.now()
    out: list[vt.ScheduleRow] = []
    for s in schedules.all_schedules(home, crew):
        nxt = None
        if s.cron and s.enabled:
            n = cronmod.next_slots(s.cron, now, 1)
            nxt = n[0].isoformat(timespec="minutes") if n else None
        recent = by.get(s.name, [])
        last: vt.LastJob | None = None
        if recent:
            rd, m = recent[-1]
            last = {"job_id": rd.name, "due": m.get("due"), "status": m.get("status"),
                    "waiting": outbox.waiting_count(rd)}
        try:
            spec_title = jobs.first_line(mdfile.read(s.spec_path(home))[1], 120) if s.spec else ""
        except (SetupError, OSError, mdfile.FrontMatterError):
            spec_title = ""
        out.append({"name": s.name, "enabled": s.enabled, "cron": s.cron_text, "spec": s.spec, "crews": s.crews,
                    "when": cronmod.describe(s.cron) if s.cron else s.cron_text, "spec_title": spec_title,
                    "next": nxt, "last_job": last, "problem": s.error, "path": str(s.path)})
    return out


def spec_list(home: Home, crew: str | None = None) -> list[vt.SpecRow]:
    from ombro.cli import saved_specs

    out: list[vt.SpecRow] = []
    for name, path in saved_specs(home, crew):
        try:
            meta, body = mdfile.read(path)
        except mdfile.FrontMatterError:
            meta, body = {}, ""
        raw = meta.get("crews") or []
        out.append({"name": name, "path": str(path), "modified": _when(path.stat().st_mtime),
                    "crews": [str(c) for c in raw] if isinstance(raw, list) else [str(raw)],
                    "title": jobs.first_line(body, 120)})
    return out


def folder_list(home: Home) -> vt.FolderList:
    from ombro import folders

    grants, declined = folders.load(home)
    rows: list[vt.FolderRow] = []
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


def tool_list(home: Home, settings=None) -> vt.ToolList:
    """The built-in tools with their provider and call limit, the MCP servers by address or
    command (never their secret headers or environment), and whether code.run can work here."""
    from ombro import folders, sandbox
    from ombro.settings import PROVIDER_TOOLS, load_tool_settings

    settings = settings or load_tool_settings(home)
    native: list[vt.NativeTool] = []
    for name in ("web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list", "workspace.place",
                 "code.run"):
        cfg = settings.tools.get(name, {})
        native.append({"tool": name, "provider": settings.provider_for(name) if name in PROVIDER_TOOLS else None,
                       "max_calls": cfg.get("max_calls") or (10 if name == "code.run" else None)})
    servers: list[vt.ServerRow] = [{"server": name, "command": cfg.get("command"), "args": cfg.get("args") or [], "url": cfg.get("url")}
               for name, cfg in settings.servers.items()]
    return {"native": native, "servers": servers, "sandbox": sandbox.unavailable_reason(),
            "folders": folders.granted_names(home)}


def model_list(home: Home) -> vt.ModelList:
    """Each model connection with its models, the default, and who uses which. Keys by name only."""
    from ombro import models

    connections: list[vt.Connection] = models.connections(home)  # type: ignore[assignment]
    return {"connections": connections, "default": load_lead_model(home)}


def approval_row(a: outbox.Action, home: Home | None = None) -> vt.ApprovalRow:
    d = a.as_dict()
    try:
        crews = jobs.job_crews(jobs.read_job(a.job_dir)[0])
    except (OSError, mdfile.FrontMatterError):
        crews = []
    return {"ref": a.ref, "crews": [crew_title(home, c) if home else c for c in crews], "long_ref": a.long_ref, "job_id": a.job_id, "job_name": jobs.job_name(a.job_id),
            "status": a.status, "tool": a.tool, "summary": a.summary(120), "role": str(d.get("role") or ""),
            "reason": str(d.get("reason") or ""), "class": str(d.get("class") or ""), "edited": a.edited,
            "arguments": a.arguments}


def approval_list(home: Home) -> list[vt.ApprovalRow]:
    """What waits for the user's yes, across every job."""
    return [approval_row(a, home) for a in outbox.waiting(home)]


def approval(home: Home, ref: str) -> vt.ApprovalDetail:
    """One approval in full: what it will send and where, why, and the job it came from."""
    a = outbox.find(home, ref)
    skill = outbox.skill_view(home, a) if a.tool == outbox.SKILL_TOOL else None
    return {**approval_row(a, home), "skill": skill}


def role(home: Home, crew: str, name: str) -> vt.RoleDetail:
    """One role: its settings and its soul, as written."""
    from ombro.roles import load_lead, load_role

    limits = load_limits(home)
    r = load_lead(home, limits) if name == "lead" else load_role(home.roles_dir(crew) / name, limits)
    return {"name": r.name, "crew": crew if name != "lead" else "", "title": r.title, "kind": r.kind,
            "model": r.model, "description": r.description, "tools": list(r.tools), "skills": list(r.skills),
            "all_skills": r.all_skills, "soul": r.soul, "path": str(r.path)}


def chat(home: Home, name: str) -> vt.ChatDetail:
    """One saved chat: its title, the jobs it started, and its turns (yours and the Lead's)."""
    from ombro.chat import parse_entries, turns

    path = home.root / "chats" / f"{name}.md"
    if "/" in name or not path.is_file():
        raise LookupError(f"no chat called {name!r}")
    _, body = mdfile.read(path)
    row = next((c for c in chat_list(home) if c["name"] == name), None)
    if row is None:
        raise LookupError(f"the chat {name!r} can't be read")
    return {**row, "turns": [{"who": "you" if who == "user" else "lead", "text": text}
                             for who, text in turns(parse_entries(body))]}


def limit_values(home: Home) -> dict[str, Any]:
    return load_limits(home).to_dict()


def preferences(home: Home) -> vt.Preferences:
    p = load_preferences(home)
    outward = p.outward
    preset = outward.get("preset") if isinstance(outward, dict) else outward
    return {"name": p.name, "theme": p.theme, "default_crew": p.default_crew,
            "default_crew_title": crew_title(home, p.default_crew), "editor": p.editor,
            "outward": str(preset or "balanced"), "instructions_set": bool(p.instructions.strip()),
            "background_feed": p.background_feed, "notifications": p.notifications,
            "files": {"config": str(home.preferences_config), "instructions": str(home.instructions)}}


# ---------------------------------------------------------------------------
# The summary an interface opens with


def summary(home: Home) -> vt.Summary:
    """Who you are, the crew, and what is going on: the opening header's data,
    and `ombro summary`."""
    from ombro import schedules

    team = crews(home)
    job_rows = job_list(home)
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
            "active_jobs": sum(1 for r in job_rows if r["active"]),
            "scheduled_jobs": enabled,
            "waiting": sum(r["waiting"] for r in job_rows),
            "unseen_jobs": len(unseen := [r for r in job_rows if _unseen(r)]),
        },
        "unseen": [{"job_id": r["job_id"], "status": r["status"], "brief": r["brief"],
                    "finished_at": r["finished_at"], "waiting": r["waiting"]} for r in unseen],
    }


def _unseen(row: vt.JobRow) -> bool:
    return row["status"] != "running" and not row["seen_at"] and Path(row["path"], "report.md").is_file()


def since_last(home: Home) -> vt.SinceLast:
    """What an interface says on opening: jobs finished that you have not seen, and
    actions waiting. Showing it does not mark anything seen; showing a job's
    report or its notice does."""
    s = summary(home)
    return {"finished": s["counts"]["unseen_jobs"], "waiting": s["counts"]["waiting"], "jobs": s["unseen"]}


def load_lead_model(home: Home) -> str | None:
    """The model the Lead talks with, as models.md names it (e.g. deepseek-chat)."""
    from ombro.roles import load_lead
    from ombro.settings import load_models

    lead = load_lead(home, load_limits(home))
    models = load_models(home)
    spec = models.get(lead.model) or models.get("default")
    return spec.model if spec else None
