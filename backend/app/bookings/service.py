import base64
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import qrcode

from app.api.schemas import BookingView, VoucherView
from app.cart.service import CartService
from app.common.errors import ApiError
from app.common.store import DemoStore, store


def _qr_data_url(payload: str) -> str:
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class BookingService:
    def __init__(self, data: DemoStore = store) -> None:
        self.data = data
        self.carts = CartService(data)

    def confirm(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        customer_details: dict[str, str] | None = None,
    ) -> BookingView:
        cache_key = (session_id, "checkout", idempotency_key)
        if cache_key in self.data.idempotency:
            return self.data.idempotency[cache_key]
        cart_view = self.carts.validate(session_id)
        cart = self.data.carts[session_id]
        if cart["status"] != "ACTIVE":
            raise ApiError(409, "Cart closed", "This cart was already checked out", "cart-closed")
        now = datetime.now(UTC)
        booking_id = uuid4()
        booking_reference = f"VN-{now:%y%m%d}-{str(booking_id)[:8].upper()}"
        voucher_id = uuid4()
        voucher_reference = f"V-{str(voucher_id)[:10].upper()}"
        qr_payload = (
            f"tourism-poc://redeem/{voucher_reference}"
            f"?booking={booking_reference}&total={cart_view.total:.2f}"
        )
        valid_from = min(
            (item.starts_at for item in cart_view.items if item.starts_at),
            default=now,
        )
        valid_until = max(
            (item.starts_at for item in cart_view.items if item.starts_at),
            default=now + timedelta(days=30),
        ) + timedelta(days=1)
        voucher = {
            "id": voucher_id,
            "voucher_reference": voucher_reference,
            "qr_payload": qr_payload,
            "qr_image_data_url": _qr_data_url(qr_payload),
            "redemption_instructions": (
                "This is a simulated voucher. Present the QR code at the meeting point "
                "15 minutes before the selected time."
            ),
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
        booking: dict[str, Any] = {
            "id": booking_id,
            "booking_reference": booking_reference,
            "status": "CONFIRMED",
            "currency": cart_view.currency,
            "total": cart_view.total,
            "customer_details": customer_details or {},
            "confirmed_at": now,
            "voucher": voucher,
        }
        for item in cart["items"]:
            if item["slot_id"]:
                product = self.data.products[item["experience_id"]]
                option = next(opt for opt in product["options"] if opt["id"] == item["option_id"])
                slot = next(slot for slot in option["slots"] if slot["id"] == item["slot_id"])
                slot["capacity_remaining"] -= item["quantity"]
        cart["status"] = "CHECKED_OUT"
        cart["version"] += 1
        self.data.bookings[booking_id] = booking
        response = self._view(booking)
        self.data.idempotency[cache_key] = response
        return response

    def get(self, booking_id: UUID) -> BookingView:
        booking = self.data.bookings.get(booking_id)
        if not booking:
            raise ApiError(404, "Booking not found", "The booking does not exist", "not-found")
        return self._view(booking)

    def voucher(self, booking_id: UUID) -> VoucherView:
        return self.get(booking_id).voucher

    @staticmethod
    def _view(booking: dict[str, Any]) -> BookingView:
        return BookingView(
            id=booking["id"],
            booking_reference=booking["booking_reference"],
            status=booking["status"],
            currency=booking["currency"],
            total=booking["total"],
            confirmed_at=booking["confirmed_at"],
            voucher=VoucherView(**booking["voucher"]),
        )
