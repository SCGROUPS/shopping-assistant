"""Collapse the two category taxonomies into the one the catalogue declares.

Seeded supply was written with "Food experience" and "Transport ticket"; the
importer wrote "Food" and "Transport" while carrying a comment promising it used
"the same language as seeded supply". Nothing compared the two, so both reached
production and stayed there.

The cost fell on shoppers. Category is offered to the intent model as an enum
built from whatever the catalogue actually contains, so the model saw both
spellings and reasonably preferred the descriptive one - and "Food experience"
had a single listing in the country and none at all in Ho Chi Minh City, so
`food tour in ho chi minh city` returned an empty page while seventy `Food`
listings sat unqueried, four of them in that city.

The repair was first applied to production by hand, as a direct UPDATE. That
fixed the rows and nothing else: no migration meant no other environment was
repaired and a rebuild would reintroduce it, and no `content_version` bump meant
a partner diffing its inventory could not see that its own listing had been
recategorised. This migration is that repair done properly and reproducibly.

Search documents and embeddings are *not* rebuilt here. They cannot be - the
document text is assembled in application code and the vectors need an
embedding call - and they do not need to be: the deploy runs the catalogue job
after the migration, whose reconciliation pass recomputes each document's
fingerprint, finds the ones whose category no longer matches what was indexed,
and enqueues every locale. Doing it there rather than here also means the work
is leased, retried and reported instead of happening inside a schema migration.
"""

from alembic import op

revision = "0007_canonical_categories"
down_revision = "0006_index_recipe"
branch_labels = None
depends_on = None

# Retired spelling -> the name in `app/catalog/vocabulary.py`.
RETIRED = {
    "Food experience": "Food",
    "Transport ticket": "Transport",
}

# The rows the hand-run UPDATE already changed in production. Identified by slug
# because that is stable across environments and rebuilds, unlike the ids.
REPAIRED_BY_HAND = (
    "hoi-an-evening-food-tour",
    "da-nang-airport-private-transfer",
    "hoi-an-da-nang-shuttle-pass",
)


def upgrade() -> None:
    for retired, canonical in RETIRED.items():
        # Bumped in the same statement as the rename, so a row cannot be
        # recategorised without the version that advertises it having moved.
        op.execute(
            f"""
            UPDATE experiences
               SET category = '{canonical}',
                   content_version = content_version + 1
             WHERE category = '{retired}'
            """
        )

    # Production's three rows were renamed outside a migration and so never got
    # this. On a database built after the vocabulary was unified they are
    # already canonical and this is a bump with nothing behind it - harmless,
    # since a version only ever means "something a partner can see changed", and
    # no approval predates a fresh build.
    slugs = ", ".join(f"'{slug}'" for slug in REPAIRED_BY_HAND)
    op.execute(
        f"""
        UPDATE experiences
           SET content_version = content_version + 1
         WHERE slug IN ({slugs})
        """
    )


def downgrade() -> None:
    # Deliberately not reversed. Restoring the retired spellings would
    # reintroduce the split vocabulary and the empty result pages that came with
    # it, and the publish gate now refuses those values outright, so a
    # downgraded database could not republish the rows it had just rewritten.
    pass
