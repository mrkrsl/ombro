"""The terminal interface's slash commands beyond chat. The commands that act are the core's
flows (ombro.flows); this adds how the terminal draws lists, reports and notices, with
colour and links. They run on a worker thread and reach the screen only through the app's bridge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.style import Style
from rich.text import Text

from ombro import jobs, views
from ombro.flows import Flows, _n, spec_view  # noqa: F401 - spec_view is used from here by tests
from ombro.session import ended_line, job_ended, progress_line  # noqa: F401 - kept importable from here
from ombro.ui import JobEnded

if TYPE_CHECKING:  # pragma: no cover
    from ombro.tui.app import OmbroApp


RECORD_MARK = "\n# Ombro's record"


def report_markdown(r: dict[str, Any], full: bool = False) -> str:
    """A report as the terminal shows it: the part you read (summary, deliverables, decisions,
    what was and was not done), then every file the job made as a link, and links to the full
    report and to Ombro's record. `full` adds the record itself."""
    from urllib.parse import quote

    text = r["report"].rstrip()
    head, _, record = text.partition("\n---\n" + RECORD_MARK.lstrip("\n"))
    if not record:
        head, _, record = text.partition(RECORD_MARK)
    out = [head.rstrip()]
    made = [d for d in r["deliverables"] if d["kind"] != "other"]
    if made:
        out += ["", "**Open what the job made:**", ""]
        for d in made:
            label = d["name"] + ("" if d["kind"] == "deliverable" else f" ({d['kind']} in a shared folder)")
            out.append(f"- [{label}]({d['url']})" + ("" if d["exists"] else " (no longer there)"))
    report_url = Path(r["report_path"]).as_uri()
    rid = quote(str(r["job_id"]))
    out += ["", f"[Open the full report]({report_url}) · "
                + (f"[Show Ombro's record](ombro:report/{rid}/full)" if not full else "Ombro's record is below.")]
    if full and record:
        out += ["", "---", "", "# Ombro's record" + record]
    return "\n".join(out) + "\n"


