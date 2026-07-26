from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.api.schemas import (
    CartItemRequest,
    CartItemView,
    CartView,
    Participant,
    PriceView,
)
from app.catalog.service import get_product
from app.common.errors import ApiError
from app.common.models import (
    AvailabilitySlot,
    Cart,
    CartItem,
    Experience,
    ExperienceOption,
    IdempotencyRecord,
    OptionPrice,
)
from app.common.persistence import (
    database_mode,
    ensure_session,
    lock_idempotency,
    require_session_factory,
)
from app.common.resolution import resolve_experience_text
from app.common.store import DemoStore, store


class CartService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data

    async def get_cart(self, session_id: str, locale: str) -> CartView:
        if not database_mode():
            return self._demo_get_cart(session_id)
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            cart = await self._active_cart(db, shopping_session.id)
            if cart is None:
                cart = Cart(
                    session_id=shopping_session.id,
                    currency=shopping_session.currency,
                    status="ACTIVE",
                    version=1,
                )
                db.add(cart)
                await db.flush()
            return await self._db_view(db, cart, locale)

    async def add_item(
        self,
        session_id: str,
        request: CartItemRequest,
        idempotency_key: str,
        locale: str,
    ) -> CartView:
        if not database_mode():
            return self._demo_add_item(session_id, request, idempotency_key)
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            await lock_idempotency(
                db, shopping_session.id, "cart-add", idempotency_key
            )
            repeated = await self._idempotent_response(
                db, shopping_session.id, "cart-add", idempotency_key
            )
            if repeated:
                return repeated

            experience = await db.scalar(
                select(Experience).where(
                    Experience.id == request.experience_id,
                    Experience.status == "PUBLISHED",
                )
            )
            option = await db.scalar(
                select(ExperienceOption).where(
                    ExperienceOption.id == request.option_id,
                    ExperienceOption.experience_id == request.experience_id,
                    ExperienceOption.active.is_(True),
                )
            )
            if experience is None or option is None:
                raise ApiError(
                    404,
                    "Option not found",
                    "The selected option is unavailable",
                    "option-not-found",
                )
            quantity = sum(participant.count for participant in request.participants)
            if quantity < 1 or quantity > option.max_party_size:
                raise ApiError(
                    422,
                    "Invalid party",
                    f"Party size must be between 1 and {option.max_party_size}",
                    "invalid-party",
                )

            prices = {
                price.participant_type: price
                for price in (
                    await db.scalars(
                        select(OptionPrice).where(OptionPrice.option_id == option.id)
                    )
                ).all()
            }
            selected_prices: list[dict[str, Any]] = []
            quoted_total = 0.0
            for participant in request.participants:
                price = prices.get(participant.type)
                if price is None:
                    raise ApiError(
                        422,
                        "Participant not supported",
                        f"No price exists for {participant.type}",
                        "participant-not-supported",
                    )
                if participant.age is not None:
                    if price.minimum_age is not None and participant.age < price.minimum_age:
                        raise ApiError(
                            422,
                            "Invalid age",
                            "Participant age does not match ticket",
                            "age",
                        )
                    if price.maximum_age is not None and participant.age > price.maximum_age:
                        raise ApiError(
                            422,
                            "Invalid age",
                            "Participant age does not match ticket",
                            "age",
                        )
                price_payload = {
                    "participant_type": price.participant_type,
                    "currency": price.currency,
                    "amount": float(price.amount),
                    "minimum_age": price.minimum_age,
                    "maximum_age": price.maximum_age,
                }
                selected_prices.append(price_payload)
                quoted_total += float(price.amount) * participant.count

            slot = None
            if option.validity_type != "OPEN_DATED":
                slot_query = select(AvailabilitySlot).where(
                    AvailabilitySlot.option_id == option.id,
                    AvailabilitySlot.status == "AVAILABLE",
                    AvailabilitySlot.capacity_remaining >= quantity,
                    AvailabilitySlot.starts_at > datetime.now(UTC),
                )
                if request.slot_id:
                    slot_query = slot_query.where(
                        AvailabilitySlot.id == request.slot_id
                    )
                else:
                    slot_query = slot_query.order_by(
                        AvailabilitySlot.starts_at
                    ).limit(1)
                slot = await db.scalar(slot_query.with_for_update())
                if slot is None:
                    raise ApiError(
                        409,
                        "Slot unavailable",
                        "The selected slot no longer has capacity",
                        "slot-unavailable",
                    )

            cart = await self._active_cart(db, shopping_session.id)
            if cart is None:
                cart = Cart(
                    session_id=shopping_session.id,
                    currency=selected_prices[0]["currency"],
                    status="ACTIVE",
                    version=1,
                )
                db.add(cart)
                await db.flush()
            if cart.currency != selected_prices[0]["currency"]:
                raise ApiError(
                    409,
                    "Mixed currency",
                    "All cart items must use one currency",
                    "mixed-currency",
                )
            db.add(
                CartItem(
                    cart_id=cart.id,
                    experience_id=experience.id,
                    option_id=option.id,
                    slot_id=slot.id if slot else None,
                    participants=[
                        participant.model_dump() for participant in request.participants
                    ],
                    unit_prices=selected_prices,
                    quantity=quantity,
                    quoted_total=quoted_total,
                    quote_expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
            )
            cart.version += 1
            await db.flush()
            response = await self._db_view(db, cart, locale)
            db.add(
                IdempotencyRecord(
                    session_id=shopping_session.id,
                    operation="cart-add",
                    idempotency_key=idempotency_key,
                    response=response.model_dump(mode="json"),
                )
            )
            return response

    async def remove_item(
        self,
        session_id: str,
        item_id: UUID,
        idempotency_key: str,
        locale: str,
    ) -> CartView:
        if not database_mode():
            return self._demo_remove_item(session_id, item_id, idempotency_key)
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            await lock_idempotency(
                db, shopping_session.id, "cart-remove", idempotency_key
            )
            repeated = await self._idempotent_response(
                db, shopping_session.id, "cart-remove", idempotency_key
            )
            if repeated:
                return repeated
            cart = await self._active_cart(db, shopping_session.id)
            if cart is None:
                raise ApiError(
                    404,
                    "Cart not found",
                    "No active cart exists",
                    "cart-not-found",
                )
            item = await db.scalar(
                select(CartItem).where(
                    CartItem.id == item_id, CartItem.cart_id == cart.id
                )
            )
            if item is None:
                raise ApiError(
                    404,
                    "Item not found",
                    "The cart item does not exist",
                    "item-not-found",
                )
            await db.delete(item)
            cart.version += 1
            await db.flush()
            response = await self._db_view(db, cart, locale)
            db.add(
                IdempotencyRecord(
                    session_id=shopping_session.id,
                    operation="cart-remove",
                    idempotency_key=idempotency_key,
                    response=response.model_dump(mode="json"),
                )
            )
            return response

    async def validate(self, session_id: str, locale: str) -> CartView:
        if not database_mode():
            return self._demo_validate(session_id)
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            _cart, view = await self.validate_db(db, shopping_session.id, locale=locale)
            return view

    async def validate_db(
        self, db, shopping_session_id: UUID, *, locale: str, lock_slots: bool = False
    ) -> tuple[Cart, CartView]:
        """`locale` is required and keyword-only, deliberately.

        The previous signature defaulted it to English, and the checkout path
        simply did not pass it: a shopper read a Vietnamese cart and then paid
        against an English one, which is precisely the moment "is this what I
        chose?" must not arise. A default here cannot distinguish "English was
        requested" from "nobody said", so it is gone, and a future call site
        that forgets is a type error rather than a quiet mistranslation.
        """
        cart = await self._active_cart(db, shopping_session_id)
        if cart is None:
            raise ApiError(
                409, "Empty cart", "Add an experience before checkout", "empty-cart"
            )
        items = (
            await db.scalars(select(CartItem).where(CartItem.cart_id == cart.id))
        ).all()
        if not items:
            raise ApiError(
                409, "Empty cart", "Add an experience before checkout", "empty-cart"
            )
        now = datetime.now(UTC)
        for item in items:
            if item.quote_expires_at <= now:
                raise ApiError(
                    409,
                    "Quote expired",
                    "Refresh the cart before checkout",
                    "quote-expired",
                )
            if item.slot_id:
                statement = select(AvailabilitySlot).where(
                    AvailabilitySlot.id == item.slot_id
                )
                if lock_slots:
                    statement = statement.with_for_update()
                slot = await db.scalar(statement)
                if (
                    slot is None
                    or slot.status != "AVAILABLE"
                    or slot.capacity_remaining < item.quantity
                ):
                    raise ApiError(
                        409,
                        "Cart changed",
                        "A selected experience is no longer available",
                        "cart-revalidation",
                    )
        return cart, await self._db_view(db, cart, locale)

    async def _active_cart(self, db, shopping_session_id: UUID) -> Cart | None:
        return await db.scalar(
            select(Cart)
            .where(
                Cart.session_id == shopping_session_id,
                Cart.status == "ACTIVE",
            )
            .order_by(Cart.created_at.desc())
            .limit(1)
        )

    async def _idempotent_response(
        self,
        db,
        shopping_session_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> CartView | None:
        record = await db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.session_id == shopping_session_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        return CartView.model_validate(record.response) if record else None

    async def _db_view(self, db, cart: Cart, locale: str) -> CartView:
        rows = await db.execute(
            select(CartItem, Experience, ExperienceOption.name)
            .join(Experience, Experience.id == CartItem.experience_id)
            .join(ExperienceOption, ExperienceOption.id == CartItem.option_id)
            .where(CartItem.cart_id == cart.id)
            .order_by(CartItem.created_at)
        )
        rows_all = rows.all()
        # Through the shared resolver, not a join on `Experience.title`. A cart
        # that names products in the source language while the page that filled
        # it named them in the shopper's own reads as a different product, and
        # "is this the thing I chose?" is the worst question to raise at the
        # moment of payment.
        titles = await resolve_experience_text(
            db, [experience for _, experience, _ in rows_all], locale
        )
        items = [
            CartItemView(
                id=item.id,
                experience_id=item.experience_id,
                experience_title=titles[experience.id]["title"].value,
                option_id=item.option_id,
                option_name=option_name,
                slot_id=item.slot_id,
                starts_at=(
                    await db.scalar(
                        select(AvailabilitySlot.starts_at).where(
                            AvailabilitySlot.id == item.slot_id
                        )
                    )
                    if item.slot_id
                    else None
                ),
                participants=[
                    Participant.model_validate(participant)
                    for participant in item.participants
                ],
                unit_prices=[
                    PriceView.model_validate(price) for price in item.unit_prices
                ],
                quantity=item.quantity,
                quoted_total=float(item.quoted_total),
            )
            for item, experience, option_name in rows_all
        ]
        total = sum(item.quoted_total for item in items)
        return CartView(
            id=cart.id,
            currency=cart.currency,
            items=items,
            subtotal=total,
            total=total,
        )

    def _demo_get_cart(self, session_id: str) -> CartView:
        cart = self.data.carts.get(session_id)
        if not cart:
            cart = {
                "id": uuid4(),
                "currency": "VND",
                "status": "ACTIVE",
                "version": 1,
                "items": [],
            }
            self.data.carts[session_id] = cart
        return self._view(cart)

    def _demo_add_item(
        self, session_id: str, request: CartItemRequest, idempotency_key: str
    ) -> CartView:
        cache_key = (session_id, "cart-add", idempotency_key)
        if cache_key in self.data.idempotency:
            return self.data.idempotency[cache_key]
        product = get_product(request.experience_id, self.data)
        option = next(
            (
                item
                for item in product["options"]
                if item["id"] == request.option_id and item["active"]
            ),
            None,
        )
        if not option:
            raise ApiError(
                404, "Option not found", "The selected option is unavailable", "option-not-found"
            )
        quantity = sum(participant.count for participant in request.participants)
        if quantity < 1 or quantity > option["max_party_size"]:
            raise ApiError(
                422,
                "Invalid party",
                f"Party size must be between 1 and {option['max_party_size']}",
                "invalid-party",
            )
        slot = None
        if option["validity_type"] != "OPEN_DATED":
            slot = (
                next((item for item in option["slots"] if item["id"] == request.slot_id), None)
                if request.slot_id
                else next(
                    (
                        item
                        for item in option["slots"]
                        if item["status"] == "AVAILABLE"
                        and item["capacity_remaining"] >= quantity
                        and item["starts_at"] > datetime.now(UTC)
                    ),
                    None,
                )
            )
            if (
                not slot
                or slot["status"] != "AVAILABLE"
                or slot["capacity_remaining"] < quantity
                or slot["starts_at"] <= datetime.now(UTC)
            ):
                raise ApiError(
                    409,
                    "Slot unavailable",
                    "The selected slot no longer has capacity",
                    "slot-unavailable",
                )
        prices = {price["participant_type"]: price for price in option["prices"]}
        selected_prices: list[dict[str, Any]] = []
        quoted_total = 0
        for participant in request.participants:
            price = prices.get(participant.type)
            if price is None:
                raise ApiError(
                    422,
                    "Participant not supported",
                    f"No price exists for {participant.type}",
                    "participant-not-supported",
                )
            if participant.age is not None:
                if price["minimum_age"] is not None and participant.age < price["minimum_age"]:
                    raise ApiError(
                        422, "Invalid age", "Participant age does not match ticket", "age"
                    )
                if price["maximum_age"] is not None and participant.age > price["maximum_age"]:
                    raise ApiError(
                        422, "Invalid age", "Participant age does not match ticket", "age"
                    )
            selected_prices.append(price)
            quoted_total += price["amount"] * participant.count
        currency = selected_prices[0]["currency"]
        cart = self.data.carts.get(session_id)
        if not cart:
            self._demo_get_cart(session_id)
            cart = self.data.carts[session_id]
        if cart["items"] and cart["currency"] != currency:
            raise ApiError(
                409, "Mixed currency", "All cart items must use one currency", "mixed-currency"
            )
        cart["currency"] = currency
        cart["items"].append(
            {
                "id": uuid4(),
                "experience_id": product["id"],
                "experience_title": product["title"],
                "option_id": option["id"],
                "option_name": option["name"],
                "slot_id": slot["id"] if slot else None,
                "starts_at": slot["starts_at"] if slot else None,
                "participants": [participant.model_dump() for participant in request.participants],
                "unit_prices": selected_prices,
                "quantity": quantity,
                "quoted_total": float(quoted_total),
                "quote_expires_at": datetime.now(UTC) + timedelta(minutes=15),
            }
        )
        cart["version"] += 1
        response = self._view(cart)
        self.data.idempotency[cache_key] = response
        return response

    def _demo_remove_item(
        self, session_id: str, item_id: UUID, idempotency_key: str
    ) -> CartView:
        cache_key = (session_id, "cart-remove", idempotency_key)
        if cache_key in self.data.idempotency:
            return self.data.idempotency[cache_key]
        cart = self.data.carts.get(session_id)
        if not cart:
            raise ApiError(404, "Cart not found", "No active cart exists", "cart-not-found")
        before = len(cart["items"])
        cart["items"] = [item for item in cart["items"] if item["id"] != item_id]
        if len(cart["items"]) == before:
            raise ApiError(404, "Item not found", "The cart item does not exist", "item-not-found")
        cart["version"] += 1
        response = self._view(cart)
        self.data.idempotency[cache_key] = response
        return response

    def _demo_validate(self, session_id: str) -> CartView:
        cart = self.data.carts.get(session_id)
        if not cart or not cart["items"]:
            raise ApiError(409, "Empty cart", "Add an experience before checkout", "empty-cart")
        for item in cart["items"]:
            product = get_product(item["experience_id"], self.data)
            option = next(opt for opt in product["options"] if opt["id"] == item["option_id"])
            if item["slot_id"]:
                slot = next(
                    (
                        candidate
                        for candidate in option["slots"]
                        if candidate["id"] == item["slot_id"]
                    ),
                    None,
                )
                if (
                    not slot
                    or slot["status"] != "AVAILABLE"
                    or slot["capacity_remaining"] < item["quantity"]
                ):
                    raise ApiError(
                        409,
                        "Cart changed",
                        f"{product['title']} is no longer available",
                        "cart-revalidation",
                    )
        return self._view(cart)

    @staticmethod
    def _view(cart: dict[str, Any]) -> CartView:
        items = [
            CartItemView(
                id=item["id"],
                experience_id=item["experience_id"],
                experience_title=item["experience_title"],
                option_id=item["option_id"],
                option_name=item["option_name"],
                slot_id=item["slot_id"],
                starts_at=item["starts_at"],
                participants=[Participant(**participant) for participant in item["participants"]],
                unit_prices=[PriceView(**price) for price in item["unit_prices"]],
                quantity=item["quantity"],
                quoted_total=item["quoted_total"],
            )
            for item in cart["items"]
        ]
        total = sum(item.quoted_total for item in items)
        return CartView(
            id=cart["id"],
            currency=cart["currency"],
            items=items,
            subtotal=total,
            total=total,
        )
