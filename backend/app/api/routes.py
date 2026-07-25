import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    File,
    Header,
    Query,
    Request,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.schemas import (
    BookingView,
    CartItemRequest,
    CartView,
    CheckoutConfirmRequest,
    CheckoutPrepareResponse,
    ConversationCreate,
    EventRequest,
    ExperienceDetail,
    ExperienceListResponse,
    MessageRequest,
    Participant,
    RecommendationResponse,
    SearchFilters,
    SearchRequest,
    SearchResponse,
    VoucherView,
)
from app.assistant.service import AssistantService
from app.bookings.service import BookingService
from app.cart.service import CartService
from app.catalog.service import get_product_async, product_card, product_detail
from app.common.database import database_ready
from app.common.errors import ApiError
from app.common.models import BehaviorEvent
from app.common.persistence import (
    catalog_products,
    database_mode,
    ensure_session,
    require_session_factory,
)
from app.common.store import store
from app.recommendations.service import RecommendationService
from app.search.service import SearchService

router = APIRouter(prefix="/api/v1")
search_service = SearchService()
recommendation_service = RecommendationService()
cart_service = CartService()
booking_service = BookingService()
assistant_service = AssistantService()

SessionHeader = Annotated[str, Header(alias="X-Session-ID")]
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest, session_id: SessionHeader = "demo-session"
) -> SearchResponse:
    result = await search_service.search(request)
    await _capture(
        session_id,
        EventRequest(event_type="search_submitted", query_id=result.query_id),
    )
    return result


