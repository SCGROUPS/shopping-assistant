"""Catalog operations against a real PostgreSQL server.

Three properties matter more than the endpoints themselves:

- an operator's correction survives the next supplier import,
- a product the classifier was unsure of is not sold, and
- the audit trail agrees with the database.

Each of those was broken before this module existed, and each would fail
silently rather than loudly.
"""

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import catalog_ops, settings_ops
from app.admin.auth import DEMO_BOOTSTRAP_KEY, Principal
from app.catalog import importer
from app.catalog.importer import upsert_catalog
from app.catalog.trippass import to_catalog_product
from app.common import database, runtime_config
from app.common.locales import SUPPORTED_LOCALES
from app.common.models import (
    AuditLog,
    BusinessSetting,
    Destination,
    Experience,
    ExperienceOverride,
    ExperienceSearchDocument,
)
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

        # Renaming the listing makes its search document stale, and the publish
        # gate refuses stale text: a queued rebuild is a committed intent, not a
        # document a shopper can find. So the console's own flow is edit, let
        # the rebuild land, then publish - and the refusal in between is part of
        # the contract, not an accident of ordering.
        refused = await api.post(
            f"/api/v1/admin/experiences/{experience.id}/status",
            json={"status": "PUBLISHED", "note": "ready"},
        )
        assert refused.status_code == 409
        assert "stale-index" in {blocker["code"] for blocker in refused.json()["blockers"]}

        from app.catalog import indexing

        assert await indexing.process_index_work(factory, _StubEmbedder(), limit=50) >= 1

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


async def test_an_operator_edit_makes_the_product_findable_by_its_new_words(factory):
    """The console could change what a product said and not what search matched.

    Only the importer and the seeder ever built a search document, so a title
    edit left the index describing the old text indefinitely. On a manually
    authored record - which no importer is allowed to touch - it would never
    have been corrected at all. The visible symptom is the worst kind: search
    returns nothing for the words on the page, and the assistant fluently
    reports that we have no such product.
    """
    from app.catalog.indexing import process_index_work
    from app.common.models import ExperienceSearchDocument, IndexWorkItem

    await _import()
    experience = await _only(factory)

    async with factory() as db:
        document = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id
            )
        )
    assert document is not None
    assert "Marble Mountains" in document.document_text
    assert "Lantern Workshop" not in document.document_text

    await catalog_ops.update_experience(
        experience.id, {"title": "Hoi An Lantern Workshop"}, OPERATOR
    )

    # The intent is recorded in the same transaction as the edit, so it cannot
    # be lost by a crash between the two.
    async with factory() as db:
        work = await db.scalar(
            select(IndexWorkItem).where(IndexWorkItem.experience_id == experience.id)
        )
    assert work is not None
    assert work.status == "queued"

    class _StubProvider:
        async def embed(self, text: str) -> list[float]:
            return [0.01] * 512

    completed = await process_index_work(factory, _StubProvider(), limit=20)
    assert completed == len(SUPPORTED_LOCALES)

    async with factory() as db:
        documents = list(
            (
                await db.scalars(
                    select(ExperienceSearchDocument).where(
                        ExperienceSearchDocument.experience_id == experience.id
                    )
                )
            ).all()
        )
        pending = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )

    # Every shopper locale gets a document, not just English. Retrieval filters
    # on locale, so a locale with no document is a locale in which the product
    # cannot be found at all - which is how a Vietnamese-authored listing would
    # otherwise be invisible to everyone else.
    assert {document.locale for document in documents} == set(SUPPORTED_LOCALES)
    assert all("Lantern Workshop" in document.document_text for document in documents)
    assert pending == []


async def test_an_import_does_not_erase_other_locales(factory):
    """The importer used to delete every document for a product, then write one.

    That is harmless while English is the only locale and destructive the
    moment it is not: a single import run would drop seven translated
    documents and leave English, so search would quietly stop returning the
    product for anyone not shopping in English - with no error and no failing
    row count to notice.
    """
    from app.catalog.indexing import upsert_search_document
    from app.common.models import ExperienceSearchDocument

    await _import()
    experience = await _only(factory)

    async with factory() as db, db.begin():
        await upsert_search_document(
            db,
            experience_id=experience.id,
            locale="vi",
            document_text="Du thuyền hoàng hôn làng chài",
            embedding=[0.02] * 512,
        )

    await _import()

    async with factory() as db:
        locales = set(
            (
                await db.scalars(
                    select(ExperienceSearchDocument.locale).where(
                        ExperienceSearchDocument.experience_id == experience.id
                    )
                )
            ).all()
        )
    assert locales == {"en", "vi"}


