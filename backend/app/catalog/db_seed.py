from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.assistant.provider import AIProvider, build_ai_provider
from app.catalog.seed import build_seed_catalog, stable_id
from app.common.database import session_factory
from app.common.models import (
    AvailabilitySlot,
    Booking,
    CartItem,
    Destination,
    Experience,
    ExperienceMedia,
    ExperienceOption,
    ExperienceSearchDocument,
    OptionPrice,
    Supplier,
)
from app.common.ranking import deterministic_embedding


def _document_text(product: dict) -> str:
    return " ".join(
        [
            product["title"],
            product["destination"],
            product["category"],
            *product["subcategories"],
            *product["interest_tags"],
            product["short_description"],
            product["description"],
            product["indoor_outdoor"],
            *product["accessibility_features"],
            *product["languages"],
        ]
    )


async def seed_database(
    *,
    force: bool = False,
    ai_provider: AIProvider | None = None,
) -> int:
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required for database seeding.")

    provider = ai_provider or build_ai_provider()
    catalog = build_seed_catalog()
    supplier_id = stable_id("supplier", "vietra-demo")

    async with session_factory() as session:
        existing = await session.scalar(select(func.count()).select_from(Experience))
        if existing and not force:
            return int(existing)
        if force:
            await session.execute(
                text(
                    "TRUNCATE TABLE "
                    "query_embedding_cache, embedding_work_items, vouchers, bookings, "
                    "cart_items, carts, conversation_messages, conversations, behavior_events, "
                    "shopping_sessions, experience_search_documents, availability_slots, "
                    "option_prices, experience_options, experience_media, experiences, "
                    "destinations, suppliers RESTART IDENTITY CASCADE"
                )
            )

        documents = [_document_text(product) for product in catalog]
        embeddings: list[list[float]] = []
        for start in range(0, len(documents), 64):
            batch = documents[start : start + 64]
            try:
                embeddings.extend(await provider.embed_many(batch))
            except Exception:
                embeddings.extend(deterministic_embedding(document) for document in batch)

        session.add(
            Supplier(
                id=supplier_id,
                external_id="VIETRA-DEMO",
                name="Vietra Demo Experiences",
                status="ACTIVE",
            )
        )

        destinations: dict[str, Destination] = {}
        for product in catalog:
            destination_name = product["destination"]
            if destination_name in destinations:
                continue
            matching = [item for item in catalog if item["destination"] == destination_name]
            latitude = sum(item["latitude"] for item in matching) / len(matching)
            longitude = sum(item["longitude"] for item in matching) / len(matching)
            destination = Destination(
                id=product["destination_id"],
                slug=destination_name.casefold().replace(" ", "-"),
                name=destination_name,
                country_code="VN",
                latitude=Decimal(str(latitude)),
                longitude=Decimal(str(longitude)),
                timezone="Asia/Ho_Chi_Minh",
            )
            destinations[destination_name] = destination
            session.add(destination)

        await session.flush()

        for product, document, embedding in zip(catalog, documents, embeddings, strict=True):
            experience = Experience(
                id=product["id"],
                external_id=product["external_id"],
                supplier_id=supplier_id,
                destination_id=product["destination_id"],
                slug=product["slug"],
                title=product["title"],
                short_description=product["short_description"],
                description=product["description"],
                category=product["category"],
                subcategories=product["subcategories"],
                interest_tags=product["interest_tags"],
                indoor_outdoor=product["indoor_outdoor"],
                duration_minutes=product["duration_minutes"],
                latitude=Decimal(str(product["latitude"])),
                longitude=Decimal(str(product["longitude"])),
                meeting_point=product["meeting_point"],
                languages=product["languages"],
                accessibility_features=product["accessibility_features"],
                minimum_age=product["minimum_age"],
                family_friendly=product["family_friendly"],
                instant_confirmation=product["instant_confirmation"],
                mobile_voucher=product["mobile_voucher"],
                rating=Decimal(str(product["rating"])),
                review_count=product["review_count"],
                popularity_score=Decimal(str(product["popularity_score"])),
                status=product["status"],
                published_at=datetime.now(UTC),
            )
            session.add(experience)
            session.add(
                ExperienceMedia(
                    id=stable_id("media", product["slug"]),
                    experience_id=product["id"],
                    url=product["image_url"],
                    alt_text=product["title"],
                    sort_order=0,
                )
            )

            for option_data in product["options"]:
                option = ExperienceOption(
                    id=option_data["id"],
                    experience_id=product["id"],
                    external_id=option_data["external_id"],
                    name=option_data["name"],
                    description=option_data["description"],
                    validity_type=option_data["validity_type"],
                    confirmation_type=option_data["confirmation_type"],
                    cancellation_policy_code=option_data["cancellation_policy_code"],
                    free_cancellation_hours=option_data["free_cancellation_hours"],
                    max_party_size=option_data["max_party_size"],
                    active=option_data["active"],
                )
                session.add(option)
                for price_data in option_data["prices"]:
                    session.add(
                        OptionPrice(
                            id=stable_id(
                                "price",
                                f"{product['slug']}:{price_data['participant_type']}",
                            ),
                            option_id=option_data["id"],
                            participant_type=price_data["participant_type"],
                            currency=price_data["currency"],
                            amount=Decimal(str(price_data["amount"])),
                            minimum_age=price_data["minimum_age"],
                            maximum_age=price_data["maximum_age"],
                        )
                    )
                for slot_data in option_data["slots"]:
                    session.add(
                        AvailabilitySlot(
                            id=slot_data["id"],
                            option_id=option_data["id"],
                            starts_at=slot_data["starts_at"],
                            ends_at=slot_data["ends_at"],
                            capacity_total=slot_data["capacity_total"],
                            capacity_remaining=slot_data["capacity_remaining"],
                            status=slot_data["status"],
                            price_override=None,
                        )
                    )

            session.add(
                ExperienceSearchDocument(
                    experience_id=product["id"],
                    document_text=document,
                    embedding=embedding,
                    embedding_model="text-embedding-3-small",
                    embedding_version="1",
                    content_hash=hashlib.sha256(document.encode()).hexdigest(),
                    embedded_at=datetime.now(UTC),
                )
            )

        await session.commit()
    return len(catalog)


