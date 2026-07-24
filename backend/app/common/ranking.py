import hashlib
import math
import re
from collections.abc import Iterable, Sequence

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold())


def deterministic_embedding(text: str, dimensions: int = 512) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode()).digest()
        for offset in range(0, 16, 2):
            index = int.from_bytes(digest[offset : offset + 2], "big") % dimensions
            vector[index] += 1.0 if digest[offset] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def reciprocal_rank_fusion(
    lexical_ids: Iterable[str], semantic_ids: Iterable[str], k: int = 60
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in (lexical_ids, semantic_ids):
        for rank, item_id in enumerate(ranked, 1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


def bayesian_rating(rating: float, reviews: int, mean: float = 4.6, minimum: int = 20) -> float:
    return ((reviews / (reviews + minimum)) * rating) + ((minimum / (reviews + minimum)) * mean)


def minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def mmr_diversify(
    candidates: list[tuple[str, float, Sequence[float], str]],
    limit: int,
    lambda_: float = 0.75,
) -> list[str]:
    selected: list[tuple[str, float, Sequence[float], str]] = []
    remaining = candidates.copy()
    while remaining and len(selected) < limit:
        eligible = [
            item for item in remaining if sum(chosen[3] == item[3] for chosen in selected) < 2
        ]
        if not eligible:
            eligible = remaining
        choice = max(
            eligible,
            key=lambda item: (
                lambda_ * item[1]
                - (1 - lambda_)
                * max(
                    (cosine_similarity(item[2], chosen[2]) for chosen in selected),
                    default=0.0,
                )
            ),
        )
        selected.append(choice)
        remaining.remove(choice)
    return [item[0] for item in selected]
