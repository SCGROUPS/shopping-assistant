import os
from asyncio import gather
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.common.database import session_factory
from app.common.models import BehaviorEvent, ConversationMessage, ShoppingSession
from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="POSTGRES_TEST_DATABASE_URL is required",
)


async def test_postgres_catalog_conversation_and_checkout_persist():
    session_id = f"postgres-{uuid4()}"
    headers = {"X-Session-ID": session_id}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        listed = await client.get("/api/v1/experiences", params={"limit": 5})
        assert listed.status_code == 200
        assert listed.json()["total"] >= 360

        searched = await client.post(
            "/api/v1/search",
            json={
                "query": "family friendly indoor activities in Hoi An",
                "party": [{"type": "adult", "count": 3}],
            },
        )
        assert searched.status_code == 200
        product = searched.json()["items"][0]

        created = await client.post(
            "/api/v1/conversations",
            json={
                "query": "family friendly indoor activities in Hoi An",
                "filters": searched.json()["effective_filters"],
                "result_ids": [product["id"]],
                "party": [{"type": "adult", "count": 3}],
            },
        )
        conversation_id = created.json()["id"]
        streamed = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages?stream=true",
            json={"message": "Check availability"},
            headers={**headers, "Accept": "text/event-stream"},
        )
        assert streamed.status_code == 200
        assert "event: status" in streamed.text
        assert "event: completed" in streamed.text

        detail = await client.get(f"/api/v1/experiences/{product['id']}")
        option = detail.json()["options"][0]
        slot = option["slots"][0]
        item = {
            "experience_id": product["id"],
            "option_id": option["id"],
            "slot_id": slot["id"],
            "participants": [{"type": "adult", "count": 3}],
        }
        added = await client.post(
            "/api/v1/cart/items",
            json=item,
            headers={**headers, "Idempotency-Key": "postgres-add-001"},
        )
        repeated = await client.post(
            "/api/v1/cart/items",
            json=item,
            headers={**headers, "Idempotency-Key": "postgres-add-001"},
        )
        assert added.status_code == 200
        assert repeated.json() == added.json()

        concurrent_key = "postgres-add-concurrent-001"
        concurrent_responses = await gather(
            client.post(
                "/api/v1/cart/items",
                json=item,
                headers={**headers, "Idempotency-Key": concurrent_key},
            ),
            client.post(
                "/api/v1/cart/items",
                json=item,
                headers={**headers, "Idempotency-Key": concurrent_key},
            ),
        )
        assert all(response.status_code == 200 for response in concurrent_responses)
        assert concurrent_responses[0].json() == concurrent_responses[1].json()
        assert len(concurrent_responses[0].json()["items"]) == 2

        checkout_headers = {
            **headers,
            "Idempotency-Key": "postgres-checkout-001",
        }
        confirmed, replayed = await gather(
            client.post(
                "/api/v1/checkout/confirm",
                json={"confirmation": "CONFIRM", "customer_details": {}},
                headers=checkout_headers,
            ),
            client.post(
                "/api/v1/checkout/confirm",
                json={"confirmation": "CONFIRM", "customer_details": {}},
                headers=checkout_headers,
            ),
        )
        assert confirmed.status_code == 200
        assert replayed.json() == confirmed.json()
        booking_id = confirmed.json()["id"]
        fetched = await client.get(f"/api/v1/bookings/{booking_id}")
        assert fetched.json()["voucher"]["qr_image_data_url"].startswith(
            "data:image/png;base64,"
        )

    assert session_factory is not None
    async with session_factory() as db:
        shopping_session = await db.scalar(
            select(ShoppingSession).where(
                ShoppingSession.anonymous_id == session_id
            )
        )
        assert shopping_session is not None
        event_count = await db.scalar(
            select(func.count())
            .select_from(BehaviorEvent)
            .where(BehaviorEvent.session_id == shopping_session.id)
        )
        message_count = await db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
        )
        assert event_count and event_count >= 3
        assert message_count == 2
