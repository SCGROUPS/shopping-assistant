"""Display-currency conversion.

Tourists compare prices in their home currency; a wall of seven-digit VND
figures is a real friction point at the moment of decision. This module
converts for **display only** — `price`/`currency` on every card remain the
authoritative VND values, and every cart, checkout and booking calculation
stays in them. Money that is charged must never depend on a presentation-layer
rate table.

Rates are static and approximate (`docs/SYSTEM_DESIGN.md` §16 tracks the open
question of a real FX source). They are deliberately not used for anything that
takes payment.
"""

from __future__ import annotations

BASE_CURRENCY = "VND"

# Units of the target currency per 1 VND.
RATES: dict[str, float] = {
    "VND": 1.0,
    "USD": 1 / 25_400,
    "EUR": 1 / 27_500,
    "GBP": 1 / 32_200,
    "AUD": 1 / 16_700,
    "SGD": 1 / 18_900,
    "KRW": 1 / 18.5,
    "JPY": 1 / 165,
}

# Currencies conventionally written without decimals.
ZERO_DECIMAL = frozenset({"VND", "KRW", "JPY"})

SUPPORTED = tuple(RATES)


def supported(currency: str | None) -> bool:
    return bool(currency) and currency.upper() in RATES


def convert(amount: float, source: str, target: str) -> float:
    """Convert between any two supported currencies via the VND base."""
    source, target = source.upper(), target.upper()
    if source == target:
        return amount
    if source not in RATES or target not in RATES:
        raise ValueError(f"Unsupported currency pair {source}->{target}")
    in_base = amount / RATES[source]
    converted = in_base * RATES[target]
    return round(converted, 0 if target in ZERO_DECIMAL else 2)
