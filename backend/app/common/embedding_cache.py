"""Shared, persistent cache for query embeddings.

`POC_SPEC.md` §11.4 specifies a persistent LRU `query_embedding_cache` table;
the POC used a process-local dict, so every replica paid to embed the same
queries and the cache emptied on every deploy. Tourist search traffic is
extremely head-heavy ("things to do in Hoi An" arrives constantly), which is
exactly the shape where a shared cache pays for itself.

Two tiers: a small in-process dict absorbs the hot head without a round trip,
and the table shares everything else across replicas and restarts.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.common.database import session_factory
from app.common.models import QueryEmbeddingCache

logger = logging.getLogger(__name__)

LOCAL_CACHE_LIMIT = 512
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold trivially different queries onto one cache entry.

    "Hoi An  lantern tour" and "hoi an lantern tour" are the same request and
    must not be embedded twice.
    """
    return _WHITESPACE.sub(" ", text.strip().casefold())


def cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x00{normalize(text)}".encode()).hexdigest()


class EmbeddingCache:
    def __init__(self, limit: int = LOCAL_CACHE_LIMIT) -> None:
        self._local: OrderedDict[str, list[float]] = OrderedDict()
        self._limit = limit

    def get_local(self, key: str) -> list[float] | None:
        vector = self._local.get(key)
        if vector is not None:
            self._local.move_to_end(key)
        return vector

    def put_local(self, key: str, vector: list[float]) -> None:
        self._local[key] = vector
        self._local.move_to_end(key)
        while len(self._local) > self._limit:
            self._local.popitem(last=False)

    async def get(self, text: str, model: str) -> list[float] | None:
        key = cache_key(text, model)
        local = self.get_local(key)
        if local is not None:
            return local
        if session_factory is None:
            return None
        try:
            async with session_factory() as db:
                row = await db.scalar(
                    select(QueryEmbeddingCache).where(QueryEmbeddingCache.key == key)
                )
                if row is None:
                    return None
                vector = list(row.embedding)
                await db.execute(
                    update(QueryEmbeddingCache)
                    .where(QueryEmbeddingCache.key == key)
                    .values(
                        last_accessed_at=datetime.now(UTC),
                        access_count=QueryEmbeddingCache.access_count + 1,
                    )
                )
                await db.commit()
        except Exception:
            # A cache is an optimization; never let it break a search.
            logger.exception("Embedding cache read failed")
            return None
        self.put_local(key, vector)
        return vector

    async def put(self, text: str, model: str, vector: list[float]) -> None:
        key = cache_key(text, model)
        self.put_local(key, vector)
        if session_factory is None:
            return
        try:
            async with session_factory() as db:
                existing = await db.scalar(
                    select(QueryEmbeddingCache.key).where(QueryEmbeddingCache.key == key)
                )
                if existing is None:
                    db.add(
                        QueryEmbeddingCache(
                            key=key,
                            normalized_text=normalize(text),
                            model=model,
                            embedding=vector,
                        )
                    )
                    await db.commit()
        except Exception:
            logger.exception("Embedding cache write failed")


embedding_cache = EmbeddingCache()
