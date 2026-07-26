"""The publish gate, against a real PostgreSQL server.

`docs/CONTENT_PIPELINE.md` §5.3. These exist because production proved the gate
was needed: three listings went live with an empty `description`, and
`resolution_chain` documents itself as safe "because the publish gate requires
the source locale complete" - a guarantee nothing was enforcing.

The filter tests matter as much as the gate tests. A gate only guards the
transition, so a catalogue that is already broken stays broken and invisible;
`unpublishable_now` is what makes the existing damage findable.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import catalog_ops, settings_ops
from app.admin.auth import Principal
from app.catalog import importer
from app.catalog.importer import upsert_catalog
from app.catalog.indexing import FALLBACK_EMBEDDING_MODEL
from app.catalog.publish_gate import publish_blockers
from app.catalog.trippass import to_catalog_product
from app.common import runtime_config
from app.common.errors import ApiError
from app.common.models import (
    AvailabilitySlot,
    Experience,
    ExperienceMedia,
    ExperienceOption,
    ExperienceSearchDocument,
    IndexWorkItem,
    OptionPrice,
    Supplier,
)

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

OPERATOR = Principal(id="bootstrap", email="ops@vietra.test", name="Ops", role="admin")

RAW = {
    "product_id": "GATE1",
    "name": "Marble Mountains Half Day",
    "description": "A half day at the Marble Mountains, with a licensed guide.",
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
    "meeting_point": "Hotel pickup at 08:00.",
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


async def _seed() -> None:
    await upsert_catalog(
        [to_catalog_product(RAW, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )


async def _only(factory) -> Experience:
    async with factory() as db:
        experience = await db.scalar(select(Experience))
    assert experience is not None
    return experience


async def _codes(factory, experience_id) -> set[str]:
    async with factory() as db:
        experience = await db.get(Experience, experience_id)
        assert experience is not None
        return {item.code for item in await publish_blockers(db, experience)}


async def test_a_complete_listing_publishes(factory):
    """The gate has to let the good case through, or nothing below is evidence.

    Every other test here asserts a refusal, and a gate that refused everything
    would pass all of them.
    """
    await _seed()
    experience = await _only(factory)
    assert await _codes(factory, experience.id) == set()

    result = await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR)
    assert result["status"] == "PUBLISHED"


async def test_the_production_defect_is_refused(factory):
    """Exactly what went live: PUBLISHED with an empty description.

    Three of these are in production. `short_description` was populated, so
    the grid looked fine; the missing text only shows up in lexical retrieval
    and in what the assistant has to reason about.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(description="   ")
        )

    assert "missing-description" in await _codes(factory, experience.id)

    # Held first, because the importer publishes on its own and the row is
    # already PUBLISHED - which is how the production three got there.
    await catalog_ops.set_status(experience.id, "PENDING_REVIEW", OPERATOR, "no copy")

    with pytest.raises(ApiError) as raised:
        await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR)
    assert raised.value.status == 409
    assert raised.value.code == "publish-blocked"

    async with factory() as db:
        after = await db.get(Experience, experience.id)
    assert after is not None
    assert after.status == "PENDING_REVIEW"


async def test_every_reason_is_reported_not_just_the_first(factory):
    """An operator told one problem at a time stops believing the list.

    The spec is explicit that refusals are itemised. This breaks four unrelated
    things at once and requires all four back.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience)
            .where(Experience.id == experience.id)
            .values(description="", meeting_point="", duration_minutes=0)
        )
        await db.execute(
            delete(ExperienceMedia).where(ExperienceMedia.experience_id == experience.id)
        )

    codes = await _codes(factory, experience.id)
    assert {
        "missing-description",
        "missing-meeting-point",
        "missing-duration-minutes",
        "no-image",
    } <= codes

    with pytest.raises(ApiError) as raised:
        await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR)
    reported = {item["code"] for item in raised.value.details["blockers"]}
    assert reported == codes
    # The prose has to carry them too: a client that only renders `detail`
    # must not show a refusal with one reason in it.
    assert "description" in raised.value.detail
    assert "image" in raised.value.detail


async def test_an_adult_price_on_a_deactivated_option_does_not_count(factory):
    """The active flag is on the option, not on the price.

    Checking "has an active option" and "has an adult price" separately passes
    a listing whose only adult price hangs off a deactivated option - and
    `catalog/service.py` then returns 409 on its product page.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(ExperienceOption)
            .where(ExperienceOption.experience_id == experience.id)
            .values(active=False)
        )

    assert "no-adult-price" in await _codes(factory, experience.id)


