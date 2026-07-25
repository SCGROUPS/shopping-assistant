"""The grader has to be trustworthy before its verdicts are worth anything."""

from __future__ import annotations

import json

import pytest

from app.common.store import store
from app.evals.checks import CHECKS, run_checks
from app.evals.runner import compare, format_report, load_cases, run_search_suite

OCEAN = {
    "id": "1",
    "title": "Fishing Village Sunset Cruise",
    "short_description": "A boat along the coast",
    "category": "Water",
    "location": "Mui Ne",
    "destination": "Mui Ne",
    "tags": ["coast", "sunset"],
    "badges": [],
    "price": 770000,
    "rating": 4.9,
    "duration_minutes": 180,
}
MOUNTAIN = {
    "id": "2",
    "title": "Marble Mountains Discovery",
    "short_description": "Climb the mountain caves",
    "category": "Nature",
    "location": "Da Nang",
    "destination": "Da Nang",
    "tags": ["mountain"],
    "badges": [],
    "price": 970000,
    "rating": 4.8,
    "duration_minutes": 300,
}


def test_an_exclusion_catches_the_product_the_shopper_ruled_out():
    """The reported failure: 'ocean, not mountain' returned Marble Mountains."""
    assert not run_checks([OCEAN], {"forbid_terms": ["mountain"]})
    violations = run_checks([OCEAN, MOUNTAIN], {"forbid_terms": ["mountain"]})
    assert len(violations) == 1
    assert "Marble Mountains" in violations[0].detail


def test_substring_matches_do_not_count_as_relevance():
    """'pho' lives inside 'photography'.

    A substring grader would score a photography tour as a Pho match and pass
    the exact bug this suite exists to catch.
    """
    photography = {**OCEAN, "title": "Ha Long Sunrise Photography Journey", "tags": []}
    assert run_checks([photography], {"require_any_term": ["pho"]})


def test_a_budget_violation_is_reported_with_the_offending_amount():
    violations = run_checks([OCEAN, MOUNTAIN], {"max_price": 800000})
    assert len(violations) == 1
    assert "970000" in violations[0].detail


def test_sort_order_checks_catch_a_single_inversion():
    assert not run_checks([OCEAN, MOUNTAIN], {"ascending": "price"})
    assert run_checks([MOUNTAIN, OCEAN], {"ascending": "price"})
    assert not run_checks([OCEAN, MOUNTAIN], {"descending": "rating"})


def test_an_empty_answer_is_a_failure_when_supply_was_expected():
    assert run_checks([], {"min_results": 1})
    assert not run_checks([], {"max_results": 0})
    assert run_checks([OCEAN], {"max_results": 0})


def test_the_prose_must_acknowledge_what_was_asked():
    """Paired with max_results, this separates a refusal from a blank screen."""
    assert not run_checks([], {"message_mentions": ["flight"]}, message="I cannot book flights.")
    assert run_checks([], {"message_mentions": ["flight"]}, message="Here are some tours.")


def test_an_invented_product_is_a_hard_failure():
    """A card that traces to nothing is a card nobody can buy."""
    assert not run_checks([OCEAN], {}, offered_ids={"1", "2"})
    violations = run_checks([OCEAN], {}, offered_ids={"9"})
    assert violations and violations[0].check == "grounded"


def test_an_unknown_check_fails_loudly_instead_of_being_skipped():
    """A typo in a case file must not silently turn the case into a no-op."""
    violations = run_checks([OCEAN], {"max_pirce": 10})
    assert violations and violations[0].check == "unknown_check"


@pytest.mark.parametrize("suite", ["search", "assistant"])
def test_every_case_file_only_uses_checks_that_exist(suite: str):
    cases = load_cases(suite)
    known = set(CHECKS) | {"expect_terms_in_top", "message_mentions"}
    for case in cases["cases"]:
        turns = case.get("turns") or [case]
        for turn in turns:
            unknown = set(turn.get("expect", {})) - known
            assert not unknown, f"{case['id']} uses unknown checks {unknown}"


@pytest.mark.parametrize("suite", ["search", "assistant"])
def test_every_case_explains_why_it_exists(suite: str):
    """A case nobody can justify is a case nobody will maintain."""
    for case in load_cases(suite)["cases"]:
        assert case.get("why"), f"{case['id']} has no rationale"


@pytest.mark.parametrize("suite", ["search", "assistant"])
def test_no_case_asserts_nothing(suite: str):
    """A green suite full of empty expectations is worse than no suite."""
    for case in load_cases(suite)["cases"]:
        turns = case.get("turns") or [case]
        assert any(turn.get("expect") for turn in turns), f"{case['id']} asserts nothing"


async def test_the_search_suite_passes_against_the_shipped_ranking():
    """This is the guard itself: if ranking regresses, this test goes red."""
    store.seed()
    report = await run_search_suite(load_cases("search"))
    assert report.total >= 10
    assert report.passed == report.total, format_report(report)


async def test_a_crashing_case_is_recorded_rather_than_ending_the_run():
    """One exception must not hide the state of every other case."""

    class Exploding:
        async def search(self, request):
            raise RuntimeError("boom")

    report = await run_search_suite(
        {"cases": [{"id": "x", "query": "q", "expect": {}}]}, service=Exploding()
    )
    assert report.passed == 0
    assert report.results[0].error and "boom" in report.results[0].error


async def test_a_regression_is_named_even_when_the_pass_rate_holds():
    """Fixing one case while breaking another must not read as 'no change'."""
    store.seed()
    report = await run_search_suite(load_cases("search"))
    baseline = json.loads(json.dumps(report.to_dict()))
    assert compare(baseline, report) == []

    broken = report
    broken.results[0].violations = list(run_checks([OCEAN], {"max_results": 0}))
    assert compare(baseline, broken) == [report.results[0].case_id]
