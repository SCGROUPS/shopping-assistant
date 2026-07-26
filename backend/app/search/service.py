import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.api.schemas import (
    Participant,
    SearchFilters,
    SearchIntent,
    SearchRequest,
    SearchResponse,
)
from app.assistant.provider import AIProvider, build_ai_provider, deterministic_intent
from app.catalog.service import product_card, starting_price
from app.common.config import get_settings
from app.common.database import session_factory
from app.common.embedding_cache import embedding_cache
from app.common.features import (
    availability_fit,
    conversion_lift,
    margin_fit,
    median_party_total,
    merchandising_multiplier,
    party_total,
    preference_fit,
    price_fit,
    promotion_active,
    quality,
)
from app.common.llm_cost import BudgetExceeded
from app.common.locales import normalize_locale
from app.common.persistence import catalog_products, demand_stats
from app.common.ranking import (
    cosine_similarity,
    deterministic_embedding,
    minmax,
    reciprocal_rank_fusion,
    smoothed_rate,
    tokenize,
)
from app.common.runtime_config import get_config
from app.common.store import DemoStore, store
from app.search.postgres import hybrid_search

DATE_WIDEN_DAYS = 3

# How far ahead a shopper can plausibly be booking. Generous on purpose: the
# bound exists to catch a model that misread a date, not to police itinerary.
MAX_VISIT_HORIZON_DAYS = 550

logger = logging.getLogger(__name__)

CONSTRAINT_FIELDS = {
    "accessibility": "accessibility",
    "budget": "max_total_price",
    "category": "category",
    "currency": "currency",
    "date": "visit_start",
    "duration": "max_duration_minutes",
    "end_date": "visit_end",
    "family_friendly": "family_friendly",
    "free_cancellation": "free_cancellation",
    "indoor_outdoor": "indoor_outdoor",
    "instant_confirmation": "instant_confirmation",
    "language": "language",
    "max_duration": "max_duration_minutes",
    "max_duration_minutes": "max_duration_minutes",
    "max_price": "max_total_price",
    "max_total_price": "max_total_price",
    "rating": "rating",
    "start_date": "visit_start",
    "visit_date": "visit_start",
    "visit_end": "visit_end",
    "visit_start": "visit_start",
}

DATE_CONSTRAINT_FIELDS = {
    "date",
    "end_date",
    "start_date",
    "visit_date",
    "visit_end",
    "visit_start",
}


def should_extract_intent(request: SearchRequest) -> bool:
    """Whether there is anything here for the model to interpret.

    This used to be a judgement about *shape* - English function words, then
    word and character counts - and it doubled as the routing decision. Both
    versions were proxies for meaning that behaved as proxies for language, so
    whether a shopper could reach the assistant depended on which language they
    wrote in. The only thing left that can be decided without reading the
    request is whether there is a request at all.
    """
    return bool(request.query.strip())


def _plausible_visit_date(value: Any) -> bool:
    """Reject a date nobody could be shopping for.

    Quoting the shopper's words proves they mentioned a date; it does not prove
    the model's arithmetic. `tomorrow or Friday?` quotes `tomorrow` and can
    still emit any ISO date at all. We cannot re-derive the date without
    re-reading the language, but a visit in the past or years away is wrong
    whatever the words were, and dropping it costs the shopper only the filter.
    """
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    today = datetime.now(UTC).date()
    return today - timedelta(days=1) <= parsed <= today + timedelta(days=MAX_VISIT_HORIZON_DAYS)


def _comparable(text: str) -> str:
    """Fold text so two spellings of the same word compare equal.

    Vietnamese arrives both composed and decomposed - `ngay mai` with a single
    precomposed vowel, or the same vowel followed by a combining mark - and the
    two are different strings to Python. Case-folding alone left the model's
    quoted phrase failing to match the shopper's own query, which silently
    dropped a date they really had typed.
    """
    return unicodedata.normalize("NFC", text).strip().casefold()