async def test_a_child_only_price_does_not_count(factory):
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        option = await db.scalar(
            select(ExperienceOption).where(ExperienceOption.experience_id == experience.id)
        )
        assert option is not None
        await db.execute(
            update(OptionPrice)
            .where(OptionPrice.option_id == option.id)
            .values(participant_type="child")
        )

    assert "no-adult-price" in await _codes(factory, experience.id)


async def test_a_listing_with_no_options_is_told_to_add_one(factory):
    """A different remedy from "no adult price", so a different blocker."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        option_ids = (
            await db.scalars(
                select(ExperienceOption.id).where(ExperienceOption.experience_id == experience.id)
            )
        ).all()
        await db.execute(delete(OptionPrice).where(OptionPrice.option_id.in_(option_ids)))
        await db.execute(delete(AvailabilitySlot).where(AvailabilitySlot.option_id.in_(option_ids)))
        await db.execute(
            delete(ExperienceOption).where(ExperienceOption.experience_id == experience.id)
        )

    codes = await _codes(factory, experience.id)
    assert "no-option" in codes
    assert "no-adult-price" not in codes


async def test_the_house_supplier_cannot_reach_a_shopper(factory):
    """The placeholder exists so drafts can be saved, not so they can sell."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Supplier)
            .where(Supplier.id == experience.supplier_id)
            .values(is_placeholder=True)
        )

    assert "placeholder-supplier" in await _codes(factory, experience.id)


async def test_a_listing_nobody_can_find_is_not_published(factory):
    """§1.4, which is the defect this whole document exists to stop."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            delete(ExperienceSearchDocument).where(
                ExperienceSearchDocument.experience_id == experience.id
            )
        )

    assert "not-indexed" in await _codes(factory, experience.id)


async def test_placeholder_vectors_are_not_a_search_document(factory):
    """A provider outage must delay a publish, not certify meaningless numbers.

    Reported separately from staleness because reindexing will not fix it -
    the document would come back just as placeholder as it went in.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(ExperienceSearchDocument)
            .where(ExperienceSearchDocument.experience_id == experience.id)
            .values(embedding_model=FALLBACK_EMBEDDING_MODEL)
        )

    codes = await _codes(factory, experience.id)
    assert "placeholder-embedding" in codes
    assert "stale-index" not in codes


async def test_text_that_has_moved_past_its_index_is_stale(factory):
    """Publishing on a document that describes an older version of the text."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience)
            .where(Experience.id == experience.id)
            .values(description="Something else entirely, never indexed.")
        )

    assert "stale-index" in await _codes(factory, experience.id)


async def test_the_gate_does_not_block_holding_or_retiring(factory):
    """Only publication is gated.

    Requiring a complete record to *withdraw* a broken one would trap exactly
    the listings an operator most needs to pull.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(description="")
        )

    result = await catalog_ops.set_status(experience.id, "PENDING_REVIEW", OPERATOR, "no copy")
    assert result["status"] == "PENDING_REVIEW"


async def test_already_published_damage_is_findable_in_one_query(factory):
    """The point of the filter.

    The gate guards the transition, so a listing that went live before the gate
    existed stays live. Production's three were found by fetching all 379
    records one at a time, which is not something an operator can do - so the
    same rules have to be a filter, not only a check.
    """
    await _seed()
    experience = await _only(factory)
    await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR)

    # It went live complete, so the filter must not accuse it.
    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert listed["total"] == 0

    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(description="")
        )

    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert [row["id"] for row in listed["items"]] == [str(experience.id)]
    # And the healthy side of the filter must be the complement, not everything.
    healthy = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=False))
    assert healthy["total"] == 0


