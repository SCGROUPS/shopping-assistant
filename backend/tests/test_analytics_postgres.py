"""Analytics aggregation against a real PostgreSQL server.

`funnel_report` has two implementations — an in-memory one for demo mode and a
SQL one for the database — and only the first was ever executed. The SQL branch
shipped with a GROUPING error that no amount of demo-mode testing could have
caught, and it took a deployment to find it.

These tests need `behavior_events` and `shopping_sessions` only, so they run
against a bare PostgreSQL service without the seeded catalog or any embedding
provider.
"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common import analytics, persistence
from app.common.analytics import funnel_report
from app.common.models import Base, BehaviorEvent, ShoppingSession

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required"
)


@pytest.fixture
async def db_factory(monkeypatch):
    engine = create_async_engine(DATABASE_URL or "", pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        # Some models carry pgvector columns. The extension is not enabled by
        # default even on an image that ships it, and creating it here keeps
        # the suite runnable against any bare server rather than depending on
        # setup performed elsewhere.
        await connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db, db.begin():
        await db.execute(delete(BehaviorEvent))
        await db.execute(delete(ShoppingSession))

    # Route the analytics module at this engine and force the SQL branch.
    monkeypatch.setattr(analytics, "database_mode", lambda: True)
    monkeypatch.setattr(analytics, "require_session_factory", lambda: factory)
    monkeypatch.setattr(persistence, "database_mode", lambda: True)
    monkeypatch.setattr(persistence, "require_session_factory", lambda: factory)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(factory, rows):
    async with factory() as db, db.begin():
        sessions: dict[str, ShoppingSession] = {}
        for anonymous_id, *_ in rows:
            if anonymous_id not in sessions:
                shopping_session = ShoppingSession(
                    id=uuid4(), anonymous_id=anonymous_id
                )
                db.add(shopping_session)
                sessions[anonymous_id] = shopping_session
        # Sessions must exist before events reference them; SQLAlchemy is free
        # to order the batched inserts otherwise.
        await db.flush()
        for anonymous_id, event_type, placement, properties in rows:
            db.add(
                BehaviorEvent(
                    session_id=sessions[anonymous_id].id,
                    event_type=event_type,
                    placement=placement,
                    properties=properties,
                    occurred_at=datetime.now(UTC),
                )
            )


async def test_funnel_report_aggregates_over_postgres(db_factory):
    await _seed(
        db_factory,
        [
            ("visitor-a", "experience_viewed", "grid", {}),
            ("visitor-a", "cart_item_added", "grid", {}),
            ("visitor-a", "checkout_started", "grid", {}),
            ("visitor-a", "booking_completed", "grid", {}),
            ("visitor-b", "assistant_opened", None, {}),
            ("visitor-b", "experience_viewed", "assistant", {}),
            ("visitor-b", "booking_completed", "assistant", {}),
            ("visitor-c", "experience_viewed", "grid", {}),
            ("visitor-c", "search_submitted", None, {}),
            ("visitor-c", "search_zero_results", None, {}),
            ("visitor-c", "search_relaxed", None, {}),
        ],
    )

    report = await funnel_report()

    assert report["surfaces"]["grid"]["experience_viewed"] == 2
    assert report["surfaces"]["assistant"]["booking_completed"] == 1
    assert report["totals"]["booking_completed"] == 2
    assert report["search_health"]["zero_result_rate"] == 1.0
    assert report["search_health"]["recovery_rate"] == 1.0


async def test_nudge_triggers_group_by_json_property(db_factory):
    """The exact query that failed in production.

    Grouping by a JSON path requires one expression object: building it twice
    produces two bind parameters, which PostgreSQL rejects because the GROUP BY
    no longer textually matches the SELECT.
    """
    await _seed(
        db_factory,
        [
            ("visitor-a", "assistant_nudge_shown", None, {"trigger": "dwell"}),
            ("visitor-a", "assistant_nudge_accepted", None, {"trigger": "dwell"}),
            ("visitor-b", "assistant_nudge_shown", None, {"trigger": "dwell"}),
            ("visitor-b", "assistant_nudge_dismissed", None, {"trigger": "dwell"}),
            ("visitor-c", "assistant_nudge_shown", None, {"trigger": "zero_results"}),
        ],
    )

    report = await funnel_report()

    assert report["nudges"]["dwell"] == {
        "shown": 2,
        "accepted": 1,
        "dismissed": 1,
        "accept_rate": 0.5,
    }
    # Never accepted, and reported as 0.0 rather than omitted: a trigger that
    # only ever interrupts is exactly what this report exists to expose.
    assert report["nudges"]["zero_results"]["accept_rate"] == 0.0


async def test_assistant_conversion_is_split_by_touch(db_factory):
    await _seed(
        db_factory,
        [
            ("touched-1", "assistant_message_sent", None, {}),
            ("touched-1", "booking_completed", "assistant", {}),
            ("touched-2", "assistant_opened", None, {}),
            ("control-1", "booking_completed", "grid", {}),
            ("control-2", "experience_viewed", "grid", {}),
        ],
    )

    report = await funnel_report()

    assert report["assistant"]["touched_sessions"] == 2
    assert report["assistant"]["touched_conversion"] == 0.5
    assert report["assistant"]["untouched_conversion"] is not None
