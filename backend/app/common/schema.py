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
    "CREATE INDEX IF NOT EXISTS ix_experience_search_locale "
    "ON experience_search_documents (locale, experience_id)",
    "CREATE INDEX IF NOT EXISTS ix_experience_title_trgm "
    "ON experiences USING GIN (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_events_session_time_desc "
    "ON behavior_events (session_id, occurred_at DESC)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_experience_reference_external
      ON experiences (external_id)
      WHERE source_type = 'reference' AND external_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_experience_partner_external
      ON experiences (partner_id, external_id)
      WHERE source_type = 'partner' AND external_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_one_open_submission
      ON partner_submissions (partner_id, external_id)
      WHERE status = 'submitted'
    """,
    # PostgreSQL ships stemmers for en/fr/de/es and none for vi/zh/ja/ko, so the
    # trigger picks a configuration per locale rather than assuming English.
    # `unaccent` is applied to document and query alike, so folding diacritics
    # costs a little Vietnamese precision and buys the larger recall win of
    # matching shoppers who type without them.
    """
    CREATE OR REPLACE FUNCTION tourism_search_vector_update() RETURNS trigger AS $$
    DECLARE config regconfig;
    BEGIN
      config := CASE NEW.locale
                  WHEN 'fr' THEN 'french'::regconfig
                  WHEN 'de' THEN 'german'::regconfig
                  WHEN 'es' THEN 'spanish'::regconfig
                  WHEN 'en' THEN 'english'::regconfig
                  ELSE 'simple'::regconfig
                END;
      NEW.search_vector := setweight(to_tsvector(config, unaccent(NEW.document_text)), 'A');
      RETURN NEW;
    END
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents",
    """
    CREATE TRIGGER trg_search_vector_update
    BEFORE INSERT OR UPDATE OF document_text, locale ON experience_search_documents
    FOR EACH ROW EXECUTE FUNCTION tourism_search_vector_update()
    """,
)

PRE_DROP: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents",
    "DROP FUNCTION IF EXISTS tourism_search_vector_update",
)