async def test_the_filter_finds_a_listing_with_no_image(factory):
    """The EXISTS subqueries are the half most likely to be silently wrong."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            delete(ExperienceMedia).where(ExperienceMedia.experience_id == experience.id)
        )

    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert [row["id"] for row in listed["items"]] == [str(experience.id)]


async def test_the_filter_finds_a_listing_with_no_sellable_price(factory):
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(ExperienceOption)
            .where(ExperienceOption.experience_id == experience.id)
            .values(active=False)
        )

    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert [row["id"] for row in listed["items"]] == [str(experience.id)]


async def test_the_filter_reads_the_records_own_source_language(factory):
    """A Vietnamese listing is complete when its Vietnamese document exists.

    Hardcoding `en` here would report every non-English listing as unindexed -
    the same wrong-locale assumption that caused the original defect.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(source_language="vi")
        )
        await db.execute(
            update(ExperienceSearchDocument)
            .where(ExperienceSearchDocument.experience_id == experience.id)
            .values(locale="vi")
        )

    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert listed["total"] == 0

    async with factory() as db, db.begin():
        await db.execute(
            update(ExperienceSearchDocument)
            .where(ExperienceSearchDocument.experience_id == experience.id)
            .values(locale="en")
        )

    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert [row["id"] for row in listed["items"]] == [str(experience.id)]


async def test_the_detail_payload_carries_the_blockers(factory):
    """So the console can point at each one without parsing English."""
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        await db.execute(
            update(Experience).where(Experience.id == experience.id).values(description="")
        )

    detail = await catalog_ops.get_experience(experience.id)
    codes = [item["code"] for item in detail["publish_blockers"]]
    assert "missing-description" in codes
    # Deleting the text also moves the document it was built from, so the
    # stale index is a real second blocker rather than noise.
    assert "stale-index" in codes
    assert detail["source_language"] == "en"


async def test_a_missing_experience_is_still_a_404(factory):
    with pytest.raises(ApiError) as raised:
        await catalog_ops.set_status(uuid4(), "PUBLISHED", OPERATOR)
    assert raised.value.status == 404


async def test_a_zero_amount_adult_price_is_not_a_price(factory):
    """Free is a decision somebody makes deliberately, not a missing number.

    An importer that failed to parse a price writes 0, and a listing that sells
    for nothing is a worse outcome than one that will not publish.
    """
    await _seed()
    experience = await _only(factory)
    async with factory() as db, db.begin():
        option = await db.scalar(
            select(ExperienceOption).where(ExperienceOption.experience_id == experience.id)
        )
        assert option is not None
        await db.execute(
            update(OptionPrice).where(OptionPrice.option_id == option.id).values(amount=Decimal(0))
        )

    assert "no-adult-price" in await _codes(factory, experience.id)


