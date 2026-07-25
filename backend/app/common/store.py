from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.catalog.seed import build_seed_catalog
from app.common.ranking import deterministic_embedding


@dataclass
class DemoStore:
    products: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    carts: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    bookings: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str, str], Any] = field(default_factory=dict)
    event_experiences: dict[str, list[tuple[str, UUID, datetime]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def seed(self, force: bool = False) -> int:
        if self.products and not force:
            return len(self.products)
        self.products.clear()
        for product in build_seed_catalog():
            document = " ".join(
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
            product["search_document"] = document
            product["embedding"] = deterministic_embedding(document)
            self.products[product["id"]] = product
        return len(self.products)

    def session(self, anonymous_id: str) -> dict[str, Any]:
        if anonymous_id not in self.sessions:
            self.sessions[anonymous_id] = {
                "id": uuid4(),
                "anonymous_id": anonymous_id,
                "currency": "VND",
                "preference_state": {},
                "created_at": datetime.now(UTC),
                "last_seen_at": datetime.now(UTC),
            }
        self.sessions[anonymous_id]["last_seen_at"] = datetime.now(UTC)
        return self.sessions[anonymous_id]

    def reset(self) -> None:
        self.products.clear()
        self.sessions.clear()
        self.carts.clear()
        self.conversations.clear()
        self.events.clear()
        self.bookings.clear()
        self.idempotency.clear()
        self.event_experiences.clear()
        self.seed()


store = DemoStore()
