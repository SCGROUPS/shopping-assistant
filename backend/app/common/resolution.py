"""One rule for "which text does this shopper see", used by every reader.

The indexer already had a resolver (`catalog/indexing.py`) and the API needed
one. Two implementations of the same rule is the failure this module exists to
prevent: search matches the text it indexed, so if the page resolves
differently, a shopper finds a product by words the page never shows and the
words they searched for appear nowhere. Nothing errors, and no test of either
side alone would notice. So the indexer calls this too.

The served tables (`experience_translations`, `option_translations`) contain
published text only - a candidate awaiting review lives in
`translation_fields.candidate_value` and is never joined here. That is what
makes "first non-empty along the chain" safe as the whole rule for *text*.
`translation_fields` is consulted only to describe what was served.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.locales import DEFAULT_LOCALE, fallback_chain
from app.common.models import Experience, ExperienceTranslation, TranslationField

# The four free-text fields on an experience. Everything else is either
# language-neutral (price, coordinates, duration) or taxonomy, which is
# translated through labels rather than prose (spec 4.2).
TRANSLATED_FIELDS: tuple[str, ...] = (
    "title",
    "short_description",
    "description",
    "meeting_point",
)

# What a reader is entitled to know about a string it was handed.
#   source   - the record's own authoring language, always current by definition
#   manual   - a human wrote this translation
#   machine  - a model wrote it and it answers the current source text
#   imported - it arrived from a partner already translated
#   stale    - published, but the source has changed since; still served
#   fallback - no translation exists in the requested locale
PROVENANCE_SOURCE = "source"
PROVENANCE_STALE = "stale"
PROVENANCE_FALLBACK = "fallback"


@dataclass(frozen=True)
class ResolvedField:
    """A served string and the honest description of where it came from."""

    value: str
    locale: str
    provenance: str

    @property
    def is_fallback(self) -> bool:
        """True when the shopper is not reading their own language."""
        return self.provenance == PROVENANCE_FALLBACK


def source_locale(experience: Experience) -> str:
    """The record's authoring language, lowercased.

    Locale tags are case-insensitive by specification and case-sensitive as
    database strings, so a record stored as `VI` would resolve against
    translations tagged `vi` and find none.
    """
    return (experience.source_language or DEFAULT_LOCALE).strip().lower()


def resolution_chain(experience: Experience, locale: str) -> tuple[str, ...]:
    """Locales to try for `locale`, in order, for this particular record.

    The configured chain ends at English, which is right for a catalogue
    authored in English and wrong for one authored in Vietnamese: a product
    written in Vietnamese and not yet translated has no English text, so a
    chain ending at `en` resolves to nothing and the product reads as blank.
    Appending `source_language` guarantees every chain ends somewhere
    populated, because the publish gate requires the source locale complete.
    """
    ordered = [*fallback_chain(locale), source_locale(experience), DEFAULT_LOCALE]
    seen: dict[str, None] = {}
    for candidate in ordered:
        seen.setdefault(candidate, None)
    return tuple(seen)


def _provenance(
    field_state: TranslationField | None,
    *,
    requested: str,
    served: str,
) -> str:
    """Describe a served translation, including whether it has gone stale.

    Stale text is still served. Falling back to English the moment an operator
    fixes a typo would blank an entire locale for every product touched, until
    the worker catches up - a far larger blast radius than showing a
    description that is one edit behind. The label is what keeps that honest,
    and it is why `content_meta` is part of the contract rather than an extra.
    """
    if served != requested:
        return PROVENANCE_FALLBACK
    if field_state is None:
        # Served text with no workflow row: imported alongside the record, or
        # written before this table existed. Neither is machine output, and
        # claiming it is would misattribute a human's work.
        return "imported"
    if field_state.published_fingerprint != field_state.desired_fingerprint:
        return PROVENANCE_STALE
    return field_state.provenance


async def resolve_experience_text(
    session: AsyncSession,
    experiences: Sequence[Experience],
    locale: str,
) -> dict[uuid.UUID, dict[str, ResolvedField]]:
    """Resolve every translated field of every experience, in two queries.

    Bulk deliberately. The application and the database are in different Azure
    regions, so a per-experience round trip is not a style problem - it is ten
    minutes on a catalogue job (spec 12, and the enqueue rewrite that preceded
    this one).
    """
    if not experiences:
        return {}

    chains = {item.id: resolution_chain(item, locale) for item in experiences}
    wanted_locales = {candidate for chain in chains.values() for candidate in chain}
    ids = [item.id for item in experiences]

    translation_rows = (
        await session.execute(
            select(ExperienceTranslation).where(
                ExperienceTranslation.experience_id.in_(ids),
                ExperienceTranslation.locale.in_(wanted_locales),
            )
        )
    ).scalars()
    translations: dict[tuple[uuid.UUID, str], ExperienceTranslation] = {
        (row.experience_id, row.locale): row for row in translation_rows
    }

    state_rows = (
        await session.execute(
            select(TranslationField).where(
                TranslationField.entity_type == "experience",
                TranslationField.entity_id.in_(ids),
                TranslationField.locale.in_(wanted_locales),
            )
        )
    ).scalars()
    states: dict[tuple[uuid.UUID, str, str], TranslationField] = {
        (row.entity_id, row.field, row.locale): row for row in state_rows
    }

    resolved: dict[uuid.UUID, dict[str, ResolvedField]] = {}
    for experience in experiences:
        chain = chains[experience.id]
        source = source_locale(experience)
        fields: dict[str, ResolvedField] = {}
        for field in TRANSLATED_FIELDS:
            fields[field] = _resolve_one(
                experience,
                field,
                chain=chain,
                source=source,
                requested=locale,
                translations=translations,
                states=states,
            )
        resolved[experience.id] = fields
    return resolved


def _resolve_one(
    experience: Experience,
    field: str,
    *,
    chain: Sequence[str],
    source: str,
    requested: str,
    translations: dict[tuple[uuid.UUID, str], ExperienceTranslation],
    states: dict[tuple[uuid.UUID, str, str], TranslationField],
) -> ResolvedField:
    for candidate in chain:
        if candidate == source:
            value = (getattr(experience, field, "") or "").strip()
            if value:
                return ResolvedField(
                    value=value,
                    locale=candidate,
                    provenance=(
                        PROVENANCE_SOURCE
                        if candidate == requested
                        else PROVENANCE_FALLBACK
                    ),
                )
            continue
        row = translations.get((experience.id, candidate))
        if row is None:
            continue
        value = (getattr(row, field, "") or "").strip()
        if value:
            return ResolvedField(
                value=value,
                locale=candidate,
                provenance=_provenance(
                    states.get((experience.id, field, candidate)),
                    requested=requested,
                    served=candidate,
                ),
            )
    # Every chain ends at the source locale, so arriving here means the source
    # itself is empty. Returning the empty string is right - inventing text
    # would be worse - but the locale is the source's, because that is the
    # language the blank belongs to.
    return ResolvedField(
        value=(getattr(experience, field, "") or ""),
        locale=source,
        provenance=PROVENANCE_SOURCE if source == requested else PROVENANCE_FALLBACK,
    )


def content_meta(fields: dict[str, ResolvedField]) -> dict[str, dict[str, str]]:
    """The additive per-field provenance map promised at the API boundary.

    A parallel map rather than a change to the field types, so existing
    clients keep working and a client that cannot tell fallback from
    translation is a client that chose not to look (spec 4.3).
    """
    return {
        name: {"locale": item.locale, "provenance": item.provenance}
        for name, item in fields.items()
    }


def fallback_fields(fields: dict[str, ResolvedField]) -> tuple[str, ...]:
    """Which fields were not available in the requested locale.

    Spec 10 requires fallback rate *by field* as an operational metric: a
    locale whose titles are translated and whose descriptions are not is a
    different problem from one nobody has started, and a single overall
    percentage cannot tell them apart.
    """
    return tuple(name for name, item in fields.items() if item.is_fallback)


def resolved_text(fields: dict[str, ResolvedField], names: Iterable[str]) -> list[str]:
    """The served values for `names`, in order, skipping blanks."""
    return [value for name in names if (value := fields[name].value)]
