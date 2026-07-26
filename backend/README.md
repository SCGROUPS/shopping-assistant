# Tourism Shopping Assistant Backend

FastAPI POC backend with deterministic local search/assistant behavior and a production-shaped
PostgreSQL, pgvector, Alembic, and Azure OpenAI integration.

## Design

See [../docs/SYSTEM_DESIGN.md](../docs/SYSTEM_DESIGN.md) for the layered
architecture. In short: `search/` owns the eligibility gate, hybrid retrieval,
and RRF fusion; `recommendations/` is a **separate** query-less engine with its
own scoring function; `assistant/` is an orchestration layer that consumes both
as tools and does not itself rank products. Shared ranking primitives live in
`common/ranking.py`. The design document also records where the implemented
scoring diverges from `POC_SPEC.md`.

## Run locally

```bash
cd backend
uv sync --dev
DEMO_MODE=true uv run uvicorn app.main:app --reload
```

OpenAPI is at `http://localhost:8000/docs`. Demo startup seeds 360 synthetic Vietnam experiences.
Images are referenced as `/assets/catalog/<slug>.webp`.

## Test and quality

```bash
uv run pytest
uv run ruff check app tests
uv run pyright
```

Tests use the deterministic in-memory catalog and require neither PostgreSQL nor Azure.

Two suites are gated behind environment variables, because the database code
paths are separate implementations from demo mode and cannot be exercised
without a server:

| Variable | Enables | Needs |
|---|---|---|
| `POSTGRES_TEST_DATABASE_URL` | `test_analytics_postgres.py`, `test_admin_catalog_postgres.py`, `test_trippass_upsert_postgres.py`, `test_availability_refresh.py` | a bare PostgreSQL server |
| `POSTGRES_SEEDED_TEST_DATABASE_URL` | `test_postgres_integration.py` — full application journey | a seeded catalogue via `DATABASE_URL`, plus an LLM provider |

CI runs the first against a throwaway `pgvector/pgvector:pg17` service. Run it
locally the same way:

```bash
createdb vietra_test
POSTGRES_TEST_DATABASE_URL="postgresql+psycopg://$(whoami)@127.0.0.1:5432/vietra_test" uv run pytest
```

### Answer quality

Unit tests cannot tell you the answers got worse. The golden suites can:

```bash
uv run python -m app.evals.cli                      # search; no model needed
uv run python -m app.evals.cli --suite assistant    # needs Azure OpenAI + a DB
uv run python -m app.evals.cli --update-baseline    # accept the current results
```

Cases live in `evals/*.json` and are graded against product attributes rather
than by an LLM judge, so a failure names the offending product and check. The
runner exits non-zero on any case that passed in `evals/baseline.json` and fails
now, which is the signal an aggregate pass rate hides. CI runs the search suite.

## PostgreSQL

Set an async SQLAlchemy URL and run the initial migration:

```bash
export DATABASE_URL='postgresql+psycopg://user:password@host:5432/tourism?sslmode=require'
uv run alembic upgrade head
```

The migration enables `vector`, `pg_trgm`, and `unaccent`, creates the complete transactional
schema, FTS/trigram indexes, and a 512-dimension pgvector HNSW index. `app/search/postgres.py`
contains the filter-before-limit FTS/vector RRF query. Demo mode intentionally uses the matching
in-memory implementation so the app remains available when PostgreSQL is absent.

## Azure OpenAI

```bash
DEMO_MODE=false
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-5.4-mini
AZURE_OPENAI_INTENT_DEPLOYMENT=gpt-5.4-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_OPENAI_IMAGE_DEPLOYMENT=gpt-image-1-mini
OPENAI_EMBEDDING_DIMENSIONS=512
```

Use `AZURE_OPENAI_API_KEY` locally, or omit it to use `DefaultAzureCredential` (managed identity,
Azure CLI, or developer credential chain). No secrets belong in source control. Search falls back
to deterministic parsing/embeddings if a model call fails. The backend only exposes the image
deployment setting; catalog image assets are generated and managed outside this service.

## Catalog CLI

```bash
uv run python -m app.catalog.cli seed                 # in-memory demo catalog
uv run python -m app.catalog.cli summary
uv run python -m app.catalog.cli seed-db [--force]    # PostgreSQL demo catalog
uv run python -m app.catalog.cli refresh-availability
uv run python -m app.catalog.cli import-trippass [--days 30]

# Operator console credentials (see docs/SYSTEM_DESIGN.md §4.2)
uv run python -m app.catalog.cli create-operator --email ops@example.com \
    --role catalog_manager [--name "Ops"] [--rotate]
uv run python -m app.catalog.cli list-operators
```

