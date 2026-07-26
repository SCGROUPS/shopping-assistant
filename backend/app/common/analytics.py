"""Funnel measurement and the assistant holdout.

`docs/SYSTEM_DESIGN.md` §13: the core thesis of this product is that a guided
assistant converts tourists better than manual search. Without a holdout that
claim is unfalsifiable, and an unfalsifiable claim cannot be tuned. Without
per-surface attribution we cannot tell whether the assistant, the grid, or a
recommendation rail earned a booking, so we cannot decide where to invest.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from app.common.config import get_settings
from app.common.models import BehaviorEvent, ShoppingSession
from app.common.persistence import database_mode, require_session_factory
from app.common.store import DemoStore, store

# Ordered funnel. Every stage must be attributable to the surface that produced
# it, otherwise "the assistant converts better" is unmeasurable.
FUNNEL_STAGES = (
    "experience_impression",
    "experience_viewed",
    "cart_item_added",
    "checkout_started",
    "booking_completed",
)

# Events that prove the shopper actually engaged with the assistant, as opposed
# to merely having it available.
ASSISTANT_TOUCH_EVENTS = frozenset(
    {"assistant_message_sent", "assistant_action_clicked", "assistant_opened"}
)

# A nudge that is shown and never accepted is an interruption, not a service.
# Tracked per trigger so a specific trigger can be retired on evidence.
NUDGE_EVENTS = {
    "assistant_nudge_shown": "shown",
    "assistant_nudge_accepted": "accepted",
    "assistant_nudge_dismissed": "dismissed",
}

DEFAULT_SURFACE = "grid"


def assistant_holdout(anonymous_id: str) -> bool:
    """Deterministically assign a session to the no-assistant control group.

    Deterministic on the session id so a shopper's experience never flips
    mid-visit, and so the assignment survives a restart without being stored.
    """
    rate = get_settings().assistant_holdout_rate
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"assistant-holdout\x00{anonymous_id}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return bucket < rate


def _surface(event: dict[str, Any]) -> str:
    placement = event.get("placement")
    return placement or DEFAULT_SURFACE


async def funnel_report(data: DemoStore = store) -> dict[str, Any]:
    """Stage counts per surface, plus assistant-touched vs. control conversion."""
    by_surface: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(FUNNEL_STAGES, 0))
    touched_sessions: set[str] = set()
    booked_sessions: set[str] = set()
    all_sessions: set[str] = set()
    nudges: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(NUDGE_EVENTS.values(), 0))
    searches = 0
    zero_results = 0
    relaxed_recoveries = 0

    if not database_mode():
        session_names = {
            session["id"]: anonymous_id for anonymous_id, session in data.sessions.items()
        }
        for event in data.events:
            anonymous_id = session_names.get(event["session_id"], str(event["session_id"]))
            all_sessions.add(anonymous_id)
            event_type = event["event_type"]
            if event_type in FUNNEL_STAGES:
                by_surface[_surface(event)][event_type] += 1
            if event_type in ASSISTANT_TOUCH_EVENTS:
                touched_sessions.add(anonymous_id)
            if event_type == "booking_completed":
                booked_sessions.add(anonymous_id)
            if event_type in NUDGE_EVENTS:
                trigger = str(event.get("properties", {}).get("trigger") or "unknown")
                nudges[trigger][NUDGE_EVENTS[event_type]] += 1
            if event_type == "search_submitted":
                searches += 1
            elif event_type == "search_zero_results":
                zero_results += 1
            elif event_type == "search_relaxed":
                relaxed_recoveries += 1
    else:
        factory = require_session_factory()
        async with factory() as db:
            rows = await db.execute(
                select(
                    BehaviorEvent.event_type,
                    BehaviorEvent.placement,
                    func.count(),
                )
                .where(BehaviorEvent.event_type.in_(FUNNEL_STAGES))
                .group_by(BehaviorEvent.event_type, BehaviorEvent.placement)
            )
            for event_type, placement, count in rows.all():
                by_surface[placement or DEFAULT_SURFACE][event_type] += int(count)

            session_rows = await db.execute(
                select(ShoppingSession.anonymous_id, BehaviorEvent.event_type)
                .join(BehaviorEvent, BehaviorEvent.session_id == ShoppingSession.id)
                .where(BehaviorEvent.event_type.in_([*ASSISTANT_TOUCH_EVENTS, "booking_completed"]))
            )
            for anonymous_id, event_type in session_rows.all():
                all_sessions.add(anonymous_id)
                if event_type in ASSISTANT_TOUCH_EVENTS:
                    touched_sessions.add(anonymous_id)
                else:
                    booked_sessions.add(anonymous_id)

            # The JSON path must be a single expression object. Building it
            # twice yields two bind parameters, which PostgreSQL sees as two
            # different expressions and rejects with a GROUPING error — a
            # failure that only appears against a real database.
            trigger = BehaviorEvent.properties["trigger"].astext.label("trigger")
            nudge_rows = await db.execute(
                select(
                    BehaviorEvent.event_type,
                    trigger,
                    func.count(),
                )
                .where(BehaviorEvent.event_type.in_(list(NUDGE_EVENTS)))
                .group_by(BehaviorEvent.event_type, trigger)
            )
            for event_type, trigger, count in nudge_rows.all():
                nudges[trigger or "unknown"][NUDGE_EVENTS[event_type]] += int(count)

            search_rows = await db.execute(
                select(BehaviorEvent.event_type, func.count())
                .where(
                    BehaviorEvent.event_type.in_(
                        ["search_submitted", "search_zero_results", "search_relaxed"]
                    )
                )
                .group_by(BehaviorEvent.event_type)
            )
            counts = {event_type: int(count) for event_type, count in search_rows.all()}
            searches = counts.get("search_submitted", 0)
            zero_results = counts.get("search_zero_results", 0)
            relaxed_recoveries = counts.get("search_relaxed", 0)

    untouched = all_sessions - touched_sessions
    return {
        "surfaces": {surface: dict(stages) for surface, stages in sorted(by_surface.items())},
        "totals": {
            stage: sum(stages[stage] for stages in by_surface.values()) for stage in FUNNEL_STAGES
        },
        "assistant": {
            "touched_sessions": len(touched_sessions),
            "untouched_sessions": len(untouched),
            "touched_conversion": _rate(touched_sessions & booked_sessions, touched_sessions),
            "untouched_conversion": _rate(untouched & booked_sessions, untouched),
            "holdout_rate": get_settings().assistant_holdout_rate,
        },
        "nudges": {
            trigger: {
                **counts,
                "accept_rate": (
                    round(counts["accepted"] / counts["shown"], 4) if counts["shown"] else None
                ),
            }
            for trigger, counts in sorted(nudges.items())
        },
        "search_health": {
            "searches": searches,
            "zero_results": zero_results,
            "zero_result_rate": round(zero_results / searches, 4) if searches else None,
            "relaxed_recoveries": relaxed_recoveries,
            "recovery_rate": (
                round(relaxed_recoveries / zero_results, 4) if zero_results else None
            ),
        },
    }


def _rate(numerator: set[str], denominator: set[str]) -> float | None:
    """None, not zero, when there is no evidence — an unmeasured rate is not 0%."""
    if not denominator:
        return None
    return round(len(numerator) / len(denominator), 4)
