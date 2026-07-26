"""Locale-aware resolution at the API boundary (spec 4.3, 9).

The failure this suite guards is not "the wrong string appeared". It is that
*search* and *display* can resolve differently, in which case a shopper finds a
product by words the page never shows, and no test of either side alone
notices. So the resolver is shared, and the first test here is the one that
proves it stays shared.
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog import indexing
from app.common.config import Settings, get_settings
from app.common.locales import negotiate_locale, parse_accept_language
from app.common.models import (
    Destination,
    Experience,
    ExperienceTranslation,
    Supplier,
    TranslationField,
)
from app.common.persistence import load_products
from app.common.resolution import (
    content_meta,
    fallback_fields,
    resolve_experience_text,
    resolution_chain,
)
from app.content.enqueue import enqueue_experience_translations

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
                description="A guided evening walk through the old town.",
                category="tour",
                indoor_outdoor="outdoor",
                duration_minutes=90,
                latitude=Decimal("15.88"),
                longitude=Decimal("108.33"),
                meeting_point="Japanese Bridge",
                source_language="en",
                status="PUBLISHED",
            )
        )
        await session.commit()

    yield session_factory
    await engine.dispose()


async def _publish(factory, locale, **values):
    """Publish a translation the way the worker does: text plus field state."""
    async with factory() as session:
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=[locale])
        session.add(
            ExperienceTranslation(experience_id=EXPERIENCE_ID, locale=locale, **values)
        )
        for field in values:
            row = (
                await session.execute(
                    select(TranslationField).where(
                        TranslationField.entity_id == EXPERIENCE_ID,
                        TranslationField.field == field,
                        TranslationField.locale == locale,
                    )
                )
            ).scalar_one()
            row.published_fingerprint = row.desired_fingerprint
            row.status = "current"
        await session.commit()


async def _experience(factory):
    async with factory() as session:
        return (
            await session.execute(select(Experience).where(Experience.id == EXPERIENCE_ID))
        ).scalar_one()


async def test_the_indexer_resolves_exactly_what_the_page_will_show(factory):
    """One resolver, or search matches text the page never displays.

    This is the whole reason `common/resolution.py` exists. Two
    implementations of "which string does a Vietnamese shopper see" drift the
    moment either changes, and the symptom - a product found by words that
    appear nowhere on it - looks like a relevance problem, not a bug.
    """
    await _publish(
        factory,
        "vi",
        title="Đi bộ đèn lồng Hội An",
        short_description="Dạo bộ buổi tối",
        description="Chuyến đi bộ có hướng dẫn.",
        meeting_point="Chùa Cầu",
    )

    experience = await _experience(factory)
    async with factory() as session:
        indexed = await indexing.resolved_document_text(session, experience, "Hoi An", "vi")
        resolved = await resolve_experience_text(session, [experience], "vi")

    fields = resolved[EXPERIENCE_ID]
    for name in ("title", "short_description", "description"):
        assert fields[name].value in indexed, (
            f"the page would show {name}={fields[name].value!r}, "
            f"which the indexed document does not contain"
        )


async def test_a_translated_field_is_served_and_labelled_as_translated(factory):
    await _publish(factory, "vi", title="Đi bộ đèn lồng Hội An")

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "vi"))[EXPERIENCE_ID]

    assert fields["title"].value == "Đi bộ đèn lồng Hội An"
    assert fields["title"].locale == "vi"
    assert fields["title"].provenance == "machine"
    # Untranslated siblings fall back rather than blanking.
    assert fields["description"].value == "A guided evening walk through the old town."
    assert fields["description"].locale == "en"
    assert fields["description"].provenance == "fallback"
    assert fallback_fields(fields) == ("short_description", "description", "meeting_point")


async def test_an_edited_source_leaves_the_translation_served_but_stale(factory):
    """Stale text is shown, and says so.

    Falling back to English the moment an operator fixes a typo would blank an
    entire locale for every product touched until the worker catches up - a
    far larger blast radius than a description one edit behind. The label is
    what keeps that honest.
    """
    await _publish(factory, "vi", title="Đi bộ đèn lồng Hội An")

    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(title="Hoi An lantern cruise")
        )
        await session.commit()
    async with factory() as session:
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=["vi"])
        await session.commit()

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "vi"))[EXPERIENCE_ID]

    assert fields["title"].value == "Đi bộ đèn lồng Hội An"
    assert fields["title"].provenance == "stale"
    assert not fields["title"].is_fallback


async def test_a_candidate_awaiting_review_is_never_served(factory):
    """`needs_review` text lives in `translation_fields` and must stay there."""
    async with factory() as session:
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=["vi"])
        row = (
            await session.execute(
                select(TranslationField).where(
                    TranslationField.entity_id == EXPERIENCE_ID,
                    TranslationField.field == "meeting_point",
                    TranslationField.locale == "vi",
                )
            )
        ).scalar_one()
        row.candidate_value = "Chùa Cầu"
        row.candidate_fingerprint = row.desired_fingerprint
        row.status = "needs_review"
        await session.commit()

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "vi"))[EXPERIENCE_ID]

    assert fields["meeting_point"].value == "Japanese Bridge"
    assert fields["meeting_point"].provenance == "fallback"


async def test_a_vietnamese_record_does_not_resolve_to_a_blank_english_page(factory):
    """A chain ending at `en` is wrong for a catalogue authored in Vietnamese."""
    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(source_language="vi", title="Đi bộ đèn lồng Hội An")
        )
        await session.commit()

    experience = await _experience(factory)
    assert resolution_chain(experience, "de") == ("de", "en", "vi")
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "de"))[EXPERIENCE_ID]

    assert fields["title"].value == "Đi bộ đèn lồng Hội An"
    assert fields["title"].locale == "vi"
    assert fields["title"].provenance == "fallback"


async def test_resolution_does_not_scale_its_queries_with_the_catalogue(factory):
    """Bulk, because the app and the database are in different regions.

    A per-experience round trip is not a style preference here: the same
    mistake in the translation enqueue cost ten minutes on the catalogue job.
    """
    from sqlalchemy import event

    async with factory() as session:
        for index in range(12):
            session.add(
                Experience(
                    id=uuid4(),
                    source_type="manual",
                    supplier_id=SUPPLIER_ID,
                    destination_id=DESTINATION_ID,
                    slug=f"extra-{index}",
                    title=f"Extra {index}",
                    short_description="s",
                    description="d",
                    category="tour",
                    indoor_outdoor="outdoor",
                    duration_minutes=60,
                    latitude=Decimal("15.88"),
                    longitude=Decimal("108.33"),
                    meeting_point="m",
                    source_language="en",
                    status="PUBLISHED",
                )
            )
        await session.commit()

    async with factory() as session:
        experiences = list(
            (await session.execute(select(Experience))).scalars().all()
        )

    statements: list[str] = []
    engine = create_async_engine(DATABASE_URL)

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await resolve_experience_text(session, experiences, "vi")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        await engine.dispose()

    selects = [item for item in statements if item.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 2, (
        f"resolution must not scale with the catalogue; {len(experiences)} "
        f"experiences issued {len(selects)} selects"
    )


async def test_loading_products_serves_the_requested_locale(factory):
    await _publish(
        factory,
        "vi",
        title="Đi bộ đèn lồng Hội An",
        meeting_point="Chùa Cầu",
    )

    async with factory() as session:
        products = await load_products(session, locale="vi")

    assert len(products) == 1
    assert products[0]["title"] == "Đi bộ đèn lồng Hội An"
    assert products[0]["meeting_point"] == "Chùa Cầu"
    assert products[0]["locale"] == "vi"
    assert products[0]["content_meta"]["title"] == {"locale": "vi", "provenance": "machine"}
    assert products[0]["content_meta"]["description"]["provenance"] == "fallback"


async def test_content_meta_describes_every_translated_field(factory):
    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "en"))[EXPERIENCE_ID]

    meta = content_meta(fields)
    assert set(meta) == {"title", "short_description", "description", "meeting_point"}
    assert all(item["provenance"] == "source" for item in meta.values())


# --- negotiation, which needs no database ---------------------------------


def test_accept_language_is_ordered_by_quality_not_by_position():
    """`en;q=0.5,vi;q=0.9` prefers Vietnamese, and splitting on commas does not."""
    assert parse_accept_language("en;q=0.5,vi;q=0.9")[0][0] == "vi"
    assert parse_accept_language("vi,en;q=0.9")[0][0] == "vi"
    # q=0 means "explicitly not this", not "least preferred".
    assert [tag for tag, _ in parse_accept_language("de;q=0,fr")] == ["fr"]


def test_an_unserved_header_tag_does_not_shadow_one_we_do_serve():
    """`normalize_locale` answers `en` for anything unknown, which would win."""
    assert (
        negotiate_locale(accept_language="sv,vi;q=0.9", enabled=["en", "vi"]) == "vi"
    )


def test_a_locale_that_is_not_enabled_degrades_rather_than_being_honoured():
    """The gate applies to explicit parameters too, not only to headers.

    A client passing `locale=ja` before Japanese content exists would search a
    corpus with no documents in it, and receive an empty result set that reads
    as a catalogue problem rather than an unfinished locale.
    """
    assert negotiate_locale(explicit="ja", enabled=["en"]) == "en"
    assert negotiate_locale(session_preference="ja", enabled=["en"]) == "en"
    assert negotiate_locale(accept_language="ja", enabled=["en"]) == "en"
    assert negotiate_locale(explicit="ja", enabled=["en", "ja"]) == "ja"


def test_the_first_match_wins_in_the_documented_order():
    assert (
        negotiate_locale(
            explicit="vi",
            session_preference="fr",
            accept_language="de",
            enabled=["en", "vi", "fr", "de"],
        )
        == "vi"
    )
    assert (
        negotiate_locale(
            session_preference="fr",
            accept_language="de",
            enabled=["en", "vi", "fr", "de"],
        )
        == "fr"
    )
    assert negotiate_locale(accept_language="de", enabled=["en", "de"]) == "de"
    assert negotiate_locale(enabled=["en", "de"]) == "en"


def test_regional_and_script_subtags_resolve_to_the_locale_we_serve():
    """Rejecting `zh-Hant-TW` would drop a Chinese speaker for a subtag we never asked about."""
    assert negotiate_locale(explicit="zh-Hant-TW", enabled=["en", "zh"]) == "zh"
    assert negotiate_locale(explicit="en_GB", enabled=["en"]) == "en"
    assert negotiate_locale(accept_language="fr-CA,en;q=0.5", enabled=["en", "fr"]) == "fr"


def test_the_shipped_default_serves_english_only():
    """Step 4 ships the machinery, not the languages (spec 12).

    The locales are enabled one at a time as their coverage and eval gates
    pass, so a half-translated catalogue is never the thing a shopper meets.
    """
    assert get_settings().enabled_locales == ["en"]
    assert Settings(_env_file=None).enabled_locales == ["en"]  # type: ignore[call-arg]


# --- the API boundary, in demo mode (no database needed) -------------------


@pytest.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_every_response_says_which_locale_it_resolved(api_client):
    """A client that cannot tell which locale it got cannot report a bug in it."""
    response = await api_client.get("/api/v1/session/context")
    assert response.status_code == 200
    body = response.json()
    assert body["locale"] == "en"
    assert body["enabled_locales"] == ["en"]


async def test_a_disabled_locale_is_not_stored_as_a_session_preference(api_client):
    """Storing it verbatim would quietly apply it to every later request."""
    response = await api_client.put(
        "/api/v1/session/locale",
        json={"locale": "ja"},
        headers={"X-Session-ID": "locale-test"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "locale": "en",
        "requested": "ja",
        "enabled_locales": ["en"],
    }


async def test_an_experience_card_carries_its_provenance(api_client):
    response = await api_client.get("/api/v1/experiences?limit=1")
    assert response.status_code == 200
    card = response.json()["items"][0]
    assert card["locale"] == "en"
    # Demo mode has no translation tables; the contract still has to hold, or
    # the frontend cannot rely on the map being present.
    assert isinstance(card["content_meta"], dict)
