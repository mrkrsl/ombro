"""The Lead loop: one run, from spec to report."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ombro import contract, mdfile, prompts, report, runs
from ombro.client import ChatClient, ModelError, ModelReply, estimate_tokens, make_client
from ombro.gate import BriefLimit, Gate, LimitReached, ToolRefused
from ombro.home import Home
from ombro.journal import Journal, utc_now
from ombro.roles import Role, load_crew, skill_texts
from ombro.settings import Limits, ModelSpec, SetupError, ToolSettings, load_models, load_preferences
from ombro.tools import Tool, ToolContext, ToolError, ToolRegistry

TOOL_RESULT_CAP = 20_000


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _canonical(args: dict | None, raw: str) -> str:
    if args is None:
        return raw
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def _tool_message(call_id: str, payload: Any) -> dict[str, Any]:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(content) > TOOL_RESULT_CAP:
        content = content[:TOOL_RESULT_CAP] + "…[truncated]"
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class Engine:
    def __init__(
        self,
        home: Home,
        run_dir: Path,
        *,
        client_factory: Callable[[ModelSpec], ChatClient] | None = None,
        registry: ToolRegistry | None = None,
        clock: Callable[[], float] = time.time,
        echo: Callable[[dict], None] | None = None,
        today: date | None = None,
    ):
        self.home = home
        self.run_dir = Path(run_dir)
        meta, _ = runs.read_run(self.run_dir)
        self.run_id = meta["run_id"]
        self.crew = meta["crew"]
        self.limits = Limits.from_dict(meta["limits"])  # fixed at run creation
        self.clock = clock
        self.journal = Journal(self.run_dir / "journal.jsonl", echo=echo)
        # Tool settings, like limits, were fixed when the run was created.
        self.tool_settings = ToolSettings.from_dict(meta.get("tools"), "run.md tools")
        if registry is None:
            from ombro.native import native_registry

            registry = native_registry(home, self.tool_settings)
        self.registry = registry
        self._client_factory = client_factory or (lambda spec: make_client(spec, home))
        self._clients: dict[str, ChatClient] = {}
        self.models: dict[str, ModelSpec] = {}
        self.gate = Gate(self.limits, self._price, clock)
        self.lead: Role | None = None
        self.roles: dict[str, Role] = {}
        self.instructions = ""
        self.finished = False
        # The local date, fixed for the run, so roles do not guess it.
        self.today = prompts.format_date(today or date.today())

    # -- set-up ------------------------------------------------------------

    def _price(self, model_name: str) -> tuple[float, float] | None:
        spec = self.models.get(model_name)
        if spec is None or not spec.priced:
            return None
        return spec.price_in, spec.price_out

    def _client(self, spec: ModelSpec) -> ChatClient:
        if spec.name not in self._clients:
            self._clients[spec.name] = self._client_factory(spec)
        return self._clients[spec.name]

    def _load(self) -> None:
        self.models = load_models(self.home)
        self.lead, self.roles = load_crew(self.home, self.crew, self.limits)
        self.instructions = load_preferences(self.home).instructions
        for role in [self.lead, *self.roles.values()]:
            if role.model not in self.models:
                raise SetupError(f"role {role.name} uses model {role.model!r}, which is not in models.md")
            spec = self.models[role.model]
            if not spec.priced:
                self.journal.write(
                    "harness",
                    "warning",
                    f"model {spec.name!r} has no price in models.md, so budget_usd cannot bound it; max_tokens still does",
                )
            missing_tools = [t for t in role.tools if self.registry.get(t) is None]
            if missing_tools:
                self.journal.write(
                    "harness",
                    "warning",
                    f"{role.name} lists tools that are not available and will not be offered: {', '.join(missing_tools)}",
                )
            self._warn_missing_keys(role)
            _, missing_skills = skill_texts(self.home, role.skills)
            if missing_skills:
                self.journal.write(
                    "harness", "warning", f"{role.name} lists skills that do not exist: {', '.join(missing_skills)}"
                )
        _, spec_body = mdfile.read(self.run_dir / "spec.md")
        self.spec_body = spec_body.strip()
        if not self.spec_body:
            raise SetupError("the spec is empty")

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

    # -- the run -----------------------------------------------------------

    def execute(self) -> str:
        runs.update_run(self.run_dir, status="running", pid=os.getpid(), started_at=utc_now())
        self.journal.write("harness", "run_started", _short(runs.read_run(self.run_dir)[0].get("brief", "")))
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
        except Exception as e:  # never leave a run without a report
            status = "failed"
            self.journal.write("harness", "failed", f"internal error: {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)
        self._report(status)
        runs.update_run(
            self.run_dir,
            status=status,
            limit_hit=limit_hit,
            finished_at=utc_now(),
            spend=self.gate.snapshot(),
        )
        self.journal.write("harness", "run_ended", status)
        return status

    def _save_spend(self) -> None:
        runs.update_run(self.run_dir, spend=self.gate.snapshot())

    def _model_call(
        self,
        role: Role,
        messages: list[dict],
        tools: list[dict] | None,
        max_output: int | None = None,
    ) -> ModelReply:
        spec = self.models[role.model]
        est = estimate_tokens(messages, tools)
        max_out, timeout = self.gate.check_model_call(spec.name, est, max_output)
        try:
            reply = self._client(spec).complete(messages, tools=tools, max_output_tokens=max_out, timeout=timeout)
        except ModelError as e:
            self.journal.write(role.name, "model_error", str(e))
            if self.gate.timed_out():
                raise LimitReached("run_timeout", "the run reached its time limit during a model call") from None
            raise
        usage = reply.usage
        in_tokens = usage.input_tokens if usage.input_tokens or not usage.estimated else est
        out_tokens = min(usage.output_tokens, max_out) if usage.estimated else usage.output_tokens
        cost = self.gate.record_model_call(spec.name, in_tokens, out_tokens, estimated=usage.estimated)
        self.journal.write(
            role.name,
            "model_call",
            f"{spec.name}: {in_tokens:,} in, {out_tokens:,} out, ${cost:.4f}",
            model=spec.name,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            usd=round(cost, 6),
        )
        self._save_spend()
        return reply

    # -- Lead --------------------------------------------------------------

    def _lead_loop(self) -> None:
        assert self.lead is not None
        messages: list[dict] = [
            {"role": "system", "content": prompts.lead_system(self.lead, self.instructions, self.roles, self.limits, self.today)},
            {"role": "user", "content": self.spec_body},
        ]
        self.gate.begin_scope(self.lead.name, self.lead.max_steps)
        idle = 0
        while not self.finished:
            reply = self._model_call(self.lead, messages, prompts.LEAD_TOOLS)
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
                    messages.append(_tool_message(tc.id, {"error": "ignored: the run is finishing"}))
                    continue
                messages.append(_tool_message(tc.id, self._lead_tool(tc)))

    def _lead_tool(self, tc) -> dict[str, Any]:
        try:
            self.gate.check_tool_call("lead", tc.name, _canonical(tc.arguments, tc.raw_arguments))
        except ToolRefused as e:
            self.journal.write("lead", "tool_refused", f"{tc.name}: {e.detail}", limit=e.limit)
            return {"error": e.detail}
        finally:
            self._save_spend()
        if tc.arguments is None:
            self.journal.write("lead", "tool_refused", f"{tc.name}: arguments were not a JSON object")
            return {"error": "the arguments were not a JSON object"}
        if tc.name == "brief":
            return self._brief(tc.arguments)
        if tc.name == "finish":
            outcome = tc.arguments.get("outcome")
            if outcome not in ("done", "cannot_proceed"):
                outcome = "done"
            summary = str(tc.arguments.get("summary") or "").strip()
            self.journal.write("lead", "finish", _short(summary), outcome=outcome, summary=summary)
            self.finished = True
            return {"ok": True}
        self.journal.write("lead", "tool_refused", f"unknown tool {tc.name!r}")
        return {"error": f"unknown tool {tc.name!r}; use brief or finish"}

    def _brief(self, args: dict) -> dict[str, Any]:
        name = str(args.get("role") or "").strip()
        task = str(args.get("task") or "").strip()
        role = self.roles.get(name)
        if role is None:
            known = ", ".join(self.roles) or "none"
            self.journal.write("lead", "tool_refused", f"brief: no role named {name!r}")
            return {"error": f"there is no role named {name!r}. Roles you can brief: {known}"}
        if not task:
            self.journal.write("lead", "tool_refused", "brief: empty task")
            return {"error": "the task is empty"}
        self.gate.check_round()
        n = self.gate.record_round()
        self.journal.write("lead", "briefed", f"{name}: {_short(task)}", target=name, task=task, round=n)
        outcome = self._run_role(role, task)
        assert self.lead is not None
        self.gate.begin_scope(self.lead.name, self.lead.max_steps)
        return outcome

    # -- roles -------------------------------------------------------------

    def _role_tools(self, role: Role) -> tuple[list[dict], dict[str, Tool]]:
        tools = [t for name in role.tools if (t := self.registry.get(name)) is not None]
        return [t.schema() for t in tools], {t.wire_name: t for t in tools}

    def _run_role(self, role: Role, task: str) -> dict[str, Any]:
        schemas, by_wire = self._role_tools(role)
        skills, _ = skill_texts(self.home, role.skills)
        messages: list[dict] = [
            {"role": "system", "content": prompts.role_system(role, skills, [t.name for t in by_wire.values()], self.today)},
            {"role": "user", "content": prompts.role_task(task)},
        ]
        self.gate.begin_scope(role.name, role.max_steps, brief=True)
        try:
            return self._role_loop(role, messages, schemas, by_wire)
        except BriefLimit as e:
            # The brief ends; the run goes on and the Lead decides what to do.
            reason = f"stopped by {e.limit}: {e.detail}"
            self.journal.write(role.name, "returned", reason, status="stopped_by_limit", limit=e.limit, reason=reason)
            self._save_spend()
            return {
                "status": "stopped_by_limit",
                "role": role.name,
                "limit": e.limit,
                "reason": e.detail,
                "note": "The role did not return a result. Files it wrote before it stopped are still in the "
                "workspace. Brief again more narrowly, or finish.",
            }

    def _role_loop(self, role: Role, messages: list[dict], schemas: list[dict], by_wire: dict[str, Tool]) -> dict:
        invalid = 0
        while True:
            reply = self._model_call(role, messages, schemas or None)
            messages.append(reply.as_message())
            if reply.tool_calls:
                for tc in reply.tool_calls:
                    messages.append(_tool_message(tc.id, self._role_tool(role, tc, by_wire)))
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

    def _role_tool(self, role: Role, tc, by_wire: dict[str, Tool]) -> Any:
        tool = by_wire.get(tc.name)
        name = tool.name if tool else tc.name
        try:
            self.gate.check_tool_call(role.name, name, _canonical(tc.arguments, tc.raw_arguments), brief=True)
        except ToolRefused as e:
            self.journal.write(role.name, "tool_refused", f"{name}: {e.detail}", limit=e.limit, tool=name)
            return {"error": f"{e.detail}. Carry on without it, or return what you have."}
        finally:
            self._save_spend()
        if tool is None:
            self.journal.write(role.name, "tool_refused", f"{name}: not one of this role's tools")
            return {"error": f"{name} is not one of your tools"}
        if tool.outward:
            self.journal.write(role.name, "tool_refused", f"{name}: outward tools are not available")
            return {"error": f"{name} sends something outside the run; outward tools are not available"}
        if tc.arguments is None:
            self.journal.write(role.name, "tool_refused", f"{name}: arguments were not a JSON object")
            return {"error": "the arguments were not a JSON object"}
        cost = self.limits.cost_for(name)
        self.gate.check_tool_cost(name, cost)  # a run-level budget stop if it cannot be afforded
        ctx = ToolContext(self.run_dir, role.name, timeout=self._tool_timeout())
        args_note = _short(_canonical(tc.arguments, ""), 120)
        try:
            out = tool.handler(tc.arguments, ctx) if tool.handler else None
        except ToolError as e:
            self._charge(name, cost, billed=e.billable)
            self.journal.write(role.name, "tool_error", f"{name} {args_note}: {_short(str(e))}", tool=name)
            return {"error": str(e)}
        except Exception as e:
            self._charge(name, cost, billed=True)
            self.journal.write(role.name, "tool_error", f"{name}: {type(e).__name__}: {_short(str(e))}", tool=name)
            return {"error": f"{type(e).__name__}: {e}"}
        self._charge(name, cost, billed=True)
        self.journal.write(role.name, "tool_call", f"{name} {args_note}", tool=name)
        return out if out is not None else {"ok": True}

    def _charge(self, name: str, cost: float, billed: bool) -> None:
        if cost and billed:
            self.gate.record_tool_cost(cost)
            self.journal.write("harness", "tool_cost", f"{name}: ${cost:.4f}", tool=name, usd=round(cost, 6))
            self._save_spend()

    def _tool_timeout(self) -> float:
        from ombro.native import CALL_TIMEOUT

        return max(0.5, min(CALL_TIMEOUT, self.limits.run_timeout - self.gate.elapsed()))

    # -- report ------------------------------------------------------------

    def _report(self, status: str) -> None:
        self.gate.enter_report_phase()
        narrative, note = None, None
        if self.lead is not None and self.lead.model in self.models:
            facts = report.gather_facts(self.home, self.run_dir, status)
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
        self._save_spend()
        facts = report.gather_facts(self.home, self.run_dir, status)
        path = report.write(self.run_dir, report.render(facts, narrative, note))
        self.journal.write("lead" if narrative else "harness", "report_written", str(path))
