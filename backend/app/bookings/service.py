import base64
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import qrcode
from sqlalchemy import select

from app.api.schemas import BookingView, VoucherView
from app.cart.service import CartService
from app.common.errors import ApiError
from app.common.models import (
    AvailabilitySlot,
    Booking,
    CartItem,
    IdempotencyRecord,
    Voucher,
)
from app.common.persistence import (
    database_mode,
    ensure_session,
    lock_idempotency,
    require_session_factory,
)
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

    async def confirm(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        locale: str,
        customer_details: dict[str, str] | None = None,
    ) -> BookingView:
        if not database_mode():
            return self._demo_confirm(
                session_id,
                idempotency_key=idempotency_key,
                customer_details=customer_details,
            )
        factory = require_session_factory()
        async with factory() as db, db.begin():
            shopping_session = await ensure_session(db, session_id)
            await lock_idempotency(db, shopping_session.id, "checkout", idempotency_key)
            repeated = await db.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.session_id == shopping_session.id,
                    IdempotencyRecord.operation == "checkout",
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
            if repeated:
                return BookingView.model_validate(repeated.response)

            cart, cart_view = await self.carts.validate_db(
                db, shopping_session.id, locale=locale, lock_slots=True
            )
            existing = await db.scalar(select(Booking).where(Booking.cart_id == cart.id))
            if existing:
                response = await self._db_view(db, existing)
                db.add(
                    IdempotencyRecord(
                        session_id=shopping_session.id,
                        operation="checkout",
                        idempotency_key=idempotency_key,
                        response=response.model_dump(mode="json"),
                    )
                )
                return response

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
            booking = Booking(
                id=booking_id,
                cart_id=cart.id,
                booking_reference=booking_reference,
                status="CONFIRMED",
                currency=cart_view.currency,
                total=Decimal(str(cart_view.total)),
                customer_details=customer_details or {},
                confirmed_at=now,
            )
            voucher = Voucher(
                id=voucher_id,
                booking_id=booking_id,
                voucher_reference=voucher_reference,
                qr_payload=qr_payload,
                redemption_instructions=(
                    "This is a simulated voucher. Present the QR code at the meeting "
                    "point 15 minutes before the selected time."
                ),
                valid_from=valid_from,
                valid_until=valid_until,
            )
            db.add_all([booking, voucher])
            items = (await db.scalars(select(CartItem).where(CartItem.cart_id == cart.id))).all()
            for item in items:
                if item.slot_id:
                    slot = await db.scalar(
                        select(AvailabilitySlot)
                        .where(AvailabilitySlot.id == item.slot_id)
                        .with_for_update()
                    )
                    if slot is None or slot.capacity_remaining < item.quantity:
                        raise ApiError(
                            409,
                            "Cart changed",
                            "A selected experience is no longer available",
                            "cart-revalidation",
                        )
                    slot.capacity_remaining -= item.quantity
            cart.status = "CHECKED_OUT"
            cart.version += 1
            await db.flush()
            response = await self._db_view(db, booking)
            db.add(
                IdempotencyRecord(
                    session_id=shopping_session.id,
                    operation="checkout",
                    idempotency_key=idempotency_key,
                    response=response.model_dump(mode="json"),
                )
            )
            return response

    async def get(self, booking_id: UUID) -> BookingView:
        if not database_mode():
            return self._demo_get(booking_id)
        factory = require_session_factory()
        async with factory() as db:
            booking = await db.get(Booking, booking_id)
            if booking is None:
                raise ApiError(
                    404,
                    "Booking not found",
                    "The booking does not exist",
                    "not-found",
                )
            return await self._db_view(db, booking)

    async def voucher(self, booking_id: UUID) -> VoucherView:
        return (await self.get(booking_id)).voucher

    async def _db_view(self, db, booking: Booking) -> BookingView:
        voucher = await db.scalar(select(Voucher).where(Voucher.booking_id == booking.id))
        if voucher is None:
            raise ApiError(
                409,
                "Voucher unavailable",
                "The booking voucher has not been generated",
                "voucher-unavailable",
            )
        return BookingView(
            id=booking.id,
            booking_reference=booking.booking_reference,
            status=booking.status,
            currency=booking.currency,
            total=float(booking.total),
            confirmed_at=booking.confirmed_at,
            voucher=VoucherView(
                id=voucher.id,
                voucher_reference=voucher.voucher_reference,
                qr_payload=voucher.qr_payload,
                qr_image_data_url=_qr_data_url(voucher.qr_payload),
                redemption_instructions=voucher.redemption_instructions,
                valid_from=voucher.valid_from,
                valid_until=voucher.valid_until,
            ),
        )

    def _demo_confirm(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        customer_details: dict[str, str] | None = None,
    ) -> BookingView:
        cache_key = (session_id, "checkout", idempotency_key)
        if cache_key in self.data.idempotency:
            return self.data.idempotency[cache_key]
        cart_view = self.carts._demo_validate(session_id)
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

    def _demo_get(self, booking_id: UUID) -> BookingView:
        booking = self.data.bookings.get(booking_id)
        if not booking:
            raise ApiError(404, "Booking not found", "The booking does not exist", "not-found")
        return self._view(booking)

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
