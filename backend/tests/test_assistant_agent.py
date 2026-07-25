"""The agent uses the tools, reasons over what they return, and curates the
answer. These tests pin the two things the application depends on: the tools
expose real capabilities the agent can drive, and every offering the agent
presents is traceable to one a tool actually returned.

Both scenarios come from a reported session. The shopper asked for somewhere
"in the ocean, not mountain" and was shown Marble Mountains; then asked for "Pho
restaurants in Hanoi old quarter" and was shown cycling in Mui Ne under "These
diverse options complement your current choice." The second happened because
the cross-sell tool has no query, so the request was discarded rather than
answered.
"""

import json
from typing import Any

import pytest

from app.api.schemas import ConversationCreate, MessageRequest
from app.assistant.provider import (
    AGENT_TOOLS,
    FINAL_ANSWER,
    SHOPPING_TOOLS,
    AgentAnswer,
    DemoAIProvider,
    Selection,
    _build_answer,
)
from app.assistant.service import AssistantService
from app.common.store import DemoStore


class ScriptedAgent(DemoAIProvider):
    """Drives the real tool executor with a scripted sequence of tool calls,
    then a final answer, standing in for the model."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls
        self.results: list[dict[str, Any]] = []

    async def run_agent(self, text, state, execute) -> AgentAnswer | None:
        offered: list[dict[str, Any]] = []
        for name, arguments in self.calls:
            result = await execute(name, arguments)
            self.results.append(result)
            offered.extend(result.get("items", []) or [])
        return AgentAnswer(
            message="Here is what fits.",
            selections=[
                Selection(experience_id=str(item["experience_id"]), reason="Fits your request.")
                for item in offered
            ],
        )


@pytest.fixture
def store() -> DemoStore:
    data = DemoStore()
    data.seed()
    return data


def tool(name: str) -> dict:
    return next(item for item in AGENT_TOOLS if item["name"] == name)


def test_exclusion_is_a_capability_of_the_search_tool() -> None:
    """Filtering out a refused subject belongs to the tool, not to hardcoded
    service logic the agent cannot see or control."""
    assert "exclude" in tool("search_experiences")["parameters"]["properties"]


def test_only_search_can_carry_a_subject() -> None:
    assert "query" in tool("search_experiences")["parameters"]["properties"]
    assert "query" not in tool("get_recommendations")["parameters"]["properties"]


def test_every_tool_is_described_for_the_agent() -> None:
    for item in AGENT_TOOLS:
        assert len(item["description"]) > 40, f"{item['name']} is under-described"


def test_strict_tools_require_every_declared_argument() -> None:
    for item in AGENT_TOOLS:
        parameters = item["parameters"]
        assert parameters["required"] == list(parameters["properties"]), item["name"]
        assert parameters["additionalProperties"] is False


def test_final_answer_ties_each_claim_to_an_offering() -> None:
    """The rendering convention: an answer references offerings by id."""
    selections = FINAL_ANSWER["parameters"]["properties"]["selections"]
    assert selections["items"]["required"] == ["experience_id", "reason"]


def test_agent_cannot_present_an_offering_no_tool_returned() -> None:
    """Grounding: an invented id is dropped rather than rendered."""
    answer = _build_answer(
        {
            "message": "Try these.",
            "selections": [
                {"experience_id": "real-1", "reason": "on the water"},
                {"experience_id": "hallucinated", "reason": "invented"},
            ],
        },
        offered={"real-1"},
    )
    assert [s.experience_id for s in answer.selections] == ["real-1"]


def test_agent_answer_keeps_reasons_and_clarification() -> None:
    answer = _build_answer(
        {
            "message": "Only one fits.",
            "selections": [{"experience_id": "a", "reason": "ocean-side"}],
            "clarification": "Shall I widen the dates?",
        },
        offered={"a"},
    )
    assert answer.selections[0].reason == "ocean-side"
    assert answer.clarification == "Shall I widen the dates?"


@pytest.mark.asyncio
async def test_search_tool_honours_the_exclusion_the_agent_passes(store: DemoStore) -> None:
    """The reported defect: "not mountain" still returned Marble Mountains.
    Anchored on a query that does surface mountain inventory, so the assertion
    cannot pass vacuously."""
    query = "marble mountains small-boat nature discovery"

    baseline = ScriptedAgent([("search_experiences", {"query": query, "exclude": []})])
    service = AssistantService(data=store, ai_provider=baseline)
    convo = await service.create("guest-baseline", ConversationCreate())
    shown = await service.respond(convo["id"], "guest-baseline", MessageRequest(message=query))
    assert [p for p in shown.products if "mountain" in p.title.casefold()], (
        "query must surface mountain inventory to be a real test"
    )

    negated = ScriptedAgent([("search_experiences", {"query": query, "exclude": ["mountain"]})])
    service.ai = negated
    convo = await service.create("guest-negation", ConversationCreate())
    response = await service.respond(
        convo["id"], "guest-negation", MessageRequest(message=f"{query}, not mountain")
    )
    offending = [p.title for p in response.products if "mountain" in p.title.casefold()]
    assert not offending, f"excluded subject still shown: {offending}"


@pytest.mark.asyncio
async def test_named_subject_is_searched_not_cross_sold(store: DemoStore) -> None:
    """The agent routes the Pho request to search, so the shopper's words reach
    the catalogue instead of being discarded by a query-less cross-sell."""
    agent = ScriptedAgent(
        [
            (
                "search_experiences",
                {"query": "Pho and street food in Hanoi Old Quarter", "exclude": []},
            )
        ]
    )
    service = AssistantService(data=store, ai_provider=agent)
    convo = await service.create("guest-subject", ConversationCreate())
    response = await service.respond(
        convo["id"],
        "guest-subject",
        MessageRequest(
            message="ok, may be recommend some good Pho restaurants in Hanoi old quarter"
        ),
    )

    assert "complement your current choice" not in response.message.casefold()
    titles = [f"{p.title} {p.location}".casefold() for p in response.products]
    assert titles, "a real destination should return options"
    assert [t for t in titles if "hanoi" in t or "old quarter" in t or "hoan kiem" in t], (
        f"expected Hanoi results, got {titles}"
    )


@pytest.mark.asyncio
async def test_tool_results_carry_ids_the_agent_can_refer_back_to(store: DemoStore) -> None:
    """Without an id on every offering the agent cannot honour final_answer."""
    agent = ScriptedAgent([("search_experiences", {"query": "street food", "exclude": []})])
    service = AssistantService(data=store, ai_provider=agent)
    convo = await service.create("guest-ids", ConversationCreate())
    await service.respond(convo["id"], "guest-ids", MessageRequest(message="street food"))

    items = agent.results[0]["items"]
    assert items
    for item in items:
        assert item["experience_id"]
        assert item["title"]
    # The agent has to be able to serialise what it is given.
    json.dumps(agent.results[0], default=str)


@pytest.mark.asyncio
async def test_a_refused_tool_is_reported_to_the_agent_not_raised(store: DemoStore) -> None:
    """The agent should be able to recover from a refusal rather than the turn
    failing outright."""
    agent = ScriptedAgent([("get_recommendations", {"experience_id": None})])
    service = AssistantService(data=store, ai_provider=agent)
    convo = await service.create("guest-refusal", ConversationCreate())
    await service.respond(convo["id"], "guest-refusal", MessageRequest(message="something similar"))
    assert agent.results[0]["error"] == "No selected experience"


@pytest.mark.asyncio
async def test_agent_never_books_on_its_own_say_so(store: DemoStore) -> None:
    agent = ScriptedAgent([("confirm_simulated_checkout", {})])
    service = AssistantService(data=store, ai_provider=agent)
    convo = await service.create("guest-confirm", ConversationCreate())
    await service.respond(
        convo["id"], "guest-confirm", MessageRequest(message="just book it for me")
    )
    assert agent.results[0]["error"] == "confirmation-required"


def test_shopping_tools_exclude_the_answer_tool() -> None:
    """final_answer ends the loop; it is not a catalogue capability."""
    assert FINAL_ANSWER not in SHOPPING_TOOLS
    assert FINAL_ANSWER in AGENT_TOOLS
