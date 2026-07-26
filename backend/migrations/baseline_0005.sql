--
-- PostgreSQL database dump
--

\restrict xzL5wPFU25SOgT0qDioPwhaNw55cf9E2C08YhX0nD6cDCwhaVfiJN9Z6jO2Izri

-- Dumped from database version 18.4 (Homebrew)
-- Dumped by pg_dump version 18.4 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: tourism_search_vector_update(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tourism_search_vector_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    operator_id uuid,
    operator_email character varying(200) NOT NULL,
    action character varying(60) NOT NULL,
    entity_type character varying(40) NOT NULL,
    entity_id character varying(120) NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    changes jsonb NOT NULL
);


--
-- Name: availability_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.availability_slots (
    id uuid NOT NULL,
    option_id uuid NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    capacity_total integer NOT NULL,
    capacity_remaining integer NOT NULL,
    status character varying(20) NOT NULL,
    price_override jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: behavior_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.behavior_events (
    id bigint NOT NULL,
    session_id uuid NOT NULL,
    event_type character varying(60) NOT NULL,
    experience_id uuid,
    placement character varying(60),
    query_id uuid,
    properties jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: behavior_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.behavior_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: behavior_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.behavior_events_id_seq OWNED BY public.behavior_events.id;


--
-- Name: bookings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bookings (
    id uuid NOT NULL,
    cart_id uuid NOT NULL,
    booking_reference character varying(50) NOT NULL,
    status character varying(20) NOT NULL,
    currency character varying(3) NOT NULL,
    total numeric(12,2) NOT NULL,
    customer_details jsonb NOT NULL,
    confirmed_at timestamp with time zone NOT NULL
);


--
-- Name: business_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.business_settings (
    key character varying(80) NOT NULL,
    value jsonb NOT NULL,
    version integer NOT NULL,
    updated_by character varying(200) DEFAULT ''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cart_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cart_items (
    id uuid NOT NULL,
    cart_id uuid NOT NULL,
    experience_id uuid NOT NULL,
    option_id uuid NOT NULL,
    slot_id uuid,
    participants jsonb NOT NULL,
    unit_prices jsonb NOT NULL,
    quantity integer NOT NULL,
    quoted_total numeric(12,2) NOT NULL,
    quote_expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: carts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.carts (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    currency character varying(3) NOT NULL,
    status character varying(20) NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: content_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_overrides (
    experience_id uuid NOT NULL,
    entity_type character varying(20) NOT NULL,
    entity_id uuid NOT NULL,
    field character varying(40) NOT NULL,
    locale character varying(10) NOT NULL,
    updated_by character varying(200) DEFAULT ''::character varying NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: conversation_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_messages (
    id bigint NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    structured_payload jsonb,
    openai_response_id character varying(150),
    input_tokens integer,
    output_tokens integer,
    estimated_cost numeric(12,6),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: conversation_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_messages_id_seq OWNED BY public.conversation_messages.id;


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    status character varying(20) NOT NULL,
    state jsonb NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: destinations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.destinations (
    id uuid NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(150) NOT NULL,
    country_code character varying(2) NOT NULL,
    latitude numeric(9,6) NOT NULL,
    longitude numeric(9,6) NOT NULL,
    timezone character varying(100) NOT NULL
);


--
-- Name: embedding_work_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embedding_work_items (
    id uuid NOT NULL,
    experience_id uuid NOT NULL,
    content_hash character varying(64) NOT NULL,
    status character varying(20) NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    error_detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experience_media (
    id uuid NOT NULL,
    experience_id uuid NOT NULL,
    url text NOT NULL,
    alt_text character varying(250) NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL
);


--
-- Name: experience_options; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experience_options (
    id uuid NOT NULL,
    experience_id uuid NOT NULL,
    external_id character varying(100) NOT NULL,
    name character varying(200) NOT NULL,
    description text NOT NULL,
    validity_type character varying(20) NOT NULL,
    confirmation_type character varying(30) NOT NULL,
    cancellation_policy_code character varying(50) NOT NULL,
    free_cancellation_hours integer DEFAULT 0 NOT NULL,
    max_party_size integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experience_overrides (
    experience_id uuid NOT NULL,
    fields jsonb NOT NULL,
    updated_by character varying(200) DEFAULT ''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_search_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experience_search_documents (
    experience_id uuid NOT NULL,
    locale character varying(10) DEFAULT 'en'::character varying NOT NULL,
    document_text text NOT NULL,
    search_vector tsvector NOT NULL,
    embedding public.vector(512),
    embedding_model character varying(100) NOT NULL,
    embedding_version character varying(50) NOT NULL,
    content_hash character varying(64) NOT NULL,
    index_fingerprint character varying(64) DEFAULT ''::character varying NOT NULL,
    embedded_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experience_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experience_translations (
    experience_id uuid NOT NULL,
    locale character varying(10) NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    short_description text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    meeting_point text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experiences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiences (
    id uuid NOT NULL,
    external_id character varying(100),
    source_type character varying(20) DEFAULT 'reference'::character varying NOT NULL,
    partner_id uuid,
    source_language character varying(10) DEFAULT 'en'::character varying NOT NULL,
    content_version integer DEFAULT 1 NOT NULL,
    supplier_id uuid NOT NULL,
    destination_id uuid NOT NULL,
    slug character varying(180) NOT NULL,
    title character varying(250) NOT NULL,
    short_description text NOT NULL,
    description text NOT NULL,
    category character varying(80) NOT NULL,
    subcategories character varying[] NOT NULL,
    interest_tags character varying[] NOT NULL,
    indoor_outdoor character varying(20) NOT NULL,
    duration_minutes integer NOT NULL,
    latitude numeric(9,6) NOT NULL,
    longitude numeric(9,6) NOT NULL,
    meeting_point text NOT NULL,
    languages character varying[] NOT NULL,
    accessibility_features character varying[] NOT NULL,
    minimum_age integer,
    family_friendly boolean NOT NULL,
    instant_confirmation boolean NOT NULL,
    mobile_voucher boolean NOT NULL,
    rating numeric(2,1) NOT NULL,
    review_count integer DEFAULT 0 NOT NULL,
    popularity_score numeric(8,5) NOT NULL,
    status character varying(30) NOT NULL,
    published_at timestamp with time zone,
    needs_review boolean NOT NULL,
    review_note text DEFAULT ''::text NOT NULL,
    boost numeric(4,2) NOT NULL,
    pinned boolean NOT NULL,
    suppressed boolean NOT NULL,
    promotion_label character varying(60) NOT NULL,
    promotion_starts_at timestamp with time zone,
    promotion_ends_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_experience_external_id CHECK (((((source_type)::text = 'manual'::text) AND (external_id IS NULL)) OR (((source_type)::text <> 'manual'::text) AND (external_id IS NOT NULL)))),
    CONSTRAINT ck_experience_partner CHECK (((((source_type)::text = 'partner'::text) AND (partner_id IS NOT NULL)) OR (((source_type)::text <> 'partner'::text) AND (partner_id IS NULL)))),
    CONSTRAINT ck_experience_source_type CHECK (((source_type)::text = ANY ((ARRAY['manual'::character varying, 'partner'::character varying, 'reference'::character varying])::text[])))
);


--
-- Name: idempotency_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_records (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    operation character varying(50) NOT NULL,
    idempotency_key character varying(200) NOT NULL,
    response jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: import_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_jobs (
    id uuid NOT NULL,
    status character varying(20) NOT NULL,
    source_name character varying(250) NOT NULL,
    imported_count integer DEFAULT 0 NOT NULL,
    errors jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: index_work_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.index_work_items (
    experience_id uuid NOT NULL,
    locale character varying(10) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    status character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    lease_token uuid,
    leased_until timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    error_detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_index_work_item_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'leased'::character varying, 'done'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: media_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_translations (
    media_id uuid NOT NULL,
    locale character varying(10) NOT NULL,
    alt_text text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operators (
    id uuid NOT NULL,
    email character varying(200) NOT NULL,
    name character varying(120) NOT NULL,
    role character varying(30) NOT NULL,
    key_prefix character varying(12) NOT NULL,
    key_hash character varying(200) NOT NULL,
    key_salt character varying(64) NOT NULL,
    active boolean NOT NULL,
    last_seen_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: option_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.option_prices (
    id uuid NOT NULL,
    option_id uuid NOT NULL,
    participant_type character varying(30) NOT NULL,
    currency character varying(3) NOT NULL,
    amount numeric(12,2) NOT NULL,
    minimum_age integer,
    maximum_age integer
);


--
-- Name: option_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.option_translations (
    option_id uuid NOT NULL,
    locale character varying(10) NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: partner_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partner_keys (
    id uuid NOT NULL,
    partner_id uuid NOT NULL,
    prefix character varying(12) NOT NULL,
    key_hash character varying(200) NOT NULL,
    key_salt character varying(64) NOT NULL,
    capabilities character varying[] DEFAULT '{}'::character varying[] NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: partner_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partner_submissions (
    id uuid NOT NULL,
    partner_id uuid NOT NULL,
    external_id character varying(100) NOT NULL,
    experience_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload_hash character varying(64) NOT NULL,
    revision_number integer DEFAULT 1 NOT NULL,
    supersedes_id uuid,
    base_content_version integer,
    status character varying(20) DEFAULT 'submitted'::character varying NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_by character varying(200),
    decided_at timestamp with time zone,
    decision_note text DEFAULT ''::text NOT NULL
);


--
-- Name: partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partners (
    id uuid NOT NULL,
    slug character varying(80) NOT NULL,
    display_name character varying(160) NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    locales character varying[] DEFAULT '{}'::character varying[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: query_embedding_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.query_embedding_cache (
    key character varying(64) NOT NULL,
    normalized_text text NOT NULL,
    model character varying(100) NOT NULL,
    embedding public.vector(512) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_accessed_at timestamp with time zone DEFAULT now() NOT NULL,
    access_count integer NOT NULL
);


--
-- Name: shopping_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shopping_sessions (
    id uuid NOT NULL,
    anonymous_id character varying(100) NOT NULL,
    currency character varying(3) NOT NULL,
    destination_id uuid,
    visit_start timestamp with time zone,
    visit_end timestamp with time zone,
    party jsonb NOT NULL,
    preference_state jsonb NOT NULL,
    interest_embedding public.vector(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suppliers (
    id uuid NOT NULL,
    external_id character varying(100) NOT NULL,
    name character varying(200) NOT NULL,
    status character varying(30) NOT NULL,
    is_placeholder boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: taxonomy_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.taxonomy_labels (
    term_id uuid NOT NULL,
    locale character varying(10) NOT NULL,
    label character varying(200) NOT NULL
);


--
-- Name: taxonomy_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.taxonomy_terms (
    id uuid NOT NULL,
    kind character varying(40) NOT NULL,
    code character varying(80) NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: translation_fields; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_fields (
    entity_type character varying(20) NOT NULL,
    entity_id uuid NOT NULL,
    field character varying(40) NOT NULL,
    locale character varying(10) NOT NULL,
    candidate_value text,
    candidate_fingerprint character varying(64),
    provenance character varying(20) DEFAULT 'machine'::character varying NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    published_fingerprint character varying(64),
    desired_fingerprint character varying(64) NOT NULL,
    generation bigint DEFAULT 0 NOT NULL,
    reviewed_by character varying(200),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_translation_field_provenance CHECK (((provenance)::text = ANY ((ARRAY['machine'::character varying, 'manual'::character varying, 'imported'::character varying])::text[]))),
    CONSTRAINT ck_translation_field_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'current'::character varying, 'needs_review'::character varying, 'rejected'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: translation_glossary; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_glossary (
    term character varying(120) NOT NULL,
    target_locale character varying(10) NOT NULL,
    replacement character varying(200) DEFAULT ''::character varying NOT NULL,
    do_not_translate boolean DEFAULT false NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: translation_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_jobs (
    id uuid NOT NULL,
    entity_type character varying(20) NOT NULL,
    entity_id uuid NOT NULL,
    field character varying(40) NOT NULL,
    locale character varying(10) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    generation bigint DEFAULT 0 NOT NULL,
    status character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    lease_token uuid,
    leased_until timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    error_detail text,
    failure_kind character varying(20),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_translation_job_failure_kind CHECK (((failure_kind IS NULL) OR ((failure_kind)::text = ANY ((ARRAY['transient'::character varying, 'permanent'::character varying])::text[]))))
);


--
-- Name: translation_spend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_spend (
    day date NOT NULL,
    amount numeric(12,4) DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vouchers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vouchers (
    id uuid NOT NULL,
    booking_id uuid NOT NULL,
    voucher_reference character varying(50) NOT NULL,
    qr_payload text NOT NULL,
    redemption_instructions text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: behavior_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.behavior_events ALTER COLUMN id SET DEFAULT nextval('public.behavior_events_id_seq'::regclass);


--
-- Name: conversation_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages ALTER COLUMN id SET DEFAULT nextval('public.conversation_messages_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: availability_slots availability_slots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_slots
    ADD CONSTRAINT availability_slots_pkey PRIMARY KEY (id);


--
-- Name: behavior_events behavior_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.behavior_events
    ADD CONSTRAINT behavior_events_pkey PRIMARY KEY (id);


--
-- Name: bookings bookings_booking_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_booking_reference_key UNIQUE (booking_reference);


--
-- Name: bookings bookings_cart_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_cart_id_key UNIQUE (cart_id);


--
-- Name: bookings bookings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_pkey PRIMARY KEY (id);


--
-- Name: business_settings business_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_settings
    ADD CONSTRAINT business_settings_pkey PRIMARY KEY (key);


--
-- Name: cart_items cart_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_pkey PRIMARY KEY (id);


--
-- Name: carts carts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.carts
    ADD CONSTRAINT carts_pkey PRIMARY KEY (id);


--
-- Name: content_overrides content_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_overrides
    ADD CONSTRAINT content_overrides_pkey PRIMARY KEY (experience_id, entity_type, entity_id, field, locale);


--
-- Name: conversation_messages conversation_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages
    ADD CONSTRAINT conversation_messages_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: destinations destinations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.destinations
    ADD CONSTRAINT destinations_pkey PRIMARY KEY (id);


--
-- Name: destinations destinations_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.destinations
    ADD CONSTRAINT destinations_slug_key UNIQUE (slug);


--
-- Name: embedding_work_items embedding_work_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embedding_work_items
    ADD CONSTRAINT embedding_work_items_pkey PRIMARY KEY (id);


--
-- Name: experience_media experience_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_media
    ADD CONSTRAINT experience_media_pkey PRIMARY KEY (id);


--
-- Name: experience_options experience_options_experience_id_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_options
    ADD CONSTRAINT experience_options_experience_id_external_id_key UNIQUE (experience_id, external_id);


--
-- Name: experience_options experience_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_options
    ADD CONSTRAINT experience_options_pkey PRIMARY KEY (id);


--
-- Name: experience_overrides experience_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_overrides
    ADD CONSTRAINT experience_overrides_pkey PRIMARY KEY (experience_id);


--
-- Name: experience_search_documents experience_search_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_search_documents
    ADD CONSTRAINT experience_search_documents_pkey PRIMARY KEY (experience_id, locale);


--
-- Name: experience_translations experience_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_translations
    ADD CONSTRAINT experience_translations_pkey PRIMARY KEY (experience_id, locale);


--
-- Name: experiences experiences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiences
    ADD CONSTRAINT experiences_pkey PRIMARY KEY (id);


--
-- Name: experiences experiences_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiences
    ADD CONSTRAINT experiences_slug_key UNIQUE (slug);


--
-- Name: idempotency_records idempotency_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_pkey PRIMARY KEY (id);


--
-- Name: idempotency_records idempotency_records_session_id_operation_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_session_id_operation_idempotency_key_key UNIQUE (session_id, operation, idempotency_key);


--
-- Name: import_jobs import_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_jobs
    ADD CONSTRAINT import_jobs_pkey PRIMARY KEY (id);


--
-- Name: index_work_items index_work_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.index_work_items
    ADD CONSTRAINT index_work_items_pkey PRIMARY KEY (experience_id, locale);


--
-- Name: media_translations media_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_translations
    ADD CONSTRAINT media_translations_pkey PRIMARY KEY (media_id, locale);


--
-- Name: operators operators_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_email_key UNIQUE (email);


--
-- Name: operators operators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_pkey PRIMARY KEY (id);


--
-- Name: option_prices option_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.option_prices
    ADD CONSTRAINT option_prices_pkey PRIMARY KEY (id);


--
-- Name: option_translations option_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.option_translations
    ADD CONSTRAINT option_translations_pkey PRIMARY KEY (option_id, locale);


--
-- Name: partner_keys partner_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_keys
    ADD CONSTRAINT partner_keys_pkey PRIMARY KEY (id);


--
-- Name: partner_keys partner_keys_prefix_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_keys
    ADD CONSTRAINT partner_keys_prefix_key UNIQUE (prefix);


--
-- Name: partner_submissions partner_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT partner_submissions_pkey PRIMARY KEY (id);


--
-- Name: partners partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_pkey PRIMARY KEY (id);


--
-- Name: partners partners_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_slug_key UNIQUE (slug);


--
-- Name: query_embedding_cache query_embedding_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.query_embedding_cache
    ADD CONSTRAINT query_embedding_cache_pkey PRIMARY KEY (key);


--
-- Name: shopping_sessions shopping_sessions_anonymous_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_sessions
    ADD CONSTRAINT shopping_sessions_anonymous_id_key UNIQUE (anonymous_id);


--
-- Name: shopping_sessions shopping_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_sessions
    ADD CONSTRAINT shopping_sessions_pkey PRIMARY KEY (id);


--
-- Name: suppliers suppliers_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_external_id_key UNIQUE (external_id);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: taxonomy_labels taxonomy_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomy_labels
    ADD CONSTRAINT taxonomy_labels_pkey PRIMARY KEY (term_id, locale);


--
-- Name: taxonomy_terms taxonomy_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomy_terms
    ADD CONSTRAINT taxonomy_terms_pkey PRIMARY KEY (id);


--
-- Name: translation_fields translation_fields_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_fields
    ADD CONSTRAINT translation_fields_pkey PRIMARY KEY (entity_type, entity_id, field, locale);


--
-- Name: translation_glossary translation_glossary_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_glossary
    ADD CONSTRAINT translation_glossary_pkey PRIMARY KEY (term, target_locale);


--
-- Name: translation_jobs translation_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_jobs
    ADD CONSTRAINT translation_jobs_pkey PRIMARY KEY (id);


--
-- Name: translation_spend translation_spend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_spend
    ADD CONSTRAINT translation_spend_pkey PRIMARY KEY (day);


--
-- Name: partner_submissions ux_partner_submission_payload; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT ux_partner_submission_payload UNIQUE (partner_id, external_id, payload_hash);


--
-- Name: partner_submissions ux_partner_submission_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT ux_partner_submission_revision UNIQUE (partner_id, external_id, revision_number);


--
-- Name: taxonomy_terms ux_taxonomy_term; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomy_terms
    ADD CONSTRAINT ux_taxonomy_term UNIQUE (kind, code);


--
-- Name: translation_jobs ux_translation_job_target; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_jobs
    ADD CONSTRAINT ux_translation_job_target UNIQUE (entity_type, entity_id, field, locale, fingerprint, generation);


--
-- Name: vouchers vouchers_booking_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_booking_id_key UNIQUE (booking_id);


--
-- Name: vouchers vouchers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_pkey PRIMARY KEY (id);


--
-- Name: vouchers vouchers_voucher_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_voucher_reference_key UNIQUE (voucher_reference);


--
-- Name: ix_audit_entity_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_entity_time ON public.audit_log USING btree (entity_type, entity_id, occurred_at);


--
-- Name: ix_audit_log_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_occurred_at ON public.audit_log USING btree (occurred_at);


--
-- Name: ix_events_session_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_session_time ON public.behavior_events USING btree (session_id, occurred_at);


--
-- Name: ix_events_session_time_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_session_time_desc ON public.behavior_events USING btree (session_id, occurred_at DESC);


--
-- Name: ix_experience_search_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experience_search_embedding_hnsw ON public.experience_search_documents USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: ix_experience_search_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experience_search_fts ON public.experience_search_documents USING gin (search_vector);


--
-- Name: ix_experience_search_locale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experience_search_locale ON public.experience_search_documents USING btree (locale, experience_id);


--
-- Name: ix_experience_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experience_title_trgm ON public.experiences USING gin (title public.gin_trgm_ops);


--
-- Name: ix_experiences_destination_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experiences_destination_status ON public.experiences USING btree (destination_id, status);


--
-- Name: ix_experiences_needs_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_experiences_needs_review ON public.experiences USING btree (needs_review);


--
-- Name: ix_index_work_items_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_index_work_items_status ON public.index_work_items USING btree (status);


--
-- Name: ix_operators_key_prefix; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_operators_key_prefix ON public.operators USING btree (key_prefix);


--
-- Name: ix_partner_submissions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_partner_submissions_status ON public.partner_submissions USING btree (status, submitted_at);


--
-- Name: ix_slots_option_start_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_slots_option_start_status ON public.availability_slots USING btree (option_id, starts_at, status);


--
-- Name: ix_translation_fields_stale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_fields_stale ON public.translation_fields USING btree (locale) WHERE ((published_fingerprint)::text IS DISTINCT FROM (desired_fingerprint)::text);


--
-- Name: ix_translation_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_jobs_status ON public.translation_jobs USING btree (status);


--
-- Name: ux_experience_partner_external; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_experience_partner_external ON public.experiences USING btree (partner_id, external_id) WHERE (((source_type)::text = 'partner'::text) AND (external_id IS NOT NULL));


--
-- Name: ux_experience_reference_external; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_experience_reference_external ON public.experiences USING btree (external_id) WHERE (((source_type)::text = 'reference'::text) AND (external_id IS NOT NULL));


--
-- Name: ux_one_open_submission; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_one_open_submission ON public.partner_submissions USING btree (partner_id, external_id) WHERE ((status)::text = 'submitted'::text);


--
-- Name: experience_search_documents trg_search_vector_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_search_vector_update BEFORE INSERT OR UPDATE OF document_text, locale ON public.experience_search_documents FOR EACH ROW EXECUTE FUNCTION public.tourism_search_vector_update();


--
-- Name: audit_log audit_log_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id);


--
-- Name: availability_slots availability_slots_option_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_slots
    ADD CONSTRAINT availability_slots_option_id_fkey FOREIGN KEY (option_id) REFERENCES public.experience_options(id);


--
-- Name: behavior_events behavior_events_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.behavior_events
    ADD CONSTRAINT behavior_events_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: behavior_events behavior_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.behavior_events
    ADD CONSTRAINT behavior_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.shopping_sessions(id);


--
-- Name: bookings bookings_cart_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_cart_id_fkey FOREIGN KEY (cart_id) REFERENCES public.carts(id);


--
-- Name: cart_items cart_items_cart_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_cart_id_fkey FOREIGN KEY (cart_id) REFERENCES public.carts(id);


--
-- Name: cart_items cart_items_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: cart_items cart_items_option_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_option_id_fkey FOREIGN KEY (option_id) REFERENCES public.experience_options(id);


--
-- Name: cart_items cart_items_slot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cart_items
    ADD CONSTRAINT cart_items_slot_id_fkey FOREIGN KEY (slot_id) REFERENCES public.availability_slots(id);


--
-- Name: carts carts_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.carts
    ADD CONSTRAINT carts_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.shopping_sessions(id);


--
-- Name: content_overrides content_overrides_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_overrides
    ADD CONSTRAINT content_overrides_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id) ON DELETE CASCADE;


--
-- Name: conversation_messages conversation_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_messages
    ADD CONSTRAINT conversation_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: conversations conversations_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.shopping_sessions(id);


--
-- Name: embedding_work_items embedding_work_items_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embedding_work_items
    ADD CONSTRAINT embedding_work_items_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: experience_media experience_media_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_media
    ADD CONSTRAINT experience_media_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: experience_options experience_options_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_options
    ADD CONSTRAINT experience_options_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: experience_overrides experience_overrides_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_overrides
    ADD CONSTRAINT experience_overrides_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id) ON DELETE CASCADE;


--
-- Name: experience_search_documents experience_search_documents_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_search_documents
    ADD CONSTRAINT experience_search_documents_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: experience_translations experience_translations_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experience_translations
    ADD CONSTRAINT experience_translations_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id) ON DELETE CASCADE;


--
-- Name: experiences experiences_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiences
    ADD CONSTRAINT experiences_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: experiences experiences_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiences
    ADD CONSTRAINT experiences_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: experiences experiences_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiences
    ADD CONSTRAINT experiences_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id);


--
-- Name: idempotency_records idempotency_records_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.shopping_sessions(id);


--
-- Name: index_work_items index_work_items_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.index_work_items
    ADD CONSTRAINT index_work_items_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id) ON DELETE CASCADE;


--
-- Name: media_translations media_translations_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_translations
    ADD CONSTRAINT media_translations_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.experience_media(id) ON DELETE CASCADE;


--
-- Name: option_prices option_prices_option_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.option_prices
    ADD CONSTRAINT option_prices_option_id_fkey FOREIGN KEY (option_id) REFERENCES public.experience_options(id);


--
-- Name: option_translations option_translations_option_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.option_translations
    ADD CONSTRAINT option_translations_option_id_fkey FOREIGN KEY (option_id) REFERENCES public.experience_options(id) ON DELETE CASCADE;


--
-- Name: partner_keys partner_keys_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_keys
    ADD CONSTRAINT partner_keys_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: partner_submissions partner_submissions_experience_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT partner_submissions_experience_id_fkey FOREIGN KEY (experience_id) REFERENCES public.experiences(id);


--
-- Name: partner_submissions partner_submissions_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT partner_submissions_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: partner_submissions partner_submissions_supersedes_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_submissions
    ADD CONSTRAINT partner_submissions_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES public.partner_submissions(id);


--
-- Name: shopping_sessions shopping_sessions_destination_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_sessions
    ADD CONSTRAINT shopping_sessions_destination_id_fkey FOREIGN KEY (destination_id) REFERENCES public.destinations(id);


--
-- Name: taxonomy_labels taxonomy_labels_term_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomy_labels
    ADD CONSTRAINT taxonomy_labels_term_id_fkey FOREIGN KEY (term_id) REFERENCES public.taxonomy_terms(id) ON DELETE CASCADE;


--
-- Name: vouchers vouchers_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bookings(id);


--
-- PostgreSQL database dump complete
--

\unrestrict xzL5wPFU25SOgT0qDioPwhaNw55cf9E2C08YhX0nD6cDCwhaVfiJN9Z6jO2Izri

