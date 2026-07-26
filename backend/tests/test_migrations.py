"""The migrations must build the schema the models describe - by themselves.

`0001_initial` calls `Base.metadata.create_all`, so a database built from
scratch gets every table and column from the *current* models regardless of
what the later migrations actually contain. That makes the obvious check -
"upgrade a fresh database and compare it to the models" - pass even when a
migration is missing half its DDL, which is exactly the situation that matters,
because production is not a fresh database.

Running `upgrade -> downgrade -> upgrade` removes the disguise. After the
downgrade the objects `create_all` contributed are gone, so the second upgrade
has nothing to lean on and has to produce them itself. Two separate defects
were found this way and neither was visible from a fresh build: incomplete
`upgrade()` DDL, and a `downgrade()` that could not drop `locale` because the
locale-aware tsvector trigger still depended on it.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.models import Base

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is required")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIRST_PIPELINE_REVISION = "0004_content_pipeline"
PREVIOUS_REVISION = "0003_operator_console"


def _alembic(command: str, *args: str, url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"alembic {command} {' '.join(args)} failed:\n{result.stderr}")


async def _live_columns(engine) -> dict[str, set[str]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
        live: dict[str, set[str]] = {}
        for table, column in rows:
            live.setdefault(table, set()).add(column)
    return live


async def _shape(engine) -> dict[str, object]:
    """Column types, nullability and defaults, plus constraints and indexes.

    Presence of a column says almost nothing: a migration can create
    `varchar(10)` where the model wants `text`, drop a unique constraint, or
    forget a server default, and a name-only comparison passes every time.

    Types come from `format_type`, not `information_schema.data_type`, which
    reports every `varchar(n)` as "character varying", every `numeric(p,s)` as
    "numeric", and - the one that matters most here - every `vector(n)` as
    "USER-DEFINED". A migration creating `vector(1536)` where the model wants
    `vector(512)` would compare equal, and the mismatch would only surface as a
    dimension error the first time production tried to store an embedding.
    """
    async with engine.connect() as connection:
        columns = (
            (
                await connection.execute(
                    text(
                        "SELECT c.relname || '.' || a.attname "
                        "  || ':' || format_type(a.atttypid, a.atttypmod) "
                        "  || ':' || a.attnotnull "
                        "  || ':' || coalesce(pg_get_expr(d.adbin, d.adrelid), '-') "
                        "FROM pg_attribute a "
                        "JOIN pg_class c ON c.oid = a.attrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "LEFT JOIN pg_attrdef d "
                        "  ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                        "  AND a.attnum > 0 AND NOT a.attisdropped"
                    )
                )
            )
            .scalars()
            .all()
        )
        constraints = (
            (
                await connection.execute(
                    text(
                        "SELECT c.conrelid::regclass::text || ':' || c.conname "
                        "  || ':' || pg_get_constraintdef(c.oid) "
                        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                        "WHERE n.nspname = 'public'"
                    )
                )
            )
            .scalars()
            .all()
        )
        indexes = (
            (
                await connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    return {
        "columns": sorted(columns),
        "constraints": sorted(constraints),
        "indexes": sorted(indexes),
    }


def _drift(live: dict[str, set[str]]) -> list[str]:
    missing: list[str] = []
    for name, table in Base.metadata.tables.items():
        if name not in live:
            missing.append(f"table {name}")
            continue
        missing += [f"{name}.{c.name}" for c in table.columns if c.name not in live[name]]
    return missing


@pytest.fixture
async def migration_database():
    assert DATABASE_URL is not None
    # A dedicated database, because the migration drops and rebuilds objects the
    # other suites are using.
    url = DATABASE_URL.rsplit("/", 1)[0] + "/vietra_migration_roundtrip_test"
    admin = create_async_engine(
        DATABASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as connection:
        await connection.exec_driver_sql("DROP DATABASE IF EXISTS vietra_migration_roundtrip_test")
        await connection.exec_driver_sql("CREATE DATABASE vietra_migration_roundtrip_test")
    await admin.dispose()

    engine = create_async_engine(url)
    try:
        yield url, engine
    finally:
        await engine.dispose()
        admin = create_async_engine(
            DATABASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT"
        )
        async with admin.connect() as connection:
            await connection.exec_driver_sql(
                "DROP DATABASE IF EXISTS vietra_migration_roundtrip_test WITH (FORCE)"
            )
        await admin.dispose()


async def test_migrations_rebuild_the_schema_without_create_all(migration_database):
    url, engine = migration_database

    _alembic("upgrade", "head", url=url)
    assert _drift(await _live_columns(engine)) == []
    built_by_create_all = await _shape(engine)

    # Everything `create_all` provided for the pipeline is now removed, so the
    # re-upgrade has to stand on the migration's own DDL.
    _alembic("downgrade", PREVIOUS_REVISION, url=url)
    reverted = await _live_columns(engine)
    assert "experience_translations" not in reverted
    assert "locale" not in reverted["experience_search_documents"]
    assert "is_placeholder" not in reverted["suppliers"]

    _alembic("upgrade", "head", url=url)
    assert _drift(await _live_columns(engine)) == []

    # The strong assertion: the schema the migration builds on its own is
    # indistinguishable from the one `create_all` produced - same types, same
    # nullability, same defaults, same constraints, same indexes.
    rebuilt = await _shape(engine)
    for aspect in ("columns", "constraints", "indexes"):
        assert rebuilt[aspect] == built_by_create_all[aspect], aspect


async def test_the_upgrade_restores_the_locale_aware_search_objects(migration_database):
    url, engine = migration_database

    _alembic("upgrade", "head", url=url)
    _alembic("downgrade", PREVIOUS_REVISION, url=url)
    _alembic("upgrade", "head", url=url)

    async with engine.connect() as connection:
        primary_key = (
            (
                await connection.execute(
                    text(
                        "SELECT a.attname FROM pg_constraint c "
                        "JOIN pg_attribute a ON a.attrelid = c.conrelid "
                        "AND a.attnum = ANY(c.conkey) "
                        "WHERE c.conrelid = 'experience_search_documents'::regclass "
                        "AND c.contype = 'p'"
                    )
                )
            )
            .scalars()
            .all()
        )
        trigger_body = await connection.scalar(
            text(
                "SELECT pg_get_functiondef(oid) FROM pg_proc "
                "WHERE proname = 'tourism_search_vector_update'"
            )
        )
        checks = (
            (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'experiences'::regclass AND contype = 'c'"
                    )
                )
            )
            .scalars()
            .all()
        )

    # Without the composite key a second locale cannot be stored at all, which
    # is the whole point of the release.
    assert sorted(primary_key) == ["experience_id", "locale"]
    # A trigger that stems every locale as English silently destroys Vietnamese
    # and CJK retrieval while the rows themselves look correct.
    assert trigger_body is not None and "NEW.locale" in trigger_body
    assert {
        "ck_experience_partner",
        "ck_experience_source_type",
        "ck_experience_external_id",
    } <= set(checks)


# A schema dump of the database as production actually has it, taken at the
# revision production is running. It is the one thing a from-scratch build
# cannot provide: `0001_initial` uses `create_all`, so a fresh database silently
# acquires every column the *current* models declare, and the round-trip test
# above - which is a genuinely good test of the later migrations' DDL - still
# sees the models and the schema agree. A column added to the model with no
# migration behind it therefore passes every existing check and is missing only
# where it matters. This file is how the test gets to stand somewhere that has
# never seen the new model.
BASELINE_SQL = BACKEND_ROOT / "migrations" / "baseline_0005.sql"
BASELINE_REVISION = "0005_translation_operations"


async def _load_baseline(engine) -> None:
    statements = "\n".join(
        line
        for line in BASELINE_SQL.read_text().splitlines()
        # psql meta-commands. Keeping the dump loadable without the psql binary
        # means CI needs no extra tooling to run this.
        if not line.startswith("\\")
        # pg_dump empties the search path and schema-qualifies everything it
        # writes. Left in, sequence defaults come back as
        # `nextval('public.x_seq')` where the migrations record `nextval('x_seq')`
        # - the same default, compared unequal.
        and "set_config('search_path'" not in line
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql(statements)


async def test_a_database_at_the_released_revision_upgrades_to_the_models(migration_database):
    """The check the from-scratch build cannot make.

    Restore the schema a deployed database is running, upgrade it, and require
    the result to satisfy the models. A model column with no migration behind it
    fails here and nowhere else.
    """
    url, engine = migration_database

    await _load_baseline(engine)
    _alembic("stamp", BASELINE_REVISION, url=url)
    _alembic("upgrade", "head", url=url)

    drift = _drift(await _live_columns(engine))
    assert drift == [], (
        "these model columns are missing after upgrading a released database, "
        f"so a migration for them was never written: {drift}"
    )


def _canonical(definition: str) -> str:
    """One rendering for two spellings of the same constraint.

    Restoring a `pg_dump` and building from `create_all` produce identical
    CHECK constraints that Postgres prints differently - `ANY (ARRAY['a'::x,
    ...])` against `ANY ((ARRAY['a', ...])::x[])`. Dropping the casts and the
    punctuation leaves the part that carries meaning: the column, the operator
    and the literals. A constraint that really changed still differs here.
    """
    for cast in ("::text[]", "::text", "::character varying"):
        definition = definition.replace(cast, "")
    return re.sub(r"[()\s]+", "", definition)


async def test_upgrading_a_released_database_lands_where_a_fresh_install_does(
    migration_database,
):
    """An upgraded database and a new one must be the same database.

    Drift here is the kind that survives every other check: an index the
    migration forgot, a default only `create_all` supplies, a type that differs
    by a length. Each behaves correctly on a machine built from scratch - every
    developer's, every test run's - and wrongly on the only one with customers.

    This also keeps `baseline_0005.sql` honest. A dump that had drifted forward
    would stop the upgrade doing any work, and the shapes would part company
    here.
    """
    url, engine = migration_database

    await _load_baseline(engine)
    _alembic("stamp", BASELINE_REVISION, url=url)
    _alembic("upgrade", "head", url=url)
    upgraded = await _shape(engine)

    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        await connection.exec_driver_sql("CREATE SCHEMA public")
    _alembic("stamp", "base", url=url)
    _alembic("upgrade", "head", url=url)
    fresh = await _shape(engine)

    for aspect in ("columns", "indexes"):
        assert upgraded[aspect] == fresh[aspect], (
            f"{aspect} differ between an upgraded database and a new one"
        )
    assert [_canonical(item) for item in upgraded["constraints"]] == [
        _canonical(item) for item in fresh["constraints"]
    ], "constraints differ between an upgraded database and a new one"
