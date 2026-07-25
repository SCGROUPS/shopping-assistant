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
