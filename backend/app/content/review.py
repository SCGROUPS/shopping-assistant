"""The other half of the review split: letting a human actually answer.

§6.5 holds `meeting_point` back from the storefront because a mistranslated
address sends a traveller to the wrong place, and "at least it was in your
language" is not the complaint that generates. The worker implements that
faithfully - it stores the candidate, leaves the published fingerprint alone
and marks the field `needs_review`.

Nothing then read that queue. `needs_review` was a terminal state: no endpoint
listed candidates, none approved one, and `reviewed_by` was never written by
any code path a running system could reach. The `rejected` status in the CHECK
constraint was unreachable. Every locale therefore sat at exactly one field per
experience unpublished, permanently, and the coverage report described that as
a review backlog - which implies somebody could work it.

The spec already states the principle, in step 8, about partner submissions: *a
submission queue no operator can see is a queue that fills up*. It applies
identically here and nobody applied it.

Approval is conditional on the fingerprint the reviewer was shown. A reviewer
reads the source text and the candidate side by side and judges the pair; if
the source changes between render and click, that judgement is about text that
no longer exists, and publishing on it would put an approved-looking address
under a product that has since moved. The stale case is not an error - it is a
re-render with the new source, because the reviewer's answer was correct about
the question they were asked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.field_policy import ENTITY_EXPERIENCE
from app.common.models import Experience, TranslationField
from app.content.enqueue import mark_manual_translation
from app.content.publish import publish_experience_translation


class ReviewConflict(Exception):
    """The candidate moved between being shown and being decided.

    Carries the reason so the caller can tell a reviewer *why* their decision
    did not apply. "Conflict" alone invites the reviewer to click again, which
    is the wrong instinct when the source text changed underneath them.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ReviewItem:
    """One candidate, with everything needed to judge it and nothing else."""

    experience_id: uuid.UUID
    title: str
    field: str
    locale: str
    source_text: str
    source_language: str
    candidate_value: str
    candidate_fingerprint: str
    desired_fingerprint: str
    generation: int
    updated_at: datetime

    @property
    def answers_current_source(self) -> bool:
        """Whether the candidate was produced for the source text as it stands.

        False means the source was edited after the machine translated it. The
        candidate is then a translation of text nobody will read, and approving
        it publishes an address for a version of the product that is gone.
        """
        return self.candidate_fingerprint == self.desired_fingerprint

    def as_dict(self) -> dict[str, object]:
        return {
            "experience_id": str(self.experience_id),
            "title": self.title,
            "field": self.field,
            "locale": self.locale,
            "source_text": self.source_text,
            "source_language": self.source_language,
            "candidate_value": self.candidate_value,
            "candidate_fingerprint": self.candidate_fingerprint,
            "generation": self.generation,
            "answers_current_source": self.answers_current_source,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class ReviewQueue:
    items: list[ReviewItem] = dataclass_field(default_factory=list)
    total: int = 0
    by_locale: dict[str, int] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [item.as_dict() for item in self.items],
            "total": self.total,
            "by_locale": self.by_locale,
        }


