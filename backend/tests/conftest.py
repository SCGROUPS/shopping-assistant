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
