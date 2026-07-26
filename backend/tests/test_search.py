from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.schemas import (
    IntentValue,
    Participant,
    SearchFilters,
    SearchIntent,
    SearchRequest,
)
from app.catalog.vocabulary import CATEGORIES
from app.common.config import get_settings
from app.common.degradation import intent_health
from app.common.features import (
    CATEGORY_TAKE_RATE,
    INTRINSIC_BUDGET,
    availability_fit,
    margin_fit,
    merchandising_multiplier,
    preference_fit,
    promotion_active,
)
from app.common.persistence import catalog_products
from app.common.ranking import deterministic_embedding
from app.search.service import SearchService, is_eligible, merge_filters, sanitize_intent


class HallucinatedCountryProvider:
    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Vietnam", confidence=0.92),
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class InventedCategoryProvider:
    """A model that answers with a category this catalogue does not stock.

    Not hypothetical. Production served "hoi an lantern" eight times and
    returned nothing on five of them, because the extractor variously called it
    "attractions", "tourist attraction", "sightseeing or lantern festival" and
    "experiences". The destination came back as "Hoi An" every time; only the
    category moved, and because that filter is an exact match against a closed
    vocabulary, each invented value matched no product at all.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            hard_constraints=[
                {"field": "category", "operator": "eq", "value": "sightseeing or lantern festival"}
            ],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class DestinationAsConstraintProvider:
    """The place expressed as a hard constraint instead of `intent.destination`.

    A reasonable reading - the destination *is* a hard constraint - and the
    model used it on about a quarter of production calls. `CONSTRAINT_FIELDS`
    had no mapping for it, so it landed in `unresolved` and forced the early
    return: the shopper named a real city and got an empty grid asking them to
    clarify the thing they had just been specific about.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            hard_constraints=[{"field": "destination", "operator": "eq", "value": "Hoi An"}],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class InventedDestinationConstraintProvider:
    """The same shape, naming a place the catalogue does not sell."""

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            hard_constraints=[{"field": "destination", "operator": "eq", "value": "Reykjavik"}],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class VocabularyRecordingProvider:
    """Captures what the service actually offered the model."""

    def __init__(self) -> None:
        self.categories: list[str] | None = None
        self.destinations: list[str] | None = None

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(
        self, text: str, *, categories=None, destinations=None
    ) -> SearchIntent:
        self.categories, self.destinations = categories, destinations
        return SearchIntent(search_text=text)

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_the_service_offers_the_model_the_catalogues_own_words():
    """Both vocabularies are enums, so omitting one silently empties it.

    `enum: [*(destinations or []), None]` collapses to `[None]` when the
    service forgets to pass the list, and the model then has exactly one legal
    answer: no destination at all. That failure is invisible - no error, no
    log, just a shopper who named a city and searched the whole country - so
    the call site is pinned here rather than left to review.
    """
    provider = VocabularyRecordingProvider()
    await SearchService(ai_provider=provider).search(SearchRequest(query="a boat trip somewhere"))

    assert provider.categories, "the service offered the model no categories"
    assert provider.destinations, "the service offered the model no destinations"
    assert "Hoi An" in provider.destinations


class VietnameseDestinationProvider:
    """The model reads the city correctly, and writes it the way it was typed.

    "Hội An" is not a mistake - it is the same place, spelled properly. The
    guard compared it to the catalogue's "Hoi An" with `casefold` alone, which
    does not fold diacritics, so a Vietnamese shopper who named the city
    perfectly had the destination silently discarded and searched the whole
    country instead.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Hội An", confidence=0.95),
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class FamilyIndoorIntentProvider:
    """The intent a model would extract for a family indoor request.

    Stated here rather than derived from the query, because deriving it is
    exactly what the codebase no longer does: reading `family` and `Hoi An` out
    of the text was English pattern-matching, and this test's subject is what
    the search service does with an intent, not how one is produced.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            interaction_mode="grid",
            destination=IntentValue(name="Hoi An", confidence=0.98),
            soft_preferences=[{"field": "family_friendly", "value": True, "weight": 0.9}],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_natural_language_search_applies_hard_filters(client: AsyncClient):
    result = await SearchService(ai_provider=FamilyIndoorIntentProvider()).search(
        SearchRequest(query="family friendly indoor activities in Hoi An for children")
    )

    assert result.items
    assert result.intent.destination.name == "Hoi An"
    assert result.effective_filters.destination == "Hoi An"
    assert all(item.destination == "Hoi An" for item in result.items)
    assert all(item.family_friendly for item in result.items)
    assert all("family_friendly" in item.badges for item in result.items)
    assert all(item.options for item in result.items)


