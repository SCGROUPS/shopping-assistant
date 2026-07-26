"""Ask the intent model one real question before serving traffic.

Two outages had the same shape: a request shape the deployed model rejects with
HTTP 400 on every single call. Nothing noticed, because `search/service.py`
catches the failure and answers with a full page of products, so every health
check, every browser journey and the entire offline suite stayed green while the
service understood nothing anyone typed.

The defect is a property of the *pair* (our request, their deployment), so it
cannot be caught by reading either side alone - only by making the call. One
call at startup is enough: the failure is total and deterministic, never
intermittent, so if the first request is rejected every subsequent one will be.

Only a rejection of the request itself counts. A timeout, a 429 or a 500 is the
model having a bad minute and says nothing about whether our request is valid;
refusing to start over one would turn a transient upstream blip into an outage
of our own making. Those degrade as before - a page still beats an error page.

The outcome is deliberately three-valued. "Rejected" and "could not tell" are
different facts with different consequences - one must stop the revision, the
other must not - and collapsing them into a boolean forces the caller to
recover the difference by reading the message, which is the same fragility this
module refuses to rely on for classification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Sent instead of a real shopper's words: the point is to exercise the request
# shape, not to test comprehension, and it must cost as little as possible.
PROBE_QUERY = "things to do"

# The only two codes that mean "this request, or this deployment name, is
# wrong". Everything else is the upstream having a bad minute.
REQUEST_REJECTED_CODES = frozenset({400, 404})


class ProbeOutcome(Enum):
    """What the probe learned. Not a boolean: see the module docstring."""

    PASSED = "passed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ProbeResult:
    outcome: ProbeOutcome
    detail: str

    @property
    def rejected(self) -> bool:
        return self.outcome is ProbeOutcome.REJECTED

    @property
    def passed(self) -> bool:
        return self.outcome is ProbeOutcome.PASSED


def _is_request_rejected(error: BaseException) -> bool:
    """True when the model refused the request, not the moment.

    Matched on the status code rather than the message. The two outages read
    "Unsupported value: 'minimal'" and "Unsupported value: 'none'" - opposite
    text, one cause - so any check written against the wording of the first
    would have missed the second.

    Only 400 and 404 count, because only those two mean what this probe is
    looking for: a request the deployment will not accept, or a deployment name
    that is not there. A 401 or 403 is a credential that has expired or a
    managed identity that has not warmed up yet - real problems, but ones where
    refusing to serve turns an authentication wobble into a dark storefront,
    and where the previous revision holds no better credentials than this one.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    return status in REQUEST_REJECTED_CODES


async def probe_intent(provider, *, categories: list[str], destinations: list[str]) -> ProbeResult:
    """One real extract_intent call, reported rather than raised."""
    try:
        await provider.extract_intent(PROBE_QUERY, categories=categories, destinations=destinations)
    except Exception as error:  # noqa: BLE001 - classified, then reported
        if _is_request_rejected(error):
            return ProbeResult(
                outcome=ProbeOutcome.REJECTED,
                detail=(
                    f"the intent deployment rejected our request: {error}. Every search "
                    "would silently fall back to keyword parsing and still return a "
                    "normal-looking page. Check that reasoning.effort in "
                    "app/assistant/provider.py is a value this deployment accepts."
                ),
            )
        logger.warning("Intent probe could not complete (not a rejection): %s", error)
        return ProbeResult(outcome=ProbeOutcome.INCONCLUSIVE, detail=f"probe inconclusive: {error}")
    return ProbeResult(outcome=ProbeOutcome.PASSED, detail="intent deployment accepted our request")
