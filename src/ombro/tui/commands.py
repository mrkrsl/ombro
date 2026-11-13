"""The terminal interface's slash commands beyond chat: each does what its command
in specification 13 does, reading views and calling the same core functions.
They run on a worker thread and reach the screen only through the app's bridge."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from rich.style import Style
from rich.text import Text

from ombro import outbox, runs, views
from ombro.ui import Failure, Field, Note, Option

if TYPE_CHECKING:  # pragma: no cover
    from ombro.tui.app import OmbroApp


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


class Commands:
    def __init__(self, app: "OmbroApp"):
        self.app = app
        self.home = app.home
        self.ui = app.bridge

    # -- output -----------------------------------------------------------------

    def say(self, text: str, **kw: Any) -> None:
        self.ui.show(Note(text, **kw))

    def fail(self, text: str) -> None:
        self.ui.show(Failure(text))

    def rich(self, text: Text, kind: str = "note") -> None:
        self.app.call_from_thread(self.app.add_text, text, kind)

    def link(self, out: Text, path: str, label: str | None = None) -> None:
        p = self.app.colours
        out.append(label or path, Style(color=p.text, underline=True, link=Path(path).as_uri()))

    def usage(self, text: str) -> None:
        self.say(f"Use it like this: {text}")

    # -- dispatch ---------------------------------------------------------------

    def run(self, word: str, rest: str) -> None:
        handler: Callable[[list[str], str], None] | None = getattr(self, "cmd_" + word.lstrip("/"), None)
        if handler is None:
            self.say(f"Unknown command {word}. /help lists them.")
            return
        handler(rest.split(), rest.strip())

    # -- set-up and roles (10c) --------------------------------------------------

    def setup(self) -> None:
        from ombro.init_cmd import run_init

        kw = {"client_factory": self.app.init_client_factory} if self.app.init_client_factory else {}
        run_init(self.home, ui=self.ui, next_steps=False, **kw)

    def cmd_role(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        if action == "list":
            self.cmd_crew(["list"], "")
            return
        if action == "add":
            from ombro.interviews import RolePurpose, start
            from ombro.roles import folder_name
            from ombro.settings import load_limits, load_preferences

            title = " ".join(words[1:]) or None
            crew = load_preferences(self.home).default_crew
            m = re.search(r"--crew\s+(\S+)", rest)
            if m:
                crew = m.group(1)
                title = " ".join(w for w in words[1:] if w not in ("--crew", crew)) or None
            if crew not in self.home.crews():
                self.say(f"No crew named {crew!r}. Crews: {', '.join(self.home.crews())}.")
                return
            if title and self.home.roles_dir(crew).joinpath(folder_name(title)).exists():
                self.say(f"Crew {crew} already has a role called {title}.")
                return
            limits = load_limits(self.home)
            kw = {"client_factory": self.app.client_factory} if self.app.client_factory else {}
            outcome = start(self.home, RolePurpose(self.home, crew, limits, title), crew, limits, ui=self.ui, **kw)
            if outcome.confirmed:
                self.say(f"Added {outcome.proposal.data['name']} to crew {crew}.", level="done")
            return
        if action == "edit" and len(words) > 1:
            name = words[1]
            if name == "lead":
                folder = self.home.lead_dir
            else:
                found = [c for c in self.home.crews() if (self.home.roles_dir(c) / name.split("/")[-1]).is_dir()]
                if not found:
                    self.say(f"No role named {name!r}.")
                    return
                folder = self.home.roles_dir(found[0]) / name.split("/")[-1]
            self.ui.edit([folder / "config.md", folder / "soul.md"])
            self.say("Saved. The role is read again at the next run.")
            return
        self.usage("/role, /role add [title], /role add [title] --crew <crew>, /role edit <name>")

    # -- runs (10d) ---------------------------------------------------------------

    def cmd_runs(self, words: list[str], rest: str) -> None:
        rows = views.run_list(self.home)
        if not rows:
            self.say("No runs yet. Brief a task with /brief.")
            return
        p = self.app.colours
        out = Text("Runs, newest last:\n", style=p.muted)
        for r in rows[-12:]:
            waiting = f"  {r['waiting']} waiting" if r["waiting"] else ""
            unseen = "  new" if not r["seen_at"] and r["status"] != "running" else ""
            out.append(f"{r['run_id']}  {r['status']:<18} {r['total_tokens']:>9,} tokens{waiting}{unseen}\n",
                       style=p.text)
            out.append(f"    {r['brief'][:90]}\n", style=p.muted)
        out.append("See one with /report <run_id>.", style=p.muted)
        self.rich(out)

    def _find_run(self, words: list[str]) -> Path | None:
        try:
            if words:
                return runs.find_run(self.home, words[0])
            return runs.latest_run(self.home, None)
        except (runs.RunNotFound, LookupError) as e:
            self.say(str(e) or "No runs yet.")
            return None

    def cmd_report(self, words: list[str], rest: str) -> None:
        run_dir = self._find_run(words)
        if run_dir is None:
            return
        r = views.report(self.home, run_dir)
        if r["report"] is None:
            self.say(f"Run {run_dir.name} has no report yet (status {r['status']}).")
            return
        self.rich(self.report_text(r), "report")
        runs.mark_seen(run_dir)

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
        out.append(f"\nWorkspace: ", style=p.muted)
        self.link(out, r["workspace"])
        return out

    def cmd_open(self, words: list[str], rest: str) -> None:
        from ombro.cli import _launch

        run_dir = self._find_run(words)
        if run_dir is None:
            return
        folder = run_dir / "workspace"
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

    def cmd_stop(self, words: list[str], rest: str) -> None:
        if not words:
            self.usage("/stop <run_id>")
            return
        run_dir = self._find_run(words)
        if run_dir is None:
            return
        meta, _ = runs.read_run(run_dir)
        if meta.get("status") != "running":
            self.say(f"Run {run_dir.name} is not running (status {meta.get('status')}).")
            return
        pid = meta.get("pid")
        if not pid or not runs.pid_alive(int(pid)):
            runs.reap_dead_runs(self.home)
            self.say(f"Run {run_dir.name} had no live process; it is marked failed and its report written.")
            return
        if not self.ui.yes("stop", f"Stop run {run_dir.name}? It finishes its current call, then writes its report."):
            self.say("Nothing changed.")
            return
        os.kill(int(pid), signal.SIGTERM)
        self.say(f"Asked run {run_dir.name} to stop. Its notice appears here when it has written its report.")

    def start_run(self, spec: str) -> str | None:
        """`ombro run --spec <spec> --detach`, as the command line would. Returns the run ID."""
        env = {**os.environ, "OMBRO_HOME": str(self.home.root)}
        r = subprocess.run([sys.executable, "-m", "ombro", "run", "--spec", spec, "--detach"], capture_output=True,
                           text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
        m = re.search(r"Started run (\S+)", r.stdout)
        if r.returncode != 0 or not m:
            self.fail(f"The run could not start: {(r.stderr or r.stdout).strip()[-400:]}")
            return None
        return m.group(1)

    # -- the outbox (10e) ----------------------------------------------------------

    def cmd_outbox(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        refs = words[1:]
        try:
            if action == "list":
                items = outbox.waiting(self.home)
                if not items:
                    self.say("Nothing is waiting for you.")
                    return
                p = self.app.colours
                out = Text(f"{_n(len(items), 'action')} waiting for your decision:\n", style=p.muted)
                for a in items:
                    out.append(f"  {a.ref}  ", style=f"bold {p.text}")
                    out.append(f"{a.status:<9} {a.tool}  ", style=p.muted)
                    out.append(a.summary(70) + "\n", style=p.text)
                out.append("/outbox show <ref> for one in full; /outbox approve or decline <ref>...", style=p.muted)
                self.rich(out)
                return
            if action == "show" and refs:
                a = outbox.find(self.home, refs[0])
                d = a.as_dict()
                lines = [f"{a.ref}: {a.tool} ({d.get('class', '')}), {a.status}",
                         f"From run {a.run_id}, role {d.get('role', '')}. Reason: {d.get('reason', '')}",
                         "Arguments:", json.dumps(a.arguments, ensure_ascii=False, indent=2)]
                self.say("\n".join(lines))
                return
            if action in ("approve", "decline") and refs:
                items = [outbox.find(self.home, r) for r in refs]
                verb = "Run" if action == "approve" else "Decline"
                tail = "" if action == "approve" else " They will never run."
                for a in items:
                    self.say(f"{a.ref}: {a.tool}  {a.summary(80)}")
                if not self.ui.yes("confirm", f"{verb} {'this action' if len(items) == 1 else f'these {len(items)} actions'}?{tail}"):
                    self.say("Nothing was done.")
                    return
                for a in items:
                    try:
                        done = outbox.approve(self.home, a) if action == "approve" else outbox.decline(self.home, a)
                        self.say(f"{done.ref}: {done.status}.", level="done")
                    except outbox.OutboxError as e:
                        self.fail(f"{a.ref}: {e}")
                return
            if action == "resolve" and len(refs) == 2 and refs[1] in ("sent", "not-sent"):
                a = outbox.resolve(self.home, outbox.find(self.home, refs[0]), refs[1] == "sent")
                self.say(f"{a.ref}: {a.status}.", level="done")
                return
        except outbox.OutboxError as e:
            self.fail(str(e))
            return
        self.usage("/outbox, /outbox show <ref>, /outbox approve <ref>..., /outbox decline <ref>..., "
                   "/outbox resolve <ref> sent|not-sent. To edit one, use `ombro outbox edit <ref>`.")

    # -- schedules, keys, folders, crews, tools, preferences (10e) -------------------

    def cmd_schedule(self, words: list[str], rest: str) -> None:
        from ombro import schedules

        action = words[0].lower() if words else "list"
        if action == "list":
            rows = views.schedule_list(self.home)
            if not rows:
                self.say("No schedules yet. Add one with `ombro schedule add <name> --spec <spec> --cron \"…\"`.")
                return
            lines = []
            for s in rows:
                last = f", last {s['last_run']['status']}" if s["last_run"] else ""
                nxt = f", next {s['next'].replace('T', ' ')}" if s["next"] else ""
                lines.append(f"{s['name']}  {'on' if s['enabled'] else 'off'}  {s['cron']}{nxt}{last}"
                             + (f"  (problem: {s['problem']})" if s["problem"] else ""))
            self.say("Schedules:", items=lines)
            return
        if action in ("enable", "disable") and len(words) > 1:
            try:
                s = schedules.find_schedule(self.home, words[1])
            except Exception as e:  # noqa: BLE001
                self.say(str(e))
                return
            schedules.set_enabled(s.path, action == "enable")
            self.say(f"{s.name} is {'on' if action == 'enable' else 'off'}.", level="done")
            return
        self.usage("/schedule, /schedule enable <name>, /schedule disable <name>. Adding one is "
                   "`ombro schedule add` for now.")

    def cmd_keys(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        if action == "list":
            data = views.key_list(self.home)
            if not data["keys"]:
                self.say("No keys are set or needed yet.")
                return
            items = [f"{k['name']}: {'set' if k['set'] else 'not set'}"
                     + (f" ({k['source']})" if k["source"] == "environment" else "")
                     + (f", used by {', '.join(k['used_by'])}" if k["used_by"] else "") for k in data["keys"]]
            items += [f"sign-in {s['server']}: {s['status']}" for s in data["sign_ins"]]
            self.say("Keys (values are never shown):", items=items)
            return
        if action == "set" and len(words) > 1:
            from ombro import keys
            from ombro.cli import set_key_flow

            if not keys.valid_name(words[1]):
                self.say("Key names use capitals, digits and underscores, starting with a letter.")
                return
            set_key_flow(self.home, words[1], self.ui)
            return
        self.usage("/keys, /keys set <NAME>. Removing one is `ombro keys remove` for now.")

    def cmd_folders(self, words: list[str], rest: str) -> None:
        from ombro import folders
        from ombro.settings import SetupError

        action = words[0].lower() if words else "list"
        try:
            if action == "list":
                data = views.folder_list(self.home)
                if not data["folders"]:
                    self.say("No folders are shared. Runs reach only their own workspace. Share one with "
                             "/folders add <path>.")
                    return
                self.say("Shared folders:", items=[f"{f['name']}  {f['access']}  {f['path']}"
                                                   + (f"  (problem: {f['problem']})" if f["problem"] else "")
                                                   for f in data["folders"]])
                return
            if action == "add" and len(words) > 1:
                from ombro.folders_cmd import add_flow

                add_flow(self.home, rest.split(" ", 1)[1].strip(), None, None, False, self.ui)
                return
            if action == "remove" and len(words) > 1:
                g = folders.remove(self.home, words[1])
                self.say(f"Removed {g.name!r} ({g.path}). Runs already going keep it.", level="done")
                return
        except (folders.FolderError, SetupError) as e:
            self.fail(str(e))
            return
        self.usage("/folders, /folders add <path>, /folders remove <name>")

    def cmd_crew(self, words: list[str], rest: str) -> None:
        from ombro.home import crew_title, ensure_crew
        from ombro.roles import NAME
        from ombro.settings import set_preference

        action = words[0].lower() if words else "list"
        if action == "list":
            team = views.crews(self.home)
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
            return
        if action == "add" and len(words) > 1:
            name = words[1]
            if not NAME.match(name) or self.home.crew_dir(name).exists():
                self.say("Crew names use lower-case letters, digits, - and _, and must be new.")
                return
            ensure_crew(self.home, name, "", " ".join(words[2:]))
            self.say(f"Created crew {name} ({crew_title(self.home, name)}). Add its roles with "
                     f"/role add <title> --crew {name}.", level="done")
            return
        if action == "use" and len(words) > 1:
            if words[1] not in self.home.crews():
                self.say(f"No crew named {words[1]!r}.")
                return
            set_preference(self.home, "default_crew", words[1])
            self.say(f"The default crew is now {words[1]}.", level="done")
            return
        self.usage("/crew, /crew add <name> [title], /crew use <name>")

    def cmd_tools(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        if action in ("add", "remove"):
            from ombro.server_cmd import ServerError, add_flow, remove_flow
            from ombro.cli import CliError

            args = argparse.Namespace(name=words[1] if len(words) > 1 else None, command=None, url=None, env=None,
                                      header=None, secret=None, server_class=None, no_prompt=False, yes=False)
            try:
                (add_flow if action == "add" else remove_flow)(self.home, args, self.ui)
            except (CliError, ServerError) as e:
                self.fail(str(e))
            return
        env = {**os.environ, "OMBRO_HOME": str(self.home.root)}
        cmd = [sys.executable, "-m", "ombro", "tools"] + ([words[0]] if words else [])
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180, stdin=subprocess.DEVNULL)
        self.say((r.stdout or r.stderr).rstrip() or "No output.")
        self.say("Add an MCP server with /tools add [name]; remove one with /tools remove <name>.")

    def cmd_preferences(self, words: list[str], rest: str) -> None:
        if words and words[0].lower() == "edit":
            self.ui.edit([self.home.preferences_config, self.home.instructions])
            self.say("Saved. They are used from the next chat, interview or run.")
            return
        p = views.preferences(self.home)
        self.say("Preferences:", items=[f"name: {p['name'] or '(not set)'}", f"theme: {p['theme']}",
                                        f"default crew: {p['default_crew']}",
                                        f"editor: {p['editor'] or '(from $VISUAL or $EDITOR)'}",
                                        f"outward: {p['outward']}",
                                        f"about you: {'set' if p['instructions_set'] else 'not set'}"])
        self.say("Change them with /preferences edit, or /theme for the theme.")


# ---------------------------------------------------------------------------
# Following runs and posting notices (10d)

PROGRESS = {"briefed": "briefed", "returned": "returned", "staged": "staged an action",
            "outward_done": "did an outward action", "limit_reached": "hit a limit", "finish": "finished the work",
            "report_written": "wrote the report", "failed": "failed"}


def progress_line(run_id: str, ev: dict) -> str | None:
    what = PROGRESS.get(str(ev.get("action")))
    if not what:
        return None
    note = " ".join(str(ev.get("note") or "").split())
    return f"{run_id} · {ev.get('role', '')} {what}" + (f": {note[:100]}" if note else "")


def notice_text(app: "OmbroApp", run_id: str) -> Text:
    """What the interface says when a run ends: outcome, deliverables as links,
    actions waiting, and how to open the report. Written by Ombro, not the model."""
    p = app.colours
    try:
        run_dir = runs.find_run(app.home, run_id)
    except (runs.RunNotFound, LookupError):
        return Text(f"Run {run_id} has ended.", style=p.text)
    r = views.report(app.home, run_dir)
    out = Text()
    out.append(ended_line(r["status"], r.get("limit_hit")), style=f"bold {p.text}")
    out.append(f"\n{r['brief']}\n", style=p.muted)
    files = [d for d in r["deliverables"] if d["kind"] == "deliverable"]
    if files:
        out.append("Here's what it made:\n", style=p.text)
    for d in files:
        out.append(f"  {d['name']}: ", style=p.muted)
        out.append(d["path"], Style(color=p.text, underline=True, link=d["url"]))
        out.append("\n")
    if r["waiting"]:
        n = r["waiting"]
        out.append(f"{_n(n, 'action')} {'is' if n == 1 else 'are'} waiting for your OK: /outbox\n", style=p.text)
    out.append(f"Read the report with /report {run_id}", style=p.muted)
    return out


def ended_line(status: str, limit: str | None) -> str:
    """How a run ended, as Ombro says it in a notice."""
    if status == "completed":
        return "Your run has finished."
    if status == "stopped_by_limit":
        return f"Your run stopped at its {limit or 'run'} limit before finishing."
    if status == "stopped":
        return "Your run was stopped, as you asked."
    if status == "insufficient_brief":
        return "Your run needs more from you before it can start."
    return "Your run didn't finish: something went wrong."
