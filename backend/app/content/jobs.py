"""Entry points for the translation pipeline, shaped for the catalogue job.

Enqueue and drain are separate commands because they fail for different
reasons and at different rates. Enqueue is pure database work that either
succeeds or leaves nothing behind; drain spends money and can be rate limited
by a provider. Folding them into one command means a provider outage stops the
catalogue noticing that content changed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.common.config import get_settings
from app.common.database import session_factory
from app.common.models import Experience
from app.content.enqueue import enqueue_experience_translations
from app.content.model_translator import make_translator
from app.content.translator import drain

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
    *, limit: int | None = None, requires_review: bool | None = None
) -> dict[str, int]:
    from app.assistant.provider import build_ai_provider

    if session_factory is None:
        raise RuntimeError("translation requires a database; DATABASE_URL is unset")

    settings = get_settings()
    provider = build_ai_provider()
    if not hasattr(provider, "client"):
        # The demo provider has no model behind it. Refusing is better than
        # writing "[vi] ..." placeholders into the storefront and having
        # somebody discover them in production.
        raise RuntimeError("translation requires a configured Azure OpenAI provider")

    review = requires_review if requires_review is not None else settings.app_env != "development"
    totals = {"leased": 0, "published": 0, "superseded": 0, "failed": 0}
    translator = make_translator(provider)

    while True:
        counts = await drain(
            session_factory, translator, limit=limit, requires_review=review
        )
        for key, value in counts.items():
            totals[key] += value
        if counts["leased"] == 0:
            break
        if limit is not None:
            # An explicit limit means "do this much work", not "work until done".
            break
    return totals
