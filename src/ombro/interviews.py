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
from ombro.home import INSTRUCTIONS_BODY, LEAD_SOUL, Home, crew_title, soul_with, tidy_title
from ombro.interview import FileDraft, Proposal, ProposalError, Purpose
from ombro.roles import KINDS, NAME, RoleError, crew_roles, folder_name, load_lead, load_role
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
    from ombro import skills

    return {name: s.description for name, s in skills.library(home).items()}


def roster(home: Home, crew: str, limits: Limits) -> str:
    """One crew's roles."""
    try:
        roles = crew_roles(home, crew, limits)
    except (RoleError, SetupError) as e:
        return f"(the crew's roles could not be loaded: {e})"
    if not roles:
        return "(none: this crew has no roles yet)"
    return "\n".join(
        f"- {r.name} ({r.kind}; tools: {', '.join(r.tools) or 'none'}): {r.description}" for r in roles.values()
    )


def crews_roster(home: Home, limits: Limits) -> str:
    """Every crew, its description and its roles, named crew/role."""
    out = []
    for crew in home.crews():
        meta = {}
        try:
            if (home.crew_dir(crew) / "crew.md").is_file():
                meta = mdfile.read(home.crew_dir(crew) / "crew.md")[0]
        except mdfile.FrontMatterError:
            meta = {}
        out.append(f"Crew {crew} (called the {crew_title(home, crew)}; use that title when talking to the user): "
                   f"{str(meta.get('description') or '').strip() or '(no description)'}")
        try:
            roles = crew_roles(home, crew, limits)
        except (RoleError, SetupError) as e:
            out.append(f"  (its roles could not be loaded: {e})")
            continue
        if not roles:
            out.append("  (no roles yet)")
        for r in roles.values():
            out.append(f"  - {crew}/{r.name} ({r.kind}; tools: {', '.join(r.tools) or 'none'}): {r.description}")
    return "\n".join(out) or "(no crews)"


# ---------------------------------------------------------------------------
# Set-up


