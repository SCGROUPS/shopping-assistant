# Vietra — content pipeline: sourcing, authoring, translation, discovery

Status: specification, revision 7. Supersedes the sourcing assumptions in
`SYSTEM_DESIGN.md` §4.1.

Revisions 2 to 7 each incorporate an independent design review. Where earlier
revisions were wrong about the existing system, §13 records what they claimed
and what is actually true, because the corrections are more instructive than the
final text — and because two of them turned out to describe live defects (§1.2,
§1.4) rather than merely bad plans.

---

## 1. Why this document exists

The system was built on an assumption nobody wrote down: **that supply arrives
from a supplier feed and operators merely correct it.** Every catalogue decision
followed from it — the importer owns a fixed set of supplier fields and rewrites
them on every run, `experience_overrides` exists to defend human edits against
that rewrite, the review queue exists because a classifier was unsure, and the
console is an editor that cannot create anything.

That assumption is wrong. Trippass is *reference inventory* — real data, useful
for proving retrieval against something other than synthetic text, but not the
supply model. Real content arrives two ways:

1. **Authored by a human operator**, in Vietnamese or English.
2. **Submitted by a remote operator** (a partner) through an API we expose.

Neither exists today. There is no `POST /experiences` at all: the console can
govern inventory it did not create, and cannot create inventory it would govern.

This is one document rather than three because sourcing, authoring and
translation are a single question — *where does content come from, who owns each
field, and in what language* — and splitting it produces an authoring tool whose
output cannot be translated, or a translation pipeline with no source.

### 1.1 What this invalidates

| Built | Status under the corrected model |
|---|---|
| `experience_overrides` | Premise narrows. It defends against a *feed*. For operator-authored records there is nothing to defend against. Generalise; also extend to child entities and languages (§3.3). |
| Import review queue | Survives and grows. Stops being an exception path and becomes the main workflow. Its single experience-level boolean is no longer sufficient (§7). |
| Trippass importer | Not wasted — a working reference implementation of the partner *pull* path, and its facet classifier is what partner submissions need too. |
| Admin console | Half a tool. Govern is right; create does not exist. |
| Identity, authz, audit | Unchanged and more load-bearing, but the audit actor model must generalise beyond operators (§8.4). |
| Ranking, merchandising, runtime config | Unaffected by content origin. |
| Eval harness | Structurally unable to test non-Latin retrieval (§1.3). |

`SYSTEM_DESIGN.md` §4.1 opens *"Real supply is not: `trippass.py` imports the
Trippass (HeriStep) API…"*, which reads as a claim that Trippass is the supply
model. It must be corrected.

### 1.2 The live defect this fixes

Multilingual support is not only a growth feature. The system is **wrong today**
for non-English shoppers, and wrong silently. Measured against production:

| Query | Language | Top result |
|---|---|---|
| `croisière au coucher du soleil` | French | ✅ Thu Bon River Sunset Cruise |
| `du thuyền hoàng hôn` | Vietnamese | ❌ Hoa Lu Private Arrival Transfer |
| `日落游船` | Chinese | ❌ Tra Que Family Discovery Pass |

Asked in Chinese for a sunset cruise under 1,000,000 VND, the assistant replies
in fluent Chinese that it found nothing suitable — while two sunset cruises sit
inside that budget. The LLM tier is multilingual for free; retrieval is not.

The root cause is not analyser configuration. **The index holds only English
text**, so a Vietnamese or Chinese query has nothing to match. Analyser choice
(§9.2) is secondary tuning.

The severity comes from the grounding discipline: because the agent may only
present products a tool returned, a retrieval failure surfaces as a confident,
well-written "we have nothing for you" — indistinguishable from an empty
catalogue. It does not look like a bug, so nobody reports it.

### 1.3 The eval harness could not detect any of this

`app/common/ranking.py` defined the tokenizer used by demo mode and by the
deterministic embedding as `TOKEN_RE = re.compile(r"[a-z0-9]+")`. Measured
before the fix:

| Input | `tokenize()` output |
|---|---|
| `sunset cruise` | `['sunset', 'cruise']` |
| `日落游船` | `[]` |
| `du thuyền hoàng hôn` | `['du', 'thuy', 'n', 'ho', 'ng', 'h', 'n']` |
| `croisière` | `['croisi', 're']` |

CJK yielded **no tokens and therefore a zero vector**; Vietnamese and French
were shredded at every non-ASCII character. The CI search suite runs against the
in-memory store (`app/evals/runner.py`), so a multilingual case added there
would have passed or failed for reasons unrelated to retrieval — most likely
passing vacuously, because an empty token list degenerates into browsing and
returns results.

**Adding multilingual eval cases to that harness would have produced a green
suite over a broken feature.** That is worse than no coverage.

**Status: fixed.** `tokenize()` now matches Unicode letters and digits, folds
diacritics the way `unaccent` does, and cuts CJK runs into overlapping character
bigrams so `日落` matches `日落游船`. Four tests in `tests/test_ranking.py` pin
this and all four fail against the old pattern; a fifth pins that ASCII
tokenisation is byte-for-byte unchanged, so no existing ranking result moves;
three more pin stroke folding (below). This was done first because it is a
prerequisite for §10 — the gate has to be able to fail before the thing it
gates gets built.

**A second tokenizer defect, found by review of the fix.** Unicode NFD
decomposition folds *combining marks*, and `đ` does not have one: it is a
distinct codepoint with a stroke. So `Đà Nẵng` tokenised as `['đa', 'nang']`
while `da nang` gave `['da', 'nang']` — a shopper typing Vietnam's second city
without diacritics missed it, and the Python lexical vector disagreed with the
PostgreSQL index, which unaccents `đ` to `d`. Fixed with an explicit fold for
stroke and ligature letters (`đ ð ø ł ı ŋ ß æ œ þ`), verified against
`SELECT unaccent(...)` on eighteen real place names with zero mismatches.

---

### 1.4 A second silent defect, found while writing this

Only `catalog/importer.py` and `catalog/db_seed.py` ever construct an
`ExperienceSearchDocument`. `admin/catalog_ops.py:update_experience` sets the
new field values, records the override and writes the audit row — and never
touches the search document or the embedding.

So an operator who corrects a title today changes the product card and changes
nothing about retrieval. PostgreSQL keeps matching the old text, and the vector
keeps encoding it, until the next full import happens to overwrite the
document. On a `manual` record, which no importer may touch (§3.1), that is
**never**.

This is the same failure shape as §1.2 — an edit that appears to work, a search
tier quietly disagreeing, and no error anywhere — and it means §5 cannot ship
without §9.4. Authoring a product that cannot be found is not authoring.

---

## 2. Principles

1. **Provenance before convenience.** Every translated field records who
   produced it, from what, with which prompt and model.
2. **Machine output is a proposal where being wrong costs money.** §6.5 defines
   where that line sits and why it is not drawn at "everything".
3. **Never hide inventory over a missing translation.** Fall back and label.
4. **The source of a record decides who may overwrite it** — one rule replacing
   accumulated special cases.
5. **Reject rather than coerce.** A silently corrected partner field becomes a
   silently wrong listing, and the partner never learns.
6. **If a locale is not covered by an eval that could actually fail, it is not
   supported.** The failure mode here is silence.

---

## 3. Content ownership

### 3.1 Structured source, not a string

Revision 1 proposed `source = 'partner:<slug>'`. That is a text field pretending
to be a foreign key: no referential integrity, no rename safety, and no way to
scope uniqueness. Instead:

```sql
CREATE TABLE partners (
  id           UUID PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active',   -- active | suspended
  locales      TEXT[] NOT NULL DEFAULT '{}',     -- locales this partner may submit
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE partner_keys (
  id          UUID PRIMARY KEY,
  partner_id  UUID NOT NULL REFERENCES partners(id),
  prefix      TEXT NOT NULL UNIQUE,             -- looked up before hashing
  key_salt    TEXT NOT NULL,                    -- scrypt needs both, see below
  key_hash    TEXT NOT NULL,
  capabilities TEXT[] NOT NULL DEFAULT '{}',
  expires_at  TIMESTAMPTZ,
  revoked_at  TIMESTAMPTZ
);

ALTER TABLE experiences
  ADD COLUMN source_type     TEXT NOT NULL DEFAULT 'reference',   -- manual | partner | reference
  ADD COLUMN partner_id      UUID NULL REFERENCES partners(id),
  ADD COLUMN source_language TEXT NOT NULL DEFAULT 'en',
  ADD COLUMN content_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE experiences ADD CONSTRAINT ck_experience_source CHECK (
  (source_type = 'partner' AND partner_id IS NOT NULL) OR
  (source_type <> 'partner' AND partner_id IS NULL)
);
```

Key storage mirrors the operator scheme so there is one hashing path to reason
about, but partner keys add expiry and revocation, which operator keys lack.
`key_salt` is not optional: the existing verifier reads a salt *and* a hash
(`admin/auth.py:81-94`, `123-130`), so a DDL with only `key_hash` cannot use the
path it claims to reuse. Partner keys also carry a distinct textual prefix
namespace (`pk_` versus the operator `vk_`, `admin/auth.py:78`), so a credential presented to the
wrong endpoint fails lookup rather than being ambiguously matched.

**`content_version` is narrower than "every mutation".** Defining it as any
mutation by any actor would make a translation publish, an indexing completion
or a merchandising boost invalidate an unrelated partner approval — the partner
would be told its base changed when nothing it can see did. It is instead the
version of exactly the **source and commerce state that appears in a partner
diff**: source-locale text, options, prices, availability policy, media set.
Derived state (translations, search documents, ranking weights) is excluded.
Every child mutation that appears in a diff bumps the parent in the same
transaction. It is incremented in SQL, never read-then-written:

```sql
UPDATE experiences SET ..., content_version = content_version + 1
 WHERE id = :id AND content_version = :expected
```

| `source_type` | Field owner | Overrides |
|---|---|---|
| `manual` | The operator. No external writer exists. | Not applicable |
| `partner` | The partner, except fields an operator overrode | Apply (§3.3) |
| `reference` | The importer. Freely replaceable demo data. | Apply |

An importer must refuse to touch a `manual` record even on external-id
collision.

### 3.2 Identity constraints that currently block this

`experiences` today requires a globally unique `external_id`, a non-null
`supplier_id`, and a unique `slug` (`common/models.py:58-62`). All three
obstruct the new model:

- **`external_id` globally unique** prevents two partners using the same id.
  Manual records get `external_id NULL` — inventing an identifier for a record
  that has no external identity is how the importer ends up matching one.
  Scope uniqueness to the source: `UNIQUE (partner_id, external_id)` for
  partner content, and a partial unique index on `external_id` where
  `source_type = 'reference'`. The importer's lookup must be scoped the same
  way (`catalog/importer.py:308`), or it will find and overwrite a record from
  a different source. Enforced as a check: `manual` requires
  `external_id IS NULL`; `partner` and `reference` require it non-null.
