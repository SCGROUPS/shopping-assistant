# Vietra

Vietra is an intelligent Vietnam tourism e-ticket marketplace proof of concept.
It combines traditional filters, PostgreSQL full-text and vector search,
explainable recommendations, voice interaction, and an Azure OpenAI shopping
assistant that can guide a user through a simulated purchase.

See [POC_SPEC.md](POC_SPEC.md) for the product and technical specification.
See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for reproducible Azure deployment,
validation, generated-data, cost, security, and teardown guidance.

## Stack

- Python 3.14, FastAPI, SQLAlchemy, and Pydantic.
- React 19, TypeScript, and Vite.
- PostgreSQL 17 with pgvector.
- Azure OpenAI deployments in East US 2:
  - `gpt-5.4-mini` for assistant orchestration.
  - `gpt-5-nano` for intent extraction.
  - `text-embedding-3-small` with 512 dimensions.
  - `gpt-image-1-mini` for a small reusable demo image set.
- Azure Container Apps, Container Apps Jobs, ACR, and Application
  Insights.
- Browser speech recognition for search and assistant prompts, plus
  speech-synthesis playback for assistant responses.

The seed contains 360 deterministic products across Hanoi, Ha Long, Ninh Binh,
Sapa, Hue, Da Nang, Hoi An, Quy Nhon, Nha Trang, Da Lat, Ho Chi Minh City,
the Mekong Delta, Can Tho, Phu Quoc, Mui Ne, and Con Dao.
The wider catalog also includes Vung Tau and Buon Ma Thuot.

## Local demo

The application has a deterministic demo mode, so the complete storefront,
assistant, cart, fake payment, and voucher flow can run without cloud
credentials or PostgreSQL.

```bash
cp .env.example .env
cd backend
uv sync
DEMO_MODE=true uv run uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
corepack pnpm install
VITE_API_BASE_URL=http://localhost:8000/api/v1 corepack pnpm dev
```

Open <http://localhost:5173>.

## Validation

```bash
cd backend
uv run pytest -q
uv run ruff check .
uv run pyright

cd ../frontend
corepack pnpm build
corepack pnpm lint
corepack pnpm exec playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:5173 corepack pnpm test:e2e
```

The browser suite has separate desktop and mobile Chromium journeys. Start the
backend and frontend as shown above before running it locally. Voice APIs are
mocked at the browser boundary while search, assistant, cart, checkout, and
voucher requests use the configured backend.

## Local PostgreSQL mode

With Docker available:

```bash
docker compose up --build
```

The React development server runs at <http://localhost:5173> and the packaged
application API at <http://localhost:8000>.

## Azure deployment

The Bicep deployment creates an East US 2 Foundry AI Services account with four
low-cost model deployments and the remaining POC infrastructure. It initially
deploys Microsoft's Container Apps sample image, builds Vietra in ACR, updates
the app, and starts the catalog job.

```bash
export AZURE_SUBSCRIPTION_ID="<target-subscription-id>"
export POSTGRES_ADMIN_PASSWORD="<strong-bootstrap-password>"
export VIETRA_PREFIX="vietrapoc"
./scripts/deploy.sh
```

The target identity needs Contributor permission to create resources.
The PostgreSQL firewall's `AllowAzureServices` rule is a low-cost POC choice;
replace it with private networking for production. The full environment and
model override list is documented in the deployment guide.

Application and AI resources use East US 2. PostgreSQL defaults to Central US
for the validated low-cost deployment and can be overridden when needed.

The POC app is deliberately capped at one active replica for low-cost
operation. Catalog, session, conversation, cart, booking, voucher, event, and
idempotency state are PostgreSQL-backed outside explicit demo mode.

## Branch and deployment workflow

- Develop and integrate changes on `dev`.
- Open a pull request from `dev` to `main`.
- Code changes pushed to `main` run backend/frontend validation, deploy to
  Azure with GitHub OIDC, wait for catalog seeding and readiness, then execute
  the desktop and mobile Playwright journeys against the live application.
- Documentation-only and demo-data-only changes do not trigger deployment.
