"""Locale-aware resolution at the API boundary (spec 4.3, 9).

The failure this suite guards is not "the wrong string appeared". It is that
*search* and *display* can resolve differently, in which case a shopper finds a
product by words the page never shows, and no test of either side alone
notices. So the resolver is shared, and the first test here is the one that
proves it stays shared.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cart.service import CartService
from app.catalog import indexing
from app.common.config import Settings, get_settings
from app.common.field_policy import policy_for, translated_fields
from app.common.locales import negotiate_locale, parse_accept_language
from app.common.models import (
    Cart,
    CartItem,
    Destination,
    Experience,
    ExperienceOption,
    ExperienceTranslation,
    ShoppingSession,
    Supplier,
    TranslationField,
)
from app.common.persistence import load_products
from app.common.resolution import (
    TRANSLATED_FIELDS,
    content_meta,
    fallback_fields,
    resolution_chain,
    resolve_experience_text,
)
from app.content import coverage
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
        session.add(ExperienceTranslation(experience_id=EXPERIENCE_ID, locale=locale, **values))
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
    assert not fields["title"].is_fallback
    assert not fields["title"].stale
    # Untranslated siblings fall back rather than blanking.
    assert fields["description"].value == "A guided evening walk through the old town."
    assert fields["description"].locale == "en"
    assert fields["description"].provenance == "source"
    assert fields["description"].is_fallback
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
    assert fields["title"].stale
    assert fields["title"].provenance == "machine"
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
    assert fields["meeting_point"].locale == "en"
    assert fields["meeting_point"].is_fallback


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
    assert fields["title"].provenance == "source"
    assert fields["title"].is_fallback


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
        experiences = list((await session.execute(select(Experience))).scalars().all())

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
    assert products[0]["content_meta"]["title"] == {
        "locale": "vi",
        "provenance": "machine",
        "stale": False,
        "fallback": False,
    }
    assert products[0]["content_meta"]["description"]["fallback"] is True


async def test_content_meta_describes_every_translated_field(factory):
    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "en"))[EXPERIENCE_ID]

    meta = content_meta(fields)
    assert set(meta) == {"title", "short_description", "description", "meeting_point"}
    assert all(item["provenance"] == "source" for item in meta.values())
    assert all(item["stale"] is False for item in meta.values())
    assert all(item["fallback"] is False for item in meta.values())


# --- negotiation, which needs no database ---------------------------------


def test_accept_language_is_ordered_by_quality_not_by_position():
    """`en;q=0.5,vi;q=0.9` prefers Vietnamese, and splitting on commas does not."""
    assert parse_accept_language("en;q=0.5,vi;q=0.9")[0][0] == "vi"
    assert parse_accept_language("vi,en;q=0.9")[0][0] == "vi"
    # q=0 means "explicitly not this", not "least preferred".
    assert [tag for tag, _ in parse_accept_language("de;q=0,fr")] == ["fr"]


def test_an_unserved_header_tag_does_not_shadow_one_we_do_serve():
    """`normalize_locale` answers `en` for anything unknown, which would win."""
    assert negotiate_locale(accept_language="sv,vi;q=0.9", enabled=["en", "vi"]) == "vi"


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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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


# --- the four cases round 21 caught, each of which had no test -------------


async def test_a_stale_fallback_reports_both_facts_not_one(factory):
    """Fallback and staleness are independent, and this is the case needing both.

    An earlier revision returned `fallback` before it looked at staleness, so
    a German request that resolved to a stale English translation reported
    only that it was English - and a reader had no way to learn the text was
    also out of date. Collapsing three facts into one label loses whichever
    the label checked second.
    """
    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(source_language="vi", title="Đi bộ đèn lồng Hội An")
        )
        await session.commit()
    await _publish(factory, "en", title="Hoi An lantern walk")
    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(title="Đi thuyền đèn lồng Hội An")
        )
        await session.commit()
    async with factory() as session:
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=["en"])
        await session.commit()

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "de"))[EXPERIENCE_ID]

    title = fields["title"]
    assert title.value == "Hoi An lantern walk"
    assert title.locale == "en"
    assert title.is_fallback, "German was requested and English was served"
    assert title.stale, "the source moved after this translation was published"


async def test_a_stale_meeting_point_is_not_served(factory):
    """Directions are operational data, not prose.

    A description one edit behind is a cosmetic problem. A meeting point one
    edit behind sends a traveller to a place the tour no longer departs from,
    and the complaint that generates is not "it was in the wrong language".
    So this field alone keeps walking the chain until it finds something
    current - which the source locale always is.
    """
    await _publish(factory, "vi", title="Đi bộ đèn lồng Hội An", meeting_point="Chùa Cầu")

    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(meeting_point="Cua Dai Beach gate", title="Hoi An lantern cruise")
        )
        await session.commit()
    async with factory() as session:
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=["vi"])
        await session.commit()

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "vi"))[EXPERIENCE_ID]

    assert fields["meeting_point"].value == "Cua Dai Beach gate"
    assert fields["meeting_point"].locale == "en"
    assert not fields["meeting_point"].stale
    # Prose in the same record is still served stale, or the policy would be
    # "fall back on any edit" wearing a different name.
    assert fields["title"].value == "Đi bộ đèn lồng Hội An"
    assert fields["title"].stale


async def test_served_text_without_workflow_state_is_unknown_not_imported(factory):
    """`imported` would state a provenance nobody recorded.

    The served table and the workflow table are created by the same migration
    and every current writer populates both, so a missing row means legacy
    data or a violated invariant. Naming that `imported` is a confident wrong
    answer, which is the failure this map exists to prevent.
    """
    async with factory() as session:
        session.add(
            ExperienceTranslation(
                experience_id=EXPERIENCE_ID, locale="vi", title="Đi bộ đèn lồng Hội An"
            )
        )
        await session.commit()

    experience = await _experience(factory)
    async with factory() as session:
        fields = (await resolve_experience_text(session, [experience], "vi"))[EXPERIENCE_ID]

    assert fields["title"].value == "Đi bộ đèn lồng Hội An"
    assert fields["title"].provenance == "unknown"


def test_an_unknown_explicit_locale_does_not_override_a_valid_session_choice():
    """`?locale=sv` is not a request for English.

    `normalize_locale` answers `en` for anything unrecognised, which is right
    when a value must be produced and wrong when one must be judged: it turns
    an unserviceable tag into a deliberate-looking request for English that
    then outranks the Vietnamese the session already chose.
    """
    assert negotiate_locale(explicit="sv", session_preference="vi", enabled=["en", "vi"]) == "vi"
    assert (
        negotiate_locale(
            explicit="klingon",
            session_preference="vi",
            accept_language="fr",
            enabled=["en", "vi", "fr"],
        )
        == "vi"
    )
    assert (
        negotiate_locale(session_preference="sv", accept_language="fr", enabled=["en", "fr"])
        == "fr"
    )


async def test_an_empty_result_still_says_which_corpus_was_searched(api_client):
    """Zero results is exactly when a client needs the locale and has no card.

    An operator debugging "the Vietnamese storefront returns nothing" cannot
    distinguish an empty corpus from a bad query if the only place the locale
    appears is on results that do not exist.
    """
    response = await api_client.post(
        "/api/v1/search", json={"query": "zzzzz no such thing anywhere", "page_size": 1}
    )
    assert response.status_code == 200
    assert response.json()["locale"] == "en"

    listing = await api_client.get("/api/v1/experiences?limit=1&offset=9999")
    assert listing.json()["items"] == []
    assert listing.json()["locale"] == "en"

    rail = await api_client.get("/api/v1/recommendations?limit=1")
    assert rail.json()["locale"] == "en"


async def test_the_assistant_searches_the_locale_the_request_resolved(api_client):
    """The guided path is the primary path, and it was the one still English.

    The assistant fans out through tools, streaming and rendering, so a locale
    that the storefront resolves correctly is worth nothing if the conversation
    beside it searches a different corpus.
    """
    headers = {"X-Session-ID": "assistant-locale", "Accept-Language": "vi"}
    # Asserting `en` would prove nothing: English is what a hardcoded default
    # returns too. The locale has to be one only negotiation could have chosen.
    settings = get_settings()
    settings.enabled_locales = ["en", "vi"]
    try:
        created = await api_client.post("/api/v1/conversations", json={}, headers=headers)
        conversation_id = created.json()["id"]

        seen: list[str | None] = []
        from app.api.routes import assistant_service

        original = assistant_service.search.search

        async def recording(request):
            seen.append(request.locale)
            return await original(request)

        assistant_service.search.search = recording
        try:
            response = await api_client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"message": "show me a boat trip"},
                headers=headers,
            )
        finally:
            assistant_service.search.search = original
    finally:
        settings.enabled_locales = ["en"]

    assert response.status_code == 200, response.text
    assert seen, "the assistant did not search at all; this test proves nothing"
    assert all(item == "vi" for item in seen), (
        f"the assistant searched {seen}, not the locale the request resolved"
    )


async def test_falling_back_to_english_is_not_counted_as_korean_coverage(factory):
    """A shopper who reads English text has not been served Korean.

    Korean has had no job enqueued and no translation published, so every
    field resolves to the English source. Counting a resolved, non-empty
    string as coverage would report this catalogue ready for Korean.
    """
    async with factory() as session:
        reports = await coverage.locale_coverage(session, ("ko",))

    [korean] = reports
    assert korean.totals().translated == 0
    assert korean.as_dict()["overall"]["percent"] == 0.0
    # And the fields are not absent: they fell back to the English source,
    # which is what a Korean shopper would actually be shown.
    assert korean.as_dict()["fields"]["title"]["fallback"] == 1


async def test_an_empty_catalogue_reports_zero_percent_not_a_hundred(factory):
    """`0 of 0` is arithmetically undefined and operationally dangerous.

    Guarding it by returning 100 - or by letting a naive `part/whole` raise and
    be caught into a default - makes a locale with nothing in it pass the gate
    designed to catch exactly that. Zero is the honest answer: no experience
    has been translated, because there is no experience.
    """
    async with factory() as session:
        await session.execute(delete(Experience).where(Experience.id == EXPERIENCE_ID))
        await session.commit()

    async with factory() as session:
        [report] = await coverage.locale_coverage(session, ("vi",))

    assert report.experiences == 0
    assert report.as_dict()["overall"]["total"] == 0
    assert report.as_dict()["overall"]["percent"] == 0.0


async def test_coverage_counts_a_blank_field_as_missing_not_translated(factory):
    """A blank is content nobody wrote; a fallback is a translation not yet done.

    Merging them lets a catalogue of empty meeting points report itself fully
    covered, and the two need entirely different work to fix.
    """
    async with factory() as session:
        await session.execute(
            update(Experience).where(Experience.id == EXPERIENCE_ID).values(meeting_point="")
        )
        await session.commit()

    async with factory() as session:
        [report] = await coverage.locale_coverage(session, ("vi",))

    assert report.as_dict()["fields"]["meeting_point"]["missing"] == 1
    assert report.as_dict()["fields"]["meeting_point"]["translated"] == 0
    assert report.as_dict()["fields"]["meeting_point"]["fallback"] == 0


async def test_coverage_reports_a_published_translation_as_translated(factory):
    await _publish(
        factory,
        "vi",
        title="Đi bộ đèn lồng Hội An",
        short_description="Đi bộ buổi tối",
        description="Một chuyến đi bộ có hướng dẫn.",
        meeting_point="Chùa Cầu",
    )

    async with factory() as session:
        [report] = await coverage.locale_coverage(session, ("vi",))

    overall = report.as_dict()["overall"]
    assert overall["translated"] == 4
    assert overall["fallback"] == 0
    assert overall["percent"] == 100.0


async def test_coverage_agrees_with_the_page_about_a_stale_meeting_point(factory):
    """Coverage is measured through the resolver, so policy cannot diverge.

    `meeting_point` refuses stale text and falls back to the source. A separate
    SQL predicate over `translation_fields` would call that row `current` and
    report Vietnamese fully covered, while the Vietnamese page shows English
    directions. Counting what the resolver returned is what keeps the two
    answers the same answer.
    """
    await _publish(
        factory,
        "vi",
        title="Đi bộ đèn lồng Hội An",
        short_description="Đi bộ buổi tối",
        description="Một chuyến đi bộ có hướng dẫn.",
        meeting_point="Chùa Cầu",
    )
    async with factory() as session:
        await session.execute(
            update(Experience)
            .where(Experience.id == EXPERIENCE_ID)
            .values(meeting_point="Tan Ky House", title="Hoi An lantern walk at dusk")
        )
        await enqueue_experience_translations(session, EXPERIENCE_ID, locales=["vi"])
        await session.commit()

    async with factory() as session:
        [report] = await coverage.locale_coverage(session, ("vi",))

    fields = report.as_dict()["fields"]
    assert fields["meeting_point"]["fallback"] == 1
    assert fields["meeting_point"]["translated"] == 0
    # Both source strings moved, so both translations are stale. Prose is still
    # served in Vietnamese and counted as covered, because the alternative -
    # falling back on every edit - blanks a whole locale until the worker
    # catches up. Only the meeting point refuses.
    assert fields["title"]["translated"] == 1
    assert fields["title"]["stale"] == 1
    assert report.queued >= 1


async def test_the_checkout_review_names_products_in_the_shopper_s_language(factory):
    """The cart was localised; checkout preparation was not.

    `validate_db` defaulted its locale to English, and `/checkout/prepare`
    simply did not pass one - so a Vietnamese shopper read a Vietnamese cart
    and then paid against an English one. "Is this the thing I chose?" is the
    worst question to raise at the moment of payment, which is the whole
    reason the cart resolves titles at all.

    The locale is now a required keyword argument rather than a defaulted one,
    so the next call site that forgets fails to type-check instead of quietly
    switching language mid-purchase. This test covers the behaviour; the
    signature covers the ones nobody has written yet.
    """
    await _publish(factory, "vi", title="Đi bộ đèn lồng Hội An")

    option_id, cart_id, session_row_id = uuid4(), uuid4(), None
    async with factory() as session:
        shopping_session = ShoppingSession(anonymous_id=f"cart-locale-{uuid4()}", currency="VND")
        session.add(shopping_session)
        await session.flush()
        session_row_id = shopping_session.id
        session.add(
            ExperienceOption(
                id=option_id,
                experience_id=EXPERIENCE_ID,
                external_id="opt-1",
                name="Standard",
                description="Standard entry",
                validity_type="fixed",
                confirmation_type="instant",
                cancellation_policy_code="flex",
            )
        )
        session.add(Cart(id=cart_id, session_id=shopping_session.id, currency="VND"))
        await session.flush()
        session.add(
            CartItem(
                cart_id=cart_id,
                experience_id=EXPERIENCE_ID,
                option_id=option_id,
                participants=[{"type": "adult", "count": 2}],
                unit_prices=[
                    {
                        "participant_type": "adult",
                        "amount": "100000",
                        "currency": "VND",
                    }
                ],
                quantity=2,
                quoted_total=Decimal("200000"),
                quote_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()

    carts = CartService()
    async with factory() as session:
        _cart, vietnamese = await carts.validate_db(session, session_row_id, locale="vi")
        _cart, english = await carts.validate_db(session, session_row_id, locale="en")

    assert vietnamese.items[0].experience_title == "Đi bộ đèn lồng Hội An"
    # The English request must still get English, or the test would pass on a
    # service that ignored the argument and always returned Vietnamese.
    assert english.items[0].experience_title == "Hoi An lantern walk"


async def test_a_client_cannot_declare_its_own_locale_on_an_event(api_client):
    """The one number that decides a locale's fate must not be client-writable.

    `properties` is a free-form map, so a client could send
    `{"locale": "ko"}` on a request the server resolved as Vietnamese. Stored
    with `setdefault` it won, which made Korean adoption - the metric used to
    decide whether Korean stays enabled - something anyone with curl could
    manufacture, or accidentally corrupt by echoing a stale UI value.
    """
    settings = get_settings()
    settings.enabled_locales = ["en", "vi"]
    try:
        response = await api_client.post(
            "/api/v1/events",
            json={"event_type": "assistant_opened", "properties": {"locale": "ko"}},
            headers={"X-Session-ID": "event-locale", "Accept-Language": "vi"},
        )
    finally:
        settings.enabled_locales = ["en"]

    assert response.status_code == 202, response.text
    # Asserted against what was stored, not what was returned: the endpoint
    # echoes neither, and the stored row is the thing the decision is made from.
    from app.common.store import store

    stored = next(
        item for item in reversed(store.events) if item["id"] == response.json()["event_id"]
    )
    assert stored["properties"]["locale"] == "vi"


def test_an_unregistered_field_is_not_translated_and_fails_closed():
    """Fail-open here auto-publishes a field nobody reviewed.

    The registry decides which fields are translated at all, so an unregistered
    one never reaches the resolver. If it somehow does, it must not inherit
    prose's two properties - auto-publication and stale serving - because the
    fields most likely to be added next are pickup points and cancellation
    terms, which are exactly the kind that must not have either.
    """
    assert "pickup_point" not in translated_fields()
    policy = policy_for("pickup_point")
    assert policy.requires_review is True
    assert policy.serve_stale is False


def test_the_enqueuer_and_the_resolver_translate_the_same_fields():
    """Two lists of the same thing drift, and the symptom is silent.

    A field enqueued but never resolved is translated into every language and
    displayed in none of them; a field resolved but never enqueued falls back
    forever. Neither raises, and neither shows up in a test of one side.
    """
    from app.content.enqueue import EXPERIENCE_FIELDS

    assert TRANSLATED_FIELDS == EXPERIENCE_FIELDS == translated_fields()
