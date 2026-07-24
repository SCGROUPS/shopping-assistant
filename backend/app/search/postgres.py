from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HYBRID_SEARCH_SQL = text(
    """
    WITH eligible AS (
      SELECT e.id
      FROM experiences e
      JOIN destinations destination ON destination.id = e.destination_id
      WHERE e.status = 'PUBLISHED'
        AND (:destination_id IS NULL OR e.destination_id = CAST(:destination_id AS uuid))
        AND (:destination IS NULL OR lower(destination.name) = lower(:destination))
        AND (:category IS NULL OR lower(e.category) = lower(:category))
        AND (:rating IS NULL OR e.rating >= :rating)
        AND (:max_duration IS NULL OR e.duration_minutes <= :max_duration)
        AND (
          :indoor_outdoor IS NULL
          OR lower(e.indoor_outdoor) = lower(:indoor_outdoor)
          OR (lower(:indoor_outdoor) = 'indoor' AND lower(e.indoor_outdoor) = 'mixed')
        )
        AND (:family_friendly IS NULL OR e.family_friendly = :family_friendly)
        AND (:instant_confirmation IS NULL OR e.instant_confirmation = :instant_confirmation)
        AND (
          :accessibility IS NULL
          OR array_to_string(e.accessibility_features, ' ') ILIKE ('%' || :accessibility || '%')
        )
        AND (
          :free_cancellation = false
          OR EXISTS (
            SELECT 1 FROM experience_options cancellation_option
            WHERE cancellation_option.experience_id = e.id
              AND cancellation_option.active = true
              AND cancellation_option.free_cancellation_hours > 0
          )
        )
        AND (
          :currency IS NULL
          OR EXISTS (
            SELECT 1
            FROM experience_options currency_option
            JOIN option_prices currency_price ON currency_price.option_id = currency_option.id
            WHERE currency_option.experience_id = e.id
              AND currency_option.active = true
              AND currency_price.currency = :currency
          )
        )
        AND (
          :max_total_price IS NULL
          OR EXISTS (
            SELECT 1
            FROM experience_options budget_option
            JOIN option_prices budget_price ON budget_price.option_id = budget_option.id
            WHERE budget_option.experience_id = e.id
              AND budget_option.active = true
              AND budget_price.participant_type = 'adult'
              AND budget_price.amount <= :max_total_price
          )
        )
        AND (
          CAST(:visit_start AS timestamptz) IS NULL
          OR EXISTS (
            SELECT 1
            FROM experience_options slot_option
            JOIN availability_slots slot ON slot.option_id = slot_option.id
            WHERE slot_option.experience_id = e.id
              AND slot_option.active = true
              AND slot.status = 'AVAILABLE'
              AND slot.capacity_remaining >= :party_size
              AND slot.starts_at::date = CAST(:visit_start AS timestamptz)::date
          )
        )
    ),
    lexical AS (
      SELECT e.id,
        row_number() OVER (
          ORDER BY ts_rank_cd(
            d.search_vector,
            websearch_to_tsquery('english', unaccent(:query))
          ) DESC
        ) AS rank
      FROM eligible e
      JOIN experience_search_documents d ON d.experience_id = e.id
      WHERE d.search_vector @@ websearch_to_tsquery('english', unaccent(:query))
      LIMIT :lexical_limit
    ),
    semantic AS (
      SELECT e.id,
        row_number() OVER (ORDER BY d.embedding <=> CAST(:embedding AS vector)) AS rank
      FROM eligible e
      JOIN experience_search_documents d ON d.experience_id = e.id
      ORDER BY d.embedding <=> CAST(:embedding AS vector)
      LIMIT :semantic_limit
    ),
    fused AS (
      SELECT id, SUM(1.0 / (:rrf_k + rank)) AS rrf_score
      FROM (
        SELECT id, rank FROM lexical
        UNION ALL
        SELECT id, rank FROM semantic
      ) candidates
      GROUP BY id
    )
    SELECT e.id, fused.rrf_score
    FROM fused JOIN eligible e ON e.id = fused.id
    ORDER BY fused.rrf_score DESC
    LIMIT :page_size
    """
)


async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    embedding: list[float],
    destination_id: str | None,
    destination: str | None = None,
    category: str | None = None,
    rating: float | None = None,
    max_duration: int | None = None,
    indoor_outdoor: str | None = None,
    family_friendly: bool | None = None,
    instant_confirmation: bool | None = None,
    free_cancellation: bool = False,
    currency: str | None = None,
    max_total_price: float | None = None,
    accessibility: str | None = None,
    visit_start: str | None = None,
    party_size: int = 1,
    lexical_limit: int = 50,
    semantic_limit: int = 50,
    rrf_k: int = 60,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    result = await session.execute(
        HYBRID_SEARCH_SQL,
        {
            "query": query,
            "embedding": str(embedding),
            "destination_id": destination_id,
            "destination": destination,
            "category": category,
            "rating": rating,
            "max_duration": max_duration,
            "indoor_outdoor": indoor_outdoor,
            "family_friendly": family_friendly,
            "instant_confirmation": instant_confirmation,
            "free_cancellation": free_cancellation,
            "currency": currency,
            "max_total_price": max_total_price,
            "accessibility": accessibility,
            "visit_start": visit_start,
            "party_size": party_size,
            "lexical_limit": lexical_limit,
            "semantic_limit": semantic_limit,
            "rrf_k": rrf_k,
            "page_size": page_size,
        },
    )
    return [dict(row) for row in result.mappings()]
