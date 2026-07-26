import logging
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
from app.assistant.provider import (
    AIProvider,
    ToolPlan,
    build_ai_provider,
    carries_injected_channel,
    names_unoffered_id,
)
from app.bookings.service import BookingService
from app.cart.service import CartService
from app.catalog.service import get_product_async, product_card, product_detail
from app.common.config import get_settings
from app.common.errors import ApiError
from app.common.llm_cost import BudgetExceeded
from app.common.locales import DEFAULT_LOCALE
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


def _offering(item: Any) -> dict[str, Any]:
    """One catalogue result, described so the agent can judge it and refer back
    to it by id."""
    return {
        "experience_id": str(item.id),
        "title": item.title,
        "destination": item.destination,
        "category": item.category,
        "short_description": item.short_description,
        "price": item.price,
        "currency": item.currency,
        "rating": item.rating,
        "duration_minutes": getattr(item, "duration_minutes", None),
        "indoor_outdoor": getattr(item, "indoor_outdoor", None),
        "family_friendly": getattr(item, "family_friendly", None),
        "availability": getattr(item, "availability", None),
        "why_ranked": item.reason,
    }


class AssistantService:
    def __init__(self, data: DemoStore = store, ai_provider: AIProvider | None = None) -> None:
        self.data = data
        self.ai = ai_provider or build_ai_provider()
        self.search = SearchService(data, self.ai)
        self.carts = CartService(data)
        self.bookings = BookingService(data)
        self.recommendations = RecommendationService(data)
        self.settings = get_settings()

    async def create(self, session_id: str, request: ConversationCreate) -> dict[str, Any]:
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

    async def get(self, conversation_id: UUID, session_id: str) -> dict[str, Any]:
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
            conversation["messages"].append({"role": "user", "content": user_message})
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

    @staticmethod
    def _locale(conversation: dict[str, Any]) -> str:
        """The locale this conversation is being held in.

        Carried on the conversation rather than passed down every call. The
        agent path fans out through tools, streaming and rendering, and a
        parameter that eight call sites have to remember to forward is one
        that a ninth will not - which is precisely how the assistant, the
        primary guided-shopping path, ended up searching English documents
        while the storefront around it had been locale-aware for a release.
        """
        value = conversation.get("state", {}).get("locale")
        return value if isinstance(value, str) else DEFAULT_LOCALE

    async def respond(
        self,
        conversation_id: UUID,
        session_id: str,
        request: MessageRequest,
        locale: str = DEFAULT_LOCALE,
    ) -> AssistantResponse:
        conversation = await self.get(conversation_id, session_id)
        conversation["state"]["locale"] = locale
        message_count = await self._conversation_message_count(conversation_id)
        # Which turn this is, so a checkout summary and the yes that answers it
        # can be told apart from both happening inside one agent turn.
        conversation["state"]["turn_marker"] = message_count
        if message_count // 2 >= self.settings.assistant_max_session_turns:
            raise ApiError(
                429,
                "Conversation limit reached",
                "Start a new conversation to continue.",
                "assistant-turn-limit",
            )
        self._merge_context(conversation, request.context)
        # Let the agent work the request with the tools first. It only falls
        # through to the single-tool path when reasoning is unavailable, which is
        # what the budget breaker and demo mode rely on.
        answer = await self._run_agent(conversation, session_id, request.message)
        if answer is not None:
            await self._save_turn(conversation, user_message=request.message, response=answer)
            return answer

        plan: ToolPlan | None = None
        try:
            plan = await self.ai.plan_action(request.message, conversation["state"])
        except Exception:
            logger.exception("Assistant action planning failed")
            plan = None

        # The agent decides and fills its own arguments. There is no keyword
        # fallback: choosing the tool from English words meant a shopper writing
        # in any other language could reach no tool at all, and an English one
        # could have a checkout started because a word appeared in a sentence
        # that was never a request to act.
        tool = plan.tool if plan else None
        # The agent names the offering by an id a tool returned. Empty means
        # "the one they are looking at", which is the first of the last results.
        referent = (plan.text("experience_id") if plan else None) or ""
        if tool == "confirm_simulated_checkout" and not self._confirmation_allowed(
            conversation, plan
        ):
            # Mid-checkout without an unambiguous yes: show the summary again
            # rather than book. Nothing pending: keep the tool so the shopper is
            # told there is nothing to confirm, instead of quietly searching for
            # whatever word they used.
            if conversation["state"].get("pending_action") == "CONFIRM_CHECKOUT":
                tool = "prepare_checkout"

        response: AssistantResponse
        if tool == "confirm_simulated_checkout":
            response = await self._confirm_checkout(conversation, session_id)
        elif tool == "prepare_checkout":
            response = await self._prepare_checkout(conversation, session_id)
        elif tool == "add_to_cart":
            response = await self._add_selected_result(conversation, session_id, referent)
        elif tool == "check_availability":
            response = await self._availability(conversation, referent)
        elif tool == "compare_experiences":
            response = await self._compare(conversation)
        elif tool == "get_recommendations":
            response = await self._recommend(conversation, session_id, referent)
        else:
            response = await self._search(
                conversation, (plan.text("query") if plan else None) or request.message
            )
            if plan is None:
                # No plan means no model, and without the model the only thing
                # left is to search for the text as written. A shopper who asked
                # to add something to their cart would otherwise be handed
                # search results as though that were the answer, with nothing
                # anywhere saying their request had not been carried out. Say so.
                response.degraded = True

        await self._save_turn(
            conversation,
            user_message=request.message,
            response=response,
        )
        return response

    @staticmethod
    def _confirmation_allowed(conversation: dict[str, Any], plan: ToolPlan | None) -> bool:
        """A booking needs the shopper's explicit go-ahead, in their own language.

        This used to require the message to be one of four English phrases, so a
        shopper writing `xac nhan` could never complete a booking at all.
        Whether someone said yes is language understanding, and the agent does
        that. What is not delegated is the state, and there are three parts to
        it, because two of them were not enough.

        A summary must be pending. It must have been sent in an *earlier* turn:
        the agent may call several tools in one turn, so it could call
        `prepare_checkout` and then confirm it, and the yes it attested to would
        be a yes to a total the shopper had not been shown yet. Requiring the
        shopper to have replied since means there is a real message from them
        that the attestation can be about - which also puts catalogue text,
        which cannot make the shopper send another message, out of reach.

        And the agent must attest that their latest message was an unambiguous
        confirmation rather than a question about the price.
        """
        state = conversation["state"]
        if state.get("pending_action") != "CONFIRM_CHECKOUT":
            return False
        shown_at = state.get("checkout_shown_at")
        if not isinstance(shown_at, int) or state.get("turn_marker", 0) <= shown_at:
            return False
        return bool(plan and plan.flag("shopper_confirmed"))

    @staticmethod
    def _merge_context(conversation: dict[str, Any], context: AssistantContext | None) -> None:
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
            state["cart_experience_ids"] = [str(item) for item in context.cart_experience_ids]
        if context.focused_experience_id:
            focused = str(context.focused_experience_id)
            state["focused_experience_id"] = focused
            # A shopper who opened the assistant from a card means that card.
            state["last_result_ids"] = [
                focused,
                *[item for item in state.get("last_result_ids", []) if item != focused],
            ]

    async def _run_agent(
        self, conversation: dict[str, Any], session_id: str, message: str
    ) -> AssistantResponse | None:
        """Give the agent the tools and render the answer it curates."""
        runner = getattr(self.ai, "run_agent", None)
        if runner is None:
            return None

        async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await self._execute_tool(conversation, session_id, name, arguments)
            except ApiError as error:
                # The agent can recover from a refusal; it should see why.
                return {"error": error.args[1], "detail": error.args[2]}
            # Everything a tool has handed the agent is something it may now act
            # on. Without this the agent could find an offering and then be told
            # it does not exist when it tried to add it to the cart, because the
            # only addressable results were the ones a previous turn rendered.
            offered = conversation["state"].setdefault("offered_ids", [])
            for item in result.get("items", []) or []:
                identifier = str(item.get("experience_id", "")) if isinstance(item, dict) else ""
                if identifier and identifier not in offered:
                    offered.append(identifier)
            return result

        try:
            answer = await runner(message, conversation["state"], execute)
        except BudgetExceeded:
            logger.info("Agent reasoning skipped: daily budget reached")
            return None
        except Exception:
            logger.exception("Agent reasoning failed")
            return None
        if answer is None:
            return None
        if answer.declined:
            # The agent's tools have already run by the time its final answer is
            # refused. Falling through to the deterministic path would replay
            # them - the same experience added to the cart twice - so this turn
            # ends here, saying plainly that the reply could not be shown.
            logger.warning("Agent answer declined; not retrying the turn")
            return AssistantResponse(
                message=(
                    "I could not put together a reply I can stand behind. Please ask me again."
                ),
                message_code="assistant.msg.unavailable",
                degraded=True,
            )

        products: list[AssistantProduct] = []
        for selection in answer.selections:
            try:
                product = await get_product_async(
                    UUID(selection.experience_id),
                    self.data,
                    locale=self._locale(conversation),
                )
            except (ApiError, ValueError):
                logger.warning("Agent selected an unknown offering %s", selection.experience_id)
                continue
            products.append(
                self._commerce_product(product, selection.reason or "Matches your request.")
            )
        if products:
            conversation["state"]["last_result_ids"] = [
                str(product.experience_id) for product in products
            ]
        return AssistantResponse(
            message=answer.message,
            products=products,
            clarification=answer.clarification,
            citations=[
                {
                    "experience_id": str(product.experience_id),
                    "fields": ["title", "price", "availability"],
                }
                for product in products
            ],
        )

    async def _execute_tool(
        self,
        conversation: dict[str, Any],
        session_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Run one tool and return facts the agent can reason over.

        Every offering carries its experience_id so the agent can refer back to
        it in final_answer and the application can render it.
        """
        state = conversation["state"]
        filters = SearchFilters.model_validate(state.get("filters", {}))
        party = [Participant.model_validate(item) for item in state.get("party", [])]

        if name == "search_experiences":
            exclude = [
                str(term).strip()
                for term in arguments.get("exclude", []) or []
                if str(term).strip()
            ]
            if exclude:
                filters.exclusions = list(dict.fromkeys([*filters.exclusions, *exclude]))
            destination = arguments.get("destination")
            if isinstance(destination, str) and destination.strip():
                filters.destination = destination.strip()
            budget = arguments.get("max_total_price")
            if isinstance(budget, int | float):
                filters.max_total_price = float(budget)
            limit = arguments.get("limit")
            result = await self.search.search(
                SearchRequest(
                    query=str(arguments.get("query") or ""),
                    filters=filters,
                    party=party,
                    page_size=int(limit) if isinstance(limit, int) and limit > 0 else 8,
                    locale=self._locale(conversation),
                    relax_order=[
                        str(code).strip()
                        for code in arguments.get("relax_order", []) or []
                        if str(code).strip()
                    ],
                )
            )
            return {
                "relaxed_preferences": result.relaxed_preferences,
                # What could still be given up, so the agent can offer the
                # shopper the choice instead of the search making it for them.
                "relaxation_candidates": result.relaxation_candidates,
                "items": [_offering(item) for item in result.items],
            }

        if name == "get_recommendations":
            current = await self._selected_product(
                conversation, str(arguments.get("experience_id") or "")
            )
            result = await self.recommendations.recommend(
                session_id=session_id,
                placement="complete_your_day",
                experience_id=current["id"],
                limit=4,
                filters=filters,
                party=party,
                locale=self._locale(conversation),
            )
            return {
                "anchor_experience_id": str(current["id"]),
                "items": [_offering(item) for item in result.items],
            }

        if name == "check_availability":
            product = await self._selected_product(
                conversation, str(arguments.get("experience_id") or "")
            )
            detail = product_detail(product)
            return {
                "experience_id": str(product["id"]),
                "title": product["title"],
                "options": [
                    {
                        "name": option.name,
                        "validity_type": option.validity_type,
                        "slots": [
                            {
                                "starts_at": slot.starts_at,
                                "capacity_remaining": slot.capacity_remaining,
                                "status": slot.status,
                            }
                            for slot in option.slots[:5]
                        ],
                    }
                    for option in detail.options
                ],
            }

        if name == "add_to_cart":
            response = await self._add_selected_result(
                conversation, session_id, str(arguments.get("experience_id") or "")
            )
            return {
                "outcome": response.message,
                "items": [
                    {"experience_id": str(product.experience_id), "title": product.title}
                    for product in response.products
                ],
            }

        if name == "prepare_checkout":
            response = await self._prepare_checkout(conversation, session_id)
            return {"outcome": response.message}

        if name == "confirm_simulated_checkout":
            # A booking needs a summary the shopper has seen and their own
            # unambiguous yes. Refusing unconditionally here left the agent no
            # way to ever complete a booking: it asked the shopper to confirm,
            # they did, and it was refused again.
            if not self._confirmation_allowed(conversation, ToolPlan(name, arguments)):
                return {
                    "error": "confirmation-required",
                    "detail": (
                        "Show the shopper their checkout summary and wait for an "
                        "unambiguous yes before calling this."
                    ),
                }
            response = await self._confirm_checkout(conversation, session_id)
            return {"outcome": response.message}

        return {"error": "unknown-tool", "detail": name}

    async def _search(self, conversation: dict[str, Any], message: str) -> AssistantResponse:
        filters = SearchFilters.model_validate(conversation["state"].get("filters", {}))
        result = await self.search.search(
            SearchRequest(
                query=message,
                filters=filters,
                party=conversation["state"].get("party", []),
                page_size=5,
                locale=self._locale(conversation),
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
                await get_product_async(product.id, self.data, locale=self._locale(conversation)),
                product.reason or "Matches your request.",
            )
            for product in result.items
        ]
        if not products:
            return AssistantResponse(
                message=(
                    "Nothing is bookable even after I widened your dates and dropped the "
                    "optional preferences. Only your accessibility needs and exclusions "
                    "were kept. Shall I try a different destination?"
                ),
                message_code="assistant.msg.noResults",
                clarification="Would you like to change destination or travel dates?",
                clarification_code="assistant.msg.noResults.ask",
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
        # What was relaxed travels as codes in `relaxed_preferences`, which the
        # client renders from the shopper's own dictionary. Naming it again here
        # would put untranslatable English back into the sentence.
        message_text = (
            f"I found {len(products)} grounded options. The first choices best match your request."
        )
        # The model writes in the shopper's language; this sentence cannot. So
        # the code survives only while the model does not, and the client
        # renders whichever of the two it was given.
        message_code: str | None = "assistant.msg.searchResults"
        try:
            enhanced = await self.ai.enhance_assistant(message, facts)
            # This is model prose reaching the shopper, so it answers to the
            # same rule as the agent's: no channel a tool could not have
            # produced, and no experience that is not on the page. It used to be
            # rendered verbatim, which made the "deterministic fallback" a
            # second, unguarded way for injected catalogue text to get through.
            if enhanced and not carries_injected_channel(enhanced):
                if not names_unoffered_id(enhanced, set(ids)):
                    message_text = enhanced
                    message_code = None
                else:
                    logger.warning("Enhanced prose named an offering not on the page; keeping code")
            elif enhanced:
                logger.warning(
                    "Enhanced prose carried a contact channel; keeping the coded message"
                )
        except Exception:
            logger.exception("Grounded assistant prose enhancement failed")
        return AssistantResponse(
            message=message_text,
            message_code=message_code,
            message_vars={"count": len(products)} if message_code else {},
            state_patch={"filters": conversation["state"]["filters"], "last_result_ids": ids},
            products=products,
            relaxed_preferences=result.relaxed_preferences,
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
        product_view = self._commerce_product(product, "Live demo inventory was checked.")
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

    async def _compare(self, conversation: dict[str, Any]) -> AssistantResponse:
        ids = conversation["state"].get("last_result_ids", [])[:3]
        products = [
            await get_product_async(UUID(item), self.data, locale=self._locale(conversation))
            for item in ids
        ]
        if len(products) < 2:
            return AssistantResponse(
                message="Please search for at least two experiences before asking me to compare.",
                message_code="assistant.msg.compareNeedsTwo",
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
            message_code="assistant.msg.comparison",
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
        self, conversation: dict[str, Any], session_id: str, message: str = ""
    ) -> AssistantResponse:
        # Cross-sell cannot represent a subject of its own, so a planner that
        # picks it for a request carrying one would answer a question nobody
        # asked. Search is the tool that can actually read the message.
        current = await self._selected_product(conversation)
        result = await self.recommendations.recommend(
            session_id=session_id,
            placement="complete_your_day",
            experience_id=current["id"],
            limit=4,
            locale=self._locale(conversation),
            filters=SearchFilters.model_validate(conversation["state"].get("filters", {})),
            party=[
                Participant.model_validate(item) for item in conversation["state"].get("party", [])
            ],
        )
        if not result.items:
            return AssistantResponse(
                message=(
                    "I could not find a complementary experience that is still bookable "
                    "for your dates and party. Would you like me to try another day?"
                ),
                message_code="assistant.msg.noComplement",
                clarification="Shall I look at nearby dates?",
                clarification_code="assistant.msg.noComplement.ask",
            )
        products = [
            self._commerce_product(
                await get_product_async(item.id, self.data, locale=self._locale(conversation)),
                item.reason or "Complements your current choice.",
                item.reason_code,
            )
            for item in result.items
        ]
        return AssistantResponse(
            message="These diverse options complement your current choice.",
            message_code="assistant.msg.complements",
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
            Participant.model_validate(item) for item in conversation["state"].get("party", [])
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
            self._locale(conversation),
        )
        conversation["state"]["pending_action"] = None
        party_label = ", ".join(
            f"{participant.count} {participant.type}{'s' if participant.count != 1 else ''}"
            for participant in participants
        )
        return AssistantResponse(
            message=(
                f"Added {product['title']} for {party_label} to the cart. "
                f"The simulated total is {cart.total:,.0f} {cart.currency}."
            ),
            message_code="assistant.msg.added",
            # Raw values, not a formatted sentence. Grouping separators and
            # currency placement are locale decisions, and the client already
            # makes them everywhere else on the page.
            message_vars={
                "title": product["title"],
                "total": cart.total,
                "currency": cart.currency,
            },
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
        cart = await self.carts.validate(session_id, self._locale(conversation))
        conversation["state"]["pending_action"] = "CONFIRM_CHECKOUT"
        # The turn the shopper is shown this total. A confirmation arriving in
        # the same turn cannot be an answer to it, because the summary has not
        # left the server yet.
        conversation["state"]["checkout_shown_at"] = conversation["state"].get("turn_marker")
        return AssistantResponse(
            message=(
                f"Final simulated booking total: {cart.total:,.0f} {cart.currency} "
                f"for {len(cart.items)} item(s). No real payment will be taken. "
                "Confirm to create the booking and QR voucher."
            ),
            message_code="assistant.msg.checkoutTotal",
            message_vars={
                "total": cart.total,
                "currency": cart.currency,
                "count": len(cart.items),
            },
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
                message=(
                    "I cannot book yet. Ask me to prepare checkout first so you can "
                    "review the total."
                ),
                message_code="assistant.msg.cannotBookYet",
            )
        booking = await self.bookings.confirm(
            session_id,
            idempotency_key=f"assistant-checkout-{conversation['id']}",
            locale=self._locale(conversation),
        )
        conversation["state"]["pending_action"] = None
        return AssistantResponse(
            message=(
                f"Your simulated booking {booking.booking_reference} is confirmed. "
                f"Voucher {booking.voucher.voucher_reference} is ready."
            ),
            message_code="assistant.msg.booked",
            message_vars={
                "booking": booking.booking_reference,
                "voucher": booking.voucher.voucher_reference,
            },
            state_patch={"pending_action": None, "booking_id": str(booking.id)},
            actions=[AssistantAction(type="VIEW_VOUCHER")],
        )

    async def _selected_product(
        self, conversation: dict[str, Any], reference: str = ""
    ) -> dict[str, Any]:
        """The offering an action applies to.

        Two different questions share this. With no reference it means "the one
        they are looking at", which is the first of the results on screen. With
        a reference it means that specific offering, and the agent may name
        anything a tool returned in this conversation - it can search and then
        add to the cart in a single turn, which it could not do while the only
        addressable offerings were the ones a previous turn had rendered.
        """
        state = conversation["state"]
        shown = [str(item) for item in state.get("last_result_ids", [])]
        if not reference:
            if not shown:
                raise ApiError(
                    409,
                    "No selected experience",
                    "Search for an experience before this action.",
                    "no-selection",
                )
            return await get_product_async(
                UUID(shown[0]), self.data, locale=self._locale(conversation)
            )
        reachable = {*shown, *(str(item) for item in state.get("offered_ids", []))}
        if reference not in reachable:
            raise ApiError(
                409,
                "Unknown experience",
                "That offering has not come back from a tool in this conversation. "
                "Search first, then use the experience_id the search returned.",
                "unknown-referent",
            )
        return await get_product_async(
            UUID(reference), self.data, locale=self._locale(conversation)
        )

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