def sanitize_intent(query: str, intent: SearchIntent) -> SearchIntent:
    """Drop constraints the shopper did not actually ask for.

    The date guard here used to be a regex of English month names, weekdays and
    words like `tomorrow`. It ran against the shopper's raw text, so a request
    written in Vietnamese or Japanese never matched, and any date the model had
    correctly understood was thrown away - the shopper typed a date, saw it
    ignored, and nothing anywhere reported a problem.

    The model now quotes the words it read the date from, and the only thing
    checked here is that those words really occur in the request. That still
    stops an invented date, because an invented one has no source text to
    quote, and it does so without the guard needing to know the language.
    """
    quoted = _comparable(intent.date_phrase or "")
    mentions_date = bool(quoted) and quoted in _comparable(query)
    constraints: list[dict[str, Any]] = []
    dropped: list[str] = []
    for constraint in intent.hard_constraints:
        field = str(constraint.get("field", "")).casefold()
        operator = str(constraint.get("operator", "")).casefold()
        value = constraint.get("value")
        if value in (None, "", []) or operator in {"unspecified", "unknown"}:
            continue
        if field in DATE_CONSTRAINT_FIELDS:
            # Refusing a date is defensible. Refusing it in silence is not: the
            # shopper sees results for dates they never asked about and has no
            # way to tell that the one thing they were most specific about was
            # thrown away. Each refusal is recorded so it can be shown.
            if not mentions_date:
                dropped.append("date_unverified")
                continue
            if not _plausible_visit_date(value):
                dropped.append("date_implausible")
                continue
        if field == "category" and isinstance(value, list):
            continue
        constraints.append(constraint)
    return intent.model_copy(
        update={
            "hard_constraints": constraints,
            "dropped_constraints": list(dict.fromkeys(dropped)),
            "needs_clarification": False,
            "clarification_question": None,
        }
    )


def _constraint_value(field: str, value: Any) -> Any:
    if field == "accessibility":
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]
    if field == "indoor_outdoor" and isinstance(value, list):
        return str(value[0]) if value else None
    if field in {"visit_start", "visit_end"}:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise ValueError("date constraint must be an ISO date or datetime")
        normalized = value.replace("Z", "+00:00")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            normalized += "T00:00:00+00:00"
        return datetime.fromisoformat(normalized)
    if field in {"max_total_price", "rating"}:
        return float(value)
    if field == "max_duration_minutes":
        return int(value)
    if field in {
        "family_friendly",
        "free_cancellation",
        "instant_confirmation",
    }:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "yes", "required"}:
            return True
        if isinstance(value, str) and value.casefold() in {"false", "no"}:
            return False
        raise ValueError(f"{field} constraint must be boolean")
    return str(value)


def merge_filters(explicit: SearchFilters, intent: SearchIntent) -> tuple[SearchFilters, list[str]]:
    values = explicit.model_dump()
    unresolved: list[str] = []
    if not values["destination"] and intent.destination.name:
        values["destination"] = intent.destination.name
    for constraint in intent.hard_constraints:
        source_field = str(constraint.get("field", "")).casefold()
        field = CONSTRAINT_FIELDS.get(source_field)
        value = constraint.get("value")
        if not field:
            unresolved.append(source_field or "unknown")
            continue
        if values.get(field) not in (None, [], ""):
            continue
        try:
            values[field] = _constraint_value(field, value)
        except (TypeError, ValueError):
            unresolved.append(source_field)
    for preference in intent.soft_preferences:
        if preference.get("field") == "family_friendly" and values["family_friendly"] is None:
            values["family_friendly"] = bool(preference.get("value"))
    values["exclusions"] = list(dict.fromkeys([*values["exclusions"], *intent.exclusions]))
    if values["visit_start"] and not values["visit_end"]:
        values["visit_end"] = values["visit_start"]
    if (
        values["visit_start"]
        and values["visit_end"]
        and values["visit_end"] < values["visit_start"]
    ):
        values["visit_end"] = values["visit_start"]
        if explicit.visit_end is not None:
            unresolved.append("visit_end")
    return SearchFilters.model_validate(values), unresolved


