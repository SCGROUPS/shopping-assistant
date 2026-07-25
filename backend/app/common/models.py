import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    # The house placeholder that manually authored experiences hang off until a
    # real supplier is attached. A flag rather than a hardcoded UUID comparison
    # scattered through the publish gate, which would silently stop protecting
    # anything the day the seed id changed.
    is_placeholder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class Destination(Base):
    __tablename__ = "destinations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    country_code: Mapped[str] = mapped_column(String(2))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    timezone: Mapped[str] = mapped_column(String(100))


class Experience(Base, TimestampMixin):
    __tablename__ = "experiences"
    __table_args__ = (
        Index("ix_experiences_destination_status", "destination_id", "status"),
        CheckConstraint(
            "source_type IN ('manual', 'partner', 'reference')",
            name="ck_experience_source_type",
        ),
        # A partner record without a partner cannot be attributed; a non-partner
        # record with one claims an owner that has no rights over it.
        CheckConstraint(
            "(source_type = 'partner' AND partner_id IS NOT NULL) OR "
            "(source_type <> 'partner' AND partner_id IS NULL)",
            name="ck_experience_partner",
        ),
        # Manual records must have no external id, or the importer's scoped
        # lookup can match one and overwrite work no importer owns. Imported
        # records must have one, or they cannot be matched on the next run and
        # every import creates duplicates.
        CheckConstraint(
            "(source_type = 'manual' AND external_id IS NULL) OR "
            "(source_type <> 'manual' AND external_id IS NOT NULL)",
            name="ck_experience_external_id",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null for operator-authored records: inventing an identifier for a product
    # that has no external identity is how an importer ends up matching one.
    # Uniqueness is scoped to the source instead (see the partial indexes in
    # migration 0004), because two partners may legitimately use the same id.
    external_id: Mapped[str | None] = mapped_column(String(100))
    # Where the content came from, which decides who may overwrite it.
    source_type: Mapped[str] = mapped_column(
        String(20), default="reference", server_default="reference"
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"))
    # Fixed at creation. Changing it would invalidate every override,
    # translation and search document at once, so it is a rebase, not an edit.
    source_language: Mapped[str] = mapped_column(String(10), default="en", server_default="en")

    @validates("source_language")
    def _normalize_source_language(self, _key: str, value: str | None) -> str:
        """Store one spelling of the locale tag, whatever the caller sent.

        Locale tags are case-insensitive by specification and case-sensitive as
        database strings, so `VI` and `vi` are the same language to an operator
        and two different keys to every query that joins on locale. Normalising
        on write rather than at each read means an import, the authoring API and
        a partner submission cannot disagree about how a record is tagged.
        """
        return (value or "en").strip().lower()
    # Incremented by mutations to the source and commerce state a partner sees
    # in its diff - text, options, prices, availability policy, media. Derived
    # state (translations, search documents, merchandising) is excluded, or
    # publishing a translation would invalidate an unrelated partner approval
    # and tell the partner its base changed when nothing it can see did.
    content_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"))
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("destinations.id"))
    slug: Mapped[str] = mapped_column(String(180), unique=True)
    title: Mapped[str] = mapped_column(String(250))
    short_description: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    subcategories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    interest_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    indoor_outdoor: Mapped[str] = mapped_column(String(20))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    meeting_point: Mapped[str] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    accessibility_features: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    minimum_age: Mapped[int | None] = mapped_column(Integer)
    family_friendly: Mapped[bool] = mapped_column(Boolean, default=False)
    instant_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    mobile_voucher: Mapped[bool] = mapped_column(Boolean, default=True)
    rating: Mapped[Decimal] = mapped_column(Numeric(2, 1), default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    popularity_score: Mapped[Decimal] = mapped_column(Numeric(8, 5), default=0)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when automated classification was not confident. The product is held
    # in PENDING_REVIEW rather than sold on a guess.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Merchandising. Bounded deliberately: a campaign should be able to move a
    # product up the page, not replace relevance with whatever pays most.
    boost: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=1)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    promotion_label: Mapped[str] = mapped_column(String(60), default="")
    promotion_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promotion_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    options: Mapped[list["ExperienceOption"]] = relationship(cascade="all, delete-orphan")
    media: Mapped[list["ExperienceMedia"]] = relationship(cascade="all, delete-orphan")


class ExperienceOption(Base, TimestampMixin):
    __tablename__ = "experience_options"
    __table_args__ = (UniqueConstraint("experience_id", "external_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    validity_type: Mapped[str] = mapped_column(String(20))
    confirmation_type: Mapped[str] = mapped_column(String(30))
    cancellation_policy_code: Mapped[str] = mapped_column(String(50))
    free_cancellation_hours: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_party_size: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    prices: Mapped[list["OptionPrice"]] = relationship(cascade="all, delete-orphan")
    slots: Mapped[list["AvailabilitySlot"]] = relationship(cascade="all, delete-orphan")


class OptionPrice(Base):
    __tablename__ = "option_prices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    option_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experience_options.id"))
    participant_type: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    minimum_age: Mapped[int | None] = mapped_column(Integer)
    maximum_age: Mapped[int | None] = mapped_column(Integer)


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"
    __table_args__ = (Index("ix_slots_option_start_status", "option_id", "starts_at", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    option_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experience_options.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity_total: Mapped[int] = mapped_column(Integer)
    capacity_remaining: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    price_override: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceMedia(Base):
    __tablename__ = "experience_media"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"))
    url: Mapped[str] = mapped_column(Text)
    alt_text: Mapped[str] = mapped_column(String(250))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ExperienceSearchDocument(Base):
    """One row per experience per locale.

    The locale is part of the primary key because a Vietnamese query has nothing
    to match against English text, and every retrieval path must therefore be
    able to select the right one. Without the filter, locale variants of one
    product consume candidate slots and multiply its fusion score - neither of
    which shows up as visibly duplicated output.
    """

    __tablename__ = "experience_search_documents"
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"), primary_key=True)
    locale: Mapped[str] = mapped_column(
        String(10), primary_key=True, default="en", server_default="en"
    )
    document_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512))
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(50))
    content_hash: Mapped[str] = mapped_column(String(64))
    # Covers the document text *and* how it was built: construction version,
    # embedding model, embedding version. `content_hash` alone cannot tell a
    # document built by an older `document_text_for` from a current one when the
    # text happens to be identical, so a version bump would leave the whole
    # catalogue stale with nothing marked stale.
    index_fingerprint: Mapped[str] = mapped_column(String(64), default="", server_default="")
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShoppingSession(Base):
    __tablename__ = "shopping_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anonymous_id: Mapped[str] = mapped_column(String(100), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="VND")
    destination_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("destinations.id"))
    visit_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visit_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    party: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    preference_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    interest_embedding: Mapped[list[float] | None] = mapped_column(Vector(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BehaviorEvent(Base):
    __tablename__ = "behavior_events"
    __table_args__ = (Index("ix_events_session_time", "session_id", "occurred_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"))
    event_type: Mapped[str] = mapped_column(String(60))
    experience_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiences.id"))
    placement: Mapped[str | None] = mapped_column(String(60))
    query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    structured_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    openai_response_id: Mapped[str | None] = mapped_column(String(150))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Cart(Base, TimestampMixin):
    __tablename__ = "carts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class CartItem(Base, TimestampMixin):
    __tablename__ = "cart_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"))
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"))
    option_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experience_options.id"))
    slot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("availability_slots.id"))
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    unit_prices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    quantity: Mapped[int] = mapped_column(Integer)
    quoted_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quote_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"), unique=True)
    booking_reference: Mapped[str] = mapped_column(String(50), unique=True)
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    customer_details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Voucher(Base):
    __tablename__ = "vouchers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), unique=True)
    voucher_reference: Mapped[str] = mapped_column(String(50), unique=True)
    qr_payload: Mapped[str] = mapped_column(Text)
    redemption_instructions: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("session_id", "operation", "idempotency_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_sessions.id"))
    operation: Mapped[str] = mapped_column(String(50))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbeddingWorkItem(Base, TimestampMixin):
    __tablename__ = "embedding_work_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_detail: Mapped[str | None] = mapped_column(Text)


