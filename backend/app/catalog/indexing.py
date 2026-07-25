"""Search indexing as an explicit consequence of content change.

Before this module, an `ExperienceSearchDocument` was only ever constructed by
the Trippass importer and the database seeder. Every other way content can
change - an operator correcting a title in the console, an override being
released, a partner revision being approved - changed what a product *said* and
nothing about what search *matched*. On a manually authored record, which no
importer is allowed to touch, the index would never have caught up at all.

The fix is an outbox. Writers enqueue an `IndexWorkItem` in the same
transaction as the content change, so the intent commits or rolls back with the
change itself; a worker leases items and rebuilds documents. Enqueueing after
commit would lose work in the crash window, and that is exactly how an index
starts describing a catalogue that no longer exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.locales import DEFAULT_LOCALE, SUPPORTED_LOCALES, fallback_chain
from app.common.models import (
    Destination,
    Experience,
    ExperienceSearchDocument,
    ExperienceTranslation,
    IndexWorkItem,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.assistant.provider import AIProvider

logger = logging.getLogger(__name__)

# Bumped whenever `document_text_for` changes shape. It is part of the
# fingerprint so that improving how a document is built invalidates every
# document; without it the first such deploy would leave the whole catalogue
# indexed the old way with nothing marked stale and no error anywhere.
DOCUMENT_VERSION = "2"

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_VERSION = "1"

# Written instead of `EMBEDDING_MODEL` when a document is indexed with a
# deterministic placeholder vector because the provider was unavailable. It has
# to be a different name, or the fingerprint would certify meaningless numbers
# as a current index and reconciliation would never replace them.
FALLBACK_EMBEDDING_MODEL = "deterministic-fallback"
# Experiences per reconciliation transaction.
RECONCILE_PAGE_SIZE = 200

LEASE_SECONDS = 300
MAX_ATTEMPTS = 5


def document_text_for(experience: Experience, destination_name: str) -> str:
    """Compose the text a search document indexes, from ORM entities.

    Deliberately mirrors `db_seed._document_text`, which works from importer
    dictionaries. Two shapes of the same catalogue exist in this codebase and
    both must produce the same document, or a record would be found or missed
    depending on which code path last touched it.
    """
    parts: list[str] = [
        experience.title,
        destination_name,
        experience.category,
        *(experience.subcategories or []),
        *(experience.interest_tags or []),
        experience.short_description or "",
        experience.description or "",
        experience.indoor_outdoor or "",
        *(experience.accessibility_features or []),
        *(experience.languages or []),
    ]
    return " ".join(part for part in parts if part)


def index_fingerprint(document_text: str, locale: str, model: str = EMBEDDING_MODEL) -> str:
    """Everything that determines the stored document, not just its text.

    The embedding model and the construction version are inside the hash on
    purpose: a model upgrade produces a different vector for identical text, so
    a fingerprint over text alone would report a stale index as current.

    `model` is a parameter rather than a constant because a document is not
    always built by the real model. When the provider is unavailable the
    importer falls back to deterministic vectors, and writing those under the
    production model name would certify them as current forever - one transient
    outage would leave the vector index holding numbers unrelated to any query
    embedding, with reconciliation reporting the catalogue perfectly healthy.
    """
    payload = "\x1f".join([DOCUMENT_VERSION, model, EMBEDDING_VERSION, locale, document_text])
    return hashlib.sha256(payload.encode()).hexdigest()


async def enqueue_reindex(
    session: AsyncSession,
    experience_id: uuid.UUID,
    locale: str,
    fingerprint: str,
    *,
    only_if_idle: bool = False,
) -> int:
    """Record the intent to reindex, in the caller's transaction.

    Re-enqueueing a locale that is already queued replaces the fingerprint
    rather than adding a row: only the newest content is worth building, and a
    leased worker discovers the change through the fingerprint check at commit
    time rather than by racing for a second row.

    New content resets `attempts`. Without that, an item that had already burnt
    its retries would hand the exhausted counter to the next edit, so a product
    whose indexing failed once could never be reindexed again however many times
    an operator corrected it.
    """
    statement = pg_insert(IndexWorkItem).values(
        experience_id=experience_id,
        locale=locale,
        fingerprint=fingerprint,
        status="queued",
        lease_token=None,
        leased_until=None,
    )
    # A `failed` item is revived even when the fingerprint is unchanged: it is
    # by definition not indexed, so any request to index it should retry it.
    # Without this, an item that failed during a provider outage could only be
    # repaired by editing the product, because reconciliation would ask for the
    # same fingerprint and the conflict clause would do nothing - while still
    # reporting the repair as enqueued.
    #
    # `only_if_idle` is the reverse concern. Reconciliation reads the
    # catalogue, computes a fingerprint, and enqueues later; in that window an
    # edit can commit a newer fingerprint, and an unconditional write would
    # replace it with the older one. The worker would then recompute the newer
    # content, find the work item disagrees, and discard its own correct output.
    condition = (
        IndexWorkItem.status.in_(["done", "failed"])
        if only_if_idle
        else (IndexWorkItem.fingerprint != statement.excluded.fingerprint)
        | (IndexWorkItem.status == "failed")
    )
    # Counted with RETURNING rather than `rowcount`: SQLAlchemy only memoizes
    # `rowcount` for UPDATE and DELETE, so an INSERT reports -1 once its cursor
    # is closed, and callers that sum the result would report negative repairs.
    # RETURNING also states the intent directly - a row comes back only when the
    # conflict clause actually wrote something.
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=[IndexWorkItem.experience_id, IndexWorkItem.locale],
            set_={
                "fingerprint": statement.excluded.fingerprint,
                "status": "queued",
                "lease_token": None,
                "leased_until": None,
                "attempts": 0,
                "error_detail": None,
                "updated_at": datetime.now(UTC),
            },
            where=condition,
        ).returning(IndexWorkItem.experience_id)
    )
    return len(result.all())


def source_locale(experience: Experience) -> str:
    """The record's authoring language, in the one spelling everything else uses.

    Locale tags are case-insensitive by specification and case-sensitive as
    database strings. Normalising in some places and not others is worse than
    not normalising at all: a record stored as `VI` would be indexed under
    `vi` while its resolution chain looked for translations tagged `VI`, so the
    document would be built, stored, and searchable - containing no text.
    """
    return (experience.source_language or DEFAULT_LOCALE).strip().lower()


async def mark_locale_indexed(
    session: AsyncSession,
    experience_id: uuid.UUID,
    locale: str,
    fingerprint: str,
) -> None:
    """Retire a work item a caller has just satisfied itself, in its transaction.

    Used by the importer, which already computed an embedding and writes the
    source document directly. It must still *enqueue* first - taking the work
    item's row lock is the only thing that stops a worker holding an older
    version from overwriting the document after the import commits - and this is
    what stops the enqueue from then causing a pointless rebuild of a document
    that is already correct.

    Conditional on the fingerprint, so a document written with a fallback
    embedding does not retire the request that exists to replace it.
    """
    await session.execute(
        update(IndexWorkItem)
        .where(
            IndexWorkItem.experience_id == experience_id,
            IndexWorkItem.locale == locale,
            IndexWorkItem.status == "queued",
            IndexWorkItem.fingerprint == fingerprint,
        )
        .values(
            status="done",
            lease_token=None,
            leased_until=None,
            attempts=0,
            error_detail=None,
            updated_at=datetime.now(UTC),
        )
    )


async def resolution_chain(experience: Experience, locale: str) -> tuple[str, ...]:
    """Locales to try for `locale`, in order, for this particular record.

    The configured chain ends at English, which is right for a catalogue
    authored in English and wrong for one authored in Vietnamese: a product
    written in Vietnamese and not yet translated has no English text, so a
    chain ending at `en` resolves to nothing and the product reads as blank.
    Appending `source_language` guarantees every chain ends somewhere that is
    populated, because the publish gate requires the source locale to be
    complete.
    """
    ordered = [*fallback_chain(locale), source_locale(experience), DEFAULT_LOCALE]
    seen: dict[str, None] = {}
    for candidate in ordered:
        seen.setdefault(candidate, None)
    return tuple(seen)


async def resolved_document_text(
    session: AsyncSession,
    experience: Experience,
    destination_name: str,
    locale: str,
) -> str:
    """The text a shopper in `locale` would actually be shown.

    Indexing the source text under every locale would make a Vietnamese
    product findable by an English shopper only through its proper nouns;
    indexing only the source locale would make it unfindable entirely, because
    retrieval filters on locale and an absent document is never a candidate.
    Indexing what is *served* keeps the index and the page in agreement, which
    is the only version of this that cannot surprise anyone.
    """
    chain = await resolution_chain(experience, locale)
    rows = (
        await session.execute(
            select(ExperienceTranslation).where(
                ExperienceTranslation.experience_id == experience.id,
                ExperienceTranslation.locale.in_(chain),
            )
        )
    ).scalars()
    translations = {row.locale: row for row in rows}

    source = source_locale(experience)

    def resolve(field: str) -> str:
        for candidate in chain:
            if candidate == source:
                value = getattr(experience, field, "") or ""
                if value:
                    return value
                continue
            row = translations.get(candidate)
            if row is not None:
                value = getattr(row, field, "") or ""
                if value:
                    return value
        return getattr(experience, field, "") or ""

    parts: list[str] = [
        resolve("title"),
        destination_name,
        experience.category,
        *(experience.subcategories or []),
        *(experience.interest_tags or []),
        resolve("short_description"),
        resolve("description"),
        experience.indoor_outdoor or "",
        *(experience.accessibility_features or []),
        *(experience.languages or []),
    ]
    return " ".join(part for part in parts if part)


def indexed_locales(experience: Experience) -> tuple[str, ...]:
    """Every locale this record needs a document in.

    `SUPPORTED_LOCALES` plus the record's own source language. A listing
    authored in a language the storefront does not sell in still has to be
    indexed in that language, because the publish gate requires a current
    source-locale document - so omitting it makes such a record unpublishable
    for a reason no error message would explain.
    """
    source = source_locale(experience)
    if source in SUPPORTED_LOCALES:
        return SUPPORTED_LOCALES
    return (*SUPPORTED_LOCALES, source)


async def enqueue_experience_reindex(
    session: AsyncSession,
    experience_id: uuid.UUID,
    locales: Sequence[str] | None = None,
) -> None:
    """Enqueue every shopper locale, not just English.

    A document exists per locale and retrieval filters on it, so a locale with
    no document is a locale in which the product cannot be found at all. Since
    every locale resolves to *something* (see `resolution_chain`), every
    enabled locale gets a document - initially built from source text, and
    improving as translations land.
    """
    row = (
        await session.execute(
            select(Experience, Destination.name)
            .join(Destination, Destination.id == Experience.destination_id)
            .where(Experience.id == experience_id)
        )
    ).first()
    if row is None:
        return
    experience, destination_name = row

    current = {
        locale: fingerprint
        for locale, fingerprint in (
            await session.execute(
                select(
                    ExperienceSearchDocument.locale,
                    ExperienceSearchDocument.index_fingerprint,
                ).where(ExperienceSearchDocument.experience_id == experience_id)
            )
        ).all()
    }

    for locale in locales or indexed_locales(experience):
        text = await resolved_document_text(session, experience, destination_name, locale)
        fingerprint = index_fingerprint(text, locale)
        # A locale whose stored document already carries this exact fingerprint
        # needs no work. That is what lets a caller which has just written one
        # locale directly - the importer - enqueue every locale unconditionally
        # and have only the genuinely stale ones become jobs.
        if current.get(locale) == fingerprint:
            continue
        await enqueue_reindex(session, experience_id, locale, fingerprint)


async def upsert_search_document(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    locale: str,
    document_text: str,
    embedding: Any,
    embedding_model: str = EMBEDDING_MODEL,
) -> None:
    """Write one locale's document, leaving every other locale alone.

    The importer used to delete all documents for an experience and recreate
    one. After a multilingual backfill that is destructive: a single run of an
    older application instance would erase seven locales and leave English,
    with search quietly degrading rather than failing.
    """
    now = datetime.now(UTC)
    statement = pg_insert(ExperienceSearchDocument).values(
        experience_id=experience_id,
        locale=locale,
        document_text=document_text,
        embedding=embedding,
        embedding_model=embedding_model,
        embedding_version=EMBEDDING_VERSION,
        content_hash=hashlib.sha256(document_text.encode()).hexdigest(),
        index_fingerprint=index_fingerprint(document_text, locale, embedding_model),
        embedded_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                ExperienceSearchDocument.experience_id,
                ExperienceSearchDocument.locale,
            ],
            set_={
                "document_text": statement.excluded.document_text,
                "embedding": statement.excluded.embedding,
                "embedding_model": statement.excluded.embedding_model,
                "embedding_version": statement.excluded.embedding_version,
                "content_hash": statement.excluded.content_hash,
                "index_fingerprint": statement.excluded.index_fingerprint,
                "embedded_at": statement.excluded.embedded_at,
            },
        )
    )


async def _lease(session: AsyncSession, limit: int) -> list[tuple[Any, ...]]:
    """Claim work, skipping rows another worker holds.

    `SKIP LOCKED` rather than a status flag alone: two workers polling the same
    queue would otherwise both read the same queued rows and duplicate the
    embedding calls, which cost money.
    """
    token = uuid.uuid4()
    # Lease deadlines are read and written in the database's clock, never the
    # application's. Replicas do not agree on the time, and a replica running a
    # few seconds fast would expire leases another replica is still working -
    # or, running slow, decline to reclaim leases that really are dead.
    now = func.now()

    # Retire leases that expired with no attempts left. Leasing requires
    # `attempts < MAX_ATTEMPTS`, so an item whose lease expired on its final
    # attempt is never selected again while its status still reads `leased` -
    # not queued, not failed, not held by anyone. That happens whenever the
    # transition itself could not be written: a database error during `_fail`,
    # or a process that died between leasing and recording the outcome. Without
    # this sweep those rows are invisible to the queue, to the backlog count and
    # to any retry, forever.
    await session.execute(
        update(IndexWorkItem)
        .where(
            IndexWorkItem.status == "leased",
            IndexWorkItem.leased_until < now,
            IndexWorkItem.attempts >= MAX_ATTEMPTS,
        )
        .values(
            status="failed",
            lease_token=None,
            leased_until=None,
            error_detail="lease expired with no attempts remaining",
            updated_at=now,
        )
    )

    candidates = (
        select(IndexWorkItem.experience_id, IndexWorkItem.locale)
        .where(
            IndexWorkItem.status.in_(["queued", "leased"]),
            (IndexWorkItem.leased_until.is_(None)) | (IndexWorkItem.leased_until < now),
            IndexWorkItem.attempts < MAX_ATTEMPTS,
        )
        .order_by(IndexWorkItem.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    keys = list((await session.execute(candidates)).all())
    if not keys:
        return []

    for experience_id, locale in keys:
        await session.execute(
            update(IndexWorkItem)
            .where(
                IndexWorkItem.experience_id == experience_id,
                IndexWorkItem.locale == locale,
            )
            .values(
                status="leased",
                lease_token=token,
                leased_until=now + text(f"interval '{LEASE_SECONDS} seconds'"),
                attempts=IndexWorkItem.attempts + 1,
                updated_at=now,
            )
        )
    return [(experience_id, locale, token) for experience_id, locale in keys]


async def process_index_work(
    session_factory: Any,
    provider: AIProvider,
    *,
    limit: int = 25,
) -> int:
    """Build documents for leased work items. Returns the number completed.

    The commit is conditional on the work item still being the one we leased.
    Without that check the sequence "worker leases F0, an edit replaces it with
    F1, worker writes the F0 document and marks it done" would both store a
    stale document and consume the request that would have corrected it - an
    index that is wrong and a backlog that says everything is fine.
    """
    async with session_factory() as session, session.begin():
        leases = await _lease(session, limit)

    completed = 0
    embeddings: dict[str, Any] = {}
    remaining = list(leases)
    try:
        for index, (experience_id, locale, token) in enumerate(leases):
            # The current item stays in `remaining` for the whole iteration,
            # including while its failure is being recorded. Dropping it first
            # meant a cancellation arriving during `_fail` released every later
            # lease and left this one held with its attempt spent. Releasing an
            # item that `_fail` already moved is harmless - the update requires
            # the row to still be `leased` under this token.
            remaining = list(leases[index:])
            try:
                completed += await _process_one(
                    session_factory, provider, experience_id, locale, token, embeddings
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad record must not strand the batch
                # Every failure has to reach the same transition, not just the
                # embedding call. A database or data error raised while loading
                # the record or writing the document would otherwise escape,
                # leaving this item and every later one leased - and since
                # `_lease` skips items at the attempt limit, a repeatable error
                # would strand them permanently in `leased`, invisible to both
                # the queue and any retry.
                logger.exception("Indexing failed for %s/%s", experience_id, locale)
                await _fail(session_factory, experience_id, locale, token, "indexing failed")
            remaining = list(leases[index + 1 :])
        remaining = []
    except asyncio.CancelledError:
        # A shutdown mid-batch would otherwise leave every unstarted item leased
        # for the full lease duration, and each redeploy would burn another
        # attempt against MAX_ATTEMPTS. Hand the work back before going away.
        await _release(session_factory, remaining)
        raise
    return completed


async def _fail(
    session_factory: Any, experience_id: Any, locale: str, token: Any, detail: str
) -> None:
    """Return an item for retry, or retire it once its attempts are spent.

    An item left `queued` past the attempt limit is never selected again while
    every status view reports it as pending, so exhaustion has to be an explicit
    terminal state rather than the absence of progress.
    """
    # Logged rather than suppressed. When the original error is a database
    # error, the transition is the operation most likely to fail too - and a
    # silent failure here leaves the item `leased` with its attempt consumed,
    # which is precisely the state nothing else was looking for. The expired
    # lease sweep in `_lease` is what recovers it; this is what explains it.
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                update(IndexWorkItem)
                .where(
                    IndexWorkItem.experience_id == experience_id,
                    IndexWorkItem.locale == locale,
                    IndexWorkItem.lease_token == token,
                )
                .values(
                    status=case(
                        (IndexWorkItem.attempts >= MAX_ATTEMPTS, "failed"),
                        else_="queued",
                    ),
                    lease_token=None,
                    leased_until=None,
                    error_detail=detail,
                    updated_at=datetime.now(UTC),
                )
            )
    except Exception:  # noqa: BLE001 - recovery is the sweep, not a raise here
        logger.exception("Could not record the failure of %s/%s", experience_id, locale)


async def _release(session_factory: Any, leases: Sequence[tuple[Any, str, Any]]) -> None:
    """Return still-held leases to the queue without consuming an attempt."""
    if not leases:
        return
    with suppress(Exception):
        async with session_factory() as session, session.begin():
            for experience_id, locale, token in leases:
                await session.execute(
                    update(IndexWorkItem)
                    .where(
                        IndexWorkItem.experience_id == experience_id,
                        IndexWorkItem.locale == locale,
                        IndexWorkItem.lease_token == token,
                        IndexWorkItem.status == "leased",
                    )
                    .values(
                        status="queued",
                        lease_token=None,
                        leased_until=None,
                        attempts=case(
                            (IndexWorkItem.attempts > 0, IndexWorkItem.attempts - 1),
                            else_=0,
                        ),
                        updated_at=datetime.now(UTC),
                    )
                )


async def _process_one(
    session_factory: Any,
    provider: AIProvider,
    experience_id: Any,
    locale: str,
    token: Any,
    embeddings: dict[str, Any],
) -> int:
    """Build and conditionally commit one locale's document. Returns 0 or 1."""
    async with session_factory() as session, session.begin():
        row = (
            await session.execute(
                select(Experience, Destination.name)
                .join(Destination, Destination.id == Experience.destination_id)
                .where(Experience.id == experience_id)
            )
        ).first()
        if row is None:
            await session.execute(
                update(IndexWorkItem)
                .where(
                    IndexWorkItem.experience_id == experience_id,
                    IndexWorkItem.locale == locale,
                )
                .values(status="done", updated_at=datetime.now(UTC))
            )
            return 0

        experience, destination_name = row
        text = await resolved_document_text(session, experience, destination_name, locale)
        fingerprint = index_fingerprint(text, locale)

    try:
        # Until a product is translated, every locale resolves to the same
        # source text. Embedding it once per locale would multiply the cost
        # of a single edit by the number of locales for identical output.
        cache_key = hashlib.sha256(text.encode()).hexdigest()
        if cache_key in embeddings:
            embedding = embeddings[cache_key]
        else:
            embedding = await provider.embed(text)
            embeddings[cache_key] = embedding
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failed embedding must not kill the loop
        logger.exception("Embedding failed for %s/%s", experience_id, locale)
        await _fail(session_factory, experience_id, locale, token, "embedding failed")
        return 0

    async with session_factory() as session, session.begin():
        current = (
            await session.execute(
                select(IndexWorkItem)
                .where(
                    IndexWorkItem.experience_id == experience_id,
                    IndexWorkItem.locale == locale,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if current is None or current.lease_token != token:
            # The lease was taken from us. Whoever holds it now owns the
            # outcome, and our document is theirs to supersede.
            return 0

        # The catalogue decides whether our output is current - not the work
        # item. A work item records what some caller *asked for*, and that can
        # be older than what is committed: reconciliation reads the catalogue,
        # computes a fingerprint, and enqueues afterwards, so a request can
        # arrive describing a version an edit has already superseded. Comparing
        # our output against the work item would then discard a correct
        # document, consume an attempt, and repeat until the item was retired
        # as failed - a failure that never happened, on a document that was
        # right the first time.
        latest = (
            await session.execute(
                select(Experience, Destination.name)
                .join(Destination, Destination.id == Experience.destination_id)
                .where(Experience.id == experience_id)
            )
        ).first()
        if latest is None:
            current.status = "done"
            current.lease_token = None
            current.leased_until = None
            current.updated_at = datetime.now(UTC)
            return 0

        desired = index_fingerprint(
            await resolved_document_text(session, latest[0], latest[1], locale), locale
        )
        if desired != fingerprint:
            # The catalogue genuinely moved while we were embedding. Record
            # what is wanted now and hand the item back without spending the
            # attempt: nothing failed, we were simply overtaken.
            current.fingerprint = desired
            current.status = "queued"
            current.lease_token = None
            current.leased_until = None
            current.attempts = max(current.attempts - 1, 0)
            current.updated_at = datetime.now(UTC)
            return 0

        await upsert_search_document(
            session,
            experience_id=experience_id,
            locale=locale,
            document_text=text,
            embedding=embedding,
        )
        # The item may have asked for an older fingerprint than the one we just
        # satisfied; what is recorded is what the document actually holds.
        current.fingerprint = desired
        current.status = "done"
        current.lease_token = None
        current.leased_until = None
        current.error_detail = None
        current.updated_at = datetime.now(UTC)
        return 1


async def reconcile_index(session: AsyncSession) -> int:
    """Enqueue every locale whose stored document does not match what it should be.

    The outbox only ever hears about *changes*. Nothing tells it that the way
    documents are built has changed - a `DOCUMENT_VERSION` bump, a new embedding
    model - because no catalogue row was touched. Without a reconciliation pass
    such a release applies to new and edited content only, leaves the rest of
    the catalogue indexed the old way, and reports a completely empty backlog
    while doing it.

    Comparing the fingerprint stored *on the document* is what makes this
    possible: work items are transient and a completed one is indistinguishable
    from an absent one, so the document has to carry the record of how it was
    built. Returns the number of locales enqueued.
    """
    total = 0
    after: uuid.UUID | None = None
    while True:
        enqueued, after = await reconcile_page(session, after_id=after)
        total += enqueued
        if after is None:
            return total


async def reconcile_page(
    session: AsyncSession,
    *,
    after_id: uuid.UUID | None = None,
    page_size: int | None = None,
) -> tuple[int, uuid.UUID | None]:
    """One page of reconciliation. Returns what it enqueued and where it stopped.

    Paged because the pass is `experiences x (locales + 1)` queries and the
    whole catalogue in one transaction would hold a snapshot open for as long
    as it takes to walk it - blocking vacuum and, on a catalogue an order of
    magnitude larger, competing with the operator edits it exists to protect.
    Keyset pagination on the primary key rather than OFFSET, so rows inserted
    while the pass runs cannot make it skip or repeat one.

    Returns `None` as the cursor when the catalogue is exhausted.

    `page_size` resolves the module constant at call time rather than binding it
    as a default, so the page size is a knob rather than a value frozen at
    import - which also means a test can actually make the catalogue span more
    than one page.
    """
    size = page_size if page_size is not None else RECONCILE_PAGE_SIZE
    rows = (
        await session.execute(
            select(Experience, Destination.name)
            .join(Destination, Destination.id == Experience.destination_id)
            .where(Experience.id > after_id if after_id is not None else true())
            .order_by(Experience.id)
            .limit(size)
        )
    ).all()
    if not rows:
        return 0, None

    enqueued = 0
    for experience, destination_name in rows:
        stored = {
            locale: fingerprint
            for locale, fingerprint in (
                await session.execute(
                    select(
                        ExperienceSearchDocument.locale,
                        ExperienceSearchDocument.index_fingerprint,
                    ).where(ExperienceSearchDocument.experience_id == experience.id)
                )
            ).all()
        }
        for locale in indexed_locales(experience):
            text = await resolved_document_text(session, experience, destination_name, locale)
            fingerprint = index_fingerprint(text, locale)
            if stored.get(locale) == fingerprint:
                continue
            # Counted from what the write actually changed, not from what we
            # intended, so a reconciliation that collided with pending work does
            # not report repairs it did not make.
            enqueued += await enqueue_reindex(
                session, experience.id, locale, fingerprint, only_if_idle=True
            )
    return enqueued, rows[-1][0].id


async def run_reconcile() -> int:
    """Reconcile the whole catalogue, one committed page at a time.

    A transaction per page rather than one for the run: reconciliation only
    enqueues work, so a pass interrupted halfway has still done real good, and
    the pages it committed are already draining while the rest is walked.
    """
    from app.common.database import session_factory

    if session_factory is None:
        return 0
    total = 0
    after: uuid.UUID | None = None
    while True:
        async with session_factory() as session, session.begin():
            enqueued, after = await reconcile_page(session, after_id=after)
        total += enqueued
        if after is None:
            return total


async def drain_index_queue(*, limit: int | None = 500) -> int:
    """Run the outbox until it is empty, or until `limit` documents are built.

    `limit` bounds the in-application worker so a large backlog cannot occupy
    the request process indefinitely. The CLI passes `None`: a release that
    changes document construction enqueues the whole catalogue, and a repair
    command that stops after 500 of several thousand leaves the job reporting
    success on a partial index.
    """
    from app.assistant.provider import build_ai_provider
    from app.common.database import session_factory

    if session_factory is None:
        return 0
    provider = build_ai_provider()
    total = 0
    while True:
        done = await process_index_work(session_factory, provider, limit=25)
        total += done
        if done == 0:
            return total
        if limit is not None and total >= limit:
            return total


async def run_reindex(
    *, settle_seconds: float = 90.0, poll_seconds: float = 3.0
) -> tuple[int, int, dict[str, int]]:
    """Reconcile, drain, and wait for work this process cannot take itself.

    The repair job does not run alone. The application keeps an in-process
    worker, so at any moment some of the backlog can be leased by a replica that
    is getting on with it perfectly well. A drain stops when *it* can build
    nothing, which is not the same as the queue being empty - so a job that
    failed the moment it saw a non-empty backlog would fail a deployment
    because indexing was working.

    Leased and queued rows are therefore treated as in flight and waited on to a
    bound. A `failed` row is terminal by definition and returns immediately.
    """
    reconciled = await run_reconcile()
    built = 0
    deadline = time.monotonic() + settle_seconds
    while True:
        built += await drain_index_queue(limit=None)
        backlog = await index_backlog()
        if not backlog or "failed" in backlog or time.monotonic() >= deadline:
            return reconciled, built, backlog
        await asyncio.sleep(poll_seconds)


async def index_backlog() -> dict[str, int]:
    """Work items by status, for a command that has to report what it left behind."""
    from app.common.database import session_factory

    if session_factory is None:
        return {}
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IndexWorkItem.status, func.count())
                .where(IndexWorkItem.status != "done")
                .group_by(IndexWorkItem.status)
            )
        ).all()
    return {status: count for status, count in rows}


async def index_worker_loop(interval_seconds: float = 15.0) -> None:
    """Poll the outbox forever.

    Deliberately a loop in the application process rather than a separate
    service: infrastructure runs a single replica, and an operator who fixes a
    title expects search to agree within seconds, not at the next manual
    catalog job. It must never raise - an exception here would kill the task
    silently and reindexing would stop with nothing to show for it.
    """
    import asyncio

    while True:
        try:
            await drain_index_queue()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a broken poll must not stop the loop
            logger.exception("Index worker iteration failed")
        await asyncio.sleep(interval_seconds)
