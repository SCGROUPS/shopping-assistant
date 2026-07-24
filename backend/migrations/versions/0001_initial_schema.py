"""Initial catalog, search, session, assistant and commerce schema."""

from alembic import op

from app.common.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    Base.metadata.create_all(bind=bind)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_experience_search_fts "
        "ON experience_search_documents USING GIN (search_vector)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_experience_search_embedding_hnsw "
        "ON experience_search_documents USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_experience_title_trgm "
        "ON experiences USING GIN (title gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_session_time_desc "
        "ON behavior_events (session_id, occurred_at DESC)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tourism_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector := setweight(to_tsvector('english', unaccent(NEW.document_text)), 'A');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_search_vector_update
        BEFORE INSERT OR UPDATE OF document_text ON experience_search_documents
        FOR EACH ROW EXECUTE FUNCTION tourism_search_vector_update()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents")
    op.execute("DROP FUNCTION IF EXISTS tourism_search_vector_update")
    Base.metadata.drop_all(bind=bind)