- **`supplier_id` non-null** forces manual records to invent a supplier. Keep it
  non-null and seed a **house supplier** per environment. The admin console
  inner-joins `Supplier` (`admin/catalog_ops.py:180`) so a null would make
  records vanish from the very screen used to fix them; the storefront does not
  join it at all (`common/persistence.py` references `Supplier` zero times), so
  making it nullable buys less than it costs. The risk a sentinel introduces is
  publishing a product whose fulfilment owner was never chosen, so **the publish
  gate rejects the house supplier** (§5.3). The gate tests
  `suppliers.is_placeholder`, **not** a hardcoded UUID: a constant compared in
  several call sites silently stops protecting anything the day an environment
  is seeded with a different id, and nothing would fail. *Fulfilment supplier*
  and *content owner* stay separate columns regardless.
- **`slug` unique** is fine, but manual creation must generate and
  de-duplicate one; a collision is a 409 naming the conflict, not a silent
  suffix.

### 3.3 Overrides must gain scope

`experience_overrides` keys a flat field-name map by `experience_id`
(`common/models.py:374`). It cannot express *which language* or *which child
entity* was overridden, and option fields and prices are not covered at all —
the importer replaces prices wholesale (`catalog/importer.py:131`).

Encoding a richer key inside the existing JSONB — `option:<id>.name@en` — would
give no entity validation, no referential integrity, and no way for two
concurrent edits to touch different fields without a read-modify-write race.
Normalize:

```sql
CREATE TABLE content_overrides (
  experience_id UUID NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
  entity_type   TEXT NOT NULL,          -- experience | option | price | media
  entity_id     UUID NOT NULL,          -- equals experience_id for the parent
  field         TEXT NOT NULL,
  locale        TEXT NOT NULL,          -- '*' for language-neutral fields
  updated_by    TEXT NOT NULL,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (experience_id, entity_type, entity_id, field, locale)
);
```

`experience_id` is denormalized onto every row so the importer can load one
record's entire override set in a single query, which is the access pattern that
matters.

**Integrity here is application-enforced, and that is a deliberate choice.**
`entity_id` is polymorphic, so no single foreign key can constrain it, and
revision 3 overclaimed when it said normalizing "solves entity validation". The
honest position: `experience_id` carries a real FK with `ON DELETE CASCADE`, so
deleting an experience can never orphan an override; within a record, deleting
an option or media row must delete the override and translation-workflow rows
addressing it **in the same transaction**, and that is a rule in the service
layer with a test, not a constraint. The alternative — a `content_entities`
registry giving every child a row to point at — buys database-level integrity
at the cost of a join on every write path, and is worth revisiting only if the
application-layer rule proves leaky. A periodic reconciliation job reports
orphans rather than assuming there are none.

The same applies to `translation_fields.entity_id`, with one exception:
taxonomy terms had no UUID at all, so `entity_type = 'taxonomy'` could not name
its target. §4.2 gives them one.

The `*` locale means *language-neutral*, and applies only to fields that are
genuinely so — price, status, capacity, merchandising. Prose overrides are
recorded against a real locale: an operator editing the source text overrides
`source_language`, never `*`. Migration maps existing prose overrides to each
record's `source_language` and the rest to `*`.

`DELETE /admin/experiences/{id}/overrides/{field}` becomes explicit in entity,
field and locale rather than a single path segment (`admin/routes.py:106`).

An operator correcting Vietnamese phrasing has no opinion about Korean, and
freezing all eight locales from one edit is the failure this table exists to
prevent, inverted.

### 3.4 Locale set

Active locales become runtime configuration — a new `enabled_locales` setting,
which does not exist today (`common/runtime_config.py`) — so adding one is an
operator action plus a backfill, not a deploy. Initial value:

```
en · vi · zh · ja · ko · fr · de · es
```

`en` is not special beyond being the last-resort fallback.

### 3.5 Naming: `locale`, never `language`

`SearchFilters.language` already exists (`api/schemas.py:16-32`, applied at
`search/service.py:258`) and means **the language the tour is conducted in**,
matched against `product["languages"]`. Reusing that name for shopper locale is
not a style question: it would filter Chinese-speaking shoppers to
Chinese-*guided* tours, which is a plausible-looking result set and therefore a
bug that survives review.

Shopper locale is `locale` everywhere — request context, service signatures,
search documents, translations, events. The tour-language facet keeps
`language`.

---

## 4. Canonical text

Stated explicitly, because leaving it implicit guarantees that readers and
writers disagree:

**The base columns on `experiences` hold the source-language text and remain
authoritative for `source_language`.** `experience_translations` holds *target*
locales only. There is no row for the source locale.

The alternative — every locale in the translation table, base columns retired —
is cleaner in the abstract and would touch every existing read path, migration
and test. Not worth it.

### 4.1 `source_language` is immutable

The asymmetry above has a sharp edge: if `source_language` could change from
`vi` to `en`, then base columns and an existing `en` translation row both claim
authority, the old Vietnamese text has nowhere to live, and every override,
translation and search document is keyed to an assumption that just moved.

`source_language` is therefore **fixed at creation**. Changing it is not an
edit; it is a *rebase*, and until there is a reason to build one the API
rejects it with a message saying so. A rebase, if ever built, is one locked
transaction that swaps base and translation values, moves overrides between
locales, invalidates every target locale and rebuilds all search documents.
Nothing less is correct, and half of it is worse than refusing.

### 4.2 Translatable content

Revision 1 translated four fields. Shoppers also see option names and
descriptions, category and destination labels, interest tags, accessibility
notes and cancellation wording (`catalog/service.py`, `common/models.py:100`).
A storefront advertised as Vietnamese that renders English option names is not
a Vietnamese storefront.

| Content | Treatment |
|---|---|
| `title`, `short_description`, `description`, `meeting_point` | `experience_translations` |
| Option `name`, `description` | `option_translations`, same shape |
| Media `alt_text` | `media_translations` — accessibility gets the same treatment as sighted browsing |
| Category, destination, indoor/outdoor, accessibility features | **Taxonomy terms plus localized labels**, see below. These are enums used for filtering and must not become free text per locale, or facets stop matching |
| `interest_tags`, `subcategories` | Same taxonomy treatment. They are displayed on cards *and* matched during search (`search/service.py:330`), so translating them as prose would break matching while translating them not at all leaves visible English on a Vietnamese card |
| Badges and generated recommendation reasons | Composed from templates per locale, not translated after the fact — they are generated text, and translating generated text twice is how "Chỉ còn 2 chỗ" becomes an English sentence with Vietnamese punctuation |
| Cancellation terms | Policy **code** plus localized wording; the code drives logic, the wording is displayed |
| `minimum_age`, prices, durations, coordinates | Numeric or language-neutral. Never translated. Formatted per locale at render |

Everything language-neutral — price, rating, coordinates, duration, capacity,
status, merchandising, availability — stays on `experiences`. This is what keeps
filtering, ranking and commerce locale-independent, so ranking is not duplicated
eight times.

**Taxonomy needs a term table, not just a label table.** A
`taxonomy_labels(kind, code, locale, label)` keyed by text cannot be addressed
by `translation_fields.entity_id`, which is a UUID — so the one content type
most obviously in need of translation workflow was the one type that could not
participate in it. It also left the valid vocabulary undefined: a labels table
alone says what `category = 'cruise'` is *called*, never whether `cruise` is a
code anyone agreed on.

```sql
CREATE TABLE taxonomy_terms (
  id        UUID PRIMARY KEY,
  kind      TEXT NOT NULL,      -- category | destination | interest | accessibility | policy
  code      TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (kind, code)
);

CREATE TABLE taxonomy_labels (
  term_id UUID NOT NULL REFERENCES taxonomy_terms(id) ON DELETE CASCADE,
  locale  TEXT NOT NULL,
  label   TEXT NOT NULL,
  PRIMARY KEY (term_id, locale)
);
```

`is_active` rather than deletion, because a term in use by a live listing must
not vanish; retiring it stops it being offered on new content while leaving
existing rows renderable.

Media alt text, cancellation wording and accessibility notes all reduce to one
of the two shapes above — `media_translations` mirrors `option_translations`,
and cancellation/accessibility are taxonomy terms whose label *is* the displayed
wording — so none of them needs a third mechanism.

### 4.3 The locale contract at the API boundary

Locale is resolved once per request, in this order, first match winning:

1. explicit `locale` query parameter or body field
2. session preference, set by the switcher
3. `Accept-Language`, negotiated against `enabled_locales`
4. `en`

The resolved locale is echoed in every response, because a client that cannot
tell which locale it got cannot tell fallback from translation.

Response models today carry plain strings (`api/schemas.py:88-123`). Per-field
provenance is **additive**: a parallel `content_meta` map of
`field → {locale, provenance}`, not a change to the field types. Existing
clients keep working; the frontend uses the map to label (§6.6).

---

## 5. Authoring

### 5.1 Endpoints

All require `catalog:write`:

```
POST   /admin/experiences                          create a DRAFT
PATCH  /admin/experiences/{id}                      (exists)
POST   /admin/experiences/{id}/options
PATCH  /admin/experiences/{id}/options/{oid}
DELETE /admin/experiences/{id}/options/{oid}        deactivates if referenced
PUT    /admin/experiences/{id}/options/{oid}/prices
PUT    /admin/experiences/{id}/options/{oid}/availability
POST   /admin/experiences/{id}/media
DELETE /admin/experiences/{id}/media/{mid}
```

A created record is `source_type = 'manual'`, `status = 'DRAFT'`, with
`source_language` set to the operator's authoring language.

### 5.2 Option deletion

An option is deactivated rather than deleted if it is referenced by **any cart
or booking**, not merely if it has been sold. An unsold option sitting in a live
cart is exactly the case that breaks: deleting it either fails on the foreign
key or empties a shopper's cart mid-session.

Revision 1 claimed this preserved booking explainability. It does not, and the
reality is worse than revision 1 assumed: cart rendering reads *current*
experience and option names (`cart/service.py:346`), while `BookingView`
(`bookings/service.py:181-208`) carries no listing names **at all** — only a
reference, status, total and voucher. A shopper cannot see what they bought
from their own booking.

Both need `booking_lines`, written at checkout, snapshotting the localized
title, option name, meeting point, price breakdown and accepted cancellation
wording, plus the locale they were served in. A booking that silently
re-describes itself when the catalogue changes is a dispute waiting to happen;
a booking that describes nothing is one already.

### 5.3 The publish gate

`status → PUBLISHED` is refused unless, checked server-side:

- `title`, `short_description`, `description` non-empty in `source_language`
- `destination`, `category`, `duration_minutes`, `meeting_point` set
- at least one **active** option carrying an **adult** price
- at least one image
- `supplier_id` is not a placeholder supplier (§3.2)
- a current source-locale search document exists (§9.4)

The adult-price rule is not arbitrary: `catalog/service.py:22-31` raises
`409 Unbookable experience` when no active option carries an adult price, so
without it a listing is publishable and immediately broken on its own product
page. (Precisely: `option_prices` has no active flag of its own — the *option*
is what carries `is_active`, and the rule is an active option with an adult
price on it.)

