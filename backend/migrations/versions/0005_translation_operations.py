"""Tell a provider outage apart from a translation that will never succeed.

Two columns' worth of consequence, both discovered by review of the worker that
shipped without them.

`failure_kind` exists because the scheduled job runs `translate --revive` every
two hours. Reviving indiscriminately means a job that fails deterministically -
a glossary term the model will never emit for this source, a field the schema
rejects - is retried forever, spending money on every cycle and never asking
anybody for help. Retrying a provider outage is correct; retrying a wrong answer
is a standing order to keep being wrong.

`translation_spend` exists because the budget it replaces was per *process*. The
ledger is in-memory, and a scheduled container starts a new process on every
run, so a "$25 daily" ceiling was $25 every two hours - three hundred a day, and
more with overlap. A ceiling that resets whenever the thing it constrains
restarts is not a ceiling.

Expand-only: both are additive, and code that does not know about them behaves
exactly as it did.
"""

from alembic import op

revision = "0005_translation_operations"
down_revision = "0004_content_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE translation_jobs
          ADD COLUMN IF NOT EXISTS failure_kind varchar(20)
        """
    )
    # Only 'transient' is revived. NULL means "not yet classified", which is
    # what every row written by the previous release says, and those are left
    # alone rather than assumed safe to retry.
    op.execute(
        """
        ALTER TABLE translation_jobs
          DROP CONSTRAINT IF EXISTS ck_translation_job_failure_kind
        """
    )
    op.execute(
        """
        ALTER TABLE translation_jobs
          ADD CONSTRAINT ck_translation_job_failure_kind
          CHECK (failure_kind IS NULL OR failure_kind IN ('transient', 'permanent'))
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_spend (
          day date PRIMARY KEY,
          amount numeric(12, 4) NOT NULL DEFAULT 0,
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS translation_spend")
    op.execute(
        "ALTER TABLE translation_jobs DROP CONSTRAINT IF EXISTS ck_translation_job_failure_kind"
    )
    op.execute("ALTER TABLE translation_jobs DROP COLUMN IF EXISTS failure_kind")
