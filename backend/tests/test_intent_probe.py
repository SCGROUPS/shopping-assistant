"""The gate that would have caught both outages at the deploy that caused them.

Each outage was a request the deployed model rejects with 400 on every call,
made invisible by a fallback that answers with a normal-looking page. One real
call at startup turns that into a failed deployment.
"""

import asyncio
import logging

import pytest

from app import main
from app.common.degradation import intent_health
from app.common.intent_probe import ProbeOutcome, probe_intent

VOCABULARY = {"categories": ["Food", "Transport"], "destinations": ["Hoi An", "Hanoi"]}


@pytest.fixture(autouse=True)
def _no_rejection_carried_between_tests():
    """`_pending_rejection` is module state, so it leaks across tests.

    It was leaking usefully - one test's rejection was the only thing making
    another test exercise the supervisor's reset. That is a coupling, not an
    assertion: it survives only until someone reorders the file. Cleared here,
    with the coverage it was accidentally providing written down explicitly in
    `test_a_supervised_run_starts_from_a_clean_slate`.
    """
    import app.main as main

    main._pending_rejection = None
    yield
    main._pending_rejection = None


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


@pytest.mark.asyncio
async def test_a_rejection_survives_the_probe_being_cut_short(monkeypatch):
    """The fail-open the deadline reopened after the retry loop closed it.

    Review constructed this: attempt one is a real rejection, which on its own
    is below the confirmation threshold and records nothing; attempt two hangs
    past the deadline. `asyncio.wait_for` *cancels* the probe, so a rejection
    remembered in a coroutine local dies with it, the supervisor sees no
    verdict, and records "unverified" - which readiness treats as ready.

    The evidence therefore has to outlive the cancellation. This is the same
    blocker as `probe is None or probe.ok`, reached by a different route, and
    it fails in exactly the case the probe exists for: a deployment that
    rejects us while something else is slow.
    """
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_ATTEMPTS", 3)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.2)

    class _Hangs:
        async def extract_intent(self, *args, **kwargs):
            await asyncio.sleep(30)

    answers = [RejectingProvider("'none' is not supported"), _Hangs(), _Hangs()]
    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: answers.pop(0))

    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None, "readiness would have stayed 503 forever"
    assert not probe.ok, (
        f"recorded {probe.detail!r} as servable; the deployment had already rejected the "
        "probe and the verdict was lost when the deadline cancelled the coroutine holding it"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_rejections_too_slow_to_be_confirmed_still_darken_the_revision(monkeypatch):
    """The second route review found: real rejections, none of them fast.

    Every attempt is a genuine rejection, but each takes long enough that the
    deadline lands before the confirmation threshold is reached. Requiring two
    rejections must not become a way for a slow upstream to convert a definite
    "no" into a pass.
    """
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_ATTEMPTS", 3)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.25)

    class _SlowlyRejects:
        async def extract_intent(self, *args, **kwargs):
            await asyncio.sleep(0.15)
            raise _Rejected("'none' is not supported")

    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: _SlowlyRejects())

    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and not probe.ok, (
        f"recorded {probe.detail if probe else None!r}; the deployment rejected every "
        "request it was sent, and slowness alone turned that into a pass"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_new_probe_does_not_inherit_the_previous_one_s_rejection(monkeypatch):
    """The cost of moving the evidence out of the coroutine.

    Rejection state that outlives a cancellation also outlives the whole probe,
    so a later run on a repaired deployment must not be condemned by what an
    earlier one saw. Otherwise fixing the deployment would never clear the
    verdict and the revision could not recover.
    """
    import app.main as main

    intent_health.reset()
    _pretend_azure_is_configured(monkeypatch, main)
    monkeypatch.setattr(main, "PROBE_ATTEMPTS", 1)
    monkeypatch.setattr(main, "PROBE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.2)

    class _Hangs:
        async def extract_intent(self, *args, **kwargs):
            await asyncio.sleep(30)

    answers = [RejectingProvider("'none' is not supported")]
    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: answers.pop(0))
    await main._supervise_intent_probe()
    assert main._pending_rejection is not None

    intent_health.reset()
    monkeypatch.setattr("app.assistant.provider.build_ai_provider", lambda: WorkingProvider())
    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None and probe.ok, (
        f"recorded {probe.detail if probe else None!r}; the deployment now accepts our "
        "requests and the revision is still being held down by an older verdict"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_a_supervised_run_starts_from_a_clean_slate(monkeypatch):
    """A repaired deployment must not be condemned by an earlier run's verdict.

    `_pending_rejection` outlives the coroutine on purpose - that is what makes
    a cancelled probe still able to report a rejection. The cost is that it
    also outlives the *run*, so the supervisor clears it before starting one.

    Without this test that reset is guarded only by another test happening to
    leave the global dirty first, which stops being true the moment the file is
    reordered or a cleanup fixture is added - and both have now happened.
    """
    import app.main as main

    intent_health.reset()
    monkeypatch.setattr(main, "_pending_rejection", "stale verdict from an earlier run")
    monkeypatch.setattr(main, "PROBE_DEADLINE_SECONDS", 0.05)

    async def _hangs() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(main, "_run_intent_probe", _hangs)
    await main._supervise_intent_probe()

    probe = intent_health.probe()
    assert probe is not None, "readiness would have stayed 503 forever"
    assert probe.ok, (
        f"recorded {probe.detail!r}; this deployment was merely slow, and it was held "
        "down by a rejection that belonged to an earlier run"
    )
    intent_health.reset()


@pytest.mark.asyncio
async def test_the_skip_log_carries_the_word_the_sev_0_alert_matches(monkeypatch, caplog):
    """The whole detection surface for the quietest outage is one log line.

    When no endpoint is configured the probe records ok=True - correctly, it
    cannot judge a model it never called - so readiness passes and the failure
    counters stay at zero. Nothing else in the process says anything. The sev-0
    rule in infra/bicep/resources.bicep fires on `has 'skipped'`, which makes
    that single word the difference between a paged outage and a silent one.

    Demoting this line to info, or rewording it to drop "skipped", previously
    failed no test in the suite.
    """
    settings = main.get_settings()
    monkeypatch.setattr(settings, "azure_openai_endpoint", "", raising=False)
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    intent_health.reset()

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        await main._run_intent_probe()

    skipped = [r for r in caplog.records if "Intent probe" in r.getMessage()]
    assert skipped, "the probe skipped the model and said nothing at all"
    assert any(r.levelno >= logging.ERROR for r in skipped), (
        "logged below ERROR; the alert rule reads error output"
    )
    assert any("skipped" in r.getMessage() for r in skipped), (
        "the sev-0 rule matches on the word 'skipped' and it is no longer in "
        "the message; the alert would never fire"
    )
