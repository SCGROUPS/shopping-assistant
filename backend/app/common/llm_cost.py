"""Spend accounting and the daily budget breaker for LLM calls.

`POC_SPEC.md` §13.7 requires a cost ceiling. `openai_daily_budget` existed in
configuration but nothing read it, so a runaway loop or a traffic spike could
spend without limit.

The breaker degrades rather than fails: every LLM call site in this codebase
already has a deterministic fallback (keyword intent parsing, hash embeddings,
templated assistant prose), so refusing a call costs relevance, not
availability. That is the correct trade — a storefront that still sells beats
one that returns errors.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

# USD per 1M tokens, list price at time of writing. Approximate by design: this
# drives a safety breaker, not billing, so being roughly right beats being
# precisely stale.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.25, 2.00),
    "gpt-5.4-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}
DEFAULT_PRICING = (0.30, 2.40)


def estimate_cost(model: str, input_tokens: float, output_tokens: float) -> float:
    input_rate, output_rate = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised instead of calling the model once the daily ceiling is reached."""


@dataclass
class DailySpend:
    day: date
    total: float = 0.0
    calls: int = 0
    by_purpose: dict[str, float] = field(default_factory=dict)


class CostLedger:
    """Process-local daily spend accumulator guarding a hard ceiling.

    Process-local is a deliberate MVP limit: with N replicas the effective
    ceiling is N x budget. Making it exact needs a shared counter in PostgreSQL
    or Redis, which is Phase 6 work; an approximate breaker that exists is worth
    far more than an exact one that does not.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spend = DailySpend(day=datetime.now(UTC).date())

    def _current(self) -> DailySpend:
        today = datetime.now(UTC).date()
        if self._spend.day != today:
            self._spend = DailySpend(day=today)
        return self._spend

    def record(
        self,
        model: str,
        input_tokens: float,
        output_tokens: float,
        purpose: str = "unknown",
    ) -> float:
        cost = estimate_cost(model, input_tokens, output_tokens)
        with self._lock:
            spend = self._current()
            spend.total += cost
            spend.calls += 1
            spend.by_purpose[purpose] = spend.by_purpose.get(purpose, 0.0) + cost
        return cost

    def snapshot(self) -> DailySpend:
        with self._lock:
            spend = self._current()
            return DailySpend(
                day=spend.day,
                total=spend.total,
                calls=spend.calls,
                by_purpose=dict(spend.by_purpose),
            )

    def exhausted(self, budget: float) -> bool:
        if budget <= 0:
            return False
        with self._lock:
            return self._current().total >= budget

    def reset(self) -> None:
        with self._lock:
            self._spend = DailySpend(day=datetime.now(UTC).date())


ledger = CostLedger()
