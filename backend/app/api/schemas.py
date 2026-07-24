from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ParticipantType = Literal["adult", "child", "infant", "senior", "student"]


class Participant(BaseModel):
    type: ParticipantType
    count: int = Field(ge=1, le=20)
    age: int | None = Field(default=None, ge=0, le=120)


class SearchFilters(BaseModel):
    destination_id: UUID | None = None
    destination: str | None = None
    visit_start: datetime | None = None
    visit_end: datetime | None = None
    currency: str | None = None
    max_total_price: float | None = Field(default=None, ge=0)
    category: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    max_duration_minutes: int | None = Field(default=None, ge=1)
    accessibility: list[str] = Field(default_factory=list)
    indoor_outdoor: str | None = None
    language: str | None = None
    instant_confirmation: bool | None = None
    free_cancellation: bool | None = None
    family_friendly: bool | None = None
    exclusions: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = ""
    filters: SearchFilters = Field(default_factory=SearchFilters)
    party: list[Participant] = Field(default_factory=list)
    sort: Literal["recommended", "price", "rating", "duration", "popularity"] = "recommended"
    page_size: int = Field(default=20, ge=1, le=50)
    cursor: str | None = None


class IntentValue(BaseModel):
    name: str | None = None
    confidence: float = 0.0


class SearchIntent(BaseModel):
    search_text: str
    destination: IntentValue = Field(default_factory=IntentValue)
    hard_constraints: list[dict[str, Any]] = Field(default_factory=list)
    soft_preferences: list[dict[str, Any]] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


class PriceView(BaseModel):
    participant_type: str
    currency: str
    amount: float
    minimum_age: int | None = None
    maximum_age: int | None = None


class SlotView(BaseModel):
    id: UUID
    starts_at: datetime
    ends_at: datetime
    capacity_remaining: int
    status: str


class OptionView(BaseModel):
    id: UUID
    name: str
    description: str
    validity_type: str
    free_cancellation_hours: int
    max_party_size: int
    prices: list[PriceView]
    slots: list[SlotView] = Field(default_factory=list)


class ExperienceCard(BaseModel):
    id: UUID
    slug: str
    title: str
    location: str
    short_description: str
    destination: str
    category: str
    image_url: str
    duration_minutes: int
    rating: float
    review_count: int
    price: float
    currency: str
    tags: list[str]
    badges: list[str]
    reason: str | None = None
    reason_code: str | None = None
    options: list[OptionView] = Field(default_factory=list)


class ExperienceDetail(ExperienceCard):
    description: str
    latitude: float
    longitude: float
    meeting_point: str
    languages: list[str]
    accessibility_features: list[str]
    minimum_age: int | None


class SearchTiming(BaseModel):
    intent_ms: float
    lexical_ms: float
    semantic_ms: float
    ranking_ms: float
    total_ms: float
    mode: str


class SearchResponse(BaseModel):
    query_id: UUID
    intent: SearchIntent
    effective_filters: SearchFilters
    items: list[ExperienceCard]
    recommendations: list[ExperienceCard] | None = None
    facets: dict[str, dict[str, int]]


class ExperienceListResponse(BaseModel):
    items: list[ExperienceCard]
    total: int


class RecommendationResponse(BaseModel):
    items: list[ExperienceCard]


class EventRequest(BaseModel):
    event_type: str
    experience_id: UUID | None = None
    placement: str | None = None
    query_id: UUID | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class CartItemRequest(BaseModel):
    experience_id: UUID
    option_id: UUID
    slot_id: UUID | None = None
    participants: list[Participant]


class CartItemView(BaseModel):
    id: UUID
    experience_id: UUID
    experience_title: str
    option_id: UUID
    option_name: str
    slot_id: UUID | None
    starts_at: datetime | None
    participants: list[Participant]
    unit_prices: list[PriceView]
    quantity: int
    quoted_total: float


class CartView(BaseModel):
    id: UUID
    currency: str
    items: list[CartItemView]
    subtotal: float
    total: float


class CheckoutPrepareResponse(BaseModel):
    cart: CartView
    ready: bool
    simulated_payment_notice: str
    confirmation_required: bool = True


class CheckoutConfirmRequest(BaseModel):
    confirmation: Literal["CONFIRM"]
    customer_details: dict[str, str] = Field(default_factory=dict)


class VoucherView(BaseModel):
    id: UUID
    voucher_reference: str
    qr_payload: str
    qr_image_data_url: str
    redemption_instructions: str
    valid_from: datetime
    valid_until: datetime


class BookingView(BaseModel):
    id: UUID
    booking_reference: str
    status: str
    currency: str
    total: float
    confirmed_at: datetime
    voucher: VoucherView


class ConversationCreate(BaseModel):
    query: str | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)
    result_ids: list[UUID] = Field(default_factory=list)
    party: list[Participant] = Field(default_factory=list)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AssistantAction(BaseModel):
    type: str
    experience_id: UUID | None = None
    option_id: UUID | None = None
    slot_id: UUID | None = None
    label: str | None = None
    requires_confirmation: bool = False


class AssistantProduct(ExperienceCard):
    experience_id: UUID
    availability: str
    actions: list[AssistantAction] = Field(default_factory=list)


class AssistantResponse(BaseModel):
    message: str
    state_patch: dict[str, Any] = Field(default_factory=dict)
    products: list[AssistantProduct] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    filter_updates: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[AssistantAction] = Field(default_factory=list)
    clarification: str | None = None
    relaxed_preferences: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
