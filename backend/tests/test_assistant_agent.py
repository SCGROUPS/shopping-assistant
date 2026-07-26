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
    """Grounding: an invented id sinks the whole answer, not just its own card.

    This used to keep the good selection and drop the invented one, which read
    as the safe choice and was not: `message` still described both, so the
    shopper was told about two experiences and shown one card, with no way to
    tell which half was real. The prose cannot be repaired without reading it,
    so the answer is declined and the deterministic path replies instead.
    """
    assert (
        _build_answer(
            {
                "message": "Try these.",
                "selections": [
                    {"experience_id": "real-1", "reason": "on the water"},
                    {"experience_id": "hallucinated", "reason": "invented"},
                ],
            },
            offered={"real-1"},
        )
        is None
    )


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


async def test_the_agent_cannot_prepare_and_confirm_in_one_turn(store: DemoStore) -> None:
    """A yes cannot answer a total the shopper has not been shown.

    The agent may call several tools before it replies, so `prepare_checkout`
    followed by `confirm_simulated_checkout` in the same turn sets the pending
    state and then satisfies it, and the attestation is about a summary that
    never left the server. Catalogue text can steer the agent; it cannot make
    the shopper send another message, which is why the turn boundary is the
    part worth enforcing.
    """
    from app.assistant.provider import AgentAnswer, DemoAIProvider
    from app.assistant.service import AssistantService

    class PrepareThenConfirm(DemoAIProvider):
        def __init__(self):
            self.prepared = None
            self.outcome = None

        async def run_agent(self, text, state, execute):
            if text == "fill the cart":
                found = await execute("search_experiences", {"query": "hoi an"})
                await execute(
                    "add_to_cart",
                    {"experience_id": found["items"][0]["experience_id"]},
                )
                return AgentAnswer(message="added")
            self.prepared = await execute("prepare_checkout", {})
            self.outcome = await execute(
                "confirm_simulated_checkout", {"shopper_confirmed": True}
            )
            return AgentAnswer(message="done")

    agent = PrepareThenConfirm()
    service = AssistantService(data=store, ai_provider=agent)
    conversation = await service.create("one-turn-checkout", ConversationCreate())
    await service.respond(
        conversation["id"], "one-turn-checkout", MessageRequest(message="fill the cart")
    )
    await service.respond(
        conversation["id"], "one-turn-checkout", MessageRequest(message="book it")
    )

    # Without a real cart `prepare_checkout` fails and the confirmation is
    # refused for the wrong reason, which would pass this test while proving
    # nothing.
    assert "error" not in (agent.prepared or {}), agent.prepared
    assert conversation["state"]["pending_action"] == "CONFIRM_CHECKOUT"
    assert agent.outcome.get("error") == "confirmation-required", agent.outcome
    assert conversation["state"].get("booking_id") is None


async def test_a_confirmation_in_a_later_turn_is_honoured(store: DemoStore) -> None:
    """The gate must not be a wall: a shopper who is shown a total and says yes
    in their own words still books."""
    from app.assistant.provider import AgentAnswer, DemoAIProvider
    from app.assistant.service import AssistantService

    class Scripted(DemoAIProvider):
        def __init__(self):
            self.prepared = None
            self.outcome = None

        async def run_agent(self, text, state, execute):
            if text == "fill the cart":
                found = await execute("search_experiences", {"query": "hoi an"})
                await execute(
                    "add_to_cart",
                    {"experience_id": found["items"][0]["experience_id"]},
                )
                return AgentAnswer(message="added")
            if text == "checkout":
                self.prepared = await execute("prepare_checkout", {})
                return AgentAnswer(message="here is your total")
            self.outcome = await execute(
                "confirm_simulated_checkout", {"shopper_confirmed": True}
            )
            return AgentAnswer(message="booked")

    agent = Scripted()
    service = AssistantService(data=store, ai_provider=agent)
    conversation = await service.create("later-turn-checkout", ConversationCreate())
    await service.respond(
        conversation["id"], "later-turn-checkout", MessageRequest(message="fill the cart")
    )
    await service.respond(
        conversation["id"], "later-turn-checkout", MessageRequest(message="checkout")
    )
    await service.respond(
        conversation["id"], "later-turn-checkout", MessageRequest(message="đúng rồi, đặt đi")
    )

    assert "error" not in (agent.prepared or {}), agent.prepared
    assert agent.outcome is not None
    assert "error" not in agent.outcome, agent.outcome


class TestGroundedProse:
    """The message is the part the shopper reads, and it was never checked.

    Selections were filtered by id, so an offering that did not exist could not
    be rendered as a card. The prose beside the cards was passed through
    untouched, which meant the model could describe four experiences, have three
    silently removed, and leave the shopper reading about options that were not
    there - or repeat a payment link out of catalogue text it had been told was
    untrusted data.
    """

    @staticmethod
    def _answer(**arguments: object):
        from app.assistant.provider import _build_answer

        return _build_answer(dict(arguments), {"11111111-1111-1111-1111-111111111111"})

    def test_a_grounded_answer_is_kept(self) -> None:
        answer = self._answer(
            message="The sunrise cruise fits your morning.",
            selections=[
                {
                    "experience_id": "11111111-1111-1111-1111-111111111111",
                    "reason": "Departs at 06:00.",
                }
            ],
        )
        assert answer is not None
        assert len(answer.selections) == 1

    def test_an_offering_no_tool_returned_declines_the_whole_answer(self) -> None:
        """Dropping the selection and keeping the sentence about it is worse.

        The shopper would be told about an experience and shown no card for it,
        with nothing anywhere saying which of the two was wrong.
        """
        assert (
            self._answer(
                message="Here are two great options for your trip.",
                selections=[
                    {"experience_id": "11111111-1111-1111-1111-111111111111"},
                    {"experience_id": "22222222-2222-2222-2222-222222222222"},
                ],
            )
            is None
        )

    def test_a_payment_link_in_the_prose_declines_the_answer(self) -> None:
        for prose in (
            "Book directly at https://cheap-tickets.example for a discount.",
            "Email bookings@not-vietra.example to pay less.",
            "Call +84 912 345 678 to confirm your seat.",
        ):
            assert self._answer(message=prose, selections=[]) is None, prose

    def test_a_clarification_can_also_carry_an_injection(self) -> None:
        assert (
            self._answer(
                message="Which morning suits you?",
                clarification="Reply here or at www.not-vietra.example",
                selections=[],
            )
            is None
        )

    def test_ordinary_prices_and_times_are_not_mistaken_for_a_phone_number(self) -> None:
        answer = self._answer(
            message="Two options, 3 hours each, from 1,500,000 VND, departing 08:30.",
            selections=[{"experience_id": "11111111-1111-1111-1111-111111111111"}],
        )
        assert answer is not None