class SetupPurpose(Purpose):
    kind = "setup"
    title = "Set-up interview"
    intro = ("Set-up interview: I'll ask you to tell me more about who you are, how you like to work, and how "
             "you'd like me to work with you on different tasks. (You'll brief actual tasks later.) The interview "
             "is usually four to six questions. Type /quit to stop; nothing is saved unless you accept it at the "
             "end.")
    proposal_intro = ("Great, I think I've got everything I need. Here's how I've written up your instructions, "
                      "and my own character as your Lead. Have a read through. You can then choose one of the "
                      "options at the end to save them, ask me to change them, or edit them yourself.")
    rules = prompts.SETUP_INTERVIEW
    typical = "usually 4 to 6 questions"
    topics = [("work", "what you do"), ("working_style", "how you work"), ("reports", "reports"),
              ("standing_rules", "standing rules")]

    def __init__(self, home: Home, crew: str = "default"):
        self.home = home
        self.crew = crew
        self.soul_path = home.lead_dir / "soul.md"

    def context(self) -> list[tuple[str, str]]:
        instructions = ""
        if self.home.instructions.is_file():
            instructions = mdfile.prompt_text(mdfile.read(self.home.instructions)[1])
        soul = self.soul_path.read_text(encoding="utf-8").strip() if self.soul_path.is_file() else ""
        if soul == LEAD_SOUL.strip():
            soul = f"{soul}\n(This is the default soul that ships with Ombro.)"
        from ombro.settings import load_preferences

        name = load_preferences(self.home).name
        return [
            ("The user's name", name or "(they chose not to give one: do not ask for it)"),
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
                "work": field("What they do, in the first person: role or field. No task details."),
                "working_style": field("How they like to work with the crew, in the first person."),
                "reports": field("How they want reports, in the first person."),
                "standing_rules": field("Rules that hold for every task, as a Markdown list. May be empty."),
                "soul": field("How you'll work with this user in particular, second person (\"With Mark, "
                              "you…\"), under about 100 words. Your core character is kept as it is. "
                              "Nothing about any task."),
            },
            ["work", "working_style", "reports", "soul"],
        )

    def build(self, args: dict) -> Proposal:
        missing = [k for k in ("work", "reports", "soul") if not _text(args, k)]
        if missing:
            raise ProposalError(f"these are empty: {', '.join(missing)}.")
        from ombro.settings import load_preferences

        name = load_preferences(self.home).name or _text(args, "name")
        about = (f"My name is {name}.\n\n" if name else "") + _text(args, "work")
        sections = [
            ("About me", about),
            ("How I like to work", _text(args, "working_style")),
            ("How I want reports", _text(args, "reports")),
            ("Standing rules", _text(args, "standing_rules")),
        ]
        body = "\n\n".join(f"# {title}\n\n{text}" for title, text in sections if text)
        return Proposal(
            [
                FileDraft("preferences/instructions.md", self.home.instructions,
                          mdfile.dump({}, INSTRUCTIONS_BODY + "\n" + body + "\n")),
                FileDraft("lead/soul.md", self.soul_path, soul_with(
                    self.soul_path.read_text(encoding="utf-8") if self.soul_path.is_file() else LEAD_SOUL,
                    name, _text(args, "soul"))),
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
    title = "Role interview"
    intro = ("Role interview: a team member is reused across many tasks, so describe the kind of work you want "
             "them to do, not one particular task. It's usually 3 to 5 questions. Type /quit to stop; nothing is "
             "written unless you accept it.")
    proposal_intro = ("Okay, here's my definition of the role you've described. Have a read through. You can "
                      "then choose one of the options at the end to save it, ask me to change it, or edit it "
                      "yourself.")
    rules = prompts.ROLE_INTERVIEW
    accept_label = "create the role"
    typical = "usually 3 to 5 questions"
    topics = [("purpose", "what the role is"), ("must_not", "what it must not do"), ("access", "what it needs"),
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
            return f"The user wants to add a role titled {self.name!r}. Begin the interview."
        return "The user wants to add a role. Begin the interview."

    def context(self) -> list[tuple[str, str]]:
        return [
            ("Tools that exist (propose only these)", "\n".join(f"- {k}: {v}" for k, v in self.tools.items())),
            ("Skills that exist (propose only these, or [\"all\"] for the whole library, now and later)",
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
                "title": {"type": "string", "description": "The role's title as the user wants it shown, e.g. "
                          "Marketing Assistant. Its folder name is made from it."},
                "kind": {"type": "string", "enum": ["specialist", "helper"]},
                "description": {"type": "string", "description": "What kind of work it does and what it returns. "
                                "No details of any one task."},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "Only tools that exist."},
                "skills": {"type": "array", "items": {"type": "string"},
                           "description": "Only skills that exist, or [\"all\"] for the whole library."},
                "model": {"type": "string", "description": "A name from models.md; leave out for the default."},
                "max_steps": {"type": "integer", "description": "Model calls per brief; leave out for the default."},
                "soul": {"type": "string", "description": "The role's prompt, second person. General: no task details."},
            },
            ["title", "kind", "description", "tools", "soul"],
        )

    def folder(self, name: str) -> Path:
        return self.home.roles_dir(self.crew) / name

    def build(self, args: dict) -> Proposal:
        title = " ".join(_text(args, "title").split())
        name = folder_name(title) if title else _text(args, "name").lower()
        if not title:
            title = tidy_title(name)
        kind = _text(args, "kind") or "specialist"
        meta: dict[str, Any] = {"name": name, "title": title, "kind": kind,
                                "description": _text(args, "description")}
        model = _text(args, "model")
        if model:
            meta["model"] = model
        tools = args.get("tools") or []
        skills = args.get("skills") or []
        if not isinstance(tools, list) or not isinstance(skills, list):
            raise ProposalError("`tools` and `skills` must be lists of names")
        if tools:
            meta["tools"] = [str(t).strip() for t in tools]
        if [str(s).strip() for s in skills] == ["all"]:
            meta["skills"] = "all"
        elif skills:
            meta["skills"] = [str(s).strip() for s in skills]
        if args.get("max_steps") is not None:
            meta["max_steps"] = args["max_steps"]
        soul = _text(args, "soul")
        if not NAME.match(name):
            raise ProposalError("give the role a title made of letters or digits, such as Marketing Assistant")
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
            raise ProposalError("there is one Lead already, above every crew; propose a specialist or a helper")
        if meta.get("kind") not in KINDS[1:]:
            raise ProposalError("kind must be specialist or helper")
        if self.folder(name).exists():
            raise ProposalError(f"a role named {name!r} already exists; choose another name "
                                f"(the user can change it with `ombro role edit {name}`)")
        unknown_tools = [t for t in meta.get("tools") or [] if t not in self.tools]
        if unknown_tools:
            raise ProposalError(f"no such tool: {', '.join(map(str, unknown_tools))}. Tools: {', '.join(self.tools)}")
        listed = meta.get("skills") or []
        unknown_skills = [] if listed == "all" else [s for s in listed if s not in self.skills]
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
    intro = ("Task brief: I'll ask you about this one piece of work, then write a spec the crew can work from "
             "without needing to ask you anything. It's usually 5 to 8 questions. Type /quit to stop; nothing is "
             "written unless you accept it.")
    proposal_intro = ("Here's the spec I've written for this task. Have a read through. You can then choose one "
                      "of the options at the end to save it, ask me to change it, or edit it yourself.")
    rules = prompts.BRIEF_INTERVIEW
    can_report_gaps = True
    typical = "usually 5 to 8 questions"
    topics = [("task", "the task"), ("done", "what done looks like"), ("users", "who uses it"),
              ("constraint", "binding constraint"),
              ("waste", "what would be a waste"), ("non_goals", "non-goals")]

    def accept_label_for(self, proposal: Proposal) -> str:
        name = proposal.data.get("name", "")
        return f"save it as {name!r} and start the run" if self.then_run else f"save it as {name!r}"

    def __init__(self, home: Home, crew: str, limits: Limits, task: str = "", *, then_run: bool = False,
                 start_dir: Path | None = None, crews: list[str] | None = None, revise: str | None = None):
        self.home = home
        self.crew = crew  # the default crew, for a spec that names none
        self.suggested = [c for c in (crews or []) if c in home.crews()]
        self.limits = limits
        self.task = task.strip()
        self.then_run = then_run
        self.start_dir = start_dir
        self.accept_label = "save the spec and start the run" if then_run else "save the spec"
        self.revise = revise  # a saved spec to change, rather than a new one
        if revise:
            self.title = f"Changing a saved spec: {revise}"
            self.intro = (f"Let's change your saved spec {revise!r}. I'll ask only about what's changing, then "
                          "show you the changes. Its old text is kept as a .previous.md file. Type /quit to stop; "
                          "nothing is written unless you accept it.")
            self.typical = "usually 1 to 3 questions"

    def opening(self) -> str:
        if self.revise:
            text = (self.home.specs_dir / f"{self.revise}.md").read_text(encoding="utf-8")
            return (f"The user wants to change their saved spec {self.revise!r}, which they confirmed earlier. "
                    "Here it is in full, front matter included:\n\n" + text + "\n\n"
                    "Every topic is already answered by this spec, so count them all as covered. Ask only about "
                    "what the user wants to change, and only what you need to make the change exactly. Then "
                    f"propose the whole spec again with the change made, under the same name ({self.revise}), "
                    "keeping everything else as it was, word for word, unless the user changes it.\n\n"
                    + (f"What the user said about the change:\n\n{self.task}" if self.task else
                       "Begin by asking what they want to change."))
        if self.task:
            return f"The user's task, in their own words:\n\n{self.task}\n\nBegin the interview."
        return ("The user has a task for the crews. Begin by asking them to describe it in their own words: "
                "what it is and why.")

    def outward_tools(self, crews: list[str] | None = None) -> list[str]:
        """External tools (reaching people outside the organisation, or the public) in the given crews, or in
        every crew: the ones a brief must ask about. Reading and work need no answer."""
        key = tuple(crews) if crews is not None else None
        cache = self.__dict__.setdefault("_external", {})
        if key not in cache:
            from ombro import outward
            from ombro.settings import load_tool_settings

            roles = []
            for c in (crews if crews is not None else self.home.crews()):
                try:
                    roles += list(crew_roles(self.home, c, self.limits).values())
                except (RoleError, SetupError):
                    pass
            names = sorted({t for r in roles for t in r.tools if t.startswith("mcp.")})
            try:
                settings = load_tool_settings(self.home)
            except (SetupError, mdfile.FrontMatterError):
                cache[key] = names  # cannot tell: ask
            else:
                cache[key] = outward.external_tools(self.home, settings, names) if names else []
        return cache[key]

    def context(self) -> list[tuple[str, str]]:
        L = self.limits
        out = [
            ("Crews and their roles (a run may brief roles only in the crews its spec names)",
             crews_roster(self.home, L)
             + (f"\nThe user started this brief for: {', '.join(self.suggested)}." if self.suggested else "")
             + f"\nThe default crew is {self.crew} (the {crew_title(self.home, self.crew)})."),
            ("Limits a run will have",
             f"- delegation rounds: {L.max_rounds}\n- time: {format_duration(L.run_timeout)}\n"
             f"- tokens: {format_tokens(L.max_tokens)}"),
        ]
        out.append(("Shared folders (outside a run, only folders the user has shared can be reached)",
                    self.folders_context()))
        tools = self.outward_tools()
        if tools:
            from ombro import outward

            try:
                default = outward.resolve(self.home, {})["preset"]
            except SetupError:
                default = outward.DEFAULT_PRESET
            out.append(("External tools in the crews (they reach people outside the organisation, or the "
                        "public, or spend money)",
                        "\n".join(f"- {t}" for t in tools)
                        + f"\nThe user's default permission is {default}. If the task's crews have any of them, "
                        "ask what may happen without them."))
        return out

    def folders_context(self) -> str:
        from ombro import folders

        try:
            grants, _ = folders.load(self.home)
        except SetupError:
            grants = {}
        lines = [f"- {g.name}: {g.path} ({g.access})" for g in grants.values()] or ["- none yet"]
        if self.start_dir is not None:
            here = Path(self.start_dir).resolve()
            inside = [g for g in grants.values() if here == folders.expand(g.path).resolve()
                      or here.is_relative_to(folders.expand(g.path).resolve())]
            if inside:
                lines.append(f"The user started this brief in {folders.display_path(here)}, inside shared folder "
                             f"{inside[0].name!r}.")
        lines.append("A run reaches only its own workspace and the folders its spec names. If the task needs "
                     "files outside the run (documents to read, or a folder to save results in), ask which "
                     "folder and why, and list it in `folders`: a shared folder by name, or a new one by its full "
                     "path. The harness asks the user before sharing a new folder; you never share one.")
        return "\n".join(lines)

    def tool(self) -> dict:
        props: dict[str, Any] = {
            "name": {"type": "string", "description": "Short lower-case file name for the spec."},
            "crews": {"type": "array", "items": {"type": "string"},
                      "description": "The crews whose roles the run may brief: the fewest that cover the task."},
            "folders": {
                "type": "array",
                "description": "Folders outside the run the task needs. Optional.",
                "items": {
                    "type": "object",
                    "required": ["folder", "access"],
                    "properties": {
                        "folder": {"type": "string", "description": "A shared folder's name, or the full path of "
                                   "a folder that is not shared yet (the user is asked)."},
                        "access": {"type": "string", "enum": ["read", "write"],
                                   "description": "read to read files; write to change or add files there."},
                        "why": {"type": "string", "description": "One line on why the task needs it."},
                    },
                },
            },
            "roles": {"type": "array", "items": {"type": "string"},
                      "description": "Optional: the roles the user wants to do the work, as crew/role or the "
                      "role's name. The run's Lead briefs them for the work."},
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
            ["name", "crews", "title", *[k for k, _ in SPEC_FIELDS]],
        )

    def path(self, name: str) -> Path:
        return self.home.specs_dir / f"{name}.md"

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
        crews = self._crews(args.get("crews"))
        meta: dict[str, Any] = {"name": name, "crews": crews}
        roles = self._roles(args.get("roles"), crews)
        if roles:
            meta["roles"] = roles
        wanted, new = self._folders(args.get("folders"))
        if wanted:
            meta["folders"] = wanted
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
        elif self.outward_tools(crews):
            raise ProposalError("the crews have external tools: ask the user what may happen without them, "
                                "and give outward_preset")
        text = mdfile.dump(meta, "\n".join(lines))
        return Proposal([FileDraft(f"specs/{name}.md", self.path(name), text)],
                        data={"name": name, "new_folders": new})

    def _crews(self, value: Any) -> list[str]:
        """The proposed crews. Left out, they are the crews the brief started from, or the default
        crew; the user sees which before accepting."""
        if isinstance(value, str):
            value = [value]
        if not value:
            value = self.suggested or [self.crew]
        if not isinstance(value, list):
            raise ProposalError("`crews` must be a list of crew names")
        crews: list[str] = []
        for c in value:
            c = str(c or "").strip()
            if c not in self.home.crews():
                raise ProposalError(f"no crew named {c!r}; crews: {', '.join(self.home.crews())}")
            if c not in crews:
                crews.append(c)
        return crews

    def _roles(self, value: Any, crews: list[str]) -> list[str]:
        """The roles the user gave the work to, each in one of the spec's crews; written as
        the role's own name, or crew/role where more than one of the crews has it."""
        from ombro.roles import RoleError, load_roles, resolve_role

        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ProposalError("`roles` must be a list of role names")
        try:
            available = load_roles(self.home, crews, self.limits)
        except (RoleError, SetupError) as e:
            raise ProposalError(str(e)) from None
        out = []
        for name in value:
            role, why = resolve_role(available, str(name or ""))
            if role is None:
                raise ProposalError(f"roles: {why}")
            if role.name not in out:
                out.append(role.name)
        return out

    def _folders(self, value: Any) -> tuple[dict[str, str], list[dict[str, str]]]:
        """The spec's folders by name, and the folders not yet shared that the user
        will be asked about on accepting."""
        from ombro import folders

        if not value:
            return {}, []
        if not isinstance(value, list):
            raise ProposalError("`folders` must be a list of {folder, access, why}")
        try:
            grants, _ = folders.load(self.home)
        except SetupError as e:
            raise ProposalError(str(e)) from None
        wanted: dict[str, str] = {}
        new: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict) or not str(item.get("folder") or "").strip():
                raise ProposalError("each folder needs `folder` (a shared folder's name or a full path) and `access`")
            ref = str(item["folder"]).strip()
            access = "write" if item.get("access") == "write" else "read"
            if ref in grants:
                wanted[ref] = "read" if grants[ref].access == "read" else access
                continue
            if not ref.startswith(("/", "~")):
                names = ", ".join(grants) or "none"
                raise ProposalError(f"no folder is shared as {ref!r} (shared: {names}); give the folder's full path")
            try:
                real = folders.check_place(ref, self.home)
            except folders.FolderError as e:
                raise ProposalError(f"{ref} cannot be shared: {e}") from None
            existing = folders.find(grants, real)
            if existing:
                wanted[existing.name] = "read" if existing.access == "read" else access
                continue
            name = folders.suggest_name(real, set(grants) | set(wanted) | {n["name"] for n in new})
            new.append({"name": name, "path": folders.display_path(real), "access": access,
                        "why": " ".join(str(item.get("why") or "").split())[:200]})
            wanted[name] = access
        return wanted, new

    def before_write(self, proposal: Proposal, ui) -> Proposal:
        """Folders the spec needs that are not shared yet: the user is asked about each,
        and only a yes shares it. A no leaves it out of the spec."""
        from ombro import folders

        new = proposal.data.get("new_folders") or []
        if not new:
            return proposal
        (spec,) = proposal.files
        meta, body = mdfile.parse(spec.text)
        wanted = dict(meta.get("folders") or {})
        for f in new:
            if f["name"] not in wanted:
                continue  # removed in a hand edit
            ui.say(f"The spec needs {f['path']} ({'read and write' if f['access'] == 'write' else 'read only'})"
                   + (f": {f['why']}" if f["why"] else "") + ".", spaced=True)
            access = share_question(ui, f["name"], may_write=f["access"] == "write")
            if access is None:
                wanted.pop(f["name"], None)
                ui.say(f"Not shared. The spec leaves {f['path']} out.")
                continue
            try:
                g = folders.add(self.home, f["path"], f["name"], access)
            except (folders.FolderError, SetupError) as e:
                wanted.pop(f["name"], None)
                ui.say(f"It could not be shared ({e}), so the spec leaves it out.")
                continue
            wanted[g.name] = access
            ui.say(f"Shared as {g.name!r} ({access}). Backup: {folders.backup_hint(g.path, self.home)}.", level="done")
        if wanted:
            meta["folders"] = wanted
        else:
            meta.pop("folders", None)
        draft = FileDraft(spec.label, spec.path, mdfile.dump(meta, body))
        return Proposal([draft], warnings=proposal.warnings, data={**proposal.data, "new_folders": []})

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
        from ombro import runs

        try:
            crews = runs.spec_crews(self.home, meta)
        except SetupError as e:
            raise ProposalError(str(e)) from None
        warnings.append("the run may brief roles in the " + " and the ".join(crew_title(self.home, c) for c in crews)
                        + ("" if meta.get("crews") else " (the default crew: the spec names none)") + ".")
        if meta.get("roles"):
            try:
                given = self._roles(meta["roles"], crews)
            except ProposalError:
                raise
            warnings.append("the work goes to " + ", ".join(given) + "; the Lead may brief others only for side "
                            "tasks, and the report says who and why.")
        if meta.get("folders"):
            from ombro import folders

            try:
                wanted = folders.parse_spec(meta["folders"])
                grants, _ = folders.load(self.home)
            except SetupError as e:
                raise ProposalError(str(e)) from None
            new = {f["name"]: f for f in proposal.data.get("new_folders") or []}
            for name, access in wanted.items():
                if name in new:
                    warnings.append(f"folder {new[name]['path']} is not shared yet: you will be asked whether to "
                                    f"share it ({new[name]['access']}) when you accept.")
                elif name in grants:
                    final = "read" if "read" in (access, grants[name].access) else "write"
                    warnings.append(f"the run may {'change files in' if final == 'write' else 'read'} shared "
                                    f"folder {name} ({grants[name].path}).")
                else:
                    warnings.append(f"folder {name!r} is not shared, so a run would leave it out.")
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


def share_question(ui, name: str, may_write: bool) -> str | None:
    """Ask whether to share a folder: "write", "read" or None for no."""
    from ombro.ui import Field, Option

    options = ([Option("w", "write")] if may_write else []) + [
        Option("r", "read only" if may_write else "read", ("read", "y", "yes")), Option("n", "no")]
    choice = ui.ask(Field(
        "share", f"Share it with Ombro's runs as {name!r}?", "choice", options=options,
        invalid="Type " + ("write, read only, or no." if may_write else "read, or no."),
    ))
    return {"w": "write", "r": "read"}.get(choice)


def start(home: Home, purpose: Purpose, crew: str, limits: Limits, **kwargs):
    """Hold one interview with the Lead. kwargs go to Interview (ui, or ask, say and edit; client_factory…)."""
    from ombro.interview import Interview
    from ombro.settings import load_preferences

    lead = load_lead(home, limits)
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


def record_insufficient_brief(home: Home, crews: list[str], limits: Limits, outcome, task: str,
                              tools: dict | None = None) -> Path:
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
    run_dir = runs.create_run(home, crews, {}, spec_text, limits, source="interview (no spec was confirmed)",
                              tools=tools)
    if outcome.transcript and Path(outcome.transcript).is_file():
        shutil.copy2(outcome.transcript, run_dir / "interview.md")
    j = Journal(run_dir / "journal.jsonl")
    now = utc_now()
    runs.update_run(run_dir, started_at=now, interview_usage=_interview_usage(outcome))
    j.write("harness", "run_started", "from a task interview")
    j.write("lead", "insufficient_brief", "; ".join(gaps), gaps=gaps)
    facts = report.gather_facts(home, run_dir, "insufficient_brief")
    report.write(run_dir, report.render(facts, None, "no roles were briefed"))
    runs.update_run(run_dir, status="insufficient_brief", finished_at=utc_now(), usage={},
                    brief=runs.first_line(task) or "(the task was not stated)")
    j.write("harness", "run_ended", "insufficient_brief")
    from ombro import memory

    memory.add_index_entry(home, run_dir)
    return run_dir


def _interview_usage(outcome) -> dict:
    s = outcome.usage or {}
    return {k: s.get(k, 0) for k in ("total_tokens", "input_tokens", "output_tokens", "model_calls")}
