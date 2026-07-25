"""The audit trail.

`SYSTEM_DESIGN.md` §14 claimed mutations were audited before anything wrote an
audit row. They are now, and the write happens on the caller's session so the
record commits with the change it describes - an audit log that can disagree
with the database is worse than none, because it is trusted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import Principal
from app.common.models import AuditLog

# Values that would bloat the log without helping anyone reading it.
_SKIP_FIELDS = {"updated_at", "created_at"}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level before/after, restricted to what actually changed."""
    changed: dict[str, Any] = {}
    for field in sorted(set(before) | set(after)):
        if field in _SKIP_FIELDS:
            continue
        old, new = before.get(field), after.get(field)
        if old != new:
            changed[field] = {"from": old, "to": new}
    return changed


def record(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str,
    entity_type: str,
    entity_id: Any,
    summary: str = "",
    changes: dict[str, Any] | None = None,
) -> None:
    """Stage an audit row on the caller's session.

    Deliberately not a coroutine and deliberately not committing: it must join
    the caller's transaction, not open one of its own.
    """
    session.add(
        AuditLog(
            operator_id=principal.id if not isinstance(principal.id, str) else None,
            operator_email=principal.email,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            summary=summary,
            changes=changes or {},
        )
    )
