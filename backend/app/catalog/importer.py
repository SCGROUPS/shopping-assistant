"""Write imported supplier products into the catalogue without destroying it.

Seeding truncates because it owns the whole demo catalogue. An import cannot:
real supply arrives alongside existing supply, arrives repeatedly as the
supplier updates prices and variants, and must never take carts, bookings or
seeded experiences with it. So this writes an upsert keyed on
``Experience.external_id`` and replaces only the children of the experiences it
owns.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.provider import AIProvider, build_ai_provider
from app.catalog import indexing
from app.catalog.db_seed import _document_text
from app.catalog.indexing import (
    FALLBACK_EMBEDDING_MODEL,
    enqueue_experience_reindex,
    upsert_search_document,
)
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
    ExperienceOverride,
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


async def _replace_media(session: AsyncSession, experience_id: Any) -> None:
    """Media is derived from the feed, so it is safe to rebuild wholesale.

    Search documents deliberately are not. This used to delete every document
    for the experience before recreating one, which is fine while English is
    the only locale and destructive the moment it is not: one run of this
    importer would erase every translated document and leave English behind,
    degrading search silently rather than failing. Documents are now upserted
    per locale by `catalog.indexing`.
    """
    await session.execute(
        delete(ExperienceMedia).where(ExperienceMedia.experience_id == experience_id)
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


# Supplier-owned fields. Everything else on the row - merchandising, review
# state, published_at - belongs to the business and is never touched by an
# import.
IMPORTED_FIELDS = (
    "slug",
    "title",
    "short_description",
    "description",
    "category",
    "subcategories",
    "interest_tags",
    "indoor_outdoor",
    "duration_minutes",
    "latitude",
    "longitude",
    "meeting_point",
    "languages",
    "accessibility_features",
    "minimum_age",
    "family_friendly",
    "instant_confirmation",
    "mobile_voucher",
    "rating",
    "review_count",
    "popularity_score",
)
_DECIMAL_FIELDS = {"latitude", "longitude", "rating", "popularity_score"}


async def _protected_fields(session: AsyncSession, experience_id: Any) -> frozenset[str]:
    """Fields a human has corrected and the supplier must not overwrite."""
    override = await session.get(ExperienceOverride, experience_id)
    return frozenset(override.fields) if override and override.fields else frozenset()


def _apply(
    experience: Experience,
    product: dict,
    supplier_id: Any,
    destination_id: Any,
    protected: frozenset[str] = frozenset(),
) -> None:
    """Copy the supplier's view onto the row, minus anything a human owns.

    `protected` carries the fields an operator has corrected. Skipping them is
    what lets the catalog editor and the nightly import coexist: a fixed
    category stays fixed while price and availability keep syncing.
    """
    experience.supplier_id = supplier_id
    if "destination_id" not in protected:
        experience.destination_id = destination_id
    for field in IMPORTED_FIELDS:
        if field in protected:
            continue
        value = product[field]
        setattr(experience, field, Decimal(str(value)) if field in _DECIMAL_FIELDS else value)
    # A product the classifier was unsure of is held for a human rather than
    # sold on a guess. `PENDING_REVIEW` is not `PUBLISHED`, and the eligibility
    # gate already filters on that, so nothing downstream needs to know.
    #
    # Once a human has ruled on the listing they own its status, and the
    # classifier does not get to reopen the question on the next run - a review
    # queue that refills itself with work already done is a queue nobody reads.
    if "status" in protected:
        experience.needs_review = False
    else:
        experience.needs_review = bool(product.get("needs_review"))
        experience.status = "PENDING_REVIEW" if experience.needs_review else product["status"]
    if experience.status == "PUBLISHED":
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
    # Tracked per document, because a deterministic vector is not an embedding:
    # it is unrelated to the vectors a shopper's query produces, so a document
    # holding one is absent from semantic search while looking perfectly
    # healthy. Recording which model actually produced each vector is what lets
    # reconciliation find and replace them once the provider recovers.
    fallbacks: list[bool] = []
    for start in range(0, len(documents), 64):
        batch = documents[start : start + 64]
        try:
            embeddings.extend(await provider.embed_many(batch))
            fallbacks.extend(False for _ in batch)
        except Exception:
            logger.exception("Embedding batch failed; falling back to deterministic vectors")
            embeddings.extend(deterministic_embedding(document) for document in batch)
            fallbacks.extend(True for _ in batch)

    created = updated = 0
    async with session_factory() as session:
        supplier = await _ensure_supplier(session, supplier_external_id, supplier_name)

        for product, document, embedding, is_fallback in zip(
            catalog, documents, embeddings, fallbacks, strict=True
        ):
            destination = await _ensure_destination(session, product)
            experience = await session.scalar(
                select(Experience).where(Experience.external_id == product["external_id"])
            )
            if experience is None:
                experience = Experience(id=product["id"], external_id=product["external_id"])
                session.add(experience)
                created += 1
                protected = frozenset()
            else:
                await _replace_media(session, experience.id)
                updated += 1
                protected = await _protected_fields(session, experience.id)
            _apply(experience, product, supplier.id, destination.id, protected)
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

            await upsert_search_document(
                session,
                experience_id=experience.id,
                locale="en",
                document_text=document,
                embedding=embedding,
                embedding_model=(
                    FALLBACK_EMBEDDING_MODEL if is_fallback else indexing.EMBEDDING_MODEL
                ),
            )
            # English is written directly because the import already computed
            # its embedding, but the other locales resolve *through* this text
            # until they are translated - so a source change that only touched
            # `en` would leave seven documents describing the previous version.
            # Enqueueing every locale is safe: the ones already carrying this
            # fingerprint are skipped rather than requeued.
            await enqueue_experience_reindex(session, experience.id)

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
