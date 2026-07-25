"""Reading and writing business configuration.

The write path is deliberately narrow: validate against the declared spec,
persist, audit, invalidate the cache. A setting an operator can change at
runtime is a setting an operator can break at runtime, so nothing reaches the
table without passing the same validator the reader trusts.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.admin import audit
from app.admin.auth import Principal
from app.common.database import session_factory
from app.common.errors import ApiError
from app.common.models import BusinessSetting
from app.common.runtime_config import SPECS, ConfigError, cache, defaults, get_config, validate


async def describe() -> dict[str, Any]:
    """Current values, deployed defaults and what each setting means."""
    current = await get_config()
    fallback = defaults()
    stored: dict[str, int] = {}
    if session_factory is not None:
        try:
            async with session_factory() as session:
                rows = (await session.scalars(select(BusinessSetting))).all()
            stored = {row.key: row.version for row in rows}
        except Exception:
            stored = {}
    return {
        "settings": [
            {
                "key": key,
                "description": spec.description,
                "value": current[key],
                "default": fallback[key],
                # An operator needs to know whether they are looking at a
                # deliberate choice or at whatever was deployed.
                "overridden": key in stored,
                "version": stored.get(key, 0),
            }
            for key, spec in SPECS.items()
        ]
    }


async def update(key: str, value: Any, principal: Principal) -> dict[str, Any]:
    try:
        cleaned = validate(key, value)
    except ConfigError as error:
        raise ApiError(422, "Invalid setting", str(error), "invalid-setting") from error
    if session_factory is None:
        raise ApiError(
            503,
            "Unavailable",
            "Configuration requires the database; it is not configured",
            "config-unavailable",
        )
    async with session_factory() as session, session.begin():
        setting = await session.get(BusinessSetting, key)
        before = setting.value if setting else defaults()[key]
        if setting is None:
            setting = BusinessSetting(key=key, value=cleaned, version=1)
            session.add(setting)
        else:
            setting.value = cleaned
            setting.version += 1
        setting.updated_by = principal.email
        audit.record(
            session,
            principal,
            action="config.update",
            entity_type="business_setting",
            entity_id=key,
            summary=f"Changed '{key}'",
            changes={key: {"from": before, "to": cleaned}},
        )
    cache.invalidate()
    return await describe()


async def reset(key: str, principal: Principal) -> dict[str, Any]:
    """Drop the override and fall back to the deployed default."""
    if key not in SPECS:
        raise ApiError(422, "Invalid setting", f"Unknown setting '{key}'", "invalid-setting")
    if session_factory is None:
        raise ApiError(
            503,
            "Unavailable",
            "Configuration requires the database; it is not configured",
            "config-unavailable",
        )
    async with session_factory() as session, session.begin():
        setting = await session.get(BusinessSetting, key)
        if setting is not None:
            audit.record(
                session,
                principal,
                action="config.reset",
                entity_type="business_setting",
                entity_id=key,
                summary=f"Reset '{key}' to the deployed default",
                changes={key: {"from": setting.value, "to": defaults()[key]}},
            )
            await session.delete(setting)
    cache.invalidate()
    return await describe()
