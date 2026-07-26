"""Operator API.

Mounted under `/api/v1/admin`. Every route declares the capability it needs
rather than a role, so adding a role is a change to `auth.ROLES` and not to
every endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.admin import catalog_ops, settings_ops
from app.admin.audit import record
from app.admin.auth import ROLES as ROLE_CAPABILITIES
from app.admin.auth import (
    CatalogPublish,
    CatalogWrite,
    Configure,
    ManageOperators,
    Merchandise,
    ReadAccess,
    generate_key,
    hash_key,
    key_prefix,
    new_salt,
)
from app.common.database import session_factory
from app.common.errors import ApiError
from app.common.models import Operator
from app.common.runtime_config import get_value
from app.content import coverage as translation_coverage
from app.content import review

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class StatusChange(BaseModel):
    status: str
    note: str = ""


class OperatorCreate(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    role: str


@router.get("/me")
async def whoami(principal: ReadAccess) -> dict[str, Any]:
    return {
        "email": principal.email,
        "name": principal.name,
        "role": principal.role,
        "capabilities": sorted(ROLE_CAPABILITIES.get(principal.role, set())),
    }


@router.get("/overview")
async def overview(principal: ReadAccess) -> dict[str, Any]:
    return await catalog_ops.review_summary()


@router.get("/experiences")
async def list_experiences(
    principal: ReadAccess,
    q: str | None = None,
    status: str | None = None,
    needs_review: bool | None = None,
    supplier: str | None = None,
    destination: str | None = None,
    promoted: bool | None = None,
    incomplete: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    return await catalog_ops.list_experiences(
        catalog_ops.CatalogQuery(
            q=q,
            status=status,
            needs_review=needs_review,
            supplier=supplier,
            destination=destination,
            promoted=promoted,
            incomplete=incomplete,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/experiences/{experience_id}")
async def experience_detail(experience_id: UUID, principal: ReadAccess) -> dict[str, Any]:
    return await catalog_ops.get_experience(experience_id)


@router.patch("/experiences/{experience_id}")
async def update_experience(
    experience_id: UUID,
    principal: CatalogWrite,
    changes: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await catalog_ops.update_experience(experience_id, changes, principal)


@router.delete("/experiences/{experience_id}/overrides/{field}")
async def clear_override(
    experience_id: UUID, field: str, principal: CatalogWrite
) -> dict[str, Any]:
    return await catalog_ops.clear_override(experience_id, field, principal)


@router.post("/experiences/{experience_id}/status")
async def change_status(
    experience_id: UUID, principal: CatalogPublish, body: StatusChange
) -> dict[str, Any]:
    return await catalog_ops.set_status(experience_id, body.status, principal, body.note)


@router.post("/experiences/{experience_id}/merchandising")
async def change_merchandising(
    experience_id: UUID,
    principal: Merchandise,
    changes: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    ceiling = float(await get_value("max_merchandising_boost"))
    return await catalog_ops.set_merchandising(experience_id, changes, principal, ceiling)


@router.get("/audit")
async def audit_trail(
    principal: ReadAccess,
    entity_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    return {"entries": await catalog_ops.recent_audit(limit, entity_id)}


@router.get("/translations")
async def translation_coverage_report(
    principal: ReadAccess,
    locale: str | None = None,
) -> dict[str, Any]:
    """Per-locale readiness, measured by resolving as a shopper would.

    This is the gate for enabling a locale (spec 12, step 6). It reports
    `fallback` separately from `missing` because they need different work: a
    fallback is a translation that has not happened yet, and a blank is content
    an operator never wrote. Enabling a locale on the strength of a number that
    merged the two would ship a storefront of empty meeting points.
    """
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "coverage-unavailable")
    wanted = (locale,) if locale else None
    async with session_factory() as session:
        reports = await translation_coverage.locale_coverage(session, wanted)
        spend = await translation_coverage.spend_today(session)
    return {"locales": [item.as_dict() for item in reports], "spend": spend}


class ReviewDecision(BaseModel):
    """What the reviewer saw, so the server can refuse if it has changed.

    Both are required and neither has a default. A default would let a caller
    omit the check and have the server publish whatever is currently there,
    which is the exact race the fields exist to prevent - and omitting a field
    is a much easier mistake to make than sending a wrong one.
    """

    experience_id: UUID
    field: str
    locale: str
    candidate_fingerprint: str
    generation: int


class TranslationEdit(BaseModel):
    experience_id: UUID
    field: str
    locale: str
    value: str = Field(min_length=1, max_length=4000)
    # Required, like the decision fields. A human translating a meeting point
    # is translating the source on their screen; if it moved while they typed,
    # they have faithfully translated directions to the wrong place - and
    # publishing it as `manual` then protects it from machine correction.
    generation: int


@router.get("/translations/review")
async def translation_review_queue(
    principal: ReadAccess,
    locale: str | None = None,
    field: str | None = None,
    state: Annotated[str, Query(pattern="^(needs_review|rejected)$")] = "needs_review",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Machine translations held back from the storefront awaiting a human.

    §6.5 holds `meeting_point` because a mistranslated address sends a
    traveller to the wrong place. The worker implemented that faithfully and
    nothing ever read the queue, so `needs_review` was a terminal state and
    every locale sat permanently at one unpublished field per experience while
    the coverage report described it as a backlog somebody could work.
    """
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "review-unavailable")
    async with session_factory() as session:
        queue = await review.review_queue(
            session, locale=locale, field=field, state=state, limit=limit, offset=offset
        )
    return queue.as_dict()


