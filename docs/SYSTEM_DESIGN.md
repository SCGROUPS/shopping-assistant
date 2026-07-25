# Vietra System Design

Status: living document. Describes the **as-built** system and the **MVP target**
design that supersedes it.

Scope note. [POC_SPEC.md](../POC_SPEC.md) remains the historical proof-of-concept
specification and the reference for the data model, API surface, and delivery
phases. This document is authoritative for architecture, ranking design, and the
assistant experience. Where the implementation diverges from `POC_SPEC.md`,
[section 12](#12-divergences-from-poc_specmd) records the divergence explicitly.

---

## 1. Purpose and design intent

Vietra is a Vietnam tourism e-ticket marketplace whose thesis is that an
AI shopping assistant converts tourists better than manual search alone.

Tourists are a specific and demanding shopper segment:

- **Low domain knowledge.** They do not know that Ba Na Hills needs a half-day,
  that Hoi An lantern boats are an evening activity, or which operator is
  reputable.
- **Hard, interacting constraints.** Fixed travel dates, a mixed party
  (toddler, grandparent, wheelchair), weather, and a schedule that must
  physically sequence.
- **Short decision windows.** Booking often happens same-day or next-day, from
  a phone, sometimes on hotel wifi.
- **Low tolerance for dead ends.** They have no loyalty to the site and will
  leave for a competitor rather than refine a fifth time.

Each of those is a case where a knowledgeable human assistant outperforms a
filter grid. That is the value the system must capture, and it is the standard
every design decision below is judged against.

The measurable objective for the MVP is **conversion**, not relevance for its
own sake. Relevance is an input to conversion, not a substitute for it.

---

## 2. System context

```text
┌──────────────┐        ┌─────────────────────────────────────────┐
│   Browser    │  HTTPS │        Container App (single app)        │
│ React 19 SPA │◄──────►│  FastAPI  ·  static SPA  ·  SSE stream   │
│  Web Speech  │        └───────────────┬─────────────────────────┘
└──────────────┘                        │
                     ┌──────────────────┼──────────────────┐
                     ▼                  ▼                  ▼
              ┌─────────────┐   ┌──────────────┐   ┌──────────────┐
              │ PostgreSQL  │   │ Azure OpenAI │   │ App Insights │
              │ 17+pgvector │   │ (Foundry)    │   │  telemetry   │
              │ FTS + ANN   │   │ chat/intent/ │   └──────────────┘
              └─────────────┘   │ embed/image  │
                     ▲          └──────────────┘
                     │
          ┌──────────┴───────────┐
          │ Container Apps Job   │
          │ catalog seed + embed │
          └──────────────────────┘
```

The API and the SPA ship in one container image. A separate Container Apps Job
performs catalog ingestion and embedding generation. There is no separate
search service; retrieval runs inside the API process against PostgreSQL.

**Demo mode** (`DEMO_MODE=true`) swaps PostgreSQL for an in-process store and
Azure OpenAI for deterministic stubs, so the entire storefront, assistant, cart,
payment, and voucher flow runs with no cloud dependency. This is why several
components have dual code paths, and why ranking exists in both a Python and a
SQL implementation.

---

## 3. Layered architecture

The single most important structural fact: **retrieval is a shared foundation,
and there are two independent ranking engines on top of it, not one.**

```text
 L5  ASSISTANT            conversation · routing · narration · actions
        │  (calls L4 as a tool; contributes no product judgement today)
 ─────────────────────────────────────────────────────────────────────
 L4  ENGINES        ┌────────────────────┬────────────────────────┐
                    │  SearchService     │  RecommendationService │
                    │  query-driven      │  query-less            │
                    │  own scorer        │  own scorer            │
                    └────────────────────┴────────────────────────┘
 ─────────────────────────────────────────────────────────────────────
 L3  RANKING             feature scoring · MMR diversification
 L2  RETRIEVAL           lexical + vector candidates → RRF fusion
 L1  ELIGIBILITY         hard constraint gate (status, date, party, budget…)
 L0  CATALOG             products · options · slots · pricing · embeddings
```

### L1 · Eligibility gate

`_eligible()` (`backend/app/search/service.py:210`) is a boolean gate applied
**before** ranking. It enforces publication status, destination, visit date
availability, party size against capacity, budget ceiling, accessibility
requirements, and indoor/outdoor constraints.

Design rule: **hard constraints are gates, never boosts.** A wheelchair user
must never be shown an inaccessible tour ranked slightly lower; it must be
absent. This rule is correct in search today and violated in recommendations
(see [6.2](#62-as-built-gaps)).

### L2 · Retrieval (hybrid candidate generation)

Two independent retrievers produce ranked candidate lists:

| Retriever | Demo path | PostgreSQL path | Candidates |
|---|---|---|---|
| Lexical | token overlap scoring | `tsvector` full-text | `search_lexical_candidates` = 50 |
| Semantic | in-process cosine | `pgvector` ANN | `search_semantic_candidates` = 50 |

They are merged by **Reciprocal Rank Fusion** (`search/service.py:461`,
`search_rrf_k` = 60). RRF is used because it fuses *ranks* rather than scores
and therefore needs no score calibration between a BM25-like signal and cosine
similarity — a pragmatic and correct choice at this scale.

Structured metadata is **not** a third ranked retriever. It acts at L1 as the
eligibility gate and at L3 as scoring features.

### L3 · Ranking

Fused candidates are scored by a weighted feature blend, then diversified with
**MMR** (`common/ranking.py`, `recommendation_mmr_lambda` = 0.75, capped at 2
items per subcategory). Diversification matters commercially: ten near-identical
Hoi An cooking classes is a worse result set than six varied ones, because it
gives the shopper nothing to choose between.

Shared primitives live in `backend/app/common/ranking.py`:
`deterministic_embedding`, `cosine_similarity`, `reciprocal_rank_fusion`,
`bayesian_rating`, `minmax`, `mmr_diversify`.

### L4 · Two engines

| | `SearchService` | `RecommendationService` |
|---|---|---|
| Trigger | user query text | placement, no query |
| Inputs | query, filters, party | session vector, current item, placement |
| Primary signal | fused lexical+vector rank | behavioural session similarity |
| Eligibility | full L1 gate | **status + destination only** |
| Consumers | storefront **and** assistant | "For you", "Complete your day" |

They share L2/L3 primitives but have **separate scoring functions**. Any ranking
change must be applied deliberately to both.

### L5 · Assistant

Conversation state and a bounded tool-using agent loop. The assistant is a
*consumer* of L4: it cannot invent offerings, and every product it presents must
have been returned by a tool. Within that constraint it does reason over results
— selecting, ordering, and justifying a subset, and asking for the search it
actually needs. See [section 7](#7-assistant-design).

---

## 4. Module map

```text
backend/app/
├── api/            routes.py · schemas.py          HTTP contracts
├── search/         service.py · postgres.py        L1–L3 + query engine
├── recommendations/service.py                      recommendation engine
├── assistant/      service.py · provider.py        L5 orchestration + LLM
├── catalog/        seed.py · trippass.py · importer.py  products, embeddings
├── commerce/       cart · booking · voucher        simulated purchase
└── common/         ranking.py · features.py · persistence.py · config.py
                    urgency.py · currency.py       commercial signals (§10)
                    analytics.py                   funnel + holdout (§13)
                    llm_cost.py · embedding_cache.py  cost controls (§11)

frontend/src/
├── App.tsx                     storefront shell, filter state, cart
├── components/AssistantPanel   assistant surface
├── components/CartDrawer       cart, cross-sell rail
├── components/ProductCard      pricing, urgency badges
├── lib/presence.ts             assistant presence and friction rules
├── lib/api.ts                  API client, SSE, normalization
└── types.ts                    shared contracts
```

---

## 4.1 Supplier ingestion

Seeded demo inventory is generated in the internal shape already. Real supply is
not: `app/catalog/trippass.py` imports the Trippass (HeriStep) API, whose
records carry names, descriptions, images and priced variants but **none of the
facets L1-L3 needs** - no destination, category, duration, indoor/outdoor or
accessibility. Its `category_name` mixes places ("Hoi An") with promotions ("Hot
Deal") and vehicle types ("Hoi An E-Car"), so it cannot be used as a facet.

Classification therefore runs **once per listing at import time**, through the
same declared-schema mechanism the assistant uses (`AIProvider.structure` with a
strict function tool). Category and indoor/outdoor are schema `enum`s, so
imported supply is forced into the same vocabulary the storefront filters on
rather than drifting into synonyms. Nothing about this touches the request path.

Three rules keep the import honest:

- **Never invent trust signals.** The feed has no review history, so imported
  products carry `rating = 0, review_count = 0`. Ranking's Bayesian prior
  (`bayesian_rating`) reads that as *unproven* and pulls it to the catalogue
  mean, so new supply competes fairly without a fabricated score. The UI shows
  "Newly listed" rather than `0.0 ★`.
- **Degradation is visible.** If classification fails - a rate limit, an outage
  - the listing is stored with defaults and flagged `needs_review`, and the
  CLI reports the count. The first import silently filed a Hoi An museum under
  Da Nang this way; normalisation now backs off through the deployment's rate
  limit before conceding.
- **Re-import must not be destructive.** `app/catalog/importer.py` upserts on
  `Experience.external_id` and replaces only that experience's children, so
  prices and variants resync while experience ids - and the carts, bookings and
  behaviour events pointing at them - survive. Options still referenced by a
  live cart are kept. Seeding truncates; importing never does.

The supplier's stock endpoint is unavailable in staging, so imported
availability is published on a fixed daily schedule rather than read from the
supplier. This is the one part of an imported product that is not authoritative.

The catalog job runs the import after seeding on every deploy, non-fatally: a
supplier outage logs and continues rather than failing the deployment.

---

## 5. Search engine design

### 5.1 Pipeline

```text
query ─► intent extraction (gpt-5-nano, conditional)
      ─► sanitize_intent()        drop hallucinated / empty constraints
      ─► merge_filters()          reconcile with explicit UI filters
      ─► L1 eligibility gate
      ─► lexical ∥ semantic retrieval
      ─► RRF fusion → min-max normalize
      ─► final scoring
      ─► sort → page → facets
```

`should_extract_intent()` (`:76`) suppresses the LLM call for short or clearly
structured queries — the primary per-query cost control.

`sanitize_intent()` (`:88`) exists because the intent model was observed
producing hallucinated dates, list-valued `category` where a scalar was
required, and empty constraints with an `unspecified` operator — which then
forced a spurious clarification and returned zero results. It drops date
constraints absent a date cue in the query, drops empty/`unspecified`
constraints, drops list-valued `category`, and clears `needs_clarification`.
**Treat generated constraints as untrusted input**; the sanitizer is a required
safety layer, not a workaround.

### 5.2 Scoring: expected value

Similarity ranking answers "what is most like the query." A marketplace must
rank on **what the shopper is most likely to book, weighted by what that
booking is worth**:

```text
score ≈ P(book | query, context, item) × value(item)
```

Implemented as a linear blend of `[0,1]` features, with every weight in
configuration (`common/config.py`, `search_weight_*`, summing to 1.0):

| Term | Weight | Signal | Why it matters for tourists |
|---|---|---|---|
| `relevance` | 0.45 | normalized RRF | baseline query match |
| `preference_fit` | 0.14 | party, duration, rating, indoor/outdoor, instant confirmation, free cancellation, language breadth | real constraint satisfaction, not one flag |
| `availability_fit` | 0.12 | usable slots and capacity headroom on the requested dates | never rank an item they can barely book |
| `price_fit` | 0.11 | distance from the budget band | strongest observed conversion driver |
| `quality` | 0.07 | Bayesian rating + review volume | trust substitute for an unknown brand |
| `conversion_rate` | 0.06 | observed bookings ÷ exposure, Bayesian-smoothed | learns what actually sells |
| `margin` | 0.05 | category take rate (proxy, §16) | aligns ranking with revenue |

Design rules that the implementation enforces:

- **Hard constraints stay at L1.** Commercial terms can reorder candidates but
  can never resurrect one the shopper cannot book.
- **`availability_fit` measures margin above the bare minimum.** Eligibility
  already guarantees one bookable slot; a single remaining seat at one fixed
  time converts far worse than open choice, so supply breadth (saturating at
  four usable slots) and capacity headroom are scored, not just presence.
- **`price_fit` peaks below the ceiling**, at ~75% of budget. An option that
  consumes the entire budget leaves nothing for the rest of the trip. With no
  stated budget the median party total of the eligible set is the reference.
- **`conversion_rate` is smoothed** toward a prior (`conversion_prior_rate`
  0.02, strength 40) so new inventory is not starved and a single lucky
  booking cannot outrank a well-measured item. It is then mapped through
  `rate / (rate + prior)`, which puts the prior at 0.5 — dividing by the prior
  and clamping would park every unobserved item at the ceiling and recreate the
  inert-constant defect this term exists to remove.
- **Relevance still dominates at 45%.** Commercial terms together carry 11%:
  enough to break ties toward what sells, never enough to float an irrelevant
  result above a relevant one.

Features live in `common/features.py` so both engines compute "fit" identically;
if they diverged, the grid and the recommendation rail would argue with each
other.

> **Historical note.** The POC score was
> `0.55·rrf + 0.15·preference + 0.10 + 0.08·rating + 0.07·popularity + 0.05`.
> The `+0.10` and `+0.05` were constants added to every candidate — stubs for
> the spec's `availability_fit` and `commercial_quality` — so 15% of the weight
> budget could not change any ordering, and `preference` keyed only on
> `family_friendly`.

---

## 6. Recommendation engine design

### 6.1 Scoring

The rail answers a different question from search — "what else, given who this
shopper is" — so it keeps its own objective, but draws its features from the
same `common/features.py` module. Weights are in configuration
(`recommendation_weight_*`):

| Term | Weight | Signal |
|---|---|---|
| `session_similarity` | 0.30 | cosine against the time-decayed session vector |
| `context_fit` | 0.18 | blend of availability, preference and price fit |
| `item_similarity` | 0.15 | cosine against the item being viewed |
| `popularity` | 0.15 | observed demand, review-count proxy only until demand exists |
| `availability_fit` | 0.12 | usable slots and capacity headroom |
| `quality` | 0.10 | Bayesian rating |
| complement bonus | +0.12 | category complements the current item, on `complete_your_day` / `complementary` |

**The session vector** is a weighted centroid of embeddings of items the shopper
engaged with, weighted by intent strength and multiplied by exponential recency
decay (`behaviour_half_life_seconds`, default 30 minutes):

```text
impression 0.1 · view 1.0 · reco click 1.5 · assistant click 2.0
         · add to cart 4.0 · booking 8.0
```

Decay matters because tourists book same-day: a view from ten minutes ago and
one from three days ago carry very different intent, and treating them as equal
makes the rail chase stale interests.

**Cold start is handled explicitly.** With no history, `session_similarity`
would be dead weight, so its 0.30 is redistributed to the terms that still
discriminate for a first-time visitor — context fit (40%), popularity (30%),
quality (20%), availability (10%) — as `POC_SPEC.md` §12.4 requires.

**Popularity is a measurement, not a seed.** `demand_stats()` aggregates
impressions, views, cart adds and bookings per experience. The seeded
`popularity_score` is now only a cold-start prior: as observed demand for an
item accumulates, confidence shifts the term onto real behaviour.

**Eligibility is shared.** Recommendations run through the same L1 gate as
search (§9), with two-tier relaxation that never relaxes bookability,
accessibility or exclusions — so a "Curated for you" card is always something
the shopper can actually buy.

**Diversity is MMR's job**, not a scoring term. The POC added
`0.05·(1 − popularity)` as "novelty", which collapses algebraically into
`0.05 + 0.05·popularity` — halving the intended popularity weight and adding a
constant.

> **Historical note.** The POC score was
> `0.30·session + 0.20·context_fit + 0.15·item + 0.12 + 0.10·pop + 0.08·rating + 0.05·(1−pop)`.
> Three of seven terms did not function: `+0.12` was an `availability_fit` stub;
> `context_fit` was a destination check applied *after* destination filtering,
> so it was constant across all survivors; and popularity self-cancelled as
> above. Recommendations also bypassed the eligibility gate entirely, discarded
> event timestamps, and left the 0.30 session weight inert for every first-time
> visitor.

---

## 7. Assistant design

### 7.1 As-built: a tool-using agent

`AssistantService.respond()` (`assistant/service.py:293`) runs a bounded
**agent loop** (`AzureOpenAIProvider.run_agent`, `assistant/provider.py:204`).
`gpt-5.4-mini` is given the shopping tools as native Responses-API function
tools and decides, per step, which tool to call and **what arguments to fill**.
The service executes the call, feeds the JSON result back, and lets the model
reason over it. `MAX_AGENT_STEPS` (=4) bounds the loop.

Two rules shape the whole design:

**1. Language interpretation belongs to the model, not to regex.** There is no
keyword router, no substring precedence, no `re.findall` intent extraction. The
shopper's words reach the model intact and the model fills the parameters. A
phrase like "somewhere in the ocean, not mountain" is understood as a positive
query plus a negative constraint because the model reads it, not because a
pattern matched.

**2. Business logic belongs to the tools, not to the service.** Exclusion is a
declared capability of `search_experiences` (an `exclude` array), not a special
case in `_search`. The service never inspects the message to decide behaviour;
it only executes what the agent asked for. Adding a capability means adding a
tool parameter, not another branch.

#### Tool contract

`SHOPPING_TOOLS` (`provider.py:63`) declares six tools:

| Tool | Agent-filled arguments |
| --- | --- |
| `search_experiences` | `query`, `destination`, `exclude`, `max_total_price`, `limit` |
| `get_recommendations` | `experience_id` |
| `check_availability` | `experience_id`, `date`, `travellers` |
| `add_to_cart` | `experience_id`, `date`, `travellers` |
| `prepare_checkout` | — |
| `confirm_simulated_checkout` | — |

Only `search_experiences` carries a `query`. This is deliberate and is the fix
for a real defect: the previous router sent "recommend some good Pho
restaurants in Hanoi old quarter" to a cross-sell tool that had no query
parameter, so the shopper's words were **structurally discarded** and Mui Ne
cycling tours came back. A tool that cannot accept a subject must never be
chosen to answer a request that names one, and the tool descriptions say so.

Tools use **strict** schemas, so every declared property appears in `required`
and optional arguments are expressed as nullable types
(`{"type": ["string", "null"]}`). A test pins this invariant.

#### The grounding convention

The agent does not answer in prose. It terminates by calling `final_answer`
(`provider.py:132`) with:

```json
{
  "message": "...",
  "selections": [{ "experience_id": "exp_123", "reason": "..." }],
  "clarification": "..."
}
```

`selections` is the contract that makes an answer **renderable**: each entry
names a real offering, so the UI shows bookable cards with live price and
availability rather than a wall of text, and each `reason` is the model's own
justification for that specific item.

`_build_answer` (`provider.py:358`) enforces the contract. Every tool result is
serialised by `_offering` (`service.py:83`) with an `experience_id`, and the
loop accumulates the set of ids the tools actually returned. Any
`experience_id` in `selections` that is **not** in that set is dropped. A
hallucinated offering therefore cannot reach the shopper.

Because the model reasons *over* tool output rather than merely triggering it,
narration and results can no longer contradict each other — the earlier failure
where Mai described a cruise as the ocean match while the rail still showed
Marble Mountains is not expressible in this design.

#### Failure behaviour

`_run_agent` (`service.py:417`) falls through to the legacy deterministic path
when reasoning is unavailable: demo mode (`DemoAIProvider.run_agent` returns
`None`) or a tripped budget breaker (`BudgetExceeded`). Within the loop, a tool
that raises `ApiError` returns `{"error", "detail"}` **to the agent** rather
than failing the turn, so it can correct its arguments and retry inside the
step budget.

`confirm_simulated_checkout` always returns `confirmation-required`: the agent
can prepare a booking but can never self-confirm one.

The deterministic fallback remains weak — it ranks on keyword overlap and will
answer "not mountain" with mountain results. It is a degraded mode, and per the
architecture rules above it will not be improved with more pattern matching.

### 7.2 MVP target

- Add **conversational query rewriting** so "something cheaper" and "the second
  one" resolve against conversation state before the agent searches.
- Replace the deterministic fallback with a cheaper model rather than regex, so
  degraded mode is still language-aware.
- Tune `MAX_AGENT_STEPS` against observed latency; consider streaming the
  narration while tool calls are still in flight.
- Keep all mutation server-side, validated, idempotent, and audited.

### 7.3 Grounding and injection controls

The model may only describe facts returned by tools. Catalog content is
untrusted and cannot redefine tools or instructions. `confirm_simulated_checkout`
runs only when the immediately preceding response displayed the final booking
summary and the user explicitly confirmed.

---

## 8. Assistant presence and engagement model

This section defines *how the assistant shows up*. It is the design most
directly tied to the conversion thesis, and it is currently unimplemented.

### 8.1 Principle

> **Ambient, not interruptive. Earned, never automatic.**

Model a good human shop assistant: visibly available on entry, quiet while the
customer browses, and stepping in exactly when the customer looks stuck. The
assistant is always reachable at zero cost, and speaks first **only** when it
has a specific observed reason.

**The panel never auto-opens on page load.** Auto-opening is what makes an
assistant read as a spam widget, and it destroys the quality this design
depends on. Presence is a standing offer, not a tap on the shoulder.

Current behaviour to replace: a generic floating bubble (`App.tsx:816`) opening
to a **fixed canned greeting** identical for a first-time visitor and for
someone who just got zero results on a fourth refinement.

### 8.2 Three first-class entry paths

1. **Assistant-first.** The shopper opens it and starts there.
2. **Self-serve, then handoff.** The main path. The shopper browses, hits
   friction, and turns to the assistant mid-task with full context carried over.
3. **Search-box handoff.** The hero search accepts natural language. When a
   query is clearly conversational — multi-constraint, narrative, or
   question-shaped ("rainy day with a toddler and grandma in a wheelchair") —
   the assistant **opens automatically and answers it**, rather than returning
   poor keyword results. This is the one sanctioned auto-open: it is a direct
   response to an explicit user submission, not an unprompted interruption.

Classification runs on the query before retrieval and must be conservative:
short keyword queries ("hoi an cooking class") always stay in the grid.

### 8.3 Contextual invitation

The launcher label and the assistant's opening line are **functions of observed
state**, not constants:

| Observed state | Offer |
|---|---|
| Fresh visitor | Quiet default — "Ask me anything" |
| Zero results | "Nothing matched — want me to widen the dates?" |
| 3+ refinements, no click | "Narrowing this down? I can help." |
| 3+ similar items viewed | "Want me to compare these three?" |
| Cart items overlap in time | "These two clash at 2pm — want me to re-time one?" |
| Checkout hesitation | "Questions before you book?" |

These fire on **detected friction**, never on a timer or scroll depth. A real
assistant approaches when you look lost, not after thirty seconds.

Guardrails: at most one nudge live at a time; always dismissible; never
re-fires after dismissal within a session; never covers the primary CTA; the
nudge is a line of text **on the launcher** and never opens the panel itself.

### 8.4 Local entry points

Global bubble alone is not enough. Add subject-carrying entry points, which
convert better because the question already has an object:

- **"Ask about this"** on a product card → carries that product.
- **"Help me choose"** on comparison views → carries the compared set.
- **"Check my plan"** on the cart → carries cart contents and timing.

### 8.5 Continuity rules

- **Reflect context once, then stop.** Opening after a failed search should
  begin: "You're after wheelchair-accessible tours in Hoi An on Aug 15 —
  nothing matched. Want me to try Aug 16–17?" Not "Hi! How can I help?"
  Repeatedly demonstrating what it knows reads as surveillance.
- **Closing is not resetting.** Dismissing the panel preserves the thread; the
  launcher shows a subtle continue affordance while a conversation is live.
- **Non-exclusive layout.** On desktop the panel sits beside results, not over
  them. On mobile it is a partial-height bottom sheet, not a full-screen
  takeover — full screen forces an either/or between modes, which is exactly
  the split this design exists to prevent.
- **Trust boundary.** Only ever reflect back on-site actions from the current
  session. Never surface inferred nationality, income, or personal traits.

---

## 9. Unified context and session state

### 9.1 The problem

The storefront and the assistant keep **separate, unsynchronized state**.
`sendAssistantMessage` (`App.tsx:274`) transmits only the message string; the
selected date, destination, and guest count never reach the assistant. In the
other direction, the assistant maintains `conversation["state"]["filters"]`
server-side and never pushes it back to the grid.

The user-visible symptom is an assistant that "isn't listening", and a mode
switch that silently discards the shopper's work.

### 9.2 Target: one engine, one state, two renderings

Manual controls and the assistant both mutate the **same filter object**. The
assistant is a faster input method for the same search — not a parallel
universe.

**Handoff payload** carried on every assistant open and message:

| Field | Purpose |
|---|---|
| `filters` | date, destination, guests, budget, accessibility |
| `last_query` + `result_count` | enables "nothing matched" cold opens |
| `recently_viewed[]` | comparison and preference inference |
| `cart[]` | timing conflicts, cross-sell, checkout help |
| `focused_product` | set when launched from a card |

**Bidirectional sync is the requirement that matters.** When the assistant
relaxes a date or changes destination, the grid behind the panel must visibly
update. That single behaviour is what makes the experience read as one
salesperson working the session rather than a chatbot in an iframe.

---

## 10. Conversion design

Ranking quality is wasted if the funnel leaks. Known leaks and their fixes:

| Leak | Current behaviour | Fix |
|---|---|---|
| **Zero-result dead end** | All constraints hard-filtered; user sees "No exact match yet". `relaxed_preferences` exists (`schemas.py:248`) but is **never populated anywhere**. | Progressive relaxation of the lowest-value constraint, results returned with explicit "we relaxed X" messaging. |
| **Repeat purchase blocked** | `addToCart` rejects any duplicate experience ID — a family cannot book two time slots or two dates of the same tour. | Key on (experience, option, slot). |
| **Stub controls** | Hardcoded date `2026-08-15`; two-value destination toggle; click-to-increment guests; "All filters" with a hardcoded `3` badge and no handler. | Real date picker, destination search, party composition editor, working filter panel. |
| **Discarded backend work** | `facets` computed server-side and never rendered; `cursor` pagination unused; only 8 results shown; category tabs filter client-side over that page. | Render facets, wire pagination, move category filtering server-side. |
| **No trust or urgency signals** | No scarcity, no review snippets, no social proof. | Remaining-capacity indicators, review excerpts on cards. |
| **Currency mismatch** | Button reads "USD" while prices render in VND. | Real multi-currency for tourists. |
| **No recovery** | No email capture, no abandoned-cart flow, no cart/checkout cross-sell. | Add all three; `complete_your_day` is currently reachable only via the assistant. |

Graceful relaxation is the highest-ROI item: it converts the site's most common
failure state from an exit into a conversation.

### 10.1 Honest urgency

`common/urgency.py`. Scarcity and social proof are the easiest features in
commerce to fake and the fastest way to lose a first-time buyer, so both are
built to stay **silent by default** and speak only from inventory and
measured demand.

`scarcity(product, filters, party)` reads the slots the shopper could actually
book — active options, `AVAILABLE` status, inside the requested date window,
with `capacity_remaining >= party_size`. It returns `None` when supply is
comfortable. Three honest statements are possible:

| Condition | Message |
|---|---|
| ≤ `BUSY_DAY_THRESHOLD` (3) usable slots, tightest ≤ `SCARCITY_THRESHOLD` (6) | "Only N places left" |
| ≤ `BUSY_DAY_THRESHOLD` usable slots, capacity comfortable | "Just N times left in your dates" |
| Plenty of slots, but the tightest ≤ `SCARCITY_THRESHOLD` | "Some times down to N places" |

Because the badge is filter-aware, it is a **statement about the shopper's
trip**, not about the product: widening dates can legitimately remove it.

`social_proof(stats)` reports only aggregated behavioural events —
`bookings`, then `cart_adds`, then `views` — each behind
`SOCIAL_PROOF_FLOOR` (5; views at 4×). It deliberately refuses to dress the
seeded `review_count` up as recent demand: a review count is not evidence that
anyone booked recently, and presenting it as such would be the dishonest
version of this feature. Quiet inventory therefore shows nothing.

The seed models a **demand profile** rather than uniform supply (~12% of
experiences in demand, ~10% small-group formats on a limited cadence, the rest
comfortable). Uniform capacity made scarcity permanently silent and left
`availability_fit` unable to discriminate; the fix belongs in the data, not in
a lower threshold.

**Seeded data does not converge on its own.** `seed_database()` returns early
when a catalogue already exists, so changing the seed has no effect on any
environment that has been seeded once — the demand profile above reached
production only after this was addressed. `force=True` is not the answer: it
truncates `behavior_events`, `shopping_sessions` and `bookings`, destroying the
measurement data that §13 depends on and that `social_proof` is computed from.

`refresh_availability()` (`catalog/db_seed.py`, exposed as
`python -m app.catalog.cli refresh-availability` and run by the catalog job on
every deploy) closes the gap. Slot ids are
`stable_id("slot", f"{slug}:{starts.isoformat()}")` — deterministic in slug and
start time — so the same generator upserts by id: overlapping dates are
refreshed in place with the current supply profile, and dates beyond the
previous horizon are inserted, which also rolls the booking window forward on
each deploy. Capacity already consumed by confirmed bookings is subtracted, so
a refresh can never resurrect inventory that was sold, and remaining capacity is
floored at zero. Behavioural history is untouched.

Note when reading the API that `product_detail` caps each option at 24 slots,
so the *visible* window is narrower than the data — an experience running two
departures a day shows about twelve days even though roughly thirty are
stored. A short window in the response is a display cap, not expired
inventory.

### 10.2 Display currency

`common/currency.py`. Conversion is **presentation-only and server-side**.
`price`/`currency` remain VND and authoritative; `display_price`/
`display_currency` are additive fields on `ExperienceCard`. Nothing that
charges money reads the FX table, and the card always renders the VND amount
alongside the converted figure, so a tourist can see what their card will
actually be billed. Because conversion happens server-side, changing currency
refetches rather than reformatting client-side. Rates are currently static
(§16).

### 10.3 Cross-sell placement

`complete_your_day` was reachable only through the assistant, which meant the
highest-intent moment in the funnel — a shopper who has just added an item —
had no complement offer at all. The cart drawer now renders a cross-sell rail
sourced from the recommendation engine, tagged with the `cart_cross_sell`
placement so its contribution is attributable in the funnel report (§13).

### 10.4 Booking attribution

A booking is the heaviest signal the product has: weight 8.0 in the session
vector and the numerator of every conversion rate. It was being recorded as a
single event with **no `experience_id`**, so `demand_stats` — which filters on
that column — discarded every booking. Popularity, conversion lift and social
proof were all being computed as if nothing had ever been bought. Checkout now
reads the cart *before* confirmation clears it and emits one
`booking_completed` per booked experience, carrying the originating surface
so the assistant-versus-grid comparison survives to the point of sale. The
client no longer emits a duplicate event of its own.

---

## 11. Cost architecture

### 11.1 Per-request cost model

| Path | LLM calls |
|---|---|
| Structured/short search | 0 (suppressed by `should_extract_intent`) |
| NL search, cache hit | 1 intent (`gpt-5-nano`) |
| NL search, cache miss | 1 intent + 1 embedding |
| Assistant turn | 1 plan + 1 `enhance_assistant` (`gpt-5.4-mini`) + search cost |

Model tiering is deliberate: `gpt-5-nano` for high-frequency structured
extraction, `gpt-5.4-mini` reserved for user-visible prose, no frontier model
in the normal path.

### 11.2 Shared embedding cache

Tourist search traffic is head-heavy — the same few dozen queries arrive
constantly — so caching query embeddings is the single largest cost lever in
the system. `common/embedding_cache.py` implements two tiers:

1. A bounded in-process LRU (512 entries) absorbs the hot head with no round
   trip.
2. The `query_embedding_cache` table (`POC_SPEC.md` §11.4) shares everything
   else across replicas and survives restarts, keyed by SHA-256 of
   `model + normalized text`.

Normalization folds case and whitespace, so "Hoi An  Lantern Tour" and "hoi an
lantern tour" are one entry rather than two paid calls. Cache failures are
logged and swallowed: an optimization must never be able to break a search.

### 11.3 Budget breaker

`common/llm_cost.py` records estimated spend per call, keyed by UTC day and
broken down by purpose. Once `openai_daily_budget` is reached, the provider
raises `BudgetExceeded` **instead of** calling the model.

This degrades rather than fails, which is only safe because every LLM call site
already has a deterministic fallback: keyword intent parsing, hash embeddings,
keyword tool routing and templated assistant prose. A storefront that still
sells beats one that returns errors.

Catalogue ingestion (`embed_many`) is deliberately exempt from the breaker —
it is an operator task with no fallback, and blocking it would leave the
catalogue unsearchable — but its spend is still recorded so the ceiling
reflects reality.

Current spend is exposed at `GET /api/v1/analytics/funnel` under `cost`.

**Known limit:** the ledger is process-local, so with N replicas the effective
ceiling is N × budget. An exact ceiling needs a shared counter in PostgreSQL or
Redis. An approximate breaker that exists is worth far more than an exact one
that does not.

### 11.4 Remaining cost gaps

- **Demo-path scoring is O(N)** Python cosine over the full catalog. Fine at
  360 products; not at MVP scale. The PostgreSQL path uses pgvector ANN and is
  the scale path.
- **Semantic deduplication** of near-identical (not merely case-different)
  queries is not implemented; it needs an embedding to decide, which is the
  cost it would avoid. Worth revisiting only if cache-miss rate stays high.
- Single replica cap and scale-to-zero-adjacent sizing keep infrastructure cost
  low but concentrate latency risk.

---

## 12. Divergences from POC_SPEC.md

Resolved during the MVP build:

| # | Spec | Resolution | Phase |
|---|---|---|---|
| 1 | §11.3 `availability_fit`, `commercial_quality` scoring terms | both implemented as real features; inert constants removed | 4 |
| 2 | §11.3 weights in configuration | all `search_weight_*` / `recommendation_weight_*` in `config.py` | 4 |
| 3 | §12.4 `availability_fit` | real slot-supply and capacity-headroom feature | 4 |
| 4 | §12.4 `context_fit` over date/party/budget/language | blend of availability, preference and price fit | 4 |
| 5 | §12.4 cold-start weight redistribution | session weight redistributed when no history exists | 4 |
| 6 | §11.4 persistent LRU `query_embedding_cache` table | two-tier cache: in-process LRU over the shared table | 5 |
| 7 | §13.1 "the model selects tools" | planner now outranks keyword matching | 1 |
| 9 | §13.7 cost controls | daily budget breaker degrading to deterministic paths | 5 |
| 10 | §11.3 "availability is an eligibility gate" | recommendations share `is_eligible` with search | 1 |
| 13 | §12.4 scarcity and social proof signals | `common/urgency.py`, silent unless earned | 6 |
| 14 | multi-currency display for tourists | `common/currency.py`, presentation-only | 6 |
| 15 | cross-sell outside the assistant | cart drawer rail, `cart_cross_sell` placement | 6 |
| 19 | seed-data changes reaching seeded environments | `refresh_availability()` on every deploy (§10.1) | 6 |

Outstanding:

| # | Spec | Implementation | Impact |
|---|---|---|---|
| 8 | §13.3 ten tools incl. `get_experience_details`, `get_cart`, `remove_from_cart` | six agent tools (§7.1); the read-back tools are absent | agent cannot inspect cart or product detail mid-conversation |
| 11 | §11.3 margin term | category take-rate proxy; no real margin data | ranking approximates revenue (§16) |
| 12 | §13.7 exact cost ceiling | per-process ledger | effective ceiling is N × budget |
| 16 | email capture and abandoned-cart recovery | not implemented | deferred pending the account-scope decision in §16 |
| 18 | full-journey integration coverage over PostgreSQL | gated on `POSTGRES_SEEDED_TEST_DATABASE_URL`; needs a seeded catalogue and an LLM provider, so CI covers analytics SQL only | database-only defects outside analytics still reach deployment |
| 17 | live FX rates | static table in `currency.py` | displayed prices drift from market (§16) |

---

## 13. Observability and experimentation

Application Insights collects logs, metrics, and traces. On top of that,
`common/analytics.py` computes the product funnel from behaviour events, served
at `GET /api/v1/analytics/funnel`.

### 13.1 What is measured

| Metric | Why it exists |
|---|---|
| Impression → view → cart → checkout → booking, **per surface** | Without attribution we cannot tell whether the grid, the assistant, or a recommendation rail earned a booking, so we cannot decide where to invest. |
| Assistant-touched vs. untouched session conversion | The direct read on the core thesis. |
| Nudge shown / accepted / dismissed, **per trigger** | A nudge that is shown and never accepted is an interruption, not a service. Per-trigger granularity lets a single bad trigger be retired on evidence rather than the whole mechanism being abandoned. |
| Zero-result rate and post-relaxation recovery rate | Measures whether §5.1 relaxation actually rescues dead-end searches. |
| Daily LLM spend by purpose, against budget | Cost per session becomes observable rather than inferred. |

Every stage carries a `placement`, set by the frontend at the point of action:
`grid` or `assistant` today, extensible per recommendation rail.

Rates are reported as `null`, never `0`, when there is no evidence — an
unmeasured rate is unknown, and rendering it as 0% looks like failure.

Analytics has two implementations — an in-memory one for demo mode and a SQL
one for the database — and only the first was reachable from CI. The SQL branch
shipped a `GROUP BY` defect that no demo-mode test could detect, and a
deployment found it. CI now runs a throwaway PostgreSQL service so the SQL
aggregation is executed on every change (`tests/test_analytics_postgres.py`).

### 13.2 The assistant holdout

`assistant_holdout_rate` (default `0.0`) assigns a share of sessions to a
control group that never sees the assistant: no launcher, no nudges, no
subject-carrying entry points, no promo section. Assignment is a deterministic
hash of the session id, so a shopper's experience never flips mid-visit and the
assignment survives a restart without being stored.

The frontend resolves its cohort from `GET /api/v1/session/context` during
bootstrap, before first paint, so a holdout session never briefly sees the very
thing it is meant to be a control for. If that call fails, the assistant is
assumed **enabled** — a telemetry outage must not silently remove the product.

Without a holdout the core thesis — that the assistant beats manual search —
is unfalsifiable, and therefore untunable.

---

## 14. Security, privacy, reliability

- Anonymous sessions; no shopper accounts or PII collection in the POC.
- All mutations are server-side, session-validated, idempotency-keyed, and
  audited. Idempotency is verified under concurrency by integration test.
- Catalog content is untrusted input to the model and cannot alter instructions.
- Generated constraints are sanitized before reaching the query planner.
- Payment is simulated end to end; no real payment data exists.
- PostgreSQL uses `AllowAzureServices`, an explicit low-cost POC trade-off to be
  replaced with private networking before production.
- Secrets are Container Apps secrets; Azure OIDC federation is used for CI/CD.
- Assistant turns are capped (`assistant_max_session_turns` = 12).

---

## 15. MVP roadmap

Ordered by conversion impact per unit of effort. Funnel leaks precede ranking
work deliberately: better ranking into a leaking funnel returns little.

**Phase 1 — foundations (unblocks the rest)** ✅
1. Unify storefront/assistant state with bidirectional sync.
2. Route recommendations through the shared eligibility gate.
3. Replace keyword routing with a tool-using agent (§7.1).

**Phase 2 — stop the leaks** ✅
4. Graceful constraint relaxation with "we relaxed X" messaging.
5. Fix the cart to allow repeat purchases across dates and slots.
6. Replace stub search controls; render facets and pagination.

**Phase 3 — the assistant experience** ✅
7. Presence and engagement model (§8), including friction triggers, contextual
   cold opens, local entry points, and conversational-query auto-handoff.

**Phase 4 — ranking on revenue** ✅
8. Remove inert terms; implement `availability_fit` and `preference_fit`.
9. Time-decayed behavioural signals and real popularity/conversion aggregates.
10. Expected-value objective with margin and conversion rate.

**Phase 5 — prove and control it** ✅
11. Funnel telemetry, per-surface attribution, assistant holdout.
12. Persistent shared embedding cache, budget breaker. (Semantic dedupe
    deferred: it needs an embedding to decide, which is the cost it avoids.)

**Phase 6 — commercial levers** ✅
13. Honest scarcity and social proof (§10.1), display currency (§10.2), cart
    cross-sell (§10.3), and per-experience booking attribution (§10.4).

    Deferred: **email capture** and **abandoned-cart recovery**. Both require a
    product decision that is not ours to make — whether accounts are in scope,
    and what consent basis applies to a tourist's email address under GDPR
    (§16). Building either behind a guess would create a data-retention
    obligation we have not designed for.

---

## 16. Open questions

- What margin data is available per product? The expected-value objective needs
  it; a proxy (category-level take rate) may be required initially.
- Do we need real supplier availability integration, or does simulated capacity
  remain acceptable through MVP?
- Which currencies must be supported at launch, and from what FX source? Eight
  are supported today against a static table (`common/currency.py`); a live
  rate feed with a staleness policy is needed before real money is quoted.
- Is account creation in scope for abandoned-cart recovery, or is email capture
  alone sufficient? This blocks the two deferred Phase 6 items, and carries a
  consent and retention question for tourist email addresses under GDPR.
