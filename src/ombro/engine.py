"""The Lead loop: one job, from spec to report."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ombro import contract, folders, material, mdfile, memory, outbox, outward, prompts, report, jobs
from ombro import skills as skills_mod
from ombro import journal as journal_mod
from ombro import carry as carry_mod
from ombro.client import ChatClient, ModelError, ModelReply, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.mcp import McpCallLost
from ombro.roles import Role, load_lead, load_roles, resolve_role
from ombro.settings import Limits, ModelSpec, SetupError, ToolSettings, load_models, load_preferences
from ombro.tools import LOAD_NAME, NotGranted, SharedFolder, Tool, ToolContext, ToolError, ToolRegistry
from ombro.tools import is_deferred, load_tool_schema, load_tools

# What a brief's last model call may still do beside submit_result: save what it has drafted.
LAST_CALL_TOOLS = ("workspace.write", "workspace.edit", "workspace.place")
TOOL_RESULT_CAP = 64_000  # room for one workspace.read of READ_MAX_CHARS, with its fencing


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _canonical(args: dict | None, raw: str) -> str:
    if args is None:
        return raw
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def _tool_message(call_id: str, payload: Any, src: str | None = None) -> dict[str, Any]:
    """A tool's result for the model; fenced as material when it came from outside."""
    long_text = isinstance(payload, dict) and isinstance(payload.get("result"), str) and len(payload["result"]) > 500
    if long_text:
        # An outside service's long text answer goes as it is, after the rest: inside JSON every quote and line
        # break in it would be escaped again, which costs tokens on every later step.
        rest = {k: v for k, v in payload.items() if k != "result"}
        content = json.dumps(rest, ensure_ascii=False) + "\nresult:\n" + payload["result"]
    else:
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(content) > TOOL_RESULT_CAP:
        content = content[:TOOL_RESULT_CAP] + "…[truncated]"
    if src:
        content = material.fence(content, src)
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def cut_off(reply: ModelReply, max_out: int) -> bool:
    """A reply that stopped because it reached the output limit: its text or a tool call's
    arguments are incomplete."""
    if reply.finish_reason == "length":
        return True
    if reply.finish_reason is None and not reply.usage.estimated and reply.usage.output_tokens >= max_out:
        return any(tc.arguments is None for tc in reply.tool_calls) or (
            not reply.tool_calls and contract.validate_text(reply.content)[0] is None
        )
    return False


def thought_out(reply: ModelReply) -> bool:
    """A cut-off reply that was nearly all thinking: it ran out before writing anything (23 Nov 2026: a
    "low" DeepSeek reply thought for 32,000 tokens, five and a half minutes, and was lost)."""
    out = reply.usage.output_tokens or 0
    return bool(out) and (getattr(reply.usage, "reasoning", 0) or 0) >= 0.8 * out