async def test_unsupported_inferred_country_does_not_eliminate_results():
    result = await SearchService(ai_provider=HallucinatedCountryProvider()).search(
        SearchRequest(query="A relaxed family day with food and culture")
    )

    assert result.items
    assert result.intent.destination.name is None
    assert result.effective_filters.destination is None


async def test_an_invented_category_does_not_empty_the_results():
    """The defect that made production search a coin toss.

    A category the catalogue has never stocked cannot narrow a result set, only
    empty it, so it is not treated as something the shopper asked for. The drop
    is recorded rather than done quietly - the shopper is owed the knowledge
    that a constraint was set aside, and the client renders the code in its own
    language.
    """
    result = await SearchService(ai_provider=InventedCategoryProvider()).search(
        SearchRequest(query="hoi an lantern")
    )

    assert result.items
    assert result.effective_filters.category is None
    assert "category_unmatched" in result.unresolved_constraints


async def test_a_destination_written_with_its_own_diacritics_is_kept():
    """Folding accents is what every other comparison in the codebase does."""
    result = await SearchService(ai_provider=VietnameseDestinationProvider()).search(
        SearchRequest(query="đi thuyền ở hội an")
    )

    assert result.items
    assert result.effective_filters.destination == "Hội An"
    assert all(item.destination == "Hoi An" for item in result.items)
    assert "destination_unmatched" not in result.unresolved_constraints


def test_every_offered_constraint_field_is_mapped():
    """The schema and the mapping must not drift apart.

    `HARD_CONSTRAINT_FIELDS` is the enum the model picks from, and it lives in
    the provider because the search service imports that module and not the
    other way round. Nothing at runtime checks the two agree, so a name offered
    to the model but missing from `CONSTRAINT_FIELDS` would satisfy the schema,
    reach `merge_filters` unmapped, and force the empty grid - which is exactly
    what `destination` did in production.
    """
    from app.assistant.provider import HARD_CONSTRAINT_FIELDS
    from app.search.service import CONSTRAINT_FIELDS

    unmapped = [field for field in HARD_CONSTRAINT_FIELDS if field not in CONSTRAINT_FIELDS]
    assert unmapped == []


async def test_a_destination_stated_as_a_constraint_still_searches():
    """The second half of the production coin toss.

    Nothing about this request is unclear, so nothing about the response may
    ask the shopper to clarify it.
    """
    result = await SearchService(ai_provider=DestinationAsConstraintProvider()).search(
        SearchRequest(query="hoi an lantern")
    )

    assert result.items
    assert result.effective_filters.destination == "Hoi An"
    assert all(item.destination == "Hoi An" for item in result.items)
    assert result.unresolved_constraints == []
    assert result.intent.needs_clarification is False


async def test_an_invented_destination_constraint_does_not_empty_the_results():
    """Mapping the field must not become a way in for an invented city.

    The guard on `intent.destination` has always cleared a place the catalogue
    does not sell. A destination arriving as a constraint has to face the same
    check, or the fix above would trade one empty grid for another.
    """
    result = await SearchService(ai_provider=InventedDestinationConstraintProvider()).search(
        SearchRequest(query="reykjavik lantern")
    )

    assert result.items
    assert result.effective_filters.destination is None
    assert "destination_unmatched" in result.unresolved_constraints


