"""Catalog operations against a real PostgreSQL server.

Three properties matter more than the endpoints themselves:

- an operator's correction survives the next supplier import,
- a product the classifier was unsure of is not sold, and
- the audit trail agrees with the database.

Each of those was broken before this module existed, and each would fail
silently rather than loudly.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import catalog_ops, settings_ops
from app.admin.auth import DEMO_BOOTSTRAP_KEY, Principal
from app.catalog import importer
from app.catalog.importer import upsert_catalog
from app.catalog.trippass import to_catalog_product
from app.common import runtime_config
from app.common.models import AuditLog, BusinessSetting, Experience, ExperienceOverride
from app.main import app

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

OPERATOR = Principal(id="bootstrap", email="ops@vietra.test", name="Ops", role="admin")

RAW = {
    "product_id": "OPS1",
    "name": "Marble Mountains Half Day",
    "description": "A half day at the Marble Mountains.",
    "price_label": 600000,
    "image_urls": ["https://example.invalid/mm.jpg"],
    "category_name": "Da Nang",
    "variants": [{"name": "Adult", "description": "13+", "price": 600000}],
}

FACETS = {
    "destination": "Da Nang",
    "category": "Day trip",
    "subcategories": ["sightseeing"],
    "interest_tags": ["marble mountains"],
    "indoor_outdoor": "outdoor",
    "duration_minutes": 240,
    "latitude": 16.0,
    "longitude": 108.25,
    "meeting_point": "Hotel pickup.",
    "languages": ["English"],
    "accessibility_features": [],
    "family_friendly": True,
    "minimum_age": None,
    "short_description": "A half day at the Marble Mountains.",
    "needs_review": False,
}


@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine(DATABASE_URL or "", pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await create_postgres_schema(engine)
    await reset_postgres(engine)
    for module in (importer, catalog_ops, settings_ops, runtime_config):
        monkeypatch.setattr(module, "session_factory", session_factory, raising=False)
    runtime_config.cache.invalidate()
    yield session_factory
    runtime_config.cache.invalidate()
    await engine.dispose()


async def _import(facets: dict | None = None) -> None:
    await upsert_catalog(
        [to_catalog_product(RAW, facets or FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )


async def _only(factory) -> Experience:
    async with factory() as db:
        experience = await db.scalar(select(Experience))
    assert experience is not None
    return experience


async def test_an_operator_correction_survives_the_next_import(factory):
    """The importer rewrites every supplier field on every run.

    Without override tracking a corrected listing reverts on the next deploy,
    which would make the catalog editor a place where work goes to die.
    """
    await _import()
    before = await _only(factory)
    assert before.category == "Day trip"

    await catalog_ops.update_experience(
        before.id, {"category": "Culture", "duration_minutes": 300}, OPERATOR
    )

    # The supplier feed has not changed its mind, and must not get its way.
    await _import()

    after = await _only(factory)
    assert after.category == "Culture"
    assert after.duration_minutes == 300
    # Fields nobody touched still track the supplier.
    assert after.title == RAW["name"]

    async with factory() as db:
        override = await db.get(ExperienceOverride, after.id)
    assert override is not None
    assert set(override.fields) == {"category", "duration_minutes"}
    assert override.updated_by == OPERATOR.email


async def test_a_released_field_goes_back_to_tracking_the_supplier(factory):
    """An override must not be a one-way door.

    Once a field is overridden the supplier can never correct it again, so
    without a release the first typo fix freezes that field forever - and the
    operator has no way to tell the system it was wrong.
    """
    await _import()
    before = await _only(factory)
    await catalog_ops.update_experience(
        before.id, {"category": "Culture", "duration_minutes": 300}, OPERATOR
    )

    released = await catalog_ops.clear_override(before.id, "category", OPERATOR)
    assert released["overridden_fields"] == ["duration_minutes"]

    # Releasing does not restore the old value on its own; the import does.
    await _import()

    after = await _only(factory)
    assert after.category == "Day trip"
    assert after.duration_minutes == 300

    entries = await catalog_ops.recent_audit(entity_id=str(after.id))
    assert any(entry["action"] == "catalog.clear_override" for entry in entries)


async def test_releasing_a_field_nobody_overrode_is_refused(factory):
    """Silence here would let the console show a release that did nothing."""
    await _import()
    experience = await _only(factory)
    from app.common.errors import ApiError

    with pytest.raises(ApiError) as problem:
        await catalog_ops.clear_override(experience.id, "category", OPERATOR)
    assert problem.value.status == 404
    assert problem.value.code == "override-not-found"


async def test_an_unreviewed_import_is_held_out_of_the_storefront(factory):
    """Classification failed, so the listing is not trustworthy enough to sell.

    The storefront requires PUBLISHED, so holding the product in
    PENDING_REVIEW keeps it out of search, recommendations and the cart with no
    further change anywhere.
    """
    await _import({**FACETS, "needs_review": True})
    experience = await _only(factory)
    assert experience.needs_review is True
    assert experience.status == "PENDING_REVIEW"

    queue = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(needs_review=True))
    assert queue["total"] == 1
    assert queue["items"][0]["title"] == RAW["name"]


async def test_approving_a_listing_is_not_undone_by_the_next_import(factory):
    """A queue that refills itself with work already done is a queue nobody reads."""
    await _import({**FACETS, "needs_review": True})
    experience = await _only(factory)
    await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR, note="Checked by hand")

    await _import({**FACETS, "needs_review": True})

    after = await _only(factory)
    assert after.status == "PUBLISHED"
    assert after.needs_review is False
    assert after.published_at is not None


async def test_suppressing_a_listing_removes_it_from_the_storefront(factory):
    await _import()
    experience = await _only(factory)
    await catalog_ops.set_merchandising(experience.id, {"suppressed": True}, OPERATOR, 1.5)

    from app.api.schemas import SearchFilters
    from app.common.persistence import load_products
    from app.search.service import is_eligible

    async with factory() as db:
        products = await load_products(db)
    assert products, "the product should still exist"
    assert not any(is_eligible(product, SearchFilters()) for product in products)


async def test_a_boost_beyond_the_ceiling_is_refused(factory):
    """Told at the point of entry, not silently clamped and appearing not to work."""
    from app.common.errors import ApiError

    await _import()
    experience = await _only(factory)
    with pytest.raises(ApiError) as error:
        await catalog_ops.set_merchandising(experience.id, {"boost": 9.0}, OPERATOR, 1.5)
    assert error.value.status == 422

    detail = await catalog_ops.set_merchandising(experience.id, {"boost": 1.4}, OPERATOR, 1.5)
    assert detail["boost"] == pytest.approx(1.4)


async def test_a_promotion_window_that_ends_before_it_starts_is_refused(factory):
    from app.common.errors import ApiError

    await _import()
    experience = await _only(factory)
    with pytest.raises(ApiError):
        await catalog_ops.set_merchandising(
            experience.id,
            {
                "promotion_starts_at": "2026-05-01T00:00:00+00:00",
                "promotion_ends_at": "2026-04-01T00:00:00+00:00",
            },
            OPERATOR,
            1.5,
        )


async def test_every_mutation_leaves_an_attributable_record(factory):
    """§14 of the design doc claimed mutations were audited before any were."""
    await _import()
    experience = await _only(factory)
    await catalog_ops.update_experience(experience.id, {"title": "Corrected title"}, OPERATOR)
    await catalog_ops.set_status(experience.id, "ARCHIVED", OPERATOR)
    await catalog_ops.set_merchandising(experience.id, {"pinned": True}, OPERATOR, 1.5)

    entries = await catalog_ops.recent_audit(entity_id=str(experience.id))
    actions = {entry["action"] for entry in entries}
    assert actions == {"catalog.update", "catalog.archived", "catalog.merchandise"}
    assert all(entry["operator"] == OPERATOR.email for entry in entries)

    update = next(entry for entry in entries if entry["action"] == "catalog.update")
    assert update["changes"]["title"] == {
        "from": RAW["name"],
        "to": "Corrected title",
    }

    # The record must agree with the database, which is why it is written in
    # the same transaction as the change.
    async with factory() as db:
        stored = await db.scalar(select(Experience))
        logged = (await db.scalars(select(AuditLog))).all()
    assert stored.title == "Corrected title"
    assert stored.status == "ARCHIVED"
    assert len(logged) == 3


async def test_a_configuration_change_takes_effect_without_a_restart(factory):
    """The whole point: retuning is something an operator does and observes."""
    assert (await runtime_config.get_config())["max_merchandising_boost"] == 1.5

    await settings_ops.update("max_merchandising_boost", 2.0, OPERATOR)
    assert (await runtime_config.get_config())["max_merchandising_boost"] == 2.0

    async with factory() as db:
        stored = await db.get(BusinessSetting, "max_merchandising_boost")
        logged = await db.scalar(
            select(AuditLog).where(AuditLog.entity_id == "max_merchandising_boost")
        )
    assert stored.value == 2.0
    assert stored.updated_by == OPERATOR.email
    assert logged.action == "config.update"

    await settings_ops.reset("max_merchandising_boost", OPERATOR)
    assert (await runtime_config.get_config())["max_merchandising_boost"] == 1.5


async def test_a_stored_setting_that_no_longer_validates_does_not_break_ranking(factory):
    """A schema change must not take the storefront down with it."""
    async with factory() as db, db.begin():
        db.add(BusinessSetting(key="search_weights", value={"relevance": 42}, version=1))
    runtime_config.cache.invalidate()

    weights = (await runtime_config.get_config())["search_weights"]
    assert weights == runtime_config.defaults()["search_weights"]


async def test_operator_search_finds_what_the_storefront_hides(factory):
    """An operator needs to find the listing that is broken."""
    await _import({**FACETS, "needs_review": True})
    found = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(q="Marble"))
    assert found["total"] == 1
    assert found["items"][0]["status"] == "PENDING_REVIEW"

    summary = await catalog_ops.review_summary()
    assert summary["needs_review"] == 1
    assert summary["by_status"]["PENDING_REVIEW"] == 1


async def test_a_missing_experience_is_a_404_not_a_crash(factory):
    from app.common.errors import ApiError

    with pytest.raises(ApiError) as error:
        await catalog_ops.get_experience(uuid4())
    assert error.value.status == 404


async def test_an_uneditable_field_is_refused(factory):
    """Prices and ratings are not the catalog editor's to invent."""
    from app.common.errors import ApiError

    await _import()
    experience = await _only(factory)
    for field, value in (("rating", 5.0), ("status", "PUBLISHED"), ("boost", 3)):
        with pytest.raises(ApiError) as error:
            await catalog_ops.update_experience(experience.id, {field: value}, OPERATOR)
        assert error.value.status == 422


