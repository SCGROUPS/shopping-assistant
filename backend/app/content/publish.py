"""What it means for a translation to be *published*, in one place.

Three separate code paths make a translated string visible to a shopper: the
worker auto-publishing a field that needs no review, a reviewer approving a
held candidate, and a human typing a translation directly. Each one has to do
the same two things afterwards, and neither is optional:

1. write the served text into the wide translation row, one column at a time;
2. enqueue a reindex for that locale, in the same transaction.

Step 2 is the one that gets forgotten, because skipping it breaks nothing that
any test or screen would show: the field reads correctly on the product page
and reports itself `current`, while the search document for that locale still
holds the old text. The product is translated and unfindable, which is the same
outcome as not translating it, arrived at more expensively.

The column-specific write in step 1 is not a style choice. An ORM whole-row
flush writes all four columns, so a reviewer approving `meeting_point` at the
same moment a worker publishes `title` would write each other's column back to
whatever they loaded, and one of the two would vanish with nothing raised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.indexing import enqueue_experience_reindex
from app.common.models import ExperienceTranslation

_WIDE_COLUMNS = {
    field: getattr(ExperienceTranslation, field)
    for field in ("title", "short_description", "description", "meeting_point")
}


async def publish_experience_translation(
    session: AsyncSession,
    *,
    experience_id: uuid.UUID,
    field: str,
    locale: str,
    value: str,
) -> None:
    """Make one translated field visible, and findable, for one locale.

    Callers must already hold whatever lock protects the `translation_fields`
    row, and must have decided that publishing is correct. This function does
    not decide - it only guarantees that the two consequences of that decision
    happen together.
    """
    column = _WIDE_COLUMNS[field]
    await session.execute(
        pg_insert(ExperienceTranslation)
        .values(experience_id=experience_id, locale=locale, **{field: value})
        .on_conflict_do_update(
            index_elements=[
                ExperienceTranslation.experience_id,
                ExperienceTranslation.locale,
            ],
            set_={column.key: value, "updated_at": datetime.now(UTC)},
        )
    )
    await enqueue_experience_reindex(session, experience_id, locales=[locale])
