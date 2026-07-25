"""Business configuration held as data.

Ranking weights, commercial take rates and assistant policy were environment
variables read through an `lru_cache`, so retuning any of them meant a release
and a restart, applied to every shopper at once. That is not a control loop -
it is a deployment pipeline wearing one.

Settings now live in `business_settings` and are read through a short-TTL
cache. Environment values remain the schema defaults and the fallback, so an
empty table behaves exactly as before and a database outage degrades to the
deployed configuration rather than to nothing.

Every setting is declared here with a validator. Configuration a person can
edit at runtime is configuration a person can get wrong at runtime, so the
bounds are part of the definition rather than a comment.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.common.config import get_settings
from app.common.database import session_factory
from app.common.models import BusinessSetting

CACHE_TTL_SECONDS = 30.0


class ConfigError(ValueError):
    """A proposed setting value is not usable."""


def _weights(minimum: int) -> Callable[[Any], dict[str, float]]:
    def validate(value: Any) -> dict[str, float]:
        if not isinstance(value, dict) or len(value) < minimum:
            raise ConfigError(f"Expected an object of at least {minimum} named weights")
        cleaned: dict[str, float] = {}
        for name, weight in value.items():
            if not isinstance(weight, int | float) or isinstance(weight, bool):
                raise ConfigError(f"Weight '{name}' must be a number")
            if not 0.0 <= float(weight) <= 1.0:
                raise ConfigError(f"Weight '{name}' must be between 0 and 1")
            cleaned[str(name)] = float(weight)
        total = sum(cleaned.values())
        # Ranking compares items against each other, so the scale cancels and
        # the sum need not be exactly 1. A wildly off total means someone has
        # made an arithmetic slip, not a deliberate choice.
        if not 0.5 <= total <= 1.5:
            raise ConfigError(f"Weights sum to {total:.2f}; expected roughly 1.0")
        return cleaned

    return validate


def _rates(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ConfigError("Expected an object mapping category to take rate")
    cleaned: dict[str, float] = {}
    for name, rate in value.items():
        if not isinstance(rate, int | float) or isinstance(rate, bool):
            raise ConfigError(f"Take rate for '{name}' must be a number")
        if not 0.0 <= float(rate) <= 0.6:
            raise ConfigError(f"Take rate for '{name}' must be between 0 and 0.6")
        cleaned[str(name)] = float(rate)
    return cleaned


def _bounded_float(low: float, high: float) -> Callable[[Any], float]:
    def validate(value: Any) -> float:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ConfigError("Expected a number")
        if not low <= float(value) <= high:
            raise ConfigError(f"Expected a number between {low} and {high}")
        return float(value)

    return validate


def _text(limit: int) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if not isinstance(value, str):
            raise ConfigError("Expected text")
        if len(value) > limit:
            raise ConfigError(f"Expected at most {limit} characters")
        return value

    return validate


@dataclass(frozen=True)
class SettingSpec:
    key: str
    description: str
    validate: Callable[[Any], Any]
    default: Callable[[], Any]


def _default_search_weights() -> dict[str, float]:
    s = get_settings()
    return {
        "relevance": s.search_weight_relevance,
        "preference_fit": s.search_weight_preference_fit,
        "availability_fit": s.search_weight_availability_fit,
        "price_fit": s.search_weight_price_fit,
        "quality": s.search_weight_quality,
        "conversion": s.search_weight_conversion,
        "margin": s.search_weight_margin,
    }


def _default_recommendation_weights() -> dict[str, float]:
    s = get_settings()
    return {
        "session": s.recommendation_weight_session,
        "context_fit": s.recommendation_weight_context_fit,
        "item_similarity": s.recommendation_weight_item_similarity,
        "availability_fit": s.recommendation_weight_availability_fit,
        "popularity": s.recommendation_weight_popularity,
        "quality": s.recommendation_weight_quality,
    }


def _default_take_rates() -> dict[str, float]:
    from app.common.features import CATEGORY_TAKE_RATE

    return dict(CATEGORY_TAKE_RATE)


SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            "search_weights",
            "How search trades relevance against fit, quality and commercial value.",
            _weights(minimum=5),
            _default_search_weights,
        ),
        SettingSpec(
            "recommendation_weights",
            "How the recommendation rail trades session signal against popularity and fit.",
            _weights(minimum=4),
            _default_recommendation_weights,
        ),
        SettingSpec(
            "category_take_rates",
            "Commission proxy per category, the commercial value term in ranking.",
            _rates,
            _default_take_rates,
        ),
        SettingSpec(
            "max_merchandising_boost",
            "Ceiling on a promotion multiplier, so a campaign cannot replace relevance.",
            _bounded_float(1.0, 3.0),
            lambda: 1.5,
        ),
        SettingSpec(
            "assistant_policy",
            "Business rules appended to the assistant's instructions: tone, "
            "disclosures, what to refuse.",
            _text(4000),
            lambda: "",
        ),
        SettingSpec(
            "recommendation_mmr_lambda",
            "Relevance against variety in the recommendation rail. Lower shows more variety.",
            _bounded_float(0.0, 1.0),
            lambda: get_settings().recommendation_mmr_lambda,
        ),
    )
}


class ConfigCache:
    """Short-TTL cache in front of `business_settings`.

    Ranking reads configuration on every request, so this cannot be a database
    round trip each time. Thirty seconds is long enough to be free and short
    enough that an operator sees their change take effect while still looking
    at the screen.
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._loaded_at = 0.0

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def _refresh(self) -> None:
        if session_factory is None:
            self._values = {}
            self._loaded_at = time.monotonic()
            return
        try:
            async with session_factory() as session:
                rows = (await session.scalars(select(BusinessSetting))).all()
            self._values = {row.key: row.value for row in rows}
        except Exception:
            # Configuration must never be the reason the storefront is down.
            # Serving the deployed defaults is a correct, if stale, answer.
            self._values = {}
        self._loaded_at = time.monotonic()

    async def all(self) -> dict[str, Any]:
        if time.monotonic() - self._loaded_at > CACHE_TTL_SECONDS:
            await self._refresh()
        resolved: dict[str, Any] = {}
        for key, spec in SPECS.items():
            if key in self._values:
                try:
                    resolved[key] = spec.validate(self._values[key])
                    continue
                except ConfigError:
                    # A stored value that no longer validates - a schema change,
                    # say - must not take ranking down with it.
                    pass
            resolved[key] = spec.default()
        return resolved

    async def get(self, key: str) -> Any:
        return (await self.all())[key]


cache = ConfigCache()


async def get_config() -> dict[str, Any]:
    return await cache.all()


async def get_value(key: str) -> Any:
    return await cache.get(key)


def validate(key: str, value: Any) -> Any:
    spec = SPECS.get(key)
    if spec is None:
        raise ConfigError(f"Unknown setting '{key}'")
    return spec.validate(value)


def defaults() -> dict[str, Any]:
    return {key: spec.default() for key, spec in SPECS.items()}
