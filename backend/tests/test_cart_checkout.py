from httpx import AsyncClient


async def _selection(client: AsyncClient):
    search = await client.post("/api/v1/search", json={"query": "lantern workshop"})
    product_id = search.json()["items"][0]["id"]
    detail = (await client.get(f"/api/v1/experiences/{product_id}")).json()
    option = detail["options"][0]
    return product_id, option["id"], option["slots"][0]["id"]


async def test_cart_checkout_is_validated_and_idempotent(client: AsyncClient):
    product_id, option_id, slot_id = await _selection(client)
    request = {
        "experience_id": product_id,
        "option_id": option_id,
        "slot_id": slot_id,
        "participants": [
            {"type": "adult", "count": 2, "age": 34},
            {"type": "child", "count": 1, "age": 8},
        ],
    }
    headers = {"Idempotency-Key": "add-lantern-001"}
    added = await client.post("/api/v1/cart/items", json=request, headers=headers)
    repeated = await client.post("/api/v1/cart/items", json=request, headers=headers)
    assert added.status_code == 200
    assert len(added.json()["items"]) == 1
    assert repeated.json() == added.json()

    prepared = await client.post("/api/v1/checkout/prepare")
    assert prepared.status_code == 200
    assert prepared.json()["confirmation_required"] is True

    confirmed = await client.post(
        "/api/v1/checkout/confirm",
        json={"confirmation": "CONFIRM", "customer_details": {}},
        headers={"Idempotency-Key": "checkout-001"},
    )
    repeated_booking = await client.post(
        "/api/v1/checkout/confirm",
        json={"confirmation": "CONFIRM", "customer_details": {}},
        headers={"Idempotency-Key": "checkout-001"},
    )
    assert confirmed.status_code == 200
    assert repeated_booking.json()["id"] == confirmed.json()["id"]
    voucher = confirmed.json()["voucher"]
    assert voucher["qr_payload"].startswith("tourism-poc://")
    assert voucher["qr_image_data_url"].startswith("data:image/png;base64,")


async def test_cart_selects_next_available_slot_when_quick_add_omits_it(
    client: AsyncClient,
):
    product_id, option_id, _ = await _selection(client)
    added = await client.post(
        "/api/v1/cart/items",
        json={
            "experience_id": product_id,
            "option_id": option_id,
            "participants": [{"type": "adult", "count": 2, "age": 34}],
        },
        headers={"Idempotency-Key": "quick-add-without-slot"},
    )

    assert added.status_code == 200
    item = added.json()["items"][0]
    assert item["slot_id"] is not None
    assert item["starts_at"] is not None
