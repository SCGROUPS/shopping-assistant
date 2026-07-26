"""Catalog operations: the surface a human uses to run the inventory.

The storefront reads a catalogue; somebody has to be able to correct it, hold
it back, publish it and promote it. That work happened nowhere before this
module: the only admin endpoint validated an uploaded file and discarded it.

Two rules shape everything here.

**Every mutation is attributed and audited**, in the same transaction as the
change, so the log cannot disagree with the database.

**Every edit is recorded as an override.** The importer rewrites supplier-owned
fields on every run, so without `experience_overrides` an operator's correction
would survive until the next deploy and no longer. Editing and importing have
to be able to coexist, or one of them is pointless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin import audit
from app.admin.auth import Principal
from app.catalog.indexing import enqueue_experience_reindex
from app.catalog.publish_gate import publish_blockers, unpublishable_now
from app.common.database import session_factory
from app.common.errors import ApiError
from app.common.models import (
    AuditLog,
    Destination,
    Experience,
    ExperienceMedia,
    ExperienceOption,
    ExperienceOverride,
    OptionPrice,
    Supplier,
)
from app.content.enqueue import enqueue_experience_translations

# Statuses an operator may set. DRAFT and PENDING_REVIEW are invisible to the
# storefront; ARCHIVED is how inventory retires without being deleted, because
# deleting it would orphan the bookings that reference it.
STATUSES = ("DRAFT", "PENDING_REVIEW", "PUBLISHED", "ARCHIVED")

# Fields the catalog editor may change. Anything absent here either belongs to
# commerce (prices, availability) or is derived (embeddings, search documents).
EDITABLE_FIELDS = {
    "title": str,
    "short_description": str,
    "description": str,
    "category": str,
    "subcategories": list,
    "interest_tags": list,
    "indoor_outdoor": str,
    "duration_minutes": int,
    "meeting_point": str,
    "languages": list,
    "accessibility_features": list,
    "minimum_age": (int, type(None)),
    "family_friendly": bool,
    "instant_confirmation": bool,
    "mobile_voucher": bool,
}

MERCHANDISING_FIELDS = {
    "boost": (int, float),
    "pinned": bool,
    "suppressed": bool,
    "promotion_label": str,
    "promotion_starts_at": (str, type(None)),
    "promotion_ends_at": (str, type(None)),
}

INDOOR_OUTDOOR = {"indoor", "outdoor", "mixed"}


def _require_db() -> Any:
    if session_factory is None:
        raise ApiError(
            503,
            "Unavailable",
            "Catalog operations require the database; it is not configured",
            "catalog-ops-unavailable",
        )
    return session_factory


@dataclass(frozen=True)
class CatalogQuery:
    q: str | None = None
    status: str | None = None
    needs_review: bool | None = None
    supplier: str | None = None
    destination: str | None = None
    promoted: bool | None = None
    incomplete: bool | None = None
    page: int = 1
    page_size: int = 25


def _summary_row(
    experience: Experience,
    supplier: str,
    destination: str,
    overridden: list[str] | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    return {
        # Which fields a human has taken ownership of. Shown in the list
        # because it is the difference between "the supplier says this" and
        # "someone here decided this", and re-import respects the difference.
        "overridden_fields": overridden or [],
        "price": price,
        "id": str(experience.id),
        "external_id": experience.external_id,
        "title": experience.title,
        "slug": experience.slug,
        "status": experience.status,
        "needs_review": experience.needs_review,
        "review_note": experience.review_note,
        "category": experience.category,
        "destination": destination,
        "supplier": supplier,
        "rating": float(experience.rating),
        "review_count": experience.review_count,
        "boost": float(experience.boost),
        "pinned": experience.pinned,
        "suppressed": experience.suppressed,
        "promotion_label": experience.promotion_label,
        "promotion_starts_at": _iso(experience.promotion_starts_at),
        "promotion_ends_at": _iso(experience.promotion_ends_at),
        "updated_at": _iso(experience.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _filtered(statement: Select, query: CatalogQuery) -> Select:
    if query.status:
        statement = statement.where(Experience.status == query.status)
    if query.needs_review is not None:
        statement = statement.where(Experience.needs_review.is_(query.needs_review))
    if query.supplier:
        statement = statement.where(Supplier.external_id == query.supplier)
    if query.destination:
        statement = statement.where(func.lower(Destination.name) == query.destination.casefold())
    if query.promoted is not None:
        promoted = or_(
            Experience.pinned.is_(True),
            Experience.suppressed.is_(True),
            Experience.boost != 1,
        )
        statement = statement.where(promoted if query.promoted else ~promoted)
    if query.incomplete is not None:
        # Finds the listings that are live and should not be. The gate refuses
        # the transition; nothing re-examines a listing that already made it
        # through, so without this the catalogue keeps whatever damage it had.
        incomplete = unpublishable_now()
        statement = statement.where(incomplete if query.incomplete else ~incomplete)
    if query.q:
        pattern = f"%{query.q.strip()}%"
        statement = statement.where(
            or_(
                Experience.title.ilike(pattern),
                Experience.external_id.ilike(pattern),
                Experience.slug.ilike(pattern),
            )
        )
    return statement


async def list_experiences(query: CatalogQuery) -> dict[str, Any]:
    """Operator-facing catalogue search.

    Deliberately not the storefront's search: an operator needs to find the
    listing that is *broken*, which means unpublished and suppressed inventory
    has to be visible here precisely because it is invisible there.
    """
    factory = _require_db()
    base = (
        select(Experience, Supplier.name, Destination.name)
        .join(Supplier, Supplier.id == Experience.supplier_id)
        .join(Destination, Destination.id == Experience.destination_id)
    )
    async with factory() as session:
        counted = _filtered(
            select(func.count())
            .select_from(Experience)
            .join(Supplier, Supplier.id == Experience.supplier_id)
            .join(Destination, Destination.id == Experience.destination_id),
            query,
        )
        total = await session.scalar(counted) or 0
        page_size = max(1, min(query.page_size, 100))
        rows = (
            await session.execute(
                _filtered(base, query)
                # Unreviewed work first: the queue is the reason this screen
                # exists, and burying it under 300 healthy listings would make
                # the console a browser rather than a worklist.
                .order_by(Experience.needs_review.desc(), Experience.updated_at.desc())
                .limit(page_size)
                .offset((max(query.page, 1) - 1) * page_size)
            )
        ).all()
        identifiers = [experience.id for experience, _, _ in rows]
        # Two small batched lookups rather than N+1 per row: the console lists
        # a hundred at a time and an operator notices a slow table.
        overrides = {
            override.experience_id: list(override.fields or [])
            for override in await session.scalars(
                select(ExperienceOverride).where(ExperienceOverride.experience_id.in_(identifiers))
            )
        }
        prices = await _lead_prices(session, identifiers)
        return {
            "total": total,
            "page": max(query.page, 1),
            "page_size": page_size,
            "items": [
                _summary_row(
                    experience,
                    supplier,
                    destination,
                    overrides.get(experience.id),
                    prices.get(experience.id),
                )
                for experience, supplier, destination in rows
            ],
        }


async def _lead_prices(session: AsyncSession, identifiers: list[UUID]) -> dict[UUID, float]:
    """The cheapest adult price per experience: what the storefront shows."""
    if not identifiers:
        return {}
    rows = (
        await session.execute(
            select(ExperienceOption.experience_id, func.min(OptionPrice.amount))
            .join(OptionPrice, OptionPrice.option_id == ExperienceOption.id)
            .where(
                ExperienceOption.experience_id.in_(identifiers),
                # Seeded rows say "adult"; the importer echoes whatever the
                # supplier sends. Neither casing is wrong, so match on both.
                func.lower(OptionPrice.participant_type) == "adult",
            )
            .group_by(ExperienceOption.experience_id)
        )
    ).all()
    return {identifier: float(amount) for identifier, amount in rows if amount is not None}


async def get_experience(experience_id: UUID) -> dict[str, Any]:
    factory = _require_db()
    async with factory() as session:
        experience = await _load(session, experience_id)
        supplier = await session.get(Supplier, experience.supplier_id)
        destination = await session.get(Destination, experience.destination_id)
        override = await session.get(ExperienceOverride, experience.id)
        options = (
            await session.scalars(
                select(ExperienceOption)
                .where(ExperienceOption.experience_id == experience.id)
                .options(selectinload(ExperienceOption.prices))
                .order_by(ExperienceOption.external_id)
            )
        ).all()
        media = (
            await session.scalars(
                select(ExperienceMedia).where(ExperienceMedia.experience_id == experience.id)
            )
        ).all()
        prices = await _lead_prices(session, [experience.id])
        detail = _summary_row(
            experience,
            supplier.name if supplier else "",
            destination.name if destination else "",
            list(override.fields or []) if override else [],
            prices.get(experience.id),
        )
        detail.update(
            {
                "description": experience.description,
                "short_description": experience.short_description,
                "subcategories": list(experience.subcategories),
                "interest_tags": list(experience.interest_tags),
                "indoor_outdoor": experience.indoor_outdoor,
                "duration_minutes": experience.duration_minutes,
                "meeting_point": experience.meeting_point,
                "languages": list(experience.languages),
                "accessibility_features": list(experience.accessibility_features),
                "minimum_age": experience.minimum_age,
                "family_friendly": experience.family_friendly,
                "instant_confirmation": experience.instant_confirmation,
                "mobile_voucher": experience.mobile_voucher,
                "published_at": _iso(experience.published_at),
                # Shown in the console so an operator can see which fields the
                # supplier no longer controls, and why their feed appears to be
                # "wrong" for this listing.
                "overridden_fields": sorted(override.fields) if override else [],
                "image_urls": [item.url for item in media],
                "options": [
                    {
                        "id": str(option.id),
                        "external_id": option.external_id,
                        "name": option.name,
                        "active": option.active,
                        "max_party_size": option.max_party_size,
                        "free_cancellation_hours": option.free_cancellation_hours,
                        "prices": [
                            {
                                "participant_type": price.participant_type,
                                "currency": price.currency,
                                "amount": float(price.amount),
                            }
                            for price in option.prices
                        ],
                    }
                    for option in options
                ],
                # Computed on read rather than stored, because every one of
                # these can be invalidated by something other than an edit to
                # this row - deactivating an option, a reindex falling behind,
                # a supplier being merged. A stored flag would be wrong within
                # a day and nobody would know which day.
                "publish_blockers": [
                    item.as_dict() for item in await publish_blockers(session, experience)
                ],
                "source_language": experience.source_language,
            }
        )
        return detail


async def _load(session: AsyncSession, experience_id: UUID) -> Experience:
    experience = await session.get(Experience, experience_id)
    if experience is None:
        raise ApiError(404, "Not found", "No such experience", "experience-not-found")
    return experience


def _validate(changes: dict[str, Any], allowed: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for field, value in changes.items():
        expected = allowed.get(field)
        if expected is None:
            raise ApiError(422, "Invalid edit", f"'{field}' is not editable", "invalid-edit")
        if not isinstance(value, expected):
            raise ApiError(422, "Invalid edit", f"'{field}' has the wrong type", "invalid-edit")
        cleaned[field] = value
    if "indoor_outdoor" in cleaned and cleaned["indoor_outdoor"] not in INDOOR_OUTDOOR:
        raise ApiError(
            422,
            "Invalid edit",
            f"indoor_outdoor must be one of {', '.join(sorted(INDOOR_OUTDOOR))}",
            "invalid-edit",
        )
    if "duration_minutes" in cleaned and not 1 <= cleaned["duration_minutes"] <= 20_160:
        raise ApiError(
            422, "Invalid edit", "duration_minutes must be 1 minute to 14 days", "invalid-edit"
        )
    return cleaned


def _snapshot(experience: Experience, fields: dict[str, Any]) -> dict[str, Any]:
    def readable(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    return {field: readable(getattr(experience, field)) for field in fields}


async def update_experience(
    experience_id: UUID, changes: dict[str, Any], principal: Principal
) -> dict[str, Any]:
    """Apply an operator's correction and make it stick.

    Recording the edited field names in `experience_overrides` is the load
    bearing part: it is what stops the next supplier import from quietly
    undoing the work.
    """
    cleaned = _validate(changes, EDITABLE_FIELDS)
    if not cleaned:
        raise ApiError(422, "Invalid edit", "No changes supplied", "invalid-edit")
    factory = _require_db()
    async with factory() as session, session.begin():
        experience = await _load(session, experience_id)
        before = _snapshot(experience, cleaned)
        for field, value in cleaned.items():
            setattr(experience, field, value)
        after = _snapshot(experience, cleaned)

        override = await session.get(ExperienceOverride, experience.id)
        if override is None:
            override = ExperienceOverride(experience_id=experience.id, fields={})
            session.add(override)
        override.fields = {**dict(override.fields), **{field: True for field in cleaned}}
        override.updated_by = principal.email

        audit.record(
            session,
            principal,
            action="catalog.update",
            entity_type="experience",
            entity_id=experience.id,
            summary=f"Edited {', '.join(sorted(cleaned))} on '{experience.title}'",
            changes=audit.diff(before, after),
        )

        # Without this the console can change what a product says and nothing
        # about what search matches: only the importer ever wrote a search
        # document, and a manually authored record is one no importer may
        # touch. Enqueued in this transaction so the intent cannot outlive a
        # rollback, nor be lost if the process dies before a follow-up call.
        await enqueue_experience_reindex(session, experience.id)
        # And the translations, for the same reason and in the same
        # transaction. Reindexing alone rebuilds the *English* document from
        # the new title while seven locales keep serving translations that
        # still describe the old one - and go on describing it forever, because
        # staleness is derived from a fingerprint only this call updates.
        await enqueue_experience_translations(session, experience.id)
    return await get_experience(experience_id)


async def clear_override(experience_id: UUID, field: str, principal: Principal) -> dict[str, Any]:
    """Hand a field back to the supplier feed.

    Without this an override is a one-way door: the moment an operator fixes a
    typo, that field is frozen against every future import, so a later supplier
    correction can never land. Clearing does not restore the old value - the
    next import does that - because the supplier is the source of truth we are
    deferring back to.
    """
    if field not in EDITABLE_FIELDS and field not in {"status"}:
        raise ApiError(422, "Invalid edit", f"{field!r} is not an override", "invalid-edit")
    factory = _require_db()
    async with factory() as session, session.begin():
        experience = await _load(session, experience_id)
        override = await session.get(ExperienceOverride, experience.id)
        held = dict(override.fields) if override is not None else {}
        if field not in held:
            raise ApiError(404, "No override", f"{field!r} is not overridden", "override-not-found")
        del held[field]
        assert override is not None
        override.fields = held
        override.updated_by = principal.email

        audit.record(
            session,
            principal,
            action="catalog.clear_override",
            entity_type="experience",
            entity_id=experience.id,
            summary=f"Released {field} on '{experience.title}' back to the supplier feed",
            changes={field: {"from": "operator-managed", "to": "supplier-managed"}},
        )
        await enqueue_experience_reindex(session, experience.id)
        # Releasing an override hands the field back to the supplier's text,
        # which is a source change like any other.
        await enqueue_experience_translations(session, experience.id)
    return await get_experience(experience_id)


async def set_status(
    experience_id: UUID, status: str, principal: Principal, note: str = ""
) -> dict[str, Any]:
    """Publish, hold, or retire a listing.

    Status is recorded as an override because a human ruling on a listing must
    outrank the classifier's opinion on the next import.
    """
    if status not in STATUSES:
        raise ApiError(
            422, "Invalid status", f"Status must be one of {', '.join(STATUSES)}", "invalid-status"
        )
    factory = _require_db()
    async with factory() as session, session.begin():
        experience = await _load(session, experience_id)
        if status == "PUBLISHED":
            blockers = await publish_blockers(session, experience)
            if blockers:
                raise ApiError(
                    409,
                    "Cannot publish",
                    "Cannot publish: " + "; ".join(item.message for item in blockers),
                    "publish-blocked",
                    # Itemised, because an operator told only the first problem
                    # fixes it, resubmits, and is told the next one.
                    details={"blockers": [item.as_dict() for item in blockers]},
                )
        before = {"status": experience.status, "needs_review": experience.needs_review}
        experience.status = status
        experience.review_note = note
        if status != "PENDING_REVIEW":
            experience.needs_review = False
        if status == "PUBLISHED" and experience.published_at is None:
            experience.published_at = datetime.now(UTC)

        override = await session.get(ExperienceOverride, experience.id)
        if override is None:
            override = ExperienceOverride(experience_id=experience.id, fields={})
            session.add(override)
        override.fields = {**dict(override.fields), "status": True}
        override.updated_by = principal.email

        audit.record(
            session,
            principal,
            action=f"catalog.{status.casefold()}",
            entity_type="experience",
            entity_id=experience.id,
            summary=f"Set '{experience.title}' to {status}" + (f": {note}" if note else ""),
            changes=audit.diff(before, {"status": status, "needs_review": experience.needs_review}),
        )
    return await get_experience(experience_id)


def _parse_moment(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ApiError(
            422, "Invalid promotion", f"'{field}' is not a valid date-time", "invalid-promotion"
        ) from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def set_merchandising(
    experience_id: UUID, changes: dict[str, Any], principal: Principal, ceiling: float
) -> dict[str, Any]:
    """Campaign controls, bounded by the configured ceiling.

    The bound is enforced here rather than only at read time so an operator is
    told their campaign is out of range while they can still change it, instead
    of it being silently clamped and appearing not to work.
    """
    cleaned = _validate(changes, MERCHANDISING_FIELDS)
    if not cleaned:
        raise ApiError(422, "Invalid change", "No changes supplied", "invalid-merchandising")
    if "boost" in cleaned and not 1 / ceiling <= float(cleaned["boost"]) <= ceiling:
        raise ApiError(
            422,
            "Invalid boost",
            f"Boost must be between {1 / ceiling:.2f} and {ceiling:.2f}",
            "invalid-merchandising",
        )
    starts = _parse_moment(cleaned.get("promotion_starts_at"), "promotion_starts_at")
    ends = _parse_moment(cleaned.get("promotion_ends_at"), "promotion_ends_at")
    if starts and ends and ends <= starts:
        raise ApiError(
            422, "Invalid window", "The promotion ends before it starts", "invalid-merchandising"
        )

    factory = _require_db()
    tracked = set(cleaned)
    async with factory() as session, session.begin():
        experience = await _load(session, experience_id)
        before = _snapshot(experience, dict.fromkeys(tracked))
        for field, value in cleaned.items():
            if field == "boost":
                experience.boost = Decimal(str(round(float(value), 2)))
            elif field == "promotion_starts_at":
                experience.promotion_starts_at = starts
            elif field == "promotion_ends_at":
                experience.promotion_ends_at = ends
            else:
                setattr(experience, field, value)
        after = _snapshot(experience, dict.fromkeys(tracked))
        audit.record(
            session,
            principal,
            action="catalog.merchandise",
            entity_type="experience",
            entity_id=experience.id,
            summary=f"Merchandising change on '{experience.title}'",
            changes=audit.diff(before, after),
        )
    return await get_experience(experience_id)


async def review_summary() -> dict[str, Any]:
    """What the operator should look at first."""
    factory = _require_db()
    async with factory() as session:
        counts = (
            await session.execute(
                select(Experience.status, func.count()).group_by(Experience.status)
            )
        ).all()
        pending = await session.scalar(
            select(func.count()).select_from(Experience).where(Experience.needs_review.is_(True))
        )
        promoted = await session.scalar(
            select(func.count())
            .select_from(Experience)
            .where(
                or_(
                    Experience.pinned.is_(True),
                    Experience.suppressed.is_(True),
                    Experience.boost != 1,
                )
            )
        )
        return {
            "by_status": {status: count for status, count in counts},
            "needs_review": pending or 0,
            "merchandised": promoted or 0,
        }


async def recent_audit(limit: int = 50, entity_id: str | None = None) -> list[dict[str, Any]]:
    factory = _require_db()
    async with factory() as session:
        statement = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(min(limit, 200))
        if entity_id:
            statement = statement.where(AuditLog.entity_id == entity_id)
        rows = (await session.scalars(statement)).all()
        return [
            {
                "id": str(row.id),
                "occurred_at": _iso(row.occurred_at),
                "operator": row.operator_email,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "summary": row.summary,
                "changes": row.changes,
            }
            for row in rows
        ]
