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
| `POSTGRES_TEST_DATABASE_URL` | `test_analytics_postgres.py` — SQL aggregation in `common/analytics.py` | a bare PostgreSQL server |
| `POSTGRES_SEEDED_TEST_DATABASE_URL` | `test_postgres_integration.py` — full application journey | a seeded catalogue via `DATABASE_URL`, plus an LLM provider |

CI runs the first against a throwaway `pgvector/pgvector:pg17` service. Run it
locally the same way:

```bash
createdb vietra_test
POSTGRES_TEST_DATABASE_URL="postgresql+psycopg://$(whoami)@127.0.0.1:5432/vietra_test" uv run pytest
```

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
AZURE_OPENAI_INTENT_DEPLOYMENT=gpt-5-nano
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_OPENAI_IMAGE_DEPLOYMENT=gpt-image-1-mini
OPENAI_EMBEDDING_DIMENSIONS=512
```

Use `AZURE_OPENAI_API_KEY` locally, or omit it to use `DefaultAzureCredential` (managed identity,
Azure CLI, or developer credential chain). No secrets belong in source control. Search falls back
to deterministic parsing/embeddings if a model call fails. The backend only exposes the image
deployment setting; catalog image assets are generated and managed outside this service.

## Seed CLI

```bash
uv run python -m app.catalog.cli seed
uv run python -m app.catalog.cli summary
```

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