class _StubEmbedder:
    """Deterministic embeddings, optionally failing, for the outbox tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.batches = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return [0.03] * 512

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.batches += 1
        return [await self.embed(text) for text in texts]


async def test_an_import_refreshes_the_locales_that_resolve_through_english(factory):
    """A source edit that arrives by import must not leave the other locales stale.

    Until a listing is translated every locale resolves *through* the English
    text, so an import that writes only `en` leaves seven documents describing
    the previous version of the product. Nothing fails, no row is missing, and
    search quietly answers from text the catalogue no longer contains - which is
    the same defect this whole module exists to close, arriving by a different
    door.
    """
    from app.catalog.indexing import process_index_work
    from app.common.models import ExperienceSearchDocument

    await _import()
    experience = await _only(factory)
    await process_index_work(factory, _StubEmbedder(), limit=50)

    renamed = dict(RAW, name="Sunrise Kayaking Expedition")
    await upsert_catalog(
        [to_catalog_product(renamed, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )
    await process_index_work(factory, _StubEmbedder(), limit=50)

    async with factory() as db:
        documents = list(
            (
                await db.scalars(
                    select(ExperienceSearchDocument).where(
                        ExperienceSearchDocument.experience_id == experience.id
                    )
                )
            ).all()
        )

    assert len(documents) >= 8
    stale = sorted(d.locale for d in documents if "Sunrise Kayaking" not in d.document_text)
    assert stale == []


async def test_the_import_does_not_re_embed_the_locale_it_just_wrote(factory):
    """English is written directly by the importer; it must not also be queued.

    The importer enqueues every locale because the untranslated ones resolve
    through English. Only the document's own fingerprint can tell it that the
    English row it wrote a moment ago is already current - no work item exists
    yet on a first import - so without it every import would pay to re-embed
    text it had just embedded.
    """
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    async with factory() as db:
        queued = set(
            (
                await db.scalars(
                    select(IndexWorkItem.locale).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status == "queued",
                    )
                )
            ).all()
        )

    assert "en" not in queued
    assert "vi" in queued


async def test_exhausted_work_becomes_failed_and_an_edit_revives_it(factory):
    """Retry exhaustion must be terminal and visible, and recoverable by an edit.

    Leasing ignores items past their attempt limit. Leaving those items
    `queued` produces a row that is never selected again yet reports itself as
    pending forever - an index that is out of date and a queue that says it is
    keeping up. Worse, without resetting the counter the next operator edit
    would inherit the exhausted attempts and could never be indexed at all.
    """
    from app.catalog.indexing import MAX_ATTEMPTS, process_index_work
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    broken = _StubEmbedder(fail=True)
    for _ in range(MAX_ATTEMPTS + 1):
        await process_index_work(factory, broken, limit=50)

    async with factory() as db:
        statuses = dict(
            (
                await db.execute(
                    select(IndexWorkItem.locale, IndexWorkItem.status).where(
                        IndexWorkItem.experience_id == experience.id
                    )
                )
            ).all()
        )
    # English was satisfied by the import itself; every locale the worker had to
    # build is retired as failed rather than left reporting itself as pending.
    assert statuses.pop("en") == "done"
    assert set(statuses.values()) == {"failed"}

    await catalog_ops.update_experience(
        experience.id, {"title": "Corrected After An Outage"}, OPERATOR
    )
    completed = await process_index_work(factory, _StubEmbedder(), limit=50)
    assert completed >= 8

    async with factory() as db:
        remaining = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
    assert remaining == []


async def test_a_document_version_bump_marks_the_catalogue_stale(factory):
    """Changing how documents are built must invalidate the documents already built.

    The outbox only ever hears about content changes. A release that improves
    document construction, or moves to a new embedding model, touches no
    catalogue row - so nothing is enqueued, the backlog reads empty, and the
    entire existing catalogue stays indexed the old way indefinitely. The
    reconciliation pass is the only thing that closes that gap, and it can only
    work because the document stores the fingerprint of how it was built rather
    than a hash of its text.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)
    await indexing.process_index_work(factory, _StubEmbedder(), limit=50)

    # Nothing has changed, so a reconciliation pass must find nothing.
    async with factory() as db, db.begin():
        assert await indexing.reconcile_index(db) == 0

    original = indexing.DOCUMENT_VERSION
    indexing.DOCUMENT_VERSION = f"{original}-next"
    try:
        async with factory() as db, db.begin():
            stale = await indexing.reconcile_index(db)
        async with factory() as db:
            queued = list(
                (
                    await db.scalars(
                        select(IndexWorkItem).where(
                            IndexWorkItem.experience_id == experience.id,
                            IndexWorkItem.status == "queued",
                        )
                    )
                ).all()
            )
    finally:
        indexing.DOCUMENT_VERSION = original

    assert stale >= 8
    assert len(queued) >= 8


async def test_a_fallback_vector_is_not_certified_as_a_real_embedding(factory):
    """An outage must degrade the index visibly, not silently and permanently.

    When the embedding provider is down the importer substitutes a
    deterministic vector so the import still completes. That vector is not an
    embedding: it bears no relation to the vectors a shopper's query produces,
    so the product is effectively absent from semantic search. Recording it
    under the production model name would make the fingerprint say it was built
    correctly, and reconciliation - the one mechanism that could repair it -
    would report the catalogue perfectly healthy forever. One transient outage
    would remove products from search permanently, with no error anywhere.
    """
    from app.catalog import indexing
    from app.common.models import ExperienceSearchDocument, IndexWorkItem

    await upsert_catalog(
        [to_catalog_product(RAW, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=_StubEmbedder(fail=True),
    )
    experience = await _only(factory)

    async with factory() as db:
        english = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert english is not None
    assert english.embedding_model == indexing.FALLBACK_EMBEDDING_MODEL

    # Because the fallback is recorded honestly, the document does not match
    # what it should be, so English is still owed work. Written under the real
    # model name it would match, no work would be owed, and the deterministic
    # vector would stand as the product's embedding indefinitely.
    async with factory() as db:
        pending = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "en",
                IndexWorkItem.status == "queued",
            )
        )
    assert pending is not None

    await indexing.process_index_work(factory, _StubEmbedder(), limit=50)
    async with factory() as db:
        repaired = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert repaired is not None
    assert repaired.embedding_model == indexing.EMBEDDING_MODEL

    async with factory() as db, db.begin():
        assert await indexing.reconcile_index(db) == 0


