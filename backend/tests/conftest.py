import pytest
from httpx import ASGITransport, AsyncClient

from app.common.store import store
from app.main import app


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Session-ID": "test-session"},
    ) as api:
        yield api


# Shared by the PostgreSQL suites. Listing tables per fixture meant each one had
# to know every foreign key pointing at it, and adding a suite broke the others
# through leftover rows. TRUNCATE ... CASCADE resolves the ordering itself.
async def reset_postgres(engine, keep: tuple[str, ...] = ()) -> None:
    from app.common.models import Base

    tables = [table.name for table in Base.metadata.sorted_tables if table.name not in keep]
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"
        )


# `create_all` alone yields a schema that accepts catalogue rows and then
# rejects the first search document, because the tsvector trigger is raw SQL the
# metadata cannot describe. Build the same schema the migration does.
async def create_postgres_schema(engine) -> None:
    from app.common.models import Base
    from app.common.schema import EXTENSIONS, POST_CREATE

    async with engine.begin() as connection:
        for statement in EXTENSIONS:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(Base.metadata.create_all)
        for statement in POST_CREATE:
            await connection.exec_driver_sql(statement)