def test_intent_constraints_map_without_silent_relaxation():
    """Constraints the model extracted must reach the filters intact.

    This used to build its input by running an English sentence through
    `deterministic_intent`, so it was really testing a keyword parser that no
    longer exists - and it would have gone on passing for English while saying
    nothing about any other language. The intent is stated directly now,
    because the subject is the mapping, not the parsing.
    """
    intent = SearchIntent(
        search_text="indoor activity",
        hard_constraints=[
            {"field": "visit_start", "operator": "eq", "value": "2026-08-15"},
            {"field": "max_total_price", "operator": "lte", "value": 1_000_000},
            {"field": "currency", "operator": "eq", "value": "VND"},
            {"field": "language", "operator": "eq", "value": "English"},
            {"field": "indoor_outdoor", "operator": "in", "value": ["indoor"]},
            {"field": "free_cancellation", "operator": "eq", "value": True},
        ],
        exclusions=["nightlife"],
        date_phrase="2026-08-15",
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


async def test_a_zero_result_search_offers_choices_instead_of_taking_them(client: AsyncClient):
    """Nothing is given up until the shopper says so.

    This search used to come back full of results, having quietly dropped the
    duration and rating the shopper asked for. Now it comes back empty and says
    what is on the table, so someone can ask them.
    """
    filters = {
        "destination": "Hoi An",
        "category": "Cruise",
        "max_duration_minutes": 5,
        "rating": 4.9,
    }
    response = await client.post("/api/v1/search", json={"query": "museum", "filters": filters})
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [], "the service must not relax on its own initiative"
    assert payload["relaxed_preferences"] == []
    assert set(payload["relaxation_candidates"]) == {
        "destination",
        "category",
        "max_duration",
        "rating",
    }


async def test_the_shopper_authorises_exactly_what_is_given_up(client: AsyncClient):
    """And nothing beyond it, even if more would have found results."""
    filters = {
        "destination": "Hoi An",
        "category": "Cruise",
        "max_duration_minutes": 5,
        "rating": 4.9,
    }
    response = await client.post(
        "/api/v1/search",
        json={"query": "museum", "filters": filters, "relax_order": ["max_duration", "rating"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"], "authorised relaxation should recover bookable results"
    assert set(payload["relaxed_preferences"]) <= {"max_duration", "rating"}
    assert "destination" not in payload["relaxed_preferences"]
    assert "category" not in payload["relaxed_preferences"]


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
            "relax_order": ["max_duration", "rating", "budget", "dates", "destination"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "accessibility" not in " ".join(payload["relaxed_preferences"])
    for item in payload["items"]:
        detail = await client.get(f"/api/v1/experiences/{item['id']}")
        features = " ".join(detail.json()["accessibility_features"]).casefold()
        assert "wheelchair" in features


async def test_an_exclusion_survives_every_authorisation(client: AsyncClient):
    """`exclude` is the one constraint stated negatively, so it is never on offer.

    A shopper who says "no boats" and then agrees to widen their dates has not
    agreed to be shown a boat.
    """
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "something to do",
            "filters": {"exclude": ["cruise"], "rating": 5.0, "max_duration_minutes": 1},
            "relax_order": [
                "max_duration",
                "rating",
                "category",
                "dates",
                "budget",
                "destination",
                "exclude",
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "exclude" not in payload["relaxed_preferences"]
    assert "exclude" not in payload["relaxation_candidates"]
    for item in payload["items"]:
        assert "cruise" not in item["title"].casefold()


async def test_facets_allow_sideways_drill_down(client: AsyncClient):
    # "Food & drink" is not a category this catalogue has. The test passed
    # anyway, because the search used to quietly drop the filter and count
    # facets over an unfiltered set - so it never once exercised drilling
    # sideways out of a real category.
    response = await client.post(
        "/api/v1/search",
        json={"query": "things to do", "filters": {"category": "Food"}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"], "'Food' is a real category and must return results"
    facets = payload["facets"]
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


def _listing(**overrides):
    product = {
        "id": uuid4(),
        "category": "Day trip",
        "rating": 4.5,
        "review_count": 100,
        "boost": 1.0,
        "pinned": False,
        "suppressed": False,
        "promotion_starts_at": None,
        "promotion_ends_at": None,
        "status": "PUBLISHED",
    }
    product.update(overrides)
    return product


def test_merchandising_is_inert_until_an_operator_uses_it():
    """A default listing must rank exactly as it did before these controls."""
    assert merchandising_multiplier(_listing()) == 1.0
    # Seeded demo products carry none of these keys at all.
    assert merchandising_multiplier({"category": "Day trip"}) == 1.0


def test_a_boost_moves_a_listing_but_cannot_run_away_with_the_page():
    """Merchandising should be a thumb on the scale, not a replacement for it."""
    assert merchandising_multiplier(_listing(boost=1.3), ceiling=1.5) == pytest.approx(1.3)
    # Beyond the ceiling the operator does not simply get what they asked for.
    assert merchandising_multiplier(_listing(boost=10.0), ceiling=1.5) == pytest.approx(1.5)
    assert merchandising_multiplier(_listing(boost=0.01), ceiling=1.5) == pytest.approx(1 / 1.5)


def test_a_promotion_outside_its_window_does_nothing():
    """A campaign that outlives its dates is how Tet offers show up in June."""
    past = datetime.now(UTC) - timedelta(days=10)
    future = datetime.now(UTC) + timedelta(days=10)
    expired = _listing(boost=1.5, promotion_starts_at=past, promotion_ends_at=past)
    scheduled = _listing(boost=1.5, promotion_starts_at=future, promotion_ends_at=future)
    running = _listing(boost=1.5, promotion_starts_at=past, promotion_ends_at=future)

    assert merchandising_multiplier(expired) == 1.0
    assert merchandising_multiplier(scheduled) == 1.0
    assert merchandising_multiplier(running, ceiling=1.5) == pytest.approx(1.5)
    assert not promotion_active(expired)
    assert promotion_active(running)


async def test_suppression_removes_a_listing_from_the_shared_eligibility_gate():
    """Pulling a product has to remove it everywhere, not rank it lower."""
    product = dict((await catalog_products())[0])
    assert is_eligible(product, SearchFilters())
    assert not is_eligible({**product, "suppressed": True}, SearchFilters())


def test_take_rates_come_from_configuration():
    """The commercial lever must be editable by a commercial person."""
    product = {"category": "Transport ticket"}
    default = margin_fit(product)
    lifted = margin_fit(product, {"Transport ticket": 0.30, "Day trip": 0.18})
    assert lifted > default


class TestExclusionsMatchWholeWords:
    """ "No spa" must not remove a planetarium.

    Both search paths tested the shopper's exclusion as a substring - `exclusion
    in searchable` in memory, `ILIKE '%spa%'` in Postgres - so ruling one thing
    out silently removed whole categories that merely contained the letters.
    The shopper saw a shorter list with no explanation, and the words they had
    used were nowhere near the things that vanished.
    """

    def test_a_short_exclusion_does_not_swallow_longer_words(self) -> None:
        from app.search.service import excluded_by

        assert not excluded_by("A quiet space museum and planetarium", ["spa"])
        assert not excluded_by("Sunrise start from Hoi An", ["art"])
        assert not excluded_by("Barbecue dinner cruise", ["bar"])

    def test_the_exclusion_still_matches_what_it_names(self) -> None:
        from app.search.service import excluded_by

        assert excluded_by("Luxury spa and hot spring", ["spa"])
        # The plural is why the substring test existed; it has to keep working.
        assert excluded_by("Marble Mountains half-day tour", ["mountain"])
        assert excluded_by("Water sports at My Khe", ["water sport"])

    def test_an_unaccented_exclusion_matches_accented_content(self) -> None:
        from app.search.service import excluded_by

        assert excluded_by("Tour núi Bà Nà", ["nui"])

    def test_both_backends_are_given_the_same_definition(self) -> None:
        """The in-memory path and the SQL path must rule out the same things.

        They are two implementations of one rule, and a disagreement between
        them is invisible: the demo store would exclude a product that
        production kept, with every test passing.

        Asserted against a real PostgreSQL, not against a Python
        re-implementation of the pattern. A parity test that runs `re.search`
        on both sides is the very defect it exists to catch: it would agree
        with itself perfectly while Postgres did something else with `\\m`,
        `unaccent`, or the escaping.
        """
        import asyncio
        import os

        from sqlalchemy import text as sql_text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.search.postgres import exclusion_patterns
        from app.search.service import excluded_by

        database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
        if not database_url:
            pytest.skip("POSTGRES_TEST_DATABASE_URL is required for backend parity")

        cases = [
            ("A quiet space museum and planetarium", "spa", False),
            ("Luxury spa and hot spring", "spa", True),
            ("Marble Mountains half-day tour", "mountain", True),
            ("Sunrise start from Hoi An", "art", False),
            ("Barbecue dinner cruise", "bar", False),
            ("Water sports at My Khe beach", "water sport", True),
            ("Tour n\u00fai B\u00e0 N\u00e0", "nui", True),
            # Punctuation between the words. `tokenize` splits on it while the
            # SQL pattern joined on `\s+`, so Python ruled this out and
            # production kept showing it to the shopper who said no.
            ("Water-sports at My Khe beach", "water sport", True),
            ("Spa/wellness afternoon", "spa", True),
            ("Half-day tour of the citadel", "half day", True),
            # ...but the boundary still has to hold inside a word.
            ("A spacecraft exhibition", "spa", False),
        ]

        async def from_postgres() -> list[bool]:
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    results = []
                    for haystack, term, _ in cases:
                        # The same shape the search query uses: excluded when
                        # any pattern matches the unaccented document.
                        row = await connection.execute(
                            sql_text(
                                "SELECT EXISTS ("
                                "  SELECT 1 FROM unnest(CAST(:patterns AS text[])) p"
                                "  WHERE unaccent(CAST(:haystack AS text)) ~* p"
                                ")"
                            ),
                            {"patterns": exclusion_patterns([term]), "haystack": haystack},
                        )
                        results.append(bool(row.scalar()))
                    return results
            finally:
                await engine.dispose()

        sql_results = asyncio.run(from_postgres())
        for (haystack, term, expected), in_sql in zip(cases, sql_results, strict=True):
            assert excluded_by(haystack, [term]) is expected, (haystack, term, "python")
            assert in_sql is expected, (haystack, term, "postgres")


class TestRelaxationIsAuthorisedNotAssumed:
    """Which constraint a shopper can most afford to lose is their judgement.

    The search used to relax on its own initiative down a fixed tuple, so it
    decided on everyone's behalf that the language their guide speaks matters
    more than their budget. Stating an order was not enough of a fix - the
    service still relaxed when nobody had said anything. `order` is now an
    authorisation: no code in it, nothing given up.
    """

    @staticmethod
    def _filters(**overrides: object) -> SearchFilters:
        return SearchFilters(
            max_total_price=1.0,
            language="klingon",
            **overrides,  # type: ignore[arg-type]
        )

    def test_nothing_is_given_up_when_nobody_authorised_anything(self) -> None:
        from app.search.service import relax_until_results

        _, filters, relaxed, candidates = relax_until_results([], self._filters(), [])
        assert relaxed == [], "an unauthorised relaxation is one the shopper never agreed to"
        # ...and the filters come back untouched, not merely unreported.
        assert filters.max_total_price == 1.0
        assert filters.language == "klingon"
        assert set(candidates) == {"language", "budget"}

    def test_only_what_was_authorised_is_given_up(self) -> None:
        from app.search.service import relax_until_results

        _, filters, relaxed, _ = relax_until_results([], self._filters(), [], order=["budget"])
        assert relaxed == ["budget"]
        assert filters.max_total_price is None
        assert filters.language == "klingon", "authorising the budget is not authorising the rest"

    def test_an_authorised_order_is_honoured(self) -> None:
        from app.search.service import relax_until_results

        _, _, relaxed, _ = relax_until_results(
            [], self._filters(), [], order=["budget", "language"]
        )
        assert relaxed.index("budget") < relaxed.index("language")

    def test_an_unknown_code_authorises_nothing(self) -> None:
        """The order comes from a model, so it can name something that is gone.

        It must not be read as blanket permission: a code we do not recognise is
        not a constraint the shopper agreed to lose.
        """
        from app.search.service import relax_until_results

        _, filters, relaxed, _ = relax_until_results(
            [], self._filters(), [], order=["not_a_constraint"]
        )
        assert relaxed == []
        assert filters.max_total_price == 1.0
        assert filters.language == "klingon"

    def test_candidates_are_reported_so_the_shopper_can_be_asked(self) -> None:
        from app.search.service import relaxation_candidates

        assert set(relaxation_candidates(self._filters())) == {"language", "budget"}


class SoftCategoryProvider:
    """The shopper mentioned a kind of thing; they did not rule out the rest.

    "I'd like to do a cooking class in Hoi An" names a preference. The prompt
    has always classed categories as soft "unless the user says must, only, or
    required", and the model is what reads that distinction out of the
    sentence, so it reports the category with `required` false.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Hoi An", confidence=0.9),
            soft_preferences=[{"field": "category", "value": "Food", "weight": 0.5}],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_a_preferred_category_ranks_matches_up_without_hiding_the_rest():
    """The directive: the model decides what is mandatory, not the service.

    Every non-null category used to be converted into a hard equality filter,
    whatever the model said about it, so a passing mention of food removed
    every non-food experience in the city. The shopper who would happily have
    seen a lantern workshop was shown a shorter page and told nothing.
    """
    response = await SearchService(ai_provider=SoftCategoryProvider()).search(
        SearchRequest(query="i'd like to eat well in hoi an")
    )

    categories = {item.category for item in response.items}
    assert len(categories) > 1, (
        f"a soft category preference removed everything else: only {categories} came back. "
        "This is the hard-filter path; a preference must not empty the page of alternatives."
    )
    assert "Food" in categories, "the preferred category is missing entirely"

    # Compared against the same query with no preference expressed, because
    # "Food appears high up" can be true by accident - it was: the first
    # version of this assertion passed with the ranking signal deleted.
    baseline = await SearchService(ai_provider=NoPreferenceProvider()).search(
        SearchRequest(query="i'd like to eat well in hoi an")
    )

    def food_ranks(items) -> dict:
        return {item.id: index for index, item in enumerate(items) if item.category == "Food"}

    preferred_ranks = food_ranks(response.items)
    baseline_ranks = food_ranks(baseline.items)
    assert preferred_ranks and baseline_ranks, "no food came back; the fixture cannot show a change"

    # Paired by product, not averaged over the page. A mean rank compares two
    # different sets of items: expressing the preference also pulls further
    # food onto the page, and a newcomer landing last raises the mean even
    # though every item that was already there moved up. That is the
    # preference working, so a mean would have reported a regression.
    common = preferred_ranks.keys() & baseline_ranks.keys()
    assert common, "no food experience appears in both pages to compare"
    moved = {item_id: (baseline_ranks[item_id], preferred_ranks[item_id]) for item_id in common}
    assert all(after <= before for before, after in moved.values()), (
        f"a preferred-category item ranked lower once the preference was expressed: {moved}"
    )
    assert any(after < before for before, after in moved.values()), (
        f"expressing a preference for Food changed nothing: {moved}. The preference is "
        "carried through the intent but never reaches the ranker, so 'soft' means 'ignored'."
    )
    # Direction only. A magnitude floor was tried here and removed: the number
    # of places an item climbs is a property of this fixture - how many rivals
    # sit near it, and how tightly - not of the ranker. Measured across the demo
    # catalogue at one weight, categories moved by anything from 0 to 5 places,
    # so any floor that held for Food was an accident of Food. The claim that
    # the preference is strong enough to matter belongs in
    # `test_a_stated_preference_outweighs_everything_noticed_for_the_shopper`,
    # where it can be stated about the scoring function itself.
    assert len(preferred_ranks) >= len(baseline_ranks), (
        "preferring a category put less of it on the page than not mentioning it at all"
    )


async def test_a_required_category_still_removes_everything_else():
    """The other half: when the shopper does insist, the filter must still bite."""
    response = await SearchService(ai_provider=RequiredCategoryProvider()).search(
        SearchRequest(query="cooking classes only, in hoi an")
    )

    categories = {item.category for item in response.items}
    assert categories <= {"Food"}, (
        f"a required category let {categories - {'Food'}} through; the eligibility gate "
        "is no longer honouring hard category constraints"
    )


class RequiredCategoryProvider:
    """The shopper insisted, so the model reports the category as required.

    "Cooking classes only" is the sentence the prompt's "must, only, or
    required" carve-out is about. This is the path that must still remove
    everything else - the soft/hard split is only correct if both halves work.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Hoi An", confidence=0.9),
            hard_constraints=[{"field": "category", "operator": "eq", "value": "Food"}],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class NoPreferenceProvider:
    """The same query, with no category preference expressed - the baseline."""

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Hoi An", confidence=0.9),
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


class InventedSoftCategoryProvider:
    """A preference for something the catalogue does not stock.

    Real values from production logs: "attractions", "tourist attraction",
    "sightseeing or lantern festival". The hard path has been screened for this
    since the empty-grid fix; the soft path was not, because a preference cannot
    empty a grid and so looked harmless.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Hoi An", confidence=0.9),
            soft_preferences=[
                {"field": "category", "value": "sightseeing or lantern festival", "weight": 0.5}
            ],
        )

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_a_preferred_category_we_do_not_stock_is_dropped_and_declared():
    """An unsatisfiable preference must not silently reorder the page.

    It matches no product, so `preference_fit` scores every product 0.0 on that
    signal; because the score is an unweighted mean, carrying it deflates the
    preference score of the whole page by one signal's worth. The ranking then
    reflects a preference nothing could ever satisfy, and the shopper is never
    told their words were discarded - which is the thing they would need to know
    to phrase it differently.
    """
    response = await SearchService(ai_provider=InventedSoftCategoryProvider()).search(
        SearchRequest(query="sightseeing in hoi an")
    )

    assert response.effective_filters.preferred_category is None, (
        "a category the catalogue does not stock reached the ranker as a preference: "
        f"{response.effective_filters.preferred_category!r}"
    )
    assert "preferred_category_unmatched" in response.unresolved_constraints, (
        "the preference was discarded without telling the shopper; "
        f"unresolved_constraints={response.unresolved_constraints}"
    )
    assert "category_unmatched" not in response.unresolved_constraints, (
        "a dropped preference was reported with the code for a dropped requirement, so "
        "the client cannot tell 'we widened your grid' from 'we ignored your preference'"
    )
    assert response.items, "dropping an unsatisfiable preference must not empty the grid"


class BrokenIntentProvider:
    """The failure mode both production outages actually took.

    Not a contrived exception: a wrong `reasoning.effort` for the deployed model
    makes Azure OpenAI return 400 on every single call, which arrives here as an
    exception out of `extract_intent`.
    """

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]

    async def extract_intent(self, text: str, **_) -> SearchIntent:
        raise RuntimeError("400 Unsupported value: 'minimal' is not supported with this model")


@pytest.mark.asyncio
async def test_a_page_built_without_the_model_says_so_and_is_counted():
    """The outage that hid behind HTTP 200 now leaves two marks.

    The shopper still gets a page - that part is deliberate and unchanged. But
    the response now names the degradation, and the health surface carries a
    non-zero failure ratio, so it can be alerted on. Without both, a service
    that has stopped understanding every query it receives is indistinguishable
    from a healthy one, which is exactly how two outages ran for days.
    """
    intent_health.reset()
    service = SearchService(ai_provider=BrokenIntentProvider())

    response = await service.search(SearchRequest(query="lantern workshop in hoi an"))

    assert "intent_unavailable" in response.unresolved_constraints, (
        "the page was assembled by deterministic parsing, with no model having read the "
        "query, and said nothing about it - the client cannot tell this page apart from "
        "one the model actually understood"
    )
    assert response.items, "degrading must still answer; a page beats an error page"

    health = intent_health.snapshot()
    assert health.calls == 1
    assert health.failures == 1
    assert health.failure_ratio == 1.0, (
        "every intent call failed and the health surface reported it as fine"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_healthy_page_is_not_marked_as_degraded():
    """The counterpart: the marker must mean something when it is absent."""
    intent_health.reset()
    service = SearchService(ai_provider=NoPreferenceProvider())

    response = await service.search(SearchRequest(query="lantern workshop in hoi an"))

    assert "intent_unavailable" not in response.unresolved_constraints
    snapshot = intent_health.snapshot()
    assert snapshot.failures == 0
    # Zero failures out of zero calls is what a counter that was never wired up
    # also reports, and it would satisfy the line above forever.
    assert snapshot.calls == 1, (
        f"the page was built with {snapshot.calls} recorded intent calls; a healthy ratio "
        "that comes from never counting anything is the same silence in a new place"
    )
    intent_health.reset()


def test_the_models_strength_of_preference_reaches_the_ranker():
    """A mild preference and an emphatic one must not score identically.

    The model already judges how much the shopper wants something and emits a
    weight for it; that number used to be dropped in `merge_filters`, so "a
    food tour would be nice" and "I really want a food tour" produced the same
    ranking. Reading strength out of a sentence is a language judgement, which
    belongs to the model - the ranker's job is to honour it, not to substitute
    a constant of its own.
    """
    faint = SearchIntent(
        search_text="maybe some food",
        soft_preferences=[{"field": "category", "value": "Food", "weight": 0.1}],
    )
    emphatic = SearchIntent(
        search_text="really want food",
        soft_preferences=[{"field": "category", "value": "Food", "weight": 1.0}],
    )
    faint_filters, _ = merge_filters(SearchFilters(), faint)
    emphatic_filters, _ = merge_filters(SearchFilters(), emphatic)

    assert faint_filters.preferred_category_weight == 0.1
    assert emphatic_filters.preferred_category_weight == 1.0

    food = {
        "category": "Food",
        "family_friendly": True,
        "indoor_outdoor": "indoor",
        "duration_minutes": 120,
        "rating": 4.5,
        "instant_confirmation": True,
        "options": [{"free_cancellation_hours": 24}],
        "languages": ["en", "vi"],
    }
    other = {**food, "category": "Tour"}

    faint_gap = preference_fit(food, faint_filters) - preference_fit(other, faint_filters)
    emphatic_gap = preference_fit(food, emphatic_filters) - preference_fit(other, emphatic_filters)

    assert faint_gap > 0, "even a faint preference should favour the category"
    assert emphatic_gap > faint_gap * 2, (
        f"a preference the model rated 1.0 separated the categories by {emphatic_gap:.4f}, "
        f"barely more than one it rated 0.1 ({faint_gap:.4f}). The model's reading of how "
        "much the shopper wants this is being flattened to a constant."
    )


def test_every_category_we_stock_has_its_own_take_rate():
    """The commercial table and the catalogue vocabulary are one list, not two.

    CATEGORY_TAKE_RATE drifted through two renames still holding "Food
    experience" and "Transport ticket". Nothing failed: an unknown category
    silently takes DEFAULT_TAKE_RATE, so renaming a category quietly repriced
    every item in it and left a dead key behind to make it look intentional.
    """
    rated = set(CATEGORY_TAKE_RATE)
    stocked = set(CATEGORIES)
    assert not stocked - rated, (
        f"categories the catalogue stocks with no take rate, silently priced at the "
        f"default: {sorted(stocked - rated)}"
    )
    assert not rated - stocked, (
        f"take rates for categories the catalogue does not stock: {sorted(rated - stocked)}. "
        "A key here that no product can have is a rename that was never finished."
    )


def test_a_stated_preference_outweighs_everything_noticed_for_the_shopper():
    """The magnitude claim, made about the ranker rather than about a fixture.

    A shopper who says what they want must not be outvoted by the things the
    ranker notices on their behalf. This is the property the removed
    displacement floor was reaching for, stated where it is actually true:
    against the scoring function, with no catalogue involved.

    Pinned as a share rather than a constant so the guarantee survives someone
    adding a fourth intrinsic signal - which is exactly how a per-signal weight
    would have eroded it.
    """
    product = {
        "category": "Food",
        "family_friendly": True,
        "indoor_outdoor": "indoor",
        "duration_minutes": 120,
        "rating": 4.5,
        "instant_confirmation": True,
        "options": [{"free_cancellation_hours": 24}],
        "languages": ["en", "vi"],
    }
    stated = SearchFilters(preferred_category="Food")
    matched = preference_fit(product, stated)
    missed = preference_fit({**product, "category": "Tour"}, stated)

    # Everything intrinsic is perfect for this product, so the whole difference
    # between these two scores is the stated preference being honoured.
    share = matched - missed
    assert share > 0.5, (
        f"a stated preference is worth {share:.2f} of the score against a full set of "
        "intrinsic signals; the shopper's own words carry less weight than what we "
        "noticed for them, so saying what you want barely changes the page"
    )
    assert share == pytest.approx(1 / (1 + INTRINSIC_BUDGET)), (
        "the stated share has drifted from the declared budget, which means the trade "
        "between what a shopper says and what we notice is no longer the one written down"
    )


def test_the_models_strength_of_family_preference_reaches_the_ranker():
    """The same wish, expressed twice, must not count twice differently.

    `family_friendly_weight` arrives by the same soft-preference path as the
    preferred category and is read at the same point in the blend, but review
    deleted the whole plumbing - the schema field, the write in
    `merge_filters`, the read in `features.py` - and no test failed. An
    untested weight is a weight that can quietly become a constant again, which
    is precisely the regression the change was made to fix.
    """
    faint = SearchIntent(
        search_text="somewhere the kids could come along",
        soft_preferences=[{"field": "family_friendly", "value": True, "weight": 0.1}],
    )
    emphatic = SearchIntent(
        search_text="it absolutely has to work for the kids",
        soft_preferences=[{"field": "family_friendly", "value": True, "weight": 1.0}],
    )
    faint_filters, _ = merge_filters(SearchFilters(), faint)
    emphatic_filters, _ = merge_filters(SearchFilters(), emphatic)

    assert faint_filters.family_friendly is True
    assert faint_filters.family_friendly_weight == 0.1
    assert emphatic_filters.family_friendly_weight == 1.0

    suitable = {
        "category": "Food",
        "family_friendly": True,
        "indoor_outdoor": "indoor",
        "duration_minutes": 120,
        "rating": 4.5,
        "instant_confirmation": True,
        "options": [{"free_cancellation_hours": 24}],
        "languages": ["en", "vi"],
    }
    unsuitable = {**suitable, "family_friendly": False}

    faint_gap = preference_fit(suitable, faint_filters) - preference_fit(unsuitable, faint_filters)
    emphatic_gap = preference_fit(suitable, emphatic_filters) - preference_fit(
        unsuitable, emphatic_filters
    )

    assert faint_gap > 0, "even a faint wish should favour the places that suit children"
    assert emphatic_gap > faint_gap * 2, (
        f"a wish the model rated 1.0 separated suitable from unsuitable by "
        f"{emphatic_gap:.4f}, barely more than one it rated 0.1 ({faint_gap:.4f}). How much "
        "the shopper meant it is being flattened to a constant."
    )
