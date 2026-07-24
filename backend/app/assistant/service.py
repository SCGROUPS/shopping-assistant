from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.api.schemas import (
    AssistantAction,
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
from app.catalog.service import get_product, product_card, product_detail
from app.common.config import get_settings
from app.common.errors import ApiError
from app.common.store import DemoStore, store
from app.recommendations.service import RecommendationService
from app.search.service import SearchService


class AssistantService:
    def __init__(self, data: DemoStore = store, ai_provider: AIProvider | None = None) -> None:
        self.data = data
        self.ai = ai_provider or build_ai_provider()
        self.search = SearchService(data, self.ai)
        self.carts = CartService(data)
        self.bookings = BookingService(data)
        self.recommendations = RecommendationService(data)
        self.settings = get_settings()

    def create(self, session_id: str, request: ConversationCreate) -> dict[str, Any]:
        conversation_id = uuid4()
        state = {
            "filters": request.filters.model_dump(mode="json"),
            "last_result_ids": [str(item) for item in request.result_ids],
            "selected_experience_ids": [],
            "party": [],
            "hard_constraints": [],
            "soft_preferences": [],
            "pending_action": None,
        }
        conversation = {
            "id": conversation_id,
            "session_id": session_id,
            "status": "ACTIVE",
            "state": state,
            "summary": request.query or "",
            "messages": [],
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        self.data.conversations[conversation_id] = conversation
        return conversation

    def get(self, conversation_id: UUID, session_id: str) -> dict[str, Any]:
        conversation = self.data.conversations.get(conversation_id)
        if not conversation or conversation["session_id"] != session_id:
            raise ApiError(
                404, "Conversation not found", "The conversation does not exist", "not-found"
            )
        return conversation

    async def respond(
        self, conversation_id: UUID, session_id: str, request: MessageRequest
    ) -> AssistantResponse:
        conversation = self.get(conversation_id, session_id)
        if len(conversation["messages"]) // 2 >= self.settings.assistant_max_session_turns:
            raise ApiError(
                429,
                "Conversation limit reached",
                "Start a new conversation to continue.",
                "assistant-turn-limit",
            )
        conversation["messages"].append({"role": "user", "content": request.message})
        lowered = request.message.casefold().strip()
        planned_tool = None
        try:
            planned_tool = await self.ai.plan_action(request.message, conversation["state"])
        except Exception:
            planned_tool = None
        response: AssistantResponse

        if lowered in {"confirm", "confirm booking", "yes, confirm", "confirm checkout"}:
            response = self._confirm_checkout(conversation, session_id)
        elif (
            any(term in lowered for term in ("checkout", "pay", "book now"))
            or planned_tool == "prepare_checkout"
        ):
            response = self._prepare_checkout(conversation, session_id)
        elif ("add" in lowered and "cart" in lowered) or planned_tool == "add_to_cart":
            response = self._add_first_result(conversation, session_id)
        elif (
            "availability" in lowered
            or "available" in lowered
            or planned_tool == "check_availability"
        ):
            response = self._availability(conversation)
        elif "compare" in lowered or planned_tool == "compare_experiences":
            response = self._compare(conversation)
        elif (
            any(term in lowered for term in ("similar", "also like", "complete my day"))
            or planned_tool == "get_recommendations"
        ):
            response = self._recommend(conversation, session_id)
        else:
            response = await self._search(conversation, request.message)

        conversation["messages"].append(
            {
                "role": "assistant",
                "content": response.message,
                "structured_payload": response.model_dump(mode="json"),
            }
        )
        conversation["updated_at"] = datetime.now(UTC)
        return response

    async def _search(self, conversation: dict[str, Any], message: str) -> AssistantResponse:
        filters = SearchFilters.model_validate(conversation["state"].get("filters", {}))
        result = await self.search.search(
            SearchRequest(query=message, filters=filters, page_size=5)
        )
        ids = [str(product.id) for product in result.items]
        conversation["state"]["last_result_ids"] = ids
        conversation["state"]["filters"] = result.effective_filters.model_dump(mode="json")
        products = [
            self._commerce_product(
                get_product(product.id, self.data),
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
            pass
        return AssistantResponse(
            message=message_text,
            state_patch={"filters": conversation["state"]["filters"], "last_result_ids": ids},
            products=products,
            filter_updates=[
                {"field": key, "value": value, "source": "inferred"}
                for key, value in result.effective_filters.model_dump(mode="json").items()
                if value not in (None, [], "")
            ],
            actions=[action for product in products[:3] for action in product.actions],
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

    def _availability(self, conversation: dict[str, Any]) -> AssistantResponse:
        product = self._selected_product(conversation)
        detail = product_detail(product)
        slots = detail.options[0].slots[:3]
        if not slots:
            return AssistantResponse(message=f"No upcoming slots are available for {detail.title}.")
        slot_text = ", ".join(slot.starts_at.strftime("%d %b %H:%M UTC") for slot in slots)
        return AssistantResponse(
            message=f"{detail.title} has availability at {slot_text}.",
            products=[self._commerce_product(product, "Live demo inventory was checked.")],
            actions=[
                AssistantAction(
                    type="ADD_TO_CART",
                    experience_id=detail.id,
                    option_id=detail.options[0].id,
                    slot_id=slot.id,
                    label=f"Add {slot.starts_at:%d %b %H:%M} to cart",
                )
                for slot in slots
            ],
            citations=[
                {
                    "experience_id": str(detail.id),
                    "fields": ["options.slots", "options.prices"],
                }
            ],
        )

    def _compare(self, conversation: dict[str, Any]) -> AssistantResponse:
        ids = conversation["state"].get("last_result_ids", [])[:3]
        products = [get_product(UUID(item), self.data) for item in ids]
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

    def _recommend(self, conversation: dict[str, Any], session_id: str) -> AssistantResponse:
        current = self._selected_product(conversation)
        result = self.recommendations.recommend(
            session_id=session_id,
            placement="complete_your_day",
            experience_id=current["id"],
            limit=4,
        )
        products = [
            self._commerce_product(
                get_product(item.id, self.data),
                item.reason or "Complements your current choice.",
                item.reason_code,
            )
            for item in result.items
        ]
        return AssistantResponse(
            message="These diverse options complement your current choice.",
            products=products,
            actions=[action for product in products for action in product.actions],
            citations=[
                {
                    "experience_id": str(item.id),
                    "fields": ["category", "destination", "availability"],
                }
                for item in result.items
            ],
        )

    def _add_first_result(self, conversation: dict[str, Any], session_id: str) -> AssistantResponse:
        product = self._selected_product(conversation)
        option = product["options"][0]
        slot = next(
            (
                item
                for item in option["slots"]
                if item["capacity_remaining"] > 0 and item["starts_at"] > datetime.now(UTC)
            ),
            None,
        )
        cart = self.carts.add_item(
            session_id,
            CartItemRequest(
                experience_id=product["id"],
                option_id=option["id"],
                slot_id=slot["id"] if slot else None,
                participants=[Participant(type="adult", count=1)],
            ),
            f"assistant-{conversation['id']}-{len(conversation['messages'])}",
        )
        conversation["state"]["pending_action"] = None
        return AssistantResponse(
            message=(
                f"Added {product['title']} for one adult to the cart. "
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

    def _prepare_checkout(self, conversation: dict[str, Any], session_id: str) -> AssistantResponse:
        cart = self.carts.validate(session_id)
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

    def _confirm_checkout(self, conversation: dict[str, Any], session_id: str) -> AssistantResponse:
        if conversation["state"].get("pending_action") != "CONFIRM_CHECKOUT":
            return AssistantResponse(
                message="I cannot book yet. Ask me to prepare checkout first so you can review the total."
            )
        booking = self.bookings.confirm(
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

    def _selected_product(self, conversation: dict[str, Any]) -> dict[str, Any]:
        ids = conversation["state"].get("last_result_ids", [])
        if not ids:
            raise ApiError(
                409,
                "No selected experience",
                "Search for an experience before this action.",
                "no-selection",
            )
        return get_product(UUID(ids[0]), self.data)

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
