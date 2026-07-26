"""Runs the golden suites and reports what broke.

The point of this module is to make a quality regression *fail loudly* the
same way a type error does. Ranking and prompt changes are otherwise
unfalsifiable: they always look reasonable in the diff and the only signal is
a shopper who quietly leaves.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.api.schemas import (
    AssistantContext,
    ConversationCreate,
    MessageRequest,
    Participant,
    SearchFilters,
    SearchRequest,
)
from app.assistant.service import AssistantService
from app.common.persistence import catalog_products, database_mode
from app.common.store import store
from app.evals.checks import Violation, run_checks
from app.search.service import SearchService

CASE_DIR = Path(__file__).resolve().parents[2] / "evals"


@dataclass
class CaseResult:
    case_id: str
    suite: str
    violations: list[Violation] = field(default_factory=list)
    result_count: int = 0
    latency_ms: float = 0.0
    error: str | None = None
    why: str = ""

    @property
    def passed(self) -> bool:
        return not self.violations and self.error is None


@dataclass
class SuiteReport:
    suite: str
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 1.0

    @property
    def violations(self) -> list[Violation]:
        return [item for result in self.results for item in result.violations]

    def violation_counts(self) -> dict[str, int]:
        """Which checks fail most: the fastest route to the real defect."""
        counts: dict[str, int] = {}
        for violation in self.violations:
            counts[violation.check] = counts.get(violation.check, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))

    def latency_p95_ms(self) -> float:
        if not self.results:
            return 0.0
        ordered = sorted(result.latency_ms for result in self.results)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "passed": self.passed,
            "total": self.total,
            "pass_rate": round(self.pass_rate, 4),
            "latency_p95_ms": round(self.latency_p95_ms(), 1),
            "violation_counts": self.violation_counts(),
            "cases": [
                {
                    "id": result.case_id,
                    "passed": result.passed,
                    "results": result.result_count,
                    "latency_ms": round(result.latency_ms, 1),
                    "error": result.error,
                    "why": result.why,
                    "violations": [str(item) for item in result.violations],
                }
                for result in self.results
            ],
        }


def load_cases(suite: str, path: Path | None = None) -> dict[str, Any]:
    source = path or CASE_DIR / f"{suite}_cases.json"
    return json.loads(source.read_text())


def _card_dict(card: Any) -> dict[str, Any]:
    payload = card.model_dump(mode="json") if hasattr(card, "model_dump") else dict(card)
    payload["id"] = str(payload.get("id"))
    return payload


def _build_request(case: dict[str, Any]) -> SearchRequest:
    return SearchRequest(
        query=case.get("query", ""),
        filters=SearchFilters(**case.get("filters", {})),
        party=[Participant(**item) for item in case.get("party", [])],
        sort=case.get("sort", "recommended"),
        page_size=case.get("page_size", 20),
    )


async def run_search_suite(
    cases: dict[str, Any], service: SearchService | None = None
) -> SuiteReport:
    engine = service or SearchService(store)
    results: list[CaseResult] = []
    for case in cases["cases"]:
        started = time.perf_counter()
        try:
            response = await engine.search(_build_request(case))
            products = [_card_dict(item) for item in response.items]
            violations = run_checks(products, case.get("expect", {}))
            error = None
        except Exception as exc:  # noqa: BLE001 - a crash is a result, not a stop
            products, violations, error = [], [], f"{type(exc).__name__}: {exc}"
        results.append(
            CaseResult(
                case_id=case["id"],
                suite="search",
                violations=violations,
                result_count=len(products),
                latency_ms=(time.perf_counter() - started) * 1000,
                error=error,
                why=case.get("why", ""),
            )
        )
    return SuiteReport("search", results)


async def run_assistant_suite(
    cases: dict[str, Any], service: AssistantService | None = None
) -> SuiteReport:
    """Each case is one conversation, so later turns test memory too.

    Grounding is checked on every turn against the live catalogue: a card the
    agent shows must be a product that actually exists, or the shopper is
    looking at something they cannot book.
    """
    assistant = service or AssistantService(store)
    catalogue = {str(product["id"]) for product in await catalog_products()}
    results: list[CaseResult] = []
    for case in cases["cases"]:
        session_id = str(uuid4())
        conversation = await assistant.create(session_id, ConversationCreate())
        conversation_id = conversation["id"]
        for index, turn in enumerate(case.get("turns", [])):
            started = time.perf_counter()
            try:
                response = await assistant.respond(
                    conversation_id,
                    session_id,
                    MessageRequest(message=turn["message"], context=AssistantContext()),
                )
                products = [_card_dict(item) for item in response.products]
                violations = run_checks(
                    products,
                    turn.get("expect", {}),
                    message=response.message,
                    offered_ids=catalogue,
                )
                error = None
            except Exception as exc:  # noqa: BLE001
                products, violations, error = [], [], f"{type(exc).__name__}: {exc}"
            label = case["id"] if len(case.get("turns", [])) == 1 else f"{case['id']}#{index + 1}"
            results.append(
                CaseResult(
                    case_id=label,
                    suite="assistant",
                    violations=violations,
                    result_count=len(products),
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=error,
                    why=case.get("why", ""),
                )
            )
    return SuiteReport("assistant", results)


def _multilingual_request(case: dict[str, Any]) -> SearchRequest:
    request = _build_request(case)
    request.locale = case.get("locale", "en")
    return request


async def run_multilingual_suite(
    cases: dict[str, Any], service: SearchService | None = None
) -> SuiteReport:
    """The same search suite, asked in eight languages against PostgreSQL.

    Deliberately refuses to run without a database rather than falling back to
    the demo store. Locale selection, the per-locale text-search configuration
    and the fallback chain exist only in the PostgreSQL path, so a demo-store
    run would answer every case from the English corpus and report a pass rate
    that means nothing at all — the precise failure the tokenizer work already
    taught us to distrust.
    """
    if not database_mode():
        raise RuntimeError(
            "The multilingual suite requires DATABASE_URL: without it every case"
            " would be answered from the English demo store and pass vacuously."
        )
    engine = service or SearchService(store)
    results: list[CaseResult] = []
    for case in cases["cases"]:
        started = time.perf_counter()
        try:
            response = await engine.search(_multilingual_request(case))
            products = [_card_dict(item) for item in response.items]
            violations = run_checks(products, case.get("expect", {}))
            error = None
        except Exception as exc:  # noqa: BLE001 - a crash is a result, not a stop
            products, violations, error = [], [], f"{type(exc).__name__}: {exc}"
        results.append(
            CaseResult(
                case_id=case["id"],
                suite="multilingual",
                violations=violations,
                result_count=len(products),
                latency_ms=(time.perf_counter() - started) * 1000,
                error=error,
                why=case.get("why", ""),
            )
        )
    return SuiteReport("multilingual", results)


def format_report(report: SuiteReport, verbose: bool = True) -> str:
    lines = [
        f"suite: {report.suite}",
        f"passed: {report.passed}/{report.total}  ({report.pass_rate:.0%})",
        f"latency p95: {report.latency_p95_ms():.0f}ms",
    ]
    counts = report.violation_counts()
    if counts:
        summary = ", ".join(f"{name} x{count}" for name, count in counts.items())
        lines.append(f"failing checks: {summary}")
    if verbose:
        lines.append("")
        for result in report.results:
            mark = "PASS" if result.passed else "FAIL"
            lines.append(
                f"  [{mark}] {result.case_id}"
                f"  ({result.result_count} results, {result.latency_ms:.0f}ms)"
            )
            if result.error:
                lines.append(f"         error: {result.error}")
            for violation in result.violations:
                lines.append(f"         {violation}")
            if not result.passed and result.why:
                lines.append(f"         why this case exists: {result.why}")
    return "\n".join(lines)


def compare(baseline: dict[str, Any], report: SuiteReport) -> list[str]:
    """Name the cases that used to pass and no longer do.

    An overall pass rate can hold steady while the suite trades one fixed
    case for one broken one, which is exactly the change worth blocking.
    """
    was_passing = {case["id"] for case in baseline.get("cases", []) if case.get("passed")}
    now: dict[str, bool] = {result.case_id: result.passed for result in report.results}
    return sorted(case for case in was_passing if now.get(case) is False)


def write_baseline(reports: Sequence[SuiteReport], path: Path) -> None:
    payload = {report.suite: report.to_dict() for report in reports}
    path.write_text(json.dumps(payload, indent=2) + "\n")