async def test_reconciliation_revives_work_that_burnt_its_retries(factory):
    """A failed item must be retryable without editing the product.

    Exhausted work is terminal by design, so something has to be able to revive
    it. Reconciliation is that something - but it asks for the fingerprint the
    content already has, and a conflict clause keyed on "the fingerprint
    changed" does nothing for an unchanged one. The item stays `failed`, the
    repair is reported as made, and the only route back is an operator editing
    a product that has nothing wrong with it.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    broken = _StubEmbedder(fail=True)
    for _ in range(indexing.MAX_ATTEMPTS + 1):
        await indexing.process_index_work(factory, broken, limit=50)

    async with factory() as db:
        statuses = dict(
            (
                await db.execute(
                    select(IndexWorkItem.locale, IndexWorkItem.status).where(
                        IndexWorkItem.experience_id == experience.id
                    )
                )
            ).all()
        )
    assert statuses.pop("en") == "done"
    assert set(statuses.values()) == {"failed"}

    async with factory() as db, db.begin():
        revived = await indexing.reconcile_index(db)
    assert revived >= 1

    completed = await indexing.process_index_work(factory, _StubEmbedder(), limit=50)
    assert completed >= 1

    async with factory() as db:
        remaining = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
    assert remaining == []


async def test_reconciliation_does_not_overwrite_a_newer_edit(factory):
    """Reconciliation reads, then writes; an edit can land in between.

    It loads the catalogue, computes the fingerprint of what each document
    should be, and enqueues afterwards. An operator edit committing in that
    window leaves a work item asking for the *new* content, and an
    unconditional write would replace it with the fingerprint of the old. The
    worker then builds the new text, finds the work item disagrees, discards
    its own correct output - and the edit is lost from search with an empty
    backlog reporting success.
    """
    from app.catalog import indexing
    from app.common.models import ExperienceSearchDocument, IndexWorkItem

    await _import()
    experience = await _only(factory)
    await indexing.process_index_work(factory, _StubEmbedder(), limit=50)

    # Stand in for the interleaving: compute what reconciliation would have
    # seen, let the edit commit, then let reconciliation write its stale view.
    async with factory() as db:
        row = (
            await db.execute(
                select(Experience, Destination.name)
                .join(Destination, Destination.id == Experience.destination_id)
                .where(Experience.id == experience.id)
            )
        ).first()
        assert row is not None
        stale_experience, destination_name = row
        stale_text = await indexing.resolved_document_text(
            db, stale_experience, destination_name, "en"
        )
    stale_fingerprint = indexing.index_fingerprint(stale_text, "en")

    await catalog_ops.update_experience(
        experience.id, {"title": "Edited During Reconciliation"}, OPERATOR
    )

    async with factory() as db, db.begin():
        await indexing.enqueue_reindex(
            db, experience.id, "en", stale_fingerprint, only_if_idle=True
        )

    async with factory() as db:
        item = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "en",
            )
        )
    assert item is not None
    assert item.fingerprint != stale_fingerprint

    await indexing.process_index_work(factory, _StubEmbedder(), limit=50)
    async with factory() as db:
        document = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert document is not None
    assert "Edited During Reconciliation" in document.document_text


async def test_one_broken_record_does_not_strand_the_rest_of_the_batch(factory):
    """A failure outside the embedding call has to reach the same transition.

    Only embedding errors were caught. Anything raised while loading the record
    or writing the document escaped the loop, leaving that item and every item
    leased after it stuck in `leased` - and because leasing skips work at the
    attempt limit, a repeatable error would strand them there permanently:
    invisible to the queue, never retried, with the backlog reading empty.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    original = indexing.resolved_document_text
    seen: list[str] = []

    broken = SUPPORTED_LOCALES[-1]

    async def exploding(session, exp, destination_name, locale):
        seen.append(locale)
        if locale == broken:
            raise RuntimeError("data error while building the document")
        return await original(session, exp, destination_name, locale)

    indexing.resolved_document_text = exploding
    try:
        completed = await indexing.process_index_work(factory, _StubEmbedder(), limit=50)
    finally:
        indexing.resolved_document_text = original

    assert completed >= 1

    async with factory() as db:
        statuses = list(
            (
                await db.scalars(
                    select(IndexWorkItem.status).where(IndexWorkItem.experience_id == experience.id)
                )
            ).all()
        )
    # The broken one went back to the queue for a retry, every item leased
    # after it still completed, and nothing was left held.
    assert "leased" not in statuses
    assert statuses.count("queued") == 1
    assert statuses.count("done") == len(statuses) - 1
    assert len(statuses) >= len(SUPPORTED_LOCALES) - 1


