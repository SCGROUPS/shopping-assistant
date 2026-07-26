import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.schemas import Participant, RecommendationResponse, SearchFilters
from app.catalog.service import get_product_async, product_card
from app.common.config import get_settings
from app.common.features import (
    availability_fit,
    context_fit,
    median_party_total,
    merchandising_multiplier,
    promotion_active,
    quality,
)
from app.common.locales import DEFAULT_LOCALE
from app.common.persistence import catalog_products, demand_stats, event_history
from app.common.ranking import cosine_similarity, mmr_diversify, time_decay
from app.common.runtime_config import get_config
from app.common.store import DemoStore, store
from app.search.service import is_eligible

# Intent strength per event type. Booking is the only unambiguous signal, so it
# dominates; an impression barely counts.
EVENT_WEIGHTS = {
    "experience_impression": 0.1,
    "experience_viewed": 1.0,
    "recommendation_clicked": 1.5,
    "assistant_action_clicked": 2.0,
    "cart_item_added": 4.0,
    "booking_completed": 8.0,
}

# Demand weighting: a booking says far more about an item than a browse.
DEMAND_WEIGHTS = {"views": 1.0, "cart_adds": 3.0, "bookings": 6.0}
POPULARITY_SATURATION = 40.0

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


def _aware(moment: datetime) -> datetime:
    """Client-supplied timestamps may arrive naive; treat those as UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _popularity(product: dict[str, Any], stats: dict[str, float] | None) -> float:
    """One honest popularity term, measured where possible.

    The seeded review-count proxy is only a cold-start prior: as soon as real
    demand exists for an item, observed behaviour takes over. Diversity is left
    to MMR rather than being faked with a "novelty" term that only ever added a
    constant.
    """
    seeded = float(product["popularity_score"])
    if not stats:
        return seeded
    observed = sum(stats.get(key, 0.0) * weight for key, weight in DEMAND_WEIGHTS.items())
    if observed <= 0:
        return seeded
    measured = math.log1p(observed) / math.log1p(POPULARITY_SATURATION)
    confidence = min(1.0, observed / POPULARITY_SATURATION)
    return min(1.0, (1 - confidence) * seeded + confidence * measured)


class RecommendationService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data
        self.settings = get_settings()

    def _session_vector(
        self,
        history: Sequence[tuple[str, UUID, datetime]],
        products_by_id: dict[UUID, dict[str, Any]],
    ) -> list[float] | None:
        """Time-decayed centroid of what this shopper has engaged with."""
        now = datetime.now(UTC)
        half_life = self.settings.behaviour_half_life_seconds
        positive: list[tuple[float, dict[str, Any]]] = []
        for event, product_id, occurred_at in history:
            intent = EVENT_WEIGHTS.get(event, 0.0)
            product = products_by_id.get(product_id)
            if intent <= 0 or product is None:
                continue
            age = (now - _aware(occurred_at)).total_seconds()
            positive.append((intent * time_decay(age, half_life), product))

        total_weight = sum(weight for weight, _ in positive)
        if not total_weight:
            return None
        dimensions = len(positive[0][1]["embedding"])
        return [
            sum(weight * product["embedding"][i] for weight, product in positive) / total_weight
            for i in range(dimensions)
        ]

    def _weights(self, has_session: bool, configured: dict[str, float]) -> dict[str, float]:
        """Weights from configuration, with cold-start redistribution.

        With no history the session term is dead weight, so its budget goes to
        the terms that still discriminate for a first-time visitor
        (POC_SPEC.md §12.4).
        """
        weights = dict(configured)
        if has_session:
            return weights
        spare = weights["session"]
        weights["session"] = 0.0
        for field, share in (
            ("context_fit", 0.4),
            ("popularity", 0.3),
            ("quality", 0.2),
            ("availability_fit", 0.1),
        ):
            weights[field] += spare * share
        return weights

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
        display_currency: str | None = None,
        locale: str = DEFAULT_LOCALE,
    ) -> RecommendationResponse:
        products = await catalog_products(self.data, locale=locale)
        products_by_id = {product["id"]: product for product in products}
        current = (
            await get_product_async(experience_id, self.data, locale=locale)
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
        demand = await demand_stats(self.data)
        session_vector = self._session_vector(history, products_by_id)
        eligible = [product for product in products if product["id"] in eligible_ids]
        reference_total = median_party_total(eligible, party)
        config = await get_config()
        weights = self._weights(session_vector is not None, config["recommendation_weights"])
        boost_ceiling = config["max_merchandising_boost"]

        candidates: list[tuple[dict[str, Any], float, str, str]] = []
        for product in eligible:
            situation = context_fit(product, gate, party, reference_total)
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
                weights["session"] * max(session_similarity, 0)
                + weights["context_fit"] * situation
                + weights["item_similarity"] * max(item_similarity, 0)
                + weights["availability_fit"] * availability_fit(product, gate, party)
                + weights["popularity"] * _popularity(product, demand.get(product["id"]))
                + weights["quality"] * quality(product)
                + (
                    self.settings.recommendation_complement_bonus
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
            candidates.append(
                (
                    product,
                    score * merchandising_multiplier(product, boost_ceiling),
                    reason_code,
                    reason,
                )
            )

        if not candidates:
            return RecommendationResponse(items=[], locale=locale)
        # A pin lifts a product within the rail's own ordering, bounded the
        # same way search bounds it.
        candidates.sort(
            key=lambda item: (
                bool(item[0].get("pinned")) and promotion_active(item[0]),
                item[1],
            ),
            reverse=True,
        )
        diversified_ids = mmr_diversify(
            [
                (str(item[0]["id"]), item[1], item[0]["embedding"], item[0]["subcategories"][0])
                for item in candidates
            ],
            limit,
            config["recommendation_mmr_lambda"],
        )
        by_id = {str(item[0]["id"]): item for item in candidates}
        items = []
        for item_id in diversified_ids:
            product, _score, code, reason = by_id[item_id]
            items.append(
                product_card(
                    product,
                    [reason],
                    reason_code=code,
                    filters=gate,
                    party=party,
                    demand=demand.get(product["id"]),
                    display_currency=display_currency,
                )
            )
        return RecommendationResponse(items=items, locale=locale)


async def session_interest_tags(session_id: str, data: DemoStore = store) -> list[str]:
    products = {product["id"]: product for product in await catalog_products(data)}
    tags = Counter(
        tag
        for _, product_id, _occurred_at in await event_history(session_id, data=data)
        for tag in products.get(product_id, {}).get("interest_tags", [])
    )
    return [tag for tag, _ in tags.most_common(5)]
