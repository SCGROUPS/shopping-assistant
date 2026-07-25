"""`python -m app.evals.cli` - run the quality suites from a terminal or CI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.common.config import get_settings
from app.common.persistence import database_mode
from app.common.store import store
from app.evals.runner import (
    SuiteReport,
    compare,
    format_report,
    load_cases,
    run_assistant_suite,
    run_search_suite,
    write_baseline,
)

BASELINE = Path(__file__).resolve().parents[2] / "evals" / "baseline.json"


def _model_configured() -> bool:
    settings = get_settings()
    return bool(settings.azure_openai_endpoint and settings.azure_openai_chat_deployment)


async def _run(suites: list[str]) -> list[SuiteReport]:
    if not database_mode():
        store.seed()
    reports: list[SuiteReport] = []
    for suite in suites:
        cases = load_cases(suite)
        if suite == "search":
            reports.append(await run_search_suite(cases))
        else:
            reports.append(await run_assistant_suite(cases))
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Vietra quality evaluations.")
    parser.add_argument(
        "--suite",
        action="append",
        choices=["search", "assistant"],
        help="Repeatable. Defaults to search only, which needs no model.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=1.0,
        help="Minimum pass rate per suite before this exits non-zero.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current results as the reference for future runs.",
    )
    parser.add_argument("--quiet", action="store_true", help="Summary lines only.")
    args = parser.parse_args(argv)

    suites = args.suite or ["search"]
    if "assistant" in suites and not _model_configured():
        # Silently passing a suite that never ran is how a green build starts
        # meaning nothing.
        print("assistant suite needs AZURE_OPENAI_ENDPOINT and a chat deployment", file=sys.stderr)
        return 2

    reports = asyncio.run(_run(suites))

    if args.json:
        print(json.dumps({report.suite: report.to_dict() for report in reports}, indent=2))
    else:
        for report in reports:
            print(format_report(report, verbose=not args.quiet))
            print()

    regressions: list[str] = []
    if BASELINE.exists() and not args.update_baseline:
        baseline = json.loads(BASELINE.read_text())
        for report in reports:
            for case in compare(baseline.get(report.suite, {}), report):
                regressions.append(f"{report.suite}/{case}")

    if args.update_baseline:
        write_baseline(reports, BASELINE)
        print(f"baseline written to {BASELINE}")
        return 0

    failed = False
    if regressions:
        print("REGRESSIONS (passed in baseline, failing now):", file=sys.stderr)
        for case in regressions:
            print(f"  {case}", file=sys.stderr)
        failed = True
    for report in reports:
        if report.pass_rate < args.fail_under:
            print(
                f"{report.suite}: pass rate {report.pass_rate:.0%}"
                f" below threshold {args.fail_under:.0%}",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
