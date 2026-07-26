"""The gate that would have caught both outages at the deploy that caused them.

Each outage was a request the deployed model rejects with 400 on every call,
made invisible by a fallback that answers with a normal-looking page. One real
call at startup turns that into a failed deployment.
"""

import pytest

from app.common.degradation import intent_health
from app.common.intent_probe import probe_intent

VOCABULARY = {"categories": ["Food", "Transport"], "destinations": ["Hoi An", "Hanoi"]}


class _Rejected(Exception):
    """What the SDK raises when the deployment refuses the request itself."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RejectingProvider:
    """The real outage: reasoning.effort the deployed model does not accept."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def extract_intent(self, text: str, **_):
        raise _Rejected(self._message)


class FlakyProvider:
    """The model having a bad minute, which says nothing about our request."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def extract_intent(self, text: str, **_):
        raise self._error


class WorkingProvider:
    async def extract_intent(self, text: str, **_):
        return object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        # Outage one and outage two. Opposite text, one cause - which is why
        # the probe classifies on status code and not on wording.
        "Unsupported value: 'minimal' is not supported with this model",
        "Unsupported value: 'none' is not supported with this model",
    ],
)
async def test_a_deployment_that_rejects_our_request_fails_the_probe(message):
    result = await probe_intent(RejectingProvider(message), **VOCABULARY)
    assert not result.ok, (
        "every intent call would be rejected, every search would fall back to keyword "
        "parsing, and the revision would still have been let into production"
    )
    assert "reasoning.effort" in result.detail, "the failure must say where to look"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        _Rejected("rate limited", status_code=429),
        _Rejected("gateway blip", status_code=503),
        ConnectionError("connection reset"),
    ],
)
async def test_a_transient_upstream_problem_does_not_block_the_deployment(error):
    """Refusing to start over a blip would be an outage of our own making.

    These say nothing about whether our request is valid, and the running
    service already handles them: a page beats an error page.
    """
    result = await probe_intent(FlakyProvider(error), **VOCABULARY)
    assert result.ok


@pytest.mark.asyncio
async def test_a_working_deployment_passes():
    result = await probe_intent(WorkingProvider(), **VOCABULARY)
    assert result.ok


@pytest.mark.asyncio
async def test_readiness_is_withheld_while_the_model_rejects_us(monkeypatch):
    """The point of the probe: a broken revision must not take traffic."""
    from app.main import ready

    intent_health.reset()
    monkeypatch.setattr("app.main.database_ready", _always_ready)

    assert (await ready()).status_code == 200, "unprobed must not be treated as broken"

    intent_health.record_probe(ok=False, detail="the intent deployment rejected our request")
    assert (await ready()).status_code == 503, (
        "a revision that cannot understand a single query reported itself ready, so the "
        "deployment would go green and replace a working revision with a broken one"
    )

    intent_health.record_probe(ok=True, detail="fine")
    assert (await ready()).status_code == 200
    intent_health.reset()


async def _always_ready() -> bool:
    return True
