"""Write imported supplier products into the catalogue without destroying it.

Seeding truncates because it owns the whole demo catalogue. An import cannot:
real supply arrives alongside existing supply, arrives repeatedly as the
supplier updates prices and variants, and must never take carts, bookings or
seeded experiences with it. So this writes an upsert keyed on
``Experience.external_id`` and replaces only the children of the experiences it
owns.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.provider import AIProvider, build_ai_provider
from app.catalog.db_seed import _document_text
from app.catalog.seed import stable_id
from app.catalog.trippass import (
    TRIPPASS_SUPPLIER_EXTERNAL_ID,
    TRIPPASS_SUPPLIER_NAME,
    build_trippass_catalog,
)
from app.common.database import session_factory
from app.common.models import (
    AvailabilitySlot,
    Destination,
    Experience,
    ExperienceMedia,
    ExperienceOption,
    ExperienceSearchDocument,
    OptionPrice,
    Supplier,
)
from app.common.ranking import deterministic_embedding

logger = logging.getLogger(__name__)

# Approximate centroids for destinations an import may introduce. Only used
# when the destination row does not already exist.
_DESTINATION_FALLBACK = (16.0544, 108.2022)


async def _ensure_supplier(session: AsyncSession, external_id: str, name: str) -> Any:
    supplier = await session.scalar(select(Supplier).where(Supplier.external_id == external_id))
    if supplier is None:
        supplier = Supplier(
            id=stable_id("supplier", external_id.casefold()),
            external_id=external_id,
            name=name,
            status="ACTIVE",
        )
        session.add(supplier)
        await session.flush()
    return supplier


async def _ensure_destination(session: AsyncSession, product: dict) -> Any:
    slug = product["destination"].casefold().replace(" ", "-")
    destination = await session.scalar(select(Destination).where(Destination.slug == slug))
    if destination is not None:
        return destination
    latitude, longitude = product["latitude"], product["longitude"]
    if not latitude or not longitude:
        latitude, longitude = _DESTINATION_FALLBACK
    destination = Destination(
        id=product["destination_id"],
        slug=slug,
        name=product["destination"],
        country_code="VN",
        latitude=Decimal(str(latitude)),
        longitude=Decimal(str(longitude)),
        timezone="Asia/Ho_Chi_Minh",
    )
    session.add(destination)
    await session.flush()
    return destination


async def _replace_media_and_document(session: AsyncSession, experience_id: Any) -> None:
    """Media and the search document are derived, so they are safe to rebuild."""
    await session.execute(
        delete(ExperienceMedia).where(ExperienceMedia.experience_id == experience_id)
    )
    await session.execute(
        delete(ExperienceSearchDocument).where(
            ExperienceSearchDocument.experience_id == experience_id
        )
    )


async def _sync_option(session: AsyncSession, experience_id: Any, option_data: dict) -> None:
    """Upsert one option, its prices and its availability window.

    Deleting and recreating options was wrong twice over: it breaks the foreign
    keys carts and bookings hold, and skipping the ones a cart referenced left
    those options frozen on stale prices with an availability window that
    quietly expired. Upserting keeps every id stable and every option current.
    """
    option = await session.scalar(
        select(ExperienceOption).where(
            ExperienceOption.experience_id == experience_id,
            ExperienceOption.external_id == option_data["external_id"],
        )
    )
    if option is None:
        option = ExperienceOption(
            id=option_data["id"],
            experience_id=experience_id,
            external_id=option_data["external_id"],
        )
        session.add(option)
    option.name = option_data["name"][:200]
    option.description = option_data["description"]
    option.validity_type = option_data["validity_type"]
    option.confirmation_type = option_data["confirmation_type"]
    option.cancellation_policy_code = option_data["cancellation_policy_code"]
    option.free_cancellation_hours = option_data["free_cancellation_hours"]
    option.max_party_size = option_data["max_party_size"]
    option.active = option_data["active"]
    await session.flush()

    # Carts snapshot their own unit prices, so no row points at option_prices
    # and the supplier's current price list can simply replace ours.
    await session.execute(delete(OptionPrice).where(OptionPrice.option_id == option.id))
    for price_data in option_data["prices"]:
        session.add(
            OptionPrice(
                id=stable_id(
                    "price",
                    f"{option_data['external_id']}:{price_data['participant_type']}",
                ),
                option_id=option.id,
                participant_type=price_data["participant_type"],
                currency=price_data["currency"],
                amount=Decimal(str(price_data["amount"])),
                minimum_age=price_data["minimum_age"],
                maximum_age=price_data["maximum_age"],
            )
        )

    # Slot ids are derived from the option and start time, so re-importing on a
    # later date extends the rolling window instead of duplicating it. Existing
    # slots keep their capacity: resurrecting sold seats would manufacture the
    # scarcity signal the storefront is supposed to report honestly.
    wanted = option_data["slots"]
    present = set(
        (
            await session.scalars(
                select(AvailabilitySlot.id).where(
                    AvailabilitySlot.id.in_([slot["id"] for slot in wanted])
                )
            )
        ).all()
    )
    for slot_data in wanted:
        if slot_data["id"] in present:
            continue
        session.add(
            AvailabilitySlot(
                id=slot_data["id"],
                option_id=option.id,
                starts_at=slot_data["starts_at"],
                ends_at=slot_data["ends_at"],
                capacity_total=slot_data["capacity_total"],
                capacity_remaining=slot_data["capacity_remaining"],
                status=slot_data["status"],
                price_override=None,
            )
        )


async def _retire_withdrawn_options(
    session: AsyncSession, experience_id: Any, current: set[str]
) -> None:
    """Deactivate options the supplier no longer publishes.

    Deleting them would orphan carts and bookings. Deactivating retires them
    everywhere it matters instead: both search and add-to-cart require an
    active option, so a withdrawn variant stops being sellable while the rows
    a past purchase depends on stay intact.
    """
    stale = (
        await session.scalars(
            select(ExperienceOption).where(
                ExperienceOption.experience_id == experience_id,
                ExperienceOption.external_id.notin_(current),
                ExperienceOption.active.is_(True),
            )
        )
    ).all()
    for option in stale:
        option.active = False
    if stale:
        logger.info(
            "Deactivated %d option(s) withdrawn by the supplier on experience %s",
            len(stale),
            experience_id,
        )


def _apply(experience: Experience, product: dict, supplier_id: Any, destination_id: Any) -> None:
    experience.supplier_id = supplier_id
    experience.destination_id = destination_id
    experience.slug = product["slug"]
    experience.title = product["title"]
    experience.short_description = product["short_description"]
    experience.description = product["description"]
    experience.category = product["category"]
    experience.subcategories = product["subcategories"]
    experience.interest_tags = product["interest_tags"]
    experience.indoor_outdoor = product["indoor_outdoor"]
    experience.duration_minutes = product["duration_minutes"]
    experience.latitude = Decimal(str(product["latitude"]))
    experience.longitude = Decimal(str(product["longitude"]))
    experience.meeting_point = product["meeting_point"]
    experience.languages = product["languages"]
    experience.accessibility_features = product["accessibility_features"]
    experience.minimum_age = product["minimum_age"]
    experience.family_friendly = product["family_friendly"]
    experience.instant_confirmation = product["instant_confirmation"]
    experience.mobile_voucher = product["mobile_voucher"]
    experience.rating = Decimal(str(product["rating"]))
    experience.review_count = product["review_count"]
    experience.popularity_score = Decimal(str(product["popularity_score"]))
    experience.status = product["status"]
    experience.published_at = experience.published_at or datetime.now(UTC)


async def upsert_catalog(
    catalog: list[dict],
    *,
    supplier_external_id: str,
    supplier_name: str,
    ai_provider: AIProvider | None = None,
) -> dict[str, int]:
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required to import a supplier catalogue.")
    if not catalog:
        return {"created": 0, "updated": 0, "needs_review": 0}

    provider = ai_provider or build_ai_provider()
    documents = [_document_text(product) for product in catalog]
    embeddings: list[list[float]] = []
    for start in range(0, len(documents), 64):
        batch = documents[start : start + 64]
        try:
            embeddings.extend(await provider.embed_many(batch))
        except Exception:
            logger.exception("Embedding batch failed; falling back to deterministic vectors")
            embeddings.extend(deterministic_embedding(document) for document in batch)

    created = updated = 0
    async with session_factory() as session:
        supplier = await _ensure_supplier(session, supplier_external_id, supplier_name)

        for product, document, embedding in zip(catalog, documents, embeddings, strict=True):
            destination = await _ensure_destination(session, product)
            experience = await session.scalar(
                select(Experience).where(Experience.external_id == product["external_id"])
            )
            if experience is None:
                experience = Experience(id=product["id"], external_id=product["external_id"])
                session.add(experience)
                created += 1
            else:
                await _replace_media_and_document(session, experience.id)
                updated += 1
            _apply(experience, product, supplier.id, destination.id)
            await session.flush()

            for order, url in enumerate([product["image_url"]]):
                session.add(
                    ExperienceMedia(
                        id=stable_id("media", f"{product['slug']}:{order}"),
                        experience_id=experience.id,
                        url=url,
                        alt_text=product["title"][:250],
                        sort_order=order,
                    )
                )

            for option_data in product["options"]:
                await _sync_option(session, experience.id, option_data)
            await _retire_withdrawn_options(
                session,
                experience.id,
                {option_data["external_id"] for option_data in product["options"]},
            )

            session.add(
                ExperienceSearchDocument(
                    experience_id=experience.id,
                    document_text=document,
                    embedding=embedding,
                    embedding_model="text-embedding-3-small",
                    embedding_version="1",
                    content_hash=hashlib.sha256(document.encode()).hexdigest(),
                    embedded_at=datetime.now(UTC),
                )
            )

        await session.commit()

    return {
        "created": created,
        "updated": updated,
        "needs_review": sum(1 for product in catalog if product.get("needs_review")),
    }


async def import_trippass(*, days: int = 30) -> dict[str, int]:
    provider = build_ai_provider()
    catalog = await build_trippass_catalog(provider, days=days)
    result = await upsert_catalog(
        catalog,
        supplier_external_id=TRIPPASS_SUPPLIER_EXTERNAL_ID,
        supplier_name=TRIPPASS_SUPPLIER_NAME,
        ai_provider=provider,
    )
    result["fetched"] = len(catalog)
    return result
