# Deployment and operations guide

This guide deploys Vietra as a low-cost Azure proof of concept. All application
and Foundry AI Services resources are isolated in one resource group.

## Prerequisites

- Azure CLI with the Container Apps extension and Bicep support.
- Docker is not required locally; Azure Container Registry builds the image.
- An Azure subscription where you can create resources.
- Azure AI Services availability and quota for the model versions in
  `infra/bicep/ai-integration.bicep`.
- Python `uv`, Node.js 22+, and Corepack for local validation.

Authenticate and select the target subscription:

```bash
az login
az account set --subscription "<subscription-id>"
```

## Models

The deployment creates an AI Services account and manages these low-cost
Foundry deployments:

| Purpose | Deployment |
| --- | --- |
| Assistant planning and grounded prose | `gpt-5.4-mini` |
| Structured search intent | `gpt-5-nano` |
| 512-dimensional catalog/query embeddings | `text-embedding-3-small` |
| Optional catalog artwork generation | `gpt-image-1-mini` |

The model resources are serialized in Bicep because Azure rejects concurrent
deployment updates against the same parent AI account.

## Deploy

Set the required values:

```bash
export AZURE_SUBSCRIPTION_ID="<application-subscription-id>"
export POSTGRES_ADMIN_PASSWORD="<strong-random-bootstrap-password>"
export VIETRA_PREFIX="vietrapoc"
```

Override these defaults when the regions or generated AI account name differ:

```bash
export AZURE_LOCATION="eastus2"
export AZURE_POSTGRES_LOCATION="centralus"
export AZURE_AI_ACCOUNT_NAME="<globally-unique-ai-account-name>"
```

Run:

```bash
./scripts/deploy.sh
```

The script:

1. Provisions PostgreSQL 17, pgvector extensions, ACR, monitoring, Container
   Apps, and the catalog seed job.
2. Applies the Foundry model deployments.
3. Builds the combined React/FastAPI image in ACR.
4. Re-applies Bicep with the built image and a unique revision marker.
5. Runs Alembic and seeds 360 products and their embeddings.
6. Prints the public Container App hostname.

The same script runs from `.github/workflows/deploy.yml` after validated code
changes reach `main`. GitHub authenticates with an environment-scoped OIDC
federated identity; no Azure client secret is stored in the repository.

The validated default PostgreSQL region is Central US. Set
`AZURE_POSTGRES_LOCATION` to the nearest supported region for your subscription.

## Validate

```bash
APP_URL="https://<container-app-hostname>"

curl --fail "$APP_URL/health/live"
curl --fail "$APP_URL/health/ready"

az containerapp job execution list \
  --resource-group "rg-${VIETRA_PREFIX}-poc" \
  --name "job-${VIETRA_PREFIX}-catalog" \
  --output table
```

Run the real browser journeys against the deployment:

```bash
cd frontend
corepack pnpm install
corepack pnpm exec playwright install chromium
PLAYWRIGHT_BASE_URL="$APP_URL" corepack pnpm test:e2e
```

The Playwright suite covers desktop voice search, assistant voice input,
assistant speech playback, rich recommendations, real backend cart/checkout,
QR voucher generation, mobile navigation, drawer sizing, and cart removal.

## Generated catalog

- `backend/app/catalog/seed.py` deterministically builds 360 products across
  18 Vietnam destinations. Set `DEMO_CATALOG_SIZE` to change the requested
  catalog size.
- `frontend/public/assets/catalog/` contains 12 committed WebP images reused
  across the catalog.
- `scripts/generate_catalog_images.py` regenerates those assets with
  `gpt-image-1-mini`, low quality, and JPEG-to-WebP conversion:

```bash
export AZURE_OPENAI_ENDPOINT="https://<account>.openai.azure.com"
export AZURE_OPENAI_IMAGE_DEPLOYMENT="gpt-image-1-mini"
cd backend
uv run python ../scripts/generate_catalog_images.py --force
```

The script uses `AZURE_OPENAI_ACCESS_TOKEN` when set, otherwise it obtains a
Cognitive Services token from the active Azure CLI login.

## Cost controls

- Container Apps scales to zero and is capped at one replica.
- PostgreSQL uses burstable `Standard_B1ms`, 32 GiB storage, and seven-day
  backups without high availability.
- ACR uses Basic.
- Embeddings use 512 dimensions and are generated in batches.
- Intent extraction uses `gpt-5-nano`; richer prose uses `gpt-5.4-mini`.
- The image generator uses `gpt-image-1-mini` at low quality and only creates
  the small reusable image set.

The deployment remains capped at one replica for predictable POC cost.
Application state is PostgreSQL-backed outside explicit demo mode.

## Security boundaries

- The PostgreSQL `AllowAzureServices` firewall rule is a POC compromise.
- Database, ACR, and Foundry credentials are held as Container Apps secrets for
  this POC.
- No real payment details are collected.
- Use private endpoints, network isolation, persistent commerce state, and
  stricter origin controls before production use.

## Teardown

Delete the application resource group and all billable POC infrastructure:

```bash
export AZURE_SUBSCRIPTION_ID="<application-subscription-id>"
export VIETRA_PREFIX="vietrapoc"
./scripts/destroy.sh
```

The destroy script removes the application resource group, including the
dedicated Foundry account and model deployments.
