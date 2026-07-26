"""The multilingual acceptance gate, run against a real PostgreSQL server.

This suite exists *before* the translation pipeline it gates, and every case
that needs a translation is expected to fail. That is the point. The tokenizer
work taught the lesson the hard way: cases added after the fact passed
vacuously because the thing they tested emitted nothing at all, and a gate
written after the feature only ever certifies the feature's own assumptions.

The cases live in `evals/multilingual_cases.json` so that the same file drives
both this suite and `python -m app.evals.cli --suite multilingual` against real
inventory. Here they run against the full seeded catalogue rather than a
handful of fixtures, because a case asking for a cooking class among fifteen
products proves nothing about ranking - it has nothing to rank against.

Today every locale's document holds the English source text, which is exactly
what production contains: untranslated locales resolve *through* the source.
So this suite reproduces production's corpus, not an idealised one.
"""

import json
import os

import pytest
from conftest import create_postgres_schema, reset_postgres
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assistant.provider import DemoAIProvider
from app.common.locales import SUPPORTED_LOCALES, text_search_config
from app.evals.checks import run_checks
from app.evals.runner import CASE_DIR

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

CASES = json.loads((CASE_DIR / "multilingual_cases.json").read_text())["cases"]

# The control case asks in English and must pass today. Everything else needs
# content that does not exist yet. Listing the expected failures explicitly -
# rather than deriving them from `locale != "en"` - means adding a case forces a
# deliberate decision about whether it is a gate or a guarantee.
EXPECTED_TO_PASS = {"en-still-works", "vi-falls-back-rather-than-emptying"}


@pytest.fixture(scope="module")
def case_ids() -> list[str]:
    return [case["id"] for case in CASES]


def test_every_case_declares_why_it_exists(case_ids):
    """A golden case without a rationale becomes unfalsifiable folklore."""
    assert len(case_ids) == len(set(case_ids)), "duplicate case ids"
    for case in CASES:
        assert case.get("why"), f"{case['id']} has no rationale"
        assert case.get("locale"), f"{case['id']} declares no locale"
        assert case["locale"] in SUPPORTED_LOCALES, f"{case['id']} targets an unserved locale"


def test_the_suite_covers_every_locale_we_promised():
    """Seven added languages were promised; a gate missing one does not gate it."""
    covered = {case["locale"] for case in CASES}
    assert covered == set(SUPPORTED_LOCALES), f"missing {set(SUPPORTED_LOCALES) - covered}"


async def test_each_locale_is_indexed_with_its_own_text_search_configuration():
    """The trigger and the query parser must agree, per locale.

    A `german` document searched with an `english` query stems both sides
    differently and stops matching, and nothing raises. This asserts the two
    halves of that agreement resolve to the same configuration name, which is
    the only part that can be checked without content.
    """
    engine = create_async_engine(DATABASE_URL or "")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await create_postgres_schema(engine)
    try:
        async with factory() as db:
            for locale in SUPPORTED_LOCALES:
                configured = text_search_config(locale)
                exists = await db.scalar(
                    text("SELECT count(*) FROM pg_ts_config WHERE cfgname = :name"),
                    {"name": configured},
                )
                assert exists == 1, f"{locale} maps to unknown configuration {configured}"
    finally:
        await engine.dispose()


@pytest.fixture
async def seeded(monkeypatch):
    """The whole catalogue in PostgreSQL, indexed in all eight locales.

    Deliberately the full seed rather than a fixture handful: a case asking for
    a cooking class among fifteen products would pass whatever ranking did,
    because there is nothing for the right answer to beat.
    """
    from app.catalog import db_seed
    from app.common import config, database, persistence
    from app.search import service as search_service

    engine = create_async_engine(DATABASE_URL or "", pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await create_postgres_schema(engine)
    await reset_postgres(engine)

    settings = config.get_settings()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(db_seed, "session_factory", factory)
    monkeypatch.setattr(search_service, "session_factory", factory)
    monkeypatch.setattr(persistence, "session_factory", factory)
    monkeypatch.setattr(database, "session_factory", factory)

    provider = DemoAIProvider()
    await db_seed.seed_database(force=True, ai_provider=provider)
    await _mirror_english_documents_into_every_locale(factory)
    try:
        yield provider
    finally:
        await engine.dispose()


async def test_multilingual_cases_against_the_seeded_catalogue(seeded):
    """The gate itself: ask in eight languages, over the whole catalogue.

    Reported rather than asserted case by case, so that one run shows the whole
    shape of the gap. The assertion at the end is deliberately weak in one
    direction and strict in the other: cases that must pass today are hard
    failures, and cases that are expected to fail are allowed to - but if one
    of them starts passing, that is reported loudly, because it means either
    the translation pipeline has landed or the case is not testing what it
    claims.
    """
    from app.evals.runner import _multilingual_request
    from app.search.service import SearchService

    service = SearchService(ai_provider=seeded)
    outcomes: dict[str, list[str]] = {}
    for case in CASES:
        response = await service.search(_multilingual_request(case))
        products = [{**item.model_dump(mode="json"), "id": str(item.id)} for item in response.items]
        outcomes[case["id"]] = [str(v) for v in run_checks(products, case.get("expect", {}))]

    report = "\n".join(
        f"  {'PASS' if not v else 'FAIL'}  {case_id}: {'; '.join(v) or 'ok'}"
        for case_id, v in outcomes.items()
    )
    print(
        f"\nMultilingual gate ({sum(1 for v in outcomes.values() if not v)}/{len(outcomes)}):\n{report}"
    )

    must_pass = {c: v for c, v in outcomes.items() if c in EXPECTED_TO_PASS and v}
    assert not must_pass, f"cases that must pass today are failing: {must_pass}"

    unexpectedly_passing = [
        case_id for case_id, v in outcomes.items() if case_id not in EXPECTED_TO_PASS and not v
    ]
    if unexpectedly_passing:
        pytest.fail(
            "These cases are recorded as blocked on the translation pipeline but now pass: "
            f"{unexpectedly_passing}. Move them into EXPECTED_TO_PASS so the gate holds them, "
            "or explain why the case does not need translated content."
        )


async def _mirror_english_documents_into_every_locale(factory) -> None:
    """Reproduce production's corpus: one English document, copied per locale.

    Not a shortcut around the outbox — it is a statement of what the outbox
    currently produces. `resolved_document_text` falls back through the source
    language, so an untranslated locale is indexed with English text under its
    own text-search configuration. Writing that directly keeps this suite fast
    enough to run on every build while testing exactly the corpus that exists.
    """
    async with factory() as db, db.begin():
        english = await db.scalar(
            text("SELECT count(*) FROM experience_search_documents WHERE locale = 'en'")
        )
        assert english, "no English documents were built; the seed did not index"
        for locale in SUPPORTED_LOCALES:
            if locale == "en":
                continue
            await db.execute(
                text(
                    "INSERT INTO experience_search_documents (experience_id, locale,"
                    " document_text, search_vector, embedding, embedding_model,"
                    " embedding_version, content_hash, index_fingerprint, embedded_at)"
                    " SELECT experience_id, :locale, document_text,"
                    " to_tsvector(CAST(:config AS regconfig), document_text), embedding,"
                    " embedding_model, embedding_version, content_hash,"
                    " index_fingerprint, embedded_at"
                    " FROM experience_search_documents WHERE locale = 'en'"
                    " ON CONFLICT (experience_id, locale) DO NOTHING"
                ),
                {"locale": locale, "config": text_search_config(locale)},
            )
