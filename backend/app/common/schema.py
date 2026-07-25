"""PostgreSQL DDL that SQLAlchemy metadata cannot express.

Extensions, the full-text trigger and the ANN/trigram indexes are raw SQL, so
`Base.metadata.create_all` produces a schema that looks complete and then
rejects the first search document because `search_vector` is never populated.
Keeping the statements here lets the migration and the test fixtures build the
same schema instead of drifting apart.
"""

EXTENSIONS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
)

POST_CREATE: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_experience_search_fts "
    "ON experience_search_documents USING GIN (search_vector)",
    "CREATE INDEX IF NOT EXISTS ix_experience_search_embedding_hnsw "
    "ON experience_search_documents USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS ix_experience_title_trgm "
    "ON experiences USING GIN (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_events_session_time_desc "
    "ON behavior_events (session_id, occurred_at DESC)",
    """
    CREATE OR REPLACE FUNCTION tourism_search_vector_update() RETURNS trigger AS $$
    BEGIN
      NEW.search_vector := setweight(to_tsvector('english', unaccent(NEW.document_text)), 'A');
      RETURN NEW;
    END
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents",
    """
    CREATE TRIGGER trg_search_vector_update
    BEFORE INSERT OR UPDATE OF document_text ON experience_search_documents
    FOR EACH ROW EXECUTE FUNCTION tourism_search_vector_update()
    """,
)

PRE_DROP: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents",
    "DROP FUNCTION IF EXISTS tourism_search_vector_update",
)
