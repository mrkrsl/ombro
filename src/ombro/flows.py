"""The commands that act, run as flows inside a conversation.

Each does what its command on the command line does, through the same core
functions, and talks to the user only through a UI: it shows notes and asks
fields. The terminal interface (tui/commands.py) adds its own drawing of the
lists; the graphical one shows most lists on pages of their own and runs the
rest of these in a session (ombro.serve).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ombro import outbox, jobs, views
from ombro.home import crew_phrase, crew_title, find_crew
from ombro.ui import Failure, Note

if TYPE_CHECKING:  # pragma: no cover
    from ombro.session import Session


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def spec_view(home, spec: dict[str, Any]) -> str:
    """One saved spec, as /start and chat's offer show it before asking."""
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


class Flows:
    def __init__(self, session: "Session"):
        self.session = session
        self.home = session.home
        self.ui = session.ui

    # -- output -----------------------------------------------------------------

    def say(self, text: str, **kw: Any) -> None:
        self.ui.show(Note(text, **kw))

    def fail(self, text: str) -> None:
        self.ui.show(Failure(text))

    def usage(self, text: str) -> None:
        self.say(f"Use it like this: {text}")

    # -- lists, as plain notes (the terminal draws its own) ------------------------

    def list_approvals(self, items: list) -> None:
        self.say(f"{_n(len(items), 'action')} waiting for your decision:",
                 items=[f"{a.ref}  {a.status}  {a.tool}  {a.summary(70)}" for a in items])
        self.say("/approvals show <ref> for one in full; /approvals approve or decline <ref>...")

    def list_skills(self, view: dict[str, Any]) -> None:
        self.say(f"Approved ({len(view['skills'])}):", items=[
            f"{r['name']}: {r['description']} (used by {', '.join(r['used_by']) or 'no role by name'})"
            for r in view["skills"]] or ["none yet"])
        self.say(f"Waiting for your approval ({len(view['waiting'])}):", items=[
            f"{w['ref']}  {'a new version of' if w['change'] == 'update' else 'new:'} {w['name']}"
            for w in view["waiting"]] or ["none"])

    def list_crews(self, team: dict[str, Any]) -> None:
        lines = []
        for c in team["crews"]:
            lines.append(f"{c['title']} ({c['name']}{', default' if c['default'] else ''})")
            lines += [f"  {r['title']}: " + (r.get("problem") or f"{r['kind']}, {r['model']}") for r in c["roles"]]
        self.say(team["lead"]["title"], items=lines)
        self.say("Add a role with /role add; a crew with /crew add <name> [title].")

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

        kw = {"client_factory": self.session.init_client_factory} if self.session.init_client_factory else {}
        run_init(self.home, ui=self.ui, next_steps=False, **kw)

    def cmd_setup(self, words: list[str], rest: str) -> None:
        self.setup()

    def cmd_role(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        if action == "list":
            self.cmd_crew(["list"], "")
            return
        if action == "add":
            from ombro.roles import folder_name
            from ombro.settings import load_preferences

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
                self.say(f"{crew_phrase(self.home, crew, start=True)} already has a role called {title}.")
                return
            # Held in the chat, which starts from what it knows and is kept in the list of chats.
            self.session.ensure_chat().role_interview(crew, title)
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
            # The soul, as in Settings › Crews; its settings (model, tools) change there or with /model.
            self.ui.edit([folder / "soul.md"])
            self.say("Saved. The role is read again at the next job.")
            return
        self.usage("/role, /role add [title], /role add [title] --crew <crew>, /role edit <name>")

    # -- jobs (10d) ---------------------------------------------------------------

    def _find_job(self, words: list[str]) -> Path | None:
        try:
            if words:
                return jobs.find_job(self.home, words[0])
            return jobs.latest_job(self.home, None)
        except (jobs.JobNotFound, LookupError) as e:
            self.say(str(e) or "No jobs yet.")
            return None

    def cmd_stop(self, words: list[str], rest: str) -> None:
        if not words:
            self.usage("/stop <job_id>")
            return
        job_dir = self._find_job(words)
        if job_dir is None:
            return
        meta, _ = jobs.read_job(job_dir)
        if meta.get("status") != "running":
            self.say(f"Job {job_dir.name} is not running (status {meta.get('status')}).")
            return
        pid = meta.get("pid")
        if not pid or not jobs.pid_alive(int(pid)):
            jobs.reap_dead_jobs(self.home)
            self.say(f"Job {job_dir.name} had no live process; it is marked failed and its report written.")
            return
        if not self.ui.yes("stop", f"Stop job {job_dir.name}? It finishes its current call, then writes its report."):
            self.say("Nothing changed.")
            return
        os.kill(int(pid), signal.SIGTERM)
        self.say(f"Asked job {job_dir.name} to stop. Its notice appears here when it has written its report.")

    def cmd_start(self, words: list[str], rest: str) -> None:
        """/start [spec]: start a saved spec again, as `ombro start --spec <spec> --detach` does."""
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
            pick = self.ui.ask(Field("spec", "Which spec would you like to start? (newest first)", "choice",
                                     default="1", invalid="Please choose one of the numbers shown.",
                                     options=[Option(str(i), f"{s['name']}: {s['title']}")
                                              for i, s in enumerate(shown, 1)]))
            spec = shown[int(pick) - 1]
        self.session.offer_start(spec["name"], spec_view(self.home, spec))

    def start_job(self, spec: str, roles: list[str] | None = None) -> str | None:
        """Start a saved spec in the background. Returns the job's id, or None after saying why."""
        return self.session.start_job(spec, roles)

    # -- approvals (10e) ----------------------------------------------------------

    def cmd_approvals(self, words: list[str], rest: str) -> None:
        action = words[0].lower() if words else "list"
        refs = words[1:]
        try:
            if action == "list":
                items = outbox.waiting(self.home)
                if not items:
                    self.say("Nothing is waiting for you.")
                    return
                self.list_approvals(items)
                return
            if action == "show" and refs:
                a = outbox.find(self.home, refs[0])
                d = a.as_dict()
                if a.tool == outbox.SKILL_TOOL:
                    self._show_skill_proposal(a, d)
                    return
                lines = [f"{a.ref}: {a.tool} ({d.get('class', '')}), {a.status}",
                         f"From job {a.job_id}, role {d.get('role', '')}. Reason: {d.get('reason', '')}",
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
        self.usage("/approvals, /approvals show <ref>, /approvals approve <ref>..., /approvals decline <ref>..., "
                   "/approvals resolve <ref> sent|not-sent. To edit one, use `ombro approvals edit <ref>`.")

    def _show_skill_proposal(self, a: outbox.Action, d: dict) -> None:
        v = outbox.skill_view(self.home, a)
        lines = [f"{a.ref}: {'a new version of' if v['live'] else 'a new skill,'} {v['name']}, {a.status}",
                 f"From job {a.job_id}. Reason: {d.get('reason', '')}",
                 f"For: {', '.join(v['for']) or 'no role named'}"]
        if v["flags"]:
            lines.append(f"Signs of injected text in it: {', '.join(v['flags'])}. Read it closely.")
        if v["changed_since"] and a.status == "pending":
            lines.append(f"The live skill has changed since this was proposed: save it again with "
                         f"`ombro approvals edit {a.ref}` before approving.")
        lines += ["", v.get("diff") or "(no difference)"] if v["live"] else ["", v["text"].rstrip()]
        self.say("\n".join(lines))

    def cmd_skills(self, words: list[str], rest: str) -> None:
        """/skills, /skills show <name>, /skills give <skill> <crew/role>..., /skills add <folder>"""
        from ombro import skills
        from ombro.skills_cmd import library_view

        action = words[0].lower() if words else "list"
        try:
            if action == "list":
                view = library_view(self.home)
                if not view["skills"] and not view["waiting"]:
                    self.say("No skills yet. Ask me to remember how to do something, and I can write one; "
                             "after a job I may propose one for you to approve.")
                    return
                self.list_skills(view)
                return
            if action == "show" and len(words) == 2:
                s = skills.load(self.home, words[1])
                if s is None:
                    self.fail(f"There's no skill called {words[1]}.")
                    return
                self.say(s.text.rstrip())
                return
            if action == "add" and len(words) >= 2:
                from datetime import date

                from ombro.skills_cmd import describe_import

                imp = skills.inspect_import(self.home, rest.strip()[3:].strip())
                self.say("\n".join(describe_import(imp)))
                if not self.ui.yes("confirm", "Add it to your library? Roles that use it will follow it as your "
                                   "own instructions.", default=False):
                    self.say("Nothing was added.")
                    return
                kept = skills.add(self.home, imp, date.today().isoformat())
                self.say(f"Added {imp.name}." + (f" The old version is kept in {kept}." if kept else "")
                         + f" Give it to a role with /skills give {imp.name} <crew/role>.", level="done")
                return
            if action == "give" and len(words) >= 3:
                for who in words[2:]:
                    self.say(skills.give(self.home, words[1], who).capitalize() + ".", level="done")
                return
        except skills.SkillError as e:
            self.fail(str(e).capitalize() + ".")
            return
        self.usage("/skills, /skills show <name>, /skills give <skill> <crew/role>..., /skills add <folder>")

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
                last = f", last {s['last_job']['status']}" if s["last_job"] else ""
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
            self.say(f"Ombro now uses the {level} level, from this chat on. Jobs already going keep their "
                     "limits." + (f" Values set by hand were removed so the level "
                     f"applies: {', '.join(dropped)}." if dropped else ""), level="done")
            if self.session.chat is not None:
                self.session.chat.reload_limits()  # this chat's cap follows at once
            return
        if words:
            self.usage("/limits, /limits set [level]")
            return
        lim = load_limits(self.home)
        meta, _ = mdfile.read(self.home.limits)
        by_hand = [k for k in LEVEL_KEYS if k in meta]
        self.say(f"Ombro uses the {lim.allowance} level" + (f" (set by hand: {', '.join(by_hand)})" if by_hand
                                                             else "") + ":",
                 items=[f"each job: {format_tokens(lim.max_tokens)} tokens, {lim.max_rounds} rounds, "
                        f"{format_duration(lim.job_timeout)}",
                        f"each chat: {format_tokens(lim.chat_max_tokens)} tokens",
                        f"each interview: {format_tokens(lim.interview_max_tokens)} tokens",
                        f"each model call: {lim.max_output_tokens:,} output tokens"])
        self.say("Change it with /limits set. The levels:", items=allowance_table())

    def cmd_models(self, words: list[str], rest: str) -> None:
        from ombro import models

        action = words[0].lower() if words else "list"
        factory = self.session.init_client_factory
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
                    self.say("No folders are shared. Jobs reach only their own workspace. Share one with "
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
                self.say(f"Removed {g.name!r} ({g.path}). Jobs already going keep it.", level="done")
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
            self.list_crews(team)
            return
        if action == "add" and len(words) > 1:
            name = words[1]
            if not NAME.match(name) or self.home.crew_dir(name).exists():
                self.say("Crew names use lower-case letters, digits, - and _, and must be new.")
                return
            ensure_crew(self.home, name, "", " ".join(words[2:]))
            self.say(f"Created {crew_phrase(self.home, name)}. Add its roles with "
                     f"/role add <title> --crew {name}.", level="done")
            return
        if action == "use" and len(words) > 1:
            crew = find_crew(self.home, " ".join(words[1:]))
            if crew is None:
                self.say(f"There's no crew called {' '.join(words[1:])}.")
                return
            set_preference(self.home, "default_crew", crew)
            self.say(f"The default crew is now {crew_phrase(self.home, crew)}.", level="done")
            return
        self.usage("/crew, /crew add <name> [title], /crew use <name>")

    def cmd_tools(self, words: list[str], rest: str) -> None:
        from ombro import tool_cmd

        if words and tool_cmd.is_native(words[0].lower()):
            # /tools web.search [provider <name>] [max <n>]: see a built-in tool, or change it
            tool, opts = words[0].lower(), [w.lower() for w in words[1:]]
            provider = opts[opts.index("provider") + 1] if "provider" in opts[:-1] else None
            raw = opts[opts.index("max") + 1] if "max" in opts[:-1] else None
            if raw is not None and not raw.isdigit():
                self.usage("/tools web.search [provider <name>] [max <calls>]")
                return
            tool_cmd.flow(self.home, self.ui, tool, provider, int(raw) if raw else None)
            return
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
        env = {**os.environ, **self.home.process_env()}
        cmd = [*jobs.ombro_argv(), "tools"] + ([words[0]] if words else [])
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180, stdin=subprocess.DEVNULL)
        self.say((r.stdout or r.stderr).rstrip() or "No output.")
        self.say("Change a built-in tool with /tools <tool>, such as /tools web.search. Add an MCP server with "
                 "/tools add [name]; remove one with /tools remove <name>.")

    def cmd_preferences(self, words: list[str], rest: str) -> None:
        if words and words[0].lower() == "edit":
            self.ui.edit([self.home.preferences_config, self.home.instructions])
            self.say("Saved. They are used from the next chat, interview or job.")
            return
        p = views.preferences(self.home)
        self.say("Preferences:", items=[f"name: {p['name'] or '(not set)'}", f"theme: {p['theme']}",
                                        f"default crew: {p['default_crew_title']}",
                                        f"editor: {p['editor'] or '(from $VISUAL or $EDITOR)'}",
                                        f"outward: {p['outward']}",
                                        f"about you: {'set' if p['instructions_set'] else 'not set'}"])
        self.say("Change them with /preferences edit, or /theme for the theme.")
