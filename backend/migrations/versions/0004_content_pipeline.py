"""Content pipeline: ownership, locales, translation state, staged partner revisions.

Three things happen here, and they are separable only in principle.

The search document gains a locale and a composite primary key, because a
Vietnamese query has nothing to match against English text. `experiences` gains
the columns that say where content came from, in what language, and at which
version - the vocabulary the old schema lacked when it assumed every product
arrived from a supplier feed. And the tables that make translation, override and
partner review concurrency-safe are created empty.

The primary key is replaced in the same migration as the expand, which is only
safe because no multilingual row exists yet: until the backfill runs, old code
inserting without a locale still lands on the 'en' default, and old code
deleting "all documents for this experience" still deletes exactly one row.
Once translations exist that stops being true, which is why `IndexWorkItem` and
the non-destructive writers ship before any backfill.
"""

from alembic import op

revision = "0004_content_pipeline"
down_revision = "0003_operator_console"
branch_labels = None
depends_on = None

HOUSE_SUPPLIER_ID = "00000000-0000-4000-8000-00000000f005"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS partners (
          id uuid PRIMARY KEY,
          slug varchar(80) NOT NULL UNIQUE,
          display_name varchar(160) NOT NULL,
          status varchar(20) NOT NULL DEFAULT 'active',
          locales varchar[] NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_keys (
          id uuid PRIMARY KEY,
          partner_id uuid NOT NULL REFERENCES partners(id),
          prefix varchar(12) NOT NULL UNIQUE,
          key_hash varchar(200) NOT NULL,
          key_salt varchar(64) NOT NULL,
          capabilities varchar[] NOT NULL DEFAULT '{}',
          expires_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # A manual record needs a supplier because the console inner-joins one, but
    # a sentinel must never reach a shopper: the publish gate rejects it. The
    # flag, not the id, is what the gate tests.
    op.execute(
        "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS is_placeholder boolean "
        "NOT NULL DEFAULT false"
    )
    op.execute(
        f"""
        INSERT INTO suppliers (id, external_id, name, status, is_placeholder,
                               created_at, updated_at)
        VALUES ('{HOUSE_SUPPLIER_ID}', 'house-unassigned', 'House (unassigned)',
                'ACTIVE', true, now(), now())
        ON CONFLICT (id) DO UPDATE SET is_placeholder = true
        """
    )

    op.execute(
        """
        ALTER TABLE experiences
          ADD COLUMN IF NOT EXISTS source_type varchar(20) NOT NULL DEFAULT 'reference',
          ADD COLUMN IF NOT EXISTS partner_id uuid REFERENCES partners(id),
          ADD COLUMN IF NOT EXISTS source_language varchar(10) NOT NULL DEFAULT 'en',
          ADD COLUMN IF NOT EXISTS content_version integer NOT NULL DEFAULT 1
        """
    )
    # `ADD COLUMN IF NOT EXISTS` is a no-op when a database built from current
    # model metadata already has the column, and SQLAlchemy's `default=` is
    # client-side only - so without this the migrated and freshly created
    # schemas disagree about defaults, and raw-SQL inserts fail on one of them.
    op.execute(
        """
        ALTER TABLE experiences
          ALTER COLUMN source_type SET DEFAULT 'reference',
          ALTER COLUMN source_language SET DEFAULT 'en',
          ALTER COLUMN content_version SET DEFAULT 1
        """
    )
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_partner")
    op.execute(
        """
        ALTER TABLE experiences
          ADD CONSTRAINT ck_experience_partner CHECK (
            (source_type = 'partner' AND partner_id IS NOT NULL) OR
            (source_type <> 'partner' AND partner_id IS NULL)
          )
        """
    )
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_source_type")
    op.execute(
        """
        ALTER TABLE experiences
          ADD CONSTRAINT ck_experience_source_type CHECK (
            source_type IN ('manual', 'partner', 'reference')
          )
        """
    )
    # Global uniqueness on external_id would stop two partners using the same
    # id, and force operator-authored records to invent one. Scope it instead.
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS experiences_external_id_key")
    op.execute("ALTER TABLE experiences ALTER COLUMN external_id DROP NOT NULL")
    # A manual record with an external id can be matched and overwritten by an
    # importer that owns none of it; an imported record without one cannot be
    # matched at all, so every run would create a duplicate.
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_external_id")
    op.execute(
        """
        ALTER TABLE experiences
          ADD CONSTRAINT ck_experience_external_id CHECK (
            (source_type = 'manual' AND external_id IS NULL) OR
            (source_type <> 'manual' AND external_id IS NOT NULL)
          )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_experience_reference_external
          ON experiences (external_id)
          WHERE source_type = 'reference' AND external_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_experience_partner_external
          ON experiences (partner_id, external_id)
          WHERE source_type = 'partner' AND external_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experience_translations (
          experience_id uuid NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
          locale varchar(10) NOT NULL,
          title text NOT NULL DEFAULT '',
          short_description text NOT NULL DEFAULT '',
          description text NOT NULL DEFAULT '',
          meeting_point text NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (experience_id, locale)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_translations (
          option_id uuid NOT NULL REFERENCES experience_options(id) ON DELETE CASCADE,
          locale varchar(10) NOT NULL,
          name text NOT NULL DEFAULT '',
          description text NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (option_id, locale)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_translations (
          media_id uuid NOT NULL REFERENCES experience_media(id) ON DELETE CASCADE,
          locale varchar(10) NOT NULL,
          alt_text text NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (media_id, locale)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS taxonomy_terms (
          id uuid PRIMARY KEY,
          kind varchar(40) NOT NULL,
          code varchar(80) NOT NULL,
          is_active boolean NOT NULL DEFAULT true,
          CONSTRAINT ux_taxonomy_term UNIQUE (kind, code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS taxonomy_labels (
          term_id uuid NOT NULL REFERENCES taxonomy_terms (id) ON DELETE CASCADE,
          locale varchar(10) NOT NULL,
          label varchar(200) NOT NULL,
          PRIMARY KEY (term_id, locale)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_fields (
          entity_type varchar(20) NOT NULL,
          entity_id uuid NOT NULL,
          field varchar(40) NOT NULL,
          locale varchar(10) NOT NULL,
          candidate_value text,
          candidate_fingerprint varchar(64),
          provenance varchar(20) NOT NULL DEFAULT 'machine',
          status varchar(20) NOT NULL DEFAULT 'pending',
          published_fingerprint varchar(64),
          desired_fingerprint varchar(64) NOT NULL,
          generation bigint NOT NULL DEFAULT 0,
          reviewed_by varchar(200),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (entity_type, entity_id, field, locale),
          CONSTRAINT ck_translation_field_status CHECK (
            status IN ('pending', 'current', 'needs_review', 'rejected', 'failed')
          ),
          CONSTRAINT ck_translation_field_provenance CHECK (
            provenance IN ('machine', 'manual', 'imported')
          )
        )
        """
    )
    # The stale set is derived from fingerprint disagreement, never a flag
    # anyone maintains, so the backlog query has to be fast.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_translation_fields_stale
          ON translation_fields (locale)
          WHERE published_fingerprint IS DISTINCT FROM desired_fingerprint
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_jobs (
          id uuid PRIMARY KEY,
          entity_type varchar(20) NOT NULL,
          entity_id uuid NOT NULL,
          field varchar(40) NOT NULL,
          locale varchar(10) NOT NULL,
          fingerprint varchar(64) NOT NULL,
          generation bigint NOT NULL DEFAULT 0,
          status varchar(20) NOT NULL DEFAULT 'queued',
          lease_token uuid,
          leased_until timestamptz,
          attempts integer NOT NULL DEFAULT 0,
          error_detail text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ux_translation_job_target
            UNIQUE (entity_type, entity_id, field, locale, fingerprint, generation)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_translation_jobs_status ON translation_jobs (status)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_glossary (
          term varchar(120) NOT NULL,
          target_locale varchar(10) NOT NULL,
          replacement varchar(200) NOT NULL DEFAULT '',
          do_not_translate boolean NOT NULL DEFAULT false,
          revision integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (term, target_locale)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_overrides (
          experience_id uuid NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
          entity_type varchar(20) NOT NULL,
          entity_id uuid NOT NULL,
          field varchar(40) NOT NULL,
          locale varchar(10) NOT NULL,
          updated_by varchar(200) NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (experience_id, entity_type, entity_id, field, locale)
        )
        """
    )
    # Carry existing corrections across. Prose belongs to the record's source
    # language; everything else is language-neutral. Getting this backwards
    # would either free a human edit for overwrite or freeze all eight locales.
    op.execute(
        """
        INSERT INTO content_overrides (experience_id, entity_type, entity_id, field,
                                       locale, updated_by, updated_at)
        SELECT o.experience_id,
               'experience',
               o.experience_id,
               f.key,
               CASE WHEN f.key IN ('title', 'short_description', 'description',
                                   'meeting_point', 'promotion_label', 'review_note')
                    THEN e.source_language ELSE '*' END,
               o.updated_by,
               o.updated_at
          FROM experience_overrides o
          JOIN experiences e ON e.id = o.experience_id
          CROSS JOIN LATERAL jsonb_each(o.fields) AS f(key, value)
         WHERE f.value = 'true'::jsonb
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_submissions (
          id uuid PRIMARY KEY,
          partner_id uuid NOT NULL REFERENCES partners(id),
          external_id varchar(100) NOT NULL,
          experience_id uuid REFERENCES experiences(id),
          payload jsonb NOT NULL DEFAULT '{}'::jsonb,
          payload_hash varchar(64) NOT NULL,
          revision_number integer NOT NULL DEFAULT 1,
          supersedes_id uuid REFERENCES partner_submissions(id),
          base_content_version integer,
          status varchar(20) NOT NULL DEFAULT 'submitted',
          submitted_at timestamptz NOT NULL DEFAULT now(),
          decided_by varchar(200),
          decided_at timestamptz,
          decision_note text NOT NULL DEFAULT '',
          CONSTRAINT ux_partner_submission_payload
            UNIQUE (partner_id, external_id, payload_hash),
          CONSTRAINT ux_partner_submission_revision
            UNIQUE (partner_id, external_id, revision_number)
        )
        """
    )
    # At most one open submission per product, so a reviewer is never asked to
    # choose between two versions of the same truth.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_one_open_submission
          ON partner_submissions (partner_id, external_id)
          WHERE status = 'submitted'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_partner_submissions_status "
        "ON partner_submissions (status, submitted_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS index_work_items (
          experience_id uuid NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
          locale varchar(10) NOT NULL,
          fingerprint varchar(64) NOT NULL,
          status varchar(20) NOT NULL DEFAULT 'queued',
          lease_token uuid,
          leased_until timestamptz,
          attempts integer NOT NULL DEFAULT 0,
          error_detail text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (experience_id, locale)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_index_work_items_status ON index_work_items (status)")
    op.execute("ALTER TABLE index_work_items DROP CONSTRAINT IF EXISTS ck_index_work_item_status")
    op.execute(
        "ALTER TABLE index_work_items ADD CONSTRAINT ck_index_work_item_status "
        "CHECK (status IN ('queued', 'leased', 'done', 'failed'))"
    )

    # The search document's locale, and the key that lets more than one exist.
    op.execute(
        "ALTER TABLE experience_search_documents "
        "ADD COLUMN IF NOT EXISTS locale varchar(10) NOT NULL DEFAULT 'en'"
    )
    op.execute("ALTER TABLE experience_search_documents ALTER COLUMN locale SET DEFAULT 'en'")
    op.execute(
        "ALTER TABLE experience_search_documents "
        "ADD COLUMN IF NOT EXISTS index_fingerprint varchar(64) NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE experience_search_documents ALTER COLUMN index_fingerprint SET DEFAULT ''"
    )
    op.execute(
        """
        DO $$
        DECLARE pk_name text;
        BEGIN
          SELECT conname INTO pk_name
            FROM pg_constraint
           WHERE conrelid = 'experience_search_documents'::regclass AND contype = 'p';
          IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE experience_search_documents DROP CONSTRAINT %I',
                           pk_name);
          END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE experience_search_documents ADD PRIMARY KEY (experience_id, locale)")
    # Locale leads, because every retrieval path filters on it first.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_experience_search_locale "
        "ON experience_search_documents (locale, experience_id)"
    )

    # PostgreSQL ships stemmers for en/fr/de/es and none for vi/zh/ja/ko, so the
    # trigger picks a configuration per locale instead of assuming English.
    op.execute(
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
          NEW.search_vector := setweight(
            to_tsvector(config, unaccent(NEW.document_text)), 'A');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents")
    op.execute(
        """
        CREATE TRIGGER trg_search_vector_update
        BEFORE INSERT OR UPDATE OF document_text, locale ON experience_search_documents
        FOR EACH ROW EXECUTE FUNCTION tourism_search_vector_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS index_work_items")
    op.execute("DROP TABLE IF EXISTS partner_submissions")
    op.execute("DROP TABLE IF EXISTS content_overrides")
    op.execute("DROP TABLE IF EXISTS translation_glossary")
    op.execute("DROP TABLE IF EXISTS translation_jobs")
    op.execute("DROP TABLE IF EXISTS translation_fields")
    op.execute("DROP TABLE IF EXISTS taxonomy_labels")
    op.execute("DROP TABLE IF EXISTS taxonomy_terms")
    op.execute("DROP TABLE IF EXISTS media_translations")
    op.execute("DROP TABLE IF EXISTS option_translations")
    op.execute("DROP TABLE IF EXISTS experience_translations")
    op.execute("DELETE FROM experience_search_documents WHERE locale <> 'en'")
    op.execute(
        """
        DO $$
        DECLARE pk_name text;
        BEGIN
          SELECT conname INTO pk_name
            FROM pg_constraint
           WHERE conrelid = 'experience_search_documents'::regclass AND contype = 'p';
          IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE experience_search_documents DROP CONSTRAINT %I',
                           pk_name);
          END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE experience_search_documents ADD PRIMARY KEY (experience_id)")
    # The locale-aware trigger reads NEW.locale, so PostgreSQL refuses to drop
    # the column while it exists. Restore the pre-0004 English-only trigger
    # first, or the downgrade fails halfway with the schema already mutated.
    op.execute("DROP TRIGGER IF EXISTS trg_search_vector_update ON experience_search_documents")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tourism_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector := setweight(
            to_tsvector('english', unaccent(NEW.document_text)), 'A');
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
    op.execute("DROP INDEX IF EXISTS ix_experience_search_locale")
    op.execute("ALTER TABLE experience_search_documents DROP COLUMN IF EXISTS index_fingerprint")
    op.execute("ALTER TABLE experience_search_documents DROP COLUMN IF EXISTS locale")
    op.execute("DROP INDEX IF EXISTS ux_experience_partner_external")
    op.execute("DROP INDEX IF EXISTS ux_experience_reference_external")
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_partner")
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_source_type")
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS ck_experience_external_id")
    op.execute(
        """
        ALTER TABLE experiences
          DROP COLUMN IF EXISTS content_version,
          DROP COLUMN IF EXISTS source_language,
          DROP COLUMN IF EXISTS partner_id,
          DROP COLUMN IF EXISTS source_type
        """
    )
    # Restore exactly what 0003 guaranteed. A downgrade that leaves the new
    # relaxations in place produces a schema that is neither the old one nor the
    # new one, and the re-upgrade then leans on objects it should have had to
    # create itself - which is the whole property the round-trip test exists to
    # check.
    op.execute("DELETE FROM experiences WHERE external_id IS NULL")
    op.execute("ALTER TABLE experiences ALTER COLUMN external_id SET NOT NULL")
    op.execute("ALTER TABLE experiences DROP CONSTRAINT IF EXISTS experiences_external_id_key")
    op.execute(
        "ALTER TABLE experiences ADD CONSTRAINT experiences_external_id_key UNIQUE (external_id)"
    )
    op.execute("ALTER TABLE suppliers DROP COLUMN IF EXISTS is_placeholder")
    op.execute("DROP TABLE IF EXISTS partner_keys")
    op.execute("DROP TABLE IF EXISTS partners")
