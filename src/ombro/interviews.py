"""The three interviews: set-up, adding a role, and briefing a task.

Each says what the Lead must find out, what `propose` takes, how the files
are built, and how the harness checks them. Checks run on the final texts,
so a version the user edited by hand is checked the same way.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ombro import mdfile, prompts
from ombro.home import INSTRUCTIONS_BODY, LEAD_SOUL, Home
from ombro.interview import FileDraft, Proposal, ProposalError, Purpose
from ombro.roles import KINDS, NAME, RoleError, load_crew, load_role
from ombro.settings import Limits, SetupError, format_duration, format_tokens, load_models, load_tool_settings


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    return value.strip() if isinstance(value, str) else ""


def _first_sentence(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    return out if len(out) <= n else out[: n - 1] + "…"


def available_tools(home: Home) -> dict[str, str]:
    """Every tool a role can be given, with a one-line description."""
    from ombro.native import native_registry

    settings = load_tool_settings(home)
    reg = native_registry(home, settings)
    out = {name: _first_sentence(reg.get(name).description) for name in reg.names()}
    # MCP tools: each server in tools.md is asked for its tools. A server that
    # cannot be reached is left out; `ombro tools <server>` says why.
    from ombro.mcp import McpClient, McpError, ServerConfig
    from ombro.outward import tool_class

    for server, cfg in settings.servers.items():
        try:
            with McpClient(home, ServerConfig.from_dict(server, cfg)) as c:
                listed = c.list_tools()
        except McpError:
            continue
        for t in listed:
            name = f"mcp.{server}.{t.name}"
            effect = tool_class(settings, name, t.annotations)[0]
            out[name] = f"({effect}; reaches {server}) " + _first_sentence(t.description)
    return out


def available_skills(home: Home) -> dict[str, str]:
    out = {}
    if home.skills_dir.is_dir():
        for d in sorted(home.skills_dir.iterdir()):
            f = d / "skill.md"
            if d.is_dir() and f.is_file():
                meta, body = mdfile.read(f)
                out[d.name] = str(meta.get("description") or _first_sentence(mdfile.prompt_text(body)) or "")
    return out


def roster(home: Home, crew: str, limits: Limits) -> str:
    try:
        _, roles = load_crew(home, crew, limits)
    except (RoleError, SetupError) as e:
        return f"(the crew's roles could not be loaded: {e})"
    if not roles:
        return "(none: this crew has no roles yet)"
    return "\n".join(
        f"- {r.name} ({r.kind}; tools: {', '.join(r.tools) or 'none'}): {r.description}" for r in roles.values()
    )


# ---------------------------------------------------------------------------
# Set-up


class SetupPurpose(Purpose):
    kind = "setup"
    title = ("Set-up interview: who you are and how you like to work, for every task. "
             "Tasks themselves are briefed later, with `ombro brief`.")
    rules = prompts.SETUP_INTERVIEW
    typical = "usually 4 to 6 questions"
    topics = [("name", "your name"), ("work", "what you do"), ("working_style", "how you work"),
              ("reports", "reports"), ("standing_rules", "standing rules")]

    def __init__(self, home: Home, crew: str = "default"):
        self.home = home
        self.crew = crew
        self.soul_path = home.roles_dir(crew) / "lead" / "soul.md"

    def context(self) -> list[tuple[str, str]]:
        instructions = ""
        if self.home.instructions.is_file():
            instructions = mdfile.prompt_text(mdfile.read(self.home.instructions)[1])
        soul = self.soul_path.read_text(encoding="utf-8").strip() if self.soul_path.is_file() else ""
        if soul == LEAD_SOUL.strip():
            soul = f"{soul}\n(This is the default soul that ships with Ombro.)"
        return [
            ("Current preferences/instructions.md", instructions or "(empty: nothing is known about the user yet)"),
            ("Current Lead soul.md", soul),
        ]

    def tool(self) -> dict:
        def field(desc: str) -> dict:
            return {"type": "string", "description": desc}

        return prompts.propose_tool(
            "Propose the user's standing context and your own soul.md. The harness writes the context as "
            "preferences/instructions.md and shows both; nothing is written unless the user accepts. "
            "No details of any one task.",
            {
                "name": field("What to call the user."),
                "work": field("What they do, in the first person: role or field. No task details."),
                "working_style": field("How they like to work with the crew, in the first person."),
                "reports": field("How they want reports, in the first person."),
                "standing_rules": field("Rules that hold for every task, as a Markdown list. May be empty."),
                "soul": field("Your soul.md: character and manner, second person. Nothing about any task."),
            },
            ["name", "work", "working_style", "reports", "soul"],
        )

    def build(self, args: dict) -> Proposal:
        missing = [k for k in ("name", "work", "reports", "soul") if not _text(args, k)]
        if missing:
            hint = " Ask the user's name." if "name" in missing else ""
            raise ProposalError(f"these are empty: {', '.join(missing)}.{hint}")
        sections = [
            ("About me", f"My name is {_text(args, 'name')}.\n\n{_text(args, 'work')}"),
            ("How I like to work", _text(args, "working_style")),
            ("How I want reports", _text(args, "reports")),
            ("Standing rules", _text(args, "standing_rules")),
        ]
        body = "\n\n".join(f"# {title}\n\n{text}" for title, text in sections if text)
        return Proposal(
            [
                FileDraft("preferences/instructions.md", self.home.instructions,
                          mdfile.dump({}, INSTRUCTIONS_BODY + "\n" + body + "\n")),
                FileDraft(f"crews/{self.crew}/roles/lead/soul.md", self.soul_path, _text(args, "soul") + "\n"),
            ]
        )

    def check(self, proposal: Proposal) -> Proposal:
        instructions, soul = proposal.files
        try:
            _, body = mdfile.parse(instructions.text)
        except mdfile.FrontMatterError as e:
            raise ProposalError(f"instructions.md: {e}") from None
        if not mdfile.prompt_text(body):
            raise ProposalError("instructions.md has no text outside comments")
        if not mdfile.prompt_text(mdfile.parse(soul.text)[1]):
            raise ProposalError("soul.md is empty")
        return proposal


# ---------------------------------------------------------------------------
# Adding a role


class RolePurpose(Purpose):
    kind = "role"
    title = ("Role interview: a crew member reused across many tasks, so it describes the kind of work, "
             "not today's task.")
    rules = prompts.ROLE_INTERVIEW
    accept_label = "create the role"
    typical = "usually 3 to 5 questions"
    topics = [("purpose", "what it is for"), ("must_not", "what it must not do"), ("access", "what it needs"),
              ("kind", "specialist or helper")]

    def __init__(self, home: Home, crew: str, limits: Limits, name: str | None = None):
        self.home = home
        self.crew = crew
        self.limits = limits
        self.name = name
        self.tools = available_tools(home)
        self.skills = available_skills(home)
        self.models = list(load_models(home))

    def opening(self) -> str:
        if self.name:
            return f"The user wants to add a role called {self.name!r}. Begin the interview."
        return "The user wants to add a role. Begin the interview."

    def context(self) -> list[tuple[str, str]]:
        return [
            ("Tools that exist (propose only these)", "\n".join(f"- {k}: {v}" for k, v in self.tools.items())),
            ("Skills that exist (propose only these)",
             "\n".join(f"- {k}: {v}" for k, v in self.skills.items()) or "(none yet)"),
            ("Models in models.md", ", ".join(self.models) + "\n(specialists use `default`, helpers `cheap`, "
             "unless there is a reason to choose otherwise)"),
            ("Roles already in the crew", roster(self.home, self.crew, self.limits)),
        ]

    def tool(self) -> dict:
        return prompts.propose_tool(
            "Propose the new role's config.md and soul.md. The harness checks them, shows them, and creates the "
            "role only if the user accepts.",
            {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["specialist", "helper"]},
                "description": {"type": "string", "description": "What kind of work it does and what it returns. "
                                "No details of any one task."},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "Only tools that exist."},
                "skills": {"type": "array", "items": {"type": "string"}, "description": "Only skills that exist."},
                "model": {"type": "string", "description": "A name from models.md; leave out for the default."},
                "max_steps": {"type": "integer", "description": "Model calls per brief; leave out for the default."},
                "soul": {"type": "string", "description": "The role's prompt, second person. General: no task details."},
            },
            ["name", "kind", "description", "tools", "soul"],
        )

    def folder(self, name: str) -> Path:
        return self.home.roles_dir(self.crew) / name

    def build(self, args: dict) -> Proposal:
        name = _text(args, "name").lower()
        kind = _text(args, "kind") or "specialist"
        meta: dict[str, Any] = {"name": name, "kind": kind, "description": _text(args, "description")}
        model = _text(args, "model")
        if model:
            meta["model"] = model
        tools = args.get("tools") or []
        skills = args.get("skills") or []
        if not isinstance(tools, list) or not isinstance(skills, list):
            raise ProposalError("`tools` and `skills` must be lists of names")
        if tools:
            meta["tools"] = [str(t).strip() for t in tools]
        if skills:
            meta["skills"] = [str(s).strip() for s in skills]
        if args.get("max_steps") is not None:
            meta["max_steps"] = args["max_steps"]
        soul = _text(args, "soul")
        if not NAME.match(name):
            raise ProposalError("the name must use lower-case letters, digits, - and _")
        folder = self.folder(name)
        return Proposal(
            [
                FileDraft(f"crews/{self.crew}/roles/{name}/config.md", folder / "config.md", mdfile.dump(meta)),
                FileDraft(f"crews/{self.crew}/roles/{name}/soul.md", folder / "soul.md", soul + "\n"),
            ],
            data={"name": name},
        )

    def check(self, proposal: Proposal) -> Proposal:
        config, soul = proposal.files
        try:
            meta, _ = mdfile.parse(config.text)
        except mdfile.FrontMatterError as e:
            raise ProposalError(f"config.md: {e}") from None
        name = str(meta.get("name") or "")
        if not NAME.match(name):
            raise ProposalError("the name must use lower-case letters, digits, - and _")
        if name == "lead" or meta.get("kind") == "lead":
            raise ProposalError("a crew has one Lead already; propose a specialist or a helper")
        if meta.get("kind") not in KINDS[1:]:
            raise ProposalError("kind must be specialist or helper")
        if self.folder(name).exists():
            raise ProposalError(f"a role named {name!r} already exists; choose another name "
                                f"(the user can change it with `ombro role edit {name}`)")
        unknown_tools = [t for t in meta.get("tools") or [] if t not in self.tools]
        if unknown_tools:
            raise ProposalError(f"no such tool: {', '.join(map(str, unknown_tools))}. Tools: {', '.join(self.tools)}")
        unknown_skills = [s for s in meta.get("skills") or [] if s not in self.skills]
        if unknown_skills:
            have = ", ".join(self.skills) or "none exist yet"
            raise ProposalError(f"no such skill: {', '.join(map(str, unknown_skills))}. Skills: {have}")
        if meta.get("model") and meta["model"] not in self.models:
            raise ProposalError(f"no model named {meta['model']!r} in models.md; choose from {', '.join(self.models)}")
        # The same check a run makes when it loads the role.
        tmp = Path(tempfile.mkdtemp(prefix="ombro-role-")) / name
        try:
            tmp.mkdir()
            (tmp / "config.md").write_text(config.text, encoding="utf-8")
            (tmp / "soul.md").write_text(soul.text, encoding="utf-8")
            role = load_role(tmp, self.limits)
        except RoleError as e:
            raise ProposalError(str(e).replace(str(tmp), name)) from None
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        folder = self.folder(name)
        files = [
            FileDraft(f"crews/{self.crew}/roles/{name}/config.md", folder / "config.md", config.text),
            FileDraft(f"crews/{self.crew}/roles/{name}/soul.md", folder / "soul.md", soul.text),
        ]
        return Proposal(files, warnings=role_warnings(role.tools), data={"name": name})

    def write(self, proposal: Proposal) -> None:
        self.folder(proposal.data["name"]).mkdir(parents=True, exist_ok=False)
        super().write(proposal)


def role_warnings(tools: list[str]) -> list[str]:
    if not tools:
        return [
            "this role has no tools. It cannot read or write workspace files, or search or fetch, so the Lead "
            "must put everything it needs into the brief itself."
        ]
    out = []
    if "workspace.read" not in tools:
        out.append("without workspace.read this role cannot read files other roles write.")
    if "workspace.write" not in tools:
        out.append("without workspace.write this role cannot save a file; its results come back inline only.")
    return out


# ---------------------------------------------------------------------------
# Briefing a task

SPEC_FIELDS = [
    ("done_looks_like", "Done looks like"),
    ("users", "Who uses it and how"),
    ("binding_constraint", "Binding constraint"),
    ("waste", "A waste would be"),
    ("non_goals", "Not wanted"),
]


class BriefPurpose(Purpose):
    kind = "brief"
    title = "Task brief: the Lead will ask about one task and write a spec for it."
    rules = prompts.BRIEF_INTERVIEW
    can_report_gaps = True
    typical = "usually 5 to 8 questions"
    topics = [("task", "the task"), ("done", "what done looks like"), ("users", "who uses it"),
              ("constraint", "binding constraint"),
              ("waste", "what would be a waste"), ("non_goals", "non-goals")]

    def accept_label_for(self, proposal: Proposal) -> str:
        name = proposal.data.get("name", "")
        return f"save it as {name!r} and start the run" if self.then_run else f"save it as {name!r}"

    def __init__(self, home: Home, crew: str, limits: Limits, task: str = "", *, then_run: bool = False):
        self.home = home
        self.crew = crew
        self.limits = limits
        self.task = task.strip()
        self.then_run = then_run
        self.accept_label = "save the spec and start the run" if then_run else "save the spec"

    def opening(self) -> str:
        if self.task:
            return f"The user's task, in their own words:\n\n{self.task}\n\nBegin the interview."
        return ("The user has a task for the crew. Begin by asking them to describe it in their own words: "
                "what it is and why.")

    def outward_tools(self) -> list[str]:
        """The crew's external tools (reaching people outside the organisation, or the public): the ones a
        brief must ask about. Reading and work need no answer."""
        if getattr(self, "_external", None) is None:
            from ombro import outward
            from ombro.settings import load_tool_settings

            try:
                _, roles = load_crew(self.home, self.crew, self.limits)
            except (RoleError, SetupError):
                roles = {}
            names = sorted({t for r in roles.values() for t in r.tools if t.startswith("mcp.")})
            try:
                settings = load_tool_settings(self.home)
            except (SetupError, mdfile.FrontMatterError):
                self._external = names  # cannot tell: ask
            else:
                self._external = outward.external_tools(self.home, settings, names) if names else []
        return self._external

    def context(self) -> list[tuple[str, str]]:
        L = self.limits
        out = [
            ("Roles in the crew", roster(self.home, self.crew, L)),
            ("Limits a run will have",
             f"- delegation rounds: {L.max_rounds}\n- time: {format_duration(L.run_timeout)}\n"
             f"- tokens: {format_tokens(L.max_tokens)}"),
        ]
        tools = self.outward_tools()
        if tools:
            from ombro import outward

            try:
                default = outward.resolve(self.home, {})["preset"]
            except SetupError:
                default = outward.DEFAULT_PRESET
            out.append(("External tools in the crew (they reach people outside the organisation, or the "
                        "public, or spend money)",
                        "\n".join(f"- {t}" for t in tools)
                        + f"\nThe user's default permission is {default}. Ask what may happen without them."))
        return out

    def tool(self) -> dict:
        props: dict[str, Any] = {
            "name": {"type": "string", "description": "Short lower-case file name for the spec."},
            "title": {"type": "string", "description": "One line saying what the task is."},
            "details": {"type": "string", "description": "Anything else the crew needs: scope, sources, where "
                        "the result goes. Optional."},
        }
        for key, label in SPEC_FIELDS:
            props[key] = {"type": "string", "description": label}
        if self.outward_tools():
            props["outward_preset"] = {"type": "string", "enum": ["manual", "balanced", "auto"],
                                       "description": "What may happen without the user, from their answer."}
            for key, what in (("outward_allow", "run without asking"), ("outward_stage", "always wait for the user")):
                props[key] = {"type": "array", "items": {"type": "string"},
                              "description": f"Optional: mcp.* tools the user said may {what}."}
        return prompts.propose_tool(
            "Propose the spec. The harness refuses it if any of the five required answers is empty, shows it, "
            "and saves it only if the user accepts.",
            props,
            ["name", "title", *[k for k, _ in SPEC_FIELDS]],
        )

    def path(self, name: str) -> Path:
        return self.home.crew_dir(self.crew) / "specs" / f"{name}.md"

    def build(self, args: dict) -> Proposal:
        name = _text(args, "name").lower()
        if not NAME.match(name):
            raise ProposalError("the name must use lower-case letters, digits, - and _")
        missing = [label for key, label in SPEC_FIELDS if not _text(args, key)]
        if not _text(args, "title"):
            missing.insert(0, "title")
        if missing:
            raise ProposalError(
                "these are empty: " + ", ".join(missing) + ". Ask the user, or call `insufficient` if they cannot say"
            )
        lines = [f"# {_text(args, 'title')}", ""]
        if _text(args, "details"):
            lines += [_text(args, "details"), ""]
        for key, label in SPEC_FIELDS:
            lines += [f"{label}: {_text(args, key)}", ""]
        meta: dict[str, Any] = {"name": name, "crew": self.crew}
        if args.get("outward_preset") or args.get("outward_allow") or args.get("outward_stage"):
            from ombro import outward

            setting = {"preset": args.get("outward_preset") or None,
                       "allow": list(args.get("outward_allow") or []), "stage": list(args.get("outward_stage") or [])}
            setting = {k: v for k, v in setting.items() if v}
            try:
                outward.parse_setting(setting, "the proposed spec")
            except SetupError as e:
                raise ProposalError(str(e)) from None
            meta["outward"] = setting
        elif self.outward_tools():
            raise ProposalError("the crew has external tools: ask the user what may happen without them, "
                                "and give outward_preset")
        text = mdfile.dump(meta, "\n".join(lines))
        return Proposal([FileDraft(f"crews/{self.crew}/specs/{name}.md", self.path(name), text)], data={"name": name})

    def check(self, proposal: Proposal) -> Proposal:
        (spec,) = proposal.files
        try:
            meta, body = mdfile.parse(spec.text)
        except mdfile.FrontMatterError as e:
            raise ProposalError(f"the spec: {e}") from None
        if not body.strip():
            raise ProposalError("the spec is empty")
        limits = meta.get("limits")
        if limits is not None and not isinstance(limits, dict):
            raise ProposalError("`limits` in a spec must be a mapping, e.g. limits: {allowance: generous}")
        warnings = []
        if meta.get("outward") is not None:
            from ombro import outward

            try:
                setting = outward.resolve(self.home, meta)
            except SetupError as e:
                raise ProposalError(str(e)) from None
            warnings.append(f"outward tools: {outward.describe(setting)}.")
        if spec.path.is_file():
            warnings.append(f"a spec named {proposal.data['name']!r} already exists and will be replaced "
                            "(its old text is kept as .previous.md).")
        if self.then_run:
            warnings.append(
                f"accepting starts the run at once: up to {self.limits.max_rounds} rounds, "
                f"{format_duration(self.limits.run_timeout)} and {format_tokens(self.limits.max_tokens)} tokens."
            )
        return Proposal(proposal.files, warnings=warnings, data=proposal.data)


# ---------------------------------------------------------------------------
# Starting an interview, and what a brief that could not be settled leaves


def start(home: Home, purpose: Purpose, crew: str, limits: Limits, **kwargs):
    """Hold one interview with the crew's Lead. kwargs go to Interview (ask, say, edit, client_factory…)."""
    from ombro.interview import Interview
    from ombro.settings import load_preferences

    lead = load_role(home.roles_dir(crew) / "lead", limits)
    return Interview(
        home,
        purpose,
        limits,
        crew=crew,
        lead_soul=lead.soul,
        instructions=load_preferences(home).instructions,
        model=lead.model,
        **kwargs,
    ).run()


