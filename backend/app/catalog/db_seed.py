from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text

from app.assistant.provider import AIProvider, build_ai_provider
from app.catalog.seed import build_seed_catalog, stable_id
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
            matching = [
                item for item in catalog if item["destination"] == destination_name
            ]
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

        for product, document, embedding in zip(
            catalog, documents, embeddings, strict=True
        ):
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
