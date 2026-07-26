"""The seed corpus and the importer must speak one language.

They did not, and nothing noticed. This is the test that would have.
"""

from app.catalog.seed import EXPERIENCE_TEMPLATES, PRODUCTS
from app.catalog.vocabulary import CATEGORIES


def test_every_seeded_category_is_one_the_importer_would_produce():
    """A category no importer can emit is one no shopper can reliably reach.

    Category reaches the intent model as an enum built from the live catalogue,
    so a spelling that exists in only the seed data is still offered to the
    model as if it were a real choice. The model picks the most descriptive
    option, which is exactly the one with almost no inventory behind it.
    """
    used = sorted(
        {row["category"] for row in PRODUCTS} | {row["category"] for row in EXPERIENCE_TEMPLATES}
    )
    unknown = [category for category in used if category not in CATEGORIES]

    assert unknown == [], (
        f"seed listings use categories the importer cannot produce: {unknown}. "
        f"Add them to app/catalog/vocabulary.py or spell them the importer's way."
    )