The house-supplier rule closes the hole the sentinel opens: it exists so manual
drafts can be saved, not so products can go live with no fulfilment owner.

The search-document rule is what stops §1.4 recurring for authored content — a
product that cannot be found is not published in any sense a shopper would
recognise.

**Locale completeness is not a publish condition.** Publication and translation
readiness are independent state machines (§7). A product publishes on its source
language and serves fallback elsewhere; requiring eight complete locales before
anything sells would be a translation queue holding revenue hostage.

Refusals are itemised — "cannot publish: no adult price on option 'Standard',
no image" — never a generic 422. An operator who must guess will publish
something wrong instead.

---

## 6. Translation

### 6.1 Storage, and which table is authoritative

Two tables could plausibly hold a translated string, and revision 3 shipped
both without saying which one readers trust. That is the worst of the three
options: a duplicate with no rebuild contract drifts, and the drift is
invisible until a shopper sees a title that no page in the console displays.

**Decision: the wide per-locale tables are authoritative for served text.
`translation_fields` holds workflow state and never holds a served value.**

```sql
CREATE TABLE experience_translations (
  experience_id      UUID NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
  locale             TEXT NOT NULL,
  title              TEXT NOT NULL DEFAULT '',
  short_description  TEXT NOT NULL DEFAULT '',
  description        TEXT NOT NULL DEFAULT '',
  meeting_point      TEXT NOT NULL DEFAULT '',
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (experience_id, locale)
);
```

Rows rather than columns: eight locales × four fields is thirty-two columns, and
adding a locale would become a migration instead of a backfill.

Readers therefore join one table per entity and never consult workflow state,
which keeps the storefront's hot path free of it. The cost is that a commit
must write two tables; §6.2 requires it to do so in one transaction, and the
conditional update is the thing that makes that safe.

`translation_fields.candidate_value` is the deliberate exception. A candidate is
*by definition* not served, so keeping it out of the wide table is what
guarantees an unreviewed machine translation cannot reach a shopper even if a
reader forgets to check status.

### 6.2 Field state is a table, not a JSONB map

Revision 1 put per-field provenance in a JSONB map. That is adequate for
metadata and **wrong as workflow state**: two concurrent field jobs
read-modify-write the same map and one silently loses; a job that started before
an edit can publish stale output after it.

Revision 2 normalized it but kept a *single* `fingerprint` column, which is
still wrong — one column cannot be both "what the published value was made
from" and "what it ought to be made from", and conflating them means either a
stale value describes itself as current, or a source edit has to rewrite every
target row before it can be considered applied.

```sql
CREATE TABLE translation_fields (
  entity_type          TEXT NOT NULL,   -- experience | option | media | taxonomy
  entity_id            UUID NOT NULL,
  field                TEXT NOT NULL,
  locale               TEXT NOT NULL,
  candidate_value      TEXT,            -- awaiting review; never served
  candidate_fingerprint TEXT,           -- what the candidate answers
  provenance           TEXT NOT NULL,   -- manual | machine | imported
  status               TEXT NOT NULL,
  published_fingerprint TEXT,           -- what the served value was produced from
  desired_fingerprint  TEXT NOT NULL,   -- what it should be produced from now
  generation           BIGINT NOT NULL DEFAULT 0,
  reviewed_by          TEXT,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (entity_type, entity_id, field, locale),
  CONSTRAINT ck_translation_field_status
    CHECK (status IN ('pending','current','needs_review','rejected','failed')),
  CONSTRAINT ck_translation_field_provenance
    CHECK (provenance IN ('machine','manual','imported'))
);
```

A row is stale exactly when `published_fingerprint IS DISTINCT FROM
desired_fingerprint`. That is a derived fact, not a flag anyone maintains, which
is the point — and it is why `stale` is **not** one of the permitted status
values. A stored `stale` could disagree with the fingerprints that define it,
and then two queries would give two different backlogs. `rejected` *is* stored,
because rejection is a human decision that no fingerprint implies; a rejected
row reopens as `pending` only when `desired_fingerprint` changes.

**Work items and leases.** Resumability needs somewhere to resume from:

```sql
CREATE TABLE translation_jobs (
  id            UUID PRIMARY KEY,
  entity_type   TEXT NOT NULL,
  entity_id     UUID NOT NULL,
  field         TEXT NOT NULL,
  locale        TEXT NOT NULL,
  fingerprint   TEXT NOT NULL,          -- the desired_fingerprint it was created for
  generation    BIGINT NOT NULL,        -- the generation it observed
  status        TEXT NOT NULL,          -- queued | leased | done | failed
  lease_token   UUID,
  leased_until  TIMESTAMPTZ,
  attempts      INTEGER NOT NULL DEFAULT 0,
  UNIQUE (entity_type, entity_id, field, locale, fingerprint, generation)
);
```

The unique constraint makes enqueueing idempotent: a hundred re-imports of
unchanged content create no work. `generation` is part of it deliberately.
Without it, an operator who edits a title and then reverts it (F0 → F1 → F0)
would find the completed F0 job still sitting there, enqueueing would be
swallowed as a duplicate, and the field would stay permanently stale with no
error anywhere. With it, the revert observes a new generation and enqueues a
distinct row.

A lease that expires returns the item to the queue, so a worker that dies
mid-batch costs one retry rather than a stuck row.

**Commit protocol.** A worker publishes with a conditional update, in the same
transaction as the write to the wide translation table:

```sql
UPDATE translation_fields
   SET published_fingerprint = :fingerprint,
       status = 'current',
       provenance = 'machine',
       candidate_value = NULL,
       candidate_fingerprint = NULL
 WHERE (entity_type, entity_id, field, locale) = (...)
   AND generation = :observed_generation
   AND desired_fingerprint = :fingerprint
   AND status = 'pending'          -- single-winner: see below
   AND provenance <> 'manual'
```

Zero rows updated means the world moved while the model was thinking: the
transaction rolls back, so neither table is written.

The `status = 'pending'` predicate is what makes this single-winner, and
revision 3 was wrong to omit it. `generation` alone does **not** separate two
workers racing on the *same* fingerprint, because nothing in that path
increments it — the spec said the first worker's increment excluded the second,
while also saying only source and manual edits increment it. Both cannot be
true. Since the successful update moves `status` from `pending` to `current`,
the loser's predicate no longer matches, and it discards.

The worker must also present its lease token, so a worker whose lease expired
and was reassigned cannot commit behind the worker that replaced it:

```sql
  AND EXISTS (SELECT 1 FROM translation_jobs j
               WHERE j.id = :job_id
                 AND j.lease_token = :lease_token
                 AND j.leased_until > now()
                 AND j.status = 'leased'
                 -- bound to *this* target, or a valid token for some other
                 -- job would satisfy the predicate
                 AND (j.entity_type, j.entity_id, j.field, j.locale) = (...)
                 AND j.fingerprint = :fingerprint
                 AND j.generation = :observed_generation)
```

This closes every interleaving the reviews identified —

- **two workers, same fingerprint, same generation**: the first flips `status`
  to `current`; the second's predicate fails and it discards
- **expired lease**: the lease predicate fails
- **a source edit mid-flight**: the edit increments `generation` and rewrites
  `desired_fingerprint` in its own transaction, so the in-flight commit fails
- **a human edit mid-flight**: sets `provenance = 'manual'`, and the predicate
  refuses to overwrite it
- **a reviewer approving a candidate produced from since-changed source**:
  approval additionally requires `candidate_fingerprint = desired_fingerprint`,
  so it is rejected with "this changed while you were reviewing" rather than
  silently shipping text that answers a title nobody has any more

For a policy-bearing field (§6.5) the worker writes `candidate_value`,
`candidate_fingerprint` and `status = 'needs_review'` under the same predicate,
and writes **nothing** to the wide table. Approval is a second conditional
update that copies the candidate across and sets `status = 'current'`.

**`generation` increments whenever `desired_fingerprint` changes, whatever the
cause** — source edit, manual translation edit, glossary revision, prompt
change, model change. Revision 4 restricted it to source and manual edits,
which reintroduced the bug it was meant to close from the other side: a recipe
revert `R0 → R1 → R0` leaves the generation unchanged, so the final R0 job
collides with the completed original under the uniqueness key, enqueueing is
swallowed, and the field stays stale forever. It is a monotonic counter on
"what this field should be", not a counter of who asked.

That also makes recipe invalidation atomic. Bumping a glossary revision and
rewriting the affected `desired_fingerprint` values must happen in **one
transaction** with the generation increments and job enqueues; otherwise an
in-flight R0 worker commits during the gap, because its CAS still sees R0 as
desired and nothing has told it otherwise.

A manual edit also sets `published_fingerprint = desired_fingerprint`, because a
human writing a translation of the current source has by definition produced
something current.

**Writes to the wide table are column-specific.** The CAS protects one
`translation_fields` row, and title and description are two rows sharing one
`experience_translations` row — so two workers can both legitimately win their
own CAS and then clobber each other on the way out, if either writes a whole-row
snapshot it read before the other committed. The result is two fields marked
`current` and one of them serving stale text, which no status query would
reveal. So:

```sql
INSERT INTO experience_translations (experience_id, locale, title)
VALUES (:experience_id, :locale, :value)
ON CONFLICT (experience_id, locale) DO UPDATE SET title = EXCLUDED.title
```

One column per statement, never an ORM object flush. Every path — worker,
manual edit, candidate approval — locks the `translation_fields` row first and
writes the wide row second, so the lock order is the same everywhere.

### 6.3 Two fingerprints, not one hash

Revision 1 used a hash of the source text alone. That misses three ways a
translation goes stale without the source changing: a corrected prompt, an
updated glossary, a different model. But folding all of those into one value
creates the opposite bug — a model upgrade would mark **human** translations
stale, when a human's Korean title has nothing to do with which model we use.

So there are two inputs, combined only for machine output:

```
source      = hash(source text · source_language · target locale)
recipe      = hash(prompt version · glossary version · model deployment)
desired_fingerprint = source                     when provenance = 'manual'
                    = hash(source · recipe)      otherwise
```

A glossary fix therefore invalidates every machine translation it could have
affected and leaves human ones alone; a source edit invalidates both, because
both are now describing text that no longer exists.

Re-running translation is genuinely idempotent — nothing whose fingerprints
already agree is touched.

Fields whose provenance is `manual` are never overwritten by machine
translation, but they **do** go stale and are surfaced as such. A human who
wrote the Korean title outranks the translator; a human who wrote it against
text that has since changed still needs to know.

### 6.4 Quality controls

A generic "translate this" turns `Hội An` into `Hoi An City` and localizes brand
names.

```sql
CREATE TABLE translation_glossary (
  term              TEXT NOT NULL,
  target_locale     TEXT NOT NULL,        -- '*' for all
  replacement       TEXT NOT NULL DEFAULT '',
  do_not_translate  BOOLEAN NOT NULL DEFAULT false,
  revision          INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (term, target_locale)
);
```

Seeded with the eighteen marketplace destinations, brand names and Vietnamese
proper nouns, injected into the prompt, and — for `do_not_translate` terms —
**verified in the output**. If a protected term is missing from the result, the
translation is rejected and retried once, then flagged. Instructing a model not
to do something is a request; checking is a guarantee.

