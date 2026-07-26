"""Entry points for the translation pipeline, shaped for the catalogue job.

Enqueue and drain are separate commands because they fail for different
reasons and at different rates. Enqueue is pure database work that either
succeeds or leaves nothing behind; drain spends money and can be rate limited
by a provider. Folding them into one command means a provider outage stops the
catalogue noticing that content changed.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.common.database import session_factory
from app.common.models import Experience, TranslationJob
from app.content.enqueue import enqueue_experience_translations
from app.content.model_translator import make_translator
from app.content.translator import drain, revive_failed_jobs

logger = logging.getLogger(__name__)


async def enqueue_all(*, locales: list[str] | None = None) -> dict[str, int]:
    """Walk the catalogue and bring every experience's translation state current.

    One transaction per experience. A catalogue-wide transaction would hold
    locks on every row for the length of the walk, and a failure at the last
    record would throw away the work for all the others.
    """
    if session_factory is None:
        raise RuntimeError("translation requires a database; DATABASE_URL is unset")

    async with session_factory() as session:
        ids = list((await session.scalars(select(Experience.id))).all())

    totals = {"experiences": len(ids), "enqueued": 0}
    for experience_id in ids:
        async with session_factory() as session:
            totals["enqueued"] += await enqueue_experience_translations(
                session, experience_id, locales=locales
            )
            await session.commit()
    return totals


async def drain_translations(
    *,
    limit: int | None = None,
    hold_all: bool = False,
    revive: bool = False,
) -> dict[str, int]:
    """Drain the queue until it stops making progress.

    `hold_all` forces every field into the review queue. It is not the normal
    path: which fields need a human is a property of the field (§6.5), not of
    the environment the worker happens to run in.
    """
    from app.assistant.provider import build_ai_provider

    if session_factory is None:
        raise RuntimeError("translation requires a database; DATABASE_URL is unset")

    provider = build_ai_provider()
    if not hasattr(provider, "client"):
        # The demo provider has no model behind it. Refusing is better than
        # writing "[vi] ..." placeholders into the storefront and having
        # somebody discover them in production.
        raise RuntimeError("translation requires a configured Azure OpenAI provider")

    totals = {
        "revived": 0,
        "leased": 0,
        "published": 0,
        "superseded": 0,
        "retrying": 0,
        "failed": 0,
        "deferred": 0,
        "throttled": 0,
    }
    translator = make_translator(provider, session_factory)

    if revive:
        async with session_factory() as session:
            totals["revived"] = await revive_failed_jobs(session)
            await session.commit()

    while True:
        counts = await drain(session_factory, translator, limit=limit, hold_all=hold_all)
        for key, value in counts.items():
            totals[key] += value
        if counts["leased"] == 0:
            break
        if limit is not None:
            # An explicit limit means "do this much work", not "work until done".
            break
        if counts["deferred"]:
            # The budget is spent. Stopping here is the whole point of deferring
            # rather than failing: the next run picks up exactly where this one
            # left off, with every attempt still available.
            logger.warning("translation.budget_reached", extra={"counts": counts})
            break
        if counts["throttled"]:
            # Throttling is not "no progress", it is the provider pacing us, and
            # `_run_one` has already waited out the interval it asked for. The
            # attempts were handed back, so going round again is free; stopping
            # here would leave a 10K TPM deployment translating one batch every
            # two hours.
            logger.info("translation.throttled", extra={"counts": counts})
            continue
        if counts["published"] == 0 and counts["superseded"] == 0:
            # Nothing moved forward. Draining again would lease the same jobs
            # back the moment their leases expire and burn the rest of their
            # attempts against whatever is broken, so stop and report instead.
            logger.warning("translation.no_progress", extra={"counts": counts})
            break
    return totals


async def backlog(session=None) -> dict[str, int]:
    """What is left, by status. A job that finishes is not the same as a queue
    that is empty: another replica may hold the rest.
    """
    if session_factory is None:
        raise RuntimeError("translation requires a database; DATABASE_URL is unset")
    async with session_factory() as owned:
        rows = (
            await owned.execute(
                select(TranslationJob.status, func.count())
                .where(TranslationJob.status.notin_(("done", "cancelled", "superseded")))
                .group_by(TranslationJob.status)
            )
        ).all()
    return {status: count for status, count in rows}
