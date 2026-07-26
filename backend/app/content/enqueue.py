"""Deciding what needs translating, and recording it so a crash costs one retry.

The rule this module enforces is the same one the indexing outbox enforces, one
layer up: *the catalogue decides what should exist, and a job is only ever a
request to go and make it so*. A job carries the fingerprint and generation it
was created for; a worker that commits against a stale pair loses its
conditional update rather than publishing text that answers a title nobody has
any more.

`generation` increments whenever `desired_fingerprint` changes, whatever the
cause. Restricting it to source and manual edits reintroduces the exact bug it
exists to close, from the other side: a recipe revert R0 -> R1 -> R0 leaves the
generation unchanged, the final R0 job collides with the completed original
under the uniqueness key, enqueueing is swallowed, and the field stays stale
forever with nothing to notice.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import get_settings
from app.common.locales import SUPPORTED_LOCALES
from app.common.models import (
    Experience,
    TranslationField,
    TranslationGlossary,
    TranslationJob,
)
from app.content.fingerprints import (
    desired_fingerprint,
    recipe_fingerprint,
    source_fingerprint,
)

# The prose a shopper reads. Deliberately not every text column on the row:
# `slug` is an identifier, and translating it would break every link that has
# ever been shared.
EXPERIENCE_FIELDS: tuple[str, ...] = (
    "title",
    "short_description",
    "description",
    "meeting_point",
)

ENTITY_EXPERIENCE = "experience"


async def glossary_revision(session: AsyncSession) -> int:
    """The highest revision any term carries, which is the recipe's input.

    A sum or a hash of the contents would invalidate on a typo fix. The maximum
    revision changes only when an operator deliberately bumps a term, which is
    what makes glossary invalidation an explicit decision.
    """
    highest = await session.scalar(select(func.max(TranslationGlossary.revision)))
    return int(highest or 0)


async def _recipe(session: AsyncSession) -> str:
    settings = get_settings()
    return recipe_fingerprint(
        prompt_version=settings.translation_prompt_version,
        glossary_revision=await glossary_revision(session),
        model=settings.translation_deployment,
    )


def target_locales(source_language: str) -> tuple[str, ...]:
    """Every supported locale except the one the listing was written in.

    Translating a Vietnamese listing into Vietnamese would produce a machine
    round trip of text a human already wrote, and mark it `machine`.
    """
    return tuple(locale for locale in SUPPORTED_LOCALES if locale != source_language)


async def enqueue_experience_translations(
    session: AsyncSession,
    experience_id: uuid.UUID,
    *,
    locales: Sequence[str] | None = None,
) -> int:
    """Bring one experience's translation state up to date with its source.

    Returns the number of jobs enqueued. Called in the *same transaction* as
    whatever changed the source, so a commit that changes a title cannot land
    without the work to retranslate it — the same invariant the indexing outbox
    depends on, for the same reason.
    """
    experience = await session.get(Experience, experience_id)
    if experience is None:
        return 0

    recipe = await _recipe(session)
    wanted = tuple(locales) if locales is not None else target_locales(experience.source_language)

    existing = {
        (row.field, row.locale): row
        for row in (
            await session.scalars(
                select(TranslationField).where(
                    TranslationField.entity_type == ENTITY_EXPERIENCE,
                    TranslationField.entity_id == experience_id,
                )
            )
        ).all()
    }

    enqueued = 0
    for field in EXPERIENCE_FIELDS:
        text_value = getattr(experience, field, "") or ""
        for locale in wanted:
            source = source_fingerprint(
                text=text_value,
                source_language=experience.source_language,
                locale=locale,
            )
            row = existing.get((field, locale))
            provenance = row.provenance if row is not None else "machine"
            desired = desired_fingerprint(
                source=source, recipe=recipe, provenance=provenance
            )

            if row is None:
                generation = 0
                await session.execute(
                    pg_insert(TranslationField)
                    .values(
                        entity_type=ENTITY_EXPERIENCE,
                        entity_id=experience_id,
                        field=field,
                        locale=locale,
                        provenance=provenance,
                        status="pending",
                        published_fingerprint=None,
                        desired_fingerprint=desired,
                        generation=generation,
                    )
                    .on_conflict_do_nothing()
                )
            elif row.desired_fingerprint != desired:
                generation = row.generation + 1
                row.desired_fingerprint = desired
                row.generation = generation
                # A rejected row reopens only when what it should say changes -
                # which is exactly here. Anything already `current` becomes
                # pending again because it now answers text that has moved.
                row.status = "pending"
                # The candidate answered the old fingerprint. Keeping it would
                # let a reviewer approve text that describes a title nobody has.
                row.candidate_value = None
                row.candidate_fingerprint = None
            else:
                # Nothing to do, and crucially no job: a hundred re-imports of
                # unchanged content must create no work at all.
                continue

            if provenance == "manual":
                # A human owns this field. It is now marked stale so the console
                # can surface it, but no machine job is created - a translator
                # outranks the model, and silently overwriting them is the
                # failure this whole table exists to prevent.
                continue

            result = await session.execute(
                pg_insert(TranslationJob)
                .values(
                    id=uuid.uuid4(),
                    entity_type=ENTITY_EXPERIENCE,
                    entity_id=experience_id,
                    field=field,
                    locale=locale,
                    fingerprint=desired,
                    generation=generation,
                    status="queued",
                    attempts=0,
                )
                .on_conflict_do_nothing(constraint="ux_translation_job_target")
                .returning(TranslationJob.id)
            )
            if result.scalar_one_or_none() is not None:
                enqueued += 1

    return enqueued


async def mark_manual_translation(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    editor: str,
) -> None:
    """Record that a human now owns this field.

    Sets `published_fingerprint = desired_fingerprint`, because a human writing
    a translation of the current source has by definition produced something
    current, and recomputes `desired` without the recipe so a later model
    upgrade cannot mark their work stale.

    Does not write the served text: the caller writes the wide table in the same
    transaction, one column at a time, after this row is locked. Every path
    takes the locks in that order.
    """
    experience = await session.get(Experience, experience_id)
    if experience is None:
        raise ValueError(f"no experience {experience_id}")

    source = source_fingerprint(
        text=getattr(experience, field, "") or "",
        source_language=experience.source_language,
        locale=locale,
    )
    desired = desired_fingerprint(source=source, recipe="", provenance="manual")

    row = await session.get(
        TranslationField,
        (ENTITY_EXPERIENCE, experience_id, field, locale),
        with_for_update=True,
    )
    if row is None:
        session.add(
            TranslationField(
                entity_type=ENTITY_EXPERIENCE,
                entity_id=experience_id,
                field=field,
                locale=locale,
                provenance="manual",
                status="current",
                published_fingerprint=desired,
                desired_fingerprint=desired,
                generation=0,
                reviewed_by=editor,
            )
        )
        return

    row.provenance = "manual"
    row.status = "current"
    row.published_fingerprint = desired
    if row.desired_fingerprint != desired:
        # Dropping the recipe from the fingerprint changes it, and any change to
        # `desired` is a generation bump - which also invalidates every job in
        # flight for this field, exactly as intended.
        row.generation += 1
    row.desired_fingerprint = desired
    row.candidate_value = None
    row.candidate_fingerprint = None
    row.reviewed_by = editor
