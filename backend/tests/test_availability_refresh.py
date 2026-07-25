"""Availability refresh against a real PostgreSQL server.

`seed_database` returns early once a catalogue exists, so any change to the
seeded supply shape silently never reaches an environment that has been seeded
before. That is how the demand profile behind the scarcity signal shipped to
production and stayed inert there.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog import db_seed
from app.catalog.db_seed import refresh_availability
from app.common.models import (
    AvailabilitySlot,
    Booking,
    Cart,
    CartItem,
    Destination,
    Experience,
    ExperienceOption,
    ShoppingSession,
    Supplier,
)

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

SUPPLIER_ID = uuid4()
DESTINATION_ID = uuid4()
EXPERIENCE_ID = uuid4()
OPTION_ID = uuid4()


def _mini_catalog(slot_id, starts_at, capacity_total, capacity_remaining):
    """One product shaped like the real seed, so the code under test is real."""
    return [
        {
            "id": EXPERIENCE_ID,
            "options": [
                {
                    "id": OPTION_ID,
                    "slots": [
                        {
                            "id": slot_id,
                            "starts_at": starts_at,
                            "ends_at": starts_at + timedelta(hours=2),
                            "capacity_total": capacity_total,
                            "capacity_remaining": capacity_remaining,
                            "status": "AVAILABLE",
                        }
                    ],
                }
            ],
        }
    ]


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine(DATABASE_URL or "", pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await create_postgres_schema(engine)
    await reset_postgres(engine)
    async with session_factory() as db, db.begin():
        db.add(Supplier(id=SUPPLIER_ID, external_id="T", name="T", status="ACTIVE"))
        db.add(
            Destination(
                id=DESTINATION_ID,
                slug="test-dest",
                name="Test",
                country_code="VN",
                latitude=Decimal("0"),
                longitude=Decimal("0"),
                timezone="Asia/Ho_Chi_Minh",
            )
        )
        await db.flush()
        db.add(
            Experience(
                id=EXPERIENCE_ID,
                external_id="X1",
                supplier_id=SUPPLIER_ID,
                destination_id=DESTINATION_ID,
                slug="test-experience",
                title="Test Experience",
                short_description="s",
                description="d",
                category="culture",
                indoor_outdoor="outdoor",
                duration_minutes=120,
                latitude=Decimal("0"),
                longitude=Decimal("0"),
                meeting_point="m",
                status="ACTIVE",
            )
        )
        await db.flush()
        db.add(
            ExperienceOption(
                id=OPTION_ID,
                experience_id=EXPERIENCE_ID,
                external_id="O1",
                name="Standard",
                description="d",
                validity_type="FIXED",
                confirmation_type="INSTANT",
                cancellation_policy_code="FLEX",
                free_cancellation_hours=24,
                max_party_size=10,
                active=True,
            )
        )

    monkeypatch.setattr(db_seed, "session_factory", session_factory)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _capacity(factory, slot_id):
    async with factory() as db:
        return await db.scalar(
            select(AvailabilitySlot.capacity_remaining).where(AvailabilitySlot.id == slot_id)
        )


async def test_refresh_inserts_slots_that_do_not_exist_yet(factory, monkeypatch):
    slot_id = uuid4()
    starts = datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr(
        db_seed, "build_seed_catalog", lambda: _mini_catalog(slot_id, starts, 12, 3)
    )

    result = await refresh_availability()

    assert result == {"created": 1, "updated": 0}
    assert await _capacity(factory, slot_id) == 3


async def test_refresh_updates_capacity_of_existing_slots(factory, monkeypatch):
    """The whole point: a changed supply profile must reach a seeded database."""
    slot_id = uuid4()
    starts = datetime.now(UTC) + timedelta(days=2)
    async with factory() as db, db.begin():
        db.add(
            AvailabilitySlot(
                id=slot_id,
                option_id=OPTION_ID,
                starts_at=starts,
                ends_at=starts + timedelta(hours=2),
                capacity_total=18,
                capacity_remaining=15,
                status="AVAILABLE",
            )
        )
    monkeypatch.setattr(
        db_seed, "build_seed_catalog", lambda: _mini_catalog(slot_id, starts, 12, 2)
    )

    result = await refresh_availability()

    assert result == {"created": 0, "updated": 1}
    assert await _capacity(factory, slot_id) == 2


async def test_refresh_does_not_resurrect_capacity_that_was_booked(factory, monkeypatch):
    """Scarcity computed from sold capacity would be a fabricated signal."""
    slot_id = uuid4()
    starts = datetime.now(UTC) + timedelta(days=2)
    session_id = uuid4()
    cart_id = uuid4()
    async with factory() as db, db.begin():
        db.add(
            AvailabilitySlot(
                id=slot_id,
                option_id=OPTION_ID,
                starts_at=starts,
                ends_at=starts + timedelta(hours=2),
                capacity_total=12,
                capacity_remaining=1,
                status="AVAILABLE",
            )
        )
        db.add(ShoppingSession(id=session_id, anonymous_id="buyer"))
        await db.flush()
        db.add(Cart(id=cart_id, session_id=session_id, currency="VND", status="ORDERED"))
        await db.flush()
        db.add(
            CartItem(
                cart_id=cart_id,
                experience_id=EXPERIENCE_ID,
                option_id=OPTION_ID,
                slot_id=slot_id,
                participants=[{"type": "adult", "count": 2}],
                unit_prices=[],
                quantity=2,
                quoted_total=Decimal("100"),
                quote_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.add(
            Booking(
                cart_id=cart_id,
                booking_reference="REF-1",
                status="CONFIRMED",
                currency="VND",
                total=Decimal("100"),
                confirmed_at=datetime.now(UTC),
            )
        )

    monkeypatch.setattr(
        db_seed, "build_seed_catalog", lambda: _mini_catalog(slot_id, starts, 12, 3)
    )

    await refresh_availability()

    # Seed says 3 places; 2 are sold, so 1 remains rather than the seeded 3.
    assert await _capacity(factory, slot_id) == 1


async def test_refresh_never_reports_negative_capacity(factory, monkeypatch):
    slot_id = uuid4()
    starts = datetime.now(UTC) + timedelta(days=2)
    session_id = uuid4()
    cart_id = uuid4()
    async with factory() as db, db.begin():
        db.add(ShoppingSession(id=session_id, anonymous_id="buyer"))
        await db.flush()
        db.add(Cart(id=cart_id, session_id=session_id, currency="VND", status="ORDERED"))
        db.add(
            AvailabilitySlot(
                id=slot_id,
                option_id=OPTION_ID,
                starts_at=starts,
                ends_at=starts + timedelta(hours=2),
                capacity_total=12,
                capacity_remaining=0,
                status="AVAILABLE",
            )
        )
        await db.flush()
        db.add(
            CartItem(
                cart_id=cart_id,
                experience_id=EXPERIENCE_ID,
                option_id=OPTION_ID,
                slot_id=slot_id,
                participants=[{"type": "adult", "count": 9}],
                unit_prices=[],
                quantity=9,
                quoted_total=Decimal("100"),
                quote_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.add(
            Booking(
                cart_id=cart_id,
                booking_reference="REF-2",
                status="CONFIRMED",
                currency="VND",
                total=Decimal("100"),
                confirmed_at=datetime.now(UTC),
            )
        )

    monkeypatch.setattr(
        db_seed, "build_seed_catalog", lambda: _mini_catalog(slot_id, starts, 12, 3)
    )

    await refresh_availability()

    assert await _capacity(factory, slot_id) == 0
