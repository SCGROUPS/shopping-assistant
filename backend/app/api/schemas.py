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
    # Presentation only. Never used for any amount that is charged.
    display_currency: str | None = None
    # Which language's documents to search. Distinct from `display_currency`
    # because it selects the corpus, not the formatting: a Vietnamese query has
    # to be parsed with the configuration its documents were indexed with, or it
    # stems differently on each side and simply stops matching.
    #
    # `None` rather than `"en"`, so "the client did not say" is distinguishable
    # from "the client asked for English". With a default the route could not
    # tell them apart, and would override a session's chosen Vietnamese with an
    # English nobody requested.
    locale: str | None = None
    # Which constraints the shopper has agreed to give up if nothing matches,
    # most expendable first. This is an authorisation, not a hint: empty means
    # nothing may be relaxed and a zero-result search stays a zero-result
    # search. The search used to relax on its own initiative, which decided on
    # every shopper's behalf that their budget mattered less to them than the
    # language their guide speaks. Codes, from `relaxation_candidates`.
    relax_order: list[str] = Field(default_factory=list)


class IntentValue(BaseModel):
    name: str | None = None
    confidence: float = 0.0


class SearchIntent(BaseModel):
    search_text: str
    # Whether this request is better answered by a conversation than by a grid.
    # Only the model decides. Every rule tried here was a proxy for meaning
    # that turned out to be a proxy for language: first English function words,
    # then sentence length. Length is no better - Vietnamese writes syllables
    # as separate words, so `ve cap treo Ba Na Hills` is a six-word lookup,
    # while a real need like `can cho cho xe lan` is five words and shorter
    # than the threshold. The heuristic sent Vietnamese keyword searches to the
    # assistant and Vietnamese cries for help to the grid.
    #
    # `undetermined` is what an unreachable model returns. It exists so that
    # not knowing is a state the storefront can see and report, rather than
    # being spelled `grid` and quietly indistinguishable from a decision.
    interaction_mode: Literal["assistant", "grid", "undetermined"] = "undetermined"
    destination: IntentValue = Field(default_factory=IntentValue)
    hard_constraints: list[dict[str, Any]] = Field(default_factory=list)
    soft_preferences: list[dict[str, Any]] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    # The shopper's own words that state the date, quoted back verbatim, or
    # None if they named no date. This exists so a date can be verified without
    # reading the language: the guard used to be a regex listing English month
    # and weekday names, so a Vietnamese "ngày mai" was extracted correctly by
    # the model and then silently deleted here. Checking that the quote really
    # occurs in the request catches an invented date in any language.
    date_phrase: str | None = None
    # Constraints the sanitizer refused to apply, as codes. A dropped date used
    # to vanish without trace: the shopper typed one, the search ignored it, and
    # the results looked like an ordinary answer. Whatever we will not honour
    # has to be said out loud, and a code can be said in any language.
    dropped_constraints: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


class LocalePreferenceRequest(BaseModel):
    locale: str