def excluded_by(text: str, exclusions: Sequence[str]) -> bool:
    """Whether the shopper ruled this out, matching whole words rather than
    substrings.

    This was `exclusion in searchable`, which is not what anyone means by
    excluding something. "spa" removed every experience mentioning a *space*,
    "art" removed anything that *starts* somewhere, and "bar" removed the
    barbecue. A shopper ruling one thing out silently lost a category they had
    never mentioned, and nothing in the response said why.

    Matching is on tokens, so accents and case fold away and Vietnamese - which
    does not inflect - compares exactly. English plurals are the one allowance:
    "mountain" still has to rule out `Marble Mountains`, which was the reason
    the substring test was there in the first place.
    """
    haystack = tokenize(text)
    if not haystack:
        return False
    # Padded so a phrase can only ever match on token boundaries.
    window = f" {' '.join(haystack)} "
    for exclusion in exclusions:
        terms = tokenize(exclusion)
        if not terms:
            continue
        head = " ".join(terms[:-1])
        # Plural on the final word only: "water sport" must rule out "water
        # sports", not "waters sport".
        for suffix in ("", "s", "es"):
            phrase = f"{head} {terms[-1]}{suffix}".strip()
            if f" {phrase} " in window:
                return True
    return False


def is_eligible(
    product: dict[str, Any],
    filters: SearchFilters,
    party: Sequence[Participant] = (),
) -> bool:
    """Hard-constraint gate shared by search and recommendations.

    Hard constraints are gates, never ranking boosts: an item the shopper cannot
    book must be absent rather than ranked lower.
    """
    if product["status"] != "PUBLISHED":
        return False
    # An operator pulling a product must remove it everywhere at once, not
    # merely rank it lower. Suppression is a hard gate for that reason, and it
    # is shared with recommendations because this function is.
    if product.get("suppressed") and promotion_active(product):
        return False
    if filters.destination_id and product["destination_id"] != filters.destination_id:
        return False
    if (
        filters.destination
        and filters.destination.casefold() not in product["destination"].casefold()
    ):
        return False
    if filters.category and filters.category.casefold() != product["category"].casefold():
        return False
    if filters.rating is not None and product["rating"] < filters.rating:
        return False
    if filters.max_duration_minutes and product["duration_minutes"] > filters.max_duration_minutes:
        return False
    if filters.indoor_outdoor:
        # A "mixed" experience happens partly indoors and partly outdoors, so it
        # satisfies either preference. Admitting it only for "indoor" hid every
        # part-outdoor experience from an outdoor shopper.
        allowed = {filters.indoor_outdoor.casefold(), "mixed"}
        if product["indoor_outdoor"].casefold() not in allowed:
            return False
    if filters.language and filters.language.casefold() not in {
        language.casefold() for language in product["languages"]
    }:
        return False
    if filters.instant_confirmation is not None and (
        product["instant_confirmation"] != filters.instant_confirmation
    ):
        return False
    if filters.family_friendly is not None and (
        product["family_friendly"] != filters.family_friendly
    ):
        return False
    if filters.free_cancellation and not any(
        option["free_cancellation_hours"] > 0 for option in product["options"]
    ):
        return False
    if filters.accessibility and not all(
        any(
            required.casefold() in feature.casefold()
            for feature in product["accessibility_features"]
        )
        for required in filters.accessibility
    ):
        return False
    if (
        filters.max_total_price is not None
        and party_total(product, party) > filters.max_total_price
    ):
        return False
    if filters.currency:
        _, currency = starting_price(product)
        if currency != filters.currency:
            return False
    if filters.exclusions and excluded_by(product["search_document"], filters.exclusions):
        return False
    if filters.visit_start:
        party_size = sum(person.count for person in party) or 1
        visit_end = filters.visit_end or filters.visit_start
        if not any(
            filters.visit_start.date() <= slot["starts_at"].date() <= visit_end.date()
            and slot["capacity_remaining"] >= party_size
            and slot["status"] == "AVAILABLE"
            for option in product["options"]
            for slot in option["slots"]
        ):
            return False
    return True


