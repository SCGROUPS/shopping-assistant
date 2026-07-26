"""What `GET /api/v1/health` says about intent extraction.

This block is the only place the two outages were visible from outside the
process. Search catches every extraction failure and answers 200 with a full
page, so `"status": "ok"` stayed true throughout - the counters beside it were
the only thing that moved. Anything watching this endpoint is watching those
three keys by name, which means renaming or dropping one is a silent change to
an alerting contract, and until this file existed no test failed when that
happened.
"""

from httpx import AsyncClient

from app.common.degradation import intent_health


async def test_health_reports_how_much_of_the_time_intent_extraction_is_failing(
    client: AsyncClient,
):
    """The numbers an alert would fire on, under the names it would read."""
    intent_health.reset()
    for _ in range(4):
        intent_health.record_call()
    intent_health.record_failure()

    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()

    # Asserted by name rather than by shape: an alert rule names these keys, so
    # renaming one is not a refactor, it is switching the alert off.
    assert "intent_extraction" in body, (
        "the only externally visible sign of an outage that keeps answering 200 is gone; "
        f"health returned {sorted(body)}"
    )
    reported = body["intent_extraction"]
    assert reported["calls"] == 4
    assert reported["failures"] == 1
    assert reported["failure_ratio"] == 0.25

    # `status` deliberately stays ok while a quarter of shoppers get a page the
    # service did not understand. That is the whole reason the ratio is here.
    assert body["status"] == "ok"
    intent_health.reset()


async def test_a_service_understanding_nobody_still_reports_a_ratio_of_one(
    client: AsyncClient,
):
    """Total failure has to be legible as a number, not as an absent field.

    In both outages every single extraction failed. If the block only appeared
    once some threshold was crossed, or if a fully-failing service reported a
    ratio of zero because nothing succeeded, the dashboard would have looked
    exactly like a healthy one - which is what it did look like for days.
    """
    intent_health.reset()
    for _ in range(3):
        intent_health.record_call()
        intent_health.record_failure()

    body = (await client.get("/api/v1/health")).json()

    assert body["intent_extraction"]["failure_ratio"] == 1.0, (
        "a service that understood nothing reported "
        f"{body['intent_extraction']['failure_ratio']} of its calls as failing"
    )
    intent_health.reset()


async def test_health_reports_a_quiet_service_without_inventing_a_failure(
    client: AsyncClient,
):
    """No traffic is not the same as no failures, and must not read as broken.

    A ratio computed as failures/calls divides by zero on a service nobody has
    searched yet - and this application scales to zero replicas, so every cold
    start passes through exactly that state. An alert on failure_ratio would
    fire on every scale-up if this returned anything but zero.
    """
    intent_health.reset()

    body = (await client.get("/api/v1/health")).json()

    assert body["intent_extraction"] == {"calls": 0, "failures": 0, "failure_ratio": 0.0}
