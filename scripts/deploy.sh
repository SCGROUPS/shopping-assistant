#!/usr/bin/env bash
set -euo pipefail

subscription="${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID}"
prefix="${VIETRA_PREFIX:-vietrapoc}"
location="${AZURE_LOCATION:-eastus2}"
postgres_location="${AZURE_POSTGRES_LOCATION:-centralus}"
ai_subscription="${AZURE_AI_SUBSCRIPTION_ID:-$subscription}"
ai_resource_group="${AZURE_AI_RESOURCE_GROUP:-ml}"
ai_account_name="${AZURE_AI_ACCOUNT_NAME:-ai-eastus2508770413322}"
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

az deployment sub create \
  --name "$deployment" \
  --location "$location" \
  --template-file infra/bicep/main.bicep \
  --parameters \
    prefix="$prefix" \
    location="$location" \
    postgresLocation="$postgres_location" \
    containerImage="$bootstrap_image" \
    buildRevision="$bootstrap_revision" \
    postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
    aiSubscriptionId="$ai_subscription" \
    aiResourceGroupName="$ai_resource_group" \
    aiAccountName="$ai_account_name"

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
    prefix="$prefix" \
    location="$location" \
    postgresLocation="$postgres_location" \
    postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
    buildRevision="$build_revision" \
    aiSubscriptionId="$ai_subscription" \
    aiResourceGroupName="$ai_resource_group" \
    aiAccountName="$ai_account_name" \
    containerImage="${login_server}/vietra:latest"

az containerapp job start \
  --resource-group "$resource_group" \
  --name "job-${prefix}-catalog"

az containerapp show \
  --resource-group "$resource_group" \
  --name "$app_name" \
  --query properties.configuration.ingress.fqdn \
  --output tsv
