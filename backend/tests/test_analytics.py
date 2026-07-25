from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.common.analytics import assistant_holdout, funnel_report
from app.common.config import get_settings
from app.common.embedding_cache import cache_key, normalize
from app.common.llm_cost import CostLedger, estimate_cost


def test_normalization_folds_trivially_different_queries():
    """Case and whitespace differences must not cost a second embedding call."""
    assert normalize("  Hoi An   Lantern Tour ") == "hoi an lantern tour"
    model = "text-embedding-3-small"
    assert cache_key("Hoi An  lantern tour", model) == cache_key(
        "hoi an lantern tour", model
    )
    assert cache_key("hoi an lantern tour", model) != cache_key(
        "hoi an lantern tour", "text-embedding-3-large"
    )


def test_ledger_trips_the_breaker_at_the_daily_budget():
    ledger = CostLedger()
    assert not ledger.exhausted(1.0)
    ledger.record("gpt-5.4-mini", 4_000_000, 1_000_000, "assistant_prose")
    assert ledger.exhausted(1.0)
    snapshot = ledger.snapshot()
    assert snapshot.calls == 1
    assert snapshot.by_purpose["assistant_prose"] == pytest.approx(snapshot.total)


def test_a_zero_budget_disables_the_breaker_rather_than_blocking_everything():
    """Zero must mean "no ceiling", not "refuse every call"."""
    ledger = CostLedger()
    ledger.record("gpt-5.4-mini", 10_000_000, 10_000_000)
    assert not ledger.exhausted(0.0)


def test_embeddings_are_cheaper_than_chat():
    embedding = estimate_cost("text-embedding-3-small", 1_000_000, 0)
    chat = estimate_cost("gpt-5.4-mini", 1_000_000, 0)
    assert embedding < chat
    assert estimate_cost("some-unknown-model", 0, 0) == 0


def test_holdout_is_deterministic_and_off_by_default():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.assistant_holdout_rate == 0.0
    assert not assistant_holdout("any-session")

    settings.assistant_holdout_rate = 0.5
    try:
        first = assistant_holdout("session-abc")
        assert first == assistant_holdout("session-abc"), "must not flip mid-visit"
        assigned = [assistant_holdout(f"session-{index}") for index in range(400)]
        share = sum(assigned) / len(assigned)
        assert 0.4 < share < 0.6
    finally:
        settings.assistant_holdout_rate = 0.0
        get_settings.cache_clear()


async def test_funnel_attributes_stages_to_the_surface_that_produced_them(
    client: AsyncClient,
):
    search = await client.post("/api/v1/search", json={"query": "Hoi An"})
    experience_id = search.json()["items"][0]["id"]
    for event_type, placement in (
        ("experience_impression", "grid"),
        ("experience_viewed", "grid"),
        ("experience_viewed", "assistant"),
        ("cart_item_added", "assistant"),
        ("booking_completed", "assistant"),
    ):
        response = await client.post(
            "/api/v1/events",
            json={
                "event_type": event_type,
                "experience_id": experience_id,
                "placement": placement,
            },
        )
        assert response.status_code == 202

    report = await funnel_report()
    assert report["surfaces"]["grid"]["experience_viewed"] == 1
    assert report["surfaces"]["assistant"]["experience_viewed"] == 1
    assert report["surfaces"]["assistant"]["booking_completed"] == 1
    assert report["totals"]["experience_viewed"] == 2


async def test_funnel_reports_none_not_zero_for_unmeasured_conversion(
    client: AsyncClient,
):
    """An unmeasured rate is unknown, not 0% — reporting 0 would look like failure."""
    report = await funnel_report()
    assert report["assistant"]["touched_conversion"] is None
    assert report["search_health"]["zero_result_rate"] is None


async def test_nudge_accept_rate_is_tracked_per_trigger(client: AsyncClient):
    for event_type, trigger in (
        ("assistant_nudge_shown", "refinement_loop"),
        ("assistant_nudge_shown", "refinement_loop"),
        ("assistant_nudge_accepted", "refinement_loop"),
        ("assistant_nudge_shown", "zero_results"),
        ("assistant_nudge_dismissed", "zero_results"),
    ):
        await client.post(
            "/api/v1/events",
            json={"event_type": event_type, "properties": {"trigger": trigger}},
        )

    report = await funnel_report()
    assert report["nudges"]["refinement_loop"]["accept_rate"] == 0.5
    assert report["nudges"]["zero_results"]["accept_rate"] == 0.0
    assert report["nudges"]["zero_results"]["dismissed"] == 1


async def test_zero_result_recovery_rate_is_measured(client: AsyncClient):
    for event_type in ("search_submitted", "search_submitted", "search_zero_results"):
        await client.post("/api/v1/events", json={"event_type": event_type})
    await client.post("/api/v1/events", json={"event_type": "search_relaxed"})

    report = await funnel_report()
    health = report["search_health"]
    assert health["searches"] == 2
    assert health["zero_result_rate"] == 0.5
    assert health["recovery_rate"] == 1.0


async def test_session_context_exposes_the_cohort(client: AsyncClient):
    response = await client.get("/api/v1/session/context")
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_enabled"] is True
    assert body["assistant_holdout"] is False


async def test_funnel_endpoint_reports_spend_against_the_budget(client: AsyncClient):
    response = await client.get("/api/v1/analytics/funnel")
    assert response.status_code == 200
    cost = response.json()["cost"]
    assert cost["budget_usd"] == get_settings().openai_daily_budget
    assert cost["breaker_tripped"] is False
    assert cost["day"] == datetime.now(UTC).date().isoformat()
    assert cost["spent_usd"] == 0.0