class ContentFieldMeta(BaseModel):
    """Where one displayed string came from, and whether it is current.

    Three independent facts. Collapsing staleness into the same field as
    fallback loses one of them in the case that needs both: a stale English
    translation served to a German shopper is both, and reporting only
    `fallback` hides that the text is also out of date.
    """

    locale: str
    # source | manual | machine | imported | unknown
    provenance: str
    # Published, but the source has changed since.
    stale: bool = False
    # Not the language the shopper asked for.
    fallback: bool = False


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
    # Stable codes - `instant_confirmation`, `available` - never sentences.
    # These were English prose, and the only carrier of the four facts below,
    # so the client recovered the facts by matching English words in them:
    # translating a badge would have silently turned the fact off. The client
    # renders a code from its own dictionary, so the text is always in the
    # shopper's language and can never disagree with the fact.
    badges: list[str]
    available: bool
    instant_confirmation: bool
    family_friendly: bool
    free_cancellation_hours: int
    # Display-only conversion; `price`/`currency` stay authoritative for money.
    display_price: float | None = None
    display_currency: str | None = None
    # Both are None unless the underlying fact is real and above a threshold
    # where it still means something (common/urgency.py).
    scarcity: str | None = None
    social_proof: str | None = None
    reason: str | None = None
    reason_code: str | None = None
    options: list[OptionView] = Field(default_factory=list)
    # The locale this card was resolved in, and where each translated string
    # came from. Additive, per spec 4.3: the fields stay plain strings, so a
    # client that ignores this keeps working, and one that reads it can label
    # a description that fell back to English or has gone stale. A client that
    # cannot tell fallback from translation cannot tell us either.
    #
    # Required, with no default. A default of English is indistinguishable
    # from "whoever built this card forgot to say", and the second one is a
    # mistranslation reported to nobody.
    locale: str
    content_meta: dict[str, ContentFieldMeta] = Field(default_factory=dict)


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
    relaxed_preferences: list[str] = Field(
        default_factory=list,
        description=(
            "Codes for the constraints that were relaxed to find results, in the "
            "order they were given up. Codes, not prose: the client renders them "
            "in the shopper's language."
        ),
    )
    relaxation_candidates: list[str] = Field(
        default_factory=list,
        description=(
            "Codes for constraints still in force that could be given up if the "
            "shopper wants more results. Offered so the choice can be theirs "
            "rather than a fixed order decided here."
        ),
    )
    unresolved_constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Codes for constraints that were understood but not applied, either "
            "because they could not be verified or because this build does not "
            "know the field. The client tells the shopper, so a filter is never "
            "dropped in silence."
        ),
    )
    # Lifted out of `intent` onto the envelope: the client needs it on every
    # response, including the ones where intent extraction never ran.
    # Required, with no default: a response that cannot say how it should be
    # shown is a broken response, and defaulting it to `grid` is how a stale
    # backend silently reverts the storefront to keyword-only behaviour.
    interaction_mode: Literal["assistant", "grid", "undetermined"]
    # On the envelope, not only on the cards. Zero results is exactly the case
    # where a client most needs to know which corpus was searched, and exactly
    # the case where there is no card to carry it.
    locale: str


class ExperienceListResponse(BaseModel):
    items: list[ExperienceCard]
    total: int
    locale: str


class RecommendationResponse(BaseModel):
    items: list[ExperienceCard]
    locale: str


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
    # The surface that sourced the cart, so "the assistant converts better"
    # stays attributable at the point of sale rather than being re-derived.
    placement: str | None = None


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


class AssistantContext(BaseModel):
    """Storefront state handed to the assistant so a shopper never repeats work."""

    query: str | None = None
    filters: SearchFilters | None = None
    party: list[Participant] = Field(default_factory=list)
    result_ids: list[UUID] = Field(default_factory=list)
    result_count: int | None = None
    recently_viewed: list[UUID] = Field(default_factory=list)
    focused_experience_id: UUID | None = None
    cart_experience_ids: list[UUID] = Field(default_factory=list)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: AssistantContext | None = None


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
    # When the assistant speaks for itself rather than relaying the model, the
    # sentence is written here as a code the client renders from the shopper's
    # own dictionary. `message` keeps the English so a non-UI consumer still
    # gets something, but a storefront that has a code must prefer it: every one
    # of these lines used to reach a Vietnamese shopper in English, and they are
    # exactly the lines that appear when the model is unavailable - the moment a
    # shopper is least able to work around them.
    message_code: str | None = None
    message_vars: dict[str, Any] = Field(default_factory=dict)
    clarification_code: str | None = None
    # True when the assistant answered without the model. It can still search,
    # but it cannot act, and saying so is better than appearing to ignore a
    # request to book.
    degraded: bool = False
    state_patch: dict[str, Any] = Field(default_factory=dict)
    products: list[AssistantProduct] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    filter_updates: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[AssistantAction] = Field(default_factory=list)
    clarification: str | None = None
    relaxed_preferences: list[str] = Field(
        default_factory=list,
        description=(
            "Codes for the constraints that were relaxed to find results, in the "
            "order they were given up. Codes, not prose: the client renders them "
            "in the shopper's language."
        ),
    )
    citations: list[dict[str, Any]] = Field(default_factory=list)
