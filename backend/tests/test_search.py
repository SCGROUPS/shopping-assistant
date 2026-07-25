from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.schemas import (
    IntentValue,
    Participant,
    SearchFilters,
    SearchIntent,
    SearchRequest,
)
from app.assistant.provider import deterministic_intent
from app.common.config import get_settings
from app.common.features import availability_fit
from app.common.persistence import catalog_products
from app.common.ranking import deterministic_embedding
from app.search.service import SearchService, is_eligible, merge_filters, sanitize_intent


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


def test_intent_constraints_map_without_silent_relaxation():
    intent = deterministic_intent(
        "English indoor activity on 2026-08-15 under VND 1000000 "
        "with free cancellation and no nightlife"
    )
    filters, unresolved = merge_filters(SearchFilters(), intent)

    assert unresolved == []
    assert filters.visit_start is not None
    assert filters.visit_start.date().isoformat() == "2026-08-15"
    assert filters.max_total_price == 1_000_000
    assert filters.currency == "VND"
    assert filters.language == "English"
    assert filters.indoor_outdoor == "indoor"
    assert filters.free_cancellation is True
    assert filters.exclusions == ["nightlife"]


def test_hallucinated_dates_do_not_override_explicit_visit_date():
    intent = SearchIntent(
        search_text="family food and culture",
        hard_constraints=[
            {"field": "visit_start", "operator": ">=", "value": "2026-07-24"},
            {"field": "visit_end", "operator": "<=", "value": "2026-07-24"},
        ],
    )
    sanitized = sanitize_intent("family food and culture", intent)
    filters, unresolved = merge_filters(
        SearchFilters(visit_start="2026-08-15T00:00:00Z"),
        sanitized,
    )

    assert unresolved == []
    assert filters.visit_start is not None
    assert filters.visit_end == filters.visit_start


def test_conflicting_inferred_end_is_discarded():
    intent = SearchIntent(
        search_text="family food and culture",
        hard_constraints=[
            {"field": "visit_end", "operator": "<=", "value": "2026-07-24"},
        ],
    )
    filters, unresolved = merge_filters(
        SearchFilters(visit_start="2026-08-15T00:00:00Z"),
        sanitize_intent("Family day on 2026-08-15", intent),
    )

    assert unresolved == []
    assert filters.visit_end == filters.visit_start


def test_invalid_explicit_date_range_is_normalized_for_clarification():
    filters, unresolved = merge_filters(
        SearchFilters(
            visit_start="2026-08-15T00:00:00Z",
            visit_end="2026-07-24T00:00:00Z",
        ),
        SearchIntent(search_text="family food and culture"),
    )

    assert unresolved == ["visit_end"]
    assert filters.visit_end == filters.visit_start


def test_optional_filters_and_multi_category_interests_do_not_block_search():
    intent = SearchIntent(
        search_text="A relaxed family day with food and culture",
        hard_constraints=[
            {
                "field": "category",
                "operator": "in",
                "value": ["family_friendly", "cultural", "food_and_drink"],
            },
            {"field": "indoor_outdoor", "operator": "unspecified", "value": ""},
        ],
        soft_preferences=[
            {"field": "family_friendly", "value": True, "weight": 1.0},
        ],
        needs_clarification=True,
        clarification_question="Please specify optional filters.",
    )

    sanitized = sanitize_intent(
        "A relaxed family day with food and culture",
        intent,
    )
    filters, unresolved = merge_filters(SearchFilters(), sanitized)

    assert sanitized.needs_clarification is False
    assert sanitized.clarification_question is None
    assert sanitized.hard_constraints == []
    assert unresolved == []
    assert filters.category is None
    assert filters.indoor_outdoor is None
    assert filters.family_friendly is True


async def test_relaxation_recovers_from_zero_results(client: AsyncClient):
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "museum",
            "filters": {
                "destination": "Hoi An",
                "category": "Cruise",
                "max_duration_minutes": 5,
                "rating": 4.9,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"], "relaxation should recover bookable results"
    assert payload["relaxed_preferences"], "the shopper must be told what changed"


async def test_relaxation_never_drops_accessibility(client: AsyncClient):
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "impossible combination",
            "filters": {
                "accessibility": ["wheelchair"],
                "max_duration_minutes": 1,
                "rating": 5.0,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "accessibility" not in " ".join(payload["relaxed_preferences"])
    for item in payload["items"]:
        detail = await client.get(f"/api/v1/experiences/{item['id']}")
        features = " ".join(detail.json()["accessibility_features"]).casefold()
        assert "wheelchair" in features


async def test_facets_allow_sideways_drill_down(client: AsyncClient):
    response = await client.post(
        "/api/v1/search",
        json={"query": "things to do", "filters": {"category": "Food & drink"}},
    )
    assert response.status_code == 200
    facets = response.json()["facets"]
    assert len(facets["category"]) > 1, (
        "category counts must ignore the category filter so shoppers can switch tabs"
    )
    assert facets["destination"], "destination counts should still be populated"


async def test_ranking_prefers_comfortably_bookable_inventory(client: AsyncClient):
    """Availability is a ranking term, not just a gate.

    Two items that are equally relevant should not tie when one has a single
    remaining seat: the shopper who picks it is far likelier to bounce.
    """
    products = await catalog_products()
    scarce, plentiful = products[0], products[1]
    for option in scarce["options"][1:]:
        option["slots"] = []
    scarce["options"][0]["slots"] = scarce["options"][0]["slots"][:1]
    for slot in scarce["options"][0]["slots"]:
        slot["capacity_remaining"] = 1

    visit = datetime.now(UTC) + timedelta(days=2)
    filters = SearchFilters(visit_start=visit, visit_end=visit + timedelta(days=7))
    party = [Participant(type="adult", count=1)]
    assert availability_fit(plentiful, filters, party) > availability_fit(scarce, filters, party)


async def test_search_score_no_longer_carries_dead_constants():
    """Every term in the objective must be able to discriminate.

    The old score added 0.10 + 0.05 to every candidate, which changed no
    ordering at all. Guard against that regressing.
    """
    settings = get_settings()
    weights = [
        settings.search_weight_relevance,
        settings.search_weight_preference_fit,
        settings.search_weight_availability_fit,
        settings.search_weight_price_fit,
        settings.search_weight_quality,
        settings.search_weight_conversion,
        settings.search_weight_margin,
    ]
    assert all(weight > 0 for weight in weights)
    assert sum(weights) == pytest.approx(1.0)


def test_mixed_settings_satisfy_either_indoor_or_outdoor_preference():
    """A part-indoor, part-outdoor experience answers both preferences.

    "mixed" used to be admitted only for an indoor preference, so an outdoor
    shopper never saw a Ba Na Hills cable-car combo - the cable car is outdoors
    and the buffet is not. Supplier inventory is mostly mixed, so the asymmetry
    silently hid the strongest imported products.
    """
    product = {
        "status": "PUBLISHED",
        "indoor_outdoor": "mixed",
        "duration_minutes": 720,
        "languages": ["English"],
        "accessibility_features": [],
        "family_friendly": True,
        "instant_confirmation": True,
        "options": [],
        "minimum_age": None,
        "rating": 4.6,
    }
    for preference in ("indoor", "outdoor"):
        assert is_eligible(product, SearchFilters(indoor_outdoor=preference))

    indoor_only = dict(product, indoor_outdoor="indoor")
    assert is_eligible(indoor_only, SearchFilters(indoor_outdoor="indoor"))
    assert not is_eligible(indoor_only, SearchFilters(indoor_outdoor="outdoor"))