async def _decide(
    decision: ReviewDecision,
    principal: Any,
    *,
    approve: bool,
) -> dict[str, Any]:
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "review-unavailable")
    act = review.approve_candidate if approve else review.reject_candidate
    async with session_factory() as session:
        try:
            value = await act(
                session,
                experience_id=decision.experience_id,
                field=decision.field,
                locale=decision.locale,
                reviewer=principal.email,
                expected_fingerprint=decision.candidate_fingerprint,
                expected_generation=decision.generation,
            )
        except review.ReviewConflict as conflict:
            # 409, not 422: the request was well-formed and was correct when it
            # was rendered. The reviewer needs to re-read, not to fix a typo.
            raise ApiError(409, "Review conflict", conflict.detail, conflict.reason) from conflict
        record(
            session,
            principal,
            action="translation.approve" if approve else "translation.reject",
            entity_type="experience",
            entity_id=decision.experience_id,
            summary=f"{decision.field} ({decision.locale})",
            # The text itself, because "approved" without it cannot answer the
            # only question an audit of a wrong address ever asks.
            changes={"field": decision.field, "locale": decision.locale, "value": value},
        )
        await session.commit()
    return {"status": "current" if approve else "rejected", "value": value}


@router.post("/translations/review/approve")
async def approve_translation(
    principal: CatalogPublish, decision: ReviewDecision
) -> dict[str, Any]:
    """Publish one candidate. Deliberately one, and deliberately not a list.

    Bulk approval is not compatible with `requires_review=True`: one click over
    379 distinct sets of directions is not review, it is the auto-publish this
    field is specifically excluded from, with an operator's name attached to
    it.
    """
    return await _decide(decision, principal, approve=True)


@router.post("/translations/review/reject")
async def reject_translation(principal: CatalogPublish, decision: ReviewDecision) -> dict[str, Any]:
    return await _decide(decision, principal, approve=False)