async def review_queue(
    session: AsyncSession,
    *,
    locale: str | None = None,
    field: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ReviewQueue:
    """Candidates awaiting a human decision, oldest first.

    Oldest first because a queue worked newest-first starves its own tail, and
    the tail here is the product nobody has edited in months - which is exactly
    the one whose translation has been unpublished the longest.

    `total` counts the whole filtered queue, not the page. A reviewer who
    approves ten of fifty needs to see forty remaining; a page-sized count would
    tell them they were done.
    """
    conditions = [
        TranslationField.entity_type == ENTITY_EXPERIENCE,
        TranslationField.status == "needs_review",
        TranslationField.candidate_value.is_not(None),
    ]
    if locale is not None:
        conditions.append(TranslationField.locale == locale)
    if field is not None:
        conditions.append(TranslationField.field == field)

    total = await session.scalar(
        select(func.count()).select_from(TranslationField).where(*conditions)
    )

    grouped = await session.execute(
        select(TranslationField.locale, func.count())
        .where(*conditions)
        .group_by(TranslationField.locale)
    )
    by_locale = {row_locale: int(count) for row_locale, count in grouped.all()}

    rows = await session.execute(
        select(TranslationField, Experience)
        .join(Experience, Experience.id == TranslationField.entity_id)
        .where(*conditions)
        .order_by(TranslationField.updated_at.asc(), TranslationField.entity_id.asc())
        .limit(limit)
        .offset(offset)
    )

    items = [
        ReviewItem(
            experience_id=state.entity_id,
            title=experience.title,
            field=state.field,
            locale=state.locale,
            source_text=getattr(experience, state.field, "") or "",
            source_language=experience.source_language,
            candidate_value=state.candidate_value or "",
            candidate_fingerprint=state.candidate_fingerprint or "",
            desired_fingerprint=state.desired_fingerprint,
            generation=state.generation,
            updated_at=state.updated_at,
        )
        for state, experience in rows.all()
    ]
    return ReviewQueue(items=items, total=int(total or 0), by_locale=by_locale)


async def _locked_candidate(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    expected_fingerprint: str,
    expected_generation: int,
) -> TranslationField:
    """Take the row lock first, then check every assumption the decision made.

    The lock is taken before the checks, not after. Checking first and locking
    second is a read-modify-write with a window in it, and the window is exactly
    long enough for a worker to replace the candidate with a newer one - which
    the approval would then publish while reporting the fingerprint the reviewer
    saw.

    The generation is checked as well as the fingerprint, because a fingerprint
    is a hash of the source text and text can come back. An operator who edits
    a meeting point and reverts it takes the field F0 -> F1 -> F0; the second
    F0 enqueues a fresh job, the model produces a *different* Vietnamese
    sentence, and it lands under a fingerprint identical to the one the
    reviewer approved. Fingerprints alone cannot see that, and the reviewer's
    click would publish a string they never read. The generation counts edits
    rather than hashing them, so it does not come back.
    """
    row = await session.get(
        TranslationField,
        (ENTITY_EXPERIENCE, experience_id, field, locale),
        with_for_update=True,
    )
    if row is None:
        raise ReviewConflict("not_found", f"no translation state for {field}/{locale}")
    if row.status != "needs_review":
        raise ReviewConflict(
            "already_decided",
            f"field is '{row.status}', not awaiting review",
        )
    if row.generation != expected_generation:
        raise ReviewConflict(
            "source_changed",
            "the source text was edited after this candidate was shown",
        )
    if (row.candidate_fingerprint or "") != expected_fingerprint:
        raise ReviewConflict(
            "candidate_replaced",
            "the candidate changed after it was shown; re-read it before deciding",
        )
    if row.candidate_fingerprint != row.desired_fingerprint:
        raise ReviewConflict(
            "source_changed",
            "the source text changed after this candidate was produced",
        )
    return row


async def approve_candidate(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    reviewer: str,
    expected_fingerprint: str,
    expected_generation: int,
) -> str:
    """Publish a held candidate on a human's authority.

    Goes through the same publish path as the worker's auto-publish branch, so
    an approved field cannot end up visible-but-unsearchable while an
    auto-published one is fine. Returns the published text, so the caller can
    record in the audit trail what was actually made visible rather than what
    was requested.
    """
    row = await _locked_candidate(
        session,
        experience_id=experience_id,
        field=field,
        locale=locale,
        expected_fingerprint=expected_fingerprint,
        expected_generation=expected_generation,
    )
    value = row.candidate_value or ""

    await publish_experience_translation(
        session,
        experience_id=experience_id,
        field=field,
        locale=locale,
        value=value,
    )

    row.status = "current"
    row.published_fingerprint = row.candidate_fingerprint
    row.reviewed_by = reviewer
    row.updated_at = datetime.now(UTC)
    row.candidate_value = None
    row.candidate_fingerprint = None
    return value


async def reject_candidate(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    reviewer: str,
    expected_fingerprint: str,
    expected_generation: int,
) -> str:
    """Refuse a candidate, leaving the field on its fallback.

    Deliberately does not re-enqueue. A machine that produced a wrong address
    once will produce it again from the same source and the same recipe, so
    automatic retry would put the same text back in the queue forever. Clearing
    a rejection is a source edit or a human translation - both of which bump the
    generation and enqueue naturally.
    """
    row = await _locked_candidate(
        session,
        experience_id=experience_id,
        field=field,
        locale=locale,
        expected_fingerprint=expected_fingerprint,
        expected_generation=expected_generation,
    )
    rejected = row.candidate_value or ""
    row.status = "rejected"
    row.reviewed_by = reviewer
    row.updated_at = datetime.now(UTC)
    row.candidate_value = None
    row.candidate_fingerprint = None
    return rejected


async def edit_translation(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    reviewer: str,
    value: str,
) -> None:
    """Publish a translation a human wrote, and hand them ownership of it.

    This is what makes rejection recoverable. Rejecting deliberately does not
    re-enqueue - the same source and the same recipe produce the same wrong
    address forever - so without a way to type the right one, a single
    rejection would leave the field on English until somebody happened to edit
    the source text. The reviewer who knows it is wrong is generally the person
    who knows what it should say.

    `mark_manual_translation` sets provenance to manual, which is checked by
    the worker's conditional update: a machine translation already in flight
    loses its commit rather than overwriting this.
    """
    if not value.strip():
        raise ReviewConflict("empty", "a translation cannot be empty")

    await mark_manual_translation(
        session,
        experience_id=experience_id,
        field=field,
        locale=locale,
        editor=reviewer,
    )
    # After `mark_manual_translation`, which takes the row lock. Every path
    # takes translation_fields before experience_translations, and two orders
    # is a deadlock under concurrent review.
    await publish_experience_translation(
        session,
        experience_id=experience_id,
        field=field,
        locale=locale,
        value=value,
    )
