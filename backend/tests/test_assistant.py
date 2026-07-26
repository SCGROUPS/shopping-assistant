from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.api import routes
from app.api.schemas import ConversationCreate, MessageRequest
from app.assistant.provider import DemoAIProvider, ToolPlan, deterministic_intent
from app.assistant.service import AssistantService
from app.common.ranking import deterministic_embedding
from app.common.store import store


class ScriptedPlanner(DemoAIProvider):
    """Stands in for the model's tool planning.

    The service no longer chooses a tool from the shopper's words - the agent
    does - so a test that wants a tool run has to say which one, the same way
    the model would. The script is keyed on the exact message the test sends,
    so nothing here interprets language.
    """

    def __init__(self, script: dict[str, Callable[[dict[str, Any]], ToolPlan | None]]) -> None:
        self.script = script

    async def plan_action(self, text: str, state: dict[str, Any]) -> ToolPlan | None:
        build = self.script.get(text)
        return build(state) if build else None


@pytest.fixture
def planner():
    """Give the HTTP-level assistant a scripted planner for one test."""

    def install(script: dict[str, Callable[[dict[str, Any]], ToolPlan | None]]) -> None:
        routes.assistant_service.ai = ScriptedPlanner(script)

    original = routes.assistant_service.ai
    yield install
    routes.assistant_service.ai = original


async def test_assistant_requires_explicit_confirmation(client: AsyncClient, planner):
    planner(
        {
            "Add the first one to cart": lambda state: ToolPlan("add_to_cart"),
            "prepare checkout": lambda state: ToolPlan("prepare_checkout"),
            "confirm": lambda state: ToolPlan(
                "confirm_simulated_checkout", {"shopper_confirmed": True}
            ),
        }
    )
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
    # Assert on the code, not the English. A test that reads the prose is a test
    # that has to be rewritten the day the sentence is translated, and it proves
    # nothing about what a Vietnamese shopper is shown.
    assert added.json()["message_code"] == "assistant.msg.added"

    premature_conversation = await client.post("/api/v1/conversations", json={})
    premature_id = premature_conversation.json()["id"]
    premature = await client.post(
        f"/api/v1/conversations/{premature_id}/messages?stream=false",
        json={"message": "confirm"},
    )
    assert premature.json()["message_code"] == "assistant.msg.cannotBookYet"

    prepared = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "prepare checkout"},
    )
    assert prepared.json()["message_code"] == "assistant.msg.checkoutTotal"
    assert prepared.json()["message_vars"]["count"] >= 1
    confirmed = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "confirm"},
    )
    assert confirmed.json()["message_code"] == "assistant.msg.booked"
    assert confirmed.json()["message_vars"]["voucher"]
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


async def test_assistant_adds_the_persisted_party(client: AsyncClient, planner):
    planner({"Add the first one to cart": lambda state: ToolPlan("add_to_cart")})
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

    async def extract_intent(self, text: str, **_):
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


async def test_the_plan_decides_the_tool(client: AsyncClient):
    from app.api.schemas import AssistantContext

    class PlannerProvider:
        async def plan_action(self, text, state):
            return ToolPlan(tool="search_experiences", arguments={"query": text, "exclusions": []})

        async def enhance_assistant(self, prompt, facts):
            return None

        async def embed(self, text):
            return deterministic_embedding(text)

        async def extract_intent(self, text, **_):
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

        async def extract_intent(self, text, **_):
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


async def test_referent_resolution_uses_the_id_the_agent_named(client: AsyncClient, planner):
    """The agent names an offering by id, so any second result can be reached.

    Reading an ordinal or a title out of the message only ever worked in
    English, and fell back to the first result - reporting success - whenever
    it did not.
    """
    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]
    search = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Find a family friendly indoor activity in Hoi An"},
    )
    products = search.json()["products"]
    assert len(products) >= 2
    second = products[1]
    planner(
        {
            "them cai nay vao gio hang": lambda state: ToolPlan(
                "add_to_cart", {"experience_id": second["experience_id"]}
            ),
        }
    )
    added = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "them cai nay vao gio hang"},
    )
    assert added.status_code == 200
    assert second["title"] in added.json()["message"]


async def test_an_offering_outside_the_results_is_refused(client: AsyncClient, planner):
    """A reference we cannot place is an error, never the first result."""
    from uuid import uuid4

    created = await client.post("/api/v1/conversations", json={})
    conversation_id = created.json()["id"]
    await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "Find a family friendly indoor activity in Hoi An"},
    )
    planner(
        {
            "add that one": lambda state: ToolPlan("add_to_cart", {"experience_id": str(uuid4())}),
        }
    )
    added = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "add that one"},
    )
    assert added.status_code == 409


async def test_the_shopper_is_told_when_the_assistant_cannot_act(client: AsyncClient):
    """An outage must not look like an answer.

    With no model there is no plan, and the only thing the assistant can do is
    search for the text as written. It used to do exactly that and say nothing:
    a shopper asking to add something to their cart got a list of search
    results, phrased as though it were the reply to their request, with the cart
    untouched. The results are still worth showing - but the client has to be
    able to tell the shopper that the thing they asked for did not happen.
    """
    conversation = await client.post("/api/v1/conversations", json={})
    conversation_id = conversation.json()["id"]
    reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages?stream=false",
        json={"message": "add the sunrise cruise to my cart"},
    )
    body = reply.json()
    assert body["degraded"] is True
    # And the sentence it did produce is a code, not English prose, because the
    # shopper this protects is the one who is not reading English.
    assert body["message_code"] == "assistant.msg.searchResults"
    assert body["message_vars"]["count"] == len(body["products"])
