# Vietra Production Readiness

Status: living document. Companion to [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md),
which describes how the system works. This document assesses **how far it is
from running a real business**, and specifies the work that closes the gap.

Audience: engineering, plus the commercial and operations people who will have
to live with whatever we ship.

---

## 1. The headline

Vietra is a **strong engine with no operating surface**.

Everything the software does on its own — retrieve, rank, reason, converse,
transact, measure — is built to a genuinely high standard. There is a shared
eligibility gate, a hybrid retrieval stage, an expected-value ranking objective,
a tool-using agent grounded to real inventory, funnel telemetry, a cost breaker
and an experiment holdout. That is more rigour than most storefronts of this
age carry.

Everything a **human being at the business** needs to do is either impossible or
requires an engineer and a deploy:

| A normal Tuesday at a real marketplace | What happens in Vietra today |
| --- | --- |
| A supplier's product is filed under the wrong city; a customer complains | No way to find it, no way to edit it, and a hand-edit to the database is silently reverted by the next deploy's import |
| Marketing wants to push Da Nang cruises for two weeks | No merchandising controls at all. The only lever is a hardcoded Python dict of take rates, changed by an engineer |
| Conversion drops 8%; ranking weights are suspected | Weights are environment variables behind an `lru_cache`. Changing one needs a redeploy and a restart, applies to 100% of traffic at once, and cannot be compared against the old behaviour |
| A supplier withdraws a product this morning | Only via the automated import; there is no manual "take it down now" |
| CX wants the assistant to stop recommending alcohol tours to family accounts | The policy lives in a Python string constant |

None of these are algorithm problems. **The distance from MVP to production is
mostly an operations-surface problem, and secondarily a control-loop problem.**
That is good news: it is tractable, and it is what this document specifies.

### 1.1 Verdict by area

| Area | State | Distance to production |
| --- | --- | --- |
| Retrieval and ranking mechanics | Strong | Short |
| Assistant reasoning and grounding | Strong | Short |
| Commerce (cart, idempotency, booking) | Solid | Short |
| Cost control | Solid | Short |
| Telemetry | Good counts, no decision loop | Medium |
| **Catalog operations** | **Absent** | **Long — blocking** |
| **Business configuration** | **Code-and-deploy only** | **Long — blocking** |
| **Quality measurement** | **Absent** | **Long — blocking** |
| Access control and audit | Spoofable header; no audit | Blocking |
| Real supplier availability | Synthesised | Blocking for real inventory |
| Accounts, payments, FX | Simulated or static | Blocking for real money |

---

## 2. How adaptable is it, really?

"Adaptable" is untestable as an adjective, so this section fixes it to
scenarios. Each is a change a tourism marketplace genuinely faces. The question
is: **what does it cost to absorb it?**

### 2.1 Product recommendation

**Scenario A — a new commercial priority.** A supplier signs a higher-commission
deal; their inventory should surface more.

Today, commercial value is `CATEGORY_TAKE_RATE`, a dict in
`app/common/features.py` keyed by category. It cannot express a supplier, a
product, a date range or a campaign. Absorbing this change means editing Python.
There is also no per-supplier commission on the `Supplier` row to edit *toward* —
the commercial model simply is not in the data.

**Scenario B — the ranking is wrong for a segment.** Families bounce; the
suspicion is that `price_fit` is over-weighted for parties of four.

The weights exist and are named, which is better than most systems. But they are
global constants, applied to everyone, changeable only by redeploy. There is no
segmentation, no way to run the change against 10% of traffic, and — the deeper
problem — **no way to tell afterwards whether it helped.** There is one holdout
flag for one hypothesis (assistant vs. no assistant). There is no general
experiment framework, and no offline replay.

**Scenario C — new supply arrives with no history.** Handled well:
`bayesian_rating` and `smoothed_rate` pull unproven inventory to the mean rather
than starving it. This is a real strength.

But the system is **pure exploitation**. It never deliberately explores. Every
impression is chosen by the current model, and every click it learns from was
conditioned on that choice. Positional and presentation bias will accumulate
into a self-confirming ranking that looks stable and is quietly wrong. At POC
volume this is invisible. At production volume it compounds.

**Scenario D — it rains.** §1 of the system design names weather as a defining
tourist constraint. Weather is not an input to ranking anywhere. On a wet
morning in Da Nang the storefront will keep recommending outdoor cycling with
full confidence. The `indoor_outdoor` facet exists — the *signal* to drive it
does not.

**Assessment.** Recommendation is *mechanically* adaptable — the feature
functions are clean, shared between engines, and well factored, so new signals
are cheap to add. It is *operationally* inflexible: every adaptation is a code
change, applied globally, with no measurement of the result. **The control loop
is open.**

### 2.2 Advising (the assistant)

The agent architecture is the right one, and deliberately so: tools carry the
capability, the model supplies the reasoning, and `_build_answer` filters
selections against ids that actually came back from a tool, so an answer is
always traceable to bookable inventory. That design absorbs new capability
gracefully — a new tool is a new thing the assistant can do, with no routing
logic to rewrite.