def _explanations(
    product: dict[str, Any], filters: SearchFilters, request: SearchRequest
) -> list[str]:
    reasons: list[str] = []
    if filters.visit_start:
        reasons.append("Available on your selected date.")
    if filters.indoor_outdoor:
        setting = product["indoor_outdoor"].casefold()
        if setting == "mixed":
            reasons.append("Runs both indoors and outdoors.")
        elif setting == filters.indoor_outdoor.casefold():
            reasons.append(f"Takes place {setting}s.")
    if filters.family_friendly and product["family_friendly"]:
        reasons.append("Family-friendly.")
    if filters.free_cancellation:
        hours = product["options"][0]["free_cancellation_hours"]
        reasons.append(f"Free cancellation until {hours} hours before the visit.")
    if filters.max_total_price is not None:
        reasons.append("Within your total budget.")
    query_tokens = set(tokenize(request.query))
    matching_tags = query_tokens.intersection(tokenize(" ".join(product["interest_tags"])))
    if matching_tags:
        reasons.append(f"Matches your interest in {', '.join(sorted(matching_tags)[:2])}.")
    if not reasons:
        reasons.append(
            f"Highly rated {product['category'].casefold()} in {product['destination']}."
        )
    return reasons[:3]


def _drop(field: str):
    def mutate(filters: SearchFilters) -> None:
        setattr(filters, field, None)

    return mutate


def _widen_dates(filters: SearchFilters) -> None:
    if not filters.visit_start:
        return
    end = filters.visit_end or filters.visit_start
    filters.visit_start = filters.visit_start - timedelta(days=DATE_WIDEN_DAYS)
    filters.visit_end = end + timedelta(days=DATE_WIDEN_DAYS)


# The constraints that can be given up at all, and how. Accessibility
# requirements and explicit exclusions are absent by design: showing a
# wheelchair user an inaccessible tour is worse than showing nothing at all, and
# an exclusion is the one thing the shopper stated negatively.
#
# The order of this tuple is not a policy. It is only the order in which
# candidates are listed to whoever is going to ask the shopper; what actually
# gets relaxed is whatever they authorise, in the order they authorise it.
#
# The second element is a *code*, not a label. These are shown to the shopper -
# "no exact match, so I relaxed your budget" - and they used to be English prose
# assembled here, which meant a Vietnamese storefront explained itself in
# English. The client renders the code from its own dictionary.
RELAXATION_STEPS: tuple[tuple[str, str, Any], ...] = (
    ("max_duration_minutes", "max_duration", _drop("max_duration_minutes")),
    ("rating", "rating", _drop("rating")),
    ("instant_confirmation", "instant_confirmation", _drop("instant_confirmation")),
    ("free_cancellation", "free_cancellation", _drop("free_cancellation")),
    ("category", "category", _drop("category")),
    ("indoor_outdoor", "indoor_outdoor", _drop("indoor_outdoor")),
    ("language", "language", _drop("language")),
    ("family_friendly", "family_friendly", _drop("family_friendly")),
    ("visit_start", "dates", _widen_dates),
    ("max_total_price", "budget", _drop("max_total_price")),
    ("destination", "destination", _drop("destination")),
)


def relaxation_candidates(filters: SearchFilters) -> list[str]:
    """Which of the shopper's constraints could still be given up, as codes.

    Reported so the decision does not have to be ours. Which constraint is
    cheapest to lose is the shopper's judgement, not a fact about the
    catalogue - a family may give up their budget before their dates, and a
    business traveller the reverse - so this only says what is on the table.
    Nothing here is given up until the shopper names it.
    """
    return [
        code for field, code, _ in RELAXATION_STEPS if getattr(filters, field) not in (None, [], "")
    ]


