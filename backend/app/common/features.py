"""Ranking features shared by the search and recommendation engines.

Both engines must agree on what "fits" means, otherwise the storefront grid and
the recommendation rail argue with each other. Keeping the feature functions
here — rather than inline in either service — is what makes that possible.

See docs/SYSTEM_DESIGN.md §5.3 and §6.3.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.schemas import Participant, SearchFilters
from app.common.ranking import bayesian_rating

# Commercial value per booking. Real margin data does not exist in the POC
# catalogue (docs/SYSTEM_DESIGN.md §16), so category take rate is the proxy.
CATEGORY_TAKE_RATE: dict[str, float] = {
    "Day trip": 0.18,
    "Cruise": 0.18,
    "Guided tour": 0.16,
    "Activity or class": 0.15,
    "Food experience": 0.14,
    "Entertainment experience": 0.13,
    "Museum or cultural venue": 0.12,
    "Open-dated voucher": 0.12,
    "Culture": 0.12,
    "Wellness": 0.15,
    "Nature": 0.14,
    "Water": 0.15,
    "Family": 0.14,
    "Food": 0.14,
    "Transport": 0.08,
    "Transport ticket": 0.08,
}
DEFAULT_TAKE_RATE = 0.12
MAX_TAKE_RATE = max([*CATEGORY_TAKE_RATE.values(), DEFAULT_TAKE_RATE])

# Enough alternative slots that a shopper who wants a different time still has
# one. Beyond this, more supply does not make the item easier to book.
SUPPLY_SATURATION = 4
DEFAULT_HORIZON_DAYS = 14
# Tourists convert best somewhat below their stated ceiling, not at it.
BUDGET_SWEET_SPOT = 0.75


def party_size(party: Sequence[Participant]) -> int:
    return sum(person.count for person in party) or 1


def party_total(product: dict[str, Any], party: Sequence[Participant]) -> float:
    option = product["options"][0]
    prices = {price["participant_type"]: price["amount"] for price in option["prices"]}
    if not party:
        return float(prices.get("adult", 0))
    return float(sum(prices.get(person.type, 0) * person.count for person in party))


def _slots(product: dict[str, Any]):
    for option in product["options"]:
        yield from option["slots"]


def availability_fit(
    product: dict[str, Any],
    filters: SearchFilters,
    party: Sequence[Participant] = (),
    now: datetime | None = None,
) -> float:
    """How comfortably the shopper can actually book this on their dates.

    Eligibility already guarantees at least one bookable slot when a date is
    set; this measures the margin above that bare minimum, because a single
    remaining seat at one fixed time converts far worse than open choice.
    """
    needed = party_size(party)
    if filters.visit_start:
        start = filters.visit_start.date()
        end = (filters.visit_end or filters.visit_start).date()
    else:
        reference = now or datetime.now(UTC)
        start = reference.date()
        end = (reference + timedelta(days=DEFAULT_HORIZON_DAYS)).date()

    usable = [
        slot
        for slot in _slots(product)
        if slot["status"] == "AVAILABLE"
        and start <= slot["starts_at"].date() <= end
        and slot["capacity_remaining"] >= needed
    ]
    if not usable:
        return 0.0
    choice = min(1.0, len(usable) / SUPPLY_SATURATION)
    headroom = min(
        1.0,
        max(slot["capacity_remaining"] for slot in usable) / (needed * 3),
    )
    return 0.7 * choice + 0.3 * headroom


def preference_fit(
    product: dict[str, Any],
    filters: SearchFilters,
    party: Sequence[Participant] = (),
) -> float:
    """Fraction of the shopper's soft preferences this item satisfies.

    Hard constraints are handled by the eligibility gate, so the signals here
    are the ones that are allowed to be traded off. When the shopper has
    expressed nothing, intrinsic party-fit signals keep the term discriminating
    instead of collapsing to a constant.
    """
    signals: list[float] = []

    wants_family = filters.family_friendly or any(
        person.type in {"child", "infant"} for person in party
    )
    if wants_family:
        signals.append(1.0 if product["family_friendly"] else 0.0)

    if filters.indoor_outdoor:
        allowed = {filters.indoor_outdoor.casefold(), "mixed"}
        signals.append(1.0 if product["indoor_outdoor"].casefold() in allowed else 0.0)

    if filters.max_duration_minutes:
        ratio = product["duration_minutes"] / filters.max_duration_minutes
        signals.append(max(0.0, min(1.0, 1.2 - ratio)))

    if filters.rating is not None:
        signals.append(min(1.0, max(0.0, (product["rating"] - filters.rating) / 0.5)))

    # Intrinsic signals: these matter to every tourist, whether or not they
    # thought to ask for them.
    signals.append(1.0 if product["instant_confirmation"] else 0.0)
    signals.append(
        1.0
        if any(option["free_cancellation_hours"] > 0 for option in product["options"])
        else 0.0
    )
    signals.append(min(1.0, len(product["languages"]) / 4))
    return sum(signals) / len(signals)


def price_fit(
    product: dict[str, Any],
    filters: SearchFilters,
    party: Sequence[Participant] = (),
    reference_total: float | None = None,
) -> float:
    """Distance from the shopper's budget band.

    Peaks below the ceiling rather than at it: an option that consumes the whole
    budget leaves nothing for the rest of the trip and converts worse than one
    that leaves room.
    """
    total = party_total(product, party)
    ceiling = filters.max_total_price or reference_total
    if not ceiling or total <= 0:
        return 0.5
    ratio = total / ceiling
    return max(0.0, min(1.0, 1 - abs(ratio - BUDGET_SWEET_SPOT) / BUDGET_SWEET_SPOT))


def conversion_lift(rate: float, prior: float) -> float:
    """Map an absolute conversion rate onto [0,1] with the prior at the midpoint.

    A raw rate cannot be used directly (it lives near 0.02, so it would barely
    move the score), and dividing by the prior and clamping would park every
    unobserved item at the ceiling — inert, which is the defect this whole term
    exists to remove. This squash returns 0.5 at exactly the prior, rises toward
    1 for proven sellers and falls toward 0 for proven non-sellers.
    """
    if rate <= 0:
        return 0.0
    return rate / (rate + max(prior, 1e-6))


def quality(product: dict[str, Any]) -> float:
    return bayesian_rating(product["rating"], product["review_count"]) / 5


def margin_fit(product: dict[str, Any]) -> float:
    rate = CATEGORY_TAKE_RATE.get(product["category"], DEFAULT_TAKE_RATE)
    return rate / MAX_TAKE_RATE


def context_fit(
    product: dict[str, Any],
    filters: SearchFilters,
    party: Sequence[Participant] = (),
    reference_total: float | None = None,
) -> float:
    """Blend of the situational features, for the recommendation rail.

    Replaces the destination check that used to sit here, which could not
    discriminate because candidates were already hard-filtered on destination.
    """
    return (
        0.4 * availability_fit(product, filters, party)
        + 0.35 * preference_fit(product, filters, party)
        + 0.25 * price_fit(product, filters, party, reference_total)
    )


def median_party_total(
    products: Sequence[dict[str, Any]], party: Sequence[Participant] = ()
) -> float | None:
    totals = sorted(party_total(product, party) for product in products)
    if not totals:
        return None
    middle = len(totals) // 2
    if len(totals) % 2:
        return totals[middle]
    return (totals[middle - 1] + totals[middle]) / 2
