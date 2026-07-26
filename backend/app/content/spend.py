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

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Charged optimistically before a call whose cost is not yet known, then
# corrected. Deliberately generous: over-reserving delays work by one cycle,
# under-reserving is how a ceiling gets crossed.
ESTIMATE = Decimal("0.01")


async def reserve(session: AsyncSession, budget: float, amount: Decimal = ESTIMATE) -> bool:
    """Claim `amount` of today's budget, or return False having claimed nothing.

    One statement. The `WHERE` runs against the row as it exists at write time,
    not as it was read, which is the whole difference between a ceiling and a
    suggestion.
    """
    if budget <= 0:
        return True

    claimed = await session.execute(
        text(
            """
            INSERT INTO translation_spend (day, amount, updated_at)
            VALUES ((now() AT TIME ZONE 'utc')::date, :amount, now())
            ON CONFLICT (day) DO UPDATE
              SET amount = translation_spend.amount + :amount,
                  updated_at = now()
              WHERE translation_spend.amount + :amount <= :budget
            RETURNING amount
            """
        ),
        {"amount": amount, "budget": Decimal(str(budget))},
    )
    return claimed.scalar_one_or_none() is not None


async def settle(session: AsyncSession, reserved: Decimal, actual: Decimal) -> None:
    """Correct a reservation once the real cost is known.

    Allowed to push the day's total *above* the ceiling: the money is already
    spent, and a ledger that lies about it is worse than one that reports an
    overrun. The next reservation simply fails.
    """
    delta = actual - reserved
    if not delta:
        return
    await session.execute(
        text(
            """
            UPDATE translation_spend
               SET amount = GREATEST(amount + :delta, 0), updated_at = now()
             WHERE day = (now() AT TIME ZONE 'utc')::date
            """
        ),
        {"delta": delta},
    )


async def spent_today(session: AsyncSession) -> Decimal:
    value = await session.scalar(
        text(
            "SELECT amount FROM translation_spend "
            "WHERE day = (now() AT TIME ZONE 'utc')::date"
        )
    )
    return Decimal(value or 0)
