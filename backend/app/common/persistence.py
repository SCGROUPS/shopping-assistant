from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.config import get_settings
from app.common.database import session_factory
from app.common.models import (
    BehaviorEvent,
    Destination,
    Experience,
    ExperienceOption,
    ExperienceSearchDocument,
    ShoppingSession,
)
from app.common.ranking import deterministic_embedding
from app.common.store import DemoStore, store


def database_mode() -> bool:
    return not get_settings().demo_mode


def require_session_factory():
    if session_factory is None:
        raise RuntimeError("DATABASE_URL is required when DEMO_MODE is false")
    return session_factory


async def ensure_session(db: AsyncSession, anonymous_id: str) -> ShoppingSession:
    statement = (
        insert(ShoppingSession)
        .values(
            anonymous_id=anonymous_id,
            currency="VND",
            party=[],
            preference_state={},
            last_seen_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=[ShoppingSession.anonymous_id],
            set_={"last_seen_at": datetime.now(UTC)},
        )
        .returning(ShoppingSession)
    )
    return (await db.execute(statement)).scalar_one()


async def lock_idempotency(
    db: AsyncSession,
    session_id: UUID,
    operation: str,
    idempotency_key: str,
) -> None:
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:idempotency_scope, 0)"
            ")"
        ),
        {
            "idempotency_scope": (
                f"{session_id}:{operation}:{idempotency_key}"
            )
        },
    )


async def catalog_products(data: DemoStore = store) -> list[dict[str, Any]]:
    if not database_mode():
        return list(data.products.values())
    factory = require_session_factory()
    async with factory() as db:
        return await load_products(db)


async def catalog_product(
    product_id: UUID, data: DemoStore = store
) -> dict[str, Any] | None:
    if not database_mode():
        return data.products.get(product_id)
    factory = require_session_factory()
    async with factory() as db:
        products = await load_products(db, [product_id])
        return products[0] if products else None


async def load_products(
    db: AsyncSession, product_ids: list[UUID] | None = None
) -> list[dict[str, Any]]:
    statement = (
        select(Experience)
        .where(Experience.status == "PUBLISHED")
        .options(
            selectinload(Experience.options).selectinload(ExperienceOption.prices),
            selectinload(Experience.options).selectinload(ExperienceOption.slots),
            selectinload(Experience.media),
        )
        .order_by(Experience.popularity_score.desc())
    )
    if product_ids is not None:
        if not product_ids:
            return []
        statement = statement.where(Experience.id.in_(product_ids))
    experiences = list((await db.scalars(statement)).unique())
    if not experiences:
        return []

    destination_rows = await db.execute(
        select(Destination.id, Destination.name).where(
            Destination.id.in_({item.destination_id for item in experiences})
        )
    )
    destinations: dict[UUID, str] = {}
    for destination_id, destination_name in destination_rows.tuples():
        destinations[destination_id] = destination_name
    document_rows = await db.execute(
        select(ExperienceSearchDocument).where(
            ExperienceSearchDocument.experience_id.in_(
                {item.id for item in experiences}
            )
        )
    )
    documents = {
        item.experience_id: item for item in document_rows.scalars().all()
    }

    products = [
        _product_dict(
            experience,
            destinations[experience.destination_id],
            documents.get(experience.id),
        )
        for experience in experiences
    ]
    if product_ids is None:
        return products
    by_id = {item["id"]: item for item in products}
    return [by_id[item_id] for item_id in product_ids if item_id in by_id]


def _product_dict(
    experience: Experience,
    destination: str,
    document: ExperienceSearchDocument | None,
) -> dict[str, Any]:
    media = sorted(experience.media, key=lambda item: item.sort_order)
    document_text = document.document_text if document else " ".join(
        [
            experience.title,
            destination,
            experience.category,
            *experience.subcategories,
            *experience.interest_tags,
            experience.short_description,
            experience.description,
        ]
    )
    embedding = (
        list(document.embedding)
        if document is not None and document.embedding is not None
        else deterministic_embedding(document_text)
    )
    return {
        "id": experience.id,
        "external_id": experience.external_id,
        "destination_id": experience.destination_id,
        "slug": experience.slug,
        "title": experience.title,
        "short_description": experience.short_description,
        "description": experience.description,
        "destination": destination,
        "category": experience.category,
        "subcategories": list(experience.subcategories),
        "interest_tags": list(experience.interest_tags),
        "indoor_outdoor": experience.indoor_outdoor,
        "duration_minutes": experience.duration_minutes,
        "latitude": float(experience.latitude),
        "longitude": float(experience.longitude),
        "meeting_point": experience.meeting_point,
        "languages": list(experience.languages),
        "accessibility_features": list(experience.accessibility_features),
        "minimum_age": experience.minimum_age,
        "family_friendly": experience.family_friendly,
        "instant_confirmation": experience.instant_confirmation,
        "mobile_voucher": experience.mobile_voucher,
        "rating": float(experience.rating),
        "review_count": experience.review_count,
        "popularity_score": float(experience.popularity_score),
        "status": experience.status,
        "image_url": media[0].url if media else "",
        "search_document": document_text,
        "embedding": embedding,
        "options": [
            {
                "id": option.id,
                "external_id": option.external_id,
                "name": option.name,
                "description": option.description,
                "validity_type": option.validity_type,
                "confirmation_type": option.confirmation_type,
                "cancellation_policy_code": option.cancellation_policy_code,
                "free_cancellation_hours": option.free_cancellation_hours,
                "max_party_size": option.max_party_size,
                "active": option.active,
                "prices": [
                    {
                        "participant_type": price.participant_type,
                        "currency": price.currency,
                        "amount": float(price.amount),
                        "minimum_age": price.minimum_age,
                        "maximum_age": price.maximum_age,
                    }
                    for price in option.prices
                ],
                "slots": [
                    {
                        "id": slot.id,
                        "starts_at": slot.starts_at,
                        "ends_at": slot.ends_at,
                        "capacity_total": slot.capacity_total,
                        "capacity_remaining": slot.capacity_remaining,
                        "status": slot.status,
                    }
                    for slot in option.slots
                ],
            }
            for option in experience.options
        ],
    }


async def event_history(
    anonymous_id: str, limit: int = 20, data: DemoStore = store
) -> list[tuple[str, UUID]]:
    if not database_mode():
        return data.event_experiences.get(anonymous_id, [])[-limit:]
    factory = require_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(BehaviorEvent.event_type, BehaviorEvent.experience_id)
            .join(ShoppingSession, ShoppingSession.id == BehaviorEvent.session_id)
            .where(
                ShoppingSession.anonymous_id == anonymous_id,
                BehaviorEvent.experience_id.is_not(None),
            )
            .order_by(BehaviorEvent.occurred_at.desc())
            .limit(limit)
        )
        return [
            (event_type, experience_id)
            for event_type, experience_id in reversed(rows.all())
            if experience_id is not None
        ]