async def test_the_importer_cannot_publish_what_the_gate_refuses(factory):
    """The path that actually put the three broken listings into production.

    `upsert_catalog` assigns `status` straight onto the row and never calls
    `set_status`, so a gate that lived only on the operator endpoint would have
    passed every test in this file and stopped nothing at all. The first
    version of this feature was exactly that.
    """
    raw = {**RAW, "description": ""}
    facets = {**FACETS, "short_description": ""}
    await upsert_catalog(
        [to_catalog_product(raw, facets, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )

    experience = await _only(factory)
    assert experience.status == "PENDING_REVIEW"
    assert experience.needs_review is True
    # The reason has to travel with it, or the operator gets a listing in a
    # queue with no explanation of what to do to it.
    assert "description" in experience.review_note


async def test_a_complete_product_still_imports_published(factory):
    """The gate must not hold the healthy catalogue.

    Without this the test above passes just as well against an importer that
    refuses everything.
    """
    result = await upsert_catalog(
        [to_catalog_product(RAW, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )
    experience = await _only(factory)
    assert experience.status == "PUBLISHED"
    assert result["held"] == 0


class FailingEmbeddingProvider:
    """A provider whose embedding call is down, as it was in the incident."""

    async def embed(self, text: str) -> list[float]:
        raise RuntimeError("embedding provider is down")

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding provider is down")

    async def extract_intent(self, text: str, **_):
        raise NotImplementedError

    async def plan_action(self, text: str, state: dict) -> str | None:
        return None

    async def enhance_assistant(self, prompt: str, facts: list[dict]) -> str | None:
        return None


async def test_an_import_that_falls_back_to_a_placeholder_vector_is_held(factory):
    """A complete listing nobody can find by meaning is still unsellable.

    The importer skips the fingerprint checks because this transaction is what
    writes the document, and the first version skipped the embedding check with
    them. That let a provider outage publish a full catalogue of listings whose
    vectors are deterministic placeholders - present in the grid, absent from
    every semantic search, and indistinguishable from healthy rows.

    Everything else about this product is complete, so the hold can only be
    coming from the placeholder vector.
    """
    result = await upsert_catalog(
        [to_catalog_product(RAW, FACETS, days=3)],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
        ai_provider=FailingEmbeddingProvider(),
    )

    experience = await _only(factory)
    assert experience.status == "PENDING_REVIEW"
    assert result["held"] == 1
    assert "placeholder" in (experience.review_note or "")


async def test_a_held_import_is_counted_for_the_operator(factory):
    """An import that quietly holds half the feed is an import nobody trusts."""
    result = await upsert_catalog(
        [
            to_catalog_product(
                {**RAW, "description": ""}, {**FACETS, "short_description": ""}, days=3
            )
        ],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )
    assert result["held"] == 1


async def test_an_operators_published_ruling_survives_a_later_import(factory):
    """The gate does not get to demote what a human decided.

    `_apply` treats a human status ruling as protected, and reopening that
    question on every import is the refilling review queue the importer
    documents itself as avoiding. The listing stays live and shows up in the
    `incomplete` filter instead, which is where an operator can act on it.
    """
    await _seed()
    experience = await _only(factory)
    await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR, "checked by hand")

    await upsert_catalog(
        [
            to_catalog_product(
                {**RAW, "description": ""}, {**FACETS, "short_description": ""}, days=3
            )
        ],
        supplier_external_id="TRIPPASS",
        supplier_name="Trippass",
    )

    after = await _only(factory)
    assert after.status == "PUBLISHED"
    listed = await catalog_ops.list_experiences(catalog_ops.CatalogQuery(incomplete=True))
    assert [row["id"] for row in listed["items"]] == [str(experience.id)]


async def test_a_scheduled_rebuild_is_not_a_finished_one(factory):
    """A committed intent to reindex is not a search document.

    This test used to assert the opposite. The reasoning was that editing a
    title makes the document stale by definition, so refusing the operator who
    fixes a typo makes the gate one people route around. The review overruled
    it: the outbox guarantees the work was *enqueued*, and a worker that later
    exhausts its retries reaches the very state the gate exists to prevent -
    live inventory no shopper can find - just more slowly.

    So an edit blocks publication until the rebuild lands, and the console
    shows why.
    """
    await _seed()
    experience = await _only(factory)
    await catalog_ops.set_status(experience.id, "PENDING_REVIEW", OPERATOR, "checking")

    await catalog_ops.update_experience(
        experience.id, {"title": "Marble Mountains, Half Day"}, OPERATOR
    )

    async with factory() as db:
        scheduled = await db.scalar(
            select(IndexWorkItem).where(
                IndexWorkItem.experience_id == experience.id,
                IndexWorkItem.locale == "en",
                IndexWorkItem.status.in_(("queued", "leased")),
            )
        )
    assert scheduled is not None, "a rebuild must be pending for this to mean anything"

    assert await _codes(factory, experience.id) == {"stale-index"}
    with pytest.raises(ApiError) as refused:
        await catalog_ops.set_status(experience.id, "PUBLISHED", OPERATOR)
    assert refused.value.status == 409
    assert "stale-index" in {
        blocker["code"] for blocker in (refused.value.details or {})["blockers"]
    }


async def test_a_failed_rebuild_is_not_a_promise_to_rebuild(factory):
    """`failed` is terminal and invisible to the leasing query.

    Treating it as scheduled would publish a listing whose document is stale
    and whose repair has already given up - the exact state the rule exists to
    catch, wearing the costume of the state that is fine.
    """
    await _seed()
    experience = await _only(factory)
    await catalog_ops.set_status(experience.id, "PENDING_REVIEW", OPERATOR, "checking")
    await catalog_ops.update_experience(
        experience.id, {"title": "Marble Mountains, Half Day"}, OPERATOR
    )
    async with factory() as db, db.begin():
        await db.execute(
            update(IndexWorkItem)
            .where(IndexWorkItem.experience_id == experience.id)
            .values(status="failed")
        )

    assert "stale-index" in await _codes(factory, experience.id)
