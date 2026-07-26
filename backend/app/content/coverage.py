"""How much of the catalogue a shopper in a given locale would actually read.

This exists to answer one operational question — *can we enable Korean yet?* —
and it answers it by asking the resolver, not by writing a second SQL predicate
that means roughly the same thing. A predicate like ``status = 'current' AND
published_fingerprint = desired_fingerprint`` looks equivalent and is not: it
does not know about the fallback chain, it does not know that `meeting_point`
refuses stale text, and it does not know that a published row with an empty
string is not a translation. Each of those differences would make coverage
report a readiness the storefront does not deliver, and no test of either side
alone would fail.

So coverage runs the real resolution and counts what came back. It costs two
queries per batch of experiences per locale, on an operator endpoint nobody
calls in a loop, and in exchange it cannot drift away from the pages it
describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import get_settings
from app.common.locales import SUPPORTED_LOCALES
from app.common.models import (
    Experience,
    TranslationField,
    TranslationJob,
    TranslationSpend,
)
from app.common.resolution import TRANSLATED_FIELDS, resolve_experience_text

# Experiences are resolved in batches rather than all at once: the resolver
# builds an IN list per batch, and a single IN list holding every experience id
# in the catalogue is a query plan nobody chose.
BATCH = 200


@dataclass
class FieldCoverage:
    translated: int = 0
    fallback: int = 0
    stale: int = 0
    missing: int = 0

    @property
    def total(self) -> int:
        return self.translated + self.fallback + self.missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "translated": self.translated,
            "fallback": self.fallback,
            "stale": self.stale,
            "missing": self.missing,
            "percent": _percent(self.translated, self.total),
        }


@dataclass
class LocaleCoverage:
    locale: str
    enabled: bool
    experiences: int = 0
    fields: dict[str, FieldCoverage] = dataclass_field(default_factory=dict)
    queued: int = 0
    failed: int = 0
    needs_review: int = 0

    def totals(self) -> FieldCoverage:
        combined = FieldCoverage()
        for one in self.fields.values():
            combined.translated += one.translated
            combined.fallback += one.fallback
            combined.stale += one.stale
            combined.missing += one.missing
        return combined

    def as_dict(self) -> dict[str, Any]:
        return {
            "locale": self.locale,
            "enabled": self.enabled,
            "experiences": self.experiences,
            "queued": self.queued,
            "failed": self.failed,
            "needs_review": self.needs_review,
            "overall": self.totals().as_dict(),
            "fields": {name: one.as_dict() for name, one in sorted(self.fields.items())},
        }


def _percent(part: int, whole: int) -> float:
    """Percentage, with an empty catalogue reported as 0 rather than 100.

    `0 of 0` is arithmetically undefined and operationally dangerous: a locale
    nobody has enqueued a single job for would otherwise report full coverage
    and pass the gate that is supposed to stop exactly that.
    """
    if whole <= 0:
        return 0.0
    return round(part * 100 / whole, 1)


async def locale_coverage(
    session: AsyncSession, locales: tuple[str, ...] | None = None
) -> list[LocaleCoverage]:
    settings = get_settings()
    enabled = set(settings.enabled_locales)
    wanted = tuple(locales) if locales is not None else SUPPORTED_LOCALES

    ids = list(
        (
            await session.scalars(
                select(Experience.id)
                .where(Experience.status == "PUBLISHED")
                .order_by(Experience.id)
            )
        ).all()
    )

    reports = {
        locale: LocaleCoverage(locale=locale, enabled=locale in enabled, experiences=len(ids))
        for locale in wanted
    }
    for report in reports.values():
        report.fields = {name: FieldCoverage() for name in TRANSLATED_FIELDS}

    for start in range(0, len(ids), BATCH):
        batch = ids[start : start + BATCH]
        experiences = list(
            (await session.scalars(select(Experience).where(Experience.id.in_(batch)))).all()
        )
        for locale, report in reports.items():
            resolved = await resolve_experience_text(session, experiences, locale)
            for fields in resolved.values():
                for name, value in fields.items():
                    tally = report.fields[name]
                    if not value.value.strip():
                        # An experience with no meeting point at all is not a
                        # translation failure, but it is not coverage either,
                        # and counting it as translated would let a catalogue
                        # of blanks report itself ready.
                        tally.missing += 1
                    elif value.is_fallback:
                        tally.fallback += 1
                    else:
                        tally.translated += 1
                    if value.stale:
                        tally.stale += 1

    await _attach_queue_state(session, reports)
    return [reports[locale] for locale in wanted]


async def _attach_queue_state(session: AsyncSession, reports: dict[str, LocaleCoverage]) -> None:
    rows = await session.execute(
        select(TranslationJob.locale, TranslationJob.status, func.count())
        .where(TranslationJob.status.in_(("queued", "leased", "failed")))
        .group_by(TranslationJob.locale, TranslationJob.status)
    )
    for locale, status, count in rows.all():
        report = reports.get(locale)
        if report is None:
            continue
        if status == "failed":
            report.failed += int(count)
        else:
            # A leased job is queued work that a worker currently holds. To an
            # operator asking "is there outstanding work", the distinction
            # between waiting and in-flight is noise.
            report.queued += int(count)

    # Held candidates are counted from the field table rather than the job
    # table, because the job that produced one is *done*. A queue depth of zero
    # with fifty meeting points awaiting a reviewer is not an idle system, and
    # reading only the queue would report it as one.
    held = await session.execute(
        select(TranslationField.locale, func.count())
        .where(
            TranslationField.entity_type == "experience",
            TranslationField.status == "needs_review",
        )
        .group_by(TranslationField.locale)
    )
    for locale, count in held.all():
        report = reports.get(locale)
        if report is not None:
            report.needs_review = int(count)


async def spend_today(session: AsyncSession) -> dict[str, Any]:
    """Today's spend, on the database's UTC day rather than the runner's.

    `date.today()` is the application container's local date, and the ledger is
    written with `(now() AT TIME ZONE 'utc')::date`. Any container not on UTC
    reports the wrong row for part of every day - most visibly reading zero
    spend while the budget is in fact nearly exhausted, which is the reading
    that would prompt someone to raise concurrency.
    """
    settings = get_settings()
    today = await session.scalar(text("SELECT (now() AT TIME ZONE 'utc')::date"))
    amount = await session.scalar(
        select(TranslationSpend.amount).where(TranslationSpend.day == today)
    )
    spent = float(amount or 0)
    budget = float(settings.translation_daily_budget)
    return {
        "day": today.isoformat() if today else None,
        "spent_usd": round(spent, 4),
        "budget_usd": budget,
        "percent": _percent(int(spent * 10000), int(budget * 10000)),
    }
