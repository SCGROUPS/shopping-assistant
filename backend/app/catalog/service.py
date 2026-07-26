from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from app.api.schemas import (
    ContentFieldMeta,
    ExperienceCard,
    ExperienceDetail,
    OptionView,
    Participant,
    PriceView,
    SearchFilters,
    SlotView,
)
from app.common import currency as fx
from app.common import urgency
from app.common.errors import ApiError
from app.common.locales import DEFAULT_LOCALE
from app.common.persistence import catalog_product
from app.common.store import DemoStore, store


def starting_price(product: dict[str, Any]) -> tuple[float, str]:
    prices = [
        price
        for option in product["options"]
        if option["active"]
        for price in option["prices"]
        if price["participant_type"] == "adult"
    ]
    if not prices:
        raise ApiError(409, "Unbookable experience", "No active price is available", "no-price")
    price = min(prices, key=lambda item: item["amount"])
    return float(price["amount"]), price["currency"]


def product_card(
    product: dict[str, Any],
    explanations: list[str] | None = None,
    *,
    reason_code: str | None = None,
    filters: SearchFilters | None = None,
    party: Sequence[Participant] = (),
    demand: dict[str, float] | None = None,
    display_currency: str | None = None,
) -> ExperienceCard:
    price, currency = starting_price(product)
    free_hours = max(
        (option["free_cancellation_hours"] for option in product["options"] if option["active"]),
        default=0,
    )
    has_capacity = any(
        slot["status"] == "AVAILABLE" and slot["capacity_remaining"] > 0
        for option in product["options"]
        for slot in option["slots"]
    )
    # Codes, not sentences. These used to be English prose, which made them
    # the only carrier of four facts the client needed - so the client
    # recovered them by matching English words, and translating a badge would
    # have silently turned the fact off. A code says the same thing in every
    # language and the storefront renders it from its own dictionary.
    badges = []
    if product["instant_confirmation"]:
        badges.append("instant_confirmation")
    if product["family_friendly"]:
        badges.append("family_friendly")
    if free_hours:
        badges.append("free_cancellation")
    badges.append("available" if has_capacity else "sold_out")
    options = [
        OptionView(
            id=option["id"],
            name=option["name"],
            description=option["description"],
            validity_type=option["validity_type"],
            free_cancellation_hours=option["free_cancellation_hours"],
            max_party_size=option["max_party_size"],
            prices=[PriceView(**item) for item in option["prices"]],
        )
        for option in product["options"]
        if option["active"]
    ]
    return ExperienceCard(
        id=product["id"],
        slug=product["slug"],
        title=product["title"],
        location=product["destination"],
        short_description=product["short_description"],
        destination=product["destination"],
        category=product["category"],
        image_url=product["image_url"],
        duration_minutes=product["duration_minutes"],
        rating=product["rating"],
        review_count=product["review_count"],
        price=price,
        currency=currency,
        tags=list(dict.fromkeys(product["interest_tags"] + product["subcategories"])),
        badges=badges,
        available=has_capacity,
        instant_confirmation=bool(product["instant_confirmation"]),
        family_friendly=bool(product["family_friendly"]),
        free_cancellation_hours=int(free_hours or 0),
        display_price=(
            fx.convert(price, currency, display_currency)
            if display_currency
            and fx.supported(display_currency)
            and display_currency.upper() != currency.upper()
            else None
        ),
        display_currency=(
            display_currency.upper()
            if display_currency
            and fx.supported(display_currency)
            and display_currency.upper() != currency.upper()
            else None
        ),
        scarcity=urgency.scarcity(product, filters, party),
        social_proof=urgency.social_proof(demand),
        reason=" ".join(explanations or []) or None,
        reason_code=reason_code,
        options=options,
        locale=product.get("locale", DEFAULT_LOCALE),
        content_meta={
            field: ContentFieldMeta(**meta)
            for field, meta in (product.get("content_meta") or {}).items()
        },
    )


def get_product(product_id: UUID, data: DemoStore = store) -> dict[str, Any]:
    product = data.products.get(product_id)
    if not product or product["status"] != "PUBLISHED":
        raise ApiError(404, "Experience not found", "The experience does not exist", "not-found")
    return product


async def get_product_async(
    product_id: UUID, data: DemoStore = store, locale: str = DEFAULT_LOCALE
) -> dict[str, Any]:
    product = await catalog_product(product_id, data, locale=locale)
    if not product or product["status"] != "PUBLISHED":
        raise ApiError(
            404,
            "Experience not found",
            "The experience does not exist",
            "not-found",
        )
    return product


def product_detail(
    product: dict[str, Any], visit_start: datetime | None = None
) -> ExperienceDetail:
    card = product_card(product).model_dump()
    options: list[OptionView] = []
    for option in product["options"]:
        slots = option["slots"]
        if visit_start:
            slots = [slot for slot in slots if slot["starts_at"].date() == visit_start.date()]
        options.append(
            OptionView(
                id=option["id"],
                name=option["name"],
                description=option["description"],
                validity_type=option["validity_type"],
                free_cancellation_hours=option["free_cancellation_hours"],
                max_party_size=option["max_party_size"],
                prices=[PriceView(**price) for price in option["prices"]],
                slots=[SlotView(**slot) for slot in slots[:24]],
            )
        )
    return ExperienceDetail.model_validate(
        {
            **card,
            "description": product["description"],
            "latitude": product["latitude"],
            "longitude": product["longitude"],
            "meeting_point": product["meeting_point"],
            "languages": product["languages"],
            "accessibility_features": product["accessibility_features"],
            "minimum_age": product["minimum_age"],
            "options": options,
        }
    )