async def test_the_console_endpoints_answer_end_to_end(factory):
    """The service functions are tested above; this proves the wiring."""
    await _import()
    experience = await _only(factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": DEMO_BOOTSTRAP_KEY},
    ) as api:
        listing = await api.get("/api/v1/admin/experiences")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1

        detail = await api.get(f"/api/v1/admin/experiences/{experience.id}")
        assert detail.status_code == 200
        assert detail.json()["options"][0]["prices"][0]["amount"] == float(Decimal("600000"))

        patched = await api.patch(
            f"/api/v1/admin/experiences/{experience.id}",
            json={"title": "Renamed by the console"},
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Renamed by the console"
        assert patched.json()["overridden_fields"] == ["title"]

        published = await api.post(
            f"/api/v1/admin/experiences/{experience.id}/status",
            json={"status": "PUBLISHED", "note": "ready"},
        )
        assert published.status_code == 200

        promoted = await api.post(
            f"/api/v1/admin/experiences/{experience.id}/merchandising",
            json={"pinned": True, "promotion_label": "Tet campaign"},
        )
        assert promoted.status_code == 200
        assert promoted.json()["pinned"] is True

        trail = await api.get("/api/v1/admin/audit")
        assert trail.status_code == 200
        assert len(trail.json()["entries"]) == 3

        assert (await api.get("/api/v1/analytics/funnel")).status_code == 200
