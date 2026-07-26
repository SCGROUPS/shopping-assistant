"""Contract tests for the Trippass supplier import.

These cover the mapping and the grounding rules, not the supplier's uptime:
every test drives real code with a recorded supplier payload, so a change that
starts inventing ratings, dropping variant prices, or filing Hoi An museums
under a promotion name fails here rather than in the storefront.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import RateLimitError

from app.catalog.trippass import (
    CATEGORIES,
    NORMALISE_TOOL,
    build_trippass_catalog,
    normalise,
    slugify,
    to_catalog_product,
)
from app.common.ranking import bayesian_rating

RAW_MUSEUM: dict[str, Any] = {
    "product_id": "69e8845e432b4c433efb5363",
    "status": "active",
    "name": "CSO Gallery Museum Admission Ticket - Hoi An",
    "sku": "CSO001",
    "description": "A museum of money, stamps and the Tale of Kieu in Hoi An old town.",
    "price_label": 135000,
    "image_urls": ["https://example.invalid/museum.jpg"],
    "category_name": "Hoi An",
    "variants": [
        {"name": "Adult", "description": "Aged 13+", "price": 135000},
        {"name": "Child", "description": "Aged 3-12", "price": 70000},
    ],
    "tags": [{"tag_id": "t1", "name": "Instant confirmation"}],
    "filter": {"location": {"country_code": "VN", "province_code": "48"}},
}

FACETS: dict[str, Any] = {
    "destination": "Hoi An",
    "category": "Culture",
    "subcategories": ["museum"],
    "interest_tags": ["Museum", "Coins", "Old Town"],
    "indoor_outdoor": "indoor",
    "duration_minutes": 90,
    "latitude": 15.8801,
    "longitude": 108.338,
    "meeting_point": "CSO Gallery, Hoi An old town.",
    "languages": ["English", "Vietnamese"],
    "accessibility_features": ["Elevator available"],
    "family_friendly": True,
    "minimum_age": None,
    "short_description": "A small Hoi An museum of money, stamps and the Tale of Kieu.",
    "needs_review": False,
}


class StubProvider:
    """Returns a scripted classification, or raises to exercise degradation."""

    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    async def structure(self, instructions: str, payload: dict, tool: dict) -> dict | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.result) if self.result is not None else None


class StubClient:
    def __init__(self, items: list[dict[str, Any]]):
        self.items = items

    async def list_products(self, language: str = "en", limit: int = 100) -> list[dict[str, Any]]:
        return self.items


def test_normalise_tool_constrains_the_facets_search_depends_on():
    properties = NORMALISE_TOOL["parameters"]["properties"]
    assert properties["category"]["enum"] == CATEGORIES
    assert set(properties["indoor_outdoor"]["enum"]) == {"indoor", "outdoor", "mixed"}
    # Strict schemas require every declared property to be required.
    assert set(NORMALISE_TOOL["parameters"]["required"]) == set(properties)
    assert NORMALISE_TOOL["strict"] is True


def test_supplier_collection_never_becomes_a_destination():
    """'Hot Deal' is merchandising, not a place, so the model resolves it."""
    raw = dict(RAW_MUSEUM, category_name="Hot Deal")
    product = to_catalog_product(raw, FACETS)
    assert product["destination"] == "Hoi An"
    assert "hot deal" not in product["destination"].casefold()


def test_every_variant_keeps_its_own_price():
    product = to_catalog_product(RAW_MUSEUM, FACETS)
    prices = {option["name"]: option["prices"][0]["amount"] for option in product["options"]}
    assert prices == {"Adult": 135000.0, "Child": 70000.0}
    assert all(option["prices"][0]["currency"] == "VND" for option in product["options"])


def test_import_does_not_invent_a_rating():
    product = to_catalog_product(RAW_MUSEUM, FACETS)
    assert product["rating"] == 0.0
    assert product["review_count"] == 0
    # Unreviewed supply must rank as unproven, not bad: the Bayesian prior has
    # to pull it to the catalogue mean or nothing imported would ever surface.
    assert bayesian_rating(product["rating"], product["review_count"]) == pytest.approx(4.6)


def test_mapping_carries_supplier_facts_and_normalised_facets():
    product = to_catalog_product(RAW_MUSEUM, FACETS)
    assert product["external_id"] == "TRIPPASS-69e8845e432b4c433efb5363"
    assert product["title"] == RAW_MUSEUM["name"]
    assert product["description"] == RAW_MUSEUM["description"]
    assert product["image_url"] == "https://example.invalid/museum.jpg"
    assert product["category"] == "Culture"
    assert product["indoor_outdoor"] == "indoor"
    assert product["duration_minutes"] == 90
    # Tags are matched case-insensitively downstream, so they are stored folded.
    assert product["interest_tags"] == ["museum", "coins", "old town"]


def test_slugs_and_ids_are_stable_across_runs():
    first = to_catalog_product(RAW_MUSEUM, FACETS)
    second = to_catalog_product(RAW_MUSEUM, FACETS)
    assert first["id"] == second["id"]
    assert first["slug"] == second["slug"]
    assert {o["id"] for o in first["options"]} == {o["id"] for o in second["options"]}
    assert slugify("Tour Essence of Hội An") == "tour-essence-of-h-i-an"


async def test_rate_limited_classification_is_retried_before_giving_up():
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    provider = StubProvider(error=RateLimitError("throttled", response=response, body=None))
    facets = await normalise(RAW_MUSEUM, provider, attempts=2)  # type: ignore[arg-type]
    assert provider.calls == 2
    # Degradation is explicit so an operator can find what was stored blind.
    assert facets["needs_review"] is True


async def test_classified_products_are_not_flagged_for_review():
    provider = StubProvider(result=FACETS)
    facets = await normalise(RAW_MUSEUM, provider)  # type: ignore[arg-type]
    assert facets["needs_review"] is False
    assert facets["destination"] == "Hoi An"


async def test_records_without_a_name_are_skipped_not_stored_blank():
    client = StubClient([RAW_MUSEUM, dict(RAW_MUSEUM, product_id="empty", name="")])
    catalog = await build_trippass_catalog(
        StubProvider(result=FACETS),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        days=1,
    )
    assert len(catalog) == 1
    assert catalog[0]["title"] == RAW_MUSEUM["name"]


async def test_imported_products_publish_bookable_availability():
    catalog = await build_trippass_catalog(
        StubProvider(result=FACETS),  # type: ignore[arg-type]
        client=StubClient([RAW_MUSEUM]),  # type: ignore[arg-type]
        days=3,
    )
    product = catalog[0]
    assert product["status"] == "PUBLISHED"
    for option in product["options"]:
        assert len(option["slots"]) == 3
        assert all(slot["capacity_remaining"] > 0 for slot in option["slots"])
        assert all(slot["ends_at"] > slot["starts_at"] for slot in option["slots"])