class _ThinkingOff:
    """For one call, a client sends its least thinking (thinking off, or the least a provider takes), then
    goes back to its own level. Clients keep the level in `thinking`, and a word of their own in
    `reasoning_effort` or `effort`."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.saved = {a: getattr(client, a) for a in ("thinking", "reasoning_effort", "effort") if hasattr(client, a)}

    def __enter__(self) -> None:
        has_level = "thinking" in self.saved
        for a in self.saved:
            if a == "thinking":
                setattr(self.client, a, "off")
            else:  # the ChatGPT plan keeps only an effort word: "none" is its off
                setattr(self.client, a, None if has_level else "none")

    def __exit__(self, *exc: Any) -> None:
        for a, v in self.saved.items():
            setattr(self.client, a, v)


# Tools whose results carry nothing from outside: the harness's own confirmations.
# skills.read returns the user's own library, which is not material either.
NOT_MATERIAL = {
    "workspace.write",
    "workspace.set_aside",
    "workspace.place",
    "skills.read",
    "browser.type",
    "browser.screenshot",
}


def _error(tool: Tool, e: Exception) -> dict[str, Any]:
    """A tool's error for the role. An outside service's error text is its own words,
    so it is marked as material, like its results."""
    out: dict[str, Any] = {"error": str(e)}
    if tool.outward:
        out["note"] = outward.MCP_NOTE
    return out


class Engine:
    def __init__(
        self,
        home: Home,
        job_dir: Path,
        *,
        client_factory: Callable[[ModelSpec], ChatClient] | None = None,
        registry: ToolRegistry | None = None,
        clock: Callable[[], float] = time.time,
        echo: Callable[[dict], None] | None = None,
        today: date | None = None,
    ):
        self.home = home
        self.job_dir = Path(job_dir)
        meta, _ = jobs.read_job(self.job_dir)
        self.job_id = meta["job_id"]
        self._skill_earlier: list[dict[str, Any]] = []  # earlier jobs a skill proposal may name
        self.crews = jobs.job_crews(meta)
        self.limits = Limits.from_dict(meta["limits"])  # fixed when the job was created
        self.live = bool(meta.get("live"))  # a live job: the user is watching, and the Lead may ask them
        self.scope = outward.Scope()  # what the job made and what waits, so related calls are one piece of work
        self.carry = carry_mod.Carry()  # private material read in the job, against what the browser carries out
        self.clock = clock
        self.journal = Journal(self.job_dir / "journal.jsonl", echo=echo)
        # Tool settings, like limits, were fixed when the job was created.
        self.tool_settings = ToolSettings.from_dict(meta.get("tools"), "job.md tools")
        # So are the permissions for outward (MCP) tools.
        self.outward = meta.get("outward") or {"preset": outward.DEFAULT_PRESET, "source": "default"}
        self.pool: outward.McpPool | None = None
        self.refused_tools: set[str] = set()  # set to `permission: refuse` in tools.md: never offered
        self.archived: set[str] = set()  # shared files whose pre-job version is in Archive/
        self._learned: set[str] = set()  # models whose own output maximum was learned in this job
        # So are the shared folders, checked against folders.md when the job was created.
        self.shared = {
            f["name"]: SharedFolder(f["name"], Path(f["path"]), f["access"]) for f in meta.get("folders") or []
        }
        self.folders_left_out = list(meta.get("folders_left_out") or [])
        self._folders_before: dict[str, Any] = {}
        # Paths in each writable shared folder that changed while one of the job's own tools ran: the rest of
        # a folder's changes were made by someone else (the user, another program) while the job ran.
        self._folders_touched: dict[str, set[str]] = {}
        self.code_unavailable: str | None = None
        if registry is None:
            from ombro.native import native_registry

            registry = native_registry(home, self.tool_settings)
        self.registry = registry
        self._client_factory = client_factory or (lambda spec: make_client(spec, home))
        self._clients: dict[str, ChatClient] = {}
        self.models: dict[str, ModelSpec] = {}
        self.gate = Gate(self.limits, clock)
        self.lead: Role | None = None
        self.roles: dict[str, Role] = {}
        self.instructions = ""
        self.finished = False
        # The local date, fixed for the job, so roles do not guess it.
        self.today = prompts.format_date(today or date.today())

    # -- set-up ------------------------------------------------------------

    def _client(self, spec: ModelSpec) -> ChatClient:
        if spec.name not in self._clients:
            self._clients[spec.name] = self._client_factory(spec)
        return self._clients[spec.name]

    def _load(self) -> None:
        self.models = load_models(self.home)
        if not self.crews:
            raise SetupError("job.md names no crews")
        self.lead = load_lead(self.home, self.limits)
        self.roles = load_roles(self.home, self.crews, self.limits)
        self.instructions = load_preferences(self.home).instructions
        self._expand_all_tools()
        self._with_browser()
        self._load_mcp()
        self._load_apis()
        self._load_composio()
        self._prepare_code()
        self._prepare_folders()
        for role in [self.lead, *self.roles.values()]:
            if role.model not in self.models:
                raise SetupError(f"role {role.name} uses model {role.model!r}, which is not in models.md")
            missing_tools = [
                t
                for t in role.tools
                if self.registry.get(t) is None
                and not (t == "code.run" and self.code_unavailable)
                and t not in self.refused_tools
            ]
            if missing_tools:
                self.journal.write(
                    "harness",
                    "warning",
                    f"{role.name} lists tools that are not available and will not be offered: {', '.join(missing_tools)}",
                )
            self._warn_missing_keys(role)
            have, missing_skills = skills_mod.names_for(self.home, role)
            if missing_skills:
                self.journal.write(
                    "harness", "warning", f"{role.name} lists skills that do not exist: {', '.join(missing_skills)}"
                )
            _, left = self._skill_listing(have)
            if left:
                self.journal.write(
                    "harness",
                    "warning",
                    f"{role.name}'s skills listing passed its cap of "
                    f"{self.limits.skills_list_chars:,} characters, so these were left out: " + ", ".join(left),
                )
        for s in skills_mod.library(self.home).values():
            if s.warning:
                self.journal.write("harness", "warning", s.warning)
        spec_meta, spec_body = mdfile.read(self.job_dir / "spec.md")
        self.spec_body = spec_body.strip()
        # `roles:` in the spec: who the work goes to (soft: the Lead may brief others for side tasks).
        self.spec_roles: list[str] = []
        raw = spec_meta.get("roles") or []
        for name in [raw] if isinstance(raw, str) else (raw if isinstance(raw, list) else []):
            role, why = resolve_role(self.roles, str(name))
            if role is None:
                self.journal.write("harness", "warning", f"the spec gives the work to {name!r}, but {why}")
            elif role.name not in self.spec_roles:
                self.spec_roles.append(role.name)
        if self.spec_roles:
            self.journal.write(
                "harness",
                "spec_roles",
                "the spec gives the work to " + ", ".join(self.spec_roles),
                roles=self.spec_roles,
            )
        if not self.spec_body:
            raise SetupError("the spec is empty")

    def _expand_all_tools(self) -> None:
        """`tools: all`: a role gets every built-in tool and every tool of every MCP server in tools.md.
        (Web APIs go to every crew member anyway.) A server that can't be reached is left out, with a warning."""
        if not any(r.all_tools for r in self.roles.values()):
            return
        names = [n for n in self.registry.names() if n != "workspace.set_aside"]
        servers = self.tool_settings.servers
        if servers:
            self.pool = self.pool or outward.McpPool(self.home, self.tool_settings, log_dir=self.job_dir)
            from ombro.mcp import McpError

            for server in sorted(servers):
                try:
                    names += [f"mcp.{server}.{t.name}" for t in self.pool.client(server).list_tools()]
                except McpError as e:
                    self.journal.write(
                        "harness",
                        "warning",
                        f"MCP server {server} could not be used, so its tools are not offered: {e}",
                    )
        for role in self.roles.values():
            if role.all_tools:
                role.tools.extend(n for n in names if n not in role.tools)

    BROWSER_TOOLS = ("browser.open", "browser.click", "browser.type", "browser.screenshot", "browser.submit")

    def _with_browser(self) -> None:
        """Web pages are read in the browser, so a role that reads them (web.fetch) gets the browser's
        other tools too, though its config.md lists tools by name. Sending still waits for approval."""
        here = [n for n in self.BROWSER_TOOLS if self.registry.get(n) is not None]
        for role in self.roles.values():
            if "web.fetch" in role.tools:
                role.tools.extend(n for n in here if n not in role.tools)

    def _load_mcp(self) -> None:
        """Start the MCP servers whose tools the roles name, and offer those tools."""
        names = sorted(
            {t for r in self.roles.values() for t in r.tools if t.startswith("mcp.") and self.registry.get(t) is None}
        )
        if not names:
            return
        self.pool = self.pool or outward.McpPool(self.home, self.tool_settings, log_dir=self.job_dir)
        from ombro.mcp import ServerConfig

        for server in sorted({p[0] for n in names if (p := outward.split_name(n))}):
            cfg = self.tool_settings.servers.get(server)
            warning = cfg and ServerConfig.from_dict(server, cfg).argv_warning()
            if warning:
                self.journal.write("harness", "warning", warning)
        tools, warnings = self.pool.tools(names)
        # `permission: refuse` beats everything, whatever a tool's class: a refused
        # tool is not offered at all (and _role_tool refuses it again if called).
        refused = [t for t in tools if outward.refused(t.name, self.tool_settings)]
        self.refused_tools = {t.name for t in refused}
        tools = [t for t in tools if t.name not in self.refused_tools]
        for t in tools:
            self.registry.register(t)
        for w in warnings:
            self.journal.write("harness", "warning", w)
        if refused:
            listed = ", ".join(sorted(self.refused_tools))
            self.journal.write(
                "harness",
                "warning",
                f"not offered to any role, because tools.md sets them to `permission: refuse`: {listed}",
                refused=sorted(self.refused_tools),
            )
        if tools:
            self.journal.write(
                "harness",
                "mcp_ready",
                f"{len(tools)} MCP tool(s): "
                + ", ".join(
                    f"{t.name} ({t.effect}: {outward.decide(self.outward, t.name, t.effect, self.tool_settings)})"
                    for t in tools
                ),
                outward=self.outward,
            )

    def _load_apis(self) -> None:
        """Offer each web API in tools.md to every crew member (not the Lead), as its tools."""
        from ombro.apitool import api_tools

        tools = [
            t for t in api_tools(self.home, self.tool_settings.apis) if not outward.refused(t.name, self.tool_settings)
        ]
        if not tools:
            return
        for t in tools:
            self.registry.register(t)
        for role in self.roles.values():
            role.tools.extend(t.name for t in tools if t.name not in role.tools)
        self.journal.write(
            "harness",
            "apis_ready",
            f"{len(tools)} API tool(s): "
            + ", ".join(
                f"{t.name} ({t.effect}: {outward.decide(self.outward, t.name, t.effect or 'external', self.tool_settings)})"
                for t in tools
            ),
        )

    def _load_composio(self) -> None:
        """Offer the tools of each toolkit connected in the user's Composio account to every crew
        member (not the Lead), as the web APIs are."""
        if not self.tool_settings.composio:
            return
        from ombro import composio

        found, warnings = composio.tools(self.home, self.tool_settings)
        for w in warnings:
            self.journal.write("harness", "warning", w)
        tools = [t for t in found if not outward.refused(t.name, self.tool_settings)]
        if not tools:
            return
        for t in tools:
            self.registry.register(t)
        for role in self.roles.values():
            role.tools.extend(t.name for t in tools if t.name not in role.tools)
        kits = sorted({t.name.split(".")[1] for t in tools})
        self.journal.write(
            "harness",
            "composio_ready",
            f"{len(tools)} Composio tool(s) from {', '.join(kits)}",
            toolkits=kits,
            tools=len(tools),
        )

    def _prepare_code(self) -> None:
        """code.run is offered only where the sandbox can be set up. Code never runs unconfined."""
        if not any("code.run" in r.tools for r in self.roles.values()):
            return
        from ombro import sandbox

        reason = sandbox.unavailable_reason()
        if reason:
            self.code_unavailable = reason
            self.registry.remove("code.run")
            self.journal.write(
                "harness",
                "warning",
                f"code.run is left out of this job: {reason}. Roles that name it carry on without it.",
                tool="code.run",
            )

    def _prepare_folders(self) -> None:
        """Clone each writable shared folder into backup/ and note what is in it,
        before any role can change it."""
        for note in self.folders_left_out:
            self.journal.write("harness", "warning", note)
        if not self.shared:
            return
        recorded = []
        for f in self.shared.values():
            entry: dict[str, Any] = {"name": f.name, "path": str(f.path), "access": f.access}
            if f.writable:
                entry.update(folders.backup(self.job_dir, entry))
                self._folders_before[f.name] = folders.manifest(f.path)
            recorded.append(entry)
            self.journal.write(
                "harness",
                "folder_ready",
                f"shared/{f.name} ({f.access}): {f.path}" + (f"; {entry['backup_note']}" if f.writable else ""),
                folder=f.name,
                **entry,
            )
        jobs.update_job(self.job_dir, folders=recorded)

    def _shared_for_prompts(self) -> list[tuple[str, str, str]]:
        return [(f.name, f.access, folders.display_path(Path(f.path))) for f in self.shared.values()]

    WRITING_TOOLS = ("workspace.write", "workspace.edit", "workspace.set_aside", "workspace.place", "code.run")

    def _folder_manifests(self) -> dict[str, Any]:
        return {f.name: folders.manifest(f.path) for f in self.shared.values() if f.name in self._folders_before}

    def _note_touched(self, before: dict[str, Any]) -> None:
        """After one of the job's writing tools: the shared paths that changed while it ran are the job's."""
        for name, after in self._folder_manifests().items():
            d = folders.diff(before.get(name), after)
            self._folders_touched.setdefault(name, set()).update(
                d["created"] + d["changed"] + d["removed"] if not d.get("too_large") else ["*"]
            )

    def _record_folder_changes(self) -> None:
        """What the job changed in each writable shared folder, from before and after, and apart from it what
        changed there while the job ran but not through the job's own tools."""
        for name, before in self._folders_before.items():
            f = self.shared[name]
            d = folders.diff(before, folders.manifest(f.path))
            touched = self._folders_touched.get(name, set())
            outside = {k: [] for k in ("created", "changed", "removed")}
            if not d.get("too_large") and "*" not in touched:
                for k in outside:
                    outside[k] = [x for x in d[k] if x not in touched]
                    d[k] = [x for x in d[k] if x in touched]
            n = folders.count(d)
            cap = folders.CHANGES_LISTED
            note = (
                "too many files to compare"
                if d.get("too_large")
                else f"{len(d['created'])} created, {len(d['changed'])} changed, {len(d['removed'])} removed"
            )
            n_out = folders.count(outside)
            if n_out:
                note += f"; {n_out} changed outside the job while it ran"
            self.journal.write(
                "harness",
                "folder_changes",
                f"shared/{name}: {note}",
                folder=name,
                path=str(f.path),
                total=n,
                too_large=bool(d.get("too_large")),
                **{k: d[k][:cap] for k in ("created", "changed", "removed")},
                counts={k: len(d[k]) for k in ("created", "changed", "removed")},
                **({"outside": {k: v[:cap] for k, v in outside.items()}} if n_out else {}),
            )
        self._folders_before = {}

    def _context(self, role: Role, tool: str) -> ToolContext:
        return ToolContext(
            self.job_dir,
            role.name,
            timeout=self._tool_timeout(tool),
            shared=self.shared,
            limits=self.limits,
            archived=self.archived,
            carry=self.carry,
            cancelled=lambda: self.gate.stop_requested or self.gate.timed_out(),
            event=lambda action, note, **fields: self.journal.write(role.name, action, note, **fields),
        )

    def _skill_listing(self, names: list[str] | None) -> tuple[str, list[str]]:
        """Name and description of the given skills (every skill with None), within the cap."""
        lib = skills_mod.library(self.home)
        chosen = list(lib.values()) if names is None else [lib[n] for n in names if n in lib]
        return skills_mod.listing(chosen, self.limits.skills_list_chars)

    def _skills_read_tool(self, allowed: list[str] | None) -> Tool:
        home, cap = self.home, self.limits.skill_read_chars

        def handler(args: dict, ctx: ToolContext) -> dict[str, Any]:
            try:
                out = skills_mod.read(home, allowed, args.get("name"), args.get("file"), cap)
            except skills_mod.SkillError as e:
                raise ToolError(str(e)) from None
            ctx.record(
                "skill_read",
                f"{out['skill']}{' ' + out['file'] if out.get('file') else ''}",
                skill=out["skill"],
                file=out.get("file"),
            )
            return out

        return Tool("skills.read", prompts.SKILLS_READ_DESCRIPTION, prompts.SKILLS_READ_PARAMETERS, handler)

    def _close_mcp(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool = None
        close = getattr(self.registry, "close", None)
        if close is not None:
            close()  # the browser, if a role used it

    def _warn_missing_keys(self, role: Role) -> None:
        from ombro import keys
        from ombro.settings import PROVIDER_TOOLS

        for tool in role.tools:
            if tool not in PROVIDER_TOOLS or self.registry.get(tool) is None:
                continue
            provider = self.tool_settings.provider_for(tool)
            key_name = self.tool_settings.key_name(provider) if provider == "firecrawl" else None
            if key_name and not keys.get_key(self.home, key_name):
                self.journal.write(
                    "harness",
                    "warning",
                    f"{tool} uses {provider}, but {key_name} is not set; its calls will fail until it is "
                    f"(`ombro keys set {key_name}`)",
                )

    def request_stop(self, reason: str) -> None:
        self.gate.request_stop(reason)

    # -- the job -----------------------------------------------------------

    def execute(self) -> str:
        jobs.update_job(self.job_dir, status="running", pid=os.getpid(), started_at=utc_now())
        self.journal.write("harness", "job_started", _short(jobs.read_job(self.job_dir)[0].get("brief", "")))
        status, limit_hit = "completed", None
        try:
            self._load()
            self._lead_loop()
        except LimitReached as e:
            status = "stopped" if e.limit == "stopped" else "stopped_by_limit"
            limit_hit = None if e.limit == "stopped" else e.limit
            self.journal.write("harness", "limit_reached", e.detail, limit=e.limit)
        except KeyboardInterrupt:
            status = "stopped"
            self.journal.write("harness", "limit_reached", "interrupted from the keyboard", limit="stopped")
        except (ModelError, SetupError) as e:
            status = "failed"
            self.journal.write("harness", "failed", str(e))
        except Exception as e:  # never leave a job without a report
            status = "failed"
            self.journal.write("harness", "failed", f"internal error: {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)
        self._close_mcp()
        try:
            self._record_folder_changes()
        except OSError as e:
            self.journal.write("harness", "warning", f"the changes in shared folders could not be listed: {e}")
        self._notes(status)
        try:
            self._skill_step(status)
        except Exception as e:  # never leave a job without a report
            self.journal.write(
                "harness", "skills", f"the skill step failed: {type(e).__name__}: {e}", outcome="skipped"
            )
        self._report(status)
        jobs.update_job(
            self.job_dir,
            status=status,
            limit_hit=limit_hit,
            finished_at=utc_now(),
            usage=self.gate.snapshot(),
            **({"outbox": c} if (c := outbox.counts(self.job_dir)) else {}),
        )
        self.journal.write("harness", "job_ended", status)
        try:
            memory.add_index_entry(self.home, self.job_dir)
        except OSError as e:
            print(f"ombro: could not add the job to index.md: {e}", file=sys.stderr)
        return status

    def _save_usage(self) -> None:
        jobs.update_job(self.job_dir, usage=self.gate.snapshot())

    def _model_call(
        self,
        role: Role,
        messages: list[dict],
        tools: list[dict] | None,
        max_output: int | None = None,
        thinking_off: bool = False,
    ) -> ModelReply:
        spec = self.models[role.model]
        est = estimate_tokens(messages, tools)
        if spec.max_output_tokens:
            max_output = min(max_output or spec.max_output_tokens, spec.max_output_tokens)
        max_out, timeout = self.gate.check_model_call(spec.name, est, max_output)
        began = self.gate.clock()
        try:
            client = self._client(spec)
            if thinking_off:
                with _ThinkingOff(client):
                    reply = client.complete(messages, tools=tools, max_output_tokens=max_out, timeout=timeout)
            else:
                reply = client.complete(messages, tools=tools, max_output_tokens=max_out, timeout=timeout)
        except ModelError as e:
            self._count_lost(role, spec.name, e.lost_attempts, est, max_out)
            self.journal.write(role.name, "model_error", str(e))
            if self.gate.timed_out():
                raise LimitReached("job_timeout", "the job reached its time limit during a model call") from None
            raise
        self._count_lost(role, spec.name, reply.lost_attempts, est, max_out)
        usage = reply.usage
        in_tokens = usage.input_tokens if usage.input_tokens or not usage.estimated else est
        out_tokens = min(usage.output_tokens, max_out) if usage.estimated else usage.output_tokens
        thought = min(getattr(usage, "reasoning", 0) or 0, out_tokens)
        self.gate.record_model_call(
            spec.name, in_tokens, out_tokens, estimated=usage.estimated, cached=usage.cached_input, reasoning=thought
        )
        seconds = round(max(0.0, self.gate.clock() - began), 1)
        self.journal.write(
            role.name,
            "model_call",
            f"{spec.name}: {in_tokens:,} in"
            + (f" ({usage.cached_input:,} cached)" if usage.cached_input else "")
            + f", {out_tokens:,} out"
            + (
                f" ({'about ' if getattr(usage, 'reasoning_estimated', False) else ''}{thought:,} thinking)"
                if thought
                else ""
            )
            + f", {seconds:g}s",
            model=spec.name,
            **({"provider_model": spec.model} if getattr(spec, "model", None) and spec.model != spec.name else {}),
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            seconds=seconds,
            **({"reasoning_tokens": thought} if thought else {}),
            **({"cached_input_tokens": usage.cached_input} if usage.cached_input else {}),
            **({"finish_reason": reply.finish_reason} if reply.finish_reason else {}),
        )
        client = self._client(spec)
        learned = getattr(client, "learned_max", None)
        if learned and spec.name not in self._learned:
            self._learned.add(spec.name)
            self.journal.write(
                "harness",
                "warning",
                f"model {spec.name} allows at most {learned:,} output tokens in one reply, fewer than "
                f"max_output_tokens ({max_out:,}), so Ombro used {learned:,}. Add "
                f"`max_output_tokens: {learned}` under {spec.name} in models.md to say so up front.",
                model=spec.name,
                learned_max=learned,
            )
        if learned:
            max_out = min(max_out, learned)
        reply.cut_off = cut_off(reply, max_out)
        self._save_usage()
        return reply

    def _count_lost(self, role: Role, model: str, n: int, est: int, max_out: int) -> None:
        if n:
            tokens = self.gate.record_lost_calls(model, n, est, max_out)
            self.journal.write(
                role.name,
                "model_call_lost",
                f"{model}: {n} attempt(s) timed out and were tried again; counted at their worst "
                f"case, {tokens:,} tokens (estimated)",
                model=model,
                attempts=n,
                tokens=tokens,
            )
            self._save_usage()

    # -- Lead --------------------------------------------------------------

    def _lead_loop(self) -> None:
        assert self.lead is not None
        messages: list[dict] = [
            {
                "role": "system",
                "content": prompts.lead_system(
                    self.lead,
                    self.instructions,
                    self.roles,
                    self.limits,
                    self.today,
                    memory=memory.lead_context(self.home, self.crews, self.limits),
                    shared=self._shared_for_prompts(),
                    spec_roles=getattr(self, "spec_roles", []),
                    skills=self._skill_listing(None)[0],
                ),
            },
            {"role": "user", "content": self.spec_body},
        ]
        self.gate.begin_scope(self.lead.name, self.lead.max_steps)
        # skills__read is offered only when there is a skill to read, so an empty library costs nothing.
        lead_tools = (
            prompts.LEAD_TOOLS
            + prompts.LEAD_READ_TOOLS
            + ([prompts.SKILLS_READ_TOOL] if skills_mod.library(self.home) else [])
        )
        if self.live:
            from ombro import livejob

            messages[0]["content"] += livejob.LIVE_NOTE
            lead_tools = lead_tools + [livejob.ASK_TOOL]
        idle = 0
        while not self.finished:
            reply = self._model_call(self.lead, messages, lead_tools)
            messages.append(reply.as_message())
            if not reply.tool_calls:
                idle += 1
                if idle >= 2:
                    self.journal.write(
                        "lead",
                        "finish",
                        "the Lead replied twice without calling a tool; treated as finish",
                        outcome="done",
                        summary=_short(reply.content or "", 500),
                    )
                    return
                messages.append({"role": "user", "content": prompts.LEAD_NUDGE})
                continue
            idle = 0
            for tc in reply.tool_calls:
                if self.finished:
                    messages.append(_tool_message(tc.id, {"error": "ignored: the job is finishing"}))
                    continue
                out = self._lead_tool(tc)
                if tc.name == "brief" and isinstance(out, dict):
                    out = {**out, "job_so_far": self._so_far()}
                src = None
                if tc.name in ("brief", "memory__search", "workspace__read") and not (
                    isinstance(out, dict) and set(out) == {"error"}
                ):
                    who = (tc.arguments or {}).get("role") if isinstance(tc.arguments, dict) else None
                    src = (
                        f"the result {who} returned"
                        if tc.name == "brief" and who
                        else material.source(
                            "workspace.read" if tc.name == "workspace__read" else "memory.search", tc.arguments
                        )
                    )
                    self._flag("lead", src, out)
                    if tc.name != "brief" and carry_mod.is_private(
                        "workspace.read" if tc.name == "workspace__read" else "memory.search", args=tc.arguments
                    ):
                        self.carry.saw(src, out)  # the Lead may pass it on in a brief
                messages.append(_tool_message(tc.id, out, src))

    def _so_far(self) -> str:
        """Time and rounds left, for the Lead after each brief, so it plans its last steps to fit."""
        from ombro.settings import format_duration

        left = max(0.0, self.limits.job_timeout - self.gate.elapsed())
        rounds = max(0, self.limits.max_rounds - self.gate.usage.rounds)
        line = (
            f"{format_duration(left)} of the job's {format_duration(self.limits.job_timeout)} left, "
            f"and {rounds} of {self.limits.max_rounds} briefs."
        )
        if left < min(900, self.limits.job_timeout * 0.2) or rounds <= 1:
            line += (
                " Little is left: finish with what you have. Brief only what the report needs, keep each "
                "brief small, and call finish in good time; skip extra checks."
            )
        return line

    def _ask_you(self, args: dict) -> dict[str, Any]:
        """A live job's Lead asks the user, who is watching in the chat, and waits for the answer."""
        from ombro import livejob

        question = " ".join(str(args.get("question") or "").split())
        if not question:
            return {"error": "give the question"}
        raw = args.get("options") or []
        options = [" ".join(str(o).split()) for o in (raw if isinstance(raw, list) else [])][:4]
        self.journal.write("lead", "asked", _short(question, 300), question=question, options=options)
        got = livejob.ask(
            self.job_dir,
            question,
            options,
            cancelled=lambda: self.gate.stop_requested or self.gate.timed_out(),
            clock=self.clock,
        )
        if got is None:
            self.journal.write("harness", "unanswered", "no answer came; the Lead carries on without one")
            return {"answer": None, "note": livejob.NO_ANSWER}
        self.journal.write("you", "answered", _short(got, 300), answer=got)
        return {"answer": got}

    def _lead_tool(self, tc) -> dict[str, Any]:
        searching = tc.name == "memory__search"
        reading = tc.name == "skills__read"
        name = "memory.search" if searching else "skills.read" if reading else tc.name
        try:
            # The Lead's own tools are bounded by max_rounds; memory.search has a per-job cap.
            self.gate.check_tool_call(
                "lead", name, _canonical(tc.arguments, tc.raw_arguments), capped=searching or reading
            )
        except ToolRefused as e:
            self.journal.write("lead", "tool_refused", f"{tc.name}: {e.detail}", limit=e.limit)
            return {"error": e.detail}
        finally:
            self._save_usage()
        if tc.arguments is None:
            self.journal.write("lead", "tool_refused", f"{tc.name}: arguments were not a JSON object")
            return {"error": "the arguments were not a JSON object"}
        if searching:
            return self._search(tc.arguments)
        if reading:
            return self._lead_read_skill(tc.arguments)
        if tc.name in ("workspace__read", "workspace__list", "workspace__edit"):
            return self._lead_workspace(tc.name.replace("__", "."), tc.arguments)
        if tc.name == "brief":
            return self._brief(tc.arguments)
        if tc.name == "ask_you" and self.live:
            return self._ask_you(tc.arguments)
        if tc.name == "finish":
            outcome = tc.arguments.get("outcome")
            if outcome not in ("done", "cannot_proceed"):
                outcome = "done"
            summary = str(tc.arguments.get("summary") or "").strip()
            self.journal.write("lead", "finish", _short(summary), outcome=outcome, summary=summary)
            self.finished = True
            return {"ok": True}
        self.journal.write("lead", "tool_refused", f"unknown tool {tc.name!r}")
        return {
            "error": f"unknown tool {tc.name!r}; use brief, memory__search, workspace__read, skills__read or finish"
        }

    # 23 Nov 2026: 2,000 until a job sent five small fixes back to a role as a whole brief (five minutes).
    LEAD_EDIT_MAX_CHARS = 4_000  # a correction or a reworded paragraph; new writing is a role's work

    def _lead_workspace(self, name: str, args: dict) -> dict[str, Any]:
        """workspace.read, .list or .edit for the Lead: this job's files and the folders shared with it."""
        path = str(args.get("path") or "").strip().lstrip("/")
        if path and path.split("/", 1)[0] not in ("workspace", "scratch", "shared"):
            return {"error": "the Lead reaches only this job's files (workspace/, scratch/) and shared/<name>/"}
        if name == "workspace.edit":
            return self._lead_edit(path, args)
        tool = self.registry.get(name)
        if tool is None or tool.handler is None:
            return {"error": f"{name} is not available"}
        try:
            out = tool.handler(
                {**args, "path": path} if path else {k: v for k, v in args.items() if k != "path"},
                self._context(self.lead, name),
            )
        except ToolError as e:
            self.journal.write("lead", "tool_error", f"{name}: {_short(str(e))}", tool=name)
            return {"error": str(e)}
        self.journal.write("lead", "tool_call", f"{name} {path or '(all)'}", tool=name)
        return out

    def _lead_edit(self, path: str, args: dict) -> dict[str, Any]:
        """Exact replacements in a file, for corrections the Lead makes itself instead of briefing a role."""
        from ombro.native import edit

        if not path:
            return {"error": "give path, and old and new (or edits: a list of them)"}
        if self.registry.get("workspace.write") is None:
            return {"error": "workspace.edit is not available"}
        ctx = self._context(self.lead, "workspace.write")
        watch = self._folder_manifests() if self._folders_before else None
        try:
            out = edit({**args, "path": path}, ctx, max_chars=self.LEAD_EDIT_MAX_CHARS)
        except ToolError as e:
            self.journal.write("lead", "tool_error", f"workspace.edit: {_short(str(e))}", tool="workspace.edit")
            return {"error": str(e)}
        finally:
            if watch is not None:
                self._note_touched(watch)
        changes = args.get("edits") if isinstance(args.get("edits"), list) else [args]
        first = changes[0] if changes and isinstance(changes[0], dict) else {}
        self.journal.write(
            "lead",
            "tool_call",
            f"workspace.edit {path}: {_short(str(first.get('old') or ''), 80)} -> "
            f"{_short(str(first.get('new') or ''), 80)}" + (f" (+{len(changes) - 1} more)" if len(changes) > 1 else ""),
            tool="workspace.edit",
            path=path,
        )
        return out

    def _lead_read_skill(self, args: dict) -> dict[str, Any]:
        try:
            out = skills_mod.read(self.home, None, args.get("name"), args.get("file"), self.limits.skill_read_chars)
        except skills_mod.SkillError as e:
            self.journal.write("lead", "tool_error", f"skills.read: {e}", tool="skills.read")
            return {"error": str(e)}
        self.journal.write(
            "lead",
            "skill_read",
            f"{out['skill']}{' ' + out['file'] if out.get('file') else ''}",
            tool="skills.read",
            skill=out["skill"],
            file=out.get("file"),
        )
        return out

    def _search(self, args: dict) -> dict[str, Any]:
        query = str(args.get("query") or "")
        out = memory.search(
            self.home,
            self.crews,  # a job sees only the memory of the crews its spec names
            query,
            job_id=str(args.get("job_id") or "") or None,
            exclude=self.job_dir,
            max_chars=self.limits.memory_search_max_chars,
        )
        found = ", ".join(sorted({r["job_id"] for r in out["results"]})) or "nothing"
        self.journal.write(
            "lead",
            "memory_search",
            f"{_short(query, 100)}: {found}",
            tool="memory.search",
            query=query,
            results=len(out["results"]),
        )
        return out

    def _brief(self, args: dict) -> dict[str, Any]:
        name = str(args.get("role") or "").strip()
        task = str(args.get("task") or "").strip()
        role, why = resolve_role(self.roles, name)
        if role is None:
            self.journal.write("lead", "tool_refused", f"brief: {why}")
            return {"error": why}
        name = role.name
        if not task:
            self.journal.write("lead", "tool_refused", "brief: empty task")
            return {"error": "the task is empty"}
        self.gate.check_round()
        n = self.gate.record_round()
        self.journal.write(
            "lead", "briefed", f"{name}: {_short(task)}", target=name, crew=role.crew, task=task, round=n
        )
        outcome = self._run_role(role, task)
        assert self.lead is not None
        self.gate.begin_scope(self.lead.name, self.lead.max_steps)
        return outcome

    # -- roles -------------------------------------------------------------

    def _role_tools(self, role: Role) -> tuple[list[dict], dict[str, Tool]]:
        names = list(role.tools)
        if "workspace.write" in names:
            # A role that can write can change part of a file (never rewrite it all for a sentence: 23 Nov 2026)
            # and set a file aside, never delete it.
            names += [n for n in ("workspace.edit", "workspace.set_aside") if n not in names]
        tools = [t for name in names if (t := self.registry.get(name)) is not None]
        have = skills_mod.names_for(self.home, role)[0] if role.skills or role.all_skills else []
        if have:
            tools.append(self._skills_read_tool(have))
        return [t.schema() for t in tools], {t.wire_name: t for t in tools}

    def _flag(self, who: str, src: str, out: Any) -> None:
        """Record the signs of injected instructions in some material, for the report."""
        found = material.flags(out)
        if found:
            self.journal.write(
                "harness", "material_flag", f"{src}: {', '.join(found)}", source=src, signs=found, reader=who
            )

    def _run_role(self, role: Role, task: str) -> dict[str, Any]:
        self.tainted = False  # set once the brief reads material; external calls are then staged
        schemas, by_wire = self._role_tools(role)
        skills, _ = self._skill_listing(skills_mod.names_for(self.home, role)[0])
        messages: list[dict] = [
            {
                "role": "system",
                "content": prompts.role_system(
                    role, skills, [t.name for t in by_wire.values()], self.today, shared=self._shared_for_prompts()
                ),
            },
            {"role": "user", "content": prompts.role_task(task)},
        ]
        self.gate.begin_scope(role.name, role.max_steps, brief=True)
        before = self._job_files()
        try:
            return self._role_loop(role, messages, schemas, by_wire)
        except BriefLimit as e:
            # The brief ends; the job goes on and the Lead decides what to do.
            reason = f"stopped by {e.limit}: {e.detail}"
            self.journal.write(role.name, "returned", reason, status="stopped_by_limit", limit=e.limit, reason=reason)
            self._save_usage()
            after = self._job_files()
            wrote = [f"{p} ({after[p][1]:,} bytes)" for p in sorted(after) if before.get(p) != after[p]]
            return {
                "status": "stopped_by_limit",
                "role": role.name,
                "limit": e.limit,
                "reason": e.detail,
                "files_written": wrote,
                "note": "The role did not return a result. The files it wrote in this brief are listed and stay in "
                "the workspace: read them with workspace__read before you decide. Brief it to finish only what is "
                "missing, or finish.",
            }

    def _job_files(self) -> dict[str, tuple[int, int]]:
        """This job's own files (workspace/ and scratch/) as path: (mtime_ns, size)."""
        out: dict[str, tuple[int, int]] = {}
        for area in ("workspace", "scratch"):
            top = self.job_dir / area
            for f in top.rglob("*") if top.is_dir() else []:
                if f.is_file() and not f.is_symlink():
                    st = f.stat()
                    out[f.relative_to(self.job_dir).as_posix()] = (st.st_mtime_ns, st.st_size)
        return out

    def _role_loop(self, role: Role, messages: list[dict], schemas: list[dict], by_wire: dict[str, Tool]) -> dict:
        invalid = 0
        cut = 0
        think_less = False  # the reply before was cut off while still thinking: the next goes without
        # Outside services' tools are listed by name in tools__load and sent in full only once loaded.
        deferred = {w: t for w, t in by_wire.items() if is_deferred(t)}
        base = [s for s in (schemas or []) if s["function"]["name"] not in deferred]
        base += ([load_tool_schema(list(deferred.values()))] if deferred else []) + [contract.SUBMIT_TOOL]
        loaded: set[str] = set()
        while True:
            tools = base + [deferred[w].schema() for w in sorted(loaded)]
            g = self.gate
            last = g.scope_max is not None and g.scope_max > 1 and g.scope_steps == g.scope_max - 1
            if last:
                # The last call the brief has: ask for the result, so work done is reported, not lost. The tools
                # stay as they were, so the provider's cache still holds; only writing and the result are run.
                messages.append({"role": "user", "content": contract.LAST_CALL_INSTRUCTION})
            reply = self._model_call(role, messages, tools, thinking_off=think_less)
            think_less = False
            messages.append(reply.as_message())
            if getattr(reply, "cut_off", False):
                # Cut off at the output limit: nothing in the reply can be used. Say so plainly, so the role
                # writes less at once, rather than calling it an invalid result (journal of the first real job).
                cut += 1
                self.journal.write(
                    role.name,
                    "reply_cut_off",
                    f"the reply reached the output limit "
                    f"({self.gate.limits.max_output_tokens:,} tokens) and was cut off",
                    attempt=cut,
                )
                for tc in reply.tool_calls:
                    messages.append(
                        _tool_message(tc.id, {"error": "not run: your reply was cut off before this call was complete"})
                    )
                if cut >= 3:
                    reason = "its replies were cut off at the output limit three times"
                    self.journal.write(role.name, "returned", reason, status="failed", reason=reason)
                    return {
                        "status": "failed",
                        "role": role.name,
                        "reason": reason + ". Brief it with a smaller "
                        "piece of work, or one that writes long text to a file in parts.",
                    }
                if thought_out(reply):
                    # Thinking more would only run out again: the next try goes without it, once.
                    think_less = True
                    self.journal.write(
                        role.name, "thinking_off", "the reply ran out while thinking, so the next goes without"
                    )
                messages.append({"role": "user", "content": contract.CUT_OFF_INSTRUCTION})
                continue
            submitted = [tc for tc in reply.tool_calls if tc.name == contract.SUBMIT_NAME]
            if submitted:
                tc = submitted[0]
                for other in reply.tool_calls:
                    if other is tc:
                        continue
                    if last and by_wire.get(other.name) and by_wire[other.name].name in LAST_CALL_TOOLS:
                        # Writing what it drafted, in the same reply as its result: run it first.
                        messages.append(_tool_message(other.id, self._role_tool(role, other, by_wire)))
                    else:
                        messages.append(_tool_message(other.id, {"error": "not run: submit_result ends the brief"}))
                result, errors = contract.validate_value(tc.arguments)
                if result is not None:
                    messages.append(_tool_message(tc.id, {"ok": True}))
                    self.journal.write(role.name, "returned", _short(result["summary"]), status="ok", result=result)
                    if result.get("journal_note"):
                        self.journal.write(role.name, "note", _short(result["journal_note"], 300))
                    return {"status": "ok", "result": result}
                messages.append(_tool_message(tc.id, {"error": "not accepted: " + "; ".join(errors)}))
                invalid += 1
                self.journal.write(role.name, "result_invalid", _short("; ".join(errors), 300), attempt=invalid)
                if invalid >= 2:
                    reason = "returned an invalid result twice: " + _short("; ".join(errors), 400)
                    self.journal.write(role.name, "returned", reason, status="failed", reason=reason)
                    return {"status": "failed", "role": role.name, "reason": reason}
                continue
            if reply.tool_calls:
                for tc in reply.tool_calls:
                    if last and not (by_wire.get(tc.name) and by_wire[tc.name].name in LAST_CALL_TOOLS):
                        messages.append(
                            _tool_message(
                                tc.id,
                                {
                                    "error": "not run: that was your last model call, "
                                    "when only writing files and submit_result are run"
                                },
                            )
                        )
                        continue
                    if tc.name == LOAD_NAME and deferred:
                        messages.append(_tool_message(tc.id, self._load_tools(role, tc, deferred, loaded)))
                        continue
                    out = self._role_tool(role, tc, by_wire)
                    tool = by_wire.get(tc.name)
                    name = tool.name if tool else tc.name
                    src = None
                    harness_reply = isinstance(out, dict) and (
                        set(out) == {"error"}
                        or (
                            tool is not None
                            and tool.outward
                            and tool.effect != "read-only"
                            and out.get("status") != "done"
                        )
                    )  # staged, uncertain or refused: Ombro's words
                    if tool is not None and name not in NOT_MATERIAL and not harness_reply:
                        src = material.source(name, tc.arguments)
                        self.tainted = True  # this brief has read material from outside
                        self._flag(role.name, src, out)
                        if carry_mod.is_private(
                            name, outward=tool.outward, args=tc.arguments, shared=bool(self.shared)
                        ):
                            self.carry.saw(src, out)
                    messages.append(_tool_message(tc.id, out, src))
                continue
            result, errors = contract.validate_text(reply.content)
            if result is not None:
                self.journal.write(role.name, "returned", _short(result["summary"]), status="ok", result=result)
                if result.get("journal_note"):
                    self.journal.write(role.name, "note", _short(result["journal_note"], 300))
                return {"status": "ok", "result": result}
            invalid += 1
            self.journal.write(role.name, "result_invalid", _short("; ".join(errors), 300), attempt=invalid)
            if invalid >= 2:
                reason = "returned an invalid result twice: " + _short("; ".join(errors), 400)
                self.journal.write(role.name, "returned", reason, status="failed", reason=reason)
                return {"status": "failed", "role": role.name, "reason": reason}
            messages.append({"role": "user", "content": contract.retry_instruction(errors)})

    def _load_tools(self, role: Role, tc, deferred: dict[str, Tool], loaded: set[str]) -> dict:
        try:
            self.gate.check_tool_call(
                role.name, LOAD_NAME, _canonical(tc.arguments, tc.raw_arguments), brief=True, capped=False
            )
        finally:
            self._save_usage()
        out = load_tools(tc.arguments, deferred, loaded)
        if out.get("loaded"):
            self.journal.write(
                role.name,
                "tools_loaded",
                ", ".join(deferred[w].name for w in out["loaded"]),
                tool=LOAD_NAME,
                loaded=[deferred[w].name for w in out["loaded"]],
            )
        return out

    def _role_tool(self, role: Role, tc, by_wire: dict[str, Tool]) -> Any:
        tool = by_wire.get(tc.name)
        name = tool.name if tool else tc.name
        try:
            self.gate.check_tool_call(role.name, name, _canonical(tc.arguments, tc.raw_arguments), brief=True)
        except ToolRefused as e:
            self.journal.write(role.name, "tool_refused", f"{name}: {e.detail}", limit=e.limit, tool=name)
            return {"error": f"{e.detail}. Carry on without it, or return what you have."}
        finally:
            self._save_usage()
        if tool is None:
            self.journal.write(role.name, "tool_refused", f"{name}: not one of this role's tools")
            return {"error": f"{name} is not one of your tools"}
        if tc.arguments is None:
            self.journal.write(role.name, "tool_refused", f"{name}: arguments were not a JSON object")
            return {"error": "the arguments were not a JSON object"}
        if tool.outward and outward.refused(name, self.tool_settings):
            # Checked before the class: refuse holds for read-only tools too.
            self.journal.write(
                role.name, "tool_refused", f"{name}: refused by tools.md", tool=name, permission="refuse"
            )
            return {
                "error": f"{name} is refused by the user's settings. Carry on without it, and say so in did_not_do."
            }
        if tool.outward and tool.effect != "read-only":
            return self._outward_call(role, tool, tc.arguments)
        ctx = self._context(role, name)
        args_note = _short(_canonical(tc.arguments, ""), 120)
        watch = self._folder_manifests() if name in self.WRITING_TOOLS and self._folders_before else None
        try:
            out = tool.handler(tc.arguments, ctx) if tool.handler else None
        except NotGranted as e:
            self.journal.write(
                role.name,
                "tool_error",
                f"{name}: {_short(e.path, 200)} is not in a shared folder",
                tool=name,
                not_shared=e.path,
            )
            return {"error": str(e)}
        except ToolError as e:
            self.journal.write(role.name, "tool_error", f"{name} {args_note}: {_short(str(e))}", tool=name)
            return _error(tool, e)
        except Exception as e:
            self.journal.write(role.name, "tool_error", f"{name}: {type(e).__name__}: {_short(str(e))}", tool=name)
            return {"error": f"{type(e).__name__}: {e}"}
        finally:
            if watch is not None:
                self._note_touched(watch)
        self.journal.write(role.name, "tool_call", f"{name} {args_note}", tool=name)
        if name in self.WRITING_TOOLS:
            self.gate.forget_reads(role.name)  # reading a changed file again is not a loop
        return out if out is not None else {"ok": True}

    def _outward_call(self, role: Role, tool: Tool, arguments: dict) -> dict[str, Any]:
        """A call that changes or sends something outside: allowed, staged or refused,
        as the job's permissions say. Read calls go the ordinary way."""
        name = tool.name
        args = dict(arguments)
        reason = str(args.pop(tool.reason_key, "") or "").strip() if tool.reason_key else ""
        if tool.prepare is not None:  # e.g. browser.submit: the page's refs become its address and selectors
            try:
                args = tool.prepare(args, self._context(role, name))
            except ToolError as e:
                self.journal.write(role.name, "tool_refused", f"{name}: {_short(str(e), 200)}", tool=name)
                return {"error": str(e)}
            except Exception as e:  # noqa: BLE001 - shown to the role, never a crash
                self.journal.write(role.name, "tool_error", f"{name}: {type(e).__name__}: {_short(str(e))}", tool=name)
                return {"error": f"{type(e).__name__}: {e}"}
        # A tool may say what this one call is (browser.submit: a form of filters is internal, one with the
        # user's details external); otherwise its class.
        effect = str(args.pop("_effect", "") or tool.effect or "external")
        carried = bool(args.pop("_carry", False))  # would carry private material to a site: always asks
        effect = self.scope.effect(name, args, effect)  # deleting in what this job made is its own work
        decision = outward.decide(self.outward, name, effect, self.tool_settings)
        tainted_stage = False
        if (
            decision == "allow"
            and effect == "external"
            and (carried or (getattr(self, "tainted", False) and name not in ((self.outward or {}).get("allow") or [])))
        ):
            # Material taints a brief: after reading it, an external call waits for the user, even
            # under auto, unless the spec allows this tool by name.
            decision, tainted_stage = "stage", True
        elif (
            decision == "allow"
            and effect == "internal"
            and getattr(self, "tainted", False)
            and name not in ((self.outward or {}).get("allow") or [])
            and outward.held_after_reading(name, args)
            and not self.scope.own(args)
        ):
            # An internal change still runs after reading, but not one that deletes something or changes
            # who can see it: an injected "trash it" or "make it public" waits for the user.
            decision, tainted_stage = "stage", True
        if decision == "refuse":
            self.journal.write(
                role.name, "tool_refused", f"{name}: refused by tools.md", tool=name, permission="refuse"
            )
            return {
                "error": f"{name} is refused by the user's settings. Carry on without it, and say so in did_not_do."
            }
        if not reason:
            self.journal.write(role.name, "tool_refused", f"{name}: no reason given", tool=name)
            return {"error": f"give `{tool.reason_key}`: one sentence on why this action is needed"}
        problem = outward.validation_error(tool.input_schema, args)
        if problem:
            self.journal.write(role.name, "tool_refused", f"{name}: {problem}", tool=name)
            return {"error": problem}
        args_note = _short(_canonical(args, ""), 120)
        key = outward.Scope.key(name, args)
        if decision == "stage" and key in self.scope.waiting:
            # One approval per thing worked on: later calls on it are not staged one by one.
            ref = self.scope.waiting[key]
            self.journal.write(
                role.name,
                "staged_grouped",
                f"{name}: waits with {ref}",
                tool=name,
                ref=ref,
                reason=reason,
                arguments=args,
            )
            return {
                "status": "not_staged",
                "waits_with": ref,
                "note": f"An earlier call on this same item ({ref}) already waits for the user's approval, "
                "so this one was not staged and has not happened. Stage nothing more on it. In your summary, "
                "list what is still to do on it once that is approved, as not done.",
            }
        if decision == "stage":
            try:
                action, duplicate = outbox.stage(
                    self.job_dir,
                    tool=name,
                    effect=effect,
                    arguments=args,
                    reason=reason,
                    role=role.name,
                    input_schema=tool.input_schema,
                )
            except outbox.OutboxBusy as e:
                self.journal.write(role.name, "tool_error", f"{name}: {e}", tool=name)
                return {
                    "error": "The outbox is busy with a decision the user is making, so this was not staged. "
                    "Try it once more later; if it fails again, say so in did_not_do."
                }
            rel = f"outbox/{action.path.name}"
            if duplicate:
                self.journal.write(
                    role.name, "staged_duplicate", f"{name}: same as {action.ref}", tool=name, ref=action.ref
                )
                return {
                    "status": "already_staged",
                    "outbox": rel,
                    "note": "An identical action is already staged. Do not stage it again.",
                }
            self.journal.write(
                role.name,
                "staged",
                f"{action.ref} {name} {args_note}",
                tool=name,
                ref=action.ref,
                path=rel,
                effect=effect,
                reason=reason,
                arguments=args,
                **({"tainted": True} if tainted_stage else {}),
            )
            if key:
                self.scope.waiting[key] = action.ref
            return {
                "status": "staged",
                "outbox": rel,
                "ref": action.ref,
                "note": "Not done: it waits in Approvals for the user's decision, and this job does not wait "
                "for it. Do not stage it again, and do not stage further calls that need its result. Finish "
                "everything that does not need it; in your summary say it waits for approval and list, as "
                "not done, each step that needed it. Approving it later runs only this one call.",
            }
        # allowed: it runs now
        ctx = self._context(role, name)
        try:
            out = tool.handler(args, ctx) if tool.handler else {"ok": True}
        except McpCallLost as e:
            # Sent, but no answer: it may have happened. Retrying could do it twice.
            self.journal.write(
                role.name,
                "outward_uncertain",
                f"{name} {args_note}: {_short(str(e))}",
                tool=name,
                reason=reason,
                arguments=args,
            )
            return {
                "status": "uncertain",
                "error": f"{e}. It may or may not have happened. Do not try it "
                "again; say in your summary that it is uncertain, so the user can check.",
            }
        except ToolError as e:
            self.journal.write(
                role.name,
                "outward_error",
                f"{name} {args_note}: {_short(str(e))}",
                tool=name,
                reason=reason,
                arguments=args,
            )
            return _error(tool, e)
        self.journal.write(
            role.name, "outward_done", f"{name} {args_note}", tool=name, effect=effect, reason=reason, arguments=args
        )
        self.scope.done(name, args, out)
        return {"status": "done", **(out if isinstance(out, dict) else {"result": out})}

    def _tool_timeout(self, tool: str = "") -> float:
        from ombro.native import CALL_TIMEOUT

        cap = self.limits.code_timeout if tool == "code.run" else CALL_TIMEOUT
        return max(0.5, min(cap, self.limits.job_timeout - self.gate.elapsed()))

    # -- notes -------------------------------------------------------------

    NOTES_ATTEMPTS = 2

    def _notes(self, status: str) -> None:
        """The Lead rewrites its part of notes.md for each crew whose roles it briefed, one crew
        per call, before the report. Each is recorded as a `notes` event that the report shows."""
        events = journal_mod.read(self.job_dir / "journal.jsonl")
        if self.lead is None or self.lead.model not in self.models:
            self.journal.write("harness", "notes", "the Lead could not be loaded", outcome="skipped")
            return
        briefed = {str(e.get("crew") or "") for e in events if e.get("action") == "briefed"}
        crews = [c for c in self.crews if c in briefed]
        if not crews:
            self.journal.write(
                "harness", "notes", "no role was briefed, so this job had nothing new to teach", outcome="skipped"
            )
            return
        self.gate.enter_report_phase("notes")
        for crew in crews:
            self._notes_for(crew, status)

    def _notes_for(self, crew: str, status: str) -> None:
        def record(outcome: str, note: str, **extra: Any) -> None:
            self.journal.write(
                "lead" if outcome in ("changed", "kept") else "harness",
                "notes",
                f"{crew}: {note}",
                outcome=outcome,
                crew=crew,
                **extra,
            )

        cap = self.limits.memory_notes_max_chars
        try:
            current = memory.read_notes(self.home, crew)
        except OSError as e:
            return record("skipped", f"notes.md could not be read: {e}")
        room = cap - memory.with_from_jobs(current, "").size
        facts = report.gather_facts(self.home, self.job_dir, status)
        messages: list[dict] = [
            {"role": "system", "content": prompts.notes_system(self.lead, self.instructions, self.today)},
            {
                "role": "user",
                "content": prompts.notes_input(
                    current.from_you, current.from_jobs, cap, room, report.narrative_input(facts), crew
                ),
            },
        ]
        # Keep enough of the reserve for the report: estimate its call before each notes call.
        report_worst = (
            estimate_tokens([{"role": "user", "content": report.narrative_input(facts)}])
            + 2_000
            + self.limits.report_max_output_tokens
        )
        max_out = min(self.limits.max_output_tokens, cap // 3 + 500)
        refused = 0
        for _ in range(self.NOTES_ATTEMPTS + 1):
            worst = estimate_tokens(messages, prompts.NOTES_TOOLS) + max_out
            if (
                self.limits.max_tokens is not None
                and self.gate.usage.counted_tokens + worst + report_worst > self.limits.max_tokens
            ):
                note = "not enough of the token limit was left to rewrite the notes and still write the report"
                return record("refused" if refused else "skipped", note)
            try:
                # Keeping lessons needs no deliberation: thinking spent a whole notes call (23 Nov 2026).
                reply = self._model_call(
                    self.lead, messages, prompts.NOTES_TOOLS, max_output=max_out, thinking_off=True
                )
            except (LimitReached, ModelError) as e:
                return record("refused" if refused else "skipped", f"the notes call could not be made: {e}")
            except KeyboardInterrupt:
                return record("skipped", "the notes call was interrupted")
            messages.append(reply.as_message())
            tc = reply.tool_calls[0] if reply.tool_calls else None
            for extra in reply.tool_calls[1:]:
                messages.append(_tool_message(extra.id, {"error": "one call at a time; ignored"}))
            if tc is None or tc.name == "keep_notes":
                why = str((tc.arguments or {}).get("reason") or "") if tc else "the Lead replied without a tool"
                if tc:
                    messages.append(_tool_message(tc.id, {"ok": True}))
                return record("kept", _short(why or "nothing new to keep", 300))
            if tc.name not in ("memory__write_notes", "memory__edit_notes") or tc.arguments is None:
                messages.append(
                    _tool_message(tc.id, {"error": "use memory__edit_notes, memory__write_notes or keep_notes"})
                )
                refused += 1
                if refused >= self.NOTES_ATTEMPTS:
                    break
                continue
            try:
                a = tc.arguments
                if tc.name == "memory__edit_notes":
                    lists = {k: a.get(k) for k in ("add", "drop", "replace")}
                    if not any(isinstance(v, list) and v for v in lists.values()):
                        raise memory.NotesError("give add, replace or drop, or call keep_notes")
                    old, new = memory.edit_from_jobs(
                        self.home, crew, cap, **{k: v if isinstance(v, list) else None for k, v in lists.items()}
                    )
                else:
                    old, new = memory.replace_from_jobs(self.home, crew, a.get("from_jobs", ""), cap)
            except memory.NotesError as e:
                refused += 1
                self.journal.write("lead", "notes_refused", _short(str(e), 300), attempt=refused, crew=crew)
                messages.append(_tool_message(tc.id, {"error": str(e)}))
                last_error = str(e)
                if refused >= self.NOTES_ATTEMPTS:
                    return record("refused", f"the Lead's change was refused twice: {_short(last_error, 300)}")
                continue
            added, removed = memory.notes_diff(old, new)
            messages.append(_tool_message(tc.id, {"ok": True}))
            return record(
                "changed",
                f"{len(added)} added, {len(removed)} removed; {new.size:,} of {cap:,} characters",
                added=added,
                removed=removed,
                size=new.size,
                cap=cap,
            )
        return record("refused", "the Lead did not write the notes in the form required")

    # -- skills ------------------------------------------------------------

    SKILL_CALLS = 4  # the proposal, or none, plus a few skill reads

    def _skill_step(self, status: str) -> None:
        """The Lead may propose one skill, into the outbox, after a completed job in which a
        role returned a result. Recorded as a `skills` event."""

        def record(outcome: str, note: str, **extra: Any) -> None:
            self.journal.write(
                "lead" if outcome in ("proposed", "none") else "harness", "skills", note, outcome=outcome, **extra
            )

        events = journal_mod.read(self.job_dir / "journal.jsonl")
        if status != "completed":
            return record("skipped", "the job did not complete, so no skill was considered", quiet=True)
        if not any(e.get("action") == "returned" and e.get("status") == "ok" for e in events):
            return record("skipped", "no role returned a result, so no skill was considered", quiet=True)
        if self.lead is None or self.lead.model not in self.models:
            return record("skipped", "the Lead could not be loaded")
        waiting = outbox.skills_waiting(self.home)
        if waiting >= self.limits.skill_proposals_waiting:
            return record(
                "skipped",
                f"{waiting} skill proposals already wait for you, the most allowed; "
                "no more are made until you decide some",
            )
        # A skill needs earlier jobs of the same kind (skills.MIN_JOBS); with none, there is nothing to ask.
        self._skill_earlier = skills_mod.earlier_jobs(self.home, self.job_id)
        if len(self._skill_earlier) < skills_mod.MIN_JOBS - 1:
            return record(
                "skipped", "no earlier completed job to compare this one with, so no skill was considered", quiet=True
            )
        self.gate.enter_report_phase("skills")
        facts = report.gather_facts(self.home, self.job_dir, status)
        cap = self.limits.skill_max_chars
        messages: list[dict] = [
            {"role": "system", "content": prompts.skill_system(self.lead, self.today)},
            {
                "role": "user",
                "content": prompts.skill_input(
                    self._skill_listing(None)[0],
                    list(self.roles),
                    cap,
                    report.narrative_input(facts),
                    waiting="\n".join(
                        f"- {n}: {d or '(no description)'}" for n, d, _ in outbox.skill_proposals_waiting(self.home)
                    ),
                    declined=skills_mod.declined_listing(self.home),
                    earlier=skills_mod.history_listing(self._skill_earlier),
                ),
            },
        ]
        report_worst = (
            estimate_tokens([{"role": "user", "content": report.narrative_input(facts)}])
            + 2_000
            + self.limits.report_max_output_tokens
        )
        max_out = min(self.limits.max_output_tokens, cap // 3 + 800)
        refused = 0
        for _ in range(self.SKILL_CALLS):
            worst = estimate_tokens(messages, prompts.SKILL_TOOLS) + max_out
            if (
                self.limits.max_tokens is not None
                and self.gate.usage.counted_tokens + worst + report_worst > self.limits.max_tokens
            ):
                return record(
                    "skipped", "not enough of the token limit was left to consider a skill and still write the report"
                )
            try:
                reply = self._model_call(self.lead, messages, prompts.SKILL_TOOLS, max_output=max_out)
            except (LimitReached, ModelError) as e:
                return record("skipped", f"the skills call could not be made: {e}")
            except KeyboardInterrupt:
                return record("skipped", "the skills call was interrupted")
            except Exception as e:  # an optional step never stops the report
                return record("skipped", f"the skills call failed: {type(e).__name__}: {e}")
            messages.append(reply.as_message())
            tc = reply.tool_calls[0] if reply.tool_calls else None
            for extra in reply.tool_calls[1:]:
                messages.append(_tool_message(extra.id, {"error": "one call at a time; ignored"}))
            if tc is None or tc.name == "no_skill":
                why = str((tc.arguments or {}).get("reason") or "") if tc else "the Lead replied without a tool"
                return record("none", _short(why or "nothing new to keep", 300))
            if tc.arguments is None:
                messages.append(_tool_message(tc.id, {"error": "the arguments were not a JSON object"}))
                refused += 1
            elif tc.name == "skills__read":
                messages.append(_tool_message(tc.id, self._lead_read_skill(tc.arguments)))
                continue
            elif tc.name == "skills__propose":
                out = self._propose_skill(tc.arguments)
                messages.append(_tool_message(tc.id, out))
                if "error" not in out:
                    return
                refused += 1
            else:
                messages.append(_tool_message(tc.id, {"error": "use skills__propose, skills__read or no_skill"}))
                refused += 1
            if refused >= 2:
                return record("refused", "the Lead's proposal was refused twice")
        record("refused", "the Lead did not finish deciding within its calls")

    def _propose_skill(self, args: dict) -> dict[str, Any]:
        name = str(args.get("name") or "").strip()
        reason = " ".join(str(args.get("reason") or "").split())
        text = skills_mod.compose(
            name, str(args.get("description") or ""), str(args.get("body") or ""), {"source": f"job {self.job_id}"}
        )
        try:
            skills_mod.check(text, name, self.limits.skill_max_chars)
        except skills_mod.SkillError as e:
            self.journal.write("lead", "skills_refused", _short(str(e), 300))
            return {"error": str(e)}
        adds = " ".join(str(args.get("adds") or "").split())
        if not reason:
            return {"error": "give `reason`: one sentence on what in this job shows the skill helps"}
        if not adds:
            return {
                "error": "give `adds`: one sentence on what the skill gives later jobs that writing it in "
                "their brief would not. If it adds nothing, call no_skill"
            }
        evidence, short = skills_mod.check_evidence(args.get("jobs"), self._skill_earlier, self.job_id)
        if short:
            self.journal.write("harness", "skills_refused", _short(short, 300))
            return {"error": short}
        blocked = skills_mod.declined_block(self.home, name)
        if blocked:
            self.journal.write("harness", "skills_refused", blocked)
            return {"error": blocked + ". Call no_skill, unless this job shows a different method"}
        if any(n == name for n, _, _ in outbox.skill_proposals_waiting(self.home)):
            msg = (
                f"a proposal for {name} already waits for the user; another is not made until they decide it. "
                "Call no_skill"
            )
            self.journal.write("harness", "skills_refused", msg)
            return {"error": msg}
        raw_for = args.get("for") or []
        for_roles, unknown = [], []
        for who in raw_for if isinstance(raw_for, list) else [raw_for]:
            role, _ = resolve_role(self.roles, str(who))
            if role is None:
                unknown.append(str(who))
            elif f"{role.crew}/{role.path.name}" not in for_roles:
                for_roles.append(f"{role.crew}/{role.path.name}")
        signs = material.flags(text)
        base = skills_mod.live_sha(self.home, name)
        try:
            action, duplicate = outbox.stage_skill(
                self.job_dir,
                name=name,
                text=text,
                for_roles=for_roles,
                reason=reason,
                base_sha=base,
                flags=signs,
                adds=adds,
                jobs=evidence,
            )
        except outbox.OutboxError as e:
            self.journal.write("harness", "skills", f"the proposal could not be staged: {e}", outcome="skipped")
            return {"ok": True, "note": "it could not be staged"}
        change = "a new version of" if base else "a new skill,"
        self.journal.write(
            "lead",
            "skills",
            f"proposed {change} {name}, waiting for you as {action.ref}",
            outcome="proposed",
            ref=action.ref,
            skill=name,
            change="update" if base else "new",
            reason=reason,
            adds=adds,
            jobs=evidence,
            flags=signs,
            **({"for": for_roles} if for_roles else {}),
            **({"unknown_roles": unknown} if unknown else {}),
        )
        if signs:
            self.journal.write(
                "harness",
                "material_flag",
                f"the proposed skill {name}: {', '.join(signs)}",
                source=f"the proposed skill {name}",
                signs=signs,
                reader="lead",
            )
        return {"ok": True, "staged": action.ref, **({"note": "already staged"} if duplicate else {})}

    # -- report ------------------------------------------------------------

    def _report(self, status: str) -> None:
        self.gate.enter_report_phase()
        narrative, note = None, None
        if self.lead is not None and self.lead.model in self.models:
            facts = report.gather_facts(self.home, self.job_dir, status)
            messages = [
                {"role": "system", "content": prompts.report_system(self.lead, self.instructions, self.today)},
                {"role": "user", "content": report.narrative_input(facts)},
            ]
            try:
                reply = self._model_call(self.lead, messages, None, max_output=self.limits.report_max_output_tokens)
                text = (reply.content or "").strip()
                if text:
                    narrative = text
                else:
                    note = "the Lead's report reply was empty"
            except LimitReached as e:
                note = f"the gate refused the report call ({e.limit}: {e.detail})"
            except ModelError as e:
                note = f"the report call failed: {e}"
            except KeyboardInterrupt:
                note = "the report call was interrupted"
            except Exception as e:
                note = f"the report call failed: {type(e).__name__}: {e}"
        else:
            note = "the Lead could not be loaded"
        if note:
            self.journal.write("harness", "warning", f"report written without the Lead: {note}")
        self._save_usage()
        facts = report.gather_facts(self.home, self.job_dir, status)
        path = report.write(self.job_dir, report.render(facts, narrative, note))
        self.journal.write("lead" if narrative else "harness", "report_written", str(path))
