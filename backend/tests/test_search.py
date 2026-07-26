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
from app.common.config import get_settings
from app.common.features import (
    availability_fit,
    margin_fit,
    merchandising_multiplier,
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

    async def extract_intent(self, text: str) -> SearchIntent:
        return SearchIntent(
            search_text=text,
            destination=IntentValue(name="Vietnam", confidence=0.92),
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

    async def extract_intent(self, text: str) -> SearchIntent:
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
    """"No spa" must not remove a planetarium.

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

        _, filters, relaxed, _ = relax_until_results(
            [], self._filters(), [], order=["budget"]
        )
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