Three things stop it being production-grade advice:

**No regression safety net.** `AGENT_SYSTEM_PROMPT` is a Python constant, and
its behaviour is verified by exactly one thing: someone typing a question and
reading the answer. The two defects that were fixed most recently — recommending
mountains for "not mountain", and answering a pho question with boat tours —
were both found by a human noticing. A prompt edit, a model version bump, or an
Azure deployment swap can silently reintroduce either. **Nothing would catch
it.** For a system whose entire thesis is that its advice beats manual search,
having no measurement of advice quality is the single largest risk on this list.

**Policy is not editable by the people who own it.** Tone, refusal rules,
disclosure requirements, what to do when nothing matches — these are CX and
commercial decisions living in source code.

**Degradation is silent.** When the model is unavailable the deterministic
fallback answers, and it answers badly (it is honest about this in the design
doc). Nothing alerts on how often that path is taken, so a partial Azure outage
looks like a quality problem, not an availability problem.

**Latency is a conversion cost nobody owns.** Measured against the live
deployment, an assistant turn takes **17-21 seconds** end to end — the price of
a multi-step agent loop over a reasoning model. The storefront streams, so
perceived latency is lower than that, but a shopper on hotel wifi deciding
between four tabs will not wait twice. There is no latency budget, no
per-stage timing in telemetry, and no fast path for the common case where a
single `search_experiences` call would have answered. This is the clearest
example of a quality improvement (§7.1 of the system design) that quietly
bought itself a cost nobody is tracking.

### 2.3 Operations

This is the weakest area and the one the business will feel first.

- **No catalog management module.** No product editor, no publish workflow, no
  bulk action, no search across the operator's own inventory.
- **The import review loop is open at both ends.** `build_trippass_catalog`
  computes `needs_review` when classification fails — and `upsert_catalog`
  *counts it and throws it away*. It is never stored. Meanwhile imported
  products are written straight to `ACTIVE`. So a product the system knows it
  misclassified goes live, unreviewed, unfindable.
- **Operator edits cannot survive.** `_apply()` overwrites every field on every
  import. Even with an editor, a corrected category would be reverted on the
  next deploy. Any catalog tool is worthless without field-level override
  tracking — this is the load-bearing insight for §3.
- **Access control is decorative.** `POST /admin/imports` trusts an
  `X-Admin-Role: catalog_manager` **request header**. Anyone with curl is a
  catalog manager. `/analytics/funnel` — revenue, conversion and cost — is
  entirely unauthenticated.
- **There is no audit trail.** §14 of the system design states that mutations
  are "audited". They are not; no audit table exists. That claim is corrected in
  this revision.
- **`POST /admin/imports` does not import.** It validates the shape of an
  uploaded file, returns a count, and discards the data.
- **No supplier management.** No commission terms, no contacts, no SLA, no
  ability to suspend a supplier.

### 2.4 What is genuinely production-grade already

Worth stating plainly, because the gaps above are not an indictment of the
build: the eligibility gate, hybrid retrieval, MMR diversification, honest
scarcity, idempotent commerce, the shared embedding cache, the daily cost
breaker, and the tool-grounding convention are all things many production
systems lack. The engine is not the problem.

---

## 3. What ships in this revision

Scoped to what a first production use genuinely cannot open without, ordered by
dependency. Everything here is implemented in this change.

### 3.1 Operator identity, authorisation and audit

Real operator accounts (`operators`) with hashed API keys, four roles, and
per-role endpoint authorisation replacing the header check. Every mutation
writes an `audit_log` row recording actor, action, entity, and a before/after
diff. Audit is written in the same transaction as the change it describes, so it
cannot drift from reality.

Roles: `admin`, `catalog_manager` (edit and publish inventory),
`merchandiser` (promotion controls and configuration), `analyst` (read-only).

### 3.2 Runtime business configuration

A `business_settings` table holding typed, validated, versioned settings, read
through a short-TTL cache. Ranking weights, take rates, and assistant policy
move from environment variables to data. Environment values remain the defaults
and the fallback, so nothing breaks if the table is empty.

This converts "redeploy to retune" into "change a value and observe", which is
the precondition for every other feedback loop in this document.

### 3.3 Field-level override tracking

`experience_overrides` records which fields a human has edited. `_apply()`
consults it and skips those fields on re-import. A corrected category stays
corrected while price and availability continue to sync from the supplier —
which is the whole point of importing.

### 3.4 The import review queue

`needs_review` is persisted. Products the classifier was unsure of land in
`PENDING_REVIEW` rather than `ACTIVE`, so unreviewed supply is not sold. The
storefront's eligibility gate already filters on status, so nothing else has to
change. Operators can approve, correct-and-approve, or reject.

### 3.5 Merchandising controls