`import-trippass` pulls live inventory from the Trippass (HeriStep) supplier API
and upserts it on `Experience.external_id`, so it is safe to re-run: prices and
variants resync while experience ids - and therefore carts, bookings and
behaviour events - stay put. The supplier feed carries no facets, so each
listing is classified once at import time by the model (see
`app/catalog/trippass.py`); a listing that could not be classified is flagged
`needs_review` and held at `PENDING_REVIEW`, which keeps it out of the
storefront until an operator decides (`/admin`). Fields an operator edits are
recorded as overrides and are not overwritten by later imports. Requires
`DATABASE_URL`.

Operator endpoints live under `/api/v1/admin` and take an `X-API-Key` header.
`/api/v1/analytics/funnel` needs the same credential. Set `ADMIN_BOOTSTRAP_KEY`
to create the first operator; in demo mode `demo-admin-key` stands in.

All public endpoints use `/api/v1`. Send `X-Session-ID` to isolate anonymous state and
`Idempotency-Key` for cart mutations and checkout. Assistant messages stream SSE by default;
send `Accept: text/event-stream` or append `?stream=true` for SSE. Normal
`application/json` requests receive the complete structured assistant payload.

Frontend discovery contracts:

- `GET /experiences` → `{items, total}`
- `POST /search` → `{query_id, intent, effective_filters, items, recommendations, facets}`
- `GET /recommendations` → `{items}`
- `POST /conversations` → `{id}`
- `GET|POST|DELETE /cart...` → `{id, currency, items, subtotal, total}`

In demo mode, CORS accepts HTTP/HTTPS origins on `localhost` and `127.0.0.1` on any port.
Assistant `products` are complete commerce cards with image, destination, rating, price, badges,
live option/slot availability, grounding reason, and per-product `CHECK_AVAILABILITY` and
`ADD_TO_CART` actions. Azure may enhance prose or select a typed tool, but the server always builds
and validates this structured payload. Checkout exposes `PREPARE_CHECKOUT`, followed by a
confirmation-required `CONFIRM_SIMULATED_CHECKOUT` action.

### Live model tests

Every other suite stubs the provider, so the model's own behaviour is never
under test. Both defects that reached production hid in that gap: the extractor
invented categories the catalogue does not stock, and expressed the destination
as a constraint nothing mapped. Neither is reachable with a deterministic stub.

```bash
export AZURE_OPENAI_ENDPOINT="https://<account>.openai.azure.com/"
export AZURE_OPENAI_API_KEY=$(az cognitiveservices account keys list \
  -g rg-vietra9c2f-poc -n <account> --query key1 -o tsv)
export LIVE_DATABASE_URL=$(az containerapp secret show \
  -g rg-vietra9c2f-poc -n ca-vietra9c2f-web --secret-name database-url \
  --query value -o tsv | sed 's#^postgresql://#postgresql+psycopg://#')
LIVE_LLM_TESTS=1 .venv/bin/python -m pytest tests/test_live_intent.py -q
```

Reaching the live database needs a firewall rule for your address:

```bash
az postgres flexible-server firewall-rule create \
  -g rg-vietra9c2f-poc -n vietra9c2f-centralus-pg \
  --rule-name dev-$USER --start-ip-address $(curl -s https://api.ipify.org) \
  --end-ip-address $(curl -s https://api.ipify.org)
```

The model is non-deterministic, so each query runs five times and the invariant
has to hold on every one. A single green pass proves nothing about a failure
that shows up half the time.

### Comparing intent models

`scripts/compare_intent_models.py` runs two Azure OpenAI deployments end-to-end
against the live catalogue and scores them on stated-correct answers rather than
on whether the models agree with each other. Agreement proves nothing: both were
wrong about the same things before the destination enum landed.

```bash
export DEMO_MODE=false                     # before import: the engine is built at module load
export DATABASE_URL="postgresql+psycopg://..."
export AZURE_OPENAI_ENDPOINT="https://<account>.openai.azure.com/"
export AZURE_OPENAI_API_KEY="$(az cognitiveservices account keys list -g <rg> -n <account> --query key1 -o tsv)"
.venv/bin/python scripts/compare_intent_models.py
```

It aborts if fewer than 100 products load. An empty catalogue scores 100% on
every axis because nothing it reports can be contradicted, and the first run of
this harness did exactly that and looked like a pass.
