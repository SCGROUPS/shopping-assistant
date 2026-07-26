"""Fingerprints: what a translation was made from, and what it should be made from.

Two values, never one. A single hash of the source text misses three ways a
translation goes stale without the source changing — a corrected prompt, an
updated glossary, a different model — but folding all of those into one value
creates the opposite bug: a model upgrade would mark a *human's* Korean title
stale, and a human's Korean title has nothing to do with which model we use.

So `source` covers the text, and `recipe` covers how a machine would render it.
Manual provenance ignores the recipe entirely.
"""

from __future__ import annotations

import hashlib

# Bumped when this module changes how a fingerprint is computed. Without it, a
# change to the hashing itself would leave every field describing itself as
# current against a value the new code would never produce.
FINGERPRINT_VERSION = "1"


def _digest(*parts: str) -> str:
    # NUL-joined rather than concatenated: "ab" + "c" and "a" + "bc" must not
    # collide, and NUL cannot occur in a PostgreSQL text column.
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def source_fingerprint(*, text: str, source_language: str, locale: str) -> str:
    """What the translation is *of*.

    The target locale participates because the same source text translated into
    Korean and into German are different answers; without it, a field's
    fingerprint would be identical in every locale and a per-locale correction
    could not be told apart from a source change.
    """
    return _digest(FINGERPRINT_VERSION, "source", source_language, locale, text)


def recipe_fingerprint(
    *, prompt_version: str, glossary_revision: int, model: str
) -> str:
    """How a machine would render it.

    Hashes the glossary *revision number*, not its contents, so editing a term
    is an explicit decision to invalidate translations rather than an accidental
    consequence of fixing a typo in a replacement.
    """
    return _digest(FINGERPRINT_VERSION, "recipe", prompt_version, str(glossary_revision), model)


def desired_fingerprint(*, source: str, recipe: str, provenance: str) -> str:
    """What this field should be produced from, right now.

    A human translation answers only to the source. A machine translation
    answers to the source and to the recipe that produced it, so a glossary fix
    invalidates every machine translation it could have affected and leaves the
    human ones alone — while still letting a source edit invalidate both,
    because both now describe text that no longer exists.
    """
    if provenance == "manual":
        return source
    return _digest(FINGERPRINT_VERSION, "desired", source, recipe)
