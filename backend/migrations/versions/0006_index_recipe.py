"""Record how a search document was built, separately from what it says.

`index_fingerprint` mixes the document text into its hash, which makes it
unusable as a release barrier: it changes every time a translation lands, so a
deploy waiting on "is every document current?" is waiting on a value that moves
for reasons that have nothing to do with the release, and never settles.
`index_recipe` is the release-relevant half alone - document version, embedding
model, embedding version and the configured dimension count - so the question
has a stable answer that ordinary content work cannot disturb.

Expand-only. The default is the empty string, so every row written by the
previous release reads as "recipe unknown" rather than as current, and a gate
asking for a specific recipe correctly refuses to count them.

This migration exists because `0001_initial` builds the schema with
`Base.metadata.create_all`. A database created after the column was added to
the ORM gets it for free, and a database already at 0005 does not - which means
the column would have been missing in exactly the one place that matters.
"""

from alembic import op

revision = "0006_index_recipe"
down_revision = "0005_translation_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE experience_search_documents
          ADD COLUMN IF NOT EXISTS index_recipe varchar(64) NOT NULL DEFAULT ''
        """
    )
    # Indexed because the release gate counts documents that do *not* match the
    # running image's recipe, over the whole catalogue in every locale.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_experience_search_documents_index_recipe
          ON experience_search_documents (index_recipe)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_experience_search_documents_index_recipe")
    op.execute("ALTER TABLE experience_search_documents DROP COLUMN IF EXISTS index_recipe")
