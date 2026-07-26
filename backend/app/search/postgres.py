import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.locales import text_search_config
from app.common.ranking import tokenize


def exclusion_patterns(exclusions: list[str]) -> list[str]:
    """Word-boundary regexes for what the shopper ruled out.

    Built here rather than in SQL so the escaping is done once, in a language
    with a regex escaper: these terms come from the model reading the shopper,
    and a `(` in one of them must be a bracket and not a capture group.

    The shape mirrors `excluded_by` in the in-memory path exactly - tokenised,
    accent-folded, English plural allowed on the last word only - because two
    search backends that disagree about what "no spa" means is a defect a test
    cannot see and a shopper cannot explain.
    """
    patterns: list[str] = []
    for exclusion in exclusions:
        terms = tokenize(exclusion)
        if not terms:
            continue
        body = r"\s+".join(re.escape(term) for term in terms)
        patterns.append(rf"\m{body}(s|es)?\M")
    return patterns

HYBRID_SEARCH_SQL = text(
    """
    WITH eligible AS (
      SELECT e.id
      FROM experiences e
      JOIN destinations destination ON destination.id = e.destination_id
      WHERE e.status = 'PUBLISHED'
        -- Suppressed inventory must not consume a candidate slot; otherwise a
        -- withdrawn product crowds out a bookable one before ranking sees it.
        AND NOT (
          e.suppressed
          AND (e.promotion_starts_at IS NULL OR now() >= e.promotion_starts_at)
          AND (e.promotion_ends_at IS NULL OR now() <= e.promotion_ends_at)
        )
        AND (
          CAST(:destination_id AS uuid) IS NULL
          OR e.destination_id = CAST(:destination_id AS uuid)
        )
        AND (
          CAST(:destination AS text) IS NULL
          OR lower(destination.name) = lower(CAST(:destination AS text))
        )
        AND (
          CAST(:category AS text) IS NULL
          OR lower(e.category) = lower(CAST(:category AS text))
        )
        AND (
          CAST(:rating AS numeric) IS NULL
          OR e.rating >= CAST(:rating AS numeric)
        )
        AND (
          CAST(:max_duration AS integer) IS NULL
          OR e.duration_minutes <= CAST(:max_duration AS integer)
        )
        AND (
          CAST(:indoor_outdoor AS text) IS NULL
          OR lower(e.indoor_outdoor) = lower(CAST(:indoor_outdoor AS text))
          -- "mixed" is partly indoors and partly outdoors, so it satisfies
          -- either preference.
          OR lower(e.indoor_outdoor) = 'mixed'
        )
        AND (
          CAST(:family_friendly AS boolean) IS NULL
          OR e.family_friendly = CAST(:family_friendly AS boolean)
        )
        AND (
          CAST(:instant_confirmation AS boolean) IS NULL
          OR e.instant_confirmation = CAST(:instant_confirmation AS boolean)
        )
        AND (
          coalesce(cardinality(CAST(:accessibility AS text[])), 0) = 0
          OR NOT EXISTS (
            SELECT 1
            FROM unnest(CAST(:accessibility AS text[])) required_feature
            WHERE array_to_string(e.accessibility_features, ' ')
              NOT ILIKE ('%' || required_feature || '%')
          )
        )
        AND (
          CAST(:language AS text) IS NULL
          OR EXISTS (
            SELECT 1
            FROM unnest(e.languages) supported_language
            WHERE lower(supported_language) = lower(CAST(:language AS text))
          )
        )
        -- Whole words, not substrings. This was ILIKE '%spa%', which also
        -- removed every experience whose description mentioned a *space*, and
        -- '%art%' removed anything that *started* anywhere. The patterns are
        -- built and escaped in Python so both search paths rule the same things
        -- out; `unaccent` on the haystack matches tokenize()'s accent folding,
        -- so a shopper typing `nui` still excludes `Núi`.
        AND (
          coalesce(cardinality(CAST(:exclusion_patterns AS text[])), 0) = 0
          OR NOT EXISTS (
            SELECT 1
            FROM unnest(CAST(:exclusion_patterns AS text[])) exclusion_pattern
            WHERE unaccent(concat_ws(
              ' ',
              e.title,
              e.short_description,
              e.description,
              e.category,
              array_to_string(e.subcategories, ' '),
              array_to_string(e.interest_tags, ' ')
            )) ~* exclusion_pattern
          )
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
          CAST(:currency AS text) IS NULL
          OR EXISTS (
            SELECT 1
            FROM experience_options currency_option
            JOIN option_prices currency_price ON currency_price.option_id = currency_option.id
            WHERE currency_option.experience_id = e.id
              AND currency_option.active = true
              AND currency_price.currency = CAST(:currency AS text)
          )
        )
        AND (
          CAST(:max_total_price AS numeric) IS NULL
          OR EXISTS (
            SELECT 1
            FROM experience_options budget_option
            WHERE budget_option.experience_id = e.id
              AND budget_option.active = true
              AND (
                SELECT count(*)
                FROM jsonb_to_recordset(CAST(:party AS jsonb))
                  AS requested_participant(type text, count integer)
                JOIN option_prices participant_price
                  ON participant_price.option_id = budget_option.id
                  AND participant_price.participant_type = requested_participant.type
              ) = jsonb_array_length(CAST(:party AS jsonb))
              AND (
                SELECT coalesce(
                  sum(participant_price.amount * requested_participant.count),
                  0
                )
                FROM jsonb_to_recordset(CAST(:party AS jsonb))
                  AS requested_participant(type text, count integer)
                JOIN option_prices participant_price
                  ON participant_price.option_id = budget_option.id
                  AND participant_price.participant_type = requested_participant.type
              ) <= CAST(:max_total_price AS numeric)
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
              AND slot.capacity_remaining >= CAST(:party_size AS integer)
              AND slot.starts_at::date >= CAST(:visit_start AS timestamptz)::date
              AND (
                CAST(:visit_end AS timestamptz) IS NULL
                OR slot.starts_at::date <= CAST(:visit_end AS timestamptz)::date
              )
          )
        )
    ),
    lexical AS (
      SELECT e.id,
        row_number() OVER (
          ORDER BY ts_rank_cd(
            d.search_vector,
            websearch_to_tsquery(CAST(:text_config AS regconfig), unaccent(:query))
          ) DESC
        ) AS rank
      FROM eligible e
      JOIN experience_search_documents d
        ON d.experience_id = e.id AND d.locale = :locale
      WHERE d.search_vector
            @@ websearch_to_tsquery(CAST(:text_config AS regconfig), unaccent(:query))
      ORDER BY rank
      LIMIT :lexical_limit
    ),
    semantic AS (
      SELECT e.id,
        row_number() OVER (ORDER BY d.embedding <=> CAST(:embedding AS vector)) AS rank
      FROM eligible e
      JOIN experience_search_documents d
        ON d.experience_id = e.id AND d.locale = :locale
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
    accessibility: list[str] | None = None,
    language: str | None = None,
    exclusions: list[str] | None = None,
    visit_start: str | None = None,
    visit_end: str | None = None,
    party: list[dict[str, Any]] | None = None,
    party_size: int = 1,
    lexical_limit: int = 50,
    semantic_limit: int = 50,
    rrf_k: int = 60,
    page_size: int = 20,
    locale: str = "en",
) -> list[dict[str, Any]]:
    result = await session.execute(
        HYBRID_SEARCH_SQL,
        {
            "query": query,
            "locale": locale,
            "text_config": text_search_config(locale),
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
            "language": language,
            "exclusion_patterns": exclusion_patterns(exclusions or []),
            "visit_start": visit_start,
            "visit_end": visit_end,
            "party": json.dumps(party or [{"type": "adult", "count": 1}]),
            "party_size": party_size,
            "lexical_limit": lexical_limit,
            "semantic_limit": semantic_limit,
            "rrf_k": rrf_k,
            "page_size": page_size,
        },
    )
    return [dict(row) for row in result.mappings()]
