"""Locale facts shared by Python and PostgreSQL.

The text-search configuration has to be chosen identically when a document is
indexed and when a query is parsed. Indexing happens in a PostgreSQL trigger
(`common/schema.py`) and querying happens in Python (`search/postgres.py`), so
the mapping lives here and both sides are tested against each other - a
`german` document searched with an `english` query stems both sides differently
and simply stops matching, with no error to notice.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_LOCALE = "en"

# PostgreSQL ships stemmers for these and not for vi/zh/ja/ko, where `simple`
# is correct: it tokenises without stemming rather than applying English rules
# to text that has none.
TEXT_SEARCH_CONFIGS: dict[str, str] = {
    "en": "english",
    "fr": "french",
    "de": "german",
    "es": "spanish",
}

# Every chain terminates at English. "First non-empty" would be
# nondeterministic and could serve Japanese prose to a German shopper, which
# reads as data corruption rather than as a missing translation.
FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "en": ("en",),
    "vi": ("vi", "en"),
    "zh": ("zh", "en"),
    "ja": ("ja", "en"),
    "ko": ("ko", "en"),
    "fr": ("fr", "en"),
    "de": ("de", "en"),
    "es": ("es", "en"),
}

SUPPORTED_LOCALES: tuple[str, ...] = tuple(FALLBACK_CHAINS)

# What a translation model should be told to produce. "zh" alone is ambiguous
# between scripts, and a tourism listing rendered in the wrong one is unreadable
# to the reader it was meant for, so the target names the script explicitly.
LOCALE_NAMES: dict[str, str] = {
    "en": "English",
    "vi": "Vietnamese",
    "zh": "Simplified Chinese (zh-Hans)",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish (European)",
}


def text_search_config(locale: str) -> str:
    """The regconfig to use for `locale`, matching the indexing trigger."""
    return TEXT_SEARCH_CONFIGS.get(locale, "simple")


def fallback_chain(locale: str) -> tuple[str, ...]:
    """Ordered locales to try for `locale`, always ending at English."""
    return FALLBACK_CHAINS.get(locale, (DEFAULT_LOCALE,))


def normalize_locale(value: str | None) -> str:
    """Reduce a tag such as `zh-Hant-TW` or `en_GB` to a locale we serve.

    Accept-Language sends region and script subtags we have no content for, and
    rejecting them would drop a Chinese speaker to English for the sake of a
    subtag we never asked about.
    """
    if not value:
        return DEFAULT_LOCALE
    primary = value.strip().replace("_", "-").split("-")[0].lower()
    return primary if primary in FALLBACK_CHAINS else DEFAULT_LOCALE


def parse_accept_language(header: str | None) -> tuple[tuple[str, float], ...]:
    """Parse `Accept-Language` into (locale, quality) pairs, best first.

    Quality is what makes this worth parsing rather than splitting on commas.
    A browser configured for Vietnamese with English as a fallback sends
    `vi,en;q=0.9` - taking the first entry happens to be right there and is
    wrong for `en;q=0.5,vi;q=0.9`, which is what a shopper who *prefers*
    Vietnamese but tolerates English sends. Ties keep header order, because
    that is the order the client asked for.
    """
    if not header:
        return ()
    parsed: list[tuple[int, str, float]] = []
    for position, part in enumerate(header.split(",")):
        token, _, params = part.strip().partition(";")
        token = token.strip()
        if not token or token == "*":
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, raw = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            # `q=0` means "explicitly not this one", not "least preferred".
            continue
        parsed.append((position, token, quality))
    parsed.sort(key=lambda item: (-item[2], item[0]))
    return tuple((token, quality) for _, token, quality in parsed)


def negotiate_locale(
    *,
    explicit: str | None = None,
    session_preference: str | None = None,
    accept_language: str | None = None,
    enabled: Sequence[str] = (DEFAULT_LOCALE,),
) -> str:
    """Resolve the locale for one request, first match winning (spec 4.3).

    Order: explicit parameter, session preference, `Accept-Language`, English.

    `enabled` is the gate, and it applies to all three inputs rather than only
    to header negotiation. A locale is enabled when its content is ready; a
    client that passes `locale=ja` before Japanese is ready would otherwise
    search a corpus that has no documents in it and receive an empty result
    set that looks like a product problem. Refusing the request would be
    worse - the shopper asked for something reasonable - so it degrades to a
    locale we can actually serve.
    """
    allowed = [item for item in enabled if item in FALLBACK_CHAINS] or [DEFAULT_LOCALE]
    for candidate in (explicit, session_preference):
        if not candidate:
            continue
        resolved = normalize_locale(candidate)
        if resolved in allowed:
            return resolved
    for token, _ in parse_accept_language(accept_language):
        # Matched strictly, not through `normalize_locale`, which answers `en`
        # for anything it does not recognise. A header of `sv,vi;q=0.9` would
        # otherwise resolve Swedish to English and return before reaching the
        # Vietnamese the shopper also asked for.
        primary = token.strip().replace("_", "-").split("-")[0].lower()
        if primary in allowed:
            return primary
    return DEFAULT_LOCALE if DEFAULT_LOCALE in allowed else allowed[0]
