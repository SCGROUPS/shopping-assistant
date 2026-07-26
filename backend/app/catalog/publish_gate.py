"""What a listing must have before it is allowed to be sold.

`docs/CONTENT_PIPELINE.md` §5.3. Two things made this worth building rather
than trusting operators with.

The first is that `resolution_chain` already depends on it. Its docstring says
every fallback chain ends somewhere populated "because the publish gate
requires the source locale complete" - a load-bearing assumption with nothing
behind it. Production has three PUBLISHED listings with an empty
`description`, and the only way anybody found them was a script that fetched
all 379 records one at a time.

The second is that some of these are not judgement calls at all.
`catalog/service.py` raises `409 Unbookable experience` when no active option
carries an adult price, so a listing without one is publishable and broken on
its own product page - live, sellable, and unable to sell.

Blockers are returned as a list, never as the first failure. An operator who
fixes one thing, resubmits and is told about the next thing learns that the
system is wasting their time; the spec is explicit that refusals are itemised.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.catalog.indexing import (
    FALLBACK_EMBEDDING_MODEL,
    document_text_for,
    index_fingerprint,
)
from app.common.models import (
    Destination,
    Experience,
    ExperienceMedia,
    ExperienceOption,
    ExperienceSearchDocument,
    IndexWorkItem,
    OptionPrice,
    Supplier,
)
from app.common.resolution import source_locale

# Text a shopper reads before deciding. Checked on the wide `experiences` row
# because that row *is* the source language - `resolution.py` reads
# `getattr(experience, field)` for the source locale and `ExperienceTranslation`
# for every other one.
REQUIRED_TEXT = ("title", "short_description", "description")

# Facts a shopper needs to turn an interest into a booking: where it is, what
# kind of thing it is, how long it takes, and where to stand.
REQUIRED_FACTS = ("category", "duration_minutes", "meeting_point")


@dataclass(frozen=True)
class Blocker:
    """One reason a listing cannot go live, in both registers.

    `code` is for the console, which needs to decide what to highlight without
    parsing English. `message` is for the operator, and names the remedy rather
    than the rule - "no description" is a verdict, "write a description" is a
    task.
    """

    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


async def publish_blockers(
    session: AsyncSession, experience: Experience, *, require_index: bool = True
) -> list[Blocker]:
    """Everything standing between this listing and PUBLISHED.

    Empty means publishable. The checks run in the order an operator would
    work them - text, facts, commerce, media, ownership, findability - so the
    list reads as a to-do rather than as a dump.

    `require_index=False` is for the importer, which is the transaction that
    *builds* the document. Asking it whether the document exists yet would
    refuse every product on its first import and publish it on the second,
    which is not a rule anybody could reason about. Findability is still
    enforced there, just by a different mechanism: reconciliation repairs
    missing documents, and `unpublishable_now` surfaces the ones it has not
    reached.
    """
    blockers: list[Blocker] = []
    locale = source_locale(experience)

    for field in REQUIRED_TEXT:
        if not (getattr(experience, field, "") or "").strip():
            blockers.append(
                Blocker(
                    f"missing-{field.replace('_', '-')}",
                    f"write a {field.replace('_', ' ')} in {locale}",
                )
            )

    for field in REQUIRED_FACTS:
        value = getattr(experience, field, None)
        # `duration_minutes` is an int, so falsiness is the right test for it
        # and zero is genuinely missing; the rest are strings.
        if value is None or (isinstance(value, str) and not value.strip()) or value == 0:
            blockers.append(
                Blocker(
                    f"missing-{field.replace('_', '-')}",
                    f"set the {field.replace('_', ' ')}",
                )
            )

    if experience.destination_id is None:
        blockers.append(Blocker("missing-destination", "set the destination"))

    blockers.extend(await _commerce_blockers(session, experience.id))

    media = await session.scalar(
        select(func.count())
        .select_from(ExperienceMedia)
        .where(ExperienceMedia.experience_id == experience.id)
    )
    if not media:
        blockers.append(Blocker("no-image", "add at least one image"))

    supplier = await session.get(Supplier, experience.supplier_id)
    if supplier is None or supplier.is_placeholder:
        # The house supplier exists so a manual draft can be saved before
        # anyone knows who will fulfil it. Letting it reach a shopper would
        # sell a booking with no fulfilment owner.
        blockers.append(Blocker("placeholder-supplier", "assign a real supplier"))

    if require_index:
        blockers.extend(await _index_blockers(session, experience, locale))
    return blockers


async def _commerce_blockers(session: AsyncSession, experience_id: UUID) -> list[Blocker]:
    """Whether the listing can actually take money.

    `option_prices` has no active flag of its own - the *option* carries
    `active` - so the rule is an active option with an adult price on it, and
    checking the two independently would pass a listing whose only adult price
    hangs off a deactivated option.
    """
    sellable = await session.scalar(
        select(func.count())
        .select_from(ExperienceOption)
        .join(OptionPrice, OptionPrice.option_id == ExperienceOption.id)
        .where(
            ExperienceOption.experience_id == experience_id,
            ExperienceOption.active.is_(True),
            func.lower(OptionPrice.participant_type) == "adult",
            OptionPrice.amount > 0,
        )
    )
    if sellable:
        return []

    any_option = await session.scalar(
        select(func.count())
        .select_from(ExperienceOption)
        .where(ExperienceOption.experience_id == experience_id)
    )
    if not any_option:
        return [Blocker("no-option", "add a bookable option")]
    return [
        Blocker(
            "no-adult-price",
            "put an adult price on an active option, or the product page returns 409",
        )
    ]


async def _index_blockers(
    session: AsyncSession, experience: Experience, locale: str
) -> list[Blocker]:
    """Whether a shopper could find it.

    A product nobody can retrieve is not published in any sense a shopper would
    recognise - the defect in §1.4, which is what this whole document exists to
    stop recurring.

    Staleness and placeholder vectors are separate blockers because they have
    separate remedies. A document whose text has moved on needs reindexing; a
    document embedded by the deterministic fallback during a provider outage
    needs the provider back. Reporting either as "not indexed" would send an
    operator to reindex a document that will come back just as meaningless.
    """
    document = await session.get(ExperienceSearchDocument, (experience.id, locale))
    if document is None:
        return [Blocker("not-indexed", f"wait for the {locale} search document to be built")]

    if document.embedding is None or document.embedding_model == FALLBACK_EMBEDDING_MODEL:
        return [
            Blocker(
                "placeholder-embedding",
                "the search document holds placeholder vectors; the embedding provider "
                "was unavailable when it was built",
            )
        ]

    destination_name = ""
    if experience.destination_id is not None:
        destination_name = (
            await session.scalar(
                select(Destination.name).where(Destination.id == experience.destination_id)
            )
            or ""
        )
    # Recomputed with the model the document records, not the current constant.
    # Using the constant would report every document stale the moment the model
    # is upgraded - which is true, but it is a reindexing backlog rather than an
    # authoring mistake, and blocking publication on it would tell operators to
    # fix something they cannot.
    expected = index_fingerprint(
        document_text_for(experience, destination_name), locale, model=document.embedding_model
    )
    if document.index_fingerprint == expected:
        return []

    # Stale text with a rebuild already scheduled is not the defect this rule
    # exists to catch. `IndexWorkItem` is an outbox, and its guarantee is
    # written on the model: "if the write commits the reindex is guaranteed".
    # The state §1.4 described is content nothing will *ever* index - not
    # content a worker is a few seconds behind on.
    #
    # The distinction is what keeps the gate usable. Editing a title makes the
    # document stale by definition, so without this an operator who fixes a
    # typo and publishes is refused with "wait" - and a gate whose only remedy
    # is to wait is a gate people learn to route around.
    scheduled = await session.scalar(
        select(func.count())
        .select_from(IndexWorkItem)
        .where(
            IndexWorkItem.experience_id == experience.id,
            IndexWorkItem.locale == locale,
            # 'failed' is terminal and invisible to the leasing query, so it is
            # emphatically not a promise that anything will be rebuilt.
            IndexWorkItem.status.in_(("queued", "leased")),
        )
    )
    if scheduled:
        return []
    return [
        Blocker(
            "stale-index",
            "the search document describes older text and no rebuild is scheduled; "
            "reindex the listing",
        )
    ]


def unpublishable_now() -> ColumnElement[bool]:
    """SQL for "this listing would be refused if it were published today".

    The gate guards a *transition*, so everything already PUBLISHED keeps its
    status no matter how incomplete it is. That is the right behaviour - a
    deploy that silently unpublished live inventory would be far worse - but it
    means the gate on its own makes nothing better for the catalogue that
    already exists. Production's three description-less listings were found by
    a script that fetched all 379 records one at a time, which is not a thing an
    operator can do.

    So the same rules are also expressed as a predicate the console can filter
    on. It is deliberately the cheap subset: everything here is a column or an
    EXISTS, and the two index checks that need a fingerprint recomputed per row
    (`stale-index`, `placeholder-embedding`) are left to `publish_blockers` on
    a single record. A filter that finds most of the damage in one query beats
    an exact one nobody can afford to run.
    """
    empty_text = [
        func.coalesce(func.trim(getattr(Experience, field)), "") == "" for field in REQUIRED_TEXT
    ]
    return or_(
        *empty_text,
        func.coalesce(func.trim(Experience.category), "") == "",
        func.coalesce(func.trim(Experience.meeting_point), "") == "",
        func.coalesce(Experience.duration_minutes, 0) == 0,
        Supplier.is_placeholder.is_(True),
        ~select(1)
        .select_from(ExperienceMedia)
        .where(ExperienceMedia.experience_id == Experience.id)
        .exists(),
        ~select(1)
        .select_from(ExperienceOption)
        .join(OptionPrice, OptionPrice.option_id == ExperienceOption.id)
        .where(
            ExperienceOption.experience_id == Experience.id,
            ExperienceOption.active.is_(True),
            func.lower(OptionPrice.participant_type) == "adult",
            OptionPrice.amount > 0,
        )
        .exists(),
        ~select(1)
        .select_from(ExperienceSearchDocument)
        .where(
            ExperienceSearchDocument.experience_id == Experience.id,
            ExperienceSearchDocument.locale
            == func.lower(func.trim(func.coalesce(Experience.source_language, "en"))),
        )
        .exists(),
    )
