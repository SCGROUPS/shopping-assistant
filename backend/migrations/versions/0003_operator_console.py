"""Operator console: identity, audit, configuration, overrides, merchandising.

Everything a human needs in order to run the catalogue without a deploy. The
new columns on `experiences` all carry defaults, so existing rows keep their
current behaviour: boost 1, nothing pinned, nothing suppressed, nothing flagged
for review.
"""

from alembic import op

revision = "0003_operator_console"
down_revision = "0002_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operators (
          id uuid PRIMARY KEY,
          email varchar(200) NOT NULL UNIQUE,
          name varchar(120) NOT NULL,
          role varchar(30) NOT NULL,
          key_prefix varchar(12) NOT NULL UNIQUE,
          key_hash varchar(200) NOT NULL,
          key_salt varchar(64) NOT NULL,
          active boolean NOT NULL DEFAULT true,
          last_seen_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_operators_key_prefix ON operators (key_prefix)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
          id uuid PRIMARY KEY,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          operator_id uuid REFERENCES operators(id),
          operator_email varchar(200) NOT NULL,
          action varchar(60) NOT NULL,
          entity_type varchar(40) NOT NULL,
          entity_id varchar(120) NOT NULL,
          summary text NOT NULL DEFAULT '',
          changes jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_occurred_at ON audit_log (occurred_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_entity_time "
        "ON audit_log (entity_type, entity_id, occurred_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS business_settings (
          key varchar(80) PRIMARY KEY,
          value jsonb NOT NULL,
          version integer NOT NULL DEFAULT 1,
          updated_by varchar(200) NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience_overrides (
          experience_id uuid PRIMARY KEY REFERENCES experiences(id) ON DELETE CASCADE,
          fields jsonb NOT NULL DEFAULT '{}'::jsonb,
          updated_by varchar(200) NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    for statement in (
        "ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false",
        "ADD COLUMN IF NOT EXISTS review_note text NOT NULL DEFAULT ''",
        "ADD COLUMN IF NOT EXISTS boost numeric(4,2) NOT NULL DEFAULT 1",
        "ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT false",
        "ADD COLUMN IF NOT EXISTS suppressed boolean NOT NULL DEFAULT false",
        "ADD COLUMN IF NOT EXISTS promotion_label varchar(60) NOT NULL DEFAULT ''",
        "ADD COLUMN IF NOT EXISTS promotion_starts_at timestamptz",
        "ADD COLUMN IF NOT EXISTS promotion_ends_at timestamptz",
    ):
        op.execute(f"ALTER TABLE experiences {statement}")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_experiences_needs_review "
        "ON experiences (needs_review) WHERE needs_review"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_experiences_needs_review")
    for column in (
        "needs_review",
        "review_note",
        "boost",
        "pinned",
        "suppressed",
        "promotion_label",
        "promotion_starts_at",
        "promotion_ends_at",
    ):
        op.execute(f"ALTER TABLE experiences DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS experience_overrides")
    op.execute("DROP TABLE IF EXISTS business_settings")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS operators")
