from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.schemas import Participant, SearchFilters
from app.common import currency as fx
from app.common.analytics import funnel_report
from app.common.persistence import demand_stats
from app.common.urgency import scarcity, social_proof


def _slot(days_ahead: int, capacity: int):
    return {
        "id": f"slot-{days_ahead}",
        "status": "AVAILABLE",
        "starts_at": datetime.now(UTC) + timedelta(days=days_ahead),
        "capacity_remaining": capacity,
    }


def _product(slots):
    return {
        "options": [
            {"active": True, "slots": slots},
        ]
    }


def _window():
    start = datetime.now(UTC)
    return SearchFilters(visit_start=start, visit_end=start + timedelta(days=10))


def test_scarcity_stays_silent_when_supply_is_comfortable():
    """A badge that always shows means nothing and trains shoppers to ignore it."""
    plentiful = _product([_slot(index, 30) for index in range(1, 8)])
    assert scarcity(plentiful, _window()) is None


def test_scarcity_reports_real_remaining_capacity():
    tight = _product([_slot(1, 2)])
    assert scarcity(tight, _window()) == "Only 2 places left"
    single = _product([_slot(1, 1)])
    assert scarcity(single, _window()) == "Only 1 place left"


def test_scarcity_ignores_slots_the_party_cannot_use():
    """A slot with two seats is not scarcity for a family of four; it is absence."""
    product = _product([_slot(1, 2)])
    party = [Participant(type="adult", count=2), Participant(type="child", count=2)]
    assert scarcity(product, _window(), party) is None


def test_scarcity_respects_the_shoppers_own_dates():
    product = _product([_slot(20, 1)])
    start = datetime.now(UTC)
    narrow = SearchFilters(visit_start=start, visit_end=start + timedelta(days=2))
    assert scarcity(product, narrow) is None
    wide = SearchFilters(visit_start=start, visit_end=start + timedelta(days=25))
    assert scarcity(product, wide) is not None


def test_social_proof_is_silent_without_real_demand():
    """Never dress the seeded review count up as recent booking activity."""
    assert social_proof(None) is None
    assert social_proof({"bookings": 1, "cart_adds": 2, "views": 3}) is None


def test_social_proof_prefers_the_strongest_honest_signal():
    assert social_proof({"bookings": 12, "views": 900}) == (
        "12 travellers booked this recently"
    )
    assert social_proof({"bookings": 0, "cart_adds": 9}) == (
        "In 9 travellers' plans right now"
    )
    assert social_proof({"views": 40}) == "Viewed 40 times recently"


def test_currency_conversion_round_trips_within_tolerance():
    usd = fx.convert(2_540_000, "VND", "USD")
    assert usd == pytest.approx(100.0, abs=0.01)
    assert fx.convert(usd, "USD", "VND") == pytest.approx(2_540_000, rel=0.001)
    assert fx.convert(100, "USD", "USD") == 100


def test_zero_decimal_currencies_are_not_shown_with_cents():
    assert fx.convert(100, "USD", "VND") % 1 == 0
    assert fx.convert(100, "USD", "JPY") % 1 == 0
    assert fx.supported("usd")
    assert not fx.supported("XYZ")
    with pytest.raises(ValueError):
        fx.convert(1, "VND", "XYZ")


async def test_display_currency_never_replaces_the_charged_price(client: AsyncClient):
    """Money that is charged must not depend on a presentation rate table."""
    response = await client.post(
        "/api/v1/search",
        json={"query": "Hoi An", "display_currency": "USD"},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["currency"] == "VND"
    assert item["price"] > 1000
    assert item["display_currency"] == "USD"
    assert 0 < item["display_price"] < item["price"]


async def test_no_display_conversion_when_the_currency_already_matches(
    client: AsyncClient,
):
    response = await client.post(
        "/api/v1/search",
        json={"query": "Hoi An", "display_currency": "VND"},
    )
    item = response.json()["items"][0]
    assert item["display_price"] is None
    assert item["display_currency"] is None


async def test_social_proof_appears_once_demand_is_real(client: AsyncClient):
    search = await client.post("/api/v1/search", json={"query": "Hoi An"})
    target = search.json()["items"][0]
    assert target["social_proof"] is None

    for _ in range(8):
        await client.post(
            "/api/v1/events",
            json={"event_type": "booking_completed", "experience_id": target["id"]},
        )

    again = await client.post("/api/v1/search", json={"query": "Hoi An"})
    refreshed = next(
        item for item in again.json()["items"] if item["id"] == target["id"]
    )
    assert refreshed["social_proof"] == "8 travellers booked this recently"


def test_scarcity_copy_is_grammatical_at_one_place():
    """A count of one must not read "1 places" — sloppy copy reads as a fake badge."""
    product = _product([_slot(day, 1) for day in range(1, 12)])
    assert scarcity(product, party=[Participant(type="adult", count=1, age=30)]) == (
        "Some times down to 1 place"
    )
    tight = _product([_slot(1, 1)])
    assert scarcity(tight, party=[Participant(type="adult", count=1, age=30)]) == (
        "Only 1 place left"
    )


async def test_booking_is_attributed_to_each_booked_experience(client: AsyncClient):
    """A booking with no experience attached is invisible to demand stats.

    `booking_completed` carries the heaviest weight in both the session vector
    and the conversion term, so dropping the attribution silently discards the
    strongest ranking signal the product has.
    """
    headers = {"X-Session-ID": "attribution-session"}
    search = await client.post(
        "/api/v1/search", json={"query": "lantern workshop"}, headers=headers
    )
    booked_ids = []
    for item in search.json()["items"][:2]:
        detail = (await client.get(f"/api/v1/experiences/{item['id']}")).json()
        option = detail["options"][0]
        added = await client.post(
            "/api/v1/cart/items",
            json={
                "experience_id": item["id"],
                "option_id": option["id"],
                "slot_id": option["slots"][0]["id"],
                "participants": [{"type": "adult", "count": 2, "age": 34}],
            },
            headers={**headers, "Idempotency-Key": f"attr-add-{item['id']}"},
        )
        if added.status_code == 200:
            booked_ids.append(item["id"])

    await client.post("/api/v1/checkout/prepare", headers=headers)
    confirmed = await client.post(
        "/api/v1/checkout/confirm",
        json={
            "confirmation": "CONFIRM",
            "customer_details": {},
            "placement": "assistant",
        },
        headers={**headers, "Idempotency-Key": "attr-checkout"},
    )
    assert confirmed.status_code == 200

    stats = await demand_stats()
    attributed = {str(key): value for key, value in stats.items()}
    for experience_id in booked_ids:
        assert attributed[experience_id]["bookings"] >= 1

    report = await funnel_report()
    # Attribution must survive to the funnel, otherwise the surface comparison
    # the holdout exists to settle cannot be computed.
    assert report["surfaces"]["assistant"]["booking_completed"] == len(booked_ids)