Per-experience `boost` (a bounded multiplier), `pinned` and `suppressed`, each
with an optional active window, honoured by both the search and recommendation
engines through the shared feature layer. This gives the business a campaign
lever that is not a code change — and bounds it, so merchandising cannot
completely override relevance.

### 3.6 The quality evaluation harness

A golden set of queries with expected properties, a runner, and a CLI that
reports constraint violations, precision@k and assistant grounding failures.
Wired into CI.

This is what makes "genuinely intelligent" a claim that can be checked rather
than asserted, and it is what stops the next prompt edit from silently
reintroducing a fixed defect.

### 3.7 The operator console

A `/admin` surface in the existing SPA covering the review queue, catalog
search and editing, merchandising, configuration and the funnel. Deliberately
plain: it is a tool for staff, not a storefront.

### 3.8 Delivered state

All eight items above are implemented and verified, not stubbed:

| Item | Evidence |
|---|---|
| Identity, authz, audit | `tests/test_admin_catalog_postgres.py` (13), against real PostgreSQL |
| Runtime configuration | validation, versioning and cache invalidation covered; a bad value is proven never to store |
| Override tracking | re-import proven not to overwrite an operator edit |
| Review queue | `PENDING_REVIEW` proven to be excluded from storefront eligibility |
| Merchandising | `tests/test_search.py` (22) proves boosts, ceilings, promotion windows and suppression change ranking — otherwise the controls are decorative |
| Evaluation harness | search 15/15 in CI; assistant 13/13 against the live model; `tests/test_evals.py` (17) tests the grader itself |
| Operator console | `frontend/e2e/console.spec.ts` drives a real browser against a real stack |
| Deployment | `adminBootstrapKey` threaded through Bicep, `deploy.sh` and CI |

Two defects found by writing those tests, rather than by reading the code:
the console's TypeScript types disagreed with five API payloads (audit rows and
settings rendered blank), and `StaticFiles(html=True)` returned 404 for `/admin`,
so the console would have been unreachable in production while working locally.

---

## 4. What does **not** ship, and why

These are named so nobody mistakes silence for completeness.

**Hard blockers for real inventory and real money.** Do not open to the public
without these:

1. **Authoritative supplier availability.** The Trippass stock endpoint is
   unavailable in staging, so imported availability is *synthesised on a fixed
   daily schedule*. Selling against invented capacity means overselling, and
   overselling a tourist's only free afternoon is unrecoverable. Requires either
   a working supplier stock API or a manual capacity workflow with conservative
   limits.
2. **Real payments and refunds**, with the reconciliation and chargeback paths
   that implies. Payment is simulated end to end today.
3. **Live FX.** Eight currencies are quoted from a static table
   (`common/currency.py`). Quoting real money from a stale rate is a direct
   loss. Needs a rate feed and a staleness policy that refuses to quote rather
   than quoting wrongly.
4. **Private networking for PostgreSQL.** `AllowAzureServices` is an
   acknowledged POC trade-off.
5. **A consent and retention model** for any shopper data, before accounts or
   email capture are built. Already blocking two deferred Phase 6 items.

**Deferred by judgement, not oversight:**

6. **A general experimentation framework** (assignment, exposure logging,
   sequential tests). §3.2 is the precondition; the framework is a substantial
   piece of work and premature before there is traffic to split.
7. **Exploration in ranking.** Real, and it compounds — but adding
   randomisation before there is measurement (§3.6) would produce noise nobody
   can read.
8. **Weather-conditioned ranking.** High value for tourists and comparatively
   cheap once configuration is data (§3.2). Next highest-value ranking work.
9. **Shopper accounts and order history.** Blocked on (5).
10. **Supplier self-service portal.** The operator console (§3.7) is the
    prerequisite; suppliers editing their own inventory is a later, larger
    surface with its own authorisation model.
11. **Assistant latency work.** 17-21s per turn (§2.2) needs a latency budget,
    per-stage timing in telemetry, and probably a single-tool fast path. It is
    deferred here only because §3.6 must land first: cutting agent steps to go
    faster is exactly the kind of change that silently trades away answer
    quality, and there is currently no way to see that happen.

---

## 5. Recommended sequence after this revision

1. Close the availability gap (blocker 1) — nothing else matters if the product
   cannot be honestly sold.
2. Use the harness (§3.6) to establish a quality baseline, then keep it in CI.
3. Payments and FX (blockers 2, 3).
4. Weather-conditioned ranking (deferred 8) — best ranking value per unit of
   effort once configuration is data.
5. Experimentation framework, then exploration (deferred 6, 7), in that order.
   Exploration without measurement is just noise.

---

## 6. How to judge this work

The test is not "does it have an admin page". It is:

> Can a non-engineer at the business correct a wrong product, run a two-week
> campaign, retune a ranking weight, and see whether any of it worked —
> without an engineer, a deploy, or a database client?

After this revision the first three are yes. The fourth is *measurable* (§3.6)
but not yet *attributable to a change* — that needs the experimentation
framework, and it is the honest boundary of what ships here.
