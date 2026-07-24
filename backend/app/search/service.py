import hashlib
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.api.schemas import (
    SearchFilters,
    SearchIntent,
    SearchRequest,
    SearchResponse,
)
from app.assistant.provider import AIProvider, build_ai_provider, deterministic_intent
from app.catalog.service import product_card, starting_price
from app.common.config import get_settings
from app.common.database import session_factory
from app.common.persistence import catalog_products
from app.common.ranking import (
    bayesian_rating,
    cosine_similarity,
    minmax,
    reciprocal_rank_fusion,
    tokenize,
)
from app.common.store import DemoStore, store
from app.search.postgres import hybrid_search

SYNONYMS = {
    "kids": "family",
    "children": "family",
    "rainy": "indoor",
    "boat": "cruise river",
    "massage": "spa wellness",
    "temple": "heritage history",
    "ticket": "admission",
}

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
    query = request.query.strip()
    if not query:
        return False
    conversational = re.search(
        r"\b(for|with|under|below|next|tomorrow|family|wheelchair|indoor|outdoor|prefer|quiet)\b",
        query,
        re.I,
    )
    return bool(conversational or len(query.split()) > 5)


def sanitize_intent(query: str, intent: SearchIntent) -> SearchIntent:
    mentions_date = bool(
        re.search(
            r"\b("
            r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
            r"\d{1,2}[/-]\d{1,2}[/-](?:20)?\d{2}|"
            r"january|february|march|april|may|june|july|august|"
            r"september|october|november|december|"
            r"today|tomorrow|tonight|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"next week|this week|weekend"
            r")\b",
            query,
            re.I,
        )
    )
    constraints: list[dict[str, Any]] = []
    for constraint in intent.hard_constraints:
        field = str(constraint.get("field", "")).casefold()
        operator = str(constraint.get("operator", "")).casefold()
        value = constraint.get("value")
        if value in (None, "", []) or operator in {"unspecified", "unknown"}:
            continue
        if field in DATE_CONSTRAINT_FIELDS and not mentions_date:
            continue
        if field == "category" and isinstance(value, list):
            continue
        constraints.append(constraint)
    return intent.model_copy(
        update={
            "hard_constraints": constraints,
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


def merge_filters(
    explicit: SearchFilters, intent: SearchIntent
) -> tuple[SearchFilters, list[str]]:
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
    values["exclusions"] = list(
        dict.fromkeys([*values["exclusions"], *intent.exclusions])
    )
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


def _expanded_tokens(query: str) -> list[str]:
    tokens = tokenize(query)
    return tokens + [synonym for token in tokens for synonym in tokenize(SYNONYMS.get(token, ""))]


def _party_total(product: dict[str, Any], request: SearchRequest) -> float:
    option = product["options"][0]
    prices = {price["participant_type"]: price["amount"] for price in option["prices"]}
    if not request.party:
        return float(prices.get("adult", 0))
    return float(sum(prices.get(person.type, 0) * person.count for person in request.party))


def _eligible(product: dict[str, Any], request: SearchRequest, filters: SearchFilters) -> bool:
    if product["status"] != "PUBLISHED":
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
        allowed = {filters.indoor_outdoor.casefold()}
        if "indoor" in allowed:
            allowed.add("mixed")
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
        and _party_total(product, request) > filters.max_total_price
    ):
        return False
    if filters.currency:
        _, currency = starting_price(product)
        if currency != filters.currency:
            return False
    if filters.exclusions:
        searchable = product["search_document"].casefold()
        if any(exclusion.casefold() in searchable for exclusion in filters.exclusions):
            return False
    if filters.visit_start:
        party_size = sum(person.count for person in request.party) or 1
        visit_end = filters.visit_end or filters.visit_start
        if not any(
            filters.visit_start.date()
            <= slot["starts_at"].date()
            <= visit_end.date()
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
    if filters.indoor_outdoor and product["indoor_outdoor"] in {"indoor", "mixed"}:
        reasons.append("Includes an indoor experience.")
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


class SearchService:
    def __init__(self, data: DemoStore = store, ai_provider: AIProvider | None = None) -> None:
        self.data = data
        self.ai = ai_provider or build_ai_provider()
        self.settings = get_settings()

    async def search(self, request: SearchRequest) -> SearchResponse:
        available_products = await catalog_products(self.data)
        if should_extract_intent(request):
            try:
                intent = await self.ai.extract_intent(request.query)
            except Exception:
                logger.exception("Intent extraction failed; using deterministic parsing")
                intent = deterministic_intent(request.query)
        else:
            intent = SearchIntent(search_text=request.query)
        intent = sanitize_intent(request.query, intent)
        if intent.destination.name:
            inferred = intent.destination.name.casefold()
            known_destinations = {
                product["destination"].casefold()
                for product in available_products
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
        filters, unresolved = merge_filters(request.filters, intent)
        if unresolved or intent.needs_clarification:
            intent = intent.model_copy(
                update={
                    "needs_clarification": True,
                    "clarification_question": intent.clarification_question
                    or (
                        "Please clarify these required constraints: "
                        f"{', '.join(sorted(set(unresolved)))}."
                        if unresolved
                        else "Please clarify the required date, budget, or accessibility details."
                    ),
                }
            )
            return SearchResponse(
                query_id=uuid4(),
                intent=intent,
                effective_filters=filters,
                items=[],
                facets={},
            )
        eligible = [
            product
            for product in available_products
            if _eligible(product, request, filters)
        ]

        tokens = _expanded_tokens(intent.search_text or request.query)
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
        cache_key = hashlib.sha256(normalized.encode()).hexdigest()
        if cache_key not in self.data.query_embeddings:
            try:
                self.data.query_embeddings[cache_key] = await self.ai.embed(normalized)
            except Exception:
                logger.exception("Query embedding failed; using deterministic embedding")
                from app.common.ranking import deterministic_embedding

                self.data.query_embeddings[cache_key] = deterministic_embedding(normalized)
        query_embedding = self.data.query_embeddings[cache_key]
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
                        visit_end=(
                            filters.visit_end.isoformat() if filters.visit_end else None
                        ),
                        party=[
                            person.model_dump(mode="json")
                            for person in request.party
                        ],
                        party_size=sum(person.count for person in request.party) or 1,
                        lexical_limit=self.settings.search_lexical_candidates,
                        semantic_limit=self.settings.search_semantic_candidates,
                        rrf_k=self.settings.search_rrf_k,
                        page_size=max(request.page_size * 3, 50),
                    )
            except Exception:
                logger.exception("PostgreSQL hybrid retrieval failed")
                raise

        if postgres_rows:
            ordered = [str(row["id"]) for row in postgres_rows]
            fused = {
                str(row["id"]): float(row["rrf_score"])
                for row in postgres_rows
            }
        else:
            lexical_ids = [
                item[0] for item in lexical_scored[: self.settings.search_lexical_candidates]
            ]
            semantic_ids = [
                item[0] for item in semantic_scored[: self.settings.search_semantic_candidates]
            ]
            fused = reciprocal_rank_fusion(
                lexical_ids, semantic_ids, self.settings.search_rrf_k
            )
            ordered = [
                item_id
                for item_id, _ in sorted(
                    fused.items(), key=lambda item: item[1], reverse=True
                )
            ]
        products = {str(product["id"]): product for product in eligible}
        ordered = [item_id for item_id in ordered if item_id in products]
        rrf_values = minmax([fused[item_id] for item_id in ordered])
        final: list[tuple[dict[str, Any], float]] = []
        for item_id, normalized_rrf in zip(ordered, rrf_values, strict=True):
            product = products[item_id]
            preference = 1.0 if filters.family_friendly and product["family_friendly"] else 0.7
            rating = bayesian_rating(product["rating"], product["review_count"]) / 5
            score = (
                0.55 * normalized_rrf
                + 0.15 * preference
                + 0.10
                + 0.08 * rating
                + 0.07 * product["popularity_score"]
                + 0.05
            )
            final.append((product, score))
        sorters = {
            "recommended": lambda item: item[1],
            "price": lambda item: -starting_price(item[0])[0],
            "rating": lambda item: item[0]["rating"],
            "duration": lambda item: -item[0]["duration_minutes"],
            "popularity": lambda item: item[0]["popularity_score"],
        }
        final.sort(key=sorters[request.sort], reverse=True)
        page = final[: request.page_size]
        facets = {
            "destination": dict(Counter(product["destination"] for product in eligible)),
            "category": dict(Counter(product["category"] for product in eligible)),
            "indoor_outdoor": dict(Counter(product["indoor_outdoor"] for product in eligible)),
        }
        return SearchResponse(
            query_id=uuid4(),
            intent=intent,
            effective_filters=filters,
            items=[
                product_card(product, _explanations(product, filters, request))
                for product, _ in page
            ],
            facets=facets,
        )
