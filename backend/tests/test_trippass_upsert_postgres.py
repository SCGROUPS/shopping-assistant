"""Supplier re-import against a real PostgreSQL server.

An import is not a seed. Seeding owns the whole catalogue and may truncate it;
an import lands beside live commerce and runs again every deploy. What has to
hold is that re-importing refreshes what the supplier changed without taking a
shopper's cart with it.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog import importer
from app.catalog.importer import upsert_catalog
from app.catalog.trippass import to_catalog_product
from app.common.models import (
    AvailabilitySlot,
    Cart,
    CartItem,
    Experience,
    ExperienceOption,
    OptionPrice,
    ShoppingSession,
)

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

RAW = {
    "product_id": "TEST123",
    "name": "Ba Na Hills Combo",
    "description": "Round-trip cable car and a buffet lunch.",
    "price_label": 1000000,
    "image_urls": ["https://example.invalid/bana.jpg"],
    "category_name": "Ba Na Hills",
    "variants": [
        {"name": "Adult", "description": "Aged 13+", "price": 1000000},
        {"name": "Child", "description": "Aged 3-12", "price": 500000},
    ],
}

FACETS = {
    "destination": "Da Nang",
    "category": "Day trip",
    "subcategories": ["cable car"],
    "interest_tags": ["ba na hills"],
    "indoor_outdoor": "mixed",
    "duration_minutes": 720,
    "latitude": 15.9955,
    "longitude": 107.9967,
    "meeting_point": "Ba Na cable car station.",
    "languages": ["English"],
    "accessibility_features": [],
    "family_friendly": True,
    "minimum_age": None,
    "short_description": "Round-trip cable car and a buffet lunch.",
    "needs_review": False,
}

@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine(DATABASE_URL or "", pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await create_postgres_schema(engine)
    await reset_postgres(engine)
    monkeypatch.setattr(importer, "session_factory", session_factory)
    yield session_factory
    await engine.dispose()

async def _import(variants: list[dict] | None = None) -> dict[str, int]:
    raw = dict(RAW, variants=variants) if variants else RAW
    return await upsert_catalog(
        [to_catalog_product(raw, FACETS, days=5)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )

async def _add_to_cart(session_factory) -> tuple:
    async with session_factory() as db, db.begin():
        experience = await db.scalar(select(Experience))
        option = await db.scalar(select(ExperienceOption))
        slot = await db.scalar(
            select(AvailabilitySlot).where(AvailabilitySlot.option_id == option.id)
        )
        shopper = ShoppingSession(id=uuid.uuid4(), anonymous_id="shopper-1")
        db.add(shopper)
        await db.flush()
        cart = Cart(id=uuid.uuid4(), session_id=shopper.id, currency="VND", status="ACTIVE")
        db.add(cart)
        await db.flush()
        db.add(
            CartItem(
                id=uuid.uuid4(),
                cart_id=cart.id,
                experience_id=experience.id,
                option_id=option.id,
                slot_id=slot.id,
                participants=[{"type": "adult", "count": 2}],
                unit_prices=[{"type": "adult", "amount": 1000000}],
                quantity=2,
                quoted_total=Decimal("2000000"),
                quote_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        return experience.id, option.id, slot.id

async def test_reimport_refreshes_supply_without_dropping_a_live_cart(factory):
    """The deploy job re-imports on every release while shoppers hold carts.

    Deleting and recreating options broke that twice: it orphans the foreign
    keys carts and bookings hold, and skipping cart-referenced options instead
    froze them on stale prices with an availability window that quietly
    expired. Upserting has to keep the ids and still move the price.
    """
    assert await _import() == {"created": 1, "updated": 0, "needs_review": 0}
    experience_id, option_id, slot_id = await _add_to_cart(factory)

    # The supplier raises the adult price and publishes a third variant.
    result = await _import(
        [
            {"name": "Adult", "description": "Aged 13+", "price": 1200000},
            {"name": "Child", "description": "Aged 3-12", "price": 500000},
            {"name": "Senior", "description": "Aged 65+", "price": 700000},
        ]
    )
    assert result == {"created": 0, "updated": 1, "needs_review": 0}

    async with factory() as db:
        item = await db.scalar(select(CartItem))
        assert item is not None, "re-import destroyed a live cart"
        assert (item.experience_id, item.option_id, item.slot_id) == (
            experience_id,
            option_id,
            slot_id,
        )
        adult = await db.scalar(
            select(OptionPrice.amount).where(
                OptionPrice.option_id == option_id,
                OptionPrice.participant_type == "adult",
            )
        )
        assert adult == Decimal("1200000.00"), "cart-referenced option kept a stale price"
        assert await db.scalar(select(func.count()).select_from(ExperienceOption)) == 3
        assert await db.scalar(select(func.count()).select_from(Experience)) == 1

async def test_reimport_is_idempotent(factory):
    await _import()
    async with factory() as db:
        before = [
            await db.scalar(select(func.count()).select_from(model))
            for model in (Experience, ExperienceOption, OptionPrice, AvailabilitySlot)
        ]
    assert await _import() == {"created": 0, "updated": 1, "needs_review": 0}
    async with factory() as db:
        after = [
            await db.scalar(select(func.count()).select_from(model))
            for model in (Experience, ExperienceOption, OptionPrice, AvailabilitySlot)
        ]
    assert before == after, "re-importing duplicated catalogue rows"


async def test_reimport_never_resurrects_sold_capacity(factory):
    """Scarcity badges are computed from remaining capacity.

    If an import reset capacity to the supplier's nominal figure, a sold-out
    departure would advertise seats it cannot deliver - the exact dishonest
    signal the urgency design forbids.
    """
    await _import()
    async with factory() as db, db.begin():
        slot = await db.scalar(select(AvailabilitySlot))
        slot.capacity_remaining = 1
        sold_slot_id = slot.id

    await _import()
    async with factory() as db:
        remaining = await db.scalar(
            select(AvailabilitySlot.capacity_remaining).where(
                AvailabilitySlot.id == sold_slot_id
            )
        )
    assert remaining == 1


async def test_withdrawn_option_is_deactivated_not_deleted(factory):
    """A supplier dropping a variant must stop it selling without erasing it.

    Deleting would orphan any cart or booking that already referenced it, so
    the option stays as a row and loses `active` - which both search and
    add-to-cart require.
    """
    await _import()
    await _import([{"name": "Adult", "description": "Aged 13+", "price": 1000000}])

    async with factory() as db:
        rows = (
            await db.execute(
                select(ExperienceOption.external_id, ExperienceOption.active).order_by(
                    ExperienceOption.external_id
                )
            )
        ).all()
    assert len(rows) == 2, "withdrawn option was deleted rather than retired"
    assert {external_id: active for external_id, active in rows} == {
        "trippass-ba-na-hills-combo-adult": True,
        "trippass-ba-na-hills-combo-child": False,
    }
