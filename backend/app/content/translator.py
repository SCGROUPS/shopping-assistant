"""The translation worker: lease a job, ask the model, commit only if still wanted.

The commit is the whole point of this module, and it is one transaction that
does three things or none of them:

1. a conditional `UPDATE translation_fields` that names the generation, the
   desired fingerprint, the pending status, a non-manual provenance *and* a
   lease token bound to this exact target;
2. a column-specific `ON CONFLICT DO UPDATE` write to the wide translation row;
3. the job's own completion, and the reindex enqueue for the locale.

If the conditional update matches zero rows, the world moved while the model was
thinking and everything above is rolled back. That is the only correct outcome:
the alternative is a slow machine translation landing on top of a human's fresh
correction, which no amount of retrying fixes because the human's text is gone.

The wide-table write must be column-specific. An ORM whole-row flush of
`experience_translations` writes all four columns, so two workers finishing
`title` and `description` for the same locale in the same second would each
write the other's column back to the value they loaded — silently losing one.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.indexing import enqueue_experience_reindex
from app.common.config import get_settings
from app.common.llm_cost import BudgetExceeded
from app.common.models import (
    Experience,
    ExperienceTranslation,
    TranslationField,
    TranslationGlossary,
    TranslationJob,
)
from app.content.enqueue import (
    ENTITY_EXPERIENCE,
    EXPERIENCE_FIELDS,
)
from app.content.enqueue import (
    requires_review as field_requires_review,
)

logger = logging.getLogger(__name__)

# Long enough that a slow model call finishes inside it, short enough that a
# worker killed mid-flight frees the job within a deploy cycle.
LEASE_SECONDS = 300
MAX_ATTEMPTS = 5

_WIDE_COLUMNS = {field: getattr(ExperienceTranslation, field) for field in EXPERIENCE_FIELDS}


@dataclass(frozen=True)
class Leased:
    job_id: uuid.UUID
    lease_token: uuid.UUID
    entity_id: uuid.UUID
    field: str
    locale: str
    fingerprint: str
    generation: int
    attempts: int
    source_text: str
    source_language: str


# Failures that will recur no matter how often they are retried. Reviving these
# every two hours spends money to be wrong on a schedule; they stay terminal
# until the source, the recipe or an operator changes.
class GlossaryViolation(RuntimeError):
    """The model translated a term it was told to leave alone.

    Raised rather than repaired: silently substituting the term back produces a
    sentence with the right words in a grammar built around different ones.
    """


# Only these are revived. An allowlist, not a denylist, because the denylist got
# the default wrong in the safe-looking direction: everything unrecognised was
# transient, so a bad deployment name, an expired credential, a permissions
# error or a plain AttributeError in our own code would be retried every two
# hours forever, paying each time and never surfacing that a human is needed.
#
# The question a transient classification answers is "would doing exactly this
# again, unchanged, plausibly work?" For a timeout or a 503, yes. For anything
# we do not recognise, we cannot know, and the honest answer to "I do not know"
# is to stop and be visible in the failed queue rather than to spend money on a
# guess in a loop.
TRANSIENT_FAILURES: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,  # covers the socket-level errors the SDK re-raises
)

# Retryable by status rather than by type: the SDK raises one class for most
# HTTP failures, so the code is what separates "come back later" from "this
# request is wrong and will stay wrong".
TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def classify(exc: Exception) -> str:
    """Decide whether retrying this exception unchanged could ever work.

    `GlossaryViolation` is checked first and explicitly: five attempts have
    already failed the same way, and the sixth is the same source under the same
    recipe, so it will omit the same term. Same for malformed structured output
    - once is flakiness, five times is a prompt or a schema that needs changing,
    and neither changes on its own.
    """
    if isinstance(exc, GlossaryViolation | ValueError):
        return "permanent"
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return "transient" if status in TRANSIENT_STATUS else "permanent"
    if isinstance(exc, TRANSIENT_FAILURES):
        return "transient"
    return "permanent"


async def lease_jobs(session: AsyncSession, *, limit: int) -> list[Leased]:
    """Claim up to `limit` jobs, skipping any another worker holds.

    `SKIP LOCKED` rather than `NOWAIT`: two workers running concurrently is the
    normal case, and the second should take different work rather than fail.
    """
    # The database clock, not this process's. A worker whose container clock runs
    # a few minutes fast would otherwise declare its own live lease expired and
    # hand its job to a second worker, and both would translate and commit.
    now = func.now()
    token = uuid.uuid4()

    claimable = (
        select(TranslationJob.id)
        .where(
            TranslationJob.status.in_(("queued", "leased")),
            TranslationJob.attempts < MAX_ATTEMPTS,
            # A leased row is claimable only once its lease has expired, which
            # is how a killed worker's job returns to the pool without anybody
            # having to notice it died.
            (TranslationJob.leased_until.is_(None)) | (TranslationJob.leased_until < now),
        )
        .order_by(TranslationJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    claimed = (
        await session.execute(
            update(TranslationJob)
            .where(TranslationJob.id.in_(claimable))
            .values(
                status="leased",
                lease_token=token,
                leased_until=now + text(f"interval '{LEASE_SECONDS} seconds'"),
                attempts=TranslationJob.attempts + 1,
            )
            .returning(
                TranslationJob.id,
                TranslationJob.entity_id,
                TranslationJob.field,
                TranslationJob.locale,
                TranslationJob.fingerprint,
                TranslationJob.generation,
                TranslationJob.attempts,
            )
        )
    ).all()

    leased: list[Leased] = []
    for row in claimed:
        experience = await session.get(Experience, row.entity_id)
        if experience is None:
            # The listing was deleted under us. Nothing to translate and nothing
            # to retry; drop the job rather than burn five attempts on it.
            await session.execute(
                update(TranslationJob)
                .where(TranslationJob.id == row.id)
                .values(status="cancelled", lease_token=None, leased_until=None)
            )
            continue
        leased.append(
            Leased(
                job_id=row.id,
                lease_token=token,
                entity_id=row.entity_id,
                field=row.field,
                locale=row.locale,
                fingerprint=row.fingerprint,
                generation=row.generation,
                attempts=row.attempts,
                source_text=getattr(experience, row.field, "") or "",
                source_language=experience.source_language,
            )
        )
    return leased


async def load_glossary(session: AsyncSession, locale: str) -> list[TranslationGlossary]:
    return list(
        (
            await session.scalars(
                select(TranslationGlossary).where(TranslationGlossary.target_locale == locale)
            )
        ).all()
    )


def verify_glossary(source: str, translated: str, terms: list[TranslationGlossary]) -> None:
    """Check the output for terms the model was told not to translate.

    Only terms the *source* actually contains are required in the output.
    Requiring every term in the locale's glossary would fail the translation of
    "Japanese Bridge" because it does not mention "Hoi An" - a glossary would
    then get less usable with every term added to it, which is precisely
    backwards.

    Instructing a model is a request; checking the output is a guarantee. A term
    that went missing fails the job, which retries and eventually parks it for a
    human - far better than shipping "Ancient Town" where the brand is "Hoi An".
    """
    folded_source = source.casefold()
    folded_output = translated.casefold()
    missing = [
        term.term
        for term in terms
        if term.do_not_translate
        and term.term.casefold() in folded_source
        and term.term.casefold() not in folded_output
    ]
    if missing:
        raise GlossaryViolation(f"missing untranslated terms: {', '.join(sorted(missing))}")


def _still_ours(job: Leased):
    """Every job transition names the lease it was made under.

    Matching on `id` alone is not enough. A worker whose lease expired is still
    running: it finishes late, writes `status='queued', lease_token=NULL`, and
    silently unlatches the lease a *second* worker is currently holding - which
    then has its own completion rejected, or worse, has a third worker start the
    same job while it is still in flight. The token is what makes a late worker
    harmless instead of destructive.
    """
    return and_(
        TranslationJob.id == job.job_id,
        TranslationJob.status == "leased",
        TranslationJob.lease_token == job.lease_token,
    )


async def commit_translation(
    session: AsyncSession,
    job: Leased,
    *,
    translated: str,
    requires_review: bool,
) -> bool:
    """Publish, but only if this job still answers what the field wants.

    Returns False when the world moved — the caller treats that as success for
    the job (there is nothing left to do) but must not write anything.
    """
    conditions = and_(
        TranslationField.entity_type == ENTITY_EXPERIENCE,
        TranslationField.entity_id == job.entity_id,
        TranslationField.field == job.field,
        TranslationField.locale == job.locale,
        # Everything a stale worker could be wrong about, named explicitly.
        TranslationField.generation == job.generation,
        TranslationField.desired_fingerprint == job.fingerprint,
        TranslationField.status == "pending",
        TranslationField.provenance != "manual",
        # Bound to *this* target, so holding a lease on some other job's row
        # cannot authorise this write.
        TranslationJob.id == job.job_id,
        TranslationJob.lease_token == job.lease_token,
        TranslationJob.entity_id == TranslationField.entity_id,
        TranslationJob.field == TranslationField.field,
        TranslationJob.locale == TranslationField.locale,
    )

    if requires_review:
        # Held back from the storefront. The candidate is stored, the published
        # fingerprint is untouched, and the field stays visibly not-current.
        values = {
            "candidate_value": translated,
            "candidate_fingerprint": job.fingerprint,
            "status": "needs_review",
        }
    else:
        values = {
            "published_fingerprint": job.fingerprint,
            "status": "current",
            "candidate_value": None,
            "candidate_fingerprint": None,
        }

    updated = await session.execute(
        update(TranslationField)
        .where(conditions)
        .values(**values, updated_at=datetime.now(UTC))
        .returning(TranslationField.entity_id)
    )
    if updated.scalar_one_or_none() is None:
        logger.info(
            "translation.superseded",
            extra={
                "entity_id": str(job.entity_id),
                "field": job.field,
                "locale": job.locale,
                "generation": job.generation,
            },
        )
        return False

    if not requires_review:
        column = _WIDE_COLUMNS[job.field]
        await session.execute(
            pg_insert(ExperienceTranslation)
            .values(
                experience_id=job.entity_id,
                locale=job.locale,
                **{job.field: translated},
            )
            .on_conflict_do_update(
                index_elements=[
                    ExperienceTranslation.experience_id,
                    ExperienceTranslation.locale,
                ],
                # One column. Naming all four would have two workers write each
                # other's column back to whatever they loaded.
                set_={column.key: translated, "updated_at": datetime.now(UTC)},
            )
        )
        # The document for this locale is now built from different text, so it
        # has to be rebuilt. Same transaction: a published translation that is
        # not searchable is a translation nobody will ever read.
        await enqueue_experience_reindex(session, job.entity_id, locales=[job.locale])

    await session.execute(
        update(TranslationJob)
        .where(_still_ours(job))
        .values(status="done", lease_token=None, leased_until=None, error_detail=None)
    )
    return True


async def fail_job(
    session: AsyncSession, job: Leased, detail: str, kind: str = "transient"
) -> bool:
    """Release the lease and record why, parking the job at the attempt cap.

    Returns True when this was the last attempt. The field is only marked
    `failed` if the *job* transition actually bit, so a worker whose lease has
    already been taken away cannot condemn a field another worker is about to
    translate successfully.
    """
    terminal = job.attempts >= MAX_ATTEMPTS
    fenced = await session.execute(
        update(TranslationJob)
        .where(_still_ours(job))
        .values(
            status="failed" if terminal else "queued",
            lease_token=None,
            leased_until=None,
            error_detail=detail[:2000],
            failure_kind=kind,
        )
        .returning(TranslationJob.id)
    )
    if fenced.scalar_one_or_none() is None:
        return False

    if terminal:
        await session.execute(
            update(TranslationField)
            .where(
                TranslationField.entity_type == ENTITY_EXPERIENCE,
                TranslationField.entity_id == job.entity_id,
                TranslationField.field == job.field,
                TranslationField.locale == job.locale,
                TranslationField.generation == job.generation,
            )
            .values(status="failed")
        )
    return terminal


async def sweep_stalled_leases(session: AsyncSession) -> int:
    """Park jobs that died holding their final attempt.

    `lease_jobs` refuses anything at the attempt cap, so a process killed on its
    fifth attempt leaves a row that is `leased`, expired, and invisible to every
    query in this module - not reclaimable, not failed, not reported. It waits
    there forever looking like work in progress.
    """
    stalled = await session.execute(
        update(TranslationJob)
        .where(
            TranslationJob.status == "leased",
            TranslationJob.leased_until < func.now(),
            TranslationJob.attempts >= MAX_ATTEMPTS,
        )
        .values(
            status="failed",
            lease_token=None,
            leased_until=None,
            error_detail="lease expired on the final attempt",
            # Whatever the last attempt concluded is preserved: a job that had
            # already been classified transient stays revivable, and one that
            # never got far enough to be classified stays NULL, which means
            # unknown and is not revived.
            failure_kind=TranslationJob.failure_kind,
        )
        .returning(
            TranslationJob.entity_id,
            TranslationJob.field,
            TranslationJob.locale,
            TranslationJob.fingerprint,
            TranslationJob.generation,
        )
    )
    targets = stalled.all()
    if not targets:
        return 0

    # `fail_job` marks the field too, and sweeping has to as well. Leaving the
    # field `pending` behind a `failed` job produces a state nothing can serve
    # and nothing can fix: no job is claimable, and re-enqueueing is a no-op
    # because the desired fingerprint has not moved.
    await session.execute(
        update(TranslationField)
        .where(
            TranslationField.entity_type == ENTITY_EXPERIENCE,
            tuple_(
                TranslationField.entity_id,
                TranslationField.field,
                TranslationField.locale,
                TranslationField.desired_fingerprint,
                TranslationField.generation,
            ).in_([tuple(row) for row in targets]),
        )
        .values(status="failed")
    )
    return len(targets)


async def revive_failed_jobs(session: AsyncSession) -> int:
    """Give up-to-date failed jobs their attempts back.

    Five failures against a provider outage is not evidence that a field cannot
    be translated, but it is terminal: the field is `failed`, and re-enqueueing
    cannot help because the desired fingerprint has not moved and the uniqueness
    key still holds the dead job. Without an explicit revival the outage parks
    those translations permanently.

    Only revives jobs that still describe what the field wants. A failed job for
    a fingerprint the catalogue has moved past is genuinely dead work.
    """
    live = (
        select(TranslationField.entity_id)
        .where(
            TranslationField.entity_type == TranslationJob.entity_type,
            TranslationField.entity_id == TranslationJob.entity_id,
            TranslationField.field == TranslationJob.field,
            TranslationField.locale == TranslationJob.locale,
            TranslationField.desired_fingerprint == TranslationJob.fingerprint,
            TranslationField.generation == TranslationJob.generation,
        )
        .exists()
    )
    revived = await session.execute(
        update(TranslationJob)
        .where(
            TranslationJob.status == "failed",
            # NULL is "written by a release that did not classify failures".
            # Left alone rather than assumed safe: an unclassified failure that
            # is actually deterministic would otherwise be retried forever.
            TranslationJob.failure_kind == "transient",
            live,
        )
        .values(status="queued", attempts=0, lease_token=None, leased_until=None)
        .returning(
            TranslationJob.entity_id,
            TranslationJob.field,
            TranslationJob.locale,
            TranslationJob.fingerprint,
            TranslationJob.generation,
        )
    )
    targets = revived.all()
    if not targets:
        return 0

    # Exactly the fields whose job came back, not every failed field on the
    # experience. A field whose failed job is for a fingerprint the catalogue
    # has moved past is dead work; flipping it to `pending` would leave it
    # pending forever with no job to service it, and no longer visible as
    # failed to whoever might have fixed it.
    await session.execute(
        update(TranslationField)
        .where(
            TranslationField.entity_type == ENTITY_EXPERIENCE,
            TranslationField.status == "failed",
            # Fingerprint and generation included, so a field that failed at a
            # newer generation while this ran is not woken by an older job.
            tuple_(
                TranslationField.entity_id,
                TranslationField.field,
                TranslationField.locale,
                TranslationField.desired_fingerprint,
                TranslationField.generation,
            ).in_([tuple(row) for row in targets]),
        )
        .values(status="pending")
    )
    return len(targets)


async def _run_one(session_factory, translator, job: Leased, *, hold_all: bool) -> str:
    """Translate and publish one field. Returns the outcome to count it under.

    Every failure path is inside this function, including a failure of the
    *commit* itself. Letting a database error out of here would abandon the rest
    of the batch still leased, so the whole batch would go quiet for five
    minutes and then retry - having consumed an attempt each for a fault that
    had nothing to do with them.
    """
    settings = get_settings()
    try:
        async with session_factory() as session:
            terms = await load_glossary(session, job.locale)
        translated = await asyncio.wait_for(
            translator(job=job, glossary=terms),
            timeout=settings.translation_timeout_seconds,
        )
        verify_glossary(job.source_text, translated, terms)
    except BudgetExceeded as exc:
        # Not a failure of this job: the work is fine, we simply declined to pay
        # for it right now. Consuming an attempt would mean a spending ceiling
        # could permanently park five batches' worth of translations.
        return await _defer(session_factory, job, str(exc))
    except Exception as exc:  # noqa: BLE001 - every failure is one retry
        return await _record_failure(session_factory, job, exc)

    try:
        async with session_factory() as session:
            published = await commit_translation(
                session,
                job,
                translated=translated,
                requires_review=hold_all or field_requires_review(job.field),
            )
            if not published:
                await session.execute(
                    update(TranslationJob)
                    .where(_still_ours(job))
                    .values(status="superseded", lease_token=None, leased_until=None)
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        return await _record_failure(session_factory, job, exc)

    return "published" if published else "superseded"


async def _defer(session_factory, job: Leased, detail: str) -> str:
    """Hand the job back untouched, including its attempt."""
    async with session_factory() as session:
        handed_back = await session.execute(
            update(TranslationJob)
            .where(_still_ours(job))
            .values(
                status="queued",
                lease_token=None,
                leased_until=None,
                attempts=TranslationJob.attempts - 1,
                error_detail=detail[:2000],
            )
            .returning(TranslationJob.id)
        )
        await session.commit()
    # If the fence missed, this worker no longer owned the job and deferring is
    # not what happened to it. Reporting `deferred` would stop the drain loop
    # over a job somebody else is already running.
    return "deferred" if handed_back.scalar_one_or_none() is not None else "superseded"


async def _record_failure(session_factory, job: Leased, exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    kind = classify(exc)
    try:
        async with session_factory() as session:
            terminal = await fail_job(session, job, detail, kind=kind)
            await session.commit()
    except Exception:  # noqa: BLE001
        # Even recording the failure failed. The lease still expires, and
        # `sweep_stalled_leases` is what stops the job hiding forever if this
        # was its last attempt.
        logger.exception("translation.failure_unrecorded", extra={"job_id": str(job.job_id)})
        return "failed"
    logger.warning(
        "translation.failed",
        extra={
            "job_id": str(job.job_id),
            "terminal": terminal,
            "kind": kind,
            "detail": detail,
        },
    )
    return "failed" if terminal else "retrying"


async def drain(
    session_factory,
    translator,
    *,
    limit: int | None = None,
    hold_all: bool = False,
    concurrency: int | None = None,
) -> dict[str, int]:
    """Work the queue once. Each job commits in its own transaction.

    One transaction per job, not one for the batch: a glossary violation on the
    twelfth job must not roll back eleven good translations.

    Jobs run concurrently because the batch is dominated by provider latency,
    not by database work. Sequentially, a first backfill of ten thousand fields
    at two seconds each is closer to six hours than to one - which is not a
    performance complaint, it is the difference between a job that finishes and
    a job that gets killed by its timeout every night.
    """
    settings = get_settings()
    batch = limit if limit is not None else settings.translation_batch
    lanes = concurrency if concurrency is not None else settings.translation_concurrency

    async with session_factory() as session:
        await sweep_stalled_leases(session)
        jobs = await lease_jobs(session, limit=batch)
        await session.commit()

    counts = {
        "leased": len(jobs),
        "published": 0,
        "superseded": 0,
        "retrying": 0,
        "failed": 0,
        "deferred": 0,
    }
    if not jobs:
        return counts

    gate = asyncio.Semaphore(max(1, lanes))

    async def guarded(job: Leased) -> str:
        async with gate:
            return await _run_one(session_factory, translator, job, hold_all=hold_all)

    for outcome in await asyncio.gather(*(guarded(job) for job in jobs)):
        counts[outcome] += 1
    return counts
