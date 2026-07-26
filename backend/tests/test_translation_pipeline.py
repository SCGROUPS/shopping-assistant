"""The translation pipeline against a real PostgreSQL server.

Every test here is about a *lost write*, because that is the only interesting
failure mode in this subsystem. A translation that is merely late is fine; a
translation that overwrites a human's correction, or that silently never gets
enqueued because a fingerprint came back around to a value it once had, is not
recoverable by retrying.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.models import (
    Destination,
    Experience,
    ExperienceTranslation,
    IndexWorkItem,
    Supplier,
    TranslationField,
    TranslationGlossary,
    TranslationJob,
)
from app.content.enqueue import (
    enqueue_experience_translations,
    mark_manual_translation,
)
from app.content.translator import (
    GlossaryViolation,
    commit_translation,
    drain,
    lease_jobs,
    verify_glossary,
)

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

SUPPLIER_ID = uuid4()
DESTINATION_ID = uuid4()
EXPERIENCE_ID = uuid4()


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
                description="A guided evening walk through the old town of Hoi An.",
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


async def _fields(factory, **filters):
    async with factory() as session:
        stmt = select(TranslationField).where(
            TranslationField.entity_id == EXPERIENCE_ID,
            *[getattr(TranslationField, key) == value for key, value in filters.items()],
        )
        return list((await session.scalars(stmt)).all())


async def _jobs(factory, **filters):
    async with factory() as session:
        stmt = select(TranslationJob).where(
            TranslationJob.entity_id == EXPERIENCE_ID,
            *[getattr(TranslationJob, key) == value for key, value in filters.items()],
        )
        return list((await session.scalars(stmt)).all())


async def _enqueue(factory, locales=None):
    async with factory() as session:
        count = await enqueue_experience_translations(session, EXPERIENCE_ID, locales=locales)
        await session.commit()
        return count


async def test_reimporting_unchanged_content_enqueues_nothing(factory):
    """The catalogue re-imports constantly. Only *changes* may cost money."""
    first = await _enqueue(factory)
    assert first == 4 * 7  # four fields, seven target locales

    second = await _enqueue(factory)
    assert second == 0
    assert len(await _jobs(factory)) == 28


async def test_editing_the_source_bumps_generation_and_reopens(factory):
    await _enqueue(factory)

    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(title="Hoi An lantern cruise")
        )
        await session.commit()

    assert await _enqueue(factory) == 7

    titles = await _fields(factory, field="title")
    assert {row.generation for row in titles} == {1}
    assert {row.status for row in titles} == {"pending"}
    # The other three fields did not move, so they must not have been touched.
    assert {row.generation for row in await _fields(factory, field="description")} == {0}


async def test_a_fingerprint_that_comes_back_around_still_enqueues(factory):
    """F0 -> F1 -> F0 must produce work, not collide with the completed F0 job.

    This is the case the `generation` column in the uniqueness key exists for.
    Without it, the third enqueue finds the first job still present, does
    nothing, and the field stays stale with nothing to notice.
    """
    original = "Hoi An lantern walk"
    await _enqueue(factory)

    async with factory() as session:
        await session.execute(
            update(Experience).where(Experience.id == EXPERIENCE_ID).values(title="Something else")
        )
        await session.commit()
    await _enqueue(factory)

    async with factory() as session:
        await session.execute(
            update(Experience).where(Experience.id == EXPERIENCE_ID).values(title=original)
        )
        await session.commit()

    assert await _enqueue(factory) == 7

    vi_title = await _fields(factory, field="title", locale="vi")
    assert vi_title[0].generation == 2
    # Three distinct jobs for the same fingerprint pair, one per generation.
    vi_jobs = await _jobs(factory, field="title", locale="vi")
    assert len(vi_jobs) == 3
    assert {job.generation for job in vi_jobs} == {0, 1, 2}


async def test_a_glossary_bump_invalidates_machine_but_not_manual(factory):
    """A new glossary revision must never mark a human's translation stale."""
    await _enqueue(factory)

    async with factory() as session:
        await mark_manual_translation(
            session,
            experience_id=EXPERIENCE_ID,
            field="title",
            locale="ko",
            editor="translator@vietra.local",
        )
        await session.commit()

    async with factory() as session:
        session.add(
            TranslationGlossary(
                term="Hoi An", target_locale="ko", do_not_translate=True, revision=2
            )
        )
        await session.commit()

    await _enqueue(factory)

    manual = (await _fields(factory, field="title", locale="ko"))[0]
    assert manual.provenance == "manual"
    assert manual.status == "current"
    assert manual.published_fingerprint == manual.desired_fingerprint

    machine = (await _fields(factory, field="title", locale="ja"))[0]
    assert machine.status == "pending"
    assert machine.published_fingerprint != machine.desired_fingerprint


