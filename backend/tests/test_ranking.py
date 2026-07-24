from app.common.ranking import (
    bayesian_rating,
    deterministic_embedding,
    mmr_diversify,
    reciprocal_rank_fusion,
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