class QueryEmbeddingCache(Base):
    __tablename__ = "query_embedding_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    normalized_text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    embedding: Mapped[list[float]] = mapped_column(Vector(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    access_count: Mapped[int] = mapped_column(Integer, default=1)


class ImportJob(Base, TimestampMixin):
    __tablename__ = "import_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20))
    source_name: Mapped[str] = mapped_column(String(250))
    imported_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class Operator(Base, TimestampMixin):
    """A member of staff who can change the business.

    Authorisation used to be a request header any caller could set. Operators
    are real rows with a hashed credential, so an action can be attributed to a
    person - which is what makes the audit log worth keeping.
    """

    __tablename__ = "operators"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(30))
    key_prefix: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(200))
    key_salt: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only record of every operator mutation.

    Written in the same transaction as the change it describes, so the log
    cannot claim something the database does not show.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity_time", "entity_type", "entity_id", "occurred_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    operator_email: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class BusinessSetting(Base, TimestampMixin):
    """A tunable the business owns, held as data rather than deployed code.

    Ranking weights, commercial take rates and assistant policy live here so
    retuning is a change an operator makes and observes, not a release.
    """

    __tablename__ = "business_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(200), default="", server_default="")


class ExperienceOverride(Base, TimestampMixin):
    """Fields a human has corrected, and must not have overwritten.

    The importer rewrites every field on every run. Without this table an
    operator's correction survives until the next deploy and no longer, which
    would make the catalog editor a place where work goes to die.
    """

    __tablename__ = "experience_overrides"
    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[str] = mapped_column(String(200), default="", server_default="")


