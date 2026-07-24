from collections import Counter
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from app.api.schemas import Participant, RecommendationResponse, SearchFilters
from app.catalog.service import get_product_async, product_card
from app.common.config import get_settings
from app.common.persistence import catalog_products, event_history
from app.common.ranking import bayesian_rating, cosine_similarity, mmr_diversify
from app.common.store import DemoStore, store
from app.search.service import is_eligible

COMPLEMENTS = {
    "Museum or cultural venue": {"Food experience", "Cruise", "Guided tour"},
    "Day trip": {"Entertainment experience", "Food experience", "Open-dated voucher"},
    "Transport ticket": {"Day trip", "Guided tour", "Activity or class"},
    "Cruise": {"Food experience", "Guided tour", "Open-dated voucher"},
    "Activity or class": {"Food experience", "Cruise", "Guided tour"},
}

PREFERENCE_FIELDS = (
    "category",
    "rating",
    "max_duration_minutes",
    "indoor_outdoor",
    "language",
    "instant_confirmation",
    "free_cancellation",
    "family_friendly",
)


def _availability_only(gate: SearchFilters) -> SearchFilters:
    """Drop soft preference constraints, keep everything that affects bookability."""
    relaxed = gate.model_copy(deep=True)
    for field in PREFERENCE_FIELDS:
        setattr(relaxed, field, None)
    relaxed.exclusions = []
    return relaxed


def _eligible_ids(
    products: list[dict[str, Any]],
    gate: SearchFilters,
    party: Sequence[Participant],
    current: dict[str, Any] | None,
) -> set[Any]:
    """Apply the shared eligibility gate, relaxing preferences before returning nothing.

    Bookability constraints (date, capacity, budget, accessibility, destination) are
    never relaxed. A recommendation the shopper cannot book is a dead end at the
    moment of highest intent, which is worse than a shorter rail.
    """
    for tier in (gate, _availability_only(gate)):
        ids = {
            product["id"]
            for product in products
            if not (current and product["id"] == current["id"])
            and is_eligible(product, tier, party)
        }
        if ids:
            return ids
    return set()


class RecommendationService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data
        self.settings = get_settings()

    async def recommend(
        self,
        *,
        session_id: str,
        placement: str,
        experience_id: UUID | None = None,
        destination: str | None = None,
        limit: int = 6,
        filters: SearchFilters | None = None,
        party: Sequence[Participant] = (),
    ) -> RecommendationResponse:
        products = await catalog_products(self.data)
        products_by_id = {product["id"]: product for product in products}
        current = (
            await get_product_async(experience_id, self.data)
            if experience_id
            else None
        )
        if current and not destination:
            destination = current["destination"]
        gate = (filters or SearchFilters()).model_copy(deep=True)
        if destination:
            gate.destination = destination
        eligible_ids = _eligible_ids(products, gate, party, current)
        history = await event_history(session_id, data=self.data)
        weights = {
            "experience_impression": 0.1,
            "experience_viewed": 1.0,
            "recommendation_clicked": 1.5,
            "assistant_action_clicked": 2.0,
            "cart_item_added": 4.0,
            "booking_completed": 8.0,
        }
        positive = [
            (weights.get(event, 0), products_by_id[product_id])
            for event, product_id in history
            if weights.get(event, 0) > 0 and product_id in products_by_id
        ]
        total_weight = sum(weight for weight, _ in positive)
        session_vector = None
        if total_weight:
            dimensions = len(positive[0][1]["embedding"])
            session_vector = [
                sum(weight * product["embedding"][i] for weight, product in positive) / total_weight
                for i in range(dimensions)
            ]

        candidates: list[tuple[dict[str, Any], float, str, str]] = []
        for product in products:
            if product["id"] not in eligible_ids:
                continue
            context_fit = 1.0 if destination and product["destination"] == destination else 0.65
            item_similarity = (
                cosine_similarity(current["embedding"], product["embedding"]) if current else 0.0
            )
            session_similarity = (
                cosine_similarity(session_vector, product["embedding"]) if session_vector else 0.0
            )
            complementary = bool(
                current and product["category"] in COMPLEMENTS.get(current["category"], set())
            )
            score = (
                0.30 * max(session_similarity, 0)
                + 0.20 * context_fit
                + 0.15 * max(item_similarity, 0)
                + 0.12
                + 0.10 * product["popularity_score"]
                + 0.08 * bayesian_rating(product["rating"], product["review_count"]) / 5
                + 0.05 * (1 - product["popularity_score"])
                + (
                    0.12
                    if placement in {"complete_your_day", "complementary"} and complementary
                    else 0
                )
            )
            if complementary and placement in {"complete_your_day", "complementary"}:
                reason_code = "COMPLEMENTARY_CATEGORY"
                reason = (
                    f"A complementary {product['category'].casefold()} in {product['destination']}."
                )
            elif current and item_similarity > 0.15:
                reason_code = "SIMILAR_INTERESTS"
                reason = f"Similar {', '.join(product['interest_tags'][:2])} experience."
            elif product["family_friendly"]:
                reason_code = "FITS_PARTY"
                reason = "Popular and suitable for families."
            elif destination:
                reason_code = "TRENDING_DESTINATION"
                reason = f"Highly rated in {destination}."
            else:
                reason_code = "AVAILABLE_ON_DATE"
                reason = "Available with instant confirmation."
            candidates.append((product, score, reason_code, reason))

        if not candidates:
            return RecommendationResponse(items=[])
        candidates.sort(key=lambda item: item[1], reverse=True)
        diversified_ids = mmr_diversify(
            [
                (str(item[0]["id"]), item[1], item[0]["embedding"], item[0]["subcategories"][0])
                for item in candidates
            ],
            limit,
            self.settings.recommendation_mmr_lambda,
        )
        by_id = {str(item[0]["id"]): item for item in candidates}
        items = []
        for item_id in diversified_ids:
            product, _score, code, reason = by_id[item_id]
            items.append(product_card(product, [reason], reason_code=code))
        return RecommendationResponse(items=items)


async def session_interest_tags(
    session_id: str, data: DemoStore = store
) -> list[str]:
    products = {
        product["id"]: product for product in await catalog_products(data)
    }
    tags = Counter(
        tag
        for _, product_id in await event_history(session_id, data=data)
        for tag in products.get(product_id, {}).get("interest_tags", [])
    )
    return [tag for tag, _ in tags.most_common(5)]
