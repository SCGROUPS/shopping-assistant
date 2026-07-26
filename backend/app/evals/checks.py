"""The assertion vocabulary shared by every evaluation suite.

Deliberately not an LLM judge. A judge is useful for open-ended prose, but
every failure this project has actually shipped was a *structural* one: an
ocean request answered with mountains, a Pho question answered with boat
trips, a budget filter that let a pricier option through. Those are all
statements about product attributes, which are data we already hold, so the
grader can be exact and reproducible. Adding a judge later is easy; getting a
flaky grader out of CI once it is there is not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Violation:
    """One failed expectation, phrased so a reader can act on it."""

    check: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.check}: {self.detail}"


def _text(product: dict[str, Any]) -> str:
    """Everything a shopper would read about a product, lowercased."""
    parts: list[Any] = [
        product.get("title"),
        product.get("short_description"),
        product.get("category"),
        product.get("location"),
        product.get("destination"),
        *(product.get("tags") or []),
        *(product.get("badges") or []),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _label(product: dict[str, Any]) -> str:
    return str(product.get("title") or product.get("id") or "<unknown>")


def _word_pattern(term: str) -> str:
    """Whole word plus a plural, which is the only inflection worth handling.

    Substring matching lies: "pho" is inside "photography", so a substring
    grader scores a photography tour as a Pho match and passes the exact bug
    this suite exists to catch. Strict word boundaries lie in the other
    direction: they read "I cannot book flights" as never mentioning
    "flight". Allowing an optional plural covers the real cases (flight,
    mountain, cruise) without reopening the first hole.
    """
    return r"\b" + re.escape(term.lower().strip()) + r"(?:s|es)?\b"


def _matches(product: dict[str, Any], term: str) -> bool:
    return re.search(_word_pattern(term), _text(product)) is not None


def forbid_terms(products: Sequence[dict[str, Any]], terms: Iterable[str]) -> list[Violation]:
    """No returned product may be about a thing the shopper ruled out.

    "Somewhere in the ocean, not mountain" is the canonical case. The old
    behaviour returned mountain trips and explained in prose which one was
    least wrong, which is not the same as honouring the exclusion.
    """
    violations = []
    for term in terms:
        for product in products:
            if _matches(product, term):
                violations.append(
                    Violation("forbid_terms", f"{_label(product)} matches excluded '{term}'")
                )
    return violations


def require_any_term(products: Sequence[dict[str, Any]], terms: Sequence[str]) -> list[Violation]:
    """Every returned product must relate to at least one thing asked for."""
    if not terms:
        return []
    return [
        Violation(
            "require_any_term",
            f"{_label(product)} relates to none of {list(terms)}",
        )
        for product in products
        if not any(_matches(product, term) for term in terms)
    ]


def expect_terms_in_top(
    products: Sequence[dict[str, Any]], terms: Sequence[str], k: int
) -> list[Violation]:
    """The obviously-right answer has to actually surface near the top."""
    if not terms:
        return []
    head = products[:k]
    return [
        Violation("expect_terms_in_top", f"no result in top {k} matches '{term}'")
        for term in terms
        if not any(_matches(product, term) for product in head)
    ]


def expect_slugs_in_top(
    products: Sequence[dict[str, Any]], slugs: Sequence[str], k: int
) -> list[Violation]:
    """The right *product*, named in a way translation cannot move.

    Every other content check reads the text a shopper sees, which is exactly
    what a multilingual case cannot assert on: the Vietnamese title of the
    cooking class is not the English one, and writing the expected translation
    into the case would test the translator's word choice rather than whether
    search found the right thing. The slug is derived once from the source
    listing and is identical in all eight locales, so it is the only handle
    that means the same thing in every language.
    """
    if not slugs:
        return []
    head = {str(product.get("slug") or "") for product in products[:k]}
    return [
        Violation("expect_slugs_in_top", f"'{slug}' absent from top {k}")
        for slug in slugs
        if slug not in head
    ]


def max_price(products: Sequence[dict[str, Any]], ceiling: float) -> list[Violation]:
    """A budget is a promise, not a preference."""
    return [
        Violation(
            "max_price",
            f"{_label(product)} costs {product.get('price')} over ceiling {ceiling}",
        )
        for product in products
        if float(product.get("price") or 0) > ceiling
    ]


def min_rating(products: Sequence[dict[str, Any]], floor: float) -> list[Violation]:
    return [
        Violation(
            "min_rating",
            f"{_label(product)} rated {product.get('rating')} below floor {floor}",
        )
        for product in products
        if float(product.get("rating") or 0) < floor
    ]


def max_duration_minutes(products: Sequence[dict[str, Any]], ceiling: int) -> list[Violation]:
    return [
        Violation(
            "max_duration_minutes",
            f"{_label(product)} runs {product.get('duration_minutes')}m over {ceiling}m",
        )
        for product in products
        if int(product.get("duration_minutes") or 0) > ceiling
    ]


def require_category(products: Sequence[dict[str, Any]], category: str) -> list[Violation]:
    return [
        Violation(
            "require_category",
            f"{_label(product)} is {product.get('category')}, not {category}",
        )
        for product in products
        if str(product.get("category") or "").lower() != category.lower()
    ]


def min_results(products: Sequence[dict[str, Any]], count: int) -> list[Violation]:
    """Silence is also a failure: a reasonable request must return supply."""
    if len(products) >= count:
        return []
    return [Violation("min_results", f"returned {len(products)}, expected >= {count}")]


def max_results(products: Sequence[dict[str, Any]], count: int) -> list[Violation]:
    """Used for 'we do not sell this' cases, where the honest answer is none.

    A restaurant question against a catalogue of tours must not be answered
    with the tours that happen to rank highest. Returning nothing and saying
    so is the correct behaviour.
    """
    if len(products) <= count:
        return []
    return [Violation("max_results", f"returned {len(products)}, expected <= {count}")]


def ascending(products: Sequence[dict[str, Any]], field: str) -> list[Violation]:
    """Explicit sort controls must not be quietly overridden by ranking."""
    values = [float(product.get(field) or 0) for product in products]
    for index in range(1, len(values)):
        if values[index] < values[index - 1] - 1e-9:
            return [
                Violation(
                    "ascending",
                    f"{field} fell from {values[index - 1]} to {values[index]} at position {index}",
                )
            ]
    return []


def descending(products: Sequence[dict[str, Any]], field: str) -> list[Violation]:
    values = [float(product.get(field) or 0) for product in products]
    for index in range(1, len(values)):
        if values[index] > values[index - 1] + 1e-9:
            return [
                Violation(
                    "descending",
                    f"{field} rose from {values[index - 1]} to {values[index]} at position {index}",
                )
            ]
    return []


def message_mentions(message: str, terms: Sequence[str]) -> list[Violation]:
    """The prose has to acknowledge the situation, not just the payload.

    Paired with `max_results: 0`, this is what separates "we do not sell
    restaurant bookings" from an empty screen.
    """
    lowered = message.lower()
    return [
        Violation("message_mentions", f"reply never mentions '{term}'")
        for term in terms
        if re.search(_word_pattern(term), lowered) is None
    ]


def grounded(products: Sequence[dict[str, Any]], offered_ids: set[str]) -> list[Violation]:
    """Every product shown must trace back to something a tool actually returned.

    This is the convention the agent is held to: it curates and explains, it
    does not invent. An ungrounded card is unbookable, so this is a hard
    failure rather than a quality score.
    """
    if not offered_ids:
        return []
    return [
        Violation("grounded", f"{_label(product)} was never returned by a tool")
        for product in products
        if str(product.get("id")) not in offered_ids
    ]


CHECKS = {
    "forbid_terms": forbid_terms,
    "require_any_term": require_any_term,
    "max_price": max_price,
    "min_rating": min_rating,
    "max_duration_minutes": max_duration_minutes,
    "require_category": require_category,
    "min_results": min_results,
    "max_results": max_results,
    "ascending": ascending,
    "descending": descending,
}


def run_checks(
    products: Sequence[dict[str, Any]],
    expectations: dict[str, Any],
    message: str = "",
    offered_ids: set[str] | None = None,
) -> list[Violation]:
    """Apply every expectation declared on a case."""
    violations: list[Violation] = []
    for name, argument in expectations.items():
        if name == "expect_terms_in_top":
            violations.extend(
                expect_terms_in_top(products, argument["terms"], argument.get("k", 5))
            )
            continue
        if name == "expect_slugs_in_top":
            violations.extend(
                expect_slugs_in_top(products, argument["slugs"], argument.get("k", 5))
            )
            continue
        if name == "message_mentions":
            violations.extend(message_mentions(message, argument))
            continue
        check = CHECKS.get(name)
        if check is None:
            violations.append(Violation("unknown_check", name))
            continue
        violations.extend(check(products, argument))  # type: ignore[operator]
    if offered_ids is not None:
        violations.extend(grounded(products, offered_ids))
    return violations
