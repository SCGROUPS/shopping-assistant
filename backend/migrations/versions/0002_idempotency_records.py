"""Persist idempotent commerce responses."""

from alembic import op

revision = "0002_idempotency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_records (
          id uuid PRIMARY KEY,
          session_id uuid NOT NULL REFERENCES shopping_sessions(id),
          operation varchar(50) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          response jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_idempotency_records_session_operation_key
            UNIQUE (session_id, operation, idempotency_key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS idempotency_records")