async def test_a_repair_run_does_not_stop_halfway_through_the_backlog(factory, monkeypatch):
    """The command that repairs the index must finish the job.

    A version bump enqueues the whole catalogue, locale by locale. A drain that
    stops after a fixed number of documents leaves the rest stale while exiting
    successfully, so the job reports the index repaired when most of it is not.
    The in-application worker keeps its bound - it shares a process with request
    handling - but the CLI must run to empty.
    """
    from app.assistant import provider as provider_module
    from app.catalog import indexing
    from app.common import database
    from app.common.models import IndexWorkItem

    # Enough products that the backlog exceeds one lease batch, which is what
    # makes the difference between a bounded and an unbounded drain visible.
    await upsert_catalog(
        [
            to_catalog_product(
                {**RAW, "product_id": f"OPS{n}", "name": f"Marble Mountains Half Day {n}"},
                FACETS,
                days=3,
            )
            for n in range(1, 13)
        ],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=_StubEmbedder(),
    )
    monkeypatch.setattr(database, "session_factory", factory, raising=False)
    monkeypatch.setattr(provider_module, "build_ai_provider", lambda: _StubEmbedder())

    async def queued_count() -> int:
        async with factory() as db:
            return (
                await db.scalar(
                    select(func.count())
                    .select_from(IndexWorkItem)
                    .where(IndexWorkItem.status == "queued")
                )
            ) or 0

    backlog = await queued_count()
    assert backlog > 1

    # A bound below the backlog stops early - that is the in-process worker's
    # contract, and the reason the CLI must not inherit it.
    bounded = await indexing.drain_index_queue(limit=1)
    assert bounded < backlog
    assert await queued_count() > 0

    drained = await indexing.drain_index_queue(limit=None)
    assert drained > 0
    assert await queued_count() == 0


async def test_a_shutdown_mid_batch_hands_the_work_back(factory):
    """Redeploying during a batch must not consume the queue's retries.

    A batch leases up to twenty-five items at once and increments `attempts` on
    each as it leases them. If a shutdown simply abandons the task, every
    unstarted item stays `leased` for the full lease duration and keeps the
    attempt it never used - so a service that redeploys a few times in a row
    can push its whole backlog past the attempt limit and retire work that
    never actually failed.
    """
    import asyncio

    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    original = indexing._process_one
    started = 0

    async def interrupted(*args, **kwargs):
        nonlocal started
        started += 1
        if started == 2:
            raise asyncio.CancelledError
        return await original(*args, **kwargs)

    indexing._process_one = interrupted
    try:
        with pytest.raises(asyncio.CancelledError):
            await indexing.process_index_work(factory, _StubEmbedder(), limit=50)
    finally:
        indexing._process_one = original

    async with factory() as db:
        items = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(IndexWorkItem.experience_id == experience.id)
                )
            ).all()
        )

    # Nothing is still held, and the interrupted item plus everything behind it
    # kept their retries: released work is not failed work.
    assert all(item.status != "leased" for item in items)
    assert all(item.lease_token is None for item in items)
    unfinished = [item for item in items if item.status == "queued"]
    assert unfinished
    assert all(item.attempts == 0 for item in unfinished)


