from collections import Counter
from typing import Any
from uuid import UUID

from app.api.schemas import RecommendationResponse
from app.catalog.service import get_product, product_card
from app.common.config import get_settings
from app.common.ranking import bayesian_rating, cosine_similarity, mmr_diversify
from app.common.store import DemoStore, store

COMPLEMENTS = {
    "Museum or cultural venue": {"Food experience", "Cruise", "Guided tour"},
    "Day trip": {"Entertainment experience", "Food experience", "Open-dated voucher"},
    "Transport ticket": {"Day trip", "Guided tour", "Activity or class"},
    "Cruise": {"Food experience", "Guided tour", "Open-dated voucher"},
    "Activity or class": {"Food experience", "Cruise", "Guided tour"},
}


class RecommendationService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data
        self.settings = get_settings()

    def recommend(
        self,
        *,
        session_id: str,
        placement: str,
        experience_id: UUID | None = None,
        destination: str | None = None,
        limit: int = 6,
    ) -> RecommendationResponse:
        current = get_product(experience_id, self.data) if experience_id else None
        if current and not destination:
            destination = current["destination"]
        history = self.data.event_experiences.get(session_id, [])[-20:]
        weights = {
            "experience_impression": 0.1,
            "experience_viewed": 1.0,
            "recommendation_clicked": 1.5,
            "assistant_action_clicked": 2.0,
            "cart_item_added": 4.0,
            "booking_completed": 8.0,
        }
        positive = [
            (weights.get(event, 0), self.data.products[product_id])
            for event, product_id in history
            if weights.get(event, 0) > 0 and product_id in self.data.products
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
        for product in self.data.products.values():
            if product["status"] != "PUBLISHED" or (current and product["id"] == current["id"]):
                continue
            if destination and product["destination"].casefold() != destination.casefold():
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
            for product in self.data.products.values():
                if not current or product["id"] != current["id"]:
                    candidates.append(
                        (
                            product,
                            product["popularity_score"],
                            "TRENDING_DESTINATION",
                            f"Trending in {product['destination']}.",
                        )
                    )
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


def session_interest_tags(session_id: str, data: DemoStore = store) -> list[str]:
    tags = Counter(
        tag
        for _, product_id in data.event_experiences.get(session_id, [])[-20:]
        for tag in data.products.get(product_id, {}).get("interest_tags", [])
    )
    return [tag for tag, _ in tags.most_common(5)]
