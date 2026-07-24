#!/usr/bin/env bash
set -euo pipefail

subscription="${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID}"
prefix="${VIETRA_PREFIX:-vietrapoc}"
location="${AZURE_LOCATION:-eastus2}"
postgres_location="${AZURE_POSTGRES_LOCATION:-centralus}"
ai_account_name="${AZURE_AI_ACCOUNT_NAME:-}"
deployment="vietra-${prefix}"
resource_group="rg-${prefix}-poc"
registry_name="$(printf '%s' "$prefix" | tr -d '-' | tr '[:upper:]' '[:lower:]')vietra"
bootstrap_image="mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"

az account set --subscription "$subscription"

if [[ -z "${POSTGRES_ADMIN_PASSWORD:-}" ]]; then
  echo "POSTGRES_ADMIN_PASSWORD must be set." >&2
  exit 1
fi

if az acr show \
  --resource-group "$resource_group" \
  --name "$registry_name" \
  --output none 2>/dev/null; then
  bootstrap_image="${registry_name}.azurecr.io/vietra:latest"
fi

bootstrap_revision="bootstrap-$(date -u +%Y%m%d%H%M%S)"
deployment_parameters=(
  prefix="$prefix"
  location="$location"
  postgresLocation="$postgres_location"
  postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD"
)
if [[ -n "$ai_account_name" ]]; then
  deployment_parameters+=(aiAccountName="$ai_account_name")
fi

az deployment sub create \
  --name "$deployment" \
  --location "$location" \
  --template-file infra/bicep/main.bicep \
  --parameters \
    "${deployment_parameters[@]}" \
    containerImage="$bootstrap_image" \
    buildRevision="$bootstrap_revision" \
  --output none

registry="$(az deployment sub show \
  --name "$deployment" \
  --query properties.outputs.registryName.value \
  --output tsv)"
resource_group="$(az deployment sub show \
  --name "$deployment" \
  --query properties.outputs.resourceGroupName.value \
  --output tsv)"
app_name="$(az deployment sub show \
  --name "$deployment" \
  --query properties.outputs.containerAppName.value \
  --output tsv)"

az acr build \
  --resource-group "$resource_group" \
  --registry "$registry" \
  --image "vietra:latest" \
  .

login_server="$(az acr show \
  --resource-group "$resource_group" \
  --name "$registry" \
  --query loginServer \
  --output tsv)"

build_revision="build-$(date -u +%Y%m%d%H%M%S)"

az deployment sub create \
  --name "$deployment" \
  --location "$location" \
  --template-file infra/bicep/main.bicep \
  --parameters \
    "${deployment_parameters[@]}" \
    buildRevision="$build_revision" \
    containerImage="${login_server}/vietra:latest" \
  --output none

az containerapp job start \
  --resource-group "$resource_group" \
  --name "job-${prefix}-catalog" \
  --output none

job_status=""
for _ in {1..120}; do
  job_status="$(az containerapp job execution list \
    --resource-group "$resource_group" \
    --name "job-${prefix}-catalog" \
    --query "sort_by(@, &properties.startTime)[-1].properties.status" \
    --output tsv)"
  case "$job_status" in
    Succeeded)
      break
      ;;
    Failed)
      echo "Catalog job failed." >&2
      exit 1
      ;;
  esac
  sleep 10
done

if [[ "$job_status" != "Succeeded" ]]; then
  echo "Catalog job did not complete within 20 minutes." >&2
  exit 1
fi

hostname="$(az containerapp show \
  --resource-group "$resource_group" \
  --name "$app_name" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)"
app_url="https://${hostname}"

for _ in {1..60}; do
  if curl --fail --silent "${app_url}/health/ready" >/dev/null; then
    break
  fi
  sleep 10
done

curl --fail --silent "${app_url}/health/ready" >/dev/null
curl --fail --silent "${app_url}/api/v1/experiences?limit=1" >/dev/null

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "app_url=${app_url}" >> "$GITHUB_OUTPUT"
fi
echo "$app_url"