async def test_reconciliation_covers_a_catalogue_larger_than_one_page(factory, monkeypatch):
    """Paging must reach the end of the catalogue, not the end of the first page.

    Reconciliation is the only thing that notices a release changed how
    documents are built, so a paging bug here does not fail - it silently
    reconciles the first page and reports the rest of the catalogue healthy.
    Keyset pagination is used precisely so that a product inserted while the
    pass runs cannot make it skip a row it has not seen.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await upsert_catalog(
        [
            to_catalog_product(
                {**RAW, "product_id": f"OPS{n}", "name": f"Marble Mountains Half Day {n}"},
                FACETS,
                days=3,
            )
            for n in range(1, 8)
        ],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=_StubEmbedder(),
    )
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    monkeypatch.setattr(indexing, "RECONCILE_PAGE_SIZE", 2)
    monkeypatch.setattr(database, "session_factory", factory, raising=False)

    async with factory() as db, db.begin():
        assert await indexing.reconcile_index(db) == 0

    original = indexing.DOCUMENT_VERSION
    monkeypatch.setattr(indexing, "DOCUMENT_VERSION", f"{original}-next")

    stale = await indexing.run_reconcile()

    async with factory() as db:
        queued = await db.scalar(
            select(func.count()).select_from(IndexWorkItem).where(IndexWorkItem.status == "queued")
        )
        experiences = await db.scalar(select(func.count()).select_from(Experience))

    assert experiences == 7
    # Every product, not just the first page of two.
    assert stale >= experiences * len(SUPPORTED_LOCALES)
    assert queued == stale


async def test_a_stale_reconciliation_request_does_not_fail_a_current_document(factory):
    """A work item records what someone asked for, not what is true.

    Reconciliation reads the catalogue, computes a fingerprint, and enqueues
    afterwards. An edit landing in that window is indexed by the worker first,
    so the item is `done` when the stale request arrives - and "only write over
    idle items" happily accepts it. The worker then rebuilds the *current*
    text, finds the work item asking for the previous version, and discards a
    correct document. Repeated to the attempt limit that retires the item as
    `failed`: a failure that never happened, on a product that was indexed
    correctly the whole time.
    """
    from app.catalog import indexing
    from app.common.models import ExperienceSearchDocument, IndexWorkItem

    await _import()
    experience = await _only(factory)
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    async with factory() as db:
        row = (
            await db.execute(
                select(Experience, Destination.name)
                .join(Destination, Destination.id == Experience.destination_id)
                .where(Experience.id == experience.id)
            )
        ).first()
        assert row is not None
        stale_fingerprint = indexing.index_fingerprint(
            await indexing.resolved_document_text(db, row[0], row[1], "en"), "en"
        )

    # The edit is made and fully indexed before the stale request arrives.
    await catalog_ops.update_experience(experience.id, {"title": "Indexed Already"}, OPERATOR)
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    async with factory() as db, db.begin():
        await indexing.enqueue_reindex(
            db, experience.id, "en", stale_fingerprint, only_if_idle=True
        )

    for _ in range(indexing.MAX_ATTEMPTS + 1):
        await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    async with factory() as db:
        item = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "en",
            )
        )
        document = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert item is not None
    assert item.status == "done"
    assert document is not None
    assert "Indexed Already" in document.document_text


async def test_a_lease_that_expired_with_no_attempts_left_is_retired(factory):
    """An item can end up leased, out of attempts, and owned by nobody.

    Leasing requires `attempts < MAX_ATTEMPTS`, so if the failure transition
    itself could not be written - a database error while recording it, a
    process that died between leasing and reporting - the row keeps the status
    `leased` after its lease expires and is never selected again. It is not
    queued, not failed and not held: invisible to the queue, to the backlog and
    to every retry, permanently.
    """
    from datetime import UTC, datetime, timedelta

    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    async with factory() as db, db.begin():
        await db.execute(
            update(IndexWorkItem)
            .where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "vi",
            )
            .values(
                status="leased",
                lease_token=uuid4(),
                leased_until=datetime.now(UTC) - timedelta(hours=1),
                attempts=indexing.MAX_ATTEMPTS,
            )
        )

    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    async with factory() as db:
        item = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "vi",
            )
        )
    assert item is not None
    assert item.status == "failed"
    assert item.lease_token is None


async def test_a_shutdown_while_recording_a_failure_still_releases_the_item(factory):
    """Cancellation during the failure transition must not strand the item.

    The item being failed is the one item the release path used to skip: it had
    already been dropped from the outstanding set before processing began. A
    deploy landing while its failure was being committed released every later
    lease and left this one held, with its attempt spent and nothing pointing
    at it.
    """
    import asyncio

    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    original_process = indexing._process_one
    original_fail = indexing._fail

    async def always_broken(*args, **kwargs):
        raise RuntimeError("indexing is broken")

    async def cancelled_fail(*args, **kwargs):
        raise asyncio.CancelledError

    indexing._process_one = always_broken
    indexing._fail = cancelled_fail
    try:
        with pytest.raises(asyncio.CancelledError):
            await indexing.process_index_work(factory, _StubEmbedder(), limit=500)
    finally:
        indexing._process_one = original_process
        indexing._fail = original_fail

    async with factory() as db:
        items = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(IndexWorkItem.experience_id == experience.id)
                )
            ).all()
        )
    released = [item for item in items if item.locale != "en"]
    assert released
    assert all(item.status == "queued" for item in released)
    assert all(item.lease_token is None for item in items)
    assert all(item.attempts == 0 for item in items)


async def test_the_production_reconcile_path_commits_every_page(factory, monkeypatch):
    """`run_reconcile` is what production runs, so it is what has to be tested.

    It differs from the in-transaction helper in exactly the ways that can go
    wrong: it commits between pages and carries a cursor across transactions.
    A pass that reconciled the first page and stopped would report the rest of
    the catalogue healthy, which is indistinguishable from success.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await upsert_catalog(
        [
            to_catalog_product(
                {**RAW, "product_id": f"OPS{n}", "name": f"Marble Mountains Half Day {n}"},
                FACETS,
                days=3,
            )
            for n in range(1, 8)
        ],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=_StubEmbedder(),
    )
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    monkeypatch.setattr(database, "session_factory", factory, raising=False)
    monkeypatch.setattr(indexing, "RECONCILE_PAGE_SIZE", 2)
    monkeypatch.setattr(indexing, "DOCUMENT_VERSION", f"{indexing.DOCUMENT_VERSION}-next")

    pages: list[int] = []
    original = indexing.reconcile_page

    async def counting(session, **kwargs):
        pages.append(1)
        return await original(session, **kwargs)

    monkeypatch.setattr(indexing, "reconcile_page", counting)
    stale = await indexing.run_reconcile()

    async with factory() as db:
        queued = await db.scalar(
            select(func.count()).select_from(IndexWorkItem).where(IndexWorkItem.status == "queued")
        )

    # Seven products at two per page is four pages plus the empty one that ends
    # the walk - if the cursor did not survive the commit, this would be two.
    assert len(pages) == 5
    assert stale == 7 * len(SUPPORTED_LOCALES)
    assert queued == stale