def record_insufficient_brief(home: Home, crew: str, limits: Limits, outcome, task: str, tools: dict | None = None) -> Path:
    """A run that ends as insufficient_brief: no roles briefed, a report naming the gaps."""
    from ombro import report, runs
    from ombro.journal import Journal, utc_now

    gaps = list(outcome.gaps)
    if outcome.status == "limit":
        gaps.append(f"The interview stopped before a spec was confirmed ({outcome.detail}).")
    if not gaps:
        gaps.append("No spec was confirmed.")
    body = task.strip() or "(The task was not stated.)"
    spec_text = f"No spec was confirmed. The task as first given:\n\n{body}"
    run_dir = runs.create_run(home, crew, {}, spec_text, limits, source="interview (no spec was confirmed)",
                              tools=tools)
    if outcome.transcript and Path(outcome.transcript).is_file():
        shutil.copy2(outcome.transcript, run_dir / "interview.md")
    j = Journal(run_dir / "journal.jsonl")
    now = utc_now()
    runs.update_run(run_dir, started_at=now, interview_spend=_interview_spend(outcome))
    j.write("harness", "run_started", "from a task interview")
    j.write("lead", "insufficient_brief", "; ".join(gaps), gaps=gaps)
    facts = report.gather_facts(home, run_dir, "insufficient_brief")
    report.write(run_dir, report.render(facts, None, "no roles were briefed"))
    runs.update_run(run_dir, status="insufficient_brief", finished_at=utc_now(), spend={},
                    brief=runs.first_line(task) or "(the task was not stated)")
    j.write("harness", "run_ended", "insufficient_brief")
    from ombro import memory

    memory.add_index_entry(home, run_dir)
    return run_dir


def _interview_spend(outcome) -> dict:
    s = outcome.spend or {}
    return {k: s.get(k, 0) for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls")}
