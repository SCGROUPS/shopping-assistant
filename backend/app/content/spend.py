"""A translation budget that survives a restart, because the job restarts a lot.

The in-memory cost ledger is per process, and the translation worker is a
scheduled container: a new process every two hours. A "$25 daily" ceiling
enforced there is $25 *per run* — three hundred a day at this cadence, more with
overlapping or manual executions. A ceiling that resets whenever the thing it
constrains restarts is not a ceiling.

Spend is **reserved before the call and reconciled after**. Checking a total and
then spending against it lets eight concurrent lanes all pass a check that none
of them has paid for yet; the reservation is a single conditional UPDATE, so at
most one lane can be the one that crosses the line.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Charged optimistically before a call whose cost is not yet known, then
# corrected. Deliberately generous: over-reserving delays work by one cycle,
# under-reserving is how a ceiling gets crossed.
ESTIMATE = Decimal("0.01")

# Returned when no budget is configured: truthy, so callers proceed, but never a
# real day, so nothing is settled against a row that was never charged.
UNMETERED = date.min


async def reserve(
    session: AsyncSession, budget: float, amount: Decimal = ESTIMATE
) -> date | None:
    """Claim `amount` of today's budget, returning the day charged, or None.

    One statement. The `WHERE` runs against the row as it exists at write time,
    not as it was read, which is the whole difference between a ceiling and a
    suggestion.

    The day is returned rather than recomputed by the caller because a call that
    starts at 23:59:59 finishes on the next one: settling "today" would credit a
    day that was never charged and leave the charged day overstated.
    """
    if budget <= 0:
        return UNMETERED
    if Decimal(str(budget)) < amount:
        # The INSERT branch has no existing row to compare against, so a budget
        # smaller than a single reservation would let the first call of the day
        # through and only start refusing afterwards. Refuse it here instead.
        return None

    claimed = await session.execute(
        text(
            """
            INSERT INTO translation_spend (day, amount, updated_at)
            VALUES ((now() AT TIME ZONE 'utc')::date, :amount, now())
            ON CONFLICT (day) DO UPDATE
              SET amount = translation_spend.amount + :amount,
                  updated_at = now()
              WHERE translation_spend.amount + :amount <= :budget
            RETURNING day
            """
        ),
        {"amount": amount, "budget": Decimal(str(budget))},
    )
    return claimed.scalar_one_or_none()


async def settle(
    session: AsyncSession, day: date | None, reserved: Decimal, actual: Decimal | None
) -> None:
    """Correct a reservation once the real cost is known.

    Allowed to push the day's total *above* the ceiling: the money is already
    spent, and a ledger that lies about it is worse than one that reports an
    overrun. The next reservation simply fails.

    `actual is None` means the provider did not report usage. The reservation is
    kept: a call whose cost we cannot see still cost something, and crediting it
    back would mean the budget never advances at all on a provider that omits
    usage - a ceiling that silently stops counting.
    """
    if day is None or day == UNMETERED or actual is None:
        return
    delta = actual - reserved
    if not delta:
        return
    await session.execute(
        text(
            """
            UPDATE translation_spend
               SET amount = GREATEST(amount + :delta, 0), updated_at = now()
             WHERE day = :day
            """
        ),
        {"delta": delta, "day": day},
    )


async def spent_today(session: AsyncSession) -> Decimal:
    value = await session.scalar(
        text(
            "SELECT amount FROM translation_spend "
            "WHERE day = (now() AT TIME ZONE 'utc')::date"
        )
    )
    return Decimal(value or 0)