async def test_a_drain_that_builds_nothing_still_reports_its_backlog(factory, monkeypatch):
    """ "The queue stopped producing" is not "the queue is empty".

    A round in which every item fails transiently returns them all to `queued`
    and builds none, which ends the drain. Without a backlog report the repair
    command exits successfully having rebuilt nothing at all, and the job that
    exists to guarantee the index is current is the one claiming it is.
    """
    from app.catalog import indexing
    from app.common import database

    await _import()
    monkeypatch.setattr(database, "session_factory", factory, raising=False)
    monkeypatch.setattr(
        "app.assistant.provider.build_ai_provider", lambda: _StubEmbedder(fail=True)
    )

    built = await indexing.drain_index_queue(limit=None)
    assert built == 0

    backlog = await indexing.index_backlog()
    assert backlog.get("queued", 0) >= len(SUPPORTED_LOCALES) - 1


async def test_an_import_cannot_be_overwritten_by_a_worker_holding_older_content(factory):
    """The import must wait for a worker mid-flight, not race it.

    A worker leases English, reads the listing, and starts embedding. The import
    arrives with newer content. If it wrote its document first and then skipped
    the enqueue - because the document it had just written already looked
    current - the worker would wake, overwrite the imported text with the
    version it had been building, and mark the item done. The newer content
    would be gone from search with nothing left to repair it.

    Enqueueing first is what prevents that: the work item's row lock is held by
    the worker, so the import blocks until the worker has finished and can
    therefore never be overtaken by it.
    """
    import asyncio

    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)
    experience = await _only(factory)

    async def import_v2() -> None:
        await upsert_catalog(
            [to_catalog_product({**RAW, "name": "Marble Mountains Full Day"}, FACETS, days=3)],
            supplier_external_id="TRIPPASS",
            supplier_name="Trippass",
            ai_provider=_StubEmbedder(),
        )

    async with factory() as holder, holder.begin():
        await holder.execute(
            select(IndexWorkItem)
            .where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "en",
            )
            .with_for_update()
        )
        running = asyncio.create_task(import_v2())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(running), timeout=1.0)

    await asyncio.wait_for(running, timeout=30)

    async with factory() as db:
        document = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert document is not None
    assert "Full Day" in document.document_text


async def test_the_repair_job_waits_for_work_another_replica_is_doing(factory, monkeypatch):
    """A deployment must not fail because indexing was working.

    The repair job does not run alone: the application keeps an in-process
    worker, so part of the backlog can be leased by a replica getting on with
    it. The job's own drain finds nothing leasable, and a job that failed the
    moment it saw a non-empty backlog would report a broken index every time
    the app happened to be busy at deploy time.
    """
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.catalog import indexing
    from app.common import database
    from app.common.models import IndexWorkItem

    await _import()
    monkeypatch.setattr(database, "session_factory", factory, raising=False)
    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: _StubEmbedder())

    # Another replica holds this locale, and holds it beyond one poll.
    async with factory() as db, db.begin():
        await db.execute(
            update(IndexWorkItem)
            .where(
                IndexWorkItem.experience_id.in_(select(Experience.id)),
                IndexWorkItem.locale == "vi",
            )
            .values(
                status="leased",
                lease_token=uuid4(),
                leased_until=datetime.now(UTC) + timedelta(minutes=5),
            )
        )

    async def finish_later() -> None:
        await asyncio.sleep(0.6)
        async with db2 as session, session.begin():
            await session.execute(
                update(IndexWorkItem)
                .where(IndexWorkItem.locale == "vi")
                .values(status="done", lease_token=None, leased_until=None)
            )

    db2 = factory()
    helper = asyncio.create_task(finish_later())
    _, _, backlog = await indexing.run_reindex(settle_seconds=20.0, poll_seconds=0.2)
    await helper

    assert backlog == {}


async def test_the_repair_job_does_not_wait_out_a_terminal_failure(factory, monkeypatch):
    """Waiting is for work in progress. A `failed` row is not in progress.

    Blocking the full settle window on something that will never change turns
    every genuine indexing failure into a slow one, and the job has to report
    it either way.
    """
    import time

    from app.catalog import indexing
    from app.common import database

    await _import()
    monkeypatch.setattr(database, "session_factory", factory, raising=False)
    monkeypatch.setattr(
        "app.assistant.provider.build_ai_provider", lambda: _StubEmbedder(fail=True)
    )
    for _ in range(indexing.MAX_ATTEMPTS + 1):
        await indexing.process_index_work(factory, _StubEmbedder(fail=True), limit=500)

    started = time.monotonic()
    _, _, backlog = await indexing.run_reindex(settle_seconds=30.0, poll_seconds=1.0)
    elapsed = time.monotonic() - started

    assert backlog.get("failed", 0) >= 1
    assert elapsed < 10


