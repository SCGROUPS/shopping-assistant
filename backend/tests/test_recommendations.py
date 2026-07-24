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
