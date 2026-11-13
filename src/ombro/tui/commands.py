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
from ombro.home import crew_title, find_crew
from ombro.ui import Failure, Field, Note, Option

if TYPE_CHECKING:  # pragma: no cover
    from ombro.tui.app import OmbroApp


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def spec_view(home, spec: dict[str, Any]) -> str:
    """One saved spec, as /run and chat's offer show it before asking."""
    from ombro import mdfile

    meta, _ = mdfile.read(Path(spec["path"]))
    crews = ", ".join(crew_title(home, c) for c in spec["crews"]) or crew_title(home, "default")
    parts = [f"{spec['name']}: {spec['title']}", f"crews: {crews}"]
    raw = meta.get("roles") or []
    if raw:
        parts.append("the work goes to: " + ", ".join(map(str, raw if isinstance(raw, list) else [raw])))
    if meta.get("folders"):
        parts.append("folders: " + ", ".join(map(str, meta["folders"] if isinstance(meta["folders"], (list, dict))
                                                else [meta["folders"]])))
    parts.append(f"saved {spec['modified']}")
    return "; ".join(parts) + "."


RECORD_MARK = "\n# Ombro's record"


def report_markdown(r: dict[str, Any], full: bool = False) -> str:
    """A report as the terminal shows it: the part you read (summary, deliverables, decisions,
    what was and was not done), then every file the run made as a link, and links to the full
    report and to Ombro's record. `full` adds the record itself."""
    from urllib.parse import quote

    text = r["report"].rstrip()
    head, _, record = text.partition("\n---\n" + RECORD_MARK.lstrip("\n"))
    if not record:
        head, _, record = text.partition(RECORD_MARK)
    out = [head.rstrip()]
    made = [d for d in r["deliverables"] if d["kind"] != "other"]
    if made:
        out += ["", "**Open what the run made:**", ""]
        for d in made:
            label = d["name"] + ("" if d["kind"] == "deliverable" else f" ({d['kind']} in a shared folder)")
            out.append(f"- [{label}]({d['url']})" + ("" if d["exists"] else " (no longer there)"))
    report_url = Path(r["report_path"]).as_uri()
    rid = quote(str(r["run_id"]))
    out += ["", f"[Open the full report]({report_url}) · "
                + (f"[Show Ombro's record](ombro:report/{rid}/full)" if not full else "Ombro's record is below.")]
    if full and record:
        out += ["", "---", "", "# Ombro's record" + record]
    return "\n".join(out) + "\n"


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
                given = m.group(1)
                title = " ".join(w for w in words[1:] if w not in ("--crew", given)) or None
                crew = find_crew(self.home, given)
                if crew is None:
                    self.say(f"There's no crew called {given}. The crews are "
                             f"{', '.join(crew_title(self.home, c) for c in self.home.crews())}.")
                    return
            if title and self.home.roles_dir(crew).joinpath(folder_name(title)).exists():
                self.say(f"The {crew_title(self.home, crew)} already has a role called {title}.")
                return
            limits = load_limits(self.home)
            kw = {"client_factory": self.app.client_factory} if self.app.client_factory else {}
            outcome = start(self.home, RolePurpose(self.home, crew, limits, title), crew, limits, ui=self.ui, **kw)
            if outcome.confirmed:
                from ombro.init_cmd import _role_title
                from ombro.models import offer_role_model

                added = outcome.proposal.data["name"]
                self.say(f"Added {added} to the {crew_title(self.home, crew)}.", level="done")
                offer_role_model(self.home, self.ui, f"{crew}/{added}", _role_title(self.home, crew, added))
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
        full = bool(words) and words[-1].lower() == "full"
        if full:
            words = words[:-1]
        run_dir = self._find_run(words)
        if run_dir is None:
            return
        r = views.report(self.home, run_dir)
        if r["report"] is None:
            self.say(f"Run {run_dir.name} has no report yet (status {r['status']}).")
            return
        self.app.call_from_thread(self.app.add_markdown, report_markdown(r, full), "report")
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

    def cmd_run(self, words: list[str], rest: str) -> None:
        """/run [spec]: run a saved spec again, as `ombro run --spec <spec> --detach` does."""
        from ombro.ui import Field, Option

        specs = views.spec_list(self.home)
        if not specs:
            self.say("There are no saved specs yet. /brief writes one.")
            return
        if words:
            name = words[0].removesuffix(".md")
            match = [s for s in specs if s["name"] == name]
            if not match:
                self.say(f"There's no saved spec called {name}. Saved specs: "
                         + ", ".join(s["name"] for s in specs[:12]) + ("…" if len(specs) > 12 else "") + ".")
                return
            spec = match[0]
        else:
            shown = specs[:20]
            pick = self.ui.ask(Field("spec", "Which spec would you like to run? (newest first)", "choice",
                                     default="1", invalid="Please choose one of the numbers shown.",
                                     options=[Option(str(i), f"{s['name']}: {s['title']}")
                                              for i, s in enumerate(shown, 1)]))
            spec = shown[int(pick) - 1]
        self.app.offer_run(spec["name"], spec_view(self.home, spec))

    def start_run(self, spec: str, roles: list[str] | None = None) -> str | None:
        """`ombro run --spec <spec> --detach [--role ...]`, as the command line would. Returns the run ID."""
        env = {**os.environ, "OMBRO_HOME": str(self.home.root)}
        extra = [a for r in roles or [] for a in ("--role", r)]
        r = subprocess.run([sys.executable, "-m", "ombro", "run", "--spec", spec, "--detach", *extra],
                           capture_output=True,
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

    def cmd_limits(self, words: list[str], rest: str) -> None:
        """One allowance level for the whole of Ombro: show it, or change it."""
        from ombro import mdfile
        from ombro.settings import (ALLOWANCES, LEVEL_KEYS, SetupError, allowance_table, format_duration,
                                    format_tokens, load_limits, set_allowance)
        from ombro.ui import Field, Option

        if words and words[0].lower() == "set":
            level = words[1].lower() if len(words) > 1 else None
            if level is None:
                names = list(ALLOWANCES)
                current = load_limits(self.home).allowance
                pick = self.ui.ask(Field(
                    "allowance", "Which level should Ombro use?", "choice",
                    default=str(names.index(current) + 1) if current in names else "2",
                    options=[Option(str(i), line) for i, line in enumerate(allowance_table(), 1)],
                    invalid="Please choose one of the numbers shown."))
                level = names[int(pick) - 1]
            try:
                dropped = set_allowance(self.home, level)
            except SetupError as e:
                self.say(str(e))
                return
            self.say(f"Ombro now uses the {level} level, from this chat on. Runs already going keep their "
                     "limits." + (f" Values set by hand were removed so the level "
                     f"applies: {', '.join(dropped)}." if dropped else ""), level="done")
            if self.app.chat is not None:
                self.app.chat.reload_limits()  # this chat's cap follows at once
            return
        if words:
            self.usage("/limits, /limits set [level]")
            return
        lim = load_limits(self.home)
        meta, _ = mdfile.read(self.home.limits)
        by_hand = [k for k in LEVEL_KEYS if k in meta]
        self.say(f"Ombro uses the {lim.allowance} level" + (f" (set by hand: {', '.join(by_hand)})" if by_hand
                                                             else "") + ":",
                 items=[f"each run: {format_tokens(lim.max_tokens)} tokens, {lim.max_rounds} rounds, "
                        f"{format_duration(lim.run_timeout)}",
                        f"each chat: {format_tokens(lim.chat_max_tokens)} tokens",
                        f"each interview: {format_tokens(lim.interview_max_tokens)} tokens",
                        f"each model call: {lim.max_output_tokens:,} output tokens"])
        self.say("Change it with /limits set. The levels:", items=allowance_table())

    def cmd_models(self, words: list[str], rest: str) -> None:
        from ombro import models

        action = words[0].lower() if words else "list"
        factory = self.app.init_client_factory
        if action == "list":
            self.say("Models, by connection:", items=models.listing_lines(self.home))
            self.say("/model changes who uses which. /models default chooses the default, /models refresh asks "
                     "each connection for its models, and /models add connects another provider.")
        elif action == "default":
            models.default_flow(self.home, self.ui, words[1] if len(words) > 1 else None)
        elif action == "refresh":
            models.refresh_flow(self.home, self.ui, factory)
        elif action == "add":
            models.add_flow(self.home, self.ui, factory)
        else:
            self.usage("/models, /models default [name], /models refresh, /models add")

    def cmd_model(self, words: list[str], rest: str) -> None:
        """Who uses which model: the Lead or a role, then the model."""
        from ombro import models

        models.model_flow(self.home, self.ui, words[0] if words else None, words[1] if len(words) > 1 else None)

    def cmd_folders(self, words: list[str], rest: str) -> None:  # the old name, kept working
        self.cmd_folder(words, rest)

    def cmd_folder(self, words: list[str], rest: str) -> None:
        from ombro import folders
        from ombro.settings import SetupError

        action = words[0].lower() if words else "list"
        try:
            if action == "list":
                data = views.folder_list(self.home)
                if not data["folders"]:
                    self.say("No folders are shared. Runs reach only their own workspace. Share one with "
                             "/folder add <path>.")
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
        self.usage("/folder, /folder add <path>, /folder remove <name>")

    def cmd_crew(self, words: list[str], rest: str) -> None:
        from ombro.home import ensure_crew
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
            self.say(f"Created the {crew_title(self.home, name)}. Add its roles with "
                     f"/role add <title> --crew {name}.", level="done")
            return
        if action == "use" and len(words) > 1:
            crew = find_crew(self.home, " ".join(words[1:]))
            if crew is None:
                self.say(f"There's no crew called {' '.join(words[1:])}.")
                return
            set_preference(self.home, "default_crew", crew)
            self.say(f"The default crew is now the {crew_title(self.home, crew)}.", level="done")
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
                                        f"default crew: {p['default_crew_title']}",
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
    out.append("Read the report", Style(color=p.text, underline=True, bold=True,
                                         meta={"@click": f"app.run_command({'/report ' + run_id!r})"}))
    out.append(f"  (or type /report {run_id})", style=p.muted)
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
