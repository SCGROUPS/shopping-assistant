import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.api.schemas import (
    AssistantAction,
    AssistantContext,
    AssistantProduct,
    AssistantResponse,
    CartItemRequest,
    ConversationCreate,
    MessageRequest,
    Participant,
    SearchFilters,
    SearchRequest,
)
from app.assistant.provider import AIProvider, build_ai_provider
from app.bookings.service import BookingService
from app.cart.service import CartService
from app.catalog.service import get_product_async, product_card, product_detail
from app.common.config import get_settings
from app.common.errors import ApiError
from app.common.models import (
    Conversation,
    ConversationMessage,
    ShoppingSession,
)
from app.common.persistence import (
    database_mode,
    ensure_session,
    require_session_factory,
)
from app.common.store import DemoStore, store
from app.recommendations.service import RecommendationService
from app.search.service import SearchService

logger = logging.getLogger(__name__)

CONFIRM_PHRASES = {"confirm", "confirm booking", "yes, confirm", "confirm checkout"}

ORDINALS = {
    "first": 0, "1st": 0, "one": 0,
    "second": 1, "2nd": 1, "two": 1,
    "third": 2, "3rd": 2, "three": 2,
    "fourth": 3, "4th": 3, "four": 3,
    "fifth": 4, "5th": 4, "five": 4,
    "last": -1,
}


def keyword_tool(lowered: str) -> str | None:
    """Deterministic fallback used only when the planner is unavailable."""
    if lowered in CONFIRM_PHRASES:
        return "confirm_simulated_checkout"
    if any(term in lowered for term in ("checkout", "pay", "book now")):
        return "prepare_checkout"
    if "add" in lowered and "cart" in lowered:
        return "add_to_cart"
    if "availability" in lowered or "available" in lowered:
        return "check_availability"
    if "compare" in lowered:
        return "compare_experiences"
    if any(term in lowered for term in ("similar", "also like", "complete my day")):
        return "get_recommendations"
    return None


def _resolve_referent(products: list[dict[str, Any]], message: str) -> dict[str, Any]:
    """Resolve which of the last results the shopper means.

    Falls back to the first result, but honours an explicit title mention or an
    ordinal reference such as "add the second one to my cart".
    """
    if not message:
        return products[0]
    lowered = message.casefold()
    titled = [
        product for product in products if product["title"].casefold() in lowered
    ]
    if titled:
        return titled[0]
    for word, index in ORDINALS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            try:
                return products[index]
            except IndexError:
                break
    return products[0]


