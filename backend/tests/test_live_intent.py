"""Tests that run the real model against the real catalogue.

Every other suite stubs the provider, which is correct for pinning what the
service does with an intent - but it means the model's own behaviour is never
under test. Both defects that reached production hid in exactly that gap:

  * the extractor invented categories this catalogue does not stock
    ("attractions", "sightseeing or lantern festival"), and the category filter
    is an exact match, so the grid came back empty;
  * it expressed the place as a hard constraint instead of `intent.destination`,
    which nothing mapped, so the request was handed back as unresolved.

Neither is reachable with a deterministic stub. Both are trivially reachable
here. The model is non-deterministic, so each query runs several times and the
invariant has to hold on every one - a single green pass proves nothing about a
failure that shows up half the time.

Opt-in, because it costs money and needs network:

    LIVE_LLM_TESTS=1 \\
    AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... \\
    LIVE_DATABASE_URL=postgresql+psycopg://... \\
    .venv/bin/python -m pytest tests/test_live_intent.py -q
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.assistant.provider import HARD_CONSTRAINT_FIELDS, AzureOpenAIProvider
from app.common.config import get_settings
from app.search.service import CONSTRAINT_FIELDS

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_LLM_TESTS") != "1",
    reason="LIVE_LLM_TESTS=1 opts in to real model and real catalogue calls",
)

# Enough to catch a coin toss, few enough to keep the bill and the runtime
# sane. The production defect showed at roughly one call in two, so five runs
# miss it about three times in a hundred.
RUNS = 5

# Phrased the way shoppers actually type: lowercase, unpunctuated, a place plus
# a thing. "hoi an lantern" is the exact query that was failing in production.
QUERIES = [
    "hoi an lantern",
    "da nang cable car",
    "things to do in hanoi",
    "family day out in ho chi minh city",
    "đi thuyền ở hội an",
]


def _live_database_url() -> str:
    url = os.getenv("LIVE_DATABASE_URL")
    if not url:
        pytest.skip("LIVE_DATABASE_URL is required")
    return url


async def _catalogue_vocabulary() -> tuple[set[str], set[str]]:
    engine = create_async_engine(_live_database_url())
    try:
        async with engine.connect() as connection:
            categories = (
                await connection.execute(
                    text("SELECT DISTINCT category FROM experiences WHERE category IS NOT NULL")
                )
            ).scalars()
            destinations = (
                await connection.execute(text("SELECT DISTINCT name FROM destinations"))
            ).scalars()
            return (
                {value.casefold() for value in categories},
                {value.casefold() for value in destinations},
            )
    finally:
        await engine.dispose()


def _provider() -> AzureOpenAIProvider:
    settings = get_settings()
    if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
        pytest.skip("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required")
    return AzureOpenAIProvider(settings)


@pytest.mark.parametrize("query", QUERIES)
async def test_the_model_only_names_constraint_fields_the_service_maps(query: str):
    """A field nothing maps becomes an unresolved constraint and an empty grid.

    This is the `destination` defect. The prompt had always listed the
    permitted names in prose and the model used others anyway; the names are a
    schema enum now, so this is what proves the enum is doing its job against
    the live model rather than only in a fixture.
    """
    provider = _provider()
    seen: set[str] = set()
    for _ in range(RUNS):
        intent = await provider.extract_intent(query)
        seen.update(str(c.get("field", "")).casefold() for c in intent.hard_constraints)

    unmapped = sorted(field for field in seen if field and field not in CONSTRAINT_FIELDS)
    assert unmapped == [], f"{query!r} produced constraint fields nothing maps: {unmapped}"
    assert seen <= set(HARD_CONSTRAINT_FIELDS) | {""}


@pytest.mark.parametrize("query", QUERIES)
async def test_the_model_only_names_categories_the_catalogue_stocks(query: str):
    """The category filter is exact, so an invented value empties the grid.

    The service clears an unmatched category before it can filter, so this does
    not gate correctness - it gates *cost*. A model that keeps inventing
    categories is one whose every answer silently discards a constraint, and
    nothing else in the suite would ever say so.
    """
    categories, _ = await _catalogue_vocabulary()
    provider = _provider()

    # The real vocabulary has to be handed over, exactly as the search service
    # hands it over. Calling this without it leaves the schema enum as [null],
    # which makes a category impossible rather than correct - the assertion
    # would pass while testing nothing.
    offered = sorted({value for value in categories})
    assert offered, "the catalogue must have categories for this to mean anything"

    invented: list[str] = []
    for _ in range(RUNS):
        intent = await provider.extract_intent(query, categories=offered)
        for constraint in intent.hard_constraints:
            if str(constraint.get("field", "")).casefold() != "category":
                continue
            value = str(constraint.get("value", "")).casefold()
            if value and value not in categories:
                invented.append(value)

    assert invented == [], (
        f"{query!r} produced categories this catalogue does not stock: {sorted(set(invented))}. "
        f"Real vocabulary: {sorted(categories)}"
    )


async def test_a_named_city_is_read_as_a_destination_we_sell():
    """The half of the defect that made the place itself unusable.

    Whichever shape the model chooses - `intent.destination` or a hard
    constraint - the city has to come back as one the catalogue sells, or the
    shopper who named it correctly gets nothing.
    """
    _CATEGORIES, destinations = await _catalogue_vocabulary()
    provider = _provider()

    for _ in range(RUNS):
        intent = await provider.extract_intent("hoi an lantern", categories=sorted(_CATEGORIES))
        named = [intent.destination.name] if intent.destination.name else []
        named += [
            str(c.get("value", ""))
            for c in intent.hard_constraints
            if str(c.get("field", "")).casefold() == "destination"
        ]
        assert named, "the model named no destination at all for an explicitly located query"
        for value in named:
            folded = value.casefold()
            assert any(folded in known or known in folded for known in destinations), (
                f"{value!r} is not a destination this catalogue sells"
            )