async def test_manual_fields_never_get_a_machine_job(factory):
    await _enqueue(factory)
    async with factory() as session:
        await session.execute(TranslationJob.__table__.delete())
        await mark_manual_translation(
            session,
            experience_id=EXPERIENCE_ID,
            field="description",
            locale="fr",
            editor="translator@vietra.local",
        )
        await session.commit()

    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(description="A different walk entirely.")
        )
        await session.commit()

    await _enqueue(factory)

    stale_manual = (await _fields(factory, field="description", locale="fr"))[0]
    # Visibly stale so the console can surface it for a human...
    assert stale_manual.published_fingerprint != stale_manual.desired_fingerprint
    # ...but no machine may be dispatched to overwrite the human.
    assert await _jobs(factory, field="description", locale="fr") == []
    assert len(await _jobs(factory, field="description")) == 6


async def _lease_one(factory, *, field="title", locale="vi"):
    async with factory() as session:
        jobs = await lease_jobs(session, limit=200)
        await session.commit()
    return next(job for job in jobs if job.field == field and job.locale == locale)


async def test_a_superseded_worker_cannot_publish(factory):
    """The race the whole commit protocol exists to lose safely.

    A worker leases a job, the model thinks for a while, and meanwhile a human
    corrects the same field. The worker's translation now answers a title that
    no longer exists, and publishing it would erase the correction.
    """
    await _enqueue(factory)
    job = await _lease_one(factory)

    async with factory() as session:
        await mark_manual_translation(
            session,
            experience_id=EXPERIENCE_ID,
            field="title",
            locale="vi",
            editor="human@vietra.local",
        )
        await session.execute(
            ExperienceTranslation.__table__.insert().values(
                experience_id=EXPERIENCE_ID, locale="vi", title="Dạo bộ đèn lồng Hội An"
            )
        )
        await session.commit()

    async with factory() as session:
        published = await commit_translation(
            session, job, translated="MACHINE TEXT", requires_review=False
        )
        await session.commit()

    assert published is False
    async with factory() as session:
        row = await session.get(ExperienceTranslation, (EXPERIENCE_ID, "vi"))
    assert row is not None and row.title == "Dạo bộ đèn lồng Hội An"


async def test_two_fields_publishing_at_once_do_not_clobber_each_other(factory):
    """Both workers write the same row. Each must write only its own column.

    The two tasks are made to overlap deliberately: each opens a session and
    reads the current row *before* either writes, which is the state a
    whole-row ORM flush would carry into its write. Row locking then serialises
    the writes, and the survivor of a whole-row flush would be whichever
    committed second - with the other's column reset to what it read.

    Written as two tasks rather than two sequential `async with` blocks because
    the second writer blocks on the first writer's uncommitted row, so a single
    coroutine awaiting both in order deadlocks against itself.
    """
    await _enqueue(factory)
    async with factory() as session:
        jobs = await lease_jobs(session, limit=200)
        await session.commit()
    by_target = {(job.field, job.locale): job for job in jobs}

    both_have_read = asyncio.Barrier(2)

    async def publish(job, text):
        async with factory() as session:
            await session.get(ExperienceTranslation, (EXPERIENCE_ID, "de"))
            await both_have_read.wait()
            published = await commit_translation(
                session, job, translated=text, requires_review=False
            )
            await session.commit()
            return published

    results = await asyncio.gather(
        publish(by_target[("title", "de")], "Laternenspaziergang"),
        publish(by_target[("description", "de")], "Ein gef\u00fchrter Abendspaziergang."),
    )
    assert results == [True, True]

    async with factory() as session:
        row = await session.get(ExperienceTranslation, (EXPERIENCE_ID, "de"))
    assert row is not None
    assert row.title == "Laternenspaziergang"
    assert row.description == "Ein gef\u00fchrter Abendspaziergang."