def relax_until_results(
    products: list[dict[str, Any]],
    filters: SearchFilters,
    party: Sequence[Participant],
    order: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], SearchFilters, list[str], list[str]]:
    """Give up only the constraints the caller was authorised to give up.

    A zero-result page is the most common exit point in tourism shopping, and
    this module used to answer it by relaxing constraints on its own initiative
    until something was bookable. That was a business judgement made here: a
    shopper who said "under 2,000,000 VND" could be shown a 5,000,000 VND tour,
    or one who asked for a Korean-speaking guide could be handed an English one,
    without ever agreeing to it. Reordering that sequence did not fix it - the
    service was still the one deciding.

    So nothing is relaxed unless `order` names it. `order` is an authorisation,
    not a preference: each code in it is a constraint the shopper has agreed to
    lose, tried in the order given. With no authorisation the exact-match result
    is returned unchanged, together with the candidates a caller may ask about.
    """
    eligible = [product for product in products if is_eligible(product, filters, party)]
    if eligible:
        return eligible, filters, [], []

    candidates = relaxation_candidates(filters)
    if not order:
        # Nothing was authorised, so nothing is given up. The caller - the agent,
        # or the storefront asking the shopper directly - decides what to offer.
        return [], filters, [], candidates

    authorised = [step for code in order for step in RELAXATION_STEPS if step[1] == code]

    working = filters.model_copy(deep=True)
    relaxed: list[str] = []
    for field, label, mutate in authorised:
        if getattr(working, field) in (None, [], ""):
            continue
        mutate(working)
        relaxed.append(label)
        eligible = [product for product in products if is_eligible(product, working, party)]
        if eligible:
            return eligible, working, relaxed, relaxation_candidates(working)
    return [], working, relaxed, relaxation_candidates(working)


FACET_FIELDS: tuple[tuple[str, str], ...] = (
    ("destination", "destination"),
    ("category", "category"),
    ("indoor_outdoor", "indoor_outdoor"),
)


def _facets(
    products: list[dict[str, Any]],
    filters: SearchFilters,
    party: Sequence[Participant],
) -> dict[str, dict[str, int]]:
    """Count each facet with its own filter removed.

    Counting against the fully filtered set would collapse every facet to the
    single selected value, which makes the counts useless for drilling sideways.
    """
    facets: dict[str, dict[str, int]] = {}
    for facet_name, field in FACET_FIELDS:
        scoped = filters.model_copy(deep=True)
        setattr(scoped, field, None)
        facets[facet_name] = dict(
            Counter(product[field] for product in products if is_eligible(product, scoped, party))
        )
    return facets


