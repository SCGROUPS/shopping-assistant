"""Honest urgency and social proof.

Both are among the strongest known conversion levers and among the easiest to
abuse. The rule this module enforces: **every claim must be a fact already true
in the data, and must be suppressed when the number is too small to mean
anything.** A fabricated "only 2 left!" converts once and destroys trust
permanently — which for a marketplace selling to tourists who will never return
is a straight trade of reputation for one booking.

See docs/SYSTEM_DESIGN.md §10.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.schemas import Participant, SearchFilters
from app.common.features import party_size

# Below this, remaining capacity is genuinely scarce and worth saying.
SCARCITY_THRESHOLD = 6
# Above this, "limited availability" is just a normal inventory level.
BUSY_DAY_THRESHOLD = 3
# Below this, a demand count is noise dressed as evidence.
SOCIAL_PROOF_FLOOR = 5
DEFAULT_HORIZON_DAYS = 14


def scarcity(
    product: dict[str, Any],
    filters: SearchFilters | None = None,
    party: Sequence[Participant] = (),
    now: datetime | None = None,
) -> str | None:
    """Real remaining capacity on the dates the shopper actually asked for.

    Returns None whenever supply is comfortable, so the badge keeps its meaning.
    """
    filters = filters or SearchFilters()
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
        for option in product["options"]
        if option["active"]
        for slot in option["slots"]
        if slot["status"] == "AVAILABLE"
        and start <= slot["starts_at"].date() <= end
        and slot["capacity_remaining"] >= needed
    ]
    if not usable:
        return None

    if len(usable) <= BUSY_DAY_THRESHOLD:
        best = max(slot["capacity_remaining"] for slot in usable)
        if best <= SCARCITY_THRESHOLD:
            plural = "s" if best != 1 else ""
            return f"Only {best} place{plural} left"
        times = "time" if len(usable) == 1 else "times"
        return f"Just {len(usable)} {times} left in your dates"

    tightest = min(slot["capacity_remaining"] for slot in usable)
    if tightest <= SCARCITY_THRESHOLD:
        plural = "s" if tightest != 1 else ""
        return f"Some times down to {tightest} place{plural}"
    return None


def social_proof(stats: dict[str, float] | None) -> str | None:
    """Observed demand, or nothing.

    Deliberately silent for quiet inventory rather than reaching for the seeded
    review count: a review count is not evidence that anyone booked recently,
    and presenting it as such would be the dishonest version of this feature.
    """
    if not stats:
        return None
    bookings = int(stats.get("bookings", 0))
    if bookings >= SOCIAL_PROOF_FLOOR:
        return f"{bookings} travellers booked this recently"
    carts = int(stats.get("cart_adds", 0))
    if carts >= SOCIAL_PROOF_FLOOR:
        return f"In {carts} travellers' plans right now"
    views = int(stats.get("views", 0))
    if views >= SOCIAL_PROOF_FLOOR * 4:
        return f"Viewed {views} times recently"
    return None
