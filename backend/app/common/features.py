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
# Keys are exactly app.catalog.vocabulary.CATEGORIES, pinned by a test. This
# table outlived two vocabulary changes carrying spellings the catalogue had
# stopped using ("Food experience", "Transport ticket"), which silently earned
# every renamed item the default rate instead of its own.
CATEGORY_TAKE_RATE: dict[str, float] = {
    "Day trip": 0.18,
    "Cruise": 0.18,
    "Guided tour": 0.16,
    "Activity or class": 0.15,
    "Entertainment experience": 0.13,
    "Open-dated voucher": 0.12,
    "Culture": 0.12,
    "Wellness": 0.15,
    "Nature": 0.14,
    "Water": 0.15,
    "Family": 0.14,
    "Food": 0.14,
    "Transport": 0.08,
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

    Weighted, not a flat average. Every signal used to count the same, so a
    thing the shopper actually asked for was worth exactly as much as whether
    the item happens to offer four languages - and the more intrinsic signals
    were added, the less any stated preference moved the page. Something the
    shopper said outranks something we noticed on their behalf, and how much
    they wanted it is the model's reading of their words, not a constant here.

    With nothing expressed, every remaining signal is intrinsic and shares one
    weight, so the score is identical to the plain mean it replaces.
    """
    # Weighted mean of (value, weight) pairs.
    signals: list[tuple[float, float]] = []
    stated = 1.0
    # Noticed on the shopper's behalf: enough to break ties between items that
    # match what was asked for equally well, not enough to overturn it.
    noticed = 0.25

    wants_family = filters.family_friendly or any(
        person.type in {"child", "infant"} for person in party
    )
    if wants_family:
        # Stated if they asked; noticed if we inferred it from the party.
        weight = stated if filters.family_friendly else noticed
        signals.append((1.0 if product["family_friendly"] else 0.0, weight))

    # A category the shopper preferred but did not require. Without this the
    # soft branch had nowhere to go: `category` was the only category field
    # anything read, and it is a filter, so every stated interest was either
    # mandatory or discarded. Here it is what a preference should be - items in
    # the category rank above items outside it, and nothing is removed.
    if filters.preferred_category:
        matched = product["category"].casefold() == filters.preferred_category.casefold()
        # `or stated`: a model that omits the weight is not saying "barely
        # wants it", it is saying nothing, and the preference stands at full
        # strength. Zero means the model judged it not worth acting on.
        strength = filters.preferred_category_weight
        signals.append((1.0 if matched else 0.0, stated if strength is None else strength))

    if filters.indoor_outdoor:
        allowed = {filters.indoor_outdoor.casefold(), "mixed"}
        signals.append((1.0 if product["indoor_outdoor"].casefold() in allowed else 0.0, stated))

    if filters.max_duration_minutes:
        ratio = product["duration_minutes"] / filters.max_duration_minutes
        signals.append((max(0.0, min(1.0, 1.2 - ratio)), stated))

    if filters.rating is not None:
        signals.append((min(1.0, max(0.0, (product["rating"] - filters.rating) / 0.5)), stated))

    # Intrinsic signals: these matter to every tourist, whether or not they
    # thought to ask for them.
    signals.append((1.0 if product["instant_confirmation"] else 0.0, noticed))
    signals.append(
        (
            1.0
            if any(option["free_cancellation_hours"] > 0 for option in product["options"])
            else 0.0,
            noticed,
        )
    )
    signals.append((min(1.0, len(product["languages"]) / 4), noticed))
    total = sum(weight for _, weight in signals)
    if not total:
        # Every signal was weighted zero, which is the model saying none of this
        # should sway the order. Neutral, so relevance decides alone.
        return 0.0
    return sum(value * weight for value, weight in signals) / total


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


def margin_fit(product: dict[str, Any], take_rates: dict[str, float] | None = None) -> float:
    """Commercial value, normalised against the best rate on offer.

    Take rates are configuration (`runtime_config.category_take_rates`), so a
    commercial change is a value an operator edits rather than a release. The
    module-level dict remains the default and the fallback.
    """
    rates = take_rates or CATEGORY_TAKE_RATE
    ceiling = max([*rates.values(), DEFAULT_TAKE_RATE]) or MAX_TAKE_RATE
    rate = rates.get(product["category"], DEFAULT_TAKE_RATE)
    return rate / ceiling


def promotion_active(product: dict[str, Any], now: datetime | None = None) -> bool:
    """Is this product's merchandising window open?

    A campaign that outlives its dates is how a storefront ends up promoting
    Tet offers in June, so the window is checked at read time rather than
    trusted to a job that clears it.
    """
    moment = now or datetime.now(UTC)
    starts, ends = product.get("promotion_starts_at"), product.get("promotion_ends_at")
    if starts and moment < starts:
        return False
    return not (ends and moment > ends)


def merchandising_multiplier(
    product: dict[str, Any], ceiling: float = 1.5, now: datetime | None = None
) -> float:
    """The operator's thumb on the scale, bounded.

    Merchandising has to be able to move a product up the page without being
    able to replace relevance with whatever pays most, so the multiplier is
    clamped to a configured ceiling and only applies inside its window.
    """
    if not promotion_active(product, now):
        return 1.0
    try:
        boost = float(product.get("boost") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return min(max(boost, 1.0 / ceiling), ceiling)


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
