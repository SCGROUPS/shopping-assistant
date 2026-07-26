"""Structural, language-neutral judgements about the shape of a search query.

This lives outside both the search service and the assistant provider because
both need the same judgement and must not disagree about it. When they were
separate, the gate that decided a query was worth a model call and the fallback
that decided where the answer should be shown used different rules, so a query
could be treated as prose on the way in and as a keyword lookup on the way out.
"""

# Both the ASCII and the full-width form: CJK input methods produce the latter,
# and a check that knows only "?" reads a Japanese question as a keyword lookup.
QUESTION_MARKS = ("?", "？")

# Chosen to separate a keyword lookup ("hoi an lantern") from a stated need
# ("something calm for my parents who tire easily"). Length in characters sits
# alongside length in words because Chinese and Japanese do not put spaces
# between words: a whole sentence has a word count of one, so a threshold
# expressed only in words is not a measurement that survives translation.
_PROSE_WORDS = 5
_PROSE_CHARACTERS = 24


def looks_conversational(text: str) -> bool:
    """Whether a query reads as a request to be interpreted, not a lookup.

    This used to be expressed as English function words - `for`, `with`,
    `prefer`, `wheelchair`. A Vietnamese or Japanese shopper could not match any
    of them, so their requests were never understood, never routed to the
    assistant, and fell to keyword search no matter how carefully they were
    phrased. The guided path was English-only by construction, which also makes
    language a hidden variable in every assistant conversion comparison.
    """
    query = text.strip()
    if not query:
        return False
    if query.endswith(QUESTION_MARKS):
        return True
    return len(query.split()) > _PROSE_WORDS or len(query) > _PROSE_CHARACTERS