Translation input is untrusted, and unlike the assistant this is not merely an
assertion:

- the translation call runs with **no tools bound**, so there is no capability
  to hijack
- input is delimited and length-capped; output is length-capped
- output is validated against the glossary and rejected on failure
- partner text is translated only after its revision is approved (§8), so
  unreviewed third-party text never reaches a model prompt

### 6.5 Publishing policy: split by consequence

Requiring review of every machine translation floods a queue nobody can staff —
there is no Korean reviewer. Auto-publishing everything means a corrected
cancellation policy becomes a wrong Korean cancellation policy, and it ships
because nobody reads Korean. Both extremes fail.

| Class | Fields | On machine translation |
|---|---|---|
| Prose | title, short/long description, option name and description, alt text | Auto-publish, marked *auto-translated* |
| Policy-bearing | meeting point, cancellation wording, accessibility notes | `needs_review`; not served until approved |

A wrong adjective costs relevance. A wrong meeting point puts a traveller on the
wrong street, and wrong accessibility wording sells a ticket to someone who
cannot use it. Those are worth a person's time, and there are few enough that
the queue stays readable — the only property that makes a review queue work.

Pending policy fields never block the product; they serve fallback (§6.6).

### 6.6 Fallback

Fallback is an **explicit ordered chain per locale**, configured, not derived:

| Locale | Chain |
|---|---|
| vi | vi → en |
| zh, ja, ko | *self* → en |
| fr, de, es | *self* → en |
| en | en |

Revision 2 ended the chain with "first non-empty", which has no deterministic
order and could serve Japanese prose to a German shopper — a result that looks
like a data-corruption bug and would be reported as one. Every chain ends at
`en`, and if `en` is empty the field is empty.

An empty field is honest; a random language is not.

Revisions 3 and 4 then ended every chain at `en` and claimed the publish gate
made emptiness unreachable. Both halves were wrong for the case this whole
document exists to serve. A Vietnamese-authored listing has `source_language =
'vi'`; the gate checks the **source** locale, not English; so a listing can be
correctly published with `vi` complete and `en` empty, and a chain terminating
at `en` then renders it blank in all eight locales. Trippass-imported inventory
happens to be English, which is why no test caught it — the defect appears with
the first product an operator writes in Vietnamese, i.e. the first product the
business actually owns.

**The chain therefore terminates at the listing's `source_language`**, appended
after `en` where the two differ (`ko → en → vi`). English keeps its place as the
preferred intermediate — it is the locale most likely to be translated and the
most widely read second language among inbound visitors — but it is no longer
the floor. The floor is "the language somebody actually wrote this in", which
is the only locale the publish gate guarantees. `resolution_chain()` in
`catalog/indexing.py` is the single implementation, used by both retrieval
indexing and API responses so the two can never diverge.

For the same reason, indexing fans out over **every** supported locale rather
than the source alone. Retrieval filters by locale, so a locale with no search
document is a locale in which the product does not exist; a Vietnamese listing
indexed only in `vi` is invisible to every other shopper even though it renders
fine on its own page. Untranslated locales resolve to identical text, so the
embedding cost is deduplicated by text hash within a run, not multiplied
eightfold.

Where a locale genuinely has a better neighbour than English, the chain is where
that goes — `zh-TW → zh → en` if the locale set ever splits — and it is a
configuration change, not a code change.

Responses carry, per field, the locale actually served and its provenance
(§4.3), so the frontend can label rather than imply. A product never vanishes
for want of a translation.

---

## 7. Review and publication states

Revision 1 conflated four independent lifecycles into one boolean, and
contradicted itself about whether pending policy translations block publication.
They are separate:

| State machine | Transitions | Scope |
|---|---|---|
| Product publication | `DRAFT → PENDING_REVIEW → PUBLISHED → ARCHIVED`, plus `PUBLISHED → DRAFT` (unpublish) and `ARCHIVED → DRAFT` (restore). `→ PUBLISHED` runs the §5.3 gate | experience |
| Partner revision | `submitted → approved \| rejected \| superseded`; `submitted → superseded` fires when a newer revision arrives for the same `(partner_id, external_id)` | submission |
| Translation field | `pending → current`, `pending → needs_review → current \| rejected`, `pending → failed → pending` (retry), `rejected → pending` (only when `desired_fingerprint` changes) | (entity, field, locale) |
| Locale readiness | derived: coverage %, stale count, failure count, eval status | locale |

**Stale is not a state**, and revision 3 was inconsistent about that: it listed
`stale` among the stored status values while also saying it is derived and never
written. Both cannot hold. It is derived from
`published_fingerprint IS DISTINCT FROM desired_fingerprint`, and a `CHECK`
constraint keeps it out of the column, so the backlog query and the status
column cannot disagree.

`rejected` *is* stored, because a human deciding "this translation is wrong" is
a fact no fingerprint implies. A rejected field reopens only when the source
changes underneath it — otherwise a retry loop would re-propose the text a
human just refused.

Review approval uses the same compare-and-set as worker publication, so
approving a candidate whose source has since changed fails loudly instead of
shipping stale text.

The existing experience-level `needs_review` boolean cannot express locale,
field, reason, decision or assignee. It is retained only as a denormalized
"something here needs attention" flag, recomputed in the same transaction as any
write to `translation_fields` or `partner_submissions` — a cache, never a
source of truth.

Locale readiness gates **enabling a locale in the switcher** — never publishing
a product.

---

## 8. Partner ingestion

### 8.1 Staged revisions, not direct upsert

Revision 1 said partner submissions upsert the live record and land as
`PENDING_REVIEW`. Those cannot both hold. The importer mutates in place
(`catalog/importer.py:269`) and status is protected as an override once a human
rules on it, so an update to an already-published product either takes a live
product offline or publishes unreviewed content immediately.

Submissions are therefore staged:

```sql
CREATE TABLE partner_submissions (
  id                   UUID PRIMARY KEY,
  partner_id           UUID NOT NULL REFERENCES partners(id),
  external_id          TEXT NOT NULL,
  experience_id        UUID REFERENCES experiences(id),   -- null for a new product
  payload              JSONB NOT NULL,
  payload_hash         TEXT NOT NULL,      -- over the canonical form, not raw bytes
  revision_number      INTEGER NOT NULL,
  supersedes_id        UUID REFERENCES partner_submissions(id),
  base_content_version INTEGER,            -- experiences.content_version when submitted
  status               TEXT NOT NULL,
  submitted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by           TEXT,
  decided_at           TIMESTAMPTZ,
  UNIQUE (partner_id, external_id, payload_hash),
  UNIQUE (partner_id, external_id, revision_number)
);

CREATE UNIQUE INDEX ux_one_open_submission
  ON partner_submissions (partner_id, external_id)
  WHERE status = 'submitted';
```

**Every operation on `(partner_id, external_id)` takes a lock first.** The
unique indexes prevent two conflicting rows from *existing*, which is not the
same as defining what happens when two requests race: without a lock, two
submissions both observe "no open submission", both allocate revision number
N+1, both insert, and one gets a constraint violation surfaced as a 500. The
partial index rejects a request that should have been ordered behind the other
and superseded it.

```sql
SELECT pg_advisory_xact_lock(hashtextextended(:partner_id || ':' || :external_id, 0));
```

Taken at the *start* of submit, supersede and approve, before reading
`content_version`, allocating a revision number, or inserting. One lock per
transaction and always this one, so there is no lock-ordering deadlock to
reason about. It also closes submit-racing-approval: a submission can otherwise
read `base_content_version`, block behind the approval of the previous
revision, and insert carrying a version that was already stale when it landed.

`UNIQUE (partner_id, external_id, revision_number)` backs the allocation, and
identical payloads must return the existing submission rather than a database
error — a no-op is a 200 describing what already exists.

Revisions 3 and 4 specified `INSERT ... ON CONFLICT DO NOTHING ... RETURNING`
for this, which does not work: `DO NOTHING` suppresses the conflicting row, and
`RETURNING` then returns **no row at all**. The handler would read `None` and
report a failure on precisely the retry the design exists to make safe. Because
the insert already runs under the per-partner advisory lock, the correct form is
an explicit read on the empty result:

```sql
INSERT INTO partner_submissions (...) VALUES (...)
ON CONFLICT (partner_id, external_id, revision_number) DO NOTHING
RETURNING id;
-- if that returned no row, the row already exists; the advisory lock means it
-- cannot change underneath us, so read it back:
SELECT id FROM partner_submissions
WHERE partner_id = :partner_id AND external_id = :external_id
  AND revision_number = :revision_number;
```

`ON CONFLICT DO UPDATE SET id = partner_submissions.id` would also return the
row, but a no-op UPDATE writes a new tuple version and fires triggers, so the
read-back is preferable.

1. Partner submits. The payload is **validated and canonicalised first** — keys
   sorted, whitespace normalised, defaults applied — and the hash taken over
   that. Hashing raw JSON would make a reformatted but identical catalogue look
   like a change, which defeats the whole point of step 2.
2. **Identical payload is a true no-op.** The unique constraint enforces it
   rather than a `SELECT`-then-`INSERT` that two concurrent requests both pass.
   No new review item, no audit row, no retranslation, no re-embedding.
   Partners re-post whole catalogues routinely.
3. A newer revision for the same product transactionally marks the open one
   `superseded` and sets `supersedes_id`. The partial unique index guarantees
   at most one open submission per product, so a reviewer is never asked to
   choose between two versions of the same truth.
4. We diff against the live record and present *the diff*.
5. On approval we hold the advisory lock, lock the submission and the
   experience, and re-check `base_content_version` against
   `experiences.content_version`. If an operator edited the record in the
   meantime, approval is refused as stale and the diff is recomputed. Otherwise
   we apply transactionally to the existing experience and option ids, bump
   `content_version`, and enqueue reindexing (§9.4) — so carts and bookings
   stay valid and search does not silently lag.
6. **Approval preserves overrides.** A matching `base_content_version` proves
   nobody edited the record *since submission*; it says nothing about an
   override written before it. Every field with a live `content_overrides` row
   is excluded from the applied diff and reported back to the reviewer as
   "held by operator override", which is the same rule the importer follows.

An open authenticated write path into a live storefront with no review gate is a
content-injection channel with a REST interface.

### 8.2 Endpoints

```
POST /api/v1/partner/experiences        submit or update
GET  /api/v1/partner/experiences/{eid}  read back what we stored
GET  /api/v1/partner/submissions        own submissions and their decisions
```

Operators need the other half, which revision 2 omitted entirely:

```
GET  /admin/submissions                      queue, filterable by partner
GET  /admin/submissions/{id}                 payload plus diff against live
POST /admin/submissions/{id}/approve         requires base_content_version
POST /admin/submissions/{id}/reject          requires a reason, returned to the partner
```

Every read and write checks that the record belongs to the calling partner.
Ownership must be verified per request, not implied by the key.

### 8.3 Abuse resistance is in scope

