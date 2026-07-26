"""The review queue, against a real PostgreSQL server.

§6.5 holds `meeting_point` back from the storefront because a mistranslated set
of directions sends a traveller to the wrong place. The worker implemented the
holding faithfully and nothing implemented the release, so `needs_review` was a
terminal state: no code path a running system could reach ever moved a field
out of it, `reviewed_by` was never written, and the `rejected` status in the
CHECK constraint was unreachable. Every non-English locale therefore sat at
exactly one unpublished field per experience, permanently, while the coverage
report described that as a backlog - which implies somebody could work it.

These tests are about the two things that can go wrong once it *is* workable:
publishing text the reviewer did not read, and publishing text that never
becomes searchable.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.models import (
    Destination,
    Experience,
    ExperienceTranslation,
    IndexWorkItem,
    Supplier,
    TranslationField,
)
from app.content.enqueue import ENTITY_EXPERIENCE
from app.content.review import (
    ReviewConflict,
    approve_candidate,
    edit_translation,
    reject_candidate,
    review_queue,
)

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

SUPPLIER_ID = uuid4()
DESTINATION_ID = uuid4()
EXPERIENCE_ID = uuid4()

CANDIDATE = "Cầu Nhật Bản"
FINGERPRINT = "f" * 64


@pytest.fixture
async def factory():
    engine = create_async_engine(DATABASE_URL)
    await create_postgres_schema(engine)
    await reset_postgres(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(
            Supplier(id=SUPPLIER_ID, external_id=f"sup-{SUPPLIER_ID.hex[:8]}", name="Test Supplier")
        )
        session.add(
            Destination(
                id=DESTINATION_ID,
                slug=f"dest-{DESTINATION_ID.hex[:8]}",
                name="Hoi An",
                country_code="VN",
                latitude=Decimal("15.88"),
                longitude=Decimal("108.33"),
                timezone="Asia/Ho_Chi_Minh",
            )
        )
        await session.flush()
        session.add(
            Experience(
                id=EXPERIENCE_ID,
                source_type="manual",
                supplier_id=SUPPLIER_ID,
                destination_id=DESTINATION_ID,
                slug="hoi-an-lantern-walk",
                title="Hoi An lantern walk",
                short_description="An evening walk",
                description="A guided evening walk.",
                category="tour",
                indoor_outdoor="outdoor",
                duration_minutes=90,
                latitude=Decimal("15.88"),
                longitude=Decimal("108.33"),
                meeting_point="Japanese Bridge",
                source_language="en",
            )
        )
        await session.commit()

    yield session_factory
    await engine.dispose()


async def _held(factory, *, fingerprint=FINGERPRINT, generation=0, candidate=CANDIDATE):
    """A `meeting_point` candidate in exactly the state the worker leaves it."""
    async with factory() as session:
        session.add(
            TranslationField(
                entity_type=ENTITY_EXPERIENCE,
                entity_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                candidate_value=candidate,
                candidate_fingerprint=fingerprint,
                desired_fingerprint=fingerprint,
                published_fingerprint=None,
                status="needs_review",
                provenance="machine",
                generation=generation,
            )
        )
        await session.commit()


async def _state(factory):
    async with factory() as session:
        return await session.get(
            TranslationField, (ENTITY_EXPERIENCE, EXPERIENCE_ID, "meeting_point", "vi")
        )


async def _served(factory):
    async with factory() as session:
        row = await session.scalar(
            select(ExperienceTranslation).where(
                ExperienceTranslation.experience_id == EXPERIENCE_ID,
                ExperienceTranslation.locale == "vi",
            )
        )
        return row.meeting_point if row else None


async def _reindex_locales(factory):
    async with factory() as session:
        items = (
            await session.scalars(
                select(IndexWorkItem).where(IndexWorkItem.experience_id == EXPERIENCE_ID)
            )
        ).all()
        return [item.locale for item in items]


class TestTheQueueIsReadable:
    async def test_a_held_candidate_appears_with_its_source(self, factory):
        """A reviewer judges a *pair*. The source alone is not reviewable."""
        await _held(factory)
        async with factory() as session:
            queue = await review_queue(session, locale="vi")

        assert queue.total == 1
        assert queue.by_locale == {"vi": 1}
        item = queue.items[0]
        assert item.candidate_value == CANDIDATE
        assert item.source_text == "Japanese Bridge"
        assert item.source_language == "en"
        assert item.answers_current_source is True

    async def test_a_candidate_whose_source_moved_is_flagged_not_hidden(self, factory):
        """Hiding it would leave a field permanently unpublished and invisible.

        The candidate is stale, so approving it must fail - but a reviewer who
        cannot see it has no way to know the field needs anything at all.
        """
        await _held(factory)
        async with factory() as session:
            row = await _state(factory)
            row.desired_fingerprint = "a" * 64
            await session.merge(row)
            await session.commit()

        async with factory() as session:
            queue = await review_queue(session, locale="vi")

        assert queue.total == 1
        assert queue.items[0].answers_current_source is False

    async def test_published_fields_are_not_in_the_queue(self, factory):
        await _held(factory)
        async with factory() as session:
            await approve_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        async with factory() as session:
            assert (await review_queue(session)).total == 0


class TestApprovalPublishes:
    async def test_the_candidate_becomes_the_served_text(self, factory):
        await _held(factory)
        async with factory() as session:
            published = await approve_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        assert published == CANDIDATE
        assert await _served(factory) == CANDIDATE

        state = await _state(factory)
        assert state.status == "current"
        assert state.published_fingerprint == FINGERPRINT
        assert state.reviewed_by == "ops@vietra.test"
        # Cleared, or the queue would keep offering a decided field.
        assert state.candidate_value is None

    async def test_approval_makes_it_searchable_too(self, factory):
        """The half that goes missing without anything failing.

        A translation published into the wide table but never reindexed reads
        correctly on the product page and reports itself `current`, while the
        search document for that locale still holds the English. The product is
        translated and unfindable - the same outcome as not translating it.
        """
        await _held(factory)
        async with factory() as session:
            await approve_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        assert "vi" in await _reindex_locales(factory)


class TestApprovalIsConditionalOnWhatWasSeen:
    async def test_a_replaced_candidate_is_refused(self, factory):
        """The reviewer approved a string. Only that string may be published."""
        await _held(factory)
        async with factory() as session:
            row = await _state(factory)
            row.candidate_value = "Somewhere else entirely"
            row.candidate_fingerprint = "b" * 64
            row.desired_fingerprint = "b" * 64
            await session.merge(row)
            await session.commit()

        async with factory() as session:
            with pytest.raises(ReviewConflict) as caught:
                await approve_candidate(
                    session,
                    experience_id=EXPERIENCE_ID,
                    field="meeting_point",
                    locale="vi",
                    reviewer="ops@vietra.test",
                    expected_fingerprint=FINGERPRINT,
                    expected_generation=0,
                )
        assert caught.value.reason == "candidate_replaced"
        assert await _served(factory) is None

    async def test_an_edited_source_is_refused_even_when_it_reverts(self, factory):
        """The ABA case, which fingerprints alone cannot see.

        An operator edits a meeting point and changes their mind: F0 -> F1 ->
        F0. The second F0 enqueues a fresh job and the model produces a
        *different* Vietnamese sentence, under a fingerprint identical to the
        one the reviewer approved. Without the generation, the click publishes
        a string nobody read.
        """
        await _held(factory)
        async with factory() as session:
            row = await _state(factory)
            row.candidate_value = "A different sentence for the same English"
            row.generation = 2
            await session.merge(row)
            await session.commit()

        async with factory() as session:
            with pytest.raises(ReviewConflict) as caught:
                await approve_candidate(
                    session,
                    experience_id=EXPERIENCE_ID,
                    field="meeting_point",
                    locale="vi",
                    reviewer="ops@vietra.test",
                    expected_fingerprint=FINGERPRINT,
                    expected_generation=0,
                )
        assert caught.value.reason == "source_changed"
        assert await _served(factory) is None

    async def test_a_stale_candidate_is_refused(self, factory):
        """It is a translation of text no shopper will ever be shown."""
        await _held(factory)
        async with factory() as session:
            row = await _state(factory)
            row.desired_fingerprint = "c" * 64
            await session.merge(row)
            await session.commit()

        async with factory() as session:
            with pytest.raises(ReviewConflict) as caught:
                await approve_candidate(
                    session,
                    experience_id=EXPERIENCE_ID,
                    field="meeting_point",
                    locale="vi",
                    reviewer="ops@vietra.test",
                    expected_fingerprint=FINGERPRINT,
                    expected_generation=0,
                )
        assert caught.value.reason == "source_changed"

    async def test_approving_twice_publishes_once(self, factory):
        await _held(factory)
        async with factory() as session:
            await approve_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        async with factory() as session:
            with pytest.raises(ReviewConflict) as caught:
                await approve_candidate(
                    session,
                    experience_id=EXPERIENCE_ID,
                    field="meeting_point",
                    locale="vi",
                    reviewer="someone.else@vietra.test",
                    expected_fingerprint=FINGERPRINT,
                    expected_generation=0,
                )
        assert caught.value.reason == "already_decided"


class TestRejectionLeavesTheFieldRecoverable:
    async def test_rejection_publishes_nothing(self, factory):
        await _held(factory)
        async with factory() as session:
            await reject_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        assert await _served(factory) is None
        state = await _state(factory)
        assert state.status == "rejected"
        assert state.reviewed_by == "ops@vietra.test"
        assert state.published_fingerprint is None

    async def test_a_reviewer_can_find_a_rejected_field_and_fix_it(self, factory):
        """The recovery has to be reachable the way an operator reaches it.

        An earlier version of this test rejected a candidate and then called
        `edit_translation` directly, and passed - while the console listed only
        `needs_review`, so the rejected field was invisible and the escape
        hatch it certified could not be opened by the person it exists for.
        That is the defect of this whole subsystem repeated one layer up, in
        the test written to prove the subsystem was fixed.

        So this goes through the queue.
        """
        await _held(factory)
        async with factory() as session:
            await reject_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        async with factory() as session:
            recoverable = await review_queue(session, state="rejected")

        assert recoverable.total == 1, "a rejected field the console cannot list is stranded"
        item = recoverable.items[0]
        assert item.status == "rejected"
        assert item.reviewed_by == "ops@vietra.test"
        # The source is what the reviewer translates from, so it must be here.
        assert item.source_text == "Japanese Bridge"
        # Nothing to approve; the only way forward is to write it.
        assert item.answers_current_source is False

        async with factory() as session:
            await edit_translation(
                session,
                experience_id=EXPERIENCE_ID,
                field=item.field,
                locale=item.locale,
                reviewer="ops@vietra.test",
                value="Chân cầu Chùa Cầu, phía đường Trần Phú",
                expected_generation=item.generation,
            )
            await session.commit()

        assert await _served(factory) == "Chân cầu Chùa Cầu, phía đường Trần Phú"
        state = await _state(factory)
        assert state.status == "current"
        # Machine provenance would let the next worker overwrite it.
        assert state.provenance == "manual"
        assert state.published_fingerprint == state.desired_fingerprint
        assert "vi" in await _reindex_locales(factory)

        # And it leaves the recoverable list, or the operator works it forever.
        async with factory() as session:
            assert (await review_queue(session, state="rejected")).total == 0

    async def test_an_edit_against_a_moved_source_is_refused(self, factory):
        """The reviewer translates the source on their screen.

        If an operator moves the tour to a different bridge while that screen
        is open, the sentence being typed is a faithful translation of
        directions to the wrong place - and publishing it as `manual` then
        protects it from ever being corrected by the machine.
        """
        await _held(factory)
        async with factory() as session:
            row = await _state(factory)
            row.generation = 3
            await session.merge(row)
            await session.commit()

        async with factory() as session:
            with pytest.raises(ReviewConflict) as caught:
                await edit_translation(
                    session,
                    experience_id=EXPERIENCE_ID,
                    field="meeting_point",
                    locale="vi",
                    reviewer="ops@vietra.test",
                    value="Chân cầu Chùa Cầu",
                    expected_generation=0,
                )
        assert caught.value.reason == "source_changed"
        assert await _served(factory) is None

    async def test_a_rejected_field_is_not_offered_for_approval(self, factory):
        """Out of the approval queue, but not out of sight - see above."""
        await _held(factory)
        async with factory() as session:
            await reject_candidate(
                session,
                experience_id=EXPERIENCE_ID,
                field="meeting_point",
                locale="vi",
                reviewer="ops@vietra.test",
                expected_fingerprint=FINGERPRINT,
                expected_generation=0,
            )
            await session.commit()

        async with factory() as session:
            assert (await review_queue(session)).total == 0