# Eight columns a row, against Postgres' limit of 65535 bind parameters.
_UPSERT_CHUNK = 2000


async def refresh_availability() -> dict[str, int]:
    """Reconcile seeded availability without destroying anything.

    `seed_database` returns early when the catalogue already exists, so a change
    to the seeded supply shape never reaches an environment that has been
    seeded once. Forcing a reseed is not an acceptable alternative: it truncates
    behaviour events, bookings and sessions, which is the measurement the whole
    funnel depends on.

    Slot ids are derived from the slug and start time, so re-running the seed on
    a later date produces the same ids for dates that already exist and new ids
    for dates that do not. Upserting on that id therefore both refreshes the
    supply profile and extends the rolling window, while past slots are simply
    left alone.

    Capacity already consumed by confirmed bookings is subtracted, so this never
    invents availability that has been sold — a scarcity badge computed from
    resurrected capacity would be exactly the dishonest signal the feature is
    designed to avoid.
    """
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required for availability refresh.")

    catalog = build_seed_catalog()
    seeded: dict[uuid.UUID, tuple[uuid.UUID, dict]] = {
        slot["id"]: (option["id"], slot)
        for product in catalog
        for option in product["options"]
        for slot in option["slots"]
    }

    async with session_factory() as session:
        # Subtracting sold capacity is only honest if nothing is sold between
        # reading the total and writing it back. Checkout locks the slot row and
        # decrements it, so a booking that commits in that gap gets overwritten
        # by a total computed before it existed - capacity that was sold, resold.
        #
        # SHARE ROW EXCLUSIVE is not enough: checkout begins with SELECT ... FOR
        # UPDATE, which takes only ROW SHARE at table level and so slips past
        # it, takes the row, and then blocks on the ROW EXCLUSIVE its UPDATE
        # needs - while we block on the row it is holding. That is a deadlock,
        # and a test found it. EXCLUSIVE conflicts with ROW SHARE too, so
        # checkout waits at the table before it holds anything, and we can never
        # wait on a row while holding the table. Plain reads take ACCESS SHARE
        # and are unaffected, so the storefront keeps serving availability.
        #
        # This is only affordable because the write below is now twelve
        # statements: the transaction lasts seconds, and holding checkout for
        # seconds during a deployment is a fair price for never overselling. At
        # 22,680 round trips it would have been eleven minutes, which is why the
        # lock could not have been taken before.
        await session.execute(text("LOCK TABLE availability_slots IN EXCLUSIVE MODE"))

        booked_rows = (
            await session.execute(
                select(
                    CartItem.slot_id,
                    func.coalesce(func.sum(CartItem.quantity), 0),
                )
                .join(Booking, Booking.cart_id == CartItem.cart_id)
                .where(CartItem.slot_id.is_not(None))
                .group_by(CartItem.slot_id)
            )
        ).all()
        booked: dict[uuid.UUID, int] = {
            slot_id: int(quantity) for slot_id, quantity in booked_rows if slot_id is not None
        }

        existing = set(
            (
                await session.scalars(
                    select(AvailabilitySlot.id).where(AvailabilitySlot.id.in_(list(seeded)))
                )
            ).all()
        )

        rows = [
            {
                "id": slot_id,
                "option_id": option_id,
                "starts_at": slot["starts_at"],
                "ends_at": slot["ends_at"],
                "capacity_total": slot["capacity_total"],
                "capacity_remaining": max(0, slot["capacity_remaining"] - booked.get(slot_id, 0)),
                "status": slot["status"],
                "price_override": None,
            }
            for slot_id, (option_id, slot) in seeded.items()
        ]

        # One statement per chunk rather than one per slot. A round trip to a
        # managed database is around 30ms, so 22,680 of them is eleven minutes
        # of a deployment spent waiting on the network - which is what it cost
        # before this, and most of the reason a deploy took half an hour.
        #
        # Chunked because Postgres accepts 65535 bind parameters per statement
        # and each row carries eight.
        for start in range(0, len(rows), _UPSERT_CHUNK):
            chunk = rows[start : start + _UPSERT_CHUNK]
            statement = pg_insert(AvailabilitySlot).values(chunk)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[AvailabilitySlot.id],
                    # Deliberately only the capacity columns. A slot that already
                    # exists keeps its own times, status and price override -
                    # those are operational state, not seed data, and the seed
                    # has no business reverting them.
                    set_={
                        "capacity_total": statement.excluded.capacity_total,
                        "capacity_remaining": statement.excluded.capacity_remaining,
                    },
                )
            )
        await session.commit()

    updated = len(existing)
    return {"created": len(rows) - updated, "updated": updated}
