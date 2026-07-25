from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.common.persistence import catalog_products, demand_stats, event_history
from app.common.ranking import cosine_similarity
from app.common.runtime_config import get_config
from app.recommendations.service import RecommendationService, _popularity


async def test_recommendations_are_diverse_and_explained(client: AsyncClient):
    search = await client.post("/api/v1/search", json={"query": "Hoi An history"})
    current = search.json()["items"][0]["id"]
    response = await client.get(
        "/api/v1/recommendations",
        params={
            "placement": "complete_your_day",
            "experience_id": current,
            "limit": 6,
        },
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 4
    assert all(item["reason_code"] for item in items)
    assert all(item["reason"] for item in items)
    assert len({item["id"] for item in items}) == len(items)


async def test_recommendations_respect_budget_gate(client: AsyncClient):
    search = await client.post("/api/v1/search", json={"query": "Hoi An history"})
    items = search.json()["items"]
    current = items[0]["id"]
    budget = max(item["price"] for item in items) * 2
    response = await client.get(
        "/api/v1/recommendations",
        params={
            "placement": "complete_your_day",
            "experience_id": current,
            "limit": 6,
            "travellers": 2,
            "max_total_price": budget,
        },
    )
    assert response.status_code == 200
    recommended = response.json()["items"]
    assert recommended, "budget gate should still return bookable options"
    assert all(item["price"] * 2 <= budget for item in recommended)


async def test_recommendations_exclude_unavailable_dates(client: AsyncClient):
    response = await client.get(
        "/api/v1/recommendations",
        params={
            "placement": "for_you",
            "limit": 6,
            "visit_start": "2030-01-01T00:00:00Z",
            "travellers": 2,
        },
    )
    assert response.status_code == 200
    # No seeded slot exists in 2030, so the rail is empty rather than unbookable.
    assert response.json()["items"] == []


async def test_recent_behaviour_outranks_older_behaviour(client: AsyncClient):
    """The session vector is time-decayed, so a fresh signal must win."""
    search = await client.post("/api/v1/search", json={"query": "Da Nang"})
    items = search.json()["items"]
    stale, fresh = items[0]["id"], items[1]["id"]

    now = datetime.now(UTC)
    for experience_id, occurred_at in (
        (stale, now - timedelta(days=3)),
        (fresh, now - timedelta(minutes=2)),
    ):
        response = await client.post(
            "/api/v1/events",
            json={
                "event_type": "experience_viewed",
                "experience_id": experience_id,
                "occurred_at": occurred_at.isoformat(),
            },
        )
        assert response.status_code == 202

    vector = RecommendationService()._session_vector(
        await event_history("test-session"),
        {product["id"]: product for product in await catalog_products()},
    )
    assert vector is not None

    products = {str(product["id"]): product for product in await catalog_products()}
    assert cosine_similarity(vector, products[fresh]["embedding"]) > cosine_similarity(
        vector, products[stale]["embedding"]
    )


async def test_observed_demand_overrides_the_seeded_popularity_proxy(client: AsyncClient):
    """Popularity is a measurement once real demand exists, not a static seed."""
    search = await client.post("/api/v1/search", json={"query": "Da Nang"})
    target = search.json()["items"][-1]["id"]
    products = {str(product["id"]): product for product in await catalog_products()}
    product = products[target]

    before = _popularity(product, None)
    for _ in range(30):
        await client.post(
            "/api/v1/events",
            json={"event_type": "booking_completed", "experience_id": target},
        )
    stats = (await demand_stats()).get(product["id"])
    assert stats and stats["bookings"] == 30
    assert _popularity(product, stats) > before


async def test_cold_start_redistributes_the_session_weight():
    service = RecommendationService()
    configured = (await get_config())["recommendation_weights"]
    cold = service._weights(has_session=False, configured=configured)
    warm = service._weights(has_session=True, configured=configured)
    assert cold["session"] == 0.0
    assert cold["context_fit"] > warm["context_fit"]
    assert sum(cold.values()) == pytest.approx(sum(warm.values()))