class SearchService:
    def __init__(self, data: DemoStore = store, ai_provider: AIProvider | None = None) -> None:
        self.data = data
        self.ai = ai_provider or build_ai_provider()
        self.settings = get_settings()

    async def _query_embedding(self, normalized: str) -> list[float]:
        """Embed the query, hitting the shared cache first.

        Tourist search traffic is head-heavy, so the same handful of queries
        arrive constantly. Caching them across replicas and restarts
        (POC_SPEC.md §11.4) is the single largest cost lever in the system.
        """
        model = self.settings.azure_openai_embedding_deployment
        cached = await embedding_cache.get(normalized, model)
        if cached is not None:
            return cached
        try:
            vector = await self.ai.embed(normalized)
        except BudgetExceeded as exhausted:
            logger.warning("%s", exhausted)
            return deterministic_embedding(normalized)
        except Exception:
            logger.exception("Query embedding failed; using deterministic embedding")
            return deterministic_embedding(normalized)
        await embedding_cache.put(normalized, model, vector)
        return vector

    async def search(self, request: SearchRequest) -> SearchResponse:
        # One locale for both halves of this request. Retrieval filters the
        # index by locale and the cards are rendered from the catalogue, so
        # loading the catalogue in a different locale returns English cards
        # for Vietnamese matches - a page whose results do not contain the
        # words that found them.
        locale = normalize_locale(request.locale)
        available_products = await catalog_products(self.data, locale=locale)
        if should_extract_intent(request):
            try:
                intent = await self.ai.extract_intent(request.query)
            except Exception:
                logger.exception("Intent extraction failed; using deterministic parsing")
                intent = deterministic_intent(request.query)
        else:
            # No model ran, so nothing judged this query. A terminal question
            # mark is the one signal available that means the same thing in
            # every language this catalogue serves.
            intent = SearchIntent(
                search_text=request.query,
                # Nothing here read the request, so nothing here may claim to
                # know how it should be answered.
                interaction_mode="undetermined",
            )
        intent = sanitize_intent(request.query, intent)
        if intent.destination.name:
            inferred = intent.destination.name.casefold()
            known_destinations = {
                product["destination"].casefold() for product in available_products
            }
            if not any(
                inferred in destination or destination in inferred
                for destination in known_destinations
            ):
                intent = intent.model_copy(
                    update={
                        "destination": intent.destination.model_copy(
                            update={"name": None, "confidence": 0.0}
                        )
                    }
                )
        # The same guard the destination above gets, for the same reason. The
        # category filter is an exact match against a closed vocabulary, so a
        # value the catalogue has never heard of does not narrow the results -
        # it empties them. The model is free to describe a request however it
        # reads it, and it does: "hoi an lantern" came back as "attractions",
        # "tourist attraction" and "sightseeing or lantern festival" on
        # different calls, none of which is a category this catalogue stocks.
        # Whether a value exists in the data is not a question about language,
        # so asking it here costs nothing in any of the languages served.
        known_categories = {product["category"].casefold() for product in available_products}
        kept: list[dict[str, Any]] = []
        unmatched: list[str] = []
        for constraint in intent.hard_constraints:
            if str(constraint.get("field", "")).casefold() == "category":
                inferred = str(constraint.get("value", "")).casefold()
                if inferred and inferred not in known_categories:
                    unmatched.append("category_unmatched")
                    continue
            kept.append(constraint)
        if unmatched:
            intent = intent.model_copy(
                update={
                    "hard_constraints": kept,
                    "dropped_constraints": list(
                        dict.fromkeys([*intent.dropped_constraints, *unmatched])
                    ),
                }
            )
        filters, unresolved = merge_filters(request.filters, intent)
        # Codes, not a sentence. This used to interpolate raw field names into
        # English prose - "Please clarify these required constraints:
        # max_total_price." - which was neither the shopper's language nor
        # anything they had written. The client owns the wording.
        unresolved_codes = list(
            dict.fromkeys([*intent.dropped_constraints, *(f"field.{f}" for f in unresolved)])
        )
        if unresolved or intent.needs_clarification:
            intent = intent.model_copy(update={"needs_clarification": True})
            return SearchResponse(
                query_id=uuid4(),
                intent=intent,
                effective_filters=filters,
                items=[],
                facets={},
                locale=locale,
                unresolved_constraints=unresolved_codes,
                interaction_mode=intent.interaction_mode,
            )
        eligible, filters, relaxed_preferences, still_relaxable = relax_until_results(
            available_products, filters, request.party, order=request.relax_order
        )

        # The shopper's own words, not a rewrite of them. A hand-written English
        # synonym map added "cruise river" to anyone who typed "boat" and did
        # nothing at all for "thuyen" - it made the lexical leg both wrong in
        # English and absent everywhere else. Meaning is the embedding's job.
        tokens = tokenize(intent.search_text or request.query)
        lexical_scored: list[tuple[str, float]] = []
        for product in eligible:
            title = tokenize(product["title"])
            tags = tokenize(" ".join(product["interest_tags"] + product["subcategories"]))
            body = tokenize(product["search_document"])
            score = sum(
                5 * title.count(token) + 3 * tags.count(token) + body.count(token)
                for token in tokens
            )
            if score or not tokens:
                lexical_scored.append((str(product["id"]), float(score)))
        lexical_scored.sort(key=lambda item: item[1], reverse=True)
        normalized = " ".join(tokenize(intent.search_text or request.query))
        query_embedding = await self._query_embedding(normalized)
        semantic_scored = sorted(
            (
                (str(product["id"]), cosine_similarity(query_embedding, product["embedding"]))
                for product in eligible
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        postgres_rows: list[dict[str, Any]] = []
        if not self.settings.demo_mode and session_factory is not None:
            try:
                async with session_factory() as session:
                    postgres_rows = await hybrid_search(
                        session,
                        query=intent.search_text or request.query,
                        embedding=query_embedding,
                        destination_id=(
                            str(filters.destination_id) if filters.destination_id else None
                        ),
                        destination=filters.destination,
                        category=filters.category,
                        rating=filters.rating,
                        max_duration=filters.max_duration_minutes,
                        indoor_outdoor=filters.indoor_outdoor,
                        family_friendly=filters.family_friendly,
                        instant_confirmation=filters.instant_confirmation,
                        free_cancellation=bool(filters.free_cancellation),
                        currency=filters.currency,
                        max_total_price=filters.max_total_price,
                        accessibility=filters.accessibility,
                        language=filters.language,
                        exclusions=filters.exclusions,
                        visit_start=(
                            filters.visit_start.isoformat() if filters.visit_start else None
                        ),
                        visit_end=(filters.visit_end.isoformat() if filters.visit_end else None),
                        party=[person.model_dump(mode="json") for person in request.party],
                        party_size=sum(person.count for person in request.party) or 1,
                        lexical_limit=self.settings.search_lexical_candidates,
                        semantic_limit=self.settings.search_semantic_candidates,
                        rrf_k=self.settings.search_rrf_k,
                        page_size=max(request.page_size * 3, 50),
                        locale=locale,
                    )
            except Exception:
                logger.exception("PostgreSQL hybrid retrieval failed")
                raise

        if postgres_rows:
            ordered = [str(row["id"]) for row in postgres_rows]
            fused = {str(row["id"]): float(row["rrf_score"]) for row in postgres_rows}
        else:
            lexical_ids = [
                item[0] for item in lexical_scored[: self.settings.search_lexical_candidates]
            ]
            semantic_ids = [
                item[0] for item in semantic_scored[: self.settings.search_semantic_candidates]
            ]
            fused = reciprocal_rank_fusion(lexical_ids, semantic_ids, self.settings.search_rrf_k)
            ordered = [
                item_id
                for item_id, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)
            ]
        products = {str(product["id"]): product for product in eligible}
        ordered = [item_id for item_id in ordered if item_id in products]
        rrf_values = minmax([fused[item_id] for item_id in ordered])
        reference_total = median_party_total(eligible, request.party)
        demand = await demand_stats(self.data)
        settings = self.settings
        config = await get_config()
        weights = config["search_weights"]
        take_rates = config["category_take_rates"]
        boost_ceiling = config["max_merchandising_boost"]
        final: list[tuple[dict[str, Any], float]] = []
        for item_id, normalized_rrf in zip(ordered, rrf_values, strict=True):
            product = products[item_id]
            stats = demand.get(product["id"], {})
            conversion = conversion_lift(
                smoothed_rate(
                    stats.get("bookings", 0.0),
                    stats.get("impressions", 0.0) + stats.get("views", 0.0),
                    settings.conversion_prior_rate,
                    settings.conversion_prior_strength,
                ),
                settings.conversion_prior_rate,
            )
            score = (
                weights.get("relevance", 0.0) * normalized_rrf
                + weights.get("preference_fit", 0.0)
                * preference_fit(product, filters, request.party)
                + weights.get("availability_fit", 0.0)
                * availability_fit(product, filters, request.party)
                + weights.get("price_fit", 0.0)
                * price_fit(product, filters, request.party, reference_total)
                + weights.get("quality", 0.0) * quality(product)
                + weights.get("conversion", 0.0) * conversion
                + weights.get("margin", 0.0) * margin_fit(product, take_rates)
            )
            final.append((product, score * merchandising_multiplier(product, boost_ceiling)))
        sorters = {
            # A pin lifts a product within the relevance ordering only. If the
            # shopper has explicitly asked for cheapest or highest-rated,
            # answering with a promoted item instead is a lie about the sort
            # control, and shoppers stop trusting the controls.
            "recommended": lambda item: (
                bool(item[0].get("pinned")) and promotion_active(item[0]),
                item[1],
            ),
            "price": lambda item: -starting_price(item[0])[0],
            "rating": lambda item: item[0]["rating"],
            "duration": lambda item: -item[0]["duration_minutes"],
            "popularity": lambda item: item[0]["popularity_score"],
        }
        final.sort(key=sorters[request.sort], reverse=True)
        page = final[: request.page_size]
        facets = _facets(available_products, filters, request.party)
        return SearchResponse(
            locale=locale,
            query_id=uuid4(),
            intent=intent,
            interaction_mode=intent.interaction_mode,
            effective_filters=filters,
            items=[
                product_card(
                    product,
                    _explanations(product, filters, request),
                    filters=filters,
                    party=request.party,
                    demand=demand.get(product["id"]),
                    display_currency=request.display_currency,
                )
                for product, _ in page
            ],
            facets=facets,
            relaxation_candidates=still_relaxable,
            unresolved_constraints=unresolved_codes,
            relaxed_preferences=relaxed_preferences,
        )
