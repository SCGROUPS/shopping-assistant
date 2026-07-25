"""Operator account management from the command line.

The console cannot be the only way to create operators, because the first
operator has to exist before anyone can sign in to create one. This is that
path, and it is also how a deployment provisions staff accounts without a
human pasting credentials into a browser.
"""

from __future__ import annotations

from sqlalchemy import select

from app.admin.auth import ROLES, generate_key, hash_key, key_prefix, new_salt
from app.common.database import session_factory
from app.common.models import Operator


async def create_operator(email: str, name: str, role: str, rotate: bool = False) -> dict[str, str]:
    """Create an operator, or rotate an existing one's key.

    Returns the plaintext key exactly once. It is never stored, so a lost key
    is rotated rather than recovered - which is what keeps the audit trail
    meaningful.
    """
    if role not in ROLES:
        raise SystemExit(f"Role must be one of: {', '.join(sorted(ROLES))}")
    if session_factory is None:
        raise SystemExit("DATABASE_URL is required to manage operators.")

    key = generate_key()
    salt = new_salt()
    async with session_factory() as session, session.begin():
        operator = await session.scalar(select(Operator).where(Operator.email == email))
        if operator is None:
            session.add(
                Operator(
                    email=email,
                    name=name,
                    role=role,
                    key_prefix=key_prefix(key),
                    key_hash=hash_key(key, salt),
                    key_salt=salt,
                    active=True,
                )
            )
            return {"email": email, "role": role, "api_key": key, "action": "created"}
        if not rotate:
            # Re-running a provisioning step must not silently invalidate the
            # key the team is already using.
            return {"email": email, "role": operator.role, "api_key": "", "action": "unchanged"}
        operator.name = name
        operator.role = role
        operator.active = True
        operator.key_prefix = key_prefix(key)
        operator.key_hash = hash_key(key, salt)
        operator.key_salt = salt
        return {"email": email, "role": role, "api_key": key, "action": "rotated"}


async def list_operators() -> list[dict[str, str]]:
    if session_factory is None:
        raise SystemExit("DATABASE_URL is required to manage operators.")
    async with session_factory() as session:
        rows = (await session.scalars(select(Operator).order_by(Operator.email))).all()
    return [
        {
            "email": row.email,
            "name": row.name,
            "role": row.role,
            "active": "yes" if row.active else "no",
        }
        for row in rows
    ]