class Partner(Base, TimestampMixin):
    """A remote operator that submits inventory through the public API.

    Distinct from `Supplier`, which is who fulfils a booking. One organisation
    may be both, but conflating them means a change of fulfilment partner would
    silently reassign content ownership.
    """

    __tablename__ = "partners"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    locales: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")


class PartnerKey(Base, TimestampMixin):
    """Credentials for a partner.

    Same scrypt scheme as operator keys so there is one hashing path to reason
    about, plus the expiry and revocation that operator keys still lack.
    """

    __tablename__ = "partner_keys"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partners.id"))
    prefix: Mapped[str] = mapped_column(String(12), unique=True)
    key_hash: Mapped[str] = mapped_column(String(200))
    key_salt: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperienceTranslation(Base):
    """Target-locale prose. The source locale lives on `experiences` itself.

    Rows rather than columns: eight locales times four fields is thirty-two
    columns, and adding a locale would become a migration instead of a backfill.
    """

    __tablename__ = "experience_translations"
    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    short_description: Mapped[str] = mapped_column(Text, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    meeting_point: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OptionTranslation(Base):
    __tablename__ = "option_translations"
    option_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experience_options.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaTranslation(Base):
    """Alt text is content, not decoration.

    A screen-reader user browsing in Korean gets English alt text otherwise,
    which is the one case where the fallback chain is actively worse than
    silence - the assistive technology announces it in the wrong language.
    """

    __tablename__ = "media_translations"
    media_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experience_media.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    alt_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaxonomyTerm(Base):
    """The controlled vocabulary itself, with a surrogate key.

    Terms are identified by ``(kind, code)`` in business logic, but translation
    and override state address entities by UUID. Without this row a
    ``translation_fields`` entry for a taxonomy term could not name its target,
    so the surrogate id exists to make that reference expressible.
    """

    __tablename__ = "taxonomy_terms"
    __table_args__ = (UniqueConstraint("kind", "code", name="ux_taxonomy_term"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class TaxonomyLabel(Base):
    """Localized display text for codes that drive filtering.

    Categories, tags and accessibility features are enums matched during search,
    so they cannot become free text per locale - but leaving them untranslated
    puts English on a Vietnamese card. Code drives logic, label is displayed.
    """

    __tablename__ = "taxonomy_labels"
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_terms.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))


class TranslationField(Base):
    """Per-field translation *state*, and the concurrency control for it.

    Deliberately does not store the served translation: that lives in
    ``experience_translations`` / ``option_translations`` / ``taxonomy_labels``,
    which readers join. Two homes for the same string would need a rebuild
    contract nobody would maintain, so this table owns workflow only, plus the
    candidate that is by definition *not* served until a human approves it.

    A JSONB map could not hold this: two field jobs would read-modify-write the
    same document and one would silently lose. Splitting the fingerprint in two
    is what lets a source edit and a model upgrade invalidate different things -
    a new model must not mark a human's Korean title stale.
    """

    __tablename__ = "translation_fields"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'current', 'needs_review', 'rejected', 'failed')",
            name="ck_translation_field_status",
        ),
        CheckConstraint(
            "provenance IN ('machine', 'manual', 'imported')",
            name="ck_translation_field_provenance",
        ),
        Index(
            "ix_translation_fields_stale",
            "locale",
            postgresql_where=text("published_fingerprint IS DISTINCT FROM desired_fingerprint"),
        ),
    )
    entity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    field: Mapped[str] = mapped_column(String(40), primary_key=True)
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    # Held back from the storefront until a human approves it. Never served.
    candidate_value: Mapped[str | None] = mapped_column(Text)
    # Which desired_fingerprint the candidate was produced for, so a reviewer
    # approving a candidate can prove it still answers the current source text.
    candidate_fingerprint: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(20), default="machine", server_default="machine")
    # 'stale' is never stored: it is derived from
    # published_fingerprint IS DISTINCT FROM desired_fingerprint, so it cannot
    # drift out of agreement with the fingerprints that define it.
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    published_fingerprint: Mapped[str | None] = mapped_column(String(64))
    desired_fingerprint: Mapped[str] = mapped_column(String(64))
    # Bumped by source edits and manual translation edits. A worker that
    # observed an older generation loses its conditional update, which is what
    # stops a slow model publishing over a fresh human correction.
    generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranslationJob(Base, TimestampMixin):
    """Queued translation work, leased so a dead worker costs one retry."""

    __tablename__ = "translation_jobs"
    __table_args__ = (
        # Generation participates, or a source edit F0 -> F1 -> F0 would find
        # the completed F0 job still present and refuse to re-enqueue, leaving
        # the field permanently stale.
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "field",
            "locale",
            "fingerprint",
            "generation",
            name="ux_translation_job_target",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field: Mapped[str] = mapped_column(String(40))
    locale: Mapped[str] = mapped_column(String(10))
    fingerprint: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", index=True
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_detail: Mapped[str | None] = mapped_column(Text)


class TranslationGlossary(Base, TimestampMixin):
    """Terms a translator must not invent.

    Instructing a model to leave "Hoi An" alone is a request. Checking the
    output for it is a guarantee, which is why `do_not_translate` is verified
    after generation rather than only prompted.
    """

    __tablename__ = "translation_glossary"
    term: Mapped[str] = mapped_column(String(120), primary_key=True)
    target_locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    replacement: Mapped[str] = mapped_column(String(200), default="", server_default="")
    do_not_translate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The recipe fingerprint hashes this number, not the glossary contents, so
    # editing a term is an explicit decision to invalidate translations rather
    # than an accidental one. Bumped by the operator who edits the glossary.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class ContentOverride(Base):
    """A human correction, per entity, per field, per locale.

    The flat map it replaces could not say which language or which child entity
    was corrected, so one Vietnamese edit froze all eight locales - the failure
    the table exists to prevent, inverted. `experience_id` is denormalized onto
    every row so the importer can load a record's whole override set at once.
    """

    __tablename__ = "content_overrides"
    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )
    entity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    field: Mapped[str] = mapped_column(String(40), primary_key=True)
    # '*' means language-neutral, and applies only to fields that genuinely are:
    # price, status, capacity, merchandising. Prose is recorded against a real
    # locale, never '*'.
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    updated_by: Mapped[str] = mapped_column(String(200), default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PartnerSubmission(Base):
    """A staged partner revision, never applied to live inventory directly.

    Applying on receipt would either take a published product offline or publish
    unreviewed third-party text. Staging turns an open write endpoint into a
    proposal queue.
    """

    __tablename__ = "partner_submissions"
    __table_args__ = (
        UniqueConstraint(
            "partner_id", "external_id", "payload_hash", name="ux_partner_submission_payload"
        ),
        # The advisory lock in the service serialises the normal path; this
        # constraint is what protects the invariant from a writer that forgets
        # to take it. Two revisions may not claim the same number.
        UniqueConstraint(
            "partner_id", "external_id", "revision_number", name="ux_partner_submission_revision"
        ),
        # The review queue is read as "oldest pending first", so the sort key
        # belongs in the index; on `status` alone every read still sorts.
        Index("ix_partner_submissions_status", "status", "submitted_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partners.id"))
    external_id: Mapped[str] = mapped_column(String(100))
    experience_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiences.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Over the canonical form, not raw bytes: reformatting is not a change, and
    # treating it as one would defeat the point of no-op resubmission.
    payload_hash: Mapped[str] = mapped_column(String(64))
    revision_number: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partner_submissions.id"))
    # What the diff was computed against. Re-checked under lock at approval, so
    # a competing operator edit cannot be silently overwritten.
    base_content_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="submitted", server_default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="", server_default="")


class IndexWorkItem(Base, TimestampMixin):
    """Pending search-document rebuild, enqueued in the same transaction as the
    content change that caused it.

    An outbox rather than a queue: if the write commits the reindex is
    guaranteed, and if it rolls back so does the intent. Enqueueing after commit
    would lose work in the gap, which is exactly how an index silently drifts
    away from the catalogue it claims to describe.
    """

    __tablename__ = "index_work_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'leased', 'done', 'failed')",
            name="ck_index_work_item_status",
        ),
    )

    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(10), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    # 'failed' is terminal and deliberately distinct from 'queued': an item that
    # has exhausted its attempts is invisible to the leasing query, so leaving it
    # 'queued' means a permanently stranded row that every status view reports as
    # pending work being handled.
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", index=True
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_detail: Mapped[str | None] = mapped_column(Text)
