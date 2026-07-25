import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.schemas import Participant, SearchFilters
from app.common.features import availability_fit, conversion_lift, price_fit
from app.common.ranking import (
    bayesian_rating,
    cosine_similarity,
    deterministic_embedding,
    mmr_diversify,
    reciprocal_rank_fusion,
    smoothed_rate,
    time_decay,
    tokenize,
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
    sweet = _product(
        options=[
            {**_product()["options"][0], "prices": [{"participant_type": "adult", "amount": 150.0}]}
        ]
    )
    at_ceiling = _product(
        options=[
            {**_product()["options"][0], "prices": [{"participant_type": "adult", "amount": 200.0}]}
        ]
    )
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


def test_ascii_tokenisation_is_unchanged():
    """The Latin path must not shift, or every existing ranking result moves."""
    assert tokenize("Sunset Cruise 2024") == ["sunset", "cruise", "2024"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("日落游船", ["日落", "落游", "游船"]),
        ("일몰 크루즈", ["일몰", "크루", "루즈"]),
        ("du thuyền hoàng hôn", ["du", "thuyen", "hoang", "hon"]),
        ("croisière", ["croisiere"]),
    ],
)
def test_non_latin_text_produces_tokens(text: str, expected: list[str]):
    """Regression guard for a tokenizer that emitted nothing at all for CJK.

    While ``[a-z0-9]+`` was the pattern, Chinese produced an empty token list
    and therefore a zero vector, so a multilingual eval case could only pass by
    degenerating into browsing - a green suite over a broken feature.
    """
    assert tokenize(text) == expected


def test_cjk_embeddings_separate_related_from_unrelated():
    related = cosine_similarity(
        deterministic_embedding("会安 日落游船"), deterministic_embedding("日落游船")
    )
    unrelated = cosine_similarity(
        deterministic_embedding("日落游船"), deterministic_embedding("烹饪课程")
    )
    assert related > 0.5
    assert unrelated < 0.2


def test_diacritics_fold_so_accentless_typing_still_matches():
    """Applied to document and query alike, so recall rises without asymmetry."""
    accented = deterministic_embedding("du thuyền hoàng hôn")
    plain = deterministic_embedding("du thuyen hoang hon")
    assert cosine_similarity(accented, plain) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("accented", "plain"),
    [
        ("Đà Nẵng", "da nang"),
        ("Đầm Sen", "dam sen"),
        ("đường phố", "duong pho"),
    ],
)
def test_stroke_letters_fold_like_postgres_unaccent(accented: str, plain: str):
    """``đ`` carries a stroke, not a combining mark, so NFD alone leaves it.

    PostgreSQL's ``unaccent`` maps it to ``d``. If Python disagreed, the lexical
    vector and the database index would tokenise Vietnam's second city two
    different ways and a shopper typing "da nang" would miss "Đà Nẵng".
    """
    assert tokenize(accented) == tokenize(plain)


def test_post_create_entries_are_single_statements():
    """Guards against Python's implicit string concatenation in the list.

    A missing comma between two adjacent triple-quoted members silently fuses
    them into one string. The result is still a valid list, so nothing fails
    until PostgreSQL rejects the second ``CREATE``, and only on a fresh
    database - which is to say, in front of a real deployment.
    """
    from app.common.schema import POST_CREATE

    for statement in POST_CREATE:
        keywords = re.findall(r"\bCREATE\b(?!\s+OR\s+REPLACE)", statement, flags=re.IGNORECASE)
        assert len(keywords) <= 1, f"fused POST_CREATE entry: {statement[:120]!r}"
