"""The gate that would have caught both outages at the deploy that caused them.

Each outage was a request the deployed model rejects with 400 on every call,
made invisible by a fallback that answers with a normal-looking page. One real
call at startup turns that into a failed deployment.
"""

import asyncio

import pytest

from app.common.degradation import intent_health
from app.common.intent_probe import ProbeOutcome, probe_intent

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
    assert result.rejected, (
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
    assert not result.rejected
    assert result.outcome is ProbeOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_a_working_deployment_passes():
    result = await probe_intent(WorkingProvider(), **VOCABULARY)
    assert result.passed


@pytest.mark.asyncio
async def test_readiness_is_withheld_while_the_model_rejects_us(monkeypatch):
    """The point of the probe: a broken revision must not take traffic."""
    from app.main import ready

    intent_health.reset()
    monkeypatch.setattr("app.main.database_ready", _always_ready)

    assert (await ready()).status_code == 503, (
        "a revision reported itself ready before anything had checked whether the model "
        "understands it, so a rejecting revision takes traffic in the window before the "
        "probe answers - which is the whole window that matters at startup"
    )

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


def _pretend_azure_is_configured(monkeypatch, main) -> None:
    """The probe skips itself when no real deployment is configured.

    Without an endpoint `build_ai_provider` hands back the in-process demo
    provider, which cannot reject anything - so probing it would record a pass
    for a conversation that never took place.
    """
    monkeypatch.setattr(main.settings, "azure_openai_endpoint", "https://probe.invalid/")
    monkeypatch.setattr(main.settings, "demo_mode", False)


@pytest.mark.asyncio
async def test_a_rejection_is_still_caught_when_the_catalogue_will_not_load(monkeypatch):
    """The probe must reach the model even if the database is still waking.

    The first version loaded the catalogue for its enums and recorded a *pass*
    when that raised. A cold database at startup would therefore have let the
    revision through without a single word being said to the model - the same
    silence the probe exists to break, reintroduced by the probe itself.
    """
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)

    async def _no_catalogue():
        raise RuntimeError("database is still starting up")

    monkeypatch.setattr("app.common.persistence.catalog_products", _no_catalogue)
    monkeypatch.setattr(
        main,
        "build_ai_provider",
        lambda: RejectingProvider("'none' is not supported"),
        raising=False,
    )
    monkeypatch.setattr(
        "app.assistant.provider.build_ai_provider",
        lambda: RejectingProvider("'none' is not supported"),
    )

    await main._run_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and not probe.ok, (
        "the catalogue was unavailable so the probe reported a pass without ever asking "
        "the model, and a revision that rejects every query would have taken traffic"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_an_unreachable_model_is_recorded_as_unverified_not_as_healthy(monkeypatch):
    """Unreachable is not evidence our request is wrong, but it is not a pass either."""
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_ATTEMPTS", 2)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(
        "app.assistant.provider.build_ai_provider",
        lambda: FlakyProvider(TimeoutError("timed out")),
    )

    await main._run_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and probe.ok, "an unreachable model must not block the deploy"
    assert "unverified" in probe.detail, (
        f"reported as a clean pass ({probe.detail!r}); nothing ever confirmed the "
        "deployment accepts our request"
    )
    intent_health.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_a_credential_problem_does_not_darken_the_storefront(status):
    """An expired key is not evidence that our request is malformed.

    Refusing to serve on a 401 would turn a rotated secret or a managed
    identity that has not warmed up into a dark shop - and the revision this
    one would be protecting holds exactly the same credentials, so there is
    nothing better to fall back to.
    """
    result = await probe_intent(FlakyProvider(_Rejected("no", status_code=status)), **VOCABULARY)
    assert not result.rejected, (
        f"HTTP {status} blocked the deployment; a credential wobble would take the whole "
        "storefront down rather than degrade it"
    )


@pytest.mark.asyncio
async def test_one_rejection_is_confirmed_before_it_darkens_anything(monkeypatch):
    """A single 400 can be content filtering. Two is the defect."""
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.0)

    calls: list[int] = []

    def _provider():
        calls.append(1)
        return RejectingProvider("'none' is not supported")

    monkeypatch.setattr("app.assistant.provider.build_ai_provider", _provider)
    await main._run_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and not probe.ok
    assert len(calls) == main.REJECTIONS_TO_CONFIRM, (
        f"asked {len(calls)} times; a confirmed rejection must stop immediately rather "
        "than spend the whole retry budget on an answer it already has"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_rejection_is_not_downgraded_by_a_later_silence(monkeypatch):
    """The dangerous ordering: rejected once, then the upstream stops answering.

    "The model did not reply" is not evidence that a request it already refused
    has become valid. If the later silence were allowed to settle as
    "unverified", the probe would hand traffic to a revision it had already
    caught rejecting every query.
    """
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_ATTEMPTS", 3)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.0)

    answers = [
        RejectingProvider("'none' is not supported"),
        FlakyProvider(TimeoutError("timed out")),
        FlakyProvider(TimeoutError("timed out")),
    ]
    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: answers.pop(0))

    await main._run_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and not probe.ok, (
        f"recorded {probe.detail if probe else None!r}; a rejection the probe had already "
        "seen was forgotten because the model later went quiet"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_probe_that_hangs_still_produces_a_verdict(monkeypatch):
    """Readiness now insists on an answer, so an answer has to be certain.

    Without this backstop a probe blocked on a socket that never returns would
    leave readiness failing forever, and the deployment would be killed for a
    fault in the probe rather than in the service.
    """
    import app.main as main

    intent_health.reset()
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.05)

    async def _never_answers() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(main, "_run_intent_probe", _never_answers)
    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None, "readiness would have stayed 503 forever"
    assert probe.ok and "unverified" in probe.detail
    intent_health.reset()


@pytest.mark.asyncio
async def test_the_deadline_never_overwrites_a_verdict_already_reached(monkeypatch):
    """A slow probe that already found the defect must keep its finding."""
    import app.main as main

    intent_health.reset()
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.05)

    async def _rejects_then_hangs() -> None:
        intent_health.record_probe(ok=False, detail="the intent deployment rejected our request")
        await asyncio.sleep(30)

    monkeypatch.setattr(main, "_run_intent_probe", _rejects_then_hangs)
    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and not probe.ok, (
        "the deadline overwrote a confirmed rejection with a pass, so the revision the "
        "probe had already caught would have taken traffic"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_provider_that_cannot_reject_is_not_recorded_as_a_pass(monkeypatch):
    """No endpoint means the demo provider, which accepts everything."""
    import app.main as main

    intent_health.reset()
    monkeypatch.setattr(main.settings, "azure_openai_endpoint", "")

    await main._run_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and probe.ok
    assert "not applicable" in probe.detail, (
        f"recorded {probe.detail!r}, which claims the deployment accepted our request - "
        "but nothing was ever asked, because there is no deployment"
    )
    intent_health.reset()