@router.post("/translations/edit")
async def edit_translation(principal: CatalogPublish, body: TranslationEdit) -> dict[str, Any]:
    """Publish a translation the reviewer wrote themselves.

    This is what stops a rejection being permanent. Rejecting does not
    re-enqueue - the same source and recipe produce the same wrong address
    forever - so without this the field would sit on English until somebody
    happened to edit the source text.
    """
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "review-unavailable")
    async with session_factory() as session:
        try:
            await review.edit_translation(
                session,
                experience_id=body.experience_id,
                field=body.field,
                locale=body.locale,
                reviewer=principal.email,
                value=body.value,
                expected_generation=body.generation,
            )
        except review.ReviewConflict as conflict:
            raise ApiError(409, "Review conflict", conflict.detail, conflict.reason) from conflict
        except ValueError as error:
            raise ApiError(404, "Not found", str(error), "experience-not-found") from error
        record(
            session,
            principal,
            action="translation.edit",
            entity_type="experience",
            entity_id=body.experience_id,
            summary=f"{body.field} ({body.locale})",
            changes={"field": body.field, "locale": body.locale, "value": body.value},
        )
        await session.commit()
    return {"status": "current", "provenance": "manual"}


@router.get("/settings")
async def read_settings(principal: ReadAccess) -> dict[str, Any]:
    return await settings_ops.describe()


@router.put("/settings/{key}")
async def write_setting(
    key: str, principal: Configure, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    if "value" not in body:
        raise ApiError(422, "Invalid setting", "Body must contain 'value'", "invalid-setting")
    return await settings_ops.update(key, body["value"], principal)


@router.delete("/settings/{key}")
async def reset_setting(key: str, principal: Configure) -> dict[str, Any]:
    return await settings_ops.reset(key, principal)


@router.get("/operators")
async def list_operators(principal: ManageOperators) -> dict[str, Any]:
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "operators-unavailable")
    async with session_factory() as session:
        rows = (await session.scalars(select(Operator).order_by(Operator.email))).all()
    return {
        "operators": [
            {
                "id": str(row.id),
                "email": row.email,
                "name": row.name,
                "role": row.role,
                "active": row.active,
                "key_prefix": row.key_prefix,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in rows
        ]
    }


@router.post("/operators", status_code=201)
async def create_operator(principal: ManageOperators, body: OperatorCreate) -> dict[str, Any]:
    """Issue a new operator credential.

    The key is returned exactly once. Storing it would make the audit trail
    forgeable by anyone with database access, which defeats the point of
    having one.
    """
    if body.role not in ROLE_CAPABILITIES:
        raise ApiError(
            422,
            "Invalid role",
            f"Role must be one of {', '.join(sorted(ROLE_CAPABILITIES))}",
            "invalid-role",
        )
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "operators-unavailable")
    key = generate_key()
    salt = new_salt()
    async with session_factory() as session, session.begin():
        existing = await session.scalar(select(Operator).where(Operator.email == body.email))
        if existing is not None:
            raise ApiError(
                409, "Conflict", "An operator with that email already exists", "operator-exists"
            )
        operator = Operator(
            email=body.email,
            name=body.name,
            role=body.role,
            key_prefix=key_prefix(key),
            key_hash=hash_key(key, salt),
            key_salt=salt,
            active=True,
        )
        session.add(operator)
        record(
            session,
            principal,
            action="operator.create",
            entity_type="operator",
            entity_id=body.email,
            summary=f"Created {body.role} operator {body.email}",
            changes={"role": {"from": None, "to": body.role}},
        )
    return {"email": body.email, "role": body.role, "api_key": key}


@router.post("/operators/{operator_id}/disable")
async def disable_operator(operator_id: UUID, principal: ManageOperators) -> dict[str, Any]:
    if session_factory is None:
        raise ApiError(503, "Unavailable", "Database is not configured", "operators-unavailable")
    async with session_factory() as session, session.begin():
        operator = await session.get(Operator, operator_id)
        if operator is None:
            raise ApiError(404, "Not found", "No such operator", "operator-not-found")
        if operator.email == principal.email:
            raise ApiError(
                422,
                "Invalid change",
                "You cannot disable your own account",
                "operator-self-disable",
            )
        operator.active = False
        record(
            session,
            principal,
            action="operator.disable",
            entity_type="operator",
            entity_id=operator.email,
            summary=f"Disabled operator {operator.email}",
            changes={"active": {"from": True, "to": False}},
        )
    return {"status": "disabled"}