async def test_a_re_import_during_an_outage_keeps_the_healthy_embedding(factory):
    """A provider outage must not downgrade a document that was already right.

    The fan-out only enqueues locales whose stored document disagrees with what
    it should be. An unchanged re-import agrees, so nothing is enqueued - and if
    the import then wrote its deterministic fallback anyway, it would replace a
    real embedding with numbers unrelated to any query, with no work item left
    anywhere to put it back. The catalogue would look untouched and the product
    would be gone from semantic search.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)
    experience = await _only(factory)

    async with factory() as db:
        before = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
    assert before is not None
    assert before.embedding_model == indexing.EMBEDDING_MODEL

    await upsert_catalog(
        [to_catalog_product(RAW, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=_StubEmbedder(fail=True),
    )

    async with factory() as db:
        after = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
        outstanding = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
    assert after is not None
    assert after.embedding_model == indexing.EMBEDDING_MODEL
    assert after.index_fingerprint == before.index_fingerprint
    assert outstanding == []


async def test_a_failure_the_document_has_outlived_stops_blocking_the_repair_job(factory):
    """A terminal row for work something else completed must not be terminal forever.

    The document can be brought current by another path while a request for it
    is failing - a direct import of unchanged text, most obviously. The
    reconciliation pass then walks straight past, because the document really is
    current and there is nothing to enqueue, and the `failed` row survives every
    pass. Every repair job after that reports a backlog it cannot empty, so
    deployments fail permanently on an index that is completely healthy.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)
    experience = await _only(factory)

    async with factory() as db:
        current = await db.scalar(
            select(ExperienceSearchDocument.index_fingerprint).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "vi",
            )
        )
    assert current is not None

    # The document is current; the request for it is terminally failed.
    async with factory() as db, db.begin():
        await db.execute(
            update(IndexWorkItem)
            .where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "vi",
            )
            .values(
                status="failed",
                fingerprint=current,
                attempts=indexing.MAX_ATTEMPTS,
                error_detail="embedding failed",
            )
        )

    async with factory() as db, db.begin():
        await indexing.reconcile_index(db)

    async with factory() as db:
        item = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "vi",
            )
        )
    assert item is not None
    assert item.status == "done"
    assert item.error_detail is None


async def test_the_repair_job_outwaits_a_live_lease(factory):
    """The settle window is meaningless if it is shorter than a lease.

    A worker holding a live lease is doing nothing wrong, and the repair job
    cannot take that work until the lease expires. A window shorter than the
    lease therefore fails the deployment for a replica that was merely slow -
    which is what a request to an embedding provider with no explicit timeout
    can be.
    """
    from app.catalog import indexing

    assert indexing.run_reindex.__kwdefaults__ is not None
    assert indexing.run_reindex.__kwdefaults__["settle_seconds"] > indexing.LEASE_SECONDS


async def test_a_re_import_does_not_undo_an_operator_correction_in_search(factory):
    """The feed's text is not the catalogue's text, and only one of them is true.

    `_apply` skips every field an operator has corrected, which is what lets the
    editor and the nightly import coexist. But the import also carries a
    document it built from the *supplier's* fields and an embedding of that
    document. Writing it puts the supplier's title back into search while the
    listing page goes on showing the operator's - and because the catalogue was
    already current, the fan-out found nothing to enqueue, so no work item
    exists to notice the disagreement.
    """
    from app.catalog import indexing
    from app.common.models import ExperienceOverride, IndexWorkItem

    await _import()
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)
    experience = await _only(factory)

    async with factory() as db, db.begin():
        db.add(ExperienceOverride(experience_id=experience.id, fields=["title"]))
        await db.execute(
            update(Experience)
            .where(Experience.id == experience.id)
            .values(title="Operator Corrected Title")
        )
        await indexing.enqueue_experience_reindex(db, experience.id)
    await indexing.process_index_work(factory, _StubEmbedder(), limit=500)

    await _import()

    async with factory() as db:
        document = await db.scalar(
            select(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id,
                ExperienceSearchDocument.locale == "en",
            )
        )
        outstanding = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
        title = await db.scalar(select(Experience.title).where(Experience.id == experience.id))

    assert title == "Operator Corrected Title"
    assert document is not None
    assert "Operator Corrected Title" in document.document_text
    assert RAW["name"] not in document.document_text
    assert outstanding == []


async def test_the_worker_embeds_a_batch_in_one_request(factory):
    """One round trip per document is what makes the first backfill impossible.

    A catalogue of a few hundred listings across eight locales is a few thousand
    documents, and at a round trip each the deployment job runs out of time long
    before the queue runs out of work - which is exactly how this was found, on
    a real deploy. Providers embed a list as cheaply as a string, which is why
    the importer has always batched.
    """
    from app.catalog import indexing

    await _import()
    experience = await _only(factory)

    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(title="Rebuilt")
        )
        await indexing.enqueue_experience_reindex(db, experience.id)

    embedder = _StubEmbedder()
    built = await indexing.process_index_work(factory, embedder, limit=indexing.DRAIN_BATCH)

    assert built >= len(indexing.SUPPORTED_LOCALES)
    # Every locale resolves through the same text until it is translated, so the
    # whole batch is one request carrying one distinct document.
    assert embedder.batches == 1
    assert embedder.calls == 1


