"""One registry for what a field *is*, keyed by `(entity_type, field)`.

Two policies about translated fields already existed in two modules, decided by
two frozensets that happened to hold the same member: `meeting_point` must be
reviewed before publication, and `meeting_point` must never be served stale.
They are different rules, but they answer the same underlying question — *what
happens if this string is wrong?* — and a third such rule would have created a
third set in a third module.

That matters because the sets are not independent in practice. A field held for
review is by construction more often un-published, which is exactly when
serving stale text becomes tempting; deciding those two things in modules that
cannot see each other is how a field ends up reviewed but stale-served, which is
the combination nobody would choose deliberately.

The registry is code, not operator data. An operator choosing which fields may
be served stale is choosing whether travellers are sent to the wrong address.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTITY_EXPERIENCE = "experience"


@dataclass(frozen=True)
class FieldPolicy:
    """What must be true of a field before a shopper reads it.

    `requires_review` is about *publication*: may a machine translation of this
    field reach the storefront without a human seeing it?

    `serve_stale` is about *display*: once published, may it still be shown
    after the source text it answers has changed?
    """

    requires_review: bool = False
    serve_stale: bool = True


# Prose. A stale adjective costs a little accuracy; withholding it costs the
# shopper the whole description, because falling back on every source edit
# blanks a locale for every product touched until the worker catches up.
PROSE = FieldPolicy(requires_review=False, serve_stale=True)

# Operational data: text a traveller acts on. A stale meeting point sends
# someone to a place the tour no longer departs from, and "it was at least in
# your language" is not the complaint that generates. Held for review for the
# same reason - there is no Korean reviewer for adjectives, but there is
# someone who can check an address.
DIRECTIONS = FieldPolicy(requires_review=True, serve_stale=False)

_REGISTRY: dict[tuple[str, str], FieldPolicy] = {
    (ENTITY_EXPERIENCE, "title"): PROSE,
    (ENTITY_EXPERIENCE, "short_description"): PROSE,
    (ENTITY_EXPERIENCE, "description"): PROSE,
    (ENTITY_EXPERIENCE, "meeting_point"): DIRECTIONS,
}


def policy_for(field: str, entity_type: str = ENTITY_EXPERIENCE) -> FieldPolicy:
    """The policy for a field, defaulting to prose.

    An unregistered field is treated as prose rather than rejected: adding a
    translated column should not be able to take the storefront down. It is the
    *safe* default in the availability sense, and the registry is small enough
    that a field needing stricter handling is a deliberate one-line entry.
    """
    return _REGISTRY.get((entity_type, field), PROSE)


def fields_requiring_review(entity_type: str = ENTITY_EXPERIENCE) -> frozenset[str]:
    return frozenset(
        field
        for (owner, field), policy in _REGISTRY.items()
        if owner == entity_type and policy.requires_review
    )


def fields_never_served_stale(entity_type: str = ENTITY_EXPERIENCE) -> frozenset[str]:
    return frozenset(
        field
        for (owner, field), policy in _REGISTRY.items()
        if owner == entity_type and not policy.serve_stale
    )