Revision 1 deferred rate limiting. That is not deferrable on a public
authenticated write endpoint, and there is a specific reason here: operator
authentication deliberately runs scrypt even for unrecognised key prefixes
(`admin/auth.py:31`) to avoid a timing oracle. Reusing it unthrottled turns
every unauthenticated request into attacker-controlled CPU cost.

- pre-auth IP throttling at ingress, before any hashing
- post-auth per-partner quotas — requests, payload bytes, items per submission
- hard caps on body size, item count, string lengths, option and price counts
- partner keys support rotation, expiry and revocation

### 8.4 The audit actor must generalise

`audit_log.operator_id` is a FK to `operators` with an `operator_email`
(`common/models.py:351`). A partner cannot be attributed without either a null
FK and a misleading column name, or a second log — and a second log means no
single answer to "who changed this".

Generalise to `actor_type` (`operator` | `partner` | `system`), `actor_id`,
`actor_label`, migrating existing rows to `operator`. The system actor matters
too: translation and embedding jobs mutate content and currently would appear as
nobody.

---

## 9. Multilingual discovery

### 9.1 Index shape

`experience_search_documents` primary key becomes `(experience_id, locale)`.
One document per product per locale, built from that locale's served text. No
new table.

Every retrieval path must filter on locale. Revision 1 claimed the failure mode
was duplicate products in results and MMR treating them as distinct. **Both were
wrong** (§13). The SQL already groups fused results by experience id
(`search/postgres.py:155`), and search does not apply MMR at all — MMR is
recommendations only (`recommendations/service.py:261`). The real failure modes
are subtler and worse for being invisible:

- **Candidate starvation.** Lexical and vector arms take top-N candidates. Eight
  locale rows of one product consume eight slots, pushing genuine alternatives
  out of the candidate set before fusion ever runs.
- **Score amplification.** One product contributing through several locale rows
  accumulates reciprocal-rank contributions and outranks better matches.

Neither produces visibly duplicated output, so neither would be caught by
eyeballing results. Both are directly testable, and §10 requires tests for them.

Beyond the composite key: `load_products()` reduces documents to a dict keyed by
`experience_id` (`common/persistence.py:131`), so with eight rows it silently
selects an arbitrary locale's embedding — which then drives recommendations and
MMR. Every loader becomes locale-aware explicitly; none may default.

### 9.2 Analysis per locale

PostgreSQL ships stemmers for French, German and Spanish, and none for
Vietnamese, Chinese, Japanese or Korean. The trigger picks configuration by
locale instead of hardcoding `'english'` (`common/schema.py:28`):

| Locales | Configuration | Notes |
|---|---|---|
| en, fr, de, es | native stemmer | `unaccent` retained |
| vi | `simple` + `unaccent` | Diacritics carry meaning, but many shoppers type without them; applied symmetrically to document and query, so recall rises at a small precision cost |
| zh, ja, ko | `simple` | No word boundaries — lexical matching is weak by construction |

For CJK the lexical channel contributes little and recall rests on vectors.
Revision 1 claimed this was tunable through `search_weights`; it is not — those
weights trade *fused* relevance against commercial signals
(`common/runtime_config.py:103`) and cannot shift lexical versus vector
contribution. A per-locale weighted-RRF setting is therefore **new work**, not
configuration.

Indexing needs a locale-leading index for filtered lookups, and the shared HNSW
index must be benchmarked under a locale filter: approximate ANN with a
restrictive filter can under-return, which would look like thin results rather
than an index problem. If it does, partition or use partial indexes per locale.

### 9.3 Embeddings

One embedding per product per locale. Incremental cost is negligible — ~380
products × 7 locales at `text-embedding-3-small` is cents, and ~5 MB of
512-dimension vectors — which is why all eight ship together.

`EmbeddingWorkItem` has `experience_id` and `content_hash` and **no locale**
(`common/models.py:286`), and the importer embeds synchronously
(`catalog/importer.py:293`). Revision 1's claim that this "carries over
unchanged" was wrong: the queue gains locale, a uniqueness key, and resumability,
because a backfill across 3,000 documents cannot be a synchronous loop inside a
request or a migration.

Query embeddings are generated from the shopper's text as typed and compared
within the selected locale. No query translation: it would add a model
round-trip to every search and make manual search depend on a model.

### 9.4 Indexing is a transactional outbox, not an importer side effect

Two problems, one mechanism.

**The importer deletes documents.** `catalog/importer.py:87` deletes every
search document for an experience before recreating one. After a multilingual
backfill, one run of an *old* application instance — or a scheduled import
mid-deploy — erases all eight locales and leaves one. Writers must upsert by
`(experience_id, locale)` and never delete-all, and that change must ship
**before** the backfill (§11).

**Nothing else indexes at all.** Only `catalog/importer.py` and
`catalog/db_seed.py` ever construct an `ExperienceSearchDocument` (§1.4).
Operator edits, translation publication, partner approval and authoring all
change what a product *says* and nothing about what search *matches*.

Both are fixed by making indexing an explicit consequence of content change
rather than something one code path happens to do:

```sql
CREATE TABLE index_work_items (
  experience_id  UUID NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
  locale         TEXT NOT NULL,
  fingerprint    TEXT NOT NULL,      -- content the document should represent
  status         TEXT NOT NULL,      -- queued | leased | done | failed
  lease_token    UUID,
  leased_until   TIMESTAMPTZ,
  attempts       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (experience_id, locale)
);
```

Every content mutation — create, edit, translation publish, override release,
partner approval, import — enqueues in **the same transaction as the change**.
That is what makes it an outbox rather than a queue: if the write commits, the
reindex is guaranteed; if it rolls back, so does the intent. A queue written
after commit loses work on a crash in the gap, which is precisely how a silent
index drift starts.

**The worker commits under the same conditional discipline as translation**,
and for the same reason. Without it: a worker leases fingerprint F0, an edit
replaces the row with F1, the worker finishes and writes the F0 document, then
marks the row done — consuming the F1 request and leaving the index describing
content that no longer exists, with an empty backlog reporting success. So in
one transaction the worker re-reads the work item `FOR UPDATE`, requires that
`fingerprint` and `lease_token` are still the ones it leased, upserts the
search document, and only then marks it done. If either changed, the output is
discarded and the row is left queued for whoever holds it now.

The fingerprint covers **everything that determines the document**, not just the
text: served text for that locale, the embedding model and its version, and a
document-construction version constant. A change to how we build the document
must invalidate every document, or the first deploy that improves the
construction leaves the entire catalogue indexed the old way with nothing
marked stale.

**That fingerprint is stored on the search document itself**
(`index_fingerprint`), not only on the work item. Revision 4 stored a hash of
the text alone in `content_hash`, which cannot distinguish a document built by
an older construction from a current one whenever the text is unchanged. Work
items are transient — a completed one is deleted or indistinguishable from one
that never existed — so the document has to carry the record of how it was
built, or nothing can answer "is this row current?" after the fact.

Two capabilities depend on that, and neither works without it:

- **Reconciliation** (`reconcile_index()`). The outbox only ever hears about
  content *changes*. A release that bumps the construction version, or moves to
  a new embedding model, touches no catalogue row — so nothing is enqueued, the
  backlog reads empty, and the existing catalogue stays indexed the old way
  indefinitely. Reconciliation walks the catalogue comparing stored fingerprint
  to desired and enqueues the difference. It runs as part of the `reindex`
  command, so a deploy that changes document construction is followed by a job
  that actually applies it.
- **Enqueue-what-is-stale.** A caller that has just written one locale directly
  — the importer, which already computed the English embedding — can enqueue
  *every* locale unconditionally and have only the genuinely stale ones become
  jobs. That is what makes the next point affordable.

**The importer enqueues before it writes, and that order is load-bearing.**
The English document is written directly because the import already paid for
its embedding. Written *first*, the import would then find its own document
current, skip the English enqueue, take no lock on the work item at all, and
commit — and a worker that had leased the previous version would wake up,
overwrite the freshly imported text with what it had been building, and mark the
item done. The newer content would be gone from search with nothing left to
repair it. Enqueueing first takes the work item's row lock, so an import cannot
overtake a worker; it waits for one. The request the import just created is then
retired explicitly, conditional on the fingerprint, so a document written with a
fallback embedding does not retire the request that exists to replace it.

**The importer enqueues every locale, not just the one it writes.** Until a
listing is translated, every locale resolves *through* the source text, so an
import that writes only `en` leaves seven documents describing the previous
version of the product. Nothing fails and no row is missing; search simply
answers from text the catalogue no longer contains. It is §1.4 again, arriving
through a different door.

**Retry exhaustion is terminal and visible.** Leasing ignores items past
`MAX_ATTEMPTS`, so an item left `queued` after its final failure is a row that
is never selected again while every status view reports it as pending work in
progress — an index that is out of date and a queue that says it is keeping up.
Exhausted items become `failed`. Conversely, enqueueing a *new* fingerprint
resets `attempts`, because otherwise a product whose indexing failed once could
never be reindexed again however many times an operator corrected it.

**A failure anywhere in the item reaches the same transition.** Catching only
embedding errors leaves everything else — a data error loading the record, a
constraint violation writing the document — to escape the loop, stranding that
item *and every item leased behind it* in `leased`. Because leasing skips items
at the attempt limit, a repeatable error strands them permanently: invisible to
the queue, never retried, with the backlog reading empty. Each item is wrapped
individually, so one bad record costs one record.

**The catalogue is the authority on whether a document is current, not the
work item.** The worker re-reads the record inside its commit transaction and
compares its output against what the catalogue says right now. A work item
records what some caller *asked for*, and that can be older than what is
committed — reconciliation reads, computes, then enqueues, so a request can
arrive describing a version an edit has already superseded, and the item can be
`done` by then. Treating the work item as the truth would make the worker
discard a correct document, consume an attempt, and repeat until the item was
retired as `failed`: a failure that never happened, on a product indexed
correctly the whole time. When the catalogue really has moved, the item is
handed back carrying the fingerprint now wanted, and the attempt is *not* spent
— being overtaken is not failing.

**Reconciliation revives failed work; it never overwrites live work.** These
pull in opposite directions and both matter. A `failed` item is by definition
not indexed, so any request to index it should retry it — a conflict clause
keyed only on "the fingerprint changed" would leave an item that failed during a
provider outage repairable only by editing a product that has nothing wrong with
it, while still reporting the repair as made. But reconciliation also *reads*
the catalogue, computes fingerprints, and enqueues afterwards; an edit
committing in that window would be clobbered by the older fingerprint, and the
worker would then build the newer text, find the work item disagrees, and
discard its own correct output. So reconciliation writes **only over `done` and
`failed`** and never over `queued` or `leased`. Monotonic generation numbers
would also close the race, but fingerprints cannot be ordered, and "never touch
work that is already pending" needs no new state to be correct.

**Counts come from what was written, not what was intended.** Reconciliation
reports repairs from the rows its writes actually changed. `RETURNING` rather
than `rowcount`: SQLAlchemy only memoises `rowcount` for `UPDATE` and `DELETE`,
so an `INSERT` reports `-1` once its cursor closes — a caller summing the result
gets a negative repair count.