class AssistantService:
    def __init__(self, data: DemoStore = store, ai_provider: AIProvider | None = None) -> None:
        self.data = data
        self.ai = ai_provider or build_ai_provider()
        self.search = SearchService(data, self.ai)
        self.carts = CartService(data)
        self.bookings = BookingService(data)
        self.recommendations = RecommendationService(data)
        self.settings = get_settings()

    async def create(
        self, session_id: str, request: ConversationCreate
    ) -> dict[str, Any]:
        conversation_id = uuid4()
        state = {
            "filters": request.filters.model_dump(mode="json"),
            "last_result_ids": [str(item) for item in request.result_ids],
            "selected_experience_ids": [],
            "party": [item.model_dump(mode="json") for item in request.party],
            "hard_constraints": [],
            "soft_preferences": [],
            "pending_action": None,
        }
        conversation_data = {
            "id": conversation_id,
            "session_id": session_id,
            "status": "ACTIVE",
            "state": state,
            "summary": request.query or "",
            "messages": [],
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        if not database_mode():
            self.data.conversations[conversation_id] = conversation_data
            return conversation_data
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            db.add(
                Conversation(
                    id=conversation_id,
                    session_id=shopping_session.id,
                    status="ACTIVE",
                    state=state,
                    summary=request.query or "",
                )
            )
        return conversation_data

    async def get(
        self, conversation_id: UUID, session_id: str
    ) -> dict[str, Any]:
        if not database_mode():
            conversation = self.data.conversations.get(conversation_id)
            if conversation and conversation["session_id"] == session_id:
                return conversation
            raise ApiError(
                404,
                "Conversation not found",
                "The conversation does not exist",
                "not-found",
            )
        factory = require_session_factory()
        async with factory() as db:
            row = await db.execute(
                select(Conversation, ShoppingSession.anonymous_id)
                .join(ShoppingSession, ShoppingSession.id == Conversation.session_id)
                .where(
                    Conversation.id == conversation_id,
                    ShoppingSession.anonymous_id == session_id,
                )
            )
            result = row.one_or_none()
            if result is None:
                raise ApiError(
                    404,
                    "Conversation not found",
                    "The conversation does not exist",
                    "not-found",
                )
            conversation, _anonymous_id = result
            messages = (
                await db.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation.id)
                    .order_by(ConversationMessage.id)
                )
            ).all()
            return {
                "id": conversation.id,
                "session_id": session_id,
                "status": conversation.status,
                "state": dict(conversation.state),
                "summary": conversation.summary,
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "structured_payload": message.structured_payload,
                    }
                    for message in messages
                ],
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }

    async def _save_turn(
        self,
        conversation: dict[str, Any],
        *,
        user_message: str,
        response: AssistantResponse,
    ) -> None:
        if not database_mode():
            conversation["messages"].append(
                {"role": "user", "content": user_message}
            )
            conversation["messages"].append(
                {
                    "role": "assistant",
                    "content": response.message,
                    "structured_payload": response.model_dump(mode="json"),
                }
            )
            conversation["updated_at"] = datetime.now(UTC)
            return
        factory = require_session_factory()
        async with factory() as db, db.begin():
            db.add_all(
                [
                    ConversationMessage(
                        conversation_id=conversation["id"],
                        role="user",
                        content=user_message,
                        structured_payload=None,
                    ),
                    ConversationMessage(
                        conversation_id=conversation["id"],
                        role="assistant",
                        content=response.message,
                        structured_payload=response.model_dump(mode="json"),
                    ),
                ]
            )
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation["id"])
                .values(
                    state=conversation["state"],
                    updated_at=datetime.now(UTC),
                )
            )

    async def _conversation_message_count(self, conversation_id: UUID) -> int:
        if not database_mode():
            conversation = self.data.conversations[conversation_id]
            return len(conversation["messages"])
        factory = require_session_factory()
        async with factory() as db:
            messages = (
                await db.scalars(
                    select(ConversationMessage.id).where(
                        ConversationMessage.conversation_id == conversation_id
                    )
                )
            ).all()
            return len(messages)

    async def respond(
        self, conversation_id: UUID, session_id: str, request: MessageRequest
    ) -> AssistantResponse:
        conversation = await self.get(conversation_id, session_id)
        message_count = await self._conversation_message_count(conversation_id)
        if message_count // 2 >= self.settings.assistant_max_session_turns:
            raise ApiError(
                429,
                "Conversation limit reached",
                "Start a new conversation to continue.",
                "assistant-turn-limit",
            )
        lowered = request.message.casefold().strip()
        self._merge_context(conversation, request.context)
        planned_tool = None
        try:
            planned_tool = await self.ai.plan_action(request.message, conversation["state"])
        except Exception:
            logger.exception("Assistant action planning failed")
            planned_tool = None

        # The planner decides; keyword matching is only a fallback for when planning
        # is unavailable (demo mode) or returns nothing.
        tool = planned_tool or keyword_tool(lowered)
        if tool == "confirm_simulated_checkout" and not self._confirmation_allowed(
            conversation, lowered
        ):
            # Re-show the summary if the shopper is mid-checkout, otherwise treat the
            # planner's guess as noise rather than acting on it.
            tool = (
                "prepare_checkout"
                if conversation["state"].get("pending_action") == "CONFIRM_CHECKOUT"
                else None
            )

        response: AssistantResponse
        if tool == "confirm_simulated_checkout":
            response = await self._confirm_checkout(conversation, session_id)
        elif tool == "prepare_checkout":
            response = await self._prepare_checkout(conversation, session_id)
        elif tool == "add_to_cart":
            response = await self._add_selected_result(
                conversation, session_id, request.message
            )
        elif tool == "check_availability":
            response = await self._availability(conversation, request.message)
        elif tool == "compare_experiences":
            response = await self._compare(conversation)
        elif tool == "get_recommendations":
            response = await self._recommend(conversation, session_id)
        else:
            response = await self._search(conversation, request.message)

        await self._save_turn(
            conversation,
            user_message=request.message,
            response=response,
        )
        return response

    @staticmethod
    def _confirmation_allowed(conversation: dict[str, Any], lowered: str) -> bool:
        """A simulated booking requires an explicit user confirmation and is never
        triggered by a model decision alone.

        `_confirm_checkout` separately enforces that a booking summary is pending.
        """
        return lowered in CONFIRM_PHRASES

    @staticmethod
    def _merge_context(
        conversation: dict[str, Any], context: AssistantContext | None
    ) -> None:
        """Fold live storefront state into the conversation.

        The storefront and the assistant are two renderings of one session, so
        filters the shopper set in the grid must apply here without being retyped.
        """
        if context is None:
            return
        state = conversation["state"]
        if context.filters is not None:
            merged = {
                **state.get("filters", {}),
                **{
                    key: value
                    for key, value in context.filters.model_dump(mode="json").items()
                    if value not in (None, [], "")
                },
            }
            state["filters"] = merged
        if context.party:
            state["party"] = [person.model_dump(mode="json") for person in context.party]
        if context.result_ids:
            state["last_result_ids"] = [str(item) for item in context.result_ids]
        if context.query:
            state["last_query"] = context.query
        if context.result_count is not None:
            state["last_result_count"] = context.result_count
        if context.recently_viewed:
            state["recently_viewed"] = [str(item) for item in context.recently_viewed]
        if context.cart_experience_ids:
            state["cart_experience_ids"] = [
                str(item) for item in context.cart_experience_ids
            ]
        if context.focused_experience_id:
            focused = str(context.focused_experience_id)
            state["focused_experience_id"] = focused
            # A shopper who opened the assistant from a card means that card.
            state["last_result_ids"] = [
                focused,
                *[item for item in state.get("last_result_ids", []) if item != focused],
            ]

    async def _search(self, conversation: dict[str, Any], message: str) -> AssistantResponse:
        filters = SearchFilters.model_validate(conversation["state"].get("filters", {}))
        result = await self.search.search(
            SearchRequest(
                query=message,
                filters=filters,
                party=conversation["state"].get("party", []),
                page_size=5,
            )
        )
        if result.intent.needs_clarification:
            question = (
                result.intent.clarification_question
                or "Please clarify the required date, budget, or accessibility constraint."
            )
            return AssistantResponse(message=question, clarification=question)
        ids = [str(product.id) for product in result.items]
        conversation["state"]["last_result_ids"] = ids
        conversation["state"]["filters"] = result.effective_filters.model_dump(mode="json")
        products = [
            self._commerce_product(
                await get_product_async(product.id, self.data),
                product.reason or "Matches your request.",
            )
            for product in result.items
        ]
        if not products:
            return AssistantResponse(
                message=(
                    "I could not find an exact match without relaxing your constraints. "
                    "Which constraint would you like to change?"
                ),
                clarification="Would you like to change destination, budget, or activity type?",
            )
        facts = [
            {
                "id": str(product.id),
                "title": product.title,
                "price": product.price,
                "currency": product.currency,
                "reason": product.reason,
            }
            for product in result.items[:4]
        ]
        message_text = (
            f"I found {len(products)} grounded options. The first choices best match your request."
        )
        try:
            enhanced = await self.ai.enhance_assistant(message, facts)
            if enhanced:
                message_text = enhanced
        except Exception:
            logger.exception("Grounded assistant prose enhancement failed")
        return AssistantResponse(
            message=message_text,
            state_patch={"filters": conversation["state"]["filters"], "last_result_ids": ids},
            products=products,
            filter_updates=[
                {"field": key, "value": value, "source": "inferred"}
                for key, value in result.effective_filters.model_dump(mode="json").items()
                if value not in (None, [], "")
            ],
            citations=[
                {
                    "experience_id": str(product.experience_id),
                    "fields": [
                        "title",
                        "price",
                        "family_friendly",
                        "indoor_outdoor",
                    ],
                }
                for product in products
            ],
        )

    async def _availability(
        self, conversation: dict[str, Any], message: str = ""
    ) -> AssistantResponse:
        product = await self._selected_product(conversation, message)
        detail = product_detail(product)
        slots = detail.options[0].slots[:3]
        if not slots:
            return AssistantResponse(message=f"No upcoming slots are available for {detail.title}.")
        slot_text = ", ".join(slot.starts_at.strftime("%d %b %H:%M UTC") for slot in slots)
        product_view = self._commerce_product(
            product, "Live demo inventory was checked."
        )
        product_view = product_view.model_copy(
            update={
                "actions": [
                    AssistantAction(
                        type="ADD_TO_CART",
                        experience_id=detail.id,
                        option_id=detail.options[0].id,
                        slot_id=slot.id,
                        label=f"Add {slot.starts_at:%d %b %H:%M} to cart",
                    )
                    for slot in slots
                ]
            }
        )
        return AssistantResponse(
            message=f"{detail.title} has availability at {slot_text}.",
            products=[product_view],
            citations=[
                {
                    "experience_id": str(detail.id),
                    "fields": ["options.slots", "options.prices"],
                }
            ],
        )

    async def _compare(
        self, conversation: dict[str, Any]
    ) -> AssistantResponse:
        ids = conversation["state"].get("last_result_ids", [])[:3]
        products = [
            await get_product_async(UUID(item), self.data) for item in ids
        ]
        if len(products) < 2:
            return AssistantResponse(
                message="Please search for at least two experiences before asking me to compare."
            )
        rows = [
            {
                "experience_id": str(product["id"]),
                "title": product["title"],
                "duration_minutes": product["duration_minutes"],
                "indoor_outdoor": product["indoor_outdoor"],
                "accessibility": product["accessibility_features"],
                "family_friendly": product["family_friendly"],
                "free_cancellation_hours": product["options"][0]["free_cancellation_hours"],
                "adult_price": product["options"][0]["prices"][0]["amount"],
                "currency": "VND",
            }
            for product in products
        ]
        return AssistantResponse(
            message="Here is a fact-based comparison of the leading options.",
            comparison={"columns": list(rows[0]), "rows": rows},
            products=[
                self._commerce_product(product, "Included in this fact-based comparison.")
                for product in products
            ],
            citations=[
                {
                    "experience_id": str(product["id"]),
                    "fields": [
                        "duration_minutes",
                        "accessibility_features",
                        "options.prices",
                    ],
                }
                for product in products
            ],
        )

    async def _recommend(
        self, conversation: dict[str, Any], session_id: str
    ) -> AssistantResponse:
        current = await self._selected_product(conversation)
        result = await self.recommendations.recommend(
            session_id=session_id,
            placement="complete_your_day",
            experience_id=current["id"],
            limit=4,
            filters=SearchFilters.model_validate(conversation["state"].get("filters", {})),
            party=[
                Participant.model_validate(item)
                for item in conversation["state"].get("party", [])
            ],
        )
        if not result.items:
            return AssistantResponse(
                message=(
                    "I could not find a complementary experience that is still bookable "
                    "for your dates and party. Would you like me to try another day?"
                ),
                clarification="Shall I look at nearby dates?",
            )
        products = [
            self._commerce_product(
                await get_product_async(item.id, self.data),
                item.reason or "Complements your current choice.",
                item.reason_code,
            )
            for item in result.items
        ]
        return AssistantResponse(
            message="These diverse options complement your current choice.",
            products=products,
            citations=[
                {
                    "experience_id": str(item.id),
                    "fields": ["category", "destination", "availability"],
                }
                for item in result.items
            ],
        )

    async def _add_selected_result(
        self, conversation: dict[str, Any], session_id: str, message: str = ""
    ) -> AssistantResponse:
        product = await self._selected_product(conversation, message)
        option = product["options"][0]
        participants = [
            Participant.model_validate(item)
            for item in conversation["state"].get("party", [])
        ] or [Participant(type="adult", count=1)]
        slot = next(
            (
                item
                for item in option["slots"]
                if item["capacity_remaining"]
                >= sum(participant.count for participant in participants)
                and item["starts_at"] > datetime.now(UTC)
            ),
            None,
        )
        cart = await self.carts.add_item(
            session_id,
            CartItemRequest(
                experience_id=product["id"],
                option_id=option["id"],
                slot_id=slot["id"] if slot else None,
                participants=participants,
            ),
            f"assistant-{conversation['id']}-{await self._conversation_message_count(conversation['id'])}",
        )
        conversation["state"]["pending_action"] = None
        party_label = ", ".join(
            f"{participant.count} {participant.type}"
            f"{'s' if participant.count != 1 else ''}"
            for participant in participants
        )
        return AssistantResponse(
            message=(
                f"Added {product['title']} for {party_label} to the cart. "
                f"The simulated total is {cart.total:,.0f} {cart.currency}."
            ),
            actions=[AssistantAction(type="PREPARE_CHECKOUT", label="Review checkout")],
            citations=[
                {
                    "experience_id": str(product["id"]),
                    "fields": ["options.prices", "options.slots"],
                }
            ],
        )

    async def _prepare_checkout(
        self, conversation: dict[str, Any], session_id: str
    ) -> AssistantResponse:
        cart = await self.carts.validate(session_id)
        conversation["state"]["pending_action"] = "CONFIRM_CHECKOUT"
        return AssistantResponse(
            message=(
                f"Final simulated booking total: {cart.total:,.0f} {cart.currency} "
                f"for {len(cart.items)} item(s). No real payment will be taken. "
                "Reply exactly “confirm” to create the booking and QR voucher."
            ),
            state_patch={"pending_action": "CONFIRM_CHECKOUT"},
            actions=[
                AssistantAction(
                    type="CONFIRM_SIMULATED_CHECKOUT",
                    label="Confirm simulated purchase",
                    requires_confirmation=True,
                )
            ],
        )

    async def _confirm_checkout(
        self, conversation: dict[str, Any], session_id: str
    ) -> AssistantResponse:
        if conversation["state"].get("pending_action") != "CONFIRM_CHECKOUT":
            return AssistantResponse(
                message="I cannot book yet. Ask me to prepare checkout first so you can review the total."
            )
        booking = await self.bookings.confirm(
            session_id,
            idempotency_key=f"assistant-checkout-{conversation['id']}",
        )
        conversation["state"]["pending_action"] = None
        return AssistantResponse(
            message=(
                f"Your simulated booking {booking.booking_reference} is confirmed. "
                f"Voucher {booking.voucher.voucher_reference} is ready."
            ),
            state_patch={"pending_action": None, "booking_id": str(booking.id)},
            actions=[AssistantAction(type="VIEW_VOUCHER")],
        )

    async def _selected_product(
        self, conversation: dict[str, Any], message: str = ""
    ) -> dict[str, Any]:
        ids = conversation["state"].get("last_result_ids", [])
        if not ids:
            raise ApiError(
                409,
                "No selected experience",
                "Search for an experience before this action.",
                "no-selection",
            )
        products = [await get_product_async(UUID(item), self.data) for item in ids]
        return _resolve_referent(products, message)

    def _commerce_product(
        self,
        product: dict[str, Any],
        reason: str,
        reason_code: str | None = None,
    ) -> AssistantProduct:
        card = product_card(product, [reason], reason_code=reason_code).model_dump()
        detail = product_detail(product)
        options = []
        for option in detail.options:
            available_slots = [
                slot
                for slot in option.slots
                if slot.status == "AVAILABLE"
                and slot.capacity_remaining > 0
                and slot.starts_at > datetime.now(UTC)
            ][:3]
            options.append(option.model_copy(update={"slots": available_slots}))
        actions = [
            AssistantAction(
                type="CHECK_AVAILABILITY",
                experience_id=product["id"],
                label="Check availability",
            )
        ]
        first_option = next((option for option in options if option.prices), None)
        if first_option:
            first_slot = first_option.slots[0] if first_option.slots else None
            if first_slot or first_option.validity_type == "OPEN_DATED":
                actions.append(
                    AssistantAction(
                        type="ADD_TO_CART",
                        experience_id=product["id"],
                        option_id=first_option.id,
                        slot_id=first_slot.id if first_slot else None,
                        label="Add to cart",
                    )
                )
        availability = (
            "AVAILABLE"
            if any(option.slots or option.validity_type == "OPEN_DATED" for option in options)
            else "SOLD_OUT"
        )
        return AssistantProduct(
            **{
                **card,
                "experience_id": product["id"],
                "reason": reason,
                "availability": availability,
                "options": options,
                "actions": actions,
            }
        )