async def test_publishing_enqueues_a_reindex_for_that_locale_only(factory):
    """A translation nobody can search for is a translation nobody will read."""
    await _enqueue(factory)
    job = await _lease_one(factory, field="title", locale="ja")

    async with factory() as session:
        assert await commit_translation(
            session, job, translated="ホイアンのランタン散歩", requires_review=False
        )
        await session.commit()

    async with factory() as session:
        items = list(
            (
                await session.scalars(
                    select(IndexWorkItem).where(IndexWorkItem.experience_id == EXPERIENCE_ID)
                )
            ).all()
        )
    assert {item.locale for item in items} == {"ja"}


async def test_an_expired_lease_returns_the_job_to_the_pool(factory):
    await _enqueue(factory)
    first = await _lease_one(factory)

    async with factory() as session:
        assert await lease_jobs(session, limit=200) == []
        await session.commit()

    async with factory() as session:
        await session.execute(
            update(TranslationJob)
            .where(TranslationJob.id == first.job_id)
            .values(leased_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    async with factory() as session:
        again = await lease_jobs(session, limit=200)
        await session.commit()
    assert [job.job_id for job in again] == [first.job_id]
    assert again[0].attempts == 2


def test_glossary_verification_catches_a_translated_brand():
    terms = [TranslationGlossary(term="Hoi An", target_locale="de", do_not_translate=True)]
    verify_glossary("Ein Abend in Hoi An", terms)
    with pytest.raises(GlossaryViolation):
        verify_glossary("Ein Abend in der Altstadt", terms)


async def test_a_glossary_violation_fails_the_job_without_publishing(factory):
    async with factory() as session:
        session.add(
            TranslationGlossary(
                term="Hoi An", target_locale="vi", do_not_translate=True, revision=1
            )
        )
        await session.commit()
    await _enqueue(factory, locales=["vi"])

    async def bad_translator(*, job, glossary):
        return "Dạo bộ đèn lồng phố cổ"

    counts = await drain(factory, bad_translator, limit=4)
    assert counts["failed"] == 4
    assert counts["published"] == 0

    async with factory() as session:
        row = await session.get(ExperienceTranslation, (EXPERIENCE_ID, "vi"))
    assert row is None


async def test_a_clean_drain_publishes_and_marks_current(factory):
    await _enqueue(factory, locales=["vi"])

    async def good_translator(*, job, glossary):
        return f"[vi] {job.source_text}"

    counts = await drain(factory, good_translator, limit=10)
    assert counts == {"leased": 4, "published": 4, "superseded": 0, "failed": 0}

    async with factory() as session:
        row = await session.get(ExperienceTranslation, (EXPERIENCE_ID, "vi"))
    assert row is not None
    assert row.title == "[vi] Hoi An lantern walk"
    assert row.meeting_point == "[vi] Japanese Bridge"

    assert {field.status for field in await _fields(factory, locale="vi")} == {"current"}
    assert {job.status for job in await _jobs(factory, locale="vi")} == {"done"}

    # And a second drain has nothing to do, because enqueueing is fingerprint
    # driven rather than time driven.
    assert await _enqueue(factory, locales=["vi"]) == 0


async def test_review_mode_holds_the_translation_back(factory):
    """`needs_review` must store the candidate and serve nothing."""
    await _enqueue(factory, locales=["ko"])

    async def good_translator(*, job, glossary):
        return f"[ko] {job.source_text}"

    counts = await drain(factory, good_translator, limit=10, requires_review=True)
    assert counts["published"] == 4

    async with factory() as session:
        row = await session.get(ExperienceTranslation, (EXPERIENCE_ID, "ko"))
    assert row is None  # nothing served

    fields = await _fields(factory, locale="ko")
    assert {field.status for field in fields} == {"needs_review"}
    assert all(field.candidate_value.startswith("[ko] ") for field in fields)
    assert all(field.published_fingerprint is None for field in fields)
    # The candidate names the fingerprint it answered, so a reviewer can prove
    # it still describes the current source before approving it.
    assert all(
        field.candidate_fingerprint == field.desired_fingerprint for field in fields
    )
