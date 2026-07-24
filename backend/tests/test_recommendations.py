from httpx import AsyncClient


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
