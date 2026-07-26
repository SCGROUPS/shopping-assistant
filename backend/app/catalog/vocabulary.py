"""The one list of categories the catalogue is allowed to contain.

This lived in the importer, next to a comment promising it used "the same
language as seeded supply". It did not. The seed listings were written with a
longer vocabulary - "Food experience", "Transport ticket" - while the importer
offered "Food" and "Transport", and both spellings reached production. Nothing
compared them, so the divergence survived every test run.

The cost was not cosmetic. Category is offered to the intent model as an enum
built from whatever the catalogue happens to contain, so the model saw both
spellings and reasonably preferred the descriptive one. "Food experience" had a
single listing in the entire country and none at all in Ho Chi Minh City, so
`food tour in ho chi minh city` returned an empty page while seventy Food
listings sat unqueried, four of them in that city.

A synonym table in the search service would have hidden this, and would have put
a business judgement - that these two words mean one thing - in the one place
that must not hold it. The vocabulary is a property of the catalogue, so it is
declared here, imported by everything that writes a category, and enforced by
`test_vocabulary.py` against the seed corpus.
"""

CATEGORIES = [
    "Activity or class",
    "Cruise",
    "Culture",
    "Day trip",
    "Entertainment experience",
    "Family",
    "Food",
    "Guided tour",
    "Nature",
    "Open-dated voucher",
    "Transport",
    "Water",
    "Wellness",
]
