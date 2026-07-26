"""Routing is the model's judgement, and nothing else may impersonate it.

Two rules have been tried in this position and both failed the same way. The
first matched English function words, so a Vietnamese request scored zero and
could never reach the assistant. The second counted words and characters, which
looked language-neutral and was not: Vietnamese writes syllables as separate
tokens, so `vé cáp treo Bà Nà Hills` - a pure keyword lookup - counted six
words and was sent to the assistant, while `cần chỗ cho xe lăn`, a stated
accessibility need, counted five and was sent to the grid. The heuristic was
close to exactly inverted for the language it most needed to serve.

So there is no heuristic. The model decides, and when the model cannot be
reached the answer is `undetermined` rather than a guess.
"""

import pytest

from app.api.schemas import SearchIntent, SearchRequest
from app.assistant.provider import deterministic_intent
from app.search.service import sanitize_intent, should_extract_intent

# Real phrasings in the languages this catalogue serves. None of them may be
# routed by anything in this process, so every one of them must come back
# undetermined from the fallback.
REQUESTS = [
    pytest.param("hoi an lantern", id="en-keyword"),
    pytest.param("A relaxed family day with food and culture", id="en-prose"),
    pytest.param("something calm for my parents who tire easily", id="en-need"),
    pytest.param("vé cáp treo Bà Nà Hills", id="vi-keyword-6-words"),
    pytest.param("vé tham quan phố cổ Hội An", id="vi-keyword-7-words"),
    pytest.param("cần chỗ cho xe lăn", id="vi-need-5-words"),
    pytest.param("có gì cho trẻ em", id="vi-need-short"),
    pytest.param("带婴儿去哪玩", id="zh-need-no-spaces"),
    pytest.param("子供向けの体験", id="ja-keyword-no-spaces"),
]


@pytest.mark.parametrize("query", REQUESTS)
def test_the_fallback_never_guesses_where_an_answer_belongs(query: str) -> None:
    assert deterministic_intent(query).interaction_mode == "undetermined"


@pytest.mark.parametrize("query", REQUESTS)
def test_every_real_request_is_worth_interpreting(query: str) -> None:
    """No request is filtered out before the model sees it.

    Both previous gates decided this from shape, and both therefore decided it
    from language: a request that failed the gate was answered without ever
    being read.
    """
    assert should_extract_intent(SearchRequest(query=query, locale="en")) is True


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_an_empty_request_is_not_sent_to_the_model(query: str) -> None:
    assert should_extract_intent(SearchRequest(query=query, locale="en")) is False


def test_routing_is_not_decided_by_length() -> None:
    """The specific inversion that the counting rule produced.

    A long Vietnamese keyword lookup and a short Vietnamese stated need must
    not be separated by their size, in either direction.
    """
    long_lookup = deterministic_intent("vé tham quan phố cổ Hội An")
    short_need = deterministic_intent("cần chỗ cho xe lăn")
    assert long_lookup.interaction_mode == short_need.interaction_mode


class TestDatesSurviveTheLanguageTheyWereWrittenIn:
    """A stated date must not be deleted for being stated in Vietnamese.

    The guard was a regex of English month names, weekdays and words like
    `tomorrow`, run against the shopper's raw text. A date the model had read
    correctly out of a Vietnamese request matched nothing and was dropped, so
    the shopper's date was silently ignored.
    """

    def _intent_with_date(self, phrase: str | None) -> SearchIntent:
        return SearchIntent(
            search_text="tour",
            hard_constraints=[
                {"field": "visit_start", "operator": "gte", "value": "2026-01-02"}
            ],
            date_phrase=phrase,
        )

    def test_a_vietnamese_date_phrase_is_kept(self) -> None:
        kept = sanitize_intent("tour Hội An ngày mai", self._intent_with_date("ngày mai"))
        assert kept.hard_constraints, "a stated Vietnamese date was dropped"

    def test_a_japanese_date_phrase_is_kept(self) -> None:
        kept = sanitize_intent("明日のホイアンツアー", self._intent_with_date("明日"))
        assert kept.hard_constraints, "a stated Japanese date was dropped"

    def test_an_english_date_phrase_is_kept(self) -> None:
        kept = sanitize_intent("hoi an tour tomorrow", self._intent_with_date("tomorrow"))
        assert kept.hard_constraints

    def test_an_invented_date_is_dropped(self) -> None:
        """The quote must actually occur in the request."""
        dropped = sanitize_intent(
            "hoi an lantern tour", self._intent_with_date("next weekend")
        )
        assert not dropped.hard_constraints

    def test_a_date_with_no_quote_is_dropped(self) -> None:
        dropped = sanitize_intent("hoi an lantern tour", self._intent_with_date(None))
        assert not dropped.hard_constraints
