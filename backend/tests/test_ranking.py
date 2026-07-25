from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.schemas import Participant, SearchFilters
from app.common.features import availability_fit, conversion_lift, price_fit
from app.common.ranking import (
    bayesian_rating,
    deterministic_embedding,
    mmr_diversify,
    reciprocal_rank_fusion,
    smoothed_rate,
    time_decay,
)


def test_rrf_rewards_results_present_in_both_lists():
    scores = reciprocal_rank_fusion(["a", "b", "c"], ["c", "a", "d"], k=60)
    assert scores["a"] > scores["b"]
    assert scores["c"] > scores["d"]


def test_bayesian_rating_limits_single_review_outlier():
    one_review = bayesian_rating(5.0, 1)
    established = bayesian_rating(4.8, 500)
    assert established > one_review


def test_mmr_limits_narrow_subcategory():
    candidates = [
        (f"item-{index}", 1 - index * 0.01, deterministic_embedding(f"museum {index}"), "museum")
        for index in range(5)
    ]
    candidates += [
        ("food", 0.8, deterministic_embedding("food cooking"), "food"),
        ("cruise", 0.79, deterministic_embedding("river boat"), "cruise"),
    ]
    result = mmr_diversify(candidates, 5)
    assert "food" in result
    assert "cruise" in result


def test_time_decay_favours_recent_signals():
    half_life = 1800
    assert time_decay(0, half_life) == 1.0
    assert time_decay(half_life, half_life) == pytest.approx(0.5)
    assert time_decay(half_life, half_life) > time_decay(4 * half_life, half_life)


def test_smoothed_rate_returns_prior_for_unobserved_inventory():
    prior = 0.02
    assert smoothed_rate(0, 0, prior, 40) == pytest.approx(prior)
    # A single lucky booking must not outrank a well-measured item.
    lucky = smoothed_rate(1, 1, prior, 40)
    proven = smoothed_rate(30, 300, prior, 40)
    assert lucky < proven
    assert lucky < 0.1


def _product(**overrides):
    product = {
        "id": uuid4(),
        "category": "Day trip",
        "rating": 4.6,
        "review_count": 200,
        "duration_minutes": 240,
        "family_friendly": True,
        "indoor_outdoor": "outdoor",
        "instant_confirmation": True,
        "languages": ["English", "Vietnamese"],
        "options": [
            {
                "free_cancellation_hours": 24,
                "prices": [{"participant_type": "adult", "amount": 100.0}],
                "slots": [],
            }
        ],
    }
    product.update(overrides)
    return product


def _slots(count: int, capacity: int = 20):
    base = datetime.now(UTC) + timedelta(days=1)
    return [
        {
            "status": "AVAILABLE",
            "starts_at": base + timedelta(days=index),
            "capacity_remaining": capacity,
        }
        for index in range(count)
    ]


def test_availability_fit_prefers_open_choice():
    filters = SearchFilters(
        visit_start=datetime.now(UTC), visit_end=datetime.now(UTC) + timedelta(days=10)
    )
    scarce = _product()
    scarce["options"][0]["slots"] = _slots(1, capacity=2)
    plentiful = _product()
    plentiful["options"][0]["slots"] = _slots(6, capacity=20)

    party = [Participant(type="adult", count=2)]
    assert availability_fit(plentiful, filters, party) > availability_fit(scarce, filters, party)
    sold_out = _product()
    assert availability_fit(sold_out, filters, party) == 0.0


def test_price_fit_peaks_below_the_ceiling():
    filters = SearchFilters(max_total_price=200)
    party = [Participant(type="adult", count=1)]
    sweet = _product(options=[{**_product()["options"][0], "prices": [
        {"participant_type": "adult", "amount": 150.0}
    ]}])
    at_ceiling = _product(options=[{**_product()["options"][0], "prices": [
        {"participant_type": "adult", "amount": 200.0}
    ]}])
    assert price_fit(sweet, filters, party) > price_fit(at_ceiling, filters, party)


def test_conversion_lift_keeps_the_prior_at_the_midpoint():
    """The term must discriminate, not saturate.

    Dividing a smoothed rate by the prior and clamping to 1.0 would park every
    unobserved item at the ceiling and reintroduce the inert-constant defect.
    """
    prior = 0.02
    assert conversion_lift(prior, prior) == pytest.approx(0.5)
    assert conversion_lift(4 * prior, prior) > 0.5
    assert conversion_lift(prior / 4, prior) < 0.5
    assert conversion_lift(0.0, prior) == 0.0
