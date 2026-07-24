from httpx import AsyncClient

from app.api.schemas import IntentValue, SearchIntent, SearchRequest
from app.common.ranking import deterministic_embedding
from app.search.service import SearchService


class HallucinatedCountryProvider:
    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Vietnam", confidence=0.92),
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_natural_language_search_applies_hard_filters(client: AsyncClient):
    response = await client.post(
        "/api/v1/search",
        json={"query": "family friendly indoor activities in Hoi An for children"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]
    assert payload["intent"]["destination"]["name"] == "Hoi An"
    assert payload["effective_filters"]["destination"] == "Hoi An"
    assert all(item["destination"] == "Hoi An" for item in payload["items"])
    assert all("Family friendly" in item["badges"] for item in payload["items"])
    assert all(item["options"] for item in payload["items"])


async def test_explicit_filter_overrides_inferred_destination(client: AsyncClient):
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "indoor things in Hoi An",
            "filters": {"destination": "Da Nang"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_filters"]["destination"] == "Da Nang"
    assert all(item["destination"] == "Da Nang" for item in payload["items"])


async def test_search_detail_and_availability(client: AsyncClient):
    search = await client.post("/api/v1/search", json={"query": "lantern workshop"})
    product_id = search.json()["items"][0]["id"]
    detail = await client.get(f"/api/v1/experiences/{product_id}")
    availability = await client.get(f"/api/v1/experiences/{product_id}/availability")
    assert detail.status_code == 200
    assert detail.json()["options"][0]["prices"]
    assert availability.status_code == 200
    assert availability.json()["options"][0]["slots"]


async def test_experience_list_frontend_contract(client: AsyncClient):
    response = await client.get("/api/v1/experiences", params={"destination": "Hoi An"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    product = payload["items"][0]
    required = {
        "id",
        "slug",
        "title",
        "location",
        "destination",
        "category",
        "short_description",
        "image_url",
        "rating",
        "review_count",
        "price",
        "currency",
        "duration_minutes",
        "tags",
        "badges",
        "reason",
        "options",
    }
    assert required.issubset(product)


async def test_demo_cors_allows_localhost_ports(client: AsyncClient):
    response = await client.options(
        "/api/v1/experiences",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_unsupported_inferred_country_does_not_eliminate_results():
    result = await SearchService(
        ai_provider=HallucinatedCountryProvider()
    ).search(
        SearchRequest(query="A relaxed family day with food and culture")
    )

    assert result.items
    assert result.intent.destination.name is None
    assert result.effective_filters.destination is None