**A fallback vector is never certified as an embedding.** When the provider is
unavailable the importer substitutes a deterministic vector so the import still
completes. That vector is not an embedding — it bears no relation to the vectors
a shopper's query produces, so the product is effectively absent from semantic
search. Written under the production model name, the fingerprint would say the
document was built correctly and reconciliation would report the catalogue
healthy forever: **one transient outage would remove products from search
permanently, with no error anywhere.** So the model that actually produced the
vector is recorded on the document (`deterministic-fallback`) and is part of the
fingerprint, which makes the degradation self-repairing — the next
reconciliation asks for those documents again.

**Reconciliation is paged, and the repair command is unbounded.** The pass is
`experiences × (locales + 1)` queries; the whole catalogue in one transaction
holds a snapshot open for the length of the walk. It pages by keyset on the
primary key — not `OFFSET`, so rows inserted mid-pass cannot make it skip or
repeat one — committing each page, so an interrupted run has still done real
good and its committed pages are already draining. The in-process drain keeps
its bound because it shares a process with request handling; the CLI drain has
none. A construction-version bump enqueues the whole catalogue (379 experiences
× 8 locales ≈ 3,000 documents), and a repair command that stops after 500 exits
successfully having fixed a sixth of the problem.

**Locale tags are normalised on write.** They are case-insensitive by
specification and case-sensitive as database strings. Normalising at some reads
and not others is worse than not normalising at all: a record stored as `VI`
would be indexed under `vi` while its resolution chain looked for translations
tagged `VI`, producing a document that is built, stored, searchable, and empty.

**A shutdown hands back its leases.** The worker runs in the application
process, so every deploy cancels it mid-batch. Without an explicit release, up
to a full batch stays leased for the lease duration and each redeploy burns
another attempt against the limit — a frequently-deployed service could exhaust
items without a single genuine failure. The item currently being processed stays
in that outstanding set for the whole of its turn, *including while its failure
is being recorded*, because the transition is exactly where a cancellation
leaves it held with its attempt already spent.

**An item can end up leased, out of attempts, and owned by nobody.** Leasing
requires `attempts < MAX_ATTEMPTS`, so if the failure transition itself cannot
be written — a database error while recording it, a process that dies between
leasing and reporting — the row keeps the status `leased` after its lease
expires and is never selected again. Not queued, not failed, not held: invisible
to the queue, the backlog and every retry. Leasing therefore begins by retiring
expired leases with no attempts left, and `_fail` logs its own failure rather
than suppressing it, so the state has both a recovery and an explanation.

**Lease deadlines are the database's clock, never the application's.**
Replicas do not agree on the time. A replica running fast would expire leases
another replica is still working; running slow, it would decline to reclaim
leases that really are dead. `now()` is evaluated in Postgres for both the
deadline and the sweep, so every replica reads the same clock. Fencing by lease
token is what makes an expired-but-still-running worker harmless: it can no
longer commit, because the token no longer matches.

**A drain that stops is not a queue that is empty.** The loop ends when a round
builds nothing, and a round in which every item failed transiently returns them
all to `queued` and builds none. The repair command therefore reports the
remaining backlog by status and exits non-zero, which is what makes "the job
succeeded" a claim about the index rather than about the command.

But it does not run alone: the application keeps an in-process worker, so part
of the backlog can be legitimately leased by a replica getting on with it, and a
job that failed the moment it saw a non-empty backlog would report a broken
index every time the app happened to be busy at deploy time. Queued and leased
rows are treated as in flight and waited on to a bound; a `failed` row is
terminal by definition and returns immediately, because waiting out a settle
window on something that will never change only makes every genuine failure a
slow one.

**The catalog job drains too, and this is not redundancy.** The web app runs
with `minReplicas: 0`, so the in-process worker only exists while the app is
awake. The catalog job *enqueues* work — it imports — and an app that has scaled
to zero would leave that work sitting until the next visitor happened to wake
it. So the job ends with `reindex`, which reconciles and then drains: the
process that changes the catalogue is the process that finishes indexing it,
with no dependency on anything else being running. The in-process worker remains
the fast path for operator edits, where seconds matter and the app is awake by
definition — the operator is using it.

"A current source document" in the publish gate (§5.3) means exactly: a search
document exists for `(experience_id, source_language)` whose stored fingerprint
equals the fingerprint derived from the record right now. Anything weaker
permits publishing a listing whose index entry describes a previous draft.

Indexing fans out over `SUPPORTED_LOCALES` **plus the record's own
`source_language`** even when that language is not one the storefront sells in.
The publish gate requires a current source-locale document, so omitting it would
make such a record unpublishable for a reason no error message explains.

`EmbeddingWorkItem` (`common/models.py:286`) is superseded by this: it has no
locale, no uniqueness key and no lease, and the importer bypasses it by
embedding synchronously (`catalog/importer.py:293`).

Publication requires a *current* document for the source locale (§5.3); other
locales converge asynchronously and their absence is fallback, not failure.

#### Every mutation of an indexed input enqueues the experiences it affects

This is the invariant the whole subsystem rests on, and it is stated here
because it is the one a future change is most likely to break without noticing.
A document's text is assembled from more than the `Experience` row:
`resolved_document_text` also reads `Destination.name`. Any writer that changes
*any* input to that function must, in the same transaction, enqueue every
experience whose document that input feeds. Reconciliation is a safety net for
crashes, not a substitute — it runs on a schedule, so between the mutation and
the next pass, search answers from text the catalogue no longer contains.

The consequence today is that **destinations are immutable**. `_ensure_destination`
only inserts, so no code path can rename one, and the invariant holds by absence
rather than by design. A rename operation is not a `Destination` update: it is a
rename *plus* a fan-out enqueue of every experience in that destination, in one
transaction. Until such an operation exists, nothing may issue an `UPDATE` on
`Destination.name`. The same reasoning applies to any field later promoted into
the document — promoting it into the text is only half the change.

#### The importer's shortcut is only valid when the feed and the catalogue agree

The import embeds a document it builds from the *supplier's* fields, and writes
it directly to save the worker a round trip. That shortcut is only sound while
the catalogue says the same thing as the feed, and `_apply` exists precisely to
make them differ: every field an operator has corrected is skipped on import, so
a fixed title lives in the row while the feed keeps sending the old one.

Writing the supplier's document then puts the old title back into search while
the listing page goes on showing the correction — and because the catalogue was
already current, the fan-out found nothing to enqueue, so no work item exists
anywhere to notice the disagreement. The import therefore rebuilds the canonical
English document from the persisted row and takes its shortcut only when the two
texts are identical. When they are not, the fan-out has already done the right
thing: either the stored document matches the catalogue and there is nothing to
do, or it does not and the work is queued for a worker that builds from the row.

This is the same rule as §9.4's opening claim, applied to the one writer that
was still exempt from it: **only the catalogue decides what is indexed.** A
document assembled from anything else is a guess about what the catalogue
contains, and the import is the one place where that guess can be wrong.

#### Imports of a supplier are serialised

Deterministic ordering removes the deadlock between two concurrent imports but
not the race: suppliers, destinations and experiences are all created
select-then-insert, so two runs meeting the same new entity race to insert it
and one dies on the unique constraint. `upsert_catalog` takes a transaction-
scoped advisory lock keyed on the supplier. It costs nothing — these are batch
jobs, and a second run has nothing useful to do while the first is in flight.

The lock is taken after the feed is fetched and embedded, so it serialises the
*application* of two imports but not their ordering: an older import with slow
embedding can still commit after a newer one and write the older feed. That is a
supplier-freshness question rather than an index-correctness one — the index
will faithfully describe whatever the catalogue ends up holding — and it stays
theoretical while imports are a scheduled job that does not overlap itself. If
overlapping imports ever become normal, the lock has to move ahead of the fetch,
or imports need a generation number that lets a newer one reject an older.

#### A deterministic vector never replaces a real one

A fallback vector is worth having when the alternative is no document at all,
and never worth having in place of an embedding. The importer therefore skips
its own write when the stored document already carries the production
fingerprint for the text it is about to write — because that combination means
the document is already correct, the fan-out found nothing to enqueue, and
overwriting it would remove the product from semantic search with no work item
anywhere to bring it back. Every other combination is already safe: if the text
changed, or no document exists, or the stored document was itself a fallback,
the fingerprints disagree, the enqueue fires under a row lock, and
`mark_locale_indexed` correctly declines to retire a request a fallback did not
satisfy.

#### Imports visit products in a fixed order

Two imports running concurrently take the same row locks. Visiting products in
different orders is the textbook deadlock — each holds what the other needs
next — and Postgres resolves it by killing one, producing an import failure
nobody can reproduce. `upsert_catalog` sorts by `external_id` before doing
anything, which costs nothing and removes the cycle by construction.

---

## 10. Evaluation, and why it comes first

§1.3 establishes that the harness could not fail on this defect. Two fixes:

1. **Tokenizer** — *done* (§1.3). Unicode-aware, diacritic-folding, CJK
   bigrams, with tests that fail against the old pattern.
2. **A PostgreSQL-backed multilingual suite.** The real trigger, GIN index, HNSW
   index and composite key are the things under test; the in-memory store
   exercises none of them.

Cases assert **expected experience ids or top-k membership**, never merely
"non-empty" — the current shape would pass on browse fallback, which is exactly
how this defect hides. Coverage:

- the documented production failures (`du thuyền hoàng hôn`, `日落游船`,
  `croisière au coucher du soleil`) in all eight locales
- candidate starvation and score amplification (§9.1) as explicit cases
- fallback and provenance assertions: the right locale is served, or the right
  fallback is served and labelled
- assistant reply-language assertions
- a locale-readiness gate: a locale activates only when its cases pass

### 10.1 Determinism, and how CI actually runs this

In database mode, search embeds the query with the configured provider
(`search/service.py:440-459`), and CI passes no model configuration to the eval
command (`.github/workflows/deploy.yml:71-76`). A suite that needs Azure OpenAI
would either not run in CI or make the gate depend on a remote model's mood.
Neither is acceptable for something whose job is to fail reliably.

So the suite is deterministic by construction:

- a committed multilingual fixture set — a small catalogue with real
  Vietnamese, Chinese, Japanese, Korean, French, German and Spanish text
- **committed 512-dimension vectors** for those documents, generated once from
  the real model and stored as fixture data, so the vectors under test are
  representative rather than synthetic
- a deterministic query-embedding provider injected for the run, returning the
  committed vector for known queries
- migrations run, fixtures loaded, `ANALYZE` executed, then the **real** SQL —
  same trigger, same GIN index, same HNSW index, same composite key

This keeps CI model-free while testing the actual retrieval path, which is the
combination revision 1 assumed it already had.

### 10.2 Runtime observability

A design motivated by silent failure needs runtime signal, not only CI. Search
and assistant events carry `locale` and served content language; the console
surfaces, per locale: zero-result rate, fallback rate by field, stale and failed
translation backlog, missing search documents, partner rejection and throttle
counts, assistant locale mismatches. Alert on elevated zero-result and fallback
rates. Without this the next multilingual regression is silent too.

