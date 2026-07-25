from httpx import AsyncClient

from app.api.schemas import ConversationCreate, MessageRequest
from app.assistant.provider import ToolPlan, deterministic_intent
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
        return ToolPlan(tool="search_experiences", arguments={"query": text, "exclusions": []})

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


async def test_planner_decision_wins_over_keyword_match(client: AsyncClient):
    from app.api.schemas import AssistantContext
    from app.assistant.service import keyword_tool

    # "compare" appears in the sentence but the shopper is asking to search.
    assert keyword_tool("compare") == "compare_experiences"

    class PlannerProvider:
        async def plan_action(self, text, state):
            return ToolPlan(tool="search_experiences", arguments={"query": text, "exclusions": []})

        async def enhance_assistant(self, prompt, facts):
            return None

        async def embed(self, text):
            return deterministic_embedding(text)

        async def extract_intent(self, text):
            return deterministic_intent(text)

    service = AssistantService(store, PlannerProvider())
    conversation = await service.create("test-session", ConversationCreate())
    response = await service.respond(
        conversation["id"],
        "test-session",
        MessageRequest(
            message="I want to compare nothing, just show me Hoi An food tours",
            context=AssistantContext(),
        ),
    )
    assert response.products, "planner asked for a search, so results are expected"


async def test_planner_alone_cannot_confirm_a_booking(client: AsyncClient):
    class ConfirmingProvider:
        async def plan_action(self, text, state):
            return ToolPlan(tool="confirm_simulated_checkout")

        async def enhance_assistant(self, prompt, facts):
            return None

        async def embed(self, text):
            return deterministic_embedding(text)

        async def extract_intent(self, text):
            return deterministic_intent(text)

    service = AssistantService(store, ConfirmingProvider())
    conversation = await service.create("test-session", ConversationCreate())
    response = await service.respond(
        conversation["id"],
        "test-session",
        MessageRequest(message="tell me about the lantern boat"),
    )
    assert "confirmed" not in response.message.casefold()


async def test_context_carries_storefront_filters(client: AsyncClient):
    from app.api.schemas import AssistantContext, Participant, SearchFilters

    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={
            "message": "what should I do",
            "context": AssistantContext(
                filters=SearchFilters(destination="Hoi An"),
                party=[Participant(type="adult", count=2)],
                query="things to do",
                result_count=12,
            ).model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    products = response.json()["products"]
    assert products, "storefront destination should scope the assistant search"
    assert all(product["destination"] == "Hoi An" for product in products)


async def test_referent_resolution_picks_the_named_experience(client: AsyncClient):
    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]
    search = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Find a family friendly indoor activity in Hoi An"},
    )
    products = search.json()["products"]
    assert len(products) >= 2
    second = products[1]
    added = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": f"add {second['title']} to my cart"},
    )
    assert added.status_code == 200
    assert second["title"] in added.json()["message"]