@router.get("/experiences", response_model=ExperienceListResponse)
async def list_experiences(
    destination: str | None = None,
    category: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> ExperienceListResponse:
    products = [
        product
        for product in await catalog_products()
        if product["status"] == "PUBLISHED"
        and (not destination or product["destination"].casefold() == destination.casefold())
        and (not category or product["category"].casefold() == category.casefold())
    ]
    products.sort(key=lambda item: item["popularity_score"], reverse=True)
    return ExperienceListResponse(
        items=[product_card(product) for product in products[offset : offset + limit]],
        total=len(products),
    )


@router.get("/experiences/{experience_id}", response_model=ExperienceDetail)
async def experience_detail(
    experience_id: UUID, session_id: SessionHeader = "demo-session"
) -> ExperienceDetail:
    product = await get_product_async(experience_id)
    await _capture(
        session_id,
        EventRequest(event_type="experience_viewed", experience_id=experience_id),
    )
    return product_detail(product)


@router.get("/experiences/{experience_id}/availability")
async def availability(
    experience_id: UUID,
    visit_start: datetime | None = None,
    session_id: SessionHeader = "demo-session",
) -> dict[str, Any]:
    product = await get_product_async(experience_id)
    detail = product_detail(product, visit_start)
    await _capture(
        session_id, EventRequest(event_type="availability_checked", experience_id=experience_id)
    )
    return {
        "experience_id": experience_id,
        "timezone": "Asia/Ho_Chi_Minh",
        "options": detail.options,
    }


@router.get("/recommendations", response_model=RecommendationResponse)
async def recommendations(
    session_id: SessionHeader = "demo-session",
    placement: str = Query(default="for_you"),
    experience_id: UUID | None = None,
    destination: str | None = None,
    limit: int = Query(default=6, ge=1, le=20),
    visit_start: datetime | None = None,
    visit_end: datetime | None = None,
    travellers: int = Query(default=0, ge=0, le=20),
    max_total_price: float | None = Query(default=None, ge=0),
    currency: str | None = None,
) -> RecommendationResponse:
    filters = SearchFilters(
        visit_start=visit_start,
        visit_end=visit_end,
        max_total_price=max_total_price,
        currency=currency,
    )
    party = [Participant(type="adult", count=travellers)] if travellers else []
    return await recommendation_service.recommend(
        session_id=session_id,
        placement=placement,
        experience_id=experience_id,
        destination=destination,
        limit=limit,
        filters=filters,
        party=party,
    )


@router.post("/events", status_code=202)
async def capture_event(
    request: EventRequest, session_id: SessionHeader = "demo-session"
) -> dict[str, Any]:
    return await _capture(session_id, request)


@router.post("/conversations", status_code=201)
async def create_conversation(
    request: ConversationCreate, session_id: SessionHeader = "demo-session"
) -> dict[str, Any]:
    conversation = await assistant_service.create(session_id, request)
    return {"id": conversation["id"]}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, session_id: SessionHeader = "demo-session"
) -> dict[str, Any]:
    conversation = await assistant_service.get(conversation_id, session_id)
    return {
        "id": conversation["id"],
        "status": conversation["status"],
        "state": conversation["state"],
        "summary": conversation["summary"],
        "messages": conversation["messages"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
    }


@router.post("/conversations/{conversation_id}/messages")
async def conversation_message(
    conversation_id: UUID,
    body: MessageRequest,
    http_request: Request,
    session_id: SessionHeader = "demo-session",
    stream: bool | None = Query(default=None),
) -> Any:
    wants_stream = (
        stream
        if stream is not None
        else "text/event-stream" in http_request.headers.get("accept", "").casefold()
    )
    if not wants_stream:
        response = await assistant_service.respond(conversation_id, session_id, body)
        await _capture(session_id, EventRequest(event_type="assistant_message_sent"))
        return JSONResponse(jsonable_encoder(response))

    async def events():
        yield _sse("status", {"status": "working"})
        try:
            response = await assistant_service.respond(conversation_id, session_id, body)
            await _capture(
                session_id,
                EventRequest(event_type="assistant_message_sent"),
            )
            yield _sse("text_delta", {"delta": response.message})
            if response.products:
                yield _sse(
                    "products",
                    [item.model_dump(mode="json") for item in response.products],
                )
            if response.state_patch:
                yield _sse("state_patch", response.state_patch)
            yield _sse("completed", response.model_dump(mode="json"))
        except ApiError as exc:
            yield _sse(
                "error",
                {
                    "status": exc.status,
                    "title": exc.title,
                    "detail": exc.detail,
                    "code": exc.code,
                },
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/cart", response_model=CartView)
async def get_cart(session_id: SessionHeader = "demo-session") -> CartView:
    return await cart_service.get_cart(session_id)


@router.post("/cart/items", response_model=CartView)
async def add_cart_item(
    request: CartItemRequest,
    idempotency_key: IdempotencyHeader,
    session_id: SessionHeader = "demo-session",
) -> CartView:
    result = await cart_service.add_item(session_id, request, idempotency_key)
    await _capture(
        session_id,
        EventRequest(event_type="cart_item_added", experience_id=request.experience_id),
    )
    return result


@router.delete("/cart/items/{item_id}", response_model=CartView)
async def remove_cart_item(
    item_id: UUID,
    idempotency_key: IdempotencyHeader,
    session_id: SessionHeader = "demo-session",
) -> CartView:
    return await cart_service.remove_item(session_id, item_id, idempotency_key)


@router.post("/checkout/prepare", response_model=CheckoutPrepareResponse)
async def prepare_checkout(
    session_id: SessionHeader = "demo-session",
) -> CheckoutPrepareResponse:
    cart = await cart_service.validate(session_id)
    await _capture(session_id, EventRequest(event_type="checkout_started"))
    return CheckoutPrepareResponse(
        cart=cart,
        ready=True,
        simulated_payment_notice="POC only: no real payment will be processed.",
    )


@router.post("/checkout/confirm", response_model=BookingView)
async def confirm_checkout(
    request: CheckoutConfirmRequest,
    idempotency_key: IdempotencyHeader,
    session_id: SessionHeader = "demo-session",
) -> BookingView:
    booking = await booking_service.confirm(
        session_id,
        idempotency_key=idempotency_key,
        customer_details=request.customer_details,
    )
    await _capture(session_id, EventRequest(event_type="booking_completed"))
    return booking


@router.get("/bookings/{booking_id}", response_model=BookingView)
async def get_booking(booking_id: UUID) -> BookingView:
    return await booking_service.get(booking_id)


@router.get("/bookings/{booking_id}/voucher", response_model=VoucherView)
async def get_voucher(booking_id: UUID) -> VoucherView:
    return await booking_service.voucher(booking_id)


@router.post("/admin/imports", status_code=202)
async def import_catalog(
    file: UploadFile = File(...),
    admin_role: Annotated[str | None, Header(alias="X-Admin-Role")] = None,
) -> dict[str, Any]:
    if admin_role != "catalog_manager":
        raise ApiError(403, "Forbidden", "Catalog manager role is required", "forbidden")
    if not file.filename or not file.filename.endswith(".json"):
        raise ApiError(422, "Invalid import", "Upload a JSON catalog file", "invalid-import")
    payload = json.loads((await file.read()).decode())
    records = payload if isinstance(payload, list) else payload.get("experiences", [])
    errors = []
    imported = 0
    required = {"slug", "title", "description", "destination", "category", "options"}
    for index, record in enumerate(records):
        missing = sorted(required.difference(record))
        if missing:
            errors.append({"row": index + 1, "error": f"Missing: {', '.join(missing)}"})
            continue
        imported += 1
    return {
        "id": uuid4(),
        "status": "VALIDATED",
        "source_name": file.filename,
        "imported_count": imported,
        "errors": errors,
        "note": "Demo mode validates uploads; use `python -m app.catalog.cli` for seed upsert.",
    }


@router.get("/health")
async def api_health() -> dict[str, Any]:
    products = await catalog_products()
    return {
        "status": "ok",
        "mode": "postgresql" if database_mode() else "demo-memory",
        "catalog_size": len(products),
        "database_ready": await database_ready(),
    }


async def _capture(session_id: str, request: EventRequest) -> dict[str, Any]:
    allowed = {
        "search_submitted",
        "search_results_viewed",
        "filter_applied",
        "experience_impression",
        "experience_viewed",
        "recommendation_impression",
        "recommendation_clicked",
        "assistant_message_sent",
        "assistant_product_shown",
        "assistant_action_clicked",
        "availability_checked",
        "cart_item_added",
        "cart_item_removed",
        "checkout_started",
        "booking_completed",
    }
    if request.event_type not in allowed:
        raise ApiError(422, "Invalid event", "Unsupported event type", "invalid-event")
    safe_properties = {
        key: value
        for key, value in request.properties.items()
        if key.casefold() not in {"message", "text", "email", "phone", "name"}
    }
    if not database_mode():
        event = {
            "id": len(store.events) + 1,
            "session_id": store.session(session_id)["id"],
            "event_type": request.event_type,
            "experience_id": request.experience_id,
            "placement": request.placement,
            "query_id": request.query_id,
            "properties": safe_properties,
            "occurred_at": request.occurred_at or datetime.now(UTC),
        }
        store.events.append(event)
        if request.experience_id:
            store.event_experiences[session_id].append(
                (request.event_type, request.experience_id, event["occurred_at"])
            )
        return {"accepted": True, "event_id": event["id"]}

    factory = require_session_factory()
    async with factory() as db, db.begin():
        shopping_session = await ensure_session(db, session_id)
        event = BehaviorEvent(
            session_id=shopping_session.id,
            event_type=request.event_type,
            experience_id=request.experience_id,
            placement=request.placement,
            query_id=request.query_id,
            properties=safe_properties,
            occurred_at=request.occurred_at or datetime.now(UTC),
        )
        db.add(event)
        await db.flush()
        return {"accepted": True, "event_id": event.id}


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(payload))}\n\n"