---

## 11. Migration

379 live records, live carts and bookings, and existing embeddings.

Revision 2's sequence was **impossible**, not merely risky: it kept the old
primary key on `experience_id` alone through the backfill, then created the
composite index afterwards. A second locale row cannot be inserted while that
key exists, and `ON CONFLICT (experience_id, locale)` cannot run before a
matching constraint does. The order below fixes that — the key is replaced
*before* any multilingual row exists.

A second correction: `scripts/deploy.sh` deploys the application and *then*
starts the migration job. New code therefore meets the old schema. Expand
migrations must complete before new code takes traffic, so the deploy script
gains a pre-deploy migration step; contract migrations stay after.

Infrastructure runs single-revision mode with one replica
(`infra/bicep/resources.bicep`), so instances do not overlap for long — but
"briefly" is not "never", and the catalog job — which is **manually triggered**,
not scheduled (`resources.bicep:302-314`) — can be started against either schema
at any time.

**The deployment mechanism had to change first, and now has.** The container app
and the jobs share one `containerImage` parameter, so there was no way to run a
migration on the new image without `deploy_stack` also promoting the app: the
schema change landed *after* new code was already serving traffic
(`scripts/deploy.sh`, previously line ~121). Fixed by giving migration its own
Container Apps job (`job-<prefix>-migrate`, `resources.bicep`) whose image
`deploy.sh` updates directly with `az containerapp job update --image`, runs to
completion, and only then deploys the stack. Alembic has been removed from the
catalog job, which now seeds and imports only. The consequence is a rule, not
just an ordering: **every migration must be expand-only**, because the previous
release is still taking traffic while it runs.

Revisions 3 and 4 specified a three-step expand for the primary-key change —
build the composite index `CONCURRENTLY`, ship compatible readers, swap the key
in a later release — and `0004_content_pipeline.py` does not do that. It swaps
the key immediately, in one migration, with no autocommit block. Rather than
describe a dance the code does not perform, this revision accepts the immediate
swap and states why it is sound here:

- `experience_search_documents` holds one row per published experience —
  hundreds, not millions. Rebuilding its primary key takes milliseconds, so the
  `ACCESS EXCLUSIVE` lock is not a meaningful availability event. `CONCURRENTLY`
  exists for tables where it is.
- The swap is still **backward-compatible with the running release**, which is
  the property that actually matters while the previous revision serves traffic:
  old readers select without a `locale` predicate and see the `en` rows they
  always saw, and old writers omit `locale` and get it from the server default.
  Nothing in the previous release fails against the new key.

The expand-only rule is unchanged and still binding, but "expand-only" needs
stating precisely rather than as a slogan. `0004` is **not** literally
additive: it swaps a primary key, drops the global `UNIQUE (external_id)` in
favour of two partial indexes, and relaxes `external_id` to nullable. What it
does not do is remove anything the *running* release depends on — every one of
those changes either widens what is permitted or replaces a key with one the old
code's writes still satisfy. That is the property that matters during a rolling
deploy, and it is the one worth asserting; "adds columns only" is neither true
here nor sufficient in general.

`downgrade()` restores the 0003 guarantees it relaxed — `external_id NOT NULL`,
global uniqueness, `suppliers.is_placeholder` removed — not because a downgrade
is a plausible production operation once translated rows exist, but because a
partial downgrade produces a schema that is neither version, and the round-trip
test (§13) then silently checks nothing. If a
future table is large enough that a rebuild is an outage, the concurrent
three-step is the pattern to reach for — with `op.get_context()
.autocommit_block()`, since `CREATE INDEX CONCURRENTLY` cannot run inside
Alembic's transaction.

1. **Expand.** Add `locale TEXT NOT NULL DEFAULT 'en'` to
   `experience_search_documents`. Add `source_type`, `partner_id`,
   `source_language`, `content_version` to `experiences`. Create the new tables
   (§3.1, §6.1, §6.2, §8.1) — including `content_overrides`, `index_work_items`
   (§9.4) and `taxonomy_terms` (§4.2), which revision 3 omitted from this list.
   Backfill imported records to `reference`/`en`; seed the house supplier and
   mark it `is_placeholder`. Nothing multilingual exists yet.
   The primary key of `experience_search_documents` moves to
   `(experience_id, locale)` in this same migration, for the reasons above.
2. Deploy readers that select `locale = 'en'` **explicitly** and writers that
   upsert by `(experience_id, locale)` and never delete all locales (§9.4).
   This release is behaviourally identical to today's, which is what makes it
   safe to ship on its own. Multilingual rows are insertable from step 1, but
   nothing inserts them yet.
3. Before anything writes non-English rows, confirm no catalog-job execution
   predating step 2 is still running, and wait for or terminate it — an old
   execution still runs the old delete-all importer and would erase every
   locale it does not know about. Container Apps Jobs have no "old revision" to
   retire; executions are what exist, so this is a check on
   `az containerapp job execution list`, not a revision operation.
4. Add the PostgreSQL multilingual eval suite (§10). It fails at first, since no
   translations exist — so it lands **non-gating**, or together with the
   retrieval implementation. The deploy workflow blocks on evals, which means a
   deliberately-failing eval release is otherwise undeployable.
5. Deploy locale-aware resolution with `enabled_locales = ['en']`, so the code
   path is live and exercised while shopper-visible behaviour is unchanged.
6. Run resumable translation and indexing backfills as jobs, outside Alembic.
   A migration that calls a model is a migration that fails halfway with no way
   to resume.
7. Enable locales in the switcher one at a time as their readiness gates and
   eval cases pass (§10, §7).
8. Drop superseded constraints and `EmbeddingWorkItem` in a later release.

Rollback after step 1 is a redeploy, not a downgrade: the schema has genuinely
changed, and reverting the code means an older reader must tolerate multilingual
rows it does not know about. It can — old readers ignore `locale` and the `en`
rows are still there — but only because step 2's explicit `en` filter and
non-destructive writers ship before anything writes a second locale. That
ordering is the rollback plan; there is no `downgrade()` worth trusting once
translated rows exist.

---

## 12. Sequence

Revision 1 ordered authoring first on the grounds that it defines the data
model. The review disagreed, and it is right: the *schema* defines the data
model, and authoring, media and partner ingestion are not prerequisites for
fixing a defect that is live now. Putting evals last was backwards — they are
the acceptance gate and must exist before the thing they gate.

Revision 2 then got the internal order wrong in turn, backfilling before
locale-aware readers existed and before the primary key could hold the rows.
Each numbered step below is a deployable release:

0. **Tokenizer** (§1.3) — *done*. The gate must be able to fail first.
1. **Schema + compatible release**, together (§11 steps 1–2): identity, locale,
   overrides, translation state, jobs, partner revisions (§3, §6, §8), the
   primary-key swap, explicit-`en` readers, non-destructive writers and the
   indexing outbox (§9.4). Schema and readers ship as one release because the
   swap is single-step (§11); the old release stays compatible throughout.
2. **Multilingual eval suite** (§10) — failing, and it should
3. **Translation pipeline**: fingerprints, leases, glossary, review split (§6)
4. **Locale-aware resolution** behind `enabled_locales = ['en']` (§4.3, §9)
5. **Backfill** translations and search documents for existing inventory
6. **Switcher, UI strings, locale formatting**; enable locales as gates pass
7. **Authoring API + publish gate** (§5), **media** (§14), **booking lines**
8. **Partner authentication, staging, approval API and review UI** — together,
   because a submission queue no operator can see is a queue that fills up
9. **Authoring console** with per-locale tabs and provenance

The ordering rule throughout: *every release is shippable on its own, and no
release depends on a schema that a running instance has not yet seen.* Steps
0–6 close the live defect and deliver all seven added languages; 7–9 build the
sourcing capability that replaces Trippass as the origin of inventory.

---

## 13. Where earlier revisions were wrong

Recorded because the corrections are more useful than the conclusions, and
because every one of these would have become a defect. Revision 1 findings
first, then revision 2's.

