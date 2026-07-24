from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.api.schemas import (
    CartItemRequest,
    CartItemView,
    CartView,
    Participant,
    PriceView,
)
from app.catalog.service import get_product
from app.common.errors import ApiError
from app.common.store import DemoStore, store


class CartService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data

    def get_cart(self, session_id: str) -> CartView:
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

    def add_item(self, session_id: str, request: CartItemRequest, idempotency_key: str) -> CartView:
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
            self.get_cart(session_id)
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

    def remove_item(self, session_id: str, item_id: UUID, idempotency_key: str) -> CartView:
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

    def validate(self, session_id: str) -> CartView:
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
