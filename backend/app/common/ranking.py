import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable, Sequence

# ``[^\W_]`` is "word character but not underscore", which under Python's
# default Unicode semantics keeps letters and digits in every script. The
# previous ``[a-z0-9]+`` silently discarded everything else: Chinese produced no
# tokens at all and therefore a zero embedding, while Vietnamese and French were
# shredded at each accent ("thuyền" became "thuy" + "n").
TOKEN_RE = re.compile(r"[^\W_]+")

# Scripts written without spaces between words. One token per run would make
# "日落游船" unmatchable by the query "日落", so these are cut into overlapping
# character bigrams instead - the same trick a CJK search analyser uses.
_CJK_RANGES = (
    (0x3040, 0x30FF),  # hiragana, katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xAC00, 0xD7AF),  # hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0x20000, 0x2A6DF),  # CJK extension B
)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(low <= codepoint <= high for low, high in _CJK_RANGES)


# Letters whose diacritic is a stroke or ligature rather than a combining mark,
# so NFD leaves them untouched. Vietnamese "đ" is the one that matters here:
# without this, "Da Nang" and "Đà Nẵng" tokenise differently and the Python
# lexical vector disagrees with the PostgreSQL index, which unaccents them.
# Values match ``SELECT unaccent(...)`` so both sides stay in step.
_STROKE_FOLDING = str.maketrans(
    {
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ı": "i",
        "ŋ": "n",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "þ": "th",
        "Þ": "TH",
    }
)


def strip_accents(text: str) -> str:
    """Fold diacritics the way PostgreSQL's ``unaccent`` does.

    Applied to documents and queries alike, so it costs a little precision in
    Vietnamese and buys the far larger recall win of matching shoppers who type
    without diacritics. Asymmetry would be the bug; folding both sides is not.
    """
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char)).translate(
        _STROKE_FOLDING
    )
    # Recompose, or Hangul stays as decomposed jamo: NFD splits 크 into ㅋ + ㅡ,
    # which are not in the ranges below, so Korean would skip bigramming
    # entirely and match only on whole words.
    return unicodedata.normalize("NFC", stripped)


def _split_cjk(token: str) -> list[str]:
    parts: list[str] = []
    run: list[str] = []
    cjk_run = False
    for character in token:
        if _is_cjk(character) != cjk_run and run:
            parts.extend(_emit(run, cjk_run))
            run = []
        cjk_run = _is_cjk(character)
        run.append(character)
    if run:
        parts.extend(_emit(run, cjk_run))
    return parts


def _emit(run: list[str], cjk: bool) -> list[str]:
    if not cjk:
        return ["".join(run)]
    if len(run) == 1:
        return run
    return ["".join(run[index : index + 2]) for index in range(len(run) - 1)]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(strip_accents(text.casefold())):
        tokens.extend(_split_cjk(token))
    return tokens


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


def time_decay(age_seconds: float, half_life_seconds: float) -> float:
    """Exponential decay used to age behavioural signals.

    Tourists book for today or tomorrow, so a view from ten minutes ago says far
    more about intent than one from yesterday.
    """
    if half_life_seconds <= 0:
        return 1.0
    return 0.5 ** (max(age_seconds, 0.0) / half_life_seconds)


def smoothed_rate(
    successes: float, trials: float, prior_rate: float, prior_strength: float
) -> float:
    """Bayesian-smoothed rate so unproven inventory is not starved.

    With no observations this returns the prior, which means a brand new
    experience ranks as an average performer rather than the worst one.
    """
    return (successes + prior_rate * prior_strength) / (trials + prior_strength)