class Commands(Flows):
    def __init__(self, app: "OmbroApp"):
        super().__init__(app.session)
        self.app = app

    # -- output -----------------------------------------------------------------

    def rich(self, text: Text, kind: str = "note") -> None:
        self.app.call_from_thread(self.app.add_text, text, kind)

    def link(self, out: Text, path: str, label: str | None = None) -> None:
        p = self.app.colours
        out.append(label or path, Style(color=p.text, underline=True, link=Path(path).as_uri()))

    def start_job(self, spec: str, roles: list[str] | None = None) -> str | None:
        return self.app.session.start_job(spec, roles)

    # -- lists, drawn ---------------------------------------------------------------

    def list_approvals(self, items: list) -> None:
        p = self.app.colours
        out = Text(f"{_n(len(items), 'action')} waiting for your decision:\n", style=p.muted)
        for a in items:
            out.append(f"  {a.ref}  ", style=f"bold {p.text}")
            out.append(f"{a.status:<9} {a.tool}  ", style=p.muted)
            out.append(a.summary(70) + "\n", style=p.text)
        out.append("/approvals show <ref> for one in full; /approvals approve or decline <ref>...", style=p.muted)
        self.rich(out)

    def list_skills(self, view: dict[str, Any]) -> None:
        p = self.app.colours
        out = Text()
        out.append(f"Approved ({len(view['skills'])})\n", style=f"bold {p.text}")
        if not view["skills"]:
            out.append("  none yet\n", style=p.muted)
        for r in view["skills"]:
            out.append(f"  {r['name']}  ", style=f"bold {p.text}")
            out.append(f"{r['description']}\n", style=p.text)
            origin = "yours" if r["source"] == "you" else f"from {r['source']}"
            out.append(f"    {origin}; used by {', '.join(r['used_by']) or 'no role by name'}\n",
                       style=p.muted)
        if view["all_skills"]:
            out.append(f"  Every skill is used by {', '.join(view['all_skills'])}\n", style=p.muted)
        out.append(f"\nWaiting for your approval ({len(view['waiting'])})\n", style=f"bold {p.text}")
        if not view["waiting"]:
            out.append("  none\n", style=p.muted)
        for w in view["waiting"]:
            what = "a new version of" if w["change"] == "update" else "new:"
            out.append(f"  {w['ref']}  ", style=f"bold {p.text}")
            out.append(f"{what} {w['name']}" + (f", for {', '.join(w['for'])}" if w["for"] else "")
                       + "\n", style=p.text)
            if w["flags"]:
                out.append(f"    signs of injected text: {', '.join(w['flags'])}\n", style=p.muted)
        out.append("\n/skills show <name>; /skills give <skill> <crew/role>; /approvals show <ref>, then "
                   "/approvals approve or decline <ref>", style=p.muted)
        self.rich(out)

    def list_crews(self, team: dict[str, Any]) -> None:
        p = self.app.colours
        out = Text(team["lead"]["title"] + "\n", style=f"bold {p.strong}")
        for c in team["crews"]:
            out.append(f"└ {c['title']}", style=p.text)
            out.append(f"  ({c['name']}{', default' if c['default'] else ''}) {c['description']}\n", style=p.muted)
            for r in c["roles"]:
                out.append(f"   └ {r['title']}", style=p.text)
                detail = r.get("problem") or f"{r['kind']}, {r['model']}: {r['description']}"
                out.append(f"  {detail[:90]}\n", style=p.muted)
        out.append("Add a role with /role add; a crew with /crew add <name> [title].", style=p.muted)
        self.rich(out)

    # -- jobs (10d) ---------------------------------------------------------------

    def cmd_jobs(self, words: list[str], rest: str) -> None:
        rows = views.job_list(self.home)
        if not rows:
            self.say("No jobs yet. Brief a task with /brief.")
            return
        p = self.app.colours
        out = Text("Jobs, newest last:\n", style=p.muted)
        for r in rows[-12:]:
            waiting = f"  {r['waiting']} waiting" if r["waiting"] else ""
            unseen = "  new" if not r["seen_at"] and r["status"] != "running" else ""
            out.append(f"{r['job_id']}  {r['status']:<18} {r['total_tokens']:>9,} tokens{waiting}{unseen}\n",
                       style=p.text)
            out.append(f"    {r['brief'][:90]}\n", style=p.muted)
        out.append("See one with /report <job_id>.", style=p.muted)
        self.rich(out)

    def cmd_report(self, words: list[str], rest: str) -> None:
        full = bool(words) and words[-1].lower() == "full"
        if full:
            words = words[:-1]
        job_dir = self._find_job(words)
        if job_dir is None:
            return
        r = views.report(self.home, job_dir)
        if r["report"] is None:
            self.say(f"Job {job_dir.name} has no report yet (status {r['status']}).")
            return
        self.app.call_from_thread(self.app.add_markdown, report_markdown(r, full), "report")
        jobs.mark_seen(job_dir)

    def report_text(self, r: dict[str, Any]) -> Text:
        p = self.app.colours
        out = Text()
        out.append(r["report"].rstrip() + "\n", style=p.text)
        files = [d for d in r["deliverables"] if d["kind"] == "deliverable"]
        if files:
            out.append("\nDeliverables (open with a click where your terminal supports it):\n", style=p.muted)
            for d in files:
                out.append(f"  {d['name']}: ", style=p.muted)
                self.link(out, d["path"])
                out.append("\n")
        out.append("\nWorkspace: ", style=p.muted)
        self.link(out, r["workspace"])
        return out

    def cmd_open(self, words: list[str], rest: str) -> None:
        from ombro.cli import _launch

        job_dir = self._find_job(words)
        if job_dir is None:
            return
        folder = job_dir / "workspace"
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            self.fail(f"{folder} is not a real folder, so Ombro will not open it.")
            return
        folder.mkdir(exist_ok=True)
        if not _launch(folder):
            out = Text("No file manager could be opened here. The folder: ")
            self.link(out, str(folder))
            self.rich(out)
            return
        self.say(f"Opened {folder}.")



# ---------------------------------------------------------------------------
# Notices


def notice_text(app: "OmbroApp", job_id: str) -> Text:
    """What the interface says when a job ends, for a job by its id."""
    return render_notice(job_ended(app.home, job_id), app)


def render_notice(e: JobEnded, app: "OmbroApp") -> Text:
    """A job's notice as the terminal draws it: outcome, deliverables as links, actions
    waiting, and a click to open the report. Written by Ombro, not the model."""
    p = app.colours
    out = Text()
    if e.status == "unknown":
        return Text(e.text, style=p.text)
    out.append(e.text, style=f"bold {p.text}")
    out.append(f"\n{e.brief}\n", style=p.muted)
    if e.summary:  # the Lead's short paragraph
        out.append(" ".join(e.summary.split()) + "\n", style=p.text)
    if e.files:
        out.append("Here's what it made:\n", style=p.text)
    for f in e.files:
        out.append(f"  {f.name}: ", style=p.muted)
        out.append(f.path, Style(color=p.text, underline=True, link=f.url))
        out.append("\n")
    if e.waiting:
        n = e.waiting
        out.append(f"{_n(n, 'action')} {'is' if n == 1 else 'are'} waiting for approval: /approvals\n", style=p.text)
    out.append("Read the report", Style(color=p.text, underline=True, bold=True,
                                         meta={"@click": f"app.run_command({'/report ' + e.job_id!r})"}))
    out.append(f"  (or type /report {e.job_id})", style=p.muted)
    if e.offers:  # one that didn't finish: the Lead's offer, and a click for each way to carry on
        out.append(f"\n{e.again_text}\n", style=p.text)
        for i, o in enumerate(e.offers):
            out.append("  " if i else "", style=p.muted)
            out.append(o.label, Style(color=p.text, underline=True, bold=True,
                                      meta={"@click": f"app.run_command({o.command!r})"}))
        out.append(f"  (or type {e.offers[0].command})", style=p.muted)
    return out
