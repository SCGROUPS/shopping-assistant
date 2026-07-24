from httpx import AsyncClient

from app.api.schemas import ConversationCreate, MessageRequest
from app.assistant.provider import deterministic_intent
from app.assistant.service import AssistantService
from app.common.ranking import deterministic_embedding
from app.common.store import store


async def test_assistant_requires_explicit_confirmation(client: AsyncClient):
    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]

    search = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Find a family friendly indoor activity in Hoi An"},
    )
    assert search.status_code == 200
    product = search.json()["products"][0]
    assert {
        "image_url",
        "title",
        "destination",
        "location",
        "rating",
        "review_count",
        "price",
        "currency",
        "reason",
        "badges",
        "availability",
        "options",
        "actions",
    }.issubset(product)
    action_types = {action["type"] for action in product["actions"]}
    assert {"CHECK_AVAILABILITY", "ADD_TO_CART"}.issubset(action_types)
    assert search.json()["actions"] == []

    added = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Add the first one to cart"},
    )
    assert added.status_code == 200
    assert "Added" in added.json()["message"]

    premature_conversation = await client.post("/api/v1/conversations", json={})
    premature_id = premature_conversation.json()["id"]
    premature = await client.post(
        f"/api/v1/conversations/{premature_id}/messages?stream=false",
        json={"message": "confirm"},
    )
    assert "cannot book yet" in premature.json()["message"]

    prepared = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "prepare checkout"},
    )
    assert "Reply exactly" in prepared.json()["message"]
    confirmed = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "confirm"},
    )
    assert "is confirmed" in confirmed.json()["message"]
    assert confirmed.json()["state_patch"]["booking_id"]


async def test_assistant_sse_contract(client: AsyncClient):
    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": "Find food tours in Hoi An"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: status" in response.text
    assert "event: completed" in response.text


async def test_assistant_defaults_to_json_contract(client: AsyncClient):
    created = await client.post("/api/v1/conversations", json={})
    assert set(created.json()) == {"id"}
    conversation_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": "Find a cruise in Da Nang"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert {"message", "products", "actions", "citations"}.issubset(response.json())


async def test_assistant_adds_the_persisted_party(client: AsyncClient):
    created = await client.post(
        "/api/v1/conversations",
        json={"party": [{"type": "adult", "count": 3}]},
    )
    conversation_id = created.json()["id"]
    await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Find a cruise in Da Nang"},
    )
    added = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Add the first one to cart"},
    )
    cart = await client.get("/api/v1/cart")

    assert added.status_code == 200
    assert "3 adults" in added.json()["message"]
    assert cart.json()["items"][0]["quantity"] == 3


class AzureLikeProvider:
    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def extract_intent(self, text: str):
        return deterministic_intent(text)

    async def plan_action(self, text: str, state: dict):
        return "search_experiences"

    async def enhance_assistant(self, prompt: str, facts: list[dict]):
        return "Azure-enhanced grounded recommendations."


async def test_azure_enhancement_path_keeps_structured_commerce_payload():
    service = AssistantService(store, AzureLikeProvider())
    conversation = await service.create("azure-test", ConversationCreate())
    response = await service.respond(
        conversation["id"],
        "azure-test",
        MessageRequest(message="Find family activities in Hoi An"),
    )
    assert response.message == "Azure-enhanced grounded recommendations."
    assert response.products
    assert response.products[0].image_url
    assert {action.type for action in response.products[0].actions} >= {
        "CHECK_AVAILABILITY",
        "ADD_TO_CART",
    }
