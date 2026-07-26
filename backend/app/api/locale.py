"""Locale resolution as a request dependency (spec 4.3).

Resolved once per request and echoed in the response, because a client that
cannot tell which locale it got cannot tell a translation from a fallback -
and neither can we, when a shopper reports that a page "looked English".
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Query
from sqlalchemy import select

from app.common.config import get_settings
from app.common.locales import negotiate_locale
from app.common.models import ShoppingSession
from app.common.persistence import database_mode, require_session_factory
from app.common.store import store

LOCALE_PREFERENCE_KEY = "locale"


def _preferred(preference_state: dict[str, Any] | None) -> str | None:
    value = (preference_state or {}).get(LOCALE_PREFERENCE_KEY)
    return value if isinstance(value, str) else None


async def session_locale_preference(session_id: str) -> str | None:
    """The locale this session last chose in the switcher, if any.

    A read, never a write. `ensure_session` would be the obvious call here and
    is the wrong one: locale resolution runs on every request including
    anonymous browsing, so upserting here would turn every page view into a
    write and create a session row for every crawler that loads a page.
    """
    if not database_mode():
        session = store.sessions.get(session_id)
        return _preferred(session.get("preference_state") if session else None)
    factory = require_session_factory()
    async with factory() as db:
        state = (
            await db.execute(
                select(ShoppingSession.preference_state).where(
                    ShoppingSession.anonymous_id == session_id
                )
            )
        ).scalar_one_or_none()
        return _preferred(state)


async def resolve_request_locale(
    locale: Annotated[str | None, Query(description="Content language")] = None,
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
    session_id: Annotated[str, Header(alias="X-Session-ID")] = "demo-session",
) -> str:
    preference = await session_locale_preference(session_id)
    return negotiate_locale(
        explicit=locale,
        session_preference=preference,
        accept_language=accept_language,
        enabled=get_settings().enabled_locales,
    )


RequestLocale = Annotated[str, Depends(resolve_request_locale)]
