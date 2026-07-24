import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    __table_args__ = (Index("ix_experiences_destination_status", "destination_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
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
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    popularity_score: Mapped[Decimal] = mapped_column(Numeric(8, 5), default=0)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    free_cancellation_hours: Mapped[int] = mapped_column(Integer, default=0)
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
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ExperienceSearchDocument(Base):
    __tablename__ = "experience_search_documents"
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"), primary_key=True)
    document_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512))
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(50))
    content_hash: Mapped[str] = mapped_column(String(64))
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
    summary: Mapped[str] = mapped_column(Text, default="")


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


class EmbeddingWorkItem(Base, TimestampMixin):
    __tablename__ = "embedding_work_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experience_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiences.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
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
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
