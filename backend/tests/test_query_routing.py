"""Routing must be decided by the shape of a request, not by its language.

The rule these tests protect replaced a list of English function words. That
list answered "not conversational" for every language it did not contain, so
the guided path was unreachable for anyone not writing in English - and any
comparison of assistant conversion was really comparing languages.
"""

import pytest

from app.api.schemas import SearchRequest
from app.assistant.provider import deterministic_intent
from app.common.query_shape import looks_conversational
from app.search.service import should_extract_intent

KEYWORD_LOOKUPS = [
    pytest.param("hoi an lantern", id="en"),
    pytest.param("lantern boat ride", id="en-noun-phrase"),
    pytest.param("vé hội an", id="vi"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
]

STATED_NEEDS = [
    pytest.param("A relaxed family day with food and culture", id="en-prose"),
    pytest.param("something calm for my parents who tire easily", id="en-need"),
    pytest.param(
        "What can we do with a toddler and a wheelchair in Hoi An?", id="en-question"
    ),
    pytest.param(
        "Chúng tôi đi với em bé và xe lăn thì nên chơi gì ở Hội An?", id="vi-question"
    ),
    pytest.param("子供と一緒に何ができますか？", id="ja-fullwidth-question"),
    pytest.param("带着婴儿车可以去哪里玩？", id="zh-fullwidth-question"),
]


@pytest.mark.parametrize("query", KEYWORD_LOOKUPS)
def test_keyword_lookups_stay_in_the_grid(query: str) -> None:
    assert looks_conversational(query) is False
    assert deterministic_intent(query).interaction_mode == "grid"


@pytest.mark.parametrize("query", STATED_NEEDS)
def test_stated_needs_reach_the_assistant(query: str) -> None:
    assert looks_conversational(query) is True
    assert deterministic_intent(query).interaction_mode == "assistant"


@pytest.mark.parametrize("query", STATED_NEEDS)
def test_stated_needs_are_worth_a_model_call(query: str) -> None:
    assert should_extract_intent(SearchRequest(query=query, locale="en")) is True


def test_a_stated_need_without_a_question_mark_still_reaches_the_assistant() -> None:
    """The fallback governs demo mode, model outages and budget exhaustion.

    When its routing rule was "ends with a question mark", losing the model
    quietly sent every shopper who described what they wanted - rather than
    asking a question - to the keyword grid, which is the population least able
    to search for themselves.
    """
    intent = deterministic_intent("something calm for my parents who tire easily")
    assert intent.interaction_mode == "assistant"


def test_the_gate_and_the_fallback_cannot_disagree() -> None:
    """A query worth interpreting must not be rendered as a keyword result."""
    for query in [q.values[0] for q in KEYWORD_LOOKUPS + STATED_NEEDS]:
        assert isinstance(query, str)
        worth_interpreting = should_extract_intent(
            SearchRequest(query=query, locale="en")
        )
        routed_to_assistant = deterministic_intent(query).interaction_mode == "assistant"
        assert worth_interpreting == routed_to_assistant, query
