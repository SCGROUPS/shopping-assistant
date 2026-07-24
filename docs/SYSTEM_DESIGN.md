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

Conversation state, tool routing, and response narration. Critically, the
assistant is a *consumer* of L4 — it does not re-rank or select products
itself. See [section 7](#7-assistant-design).

---

## 4. Module map

```text
backend/app/
├── api/            routes.py · schemas.py          HTTP contracts
├── search/         service.py · postgres.py        L1–L3 + query engine
├── recommendations/service.py                      recommendation engine
├── assistant/      service.py · provider.py        L5 orchestration + LLM
├── catalog/        seed.py · ingest                products, embeddings
├── commerce/       cart · booking · voucher        simulated purchase
└── common/         ranking.py · persistence.py · config.py

frontend/src/
├── App.tsx                     storefront shell, filter state, cart
├── components/AssistantPanel   assistant surface
├── lib/api.ts                  API client, SSE, normalization
└── types.ts                    shared contracts
```

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

### 5.2 As-built score

```python
score = (0.55 * normalized_rrf
       + 0.15 * preference        # 1.0 if family_friendly matches else 0.7
       + 0.10                     # ← availability_fit, never implemented
       + 0.08 * bayesian_rating
       + 0.07 * popularity_score
       + 0.05)                    # ← commercial_quality, never implemented
```

Two defects:

1. **15% of the weight budget is inert.** The `+0.10` and `+0.05` are constants
   added to every candidate, so they cannot change any ordering. They are
   placeholders for the spec's `availability_fit` and `commercial_quality`
   terms, which were never built.
2. **`preference` is nearly binary**, keyed only on `family_friendly`. The
   spec's richer `preference_fit` is not implemented.

Weights are hardcoded, though `POC_SPEC.md` §11.3 requires them in configuration.

### 5.3 MVP target: rank on expected value

Similarity ranking answers "what is most like the query." A marketplace should
rank on **what the shopper is most likely to book**:

```text
score = P(book | query, context, item) × value(item)
```

MVP-realistic approximation, all terms normalized to `[0,1]` and weights in
configuration:

| Term | Signal | Why it matters for tourists |
|---|---|---|
| `relevance` | normalized RRF | baseline query match |
| `preference_fit` | party, interests, accessibility, language | real constraint satisfaction, not one flag |
| `availability_fit` | slot supply on requested date | never rank an item they cannot book |
| `price_fit` | distance from inferred budget band | strongest observed conversion driver |
| `quality` | Bayesian rating + review volume | trust substitute for an unknown brand |
| `conversion_rate` | observed book/impression, smoothed | learns what actually sells |
| `margin` | commercial value per booking | aligns ranking with revenue |

Rules: keep hard constraints at L1; smooth `conversion_rate` with a Bayesian
prior so new inventory is not starved; log every scoring input so the objective
can be retrained; never let commercial terms override a hard constraint.

---

## 6. Recommendation engine design

### 6.1 As-built score

```python
score = (0.30 * session_similarity
       + 0.20 * context_fit
       + 0.15 * item_similarity
       + 0.12                       # ← availability_fit, never implemented
       + 0.10 * popularity_score
       + 0.08 * bayesian_rating
       + 0.05 * (1 - popularity_score)   # "novelty"
       + 0.12 if complementary and placement in {complete_your_day, complementary})
```

The session vector is a weighted centroid of embeddings of items the shopper
interacted with, weighted by intent strength:

```text
impression 0.1 · view 1.0 · reco click 1.5 · assistant click 2.0
         · add to cart 4.0 · booking 8.0
```

That weighting is sound. The problems are around it.

### 6.2 As-built gaps

**Three of seven terms do not function:**

- `+0.12` is a constant — inert, a stub for `availability_fit`.
- `context_fit` is dead. Candidates are hard-filtered by destination two lines
  earlier, so it is `1.0` for every survivor when a destination is set and
  `0.65` for every survivor when it is not. It never discriminates.
- Popularity **cancels itself**: `0.10·p + 0.05·(1−p)` reduces to
  `0.05 + 0.05·p`. Effective popularity weight is half the intended value, and
  the "novelty" term contributes nothing but a constant.

**Recommendations can surface unbookable items.** The service filters only on
`status` and `destination` — no date, party size, capacity, or budget. A
"Curated for you" card can therefore be sold out or unaffordable. Every such
click is a dead end at the moment of highest intent.

**Cold start is unhandled.** `session_similarity` carries the largest weight
(0.30) but is `0` with no history — i.e. for most first-time tourists, nearly a
third of the scoring budget is inert on top of the constants.
`POC_SPEC.md` §12.4 requires redistributing that weight; it is not implemented.

**No recency decay.** `event_history()` (`common/persistence.py:239`) returns
`(event_type, experience_id)` pairs with the timestamp discarded. A view from
ten days ago counts exactly as much as one ten seconds ago — badly wrong for
same-day tourist booking, where the last few minutes are nearly all the signal.

**Popularity is not behavioural.** `popularity_score` is a static function of
seeded review count (`catalog/seed.py:969`), not observed demand.

### 6.3 MVP target

- Route recommendations through the **same L1 eligibility gate** as search.
- Delete the inert constant; implement real `availability_fit`.
- Collapse popularity/novelty into one honest popularity term; express
  diversity through MMR, which already does that job properly.
- Replace dead `context_fit` with features that vary across survivors
  (date fit, party fit, budget fit, time-of-day compatibility).
- Add **exponential time decay** to event weights.
- Compute popularity and conversion rate from real bookings per destination.
- Redistribute session weight to context/quality/popularity on cold start.

---

## 7. Assistant design

### 7.1 As-built: a router, not a reasoner

`AssistantService.respond()` (`assistant/service.py:211`) calls
`ai.plan_action()`, which asks `gpt-5.4-mini` to pick one tool from a seven-value
enum. But dispatch is an `if/elif` chain of this shape:

```python
elif ("add" in lowered and "cart" in lowered) or planned_tool == "add_to_cart":
```

Because the substring test is evaluated **first**, hardcoded keyword matching
**overrides the planner**. Any message containing "compare" is routed to
compare regardless of intent.

Further limits:

- The model returns a **tool name only** — never arguments. All arguments are
  derived from conversation state by backend code.
- **One tool per turn.** No chaining, no reflection, no retry.
  `assistant_max_tool_rounds` (=3) is dead config.
- `_add_first_result` adds the **first** result, not the item the user named.
- `_search` embeds the **raw user message** with no conversational rewriting.
  "Something cheaper" is embedded literally. Only *filters* carry across turns
  (via `conversation["state"]["filters"]`), not query meaning.

**The LLM never influences which products appear or in what order.** Its three
real jobs are: extract filters from text, hint at a tool name, and rewrite the
final prose (`enhance_assistant`). Assistant quality is therefore hard-capped by
the L4 engines.

A deterministic router is a legitimate MVP choice — cheap, low-latency,
predictable, and resistant to prompt injection through catalog content. It is
documented here as a **deliberate decision**, with the caveat that the
keyword-over-planner precedence is a defect, not part of the design.

### 7.2 MVP target

- Invert precedence: the planner decides; keywords become a **fallback** used
  only when planning fails or returns low confidence.
- Add **conversational query rewriting**: resolve "something cheaper", "the
  second one", "same but indoors" against conversation state before retrieval.
- Resolve referents properly so `add_to_cart` targets the named item.
- Allow a bounded tool loop (honour `assistant_max_tool_rounds`) so the
  assistant can search, notice zero results, relax, and re-search in one turn.
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

### 11.2 As-built gaps

- **`openai_daily_budget` (=10.0) is referenced nowhere outside config.** There
  is no budget breaker and no token accounting. Verified: zero uses.
- **The query embedding cache is a process-local dict**
  (`self.data.query_embeddings`). It is lost on restart and not shared between
  Container Apps replicas, so cost scales with replica count. `POC_SPEC.md`
  §11.4 specifies a bounded `query_embedding_cache` **table** keyed by SHA-256
  of normalized text with LRU eviction — not implemented.
- **Demo-path scoring is O(N)** Python cosine over the full catalog. Fine at
  360 products; not at MVP scale. The PostgreSQL path uses pgvector ANN and is
  the scale path.
- Single replica cap and scale-to-zero-adjacent sizing keep infrastructure cost
  low but concentrate latency risk.

### 11.3 MVP target

Persist the embedding cache to the specified table; add a shared cache across
replicas; add semantic deduplication of near-identical queries; enforce the
daily budget with a circuit breaker that degrades to deterministic intent
extraction rather than failing; record per-request token usage and cost, and
surface cost per session and per booking.

---

## 12. Divergences from POC_SPEC.md

| # | Spec | Implementation | Impact |
|---|---|---|---|
| 1 | §11.3 `availability_fit`, `commercial_quality` scoring terms | bare `+0.10`, `+0.05` constants | 15% of search weight inert |
| 2 | §11.3 weights in configuration | hardcoded | not tunable |
| 3 | §12.4 `availability_fit` | bare `+0.12` constant | inert; unbookable items rank |
| 4 | §12.4 `context_fit` over date/party/budget/language | destination-only, constant post-filter | never discriminates |
| 5 | §12.4 cold-start weight redistribution | absent | 30% inert for new visitors |
| 6 | §11.4 persistent LRU `query_embedding_cache` table | process-local dict | cost scales with replicas |
| 7 | §13.1 "the model selects tools" | keyword match overrides planner | misrouted turns |
| 8 | §13.3 ten tools incl. `get_experience_details`, `get_cart`, `remove_from_cart` | seven-value enum, subset implemented | reduced capability |
| 9 | §13.7 cost controls | `openai_daily_budget` unenforced | no cost ceiling |
| 10 | §11.3 "availability is an eligibility gate" | honoured in search, **not** in recommendations | dead-end clicks |

---

## 13. Observability and experimentation

Application Insights collects logs, metrics, and traces today. The MVP requires
**funnel instrumentation**, which does not yet exist:

- Impression → click → add-to-cart → checkout → booking, attributed per surface
  (grid, assistant, each recommendation placement).
- Assistant-touched vs. untouched session conversion.
- Nudge accept/dismiss rate **per trigger type**; a trigger below roughly 10%
  acceptance is noise and should be removed.
- Zero-result rate and post-relaxation recovery rate.
- Cost per session and per booking.
- An **assistant holdout** cohort.

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

**Phase 1 — foundations (unblocks the rest)**
1. Unify storefront/assistant state with bidirectional sync.
2. Route recommendations through the shared eligibility gate.
3. Fix keyword-over-planner routing precedence.

**Phase 2 — stop the leaks**
4. Graceful constraint relaxation with "we relaxed X" messaging.
5. Fix the cart to allow repeat purchases across dates and slots.
6. Replace stub search controls; render facets and pagination.

**Phase 3 — the assistant experience**
7. Presence and engagement model (§8), including friction triggers, contextual
   cold opens, local entry points, and conversational-query auto-handoff.

**Phase 4 — ranking on revenue**
8. Remove inert terms; implement `availability_fit` and `preference_fit`.
9. Time-decayed behavioural signals and real popularity/conversion aggregates.
10. Expected-value objective with margin and conversion rate.

**Phase 5 — prove and control it**
11. Funnel telemetry, per-surface attribution, assistant holdout.
12. Persistent shared embedding cache, semantic dedupe, budget breaker.

**Phase 6 — commercial levers**
13. Scarcity, social proof, multi-currency, cross-sell, email capture,
    abandoned-cart recovery.

---

## 16. Open questions

- What margin data is available per product? The expected-value objective needs
  it; a proxy (category-level take rate) may be required initially.
- Do we need real supplier availability integration, or does simulated capacity
  remain acceptable through MVP?
- Which currencies must be supported at launch, and from what FX source?
- Is account creation in scope for abandoned-cart recovery, or is email capture
  alone sufficient?
