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

import unicodedata
from datetime import UTC, datetime, timedelta

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


@pytest.mark.parametrize(
    "query",
    [
        "wheelchair access in Hoi An under 500000 with free cancellation",
        "family indoor tour, no nightlife, in English",
        "cần chỗ cho xe lăn ở Hội An",
    ],
)
def test_the_fallback_invents_no_constraints(query: str) -> None:
    """Routing was only half of it.

    An earlier version of this test asserted `interaction_mode` alone, and
    passed while the same function was still reading destinations, budgets,
    accessibility and exclusions out of English keywords - so the check
    reported that language had been removed from the decision while language
    was still deciding what the shopper had asked for.
    """
    intent = deterministic_intent(query)
    assert intent.hard_constraints == []
    assert intent.soft_preferences == []
    assert intent.exclusions == []
    assert intent.destination.name is None


@pytest.mark.parametrize("query", REQUESTS)
def test_the_fallback_keeps_the_shoppers_own_words(query: str) -> None:
    """The English branch used to delete words from the search text.

    `hoi an`, `family`, `indoor` and others were stripped before searching, so
    an English request was searched with a mutilated query and a Vietnamese one
    was not. Whatever else the fallback cannot do, it must not edit the
    request.
    """
    assert deterministic_intent(query).search_text == query.strip()


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
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        return SearchIntent(
            search_text="tour",
            hard_constraints=[{"field": "visit_start", "operator": "gte", "value": tomorrow}],
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
        dropped = sanitize_intent("hoi an lantern tour", self._intent_with_date("next weekend"))
        assert not dropped.hard_constraints

    def test_a_dropped_date_is_reported_rather_than_discarded(self) -> None:
        """Refusing a constraint is allowed. Refusing it in silence is not.

        The shopper was specific about a date. If we will not honour it they see
        results for other days with nothing to distinguish that from an answer,
        and no way to discover which of their words we ignored. So every refusal
        leaves a code behind, and the storefront says it in their language.
        """
        unverified = sanitize_intent("hoi an lantern tour", self._intent_with_date("next weekend"))
        assert unverified.dropped_constraints == ["date_unverified"]

        implausible = SearchIntent(
            search_text="tour",
            hard_constraints=[{"field": "visit_start", "operator": "gte", "value": "1999-01-01"}],
            date_phrase="tomorrow",
        )
        result = sanitize_intent("hoi an tour tomorrow", implausible)
        assert not result.hard_constraints
        assert result.dropped_constraints == ["date_implausible"]

    def test_a_kept_date_reports_nothing(self) -> None:
        """The signal has to be absent when nothing was dropped, or it is noise."""
        kept = sanitize_intent("tour Hội An ngày mai", self._intent_with_date("ngày mai"))
        assert kept.hard_constraints
        assert kept.dropped_constraints == []

    def test_a_date_with_no_quote_is_dropped(self) -> None:
        dropped = sanitize_intent("hoi an lantern tour", self._intent_with_date(None))
        assert not dropped.hard_constraints

    def test_a_date_in_the_past_is_dropped(self) -> None:
        """A quote proves the shopper mentioned a date, not that we read it right."""
        intent = SearchIntent(
            search_text="tour",
            hard_constraints=[{"field": "visit_start", "operator": "gte", "value": "2019-04-01"}],
            date_phrase="tomorrow",
        )
        assert not sanitize_intent("hoi an tour tomorrow", intent).hard_constraints

    def test_a_date_years_away_is_dropped(self) -> None:
        intent = SearchIntent(
            search_text="tour",
            hard_constraints=[{"field": "visit_start", "operator": "gte", "value": "2099-04-01"}],
            date_phrase="ngày mai",
        )
        assert not sanitize_intent("tour ngày mai", intent).hard_constraints

    def test_a_date_next_week_is_kept(self) -> None:
        soon = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        intent = SearchIntent(
            search_text="tour",
            hard_constraints=[{"field": "visit_start", "operator": "gte", "value": soon}],
            date_phrase="tuần sau",
        )
        assert sanitize_intent("tour tuần sau", intent).hard_constraints

    def test_a_decomposed_vietnamese_quote_still_matches(self) -> None:
        """The same word typed two ways is the same word.

        Vietnamese reaches us both precomposed and decomposed, depending on the
        keyboard and the client. Comparing the raw strings dropped a date the
        shopper had plainly typed.
        """
        query = unicodedata.normalize("NFD", "tour Hội An ngày mai")
        kept = sanitize_intent(query, self._intent_with_date("ngày mai"))
        assert kept.hard_constraints, "a decomposed Vietnamese date was dropped"

    def test_a_composed_query_matches_a_decomposed_quote(self) -> None:
        kept = sanitize_intent(
            "tour Hội An ngày mai",
            self._intent_with_date(unicodedata.normalize("NFD", "ngày mai")),
        )
        assert kept.hard_constraints