| Claim | Reality |
|---|---|
| Missing locale filter returns duplicate products | SQL already groups by experience id; the real harm is candidate starvation and score amplification (§9.1) |
| MMR would treat locale variants as distinct | Search does not use MMR; it is recommendations-only |
| Per-locale hybrid tuning is available via `search_weights` | Those weights trade fused relevance against commercial signals; lexical-vs-vector weighting does not exist yet |
| The embedding queue "carries over unchanged" | It has no locale column, no uniqueness key, and the importer embeds synchronously |
| `source_hash` is sufficient staleness | Ignores prompt, glossary and model changes |
| JSONB provenance map is sufficient state | Concurrent field jobs overwrite each other; needs compare-and-set rows |
| Partner upsert lands as PENDING_REVIEW | Incompatible with in-place mutation and status overrides; needs staged revisions |
| "One positive price" is a sufficient publish gate | The storefront requires an active **adult** price or raises 409 |
| Rate limiting can be deferred | Auth runs scrypt on unknown prefixes, so unthrottled requests are a CPU-cost amplifier |
| Adding eval cases proves the languages work | The tokenizer emitted nothing for CJK; cases would have passed vacuously |
| **The importer rewrites all fields** | It rewrites an explicit supplier-owned subset (`IMPORTED_FIELDS`) and skips protected ones |
| **The storefront assumes a supplier exists** | `common/persistence.py` never references `Supplier`; only the admin console joins it |
| **Booking rendering reads current names** | `BookingView` carries no listing names at all — worse than assumed |
| **A single fingerprint makes CAS safe** | It conflates published-from with should-be; needs `published`/`desired` plus a `generation` counter |
| **Keep the old PK through backfill** | Physically impossible — the old key forbids a second locale row |
| **Rollback before locale-aware readers is configuration-only** | True only until the PK swap; after it, multilingual rows exist |
| **"First non-empty" is an acceptable last fallback** | Nondeterministic; could serve Japanese to a German shopper |
| **`source_language` can simply be a column** | Changing it invalidates overrides, translations and documents at once; it is immutable |
| **`payload_hash` alone makes resubmission a no-op** | Needs canonicalisation and a unique constraint, or concurrent posts both insert |
| **An override key of `(entity, field, locale)` is a design** | A key is not a schema; JSONB encoding gives no validation or concurrency safety |
| **`translation_fields.value` and `experience_translations` can both hold text** | Two homes, no rebuild contract, guaranteed drift. The wide tables are authoritative; workflow state holds candidates only (§6.1) |
| **`generation` alone makes worker publication single-winner** | Nothing in the worker path increments it. `status = 'pending'` is what excludes the loser (§6.2) |
| **Job uniqueness over `(target, fingerprint)` is idempotent** | A revert F0 → F1 → F0 is swallowed as a duplicate and the field stays stale forever. `generation` is part of the key |
| **`stale` is a status value** | It is derived from fingerprint disagreement; storing it lets the column and the backlog query disagree. A `CHECK` keeps it out |
| **Taxonomy is `(kind, code)` text** | `translation_fields.entity_id` is a UUID, so taxonomy could not participate in the workflow built for it. Terms get a surrogate key (§4.2) |
| **Partner-key storage "mirrors" the operator scheme** | The DDL omitted `key_salt`, which the existing verifier requires (`admin/auth.py:81-94`) |
| **Unique indexes serialize concurrent submissions** | They reject rather than order. Submit, supersede and approve take an advisory lock on `(partner_id, external_id)` first (§8.1) |
| **`content_version` increments on every mutation from any actor** | Translation and indexing would invalidate unrelated partner approvals. It versions source and commerce state only (§3.1) |
| **A leased index work item can be committed on completion** | An edit mid-flight replaces the fingerprint; committing blind writes a stale document *and* consumes the new request (§9.4) |
| **Diacritic folding falls out of NFD** | `đ` is a stroke, not a combining mark. "Đà Nẵng" and "da nang" tokenised differently until an explicit fold was added |
| **Every fallback chain can terminate at `en`** | The publish gate checks the *source* locale, so a Vietnamese listing is publishable with `en` empty and would render blank in all eight locales. Chains terminate at `source_language` (§6.6) |
| **`generation` need only bump on source and manual edits** | A recipe revert R0 → R1 → R0 then deadlocks the uniqueness key exactly as the source revert did. It bumps on any `desired_fingerprint` change (§6.3) |
| **Two workers on different fields of one row cannot conflict** | They win separate CAS rows and then both write the shared wide row. Writes are column-specific, never whole-object (§6.1) |
| **`INSERT ... ON CONFLICT DO NOTHING ... RETURNING` returns the existing row** | It returns nothing at all, so the no-op resubmission path would report failure on exactly the retry it exists to make safe (§8.1) |
| **`content_hash` is sufficient document staleness** | It hashes the text only, so a document built by an older construction is indistinguishable from a current one. The full fingerprint is stored on the document, and reconciliation depends on it (§9.4) |
| **Enqueueing on content change is sufficient** | A construction-version or embedding-model change touches no catalogue row, so nothing is enqueued and the backlog reads empty while the catalogue stays stale. Reconciliation closes it (§9.4) |
| **The importer only needs to write its own locale** | Untranslated locales resolve *through* the source text, so an import that writes only `en` leaves seven documents describing the previous version (§9.4) |
| **Returning a failed item to `queued` is a retry** | Past the attempt limit the leasing query ignores it, so it is stranded forever while reporting itself as pending. Exhaustion is `failed`, and a new fingerprint resets `attempts` (§9.4) |
| **`0004` is expand-only** | It swaps a primary key, drops a unique constraint and relaxes a NOT NULL. The property that matters is backward-compatibility with the running release, which it has; "expand-only" was the wrong claim (§11) |
| **Upgrading a fresh database validates a migration** | `0001` calls `create_all`, so a fresh build comes from current models regardless of the migration's contents. Only upgrade → downgrade → upgrade tests the DDL (§13) |
| **Catching embedding failures covers the worker's failure modes** | Every other error escaped the loop, stranding that item and all later leases in `leased`; since leasing skips items at the attempt limit, a repeatable error stranded them permanently (§9.4) |
| **A conflict clause keyed on "the fingerprint changed" is enough** | It can never revive a `failed` item, because reconciliation asks for the fingerprint the content already has — while counting the repair as made (§9.4) |
| **Reconciliation can enqueue unconditionally** | It reads, computes, then writes; an edit committing in that window is clobbered by the older fingerprint and the worker discards its own correct output. It writes only over `done` and `failed` (§9.4) |
| **A fallback vector is a degraded embedding** | It is not an embedding at all. Recorded under the production model name the fingerprint certifies it as current forever, so one provider outage removes products from semantic search permanently and silently (§9.4) |
| **`rowcount` counts what an upsert changed** | SQLAlchemy memoises `rowcount` only for `UPDATE` and `DELETE`; an `INSERT` reports `-1` after its cursor closes, so summing it produced negative repair counts. `RETURNING` is what counts (§9.4) |
| **A 500-document drain is a repair command** | A construction-version bump enqueues ~3,000 locale documents; a bounded drain exits successfully having fixed a sixth of them. The bound belongs to the in-process worker only (§9.4) |
| **Normalising `source_language` where it is read is sufficient** | `indexed_locales` lowercased and `resolution_chain` did not, so a record tagged `VI` would be indexed under `vi` with a chain looking for `VI` — a document built, stored, searchable and empty. It is normalised on write (§9.4) |
| **Reconciling the catalogue in one transaction is fine at this size** | It is `experiences × (locales + 1)` queries holding one snapshot; it pages by keyset and commits per page, so an interrupted pass keeps its work (§9.4) |
| **A work item's fingerprint says what the document should be** | It says what a caller asked for, which reconciliation can make older than what is committed. Compared against it, the worker discards correct documents and retires the item as `failed` after five attempts that never failed (§9.4) |
| **`only_if_idle` closes the reconciliation race** | It only covers the interleaving where the newer work is still pending. If the worker finished first the item is `done`, the stale request is accepted, and the damage is the same. The catalogue re-read at commit is what closes it (§9.4) |
| **Leasing recovers any item a worker abandons** | Not one whose lease expired on its final attempt: leasing requires `attempts < MAX_ATTEMPTS`, so it stays `leased` forever, invisible to the queue and the backlog alike. Expired leases with no attempts left are retired on the way in (§9.4) |
| **Dropping the current item from the outstanding set before processing it is safe** | A cancellation arriving while its failure was being recorded then released every later lease and left this one held with its attempt spent (§9.4) |
| **A drain that returns zero means the queue is empty** | A round in which every item failed transiently returns them to `queued` and builds nothing, so the repair command exited successfully having rebuilt none of the index it exists to repair (§9.4) |
| **A default argument reads the module constant** | `page_size: int = RECONCILE_PAGE_SIZE` binds at import, so the paging test monkeypatching the constant ran a single page of 200 and proved nothing about paging (§9.4) |
| **Normalising `source_language` in the chain builder is enough** | Field resolution compared each candidate against the raw column, so a record tagged `VI` resolved no fields at all — the one failure mode the normalisation was added to prevent (§9.4) |
| **A writer that keeps its own document current need not enqueue** | The importer wrote English, found it current, and skipped the enqueue — so it never took the work item's row lock, and a worker holding the previous version overwrote the import after it committed. Writers enqueue *before* they write (§9.4) |
| **Lease deadlines can be stamped by the application clock** | Replicas disagree about the time, so one running fast expires leases another is still working. Both the deadline and the sweep are evaluated in the database (§9.4) |
| **A non-empty backlog means the repair failed** | The in-process worker holds part of it whenever the app is awake, so the deploy job would fail because indexing was working. In-flight work is waited on to a bound; only `failed` is immediate (§9.4) |
| **`information_schema.data_type` compares column types** | It reports every `varchar(n)` as "character varying" and every `vector(n)` as "USER-DEFINED", so a migration creating `vector(1536)` against a model wanting `vector(512)` compared equal. `format_type` is what compares (§13) |
| **A fallback vector is always better than the alternative** | On an unchanged re-import during a provider outage the text still matches, so nothing is enqueued — and the import overwrote a healthy embedding with a deterministic vector that no query can match, with no work item left to repair it. The write is skipped when the stored document is already current (§9.4) |
| **A settle window of 90s is patient enough** | A lease is 300s and the embedding client has no explicit request timeout, so a worker doing legitimate slow work failed the deployment. The window must outlast a lease (§9.4) |
| **A `failed` row is always evidence of work still owed** | The document can be made current by another path while a request for it is failing; reconciliation then walks past it forever because there is nothing to enqueue, and every future deploy fails on a healthy index. Satisfied requests are retired (§9.4) |
| **Import order is an implementation detail** | Two concurrent imports taking the same row locks in different orders deadlock, and Postgres resolves it by killing one — an unreproducible failure. Products are visited in `external_id` order (§9.4) |
| **Only `Experience` fields feed the document** | `resolved_document_text` also reads `Destination.name`, and nothing enqueues on a rename. Latent only because destinations are insert-only today; "every mutation of an indexed input enqueues the experiences it affects" is now an explicit invariant (§9.4) |
| **The document the import embedded is the document to store** | It is built from the supplier's fields, but `_apply` skips every field an operator has corrected — so writing it reverted the correction in search while the listing page still showed it, with nothing enqueued to notice. The shortcut is taken only when the canonical text matches (§9.4) |
| **Deterministic ordering makes concurrent imports safe** | It removes the deadlock, not the race: suppliers, destinations and experiences are created select-then-insert, so two runs meeting the same new entity race to insert it. Imports of a supplier take an advisory lock (§9.4) |

Earlier revisions also under-specified: translation coverage beyond four fields,
migration entirely, the `language`/`locale` collision, audit attribution for
non-operator actors, deterministic eval fixtures, the API locale contract, and
the fact that **nothing but the importer ever writes a search document** (§1.4).

---

## 14. Media

- **Azure Blob Storage**, one container per environment. It is **not currently
  provisioned**: `infra/bicep` has no storage account today, so this is new
  infrastructure, not a configuration change. The schema keeps both
  `external_url` (imported content) and `blob_path` (managed content); assuming
  every row is managed would break the 379 existing records.
- Validation on **content sniffing, not extension**: JPEG, PNG, WebP only,
  ≤ 8 MB, with a **maximum pixel count** as well as byte size — a 200 KB file
  can decompress to gigabytes.
- Accepted images are **fully decoded and re-encoded** before storage. This
  neutralises malformed-file exploits and polyglots, and makes EXIF stripping a
  side effect rather than a separate step. It is also why virus scanning can
  reasonably remain deferred.
- Uploads are streamed with limits at ingress and application layers, not
  buffered whole and then checked.
- Blob and row lifecycle: the row is authoritative; orphaned blobs are swept by
  a scheduled job, because a blob write and a database commit cannot be one
  transaction and pretending otherwise leaks storage.

---

## 15. Out of scope

- Virus scanning (mitigated by re-encoding), derivative image sizes
- Translation memory or human translation vendor integration
- Per-locale SEO routing (`/vi/…`) — a routing change later, not a data change
- Right-to-left languages; none of the eight require it and the CSS assumes LTR
- Partner self-service portal
- Currency localisation and real FX — static FX remains a named blocker
- Machine translation of shopper-generated content (there is none)

---

## 16. How to judge this work

- Does `du thuyền hoàng hôn` return a sunset cruise, and does an eval **fail**
  if it stops doing so?
- Would a multilingual eval have failed *before* the fix? If not, it proves
  nothing.
- Can an operator create, price, illustrate and publish a product without a
  developer, and is publishing something unbookable impossible?
- Does an edit in Vietnamese produce attributed content in seven locales, and
  can you see at a glance which text a human wrote?
- Does re-running translation change nothing when nothing changed — and
  everything affected when the glossary changes?
- Can a partner submit a catalogue, and is it impossible for that content to
  reach a shopper without a human decision?
- After a locale is live, can you tell from the console that it is working —
  zero-result rate, fallback rate, backlog — without asking a shopper?
