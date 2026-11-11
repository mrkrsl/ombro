"""The guardrail gate. Every model call and tool call passes through here.

Checks happen before a call; cost is recorded after it. Limits are fixed when
the gate is built and nothing in a run can change them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Callable

from ombro.settings import Limits, format_duration


class LimitReached(Exception):
    """A limit stopped the run. `limit` names it; `detail` says what happened."""

    def __init__(self, limit: str, detail: str):
        super().__init__(f"{limit}: {detail}")
        self.limit = limit
        self.detail = detail


@dataclass
class Spend:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    model_calls: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    rounds: int = 0
    unpriced_calls: int = 0
    estimated_calls: int = 0
    elapsed_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


PriceLookup = Callable[[str], tuple[float, float] | None]


class Gate:
    def __init__(self, limits: Limits, prices: PriceLookup, clock: Callable[[], float]):
        self.limits = limits
        self.prices = prices
        self.clock = clock
        self.started = clock()
        self.spend = Spend()
        self.phase = "work"
        self.report_deadline: float | None = None
        self._stop: str | None = None
        self._seen: Counter = Counter()
        self.scope_role: str | None = None
        self.scope_steps = 0
        self.scope_max: int | None = None

    # -- state -------------------------------------------------------------

    @property
    def work_token_cap(self) -> int:
        return int(self.limits.max_tokens * (1 - self.limits.report_reserve))

    @property
    def work_budget_cap(self) -> float:
        return self.limits.budget_usd * (1 - self.limits.report_reserve)

    def elapsed(self) -> float:
        return self.clock() - self.started

    def timed_out(self) -> bool:
        return self.phase == "work" and self.elapsed() >= self.limits.run_timeout

    def request_stop(self, reason: str) -> None:
        if self._stop is None:
            self._stop = reason

    @property
    def stop_requested(self) -> bool:
        return self._stop is not None

    def begin_scope(self, role: str, max_steps: int) -> None:
        """Start counting model calls for one brief (or one Lead decision)."""
        self.scope_role, self.scope_steps, self.scope_max = role, 0, max_steps

    def enter_report_phase(self) -> None:
        self.phase = "report"
        self.report_deadline = self.clock() + self.limits.report_timeout
        self.scope_role, self.scope_max = None, None

    # -- checks ------------------------------------------------------------

    def _work_checks(self) -> None:
        if self._stop:
            raise LimitReached("stopped", self._stop)
        if self.elapsed() >= self.limits.run_timeout:
            raise LimitReached(
                "run_timeout",
                f"the run reached its time limit of {format_duration(self.limits.run_timeout)}",
            )

    def check_model_call(self, model: str, est_input: int, requested_output: int | None = None) -> tuple[int, float]:
        """Raise LimitReached if this call may not happen. Return (max_output_tokens, timeout)."""
        L = self.limits
        cap_out = L.max_output_tokens
        if self.phase == "report":
            cap_out = min(cap_out, L.report_max_output_tokens)
        max_out = min(requested_output or cap_out, cap_out)

        if self.phase == "work":
            self._work_checks()
            if self.scope_max is not None and self.scope_steps >= self.scope_max:
                raise LimitReached(
                    "max_steps",
                    f"{self.scope_role} used all {self.scope_max} model calls allowed for one brief",
                )
            token_cap, usd_cap = self.work_token_cap, self.work_budget_cap
            timeout = L.run_timeout - self.elapsed()
            where = "available before the report reserve"
        else:
            now = self.clock()
            if now >= self.report_deadline:
                raise LimitReached("report_timeout", "no time was left for the report call")
            token_cap, usd_cap = L.max_tokens, L.budget_usd
            timeout = self.report_deadline - now
            where = "in the run"

        worst_tokens = est_input + max_out
        if self.spend.total_tokens + worst_tokens > token_cap:
            raise LimitReached(
                "max_tokens",
                f"the next call could use up to {worst_tokens:,} tokens; "
                f"{self.spend.total_tokens:,} of {token_cap:,} tokens {where} are already used",
            )
        price = self.prices(model)
        if price is not None:
            worst_usd = (est_input * price[0] + max_out * price[1]) / 1_000_000
            if self.spend.usd + worst_usd > usd_cap:
                raise LimitReached(
                    "budget_usd",
                    f"the next call could cost up to ${worst_usd:.4f}; "
                    f"${self.spend.usd:.4f} of ${usd_cap:.4f} {where} is already spent",
                )
        if self.phase == "work":
            self.scope_steps += 1
        return max_out, max(timeout, 0.001)

    def record_model_call(self, model: str, input_tokens: int, output_tokens: int, estimated: bool = False) -> float:
        self.spend.model_calls += 1
        self.spend.input_tokens += input_tokens
        self.spend.output_tokens += output_tokens
        if estimated:
            self.spend.estimated_calls += 1
        price = self.prices(model)
        cost = 0.0
        if price is None:
            self.spend.unpriced_calls += 1
        else:
            cost = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
            self.spend.usd += cost
        return cost

    def check_round(self) -> None:
        self._work_checks()
        if self.spend.rounds >= self.limits.max_rounds:
            raise LimitReached("max_rounds", f"the Lead has used all {self.limits.max_rounds} delegation rounds")

    def record_round(self) -> int:
        self.spend.rounds += 1
        return self.spend.rounds

    def check_tool_call(self, role: str, tool: str, args_key: str) -> None:
        if self.phase != "work":
            raise LimitReached("tools_disabled", "tools are disabled while the report is written")
        self._work_checks()
        cap = self.limits.max_calls_for(tool)
        used = self.spend.tool_calls.get(tool, 0)
        if used >= cap:
            raise LimitReached("max_calls", f"{tool} has been called {used} times, its limit for a run")
        key = (role, tool, args_key)
        if self._seen[key] >= self.limits.loop_threshold:
            raise LimitReached(
                "loop_detection",
                f"{role} called {tool} with the same arguments {self._seen[key] + 1} times "
                f"(the threshold is {self.limits.loop_threshold})",
            )
        self._seen[key] += 1
        self.spend.tool_calls[tool] = used + 1

    def record_tool_cost(self, usd: float) -> None:
        """For tools that bill per call (cost_per_call in tools.md)."""
        self.spend.usd += usd

    def snapshot(self) -> dict:
        self.spend.elapsed_seconds = round(self.elapsed(), 3)
        d = asdict(self.spend)
        d["usd"] = round(d["usd"], 6)
        d["total_tokens"] = self.spend.total_tokens
        return d
