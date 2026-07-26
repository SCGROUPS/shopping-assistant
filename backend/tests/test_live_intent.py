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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assistant.provider import HARD_CONSTRAINT_FIELDS, AzureOpenAIProvider
from app.common.config import get_settings
from app.common.locales import DEFAULT_LOCALE
from app.common.persistence import load_products
from app.common.ranking import strip_accents
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


async def _catalogue_vocabulary() -> tuple[list[str], list[str]]:
    """Exactly the vocabulary production builds, by calling what production calls.

    This used to run its own two SELECTs, and they disagreed with production in
    three ways that all made the test easier to pass than the real thing: it
    casefolded both lists, so a model answering in the wrong case looked
    correct; it read every row of `experiences` rather than published inventory;
    and it took destinations from the `destinations` table, which includes
    cities holding nothing anyone can book. A test whose fixture is more
    forgiving than production cannot tell you production works, so it now
    derives the lists the way `SearchService.search` does - from
    `catalog_products` - and any future change there is inherited rather than
    re-implemented.
    """
    engine = create_async_engine(_live_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            products = await load_products(db, locale=DEFAULT_LOCALE)
    finally:
        await engine.dispose()

    if not products:
        pytest.skip("the live catalogue returned no published products")
    return (
        sorted({product["category"] for product in products}),
        sorted({product["destination"] for product in products}),
    )


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
    # The catalogue's own vocabulary, because the request production sends
    # carries it and the schema is built from it. Asking without it was not a
    # weaker version of this test but a different one: the category and
    # destination enums collapse to [null], so the model is answering a question
    # production never asks and the constraint fields it picks need not match.
    categories, destinations = await _catalogue_vocabulary()
    provider = _provider()
    seen: set[str] = set()
    for _ in range(RUNS):
        intent = await provider.extract_intent(
            query, categories=categories, destinations=destinations
        )
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
    categories, destinations = await _catalogue_vocabulary()
    provider = _provider()

    # The real vocabulary has to be handed over, exactly as the search service
    # hands it over. Calling this without it leaves the schema enum as [null],
    # which makes a category impossible rather than correct - the assertion
    # would pass while testing nothing.
    assert categories, "the catalogue must have categories for this to mean anything"
    # Compared case-insensitively because the eligibility gate is
    # case-insensitive, but *offered* in the catalogue's own case - the model is
    # asked the same question production asks it.
    known = {value.casefold() for value in categories}

    invented: list[str] = []
    for _ in range(RUNS):
        intent = await provider.extract_intent(
            query, categories=categories, destinations=destinations
        )
        # Both branches, because a category is now a hard constraint only when
        # the shopper insisted and a soft preference otherwise. Scanning
        # `hard_constraints` alone used to be the whole story; after the
        # soft/hard split it silently checks almost nothing, since most phrasings
        # take the soft path.
        stated = [
            str(c.get("value", ""))
            for c in intent.hard_constraints
            if str(c.get("field", "")).casefold() == "category"
        ] + [
            str(p.get("value", ""))
            for p in intent.soft_preferences
            if str(p.get("field", "")).casefold() == "category"
        ]
        invented += [value for value in stated if value and value.casefold() not in known]

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
    categories, destinations = await _catalogue_vocabulary()
    provider = _provider()

    for _ in range(RUNS):
        intent = await provider.extract_intent(
            "hoi an lantern",
            categories=categories,
            destinations=destinations,
        )
        named = [intent.destination.name] if intent.destination.name else []
        named += [
            str(c.get("value", ""))
            for c in intent.hard_constraints
            if str(c.get("field", "")).casefold() == "destination"
        ]
        assert named, "the model named no destination at all for an explicitly located query"
        for value in named:
            folded = strip_accents(value).casefold()
            assert any(folded == strip_accents(known).casefold() for known in destinations), (
                f"{value!r} is not a destination this catalogue sells"
            )


@pytest.mark.parametrize(
    ("query", "language"),
    [
        ("đi thuyền ở hội an", "Vietnamese, with the diacritics the city is actually spelled with"),
        ("会安灯笼之旅", "Chinese, where the city has its own name entirely"),
        ("Bootstour in Hoi An", "German"),
        ("호이안 등불 투어", "Korean"),
    ],
)
async def test_a_city_named_in_the_shoppers_language_still_resolves(query: str, language: str):
    """The multilingual promise, checked against the model rather than assumed.

    A shopper writing "会安" has named Hoi An perfectly; the catalogue stores
    "Hoi An". No amount of string folding bridges that, so the resolution has to
    happen where the language knowledge is - in the model - which is why
    destination is an enum of real places rather than free text. Before that
    change these queries returned the model's own spelling, the service could
    not match it, and the destination was silently discarded: the shopper who
    was most precise got the least useful page.
    """
    categories, destinations = await _catalogue_vocabulary()
    provider = _provider()

    for _ in range(RUNS):
        intent = await provider.extract_intent(
            query, categories=categories, destinations=destinations
        )
        resolved = intent.destination.name or ""
        # Compared the way the service compares. The enum constrains meaning,
        # not letter case - the model answers "hoi an" as readily as "Hoi An" -
        # and asserting the exact string would fail a request that works.
        assert strip_accents(resolved).casefold() == "hoi an", (
            f"{query!r} ({language}) resolved to {resolved!r}, which is not Hoi An. "
            "The enum should let the model do the translating."
        )