async def test_a_timeout_during_the_batch_request_hands_every_lease_back(factory):
    """The failure that actually took production down must not cost the batch.

    A job timeout arrives as a cancellation, and the batched embedding request
    is where a worker spends most of its time - so this is the likeliest moment
    to be cancelled. Pre-warming outside the handler that returns leases left
    all sixty-four items `leased` with an attempt spent apiece, which is the
    same defect the handler was written to prevent, reintroduced by the
    optimisation that made the deploy fit.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    class _Cancelling(_StubEmbedder):
        async def embed_many(self, texts: list[str]) -> list[list[float]]:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await indexing.process_index_work(factory, _Cancelling(), limit=indexing.DRAIN_BATCH)

    async with factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
    assert rows
    assert all(row.status == "queued" for row in rows)
    assert all(row.lease_token is None for row in rows)
    assert all(row.attempts == 0 for row in rows)


async def test_a_refused_batch_is_not_retried_one_document_at_a_time(factory):
    """A provider saying no is not sixty-four documents asking to be embedded.

    A rate limit or a timeout says nothing about the contents of the batch, so
    falling back to one call per document turns a single refused request into
    sixty-four more against a provider already refusing - and spends an attempt
    on every item on the way to retiring them all.
    """
    from app.catalog import indexing
    from app.common.models import IndexWorkItem

    await _import()
    experience = await _only(factory)

    class _RateLimited(_StubEmbedder):
        async def embed_many(self, texts: list[str]) -> list[list[float]]:
            self.batches += 1
            raise RuntimeError("rate limited")

    throttled = _RateLimited()
    throttled_error = type("RateLimitError", (RuntimeError,), {})

    async def refuse(texts: list[str]) -> list[list[float]]:
        throttled.batches += 1
        raise throttled_error("429 too many requests")

    throttled.embed_many = refuse  # type: ignore[method-assign]

    completed = await indexing.process_index_work(factory, throttled, limit=indexing.DRAIN_BATCH)

    assert completed == 0
    assert throttled.batches == 1
    # Not one call per document, and not one attempt per document either.
    assert throttled.calls == 0

    async with factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(IndexWorkItem).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).all()
        )
    assert rows
    assert all(row.status == "queued" for row in rows)
    assert all(row.attempts == 0 for row in rows)


async def test_re_enqueueing_unchanged_content_creates_no_work_at_all(factory):
    """Why a redeploy does not pay to rebuild a catalogue nobody edited.

    Seeding and importing touch every row on every deploy, so something has to
    stop an untouched product from being re-embedded eight times for nothing.
    That guard lives in the *enqueue* path, not in the worker: a locale whose
    stored document already carries the desired fingerprint is never queued, so
    the worker is never asked.

    Pinned here because the saving is invisible - it shows up as work that does
    not happen - and because a plausible-looking change to `enqueue_reindex`
    could remove it without breaking a single other test. A production deploy
    spends roughly one and a half seconds per document; the difference between
    this working and not is minutes.
    """
    from app.catalog.indexing import (
        enqueue_reindex,
        index_fingerprint,
        process_index_work,
        resolved_document_text,
    )
    from app.common.models import Destination, Experience, IndexWorkItem

    await _import()
    experience = await _only(factory)

    class _CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, text: str) -> list[float]:
            self.calls += 1
            return [0.02] * 512

    provider = _CountingProvider()
    await process_index_work(factory, provider, limit=50)
    assert provider.calls > 0, "the first build must actually embed something"

    # Exactly what a redeploy does: ask again for the documents that already
    # exist, with the fingerprints they already carry.
    async with factory() as db:
        row = (
            await db.execute(
                select(Experience, Destination.name)
                .join(Destination, Destination.id == Experience.destination_id)
                .where(Experience.id == experience.id)
            )
        ).first()
        assert row is not None
        for locale in SUPPORTED_LOCALES:
            text = await resolved_document_text(db, row[0], row[1], locale)
            await enqueue_reindex(db, experience.id, locale, index_fingerprint(text, locale))
        await db.commit()

    async with factory() as db:
        pending = list(
            (
                await db.execute(
                    select(IndexWorkItem.status).where(
                        IndexWorkItem.experience_id == experience.id,
                        IndexWorkItem.status != "done",
                    )
                )
            ).scalars()
        )
    assert pending == [], f"unchanged content queued {len(pending)} items"

    provider.calls = 0
    await process_index_work(factory, provider, limit=50)
    assert provider.calls == 0, f"re-indexing unchanged text cost {provider.calls} embedding calls"


async def test_a_real_edit_still_re_embeds(factory):
    """The guard against the guard.

    Whatever stops unchanged content from being rebuilt must not also stop
    changed content. Skipping too eagerly would leave search describing text
    nobody can see any more, with an empty queue and no error - invisible from
    every angle except a shopper's search results.
    """
    from app.catalog.indexing import process_index_work
    from app.common.models import ExperienceSearchDocument

    await _import()
    experience = await _only(factory)

    class _CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, text: str) -> list[float]:
            self.calls += 1
            return [0.03] * 512

    provider = _CountingProvider()
    await process_index_work(factory, provider, limit=50)

    await catalog_ops.update_experience(
        experience.id, {"title": "Hoi An Basket Boat Ride"}, OPERATOR
    )
    provider.calls = 0
    await process_index_work(factory, provider, limit=50)
    assert provider.calls > 0, "an edited title must be re-embedded"

    async with factory() as db:
        texts = list(
            (
                await db.execute(
                    select(ExperienceSearchDocument.document_text).where(
                        ExperienceSearchDocument.experience_id == experience.id
                    )
                )
            ).scalars()
        )
    assert texts and all("Basket Boat" in text for text in texts)
