"""Locale facts shared by Python and PostgreSQL.

The text-search configuration has to be chosen identically when a document is
indexed and when a query is parsed. Indexing happens in a PostgreSQL trigger
(`common/schema.py`) and querying happens in Python (`search/postgres.py`), so
the mapping lives here and both sides are tested against each other - a
`german` document searched with an `english` query stems both sides differently
and simply stops matching, with no error to notice.
"""

from __future__ import annotations

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
