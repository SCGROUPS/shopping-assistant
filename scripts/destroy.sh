#!/usr/bin/env bash
set -euo pipefail

subscription="${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID}"
prefix="${VIETRA_PREFIX:-vietrapoc}"
resource_group="${AZURE_RESOURCE_GROUP:-rg-${prefix}-poc}"

az account set --subscription "$subscription"
az group delete --name "$resource_group" --yes

echo "Deleted application resource group: $resource_group"
echo "Deleted the Vietra AI account and model deployments with the application resource group."
