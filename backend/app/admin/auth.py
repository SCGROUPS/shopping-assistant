"""Operator authentication and authorisation.

The previous control was a request header: `X-Admin-Role: catalog_manager`
made any caller a catalog manager, and the analytics endpoint had no control at
all. Operators are now rows with a hashed credential, so a change can be
attributed to a person and refused to everyone else.

Keys are shown once at creation and stored as a salted scrypt hash. The
prefix is stored in clear and indexed, so verification is one indexed lookup
plus one hash rather than a scan over every operator.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select

from app.common.config import get_settings
from app.common.database import session_factory
from app.common.errors import ApiError
from app.common.models import Operator

KEY_PREFIX_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
# Demo mode has no database and no real data to protect, but the console still
# has to be usable and testable. A production deployment leaves
# `admin_bootstrap_key` unset unless it is deliberately configured as a secret.
DEMO_BOOTSTRAP_KEY = "demo-admin-key"


def _bootstrap_key() -> str | None:
    settings = get_settings()
    if settings.admin_bootstrap_key:
        return settings.admin_bootstrap_key
    return DEMO_BOOTSTRAP_KEY if settings.demo_mode else None

# Roles are ordered by breadth, and each implies everything a narrower role can
# do. Keeping this as data means an endpoint declares the capability it needs
# rather than enumerating the roles that happen to have it today.
ROLES: dict[str, set[str]] = {
    "analyst": {"read"},
    "merchandiser": {"read", "merchandise", "configure"},
    "catalog_manager": {"read", "merchandise", "catalog:write", "catalog:publish"},
    "admin": {
        "read",
        "merchandise",
        "configure",
        "catalog:write",
        "catalog:publish",
        "operators:manage",
    },
}


@dataclass(frozen=True)
class Principal:
    """The authenticated operator behind a request."""

    id: object
    email: str
    name: str
    role: str

    def can(self, capability: str) -> bool:
        return capability in ROLES.get(self.role, set())


def generate_key() -> str:
    """A new operator key. Returned once, never recoverable."""
    return f"vk_{secrets.token_urlsafe(32)}"


def hash_key(key: str, salt: str) -> str:
    return hashlib.scrypt(key.encode(), salt=salt.encode(), **_SCRYPT).hex()


def new_salt() -> str:
    return secrets.token_hex(16)


def key_prefix(key: str) -> str:
    return key[:KEY_PREFIX_LENGTH]


def verify_key(key: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key, salt), expected_hash)


async def authenticate(api_key: str | None) -> Principal:
    if not api_key:
        raise ApiError(401, "Unauthorized", "An operator API key is required", "unauthorized")

    bootstrap = _bootstrap_key()
    if bootstrap and hmac.compare_digest(api_key, bootstrap):
        # Somebody has to be able to act before the first operator row exists,
        # and demo mode has no database to hold one. This key is a secret in a
        # real environment and is how the first real operators get created.
        settings = get_settings()
        return Principal(
            id="bootstrap",
            email=settings.admin_bootstrap_email,
            name="Bootstrap administrator",
            role="admin",
        )

    if session_factory is None:
        # Without a database there are no operator rows, so the bootstrap key
        # checked above was the only credential that could have been valid.
        # Answering "unavailable" here would report a wrong key as an outage.
        raise ApiError(401, "Unauthorized", "Unknown or invalid API key", "unauthorized")
    async with session_factory() as session:
        operator = await session.scalar(
            select(Operator).where(Operator.key_prefix == key_prefix(api_key))
        )
        # Verify the hash even when no operator matched, so a wrong prefix and a
        # wrong key cost the same and the endpoint does not confirm which
        # prefixes exist.
        if operator is None:
            hash_key(api_key, "decoy-salt-for-constant-work")
            raise ApiError(401, "Unauthorized", "Unknown or invalid API key", "unauthorized")
        if not verify_key(api_key, operator.key_salt, operator.key_hash):
            raise ApiError(401, "Unauthorized", "Unknown or invalid API key", "unauthorized")
        if not operator.active:
            raise ApiError(403, "Forbidden", "This operator account is disabled", "operator-disabled")
        operator.last_seen_at = datetime.now(UTC)
        await session.commit()
        return Principal(
            id=operator.id, email=operator.email, name=operator.name, role=operator.role
        )


def requires(capability: str) -> Callable[[str | None], Awaitable[Principal]]:
    """Dependency factory: an endpoint declares the capability it needs."""

    async def dependency(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> Principal:
        principal = await authenticate(x_api_key)
        if not principal.can(capability):
            raise ApiError(
                403,
                "Forbidden",
                f"Role '{principal.role}' cannot perform '{capability}'",
                "forbidden",
            )
        return principal

    return dependency


ReadAccess = Annotated[Principal, Depends(requires("read"))]
CatalogWrite = Annotated[Principal, Depends(requires("catalog:write"))]
CatalogPublish = Annotated[Principal, Depends(requires("catalog:publish"))]
Merchandise = Annotated[Principal, Depends(requires("merchandise"))]
Configure = Annotated[Principal, Depends(requires("configure"))]
ManageOperators = Annotated[Principal, Depends(requires("operators:manage"))]
