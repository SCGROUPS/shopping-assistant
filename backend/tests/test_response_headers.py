"""Response headers set for every route by the correlation middleware.

These are easy to delete by accident: nothing in the application reads them,
no route asserts them, and removing the line makes no test fail unless a test
like this one exists. `Vary` in particular fails *silently and intermittently*
- a shopper switches to Vietnamese, the browser serves the English body it
already had, and the bug reproduces only for someone whose cache is in the
right state.
"""

from httpx import AsyncClient


async def test_responses_vary_on_the_headers_that_change_them(
    client: AsyncClient,
):
    response = await client.get("/api/v1/experiences")
    assert response.status_code == 200
    vary = {part.strip().lower() for part in response.headers["vary"].split(",")}
    # Language and session both arrive as headers, so they are invisible to any
    # cache keyed on the URL alone.
    assert "accept-language" in vary
    assert "x-session-id" in vary


async def test_the_same_url_in_two_languages_is_not_one_cache_entry(
    client: AsyncClient,
):
    english = await client.get("/api/v1/experiences", headers={"Accept-Language": "en"})
    vietnamese = await client.get("/api/v1/experiences", headers={"Accept-Language": "vi"})
    # Both must advertise Vary regardless of whether the bodies happen to
    # differ today: with only `en` enabled they resolve identically, and a test
    # that compared bodies would pass for the wrong reason and stop passing the
    # moment translations landed.
    for response in (english, vietnamese):
        assert "accept-language" in response.headers["vary"].lower()


async def test_correlation_and_hardening_headers_survive(client: AsyncClient):
    response = await client.get("/api/v1/experiences", headers={"X-Correlation-ID": "trace-me"})
    assert response.headers["x-correlation-id"] == "trace-me"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_vary_does_not_trample_the_cors_origin_entry(client: AsyncClient):
    """CORS sets `Vary: Origin`; assigning ours would have deleted it.

    Losing `Origin` from `Vary` is worse than the caching bug this middleware
    exists to fix: it lets one origin's credentialed response be reused for
    another.
    """
    response = await client.get("/api/v1/experiences", headers={"Origin": "http://localhost:5173"})
    vary = {part.strip().lower() for part in response.headers["vary"].split(",")}
    assert "origin" in vary
    assert "accept-language" in vary
    assert "x-session-id" in vary


async def test_vary_entries_are_not_duplicated(client: AsyncClient):
    response = await client.get("/api/v1/experiences", headers={"Origin": "http://localhost:5173"})
    parts = [p.strip().lower() for p in response.headers["vary"].split(",")]
    assert len(parts) == len(set(parts))
