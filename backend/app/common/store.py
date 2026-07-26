import pickle
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.catalog.seed import build_seed_catalog
from app.common.ranking import deterministic_embedding

# The seeded catalogue, built once.
#
# `reset()` runs before every single test, and it used to rebuild all 360
# products and hash a 512-dimension embedding for each one - about 0.3s, which
# across the suite was more time than every test body put together. The
# catalogue is deterministic, so it only has to be built once; what each test
# needs is its own *copy* to mutate.
_TEMPLATE: list[dict[str, Any]] = []
_EMBEDDINGS: dict[UUID, list[float]] = {}
_BLOB: bytes = b""


def _seed_blob() -> bytes:
    """The template as bytes, because unpickling beats `deepcopy` six to one.

    `deepcopy` has to memoise every object it walks to preserve shared
    references; there are none here worth preserving, so all that bookkeeping is
    pure cost. A pickle round-trip produces the same independent tree in about
    a sixth of the time, and this runs before every test in the suite.
    """
    global _BLOB
    if not _BLOB:
        _BLOB = pickle.dumps(_seed_template(), protocol=pickle.HIGHEST_PROTOCOL)
    return _BLOB


def _seed_template() -> list[dict[str, Any]]:
    if _TEMPLATE:
        return _TEMPLATE
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
        _EMBEDDINGS[product["id"]] = deterministic_embedding(document)
        # Kept out of the template so the deep copy never walks it.
        product.pop("embedding", None)
        _TEMPLATE.append(product)
    return _TEMPLATE


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
        for product in pickle.loads(_seed_blob()):  # noqa: S301 - our own bytes
            # Restored by reference rather than copied. 360 products of 512
            # floats is most of the weight of a copy, and no code path writes to
            # a vector - they are only ever read for a similarity.
            product["embedding"] = _EMBEDDINGS[product["id"]]
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
