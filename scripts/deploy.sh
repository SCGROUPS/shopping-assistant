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

# Deploy phases announce themselves with elapsed time. Without this the script
# ran for nineteen minutes between the image push and the final URL without
# printing a single line, across four distinct phases - so a deploy that hung
# gave an operator no way to tell a slow migration from a stuck job, and no
# way to attribute the duration afterwards either. `phase` closes the previous
# one before opening the next, so the numbers add up to the whole.
_phase_name=""
_phase_started=0
phase() {
  local now
  now="$(date -u +%s)"
  if [[ -n "$_phase_name" ]]; then
    printf '==> %s took %dm%02ds\n' "$_phase_name" \
      $(( (now - _phase_started) / 60 )) $(( (now - _phase_started) % 60 ))
  fi
  _phase_name="$1"
  _phase_started="$now"
  [[ -n "$_phase_name" ]] && printf -- '--> %s\n' "$_phase_name"
  return 0
}

az account set --subscription "$subscription"

if [[ -z "${POSTGRES_ADMIN_PASSWORD:-}" ]]; then
  echo "POSTGRES_ADMIN_PASSWORD must be set." >&2
  exit 1
fi

registry_exists=false
if az acr show \
  --resource-group "$resource_group" \
  --name "$registry_name" \
  --output none 2>/dev/null; then
  bootstrap_image="${registry_name}.azurecr.io/vietra:latest"
  registry_exists=true
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
if [[ -n "${ADMIN_BOOTSTRAP_KEY:-}" ]]; then
  deployment_parameters+=(adminBootstrapKey="$ADMIN_BOOTSTRAP_KEY")
else
  # Not fatal: the storefront is unaffected. But say so plainly, because the
  # failure mode is otherwise a console that returns 401 to a correct key and
  # gives no hint why.
  echo "ADMIN_BOOTSTRAP_KEY is not set: the operator console will be unreachable." >&2
fi

# This script runs the same ARM deployment twice — once to bootstrap the
# registry, once with the real image. Azure Database for PostgreSQL rejects a
# configuration write while the server is still settling from a previous
# operation ("ServerIsBusy"), so the second deployment can fail purely because
# the first one just succeeded. The condition is transient and clears on its
# own, so retry it rather than failing a deployment that is actually fine.
deploy_stack() {
  local image="$1"
  local revision="$2"
  local attempt=1
  local max_attempts=4
  local delay=90
  local log

  log="$(mktemp)"
  # No model deployment names are passed here, deliberately. The defaults in
  # infra/bicep/main.bicep are therefore what production runs, and that file is
  # the single place they are decided. Passing one from here would mean the name
  # in the template no longer describes the running service - which is how a
  # hand-set AZURE_OPENAI_INTENT_DEPLOYMENT once survived until the next deploy
  # silently reverted it and reopened an outage that looked fixed.
  # backend/tests/test_intent_request_shape.py reads those defaults directly and
  # will not see anything added here.
  while true; do
    if az deployment sub create \
      --name "$deployment" \
      --location "$location" \
      --template-file infra/bicep/main.bicep \
      --parameters \
        "${deployment_parameters[@]}" \
        containerImage="$image" \
        buildRevision="$revision" \
      --output none 2>"$log"; then
      rm -f "$log"
      return 0
    fi

    # Only retry the known-transient condition. Anything else is a real
    # failure and must surface immediately rather than after four waits.
    if (( attempt >= max_attempts )) || ! grep -qiE 'ServerIsBusy|busy processing another operation' "$log"; then
      cat "$log" >&2
      rm -f "$log"
      return 1
    fi

    echo "Azure reported a busy server; retrying in ${delay}s (attempt ${attempt}/${max_attempts})." >&2
    sleep "$delay"
    attempt=$(( attempt + 1 ))
    delay=$(( delay * 2 ))
  done
}

# The stack deployment runs twice: once before the image is built, once after.
# On a first deploy the first run is what creates the registry. On every deploy
# after that it exists only to apply infrastructure changes *before* the
# migration runs — and when there are none it is three minutes of a deployment
# spent asking Azure to confirm that nothing changed.
#
# So skip it, but only on proof that nothing changed. The fingerprint covers the
# templates and every parameter value, and is recorded on the resource group
# only after a deployment has succeeded end to end. Anything else — a template
# edit, a rotated password, a missing or unreadable tag, a half-finished
# previous run — misses and the deployment happens. The second run is
# unconditional either way, so infrastructure is still fully reconciled on every
# deploy; skipping only moves that reconciliation after the migration, which is
# safe precisely because the fingerprint proves there is nothing to reconcile.
if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum | cut -d' ' -f1; }
else
  sha256() { shasum -a 256 | cut -d' ' -f1; }
fi
stack_fingerprint="$(
  {
    cat infra/bicep/*.bicep
    printf '%s\n' "${deployment_parameters[@]}"
  } | sha256
)"

read_stack_outputs() {
  registry="$(az deployment sub show --name "$deployment" \
    --query properties.outputs.registryName.value --output tsv 2>/dev/null || true)"
  resource_group="$(az deployment sub show --name "$deployment" \
    --query properties.outputs.resourceGroupName.value --output tsv 2>/dev/null || true)"
  app_name="$(az deployment sub show --name "$deployment" \
    --query properties.outputs.containerAppName.value --output tsv 2>/dev/null || true)"
  [[ -n "$registry" && -n "$resource_group" && -n "$app_name" ]]
}

applied_fingerprint=""
if [[ "$registry_exists" == true ]]; then
  applied_fingerprint="$(az group show --name "$resource_group" \
    --query "tags.stackFingerprint" --output tsv 2>/dev/null || true)"
fi

if [[ "$registry_exists" == true && -n "$applied_fingerprint" &&
      "$applied_fingerprint" == "$stack_fingerprint" ]] && read_stack_outputs; then
  echo "Infrastructure is unchanged since the last successful deployment; building directly." >&2
else
  deploy_stack "$bootstrap_image" "$bootstrap_revision"
  read_stack_outputs || {
    echo "The stack deployment produced no outputs." >&2
    exit 1
  }
fi
phase "Build and push image"
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

# Runs a Container Apps job to completion, failing the deploy if it does not
# succeed. Both jobs need this and neither may be fire-and-forget: an unnoticed
# migration failure would be discovered by the first request that needed the
# new column.
run_job() {
  local job_name="$1"
  local description="$2"
  # Polls of ten seconds. The default suits a migration; the catalogue job is
  # given its own bound because it does the work, and a bound shorter than the
  # job's own timeout fails the deployment for a job that is still succeeding.
  local attempts="${3:-120}"
  local execution=""
  local status=""

  # Capture the execution this call started and poll *that* one. Selecting the
  # most recently started execution instead looks equivalent and is not: for a
  # few seconds after `start` returns, Azure's list can still show only the
  # previous run. That run has already succeeded, so the script would read
  # "Succeeded", promote the application, and take traffic on the new image
  # before the migration it was supposed to wait for had even begun.
  execution="$(az containerapp job start \
    --resource-group "$resource_group" \
    --name "$job_name" \
    --query name \
    --output tsv)"

  if [[ -z "$execution" ]]; then
    echo "${description} could not be started." >&2
    return 1
  fi

  # The execution name is printed before the wait, not after it. A deploy that
  # is still going is exactly when someone needs the identifier to go and read
  # the job's own logs, and printing it only on failure means it is available
  # in every case except the one where it is wanted.
  echo "${description} running as ${execution}"

  local started elapsed attempt
  started="$(date -u +%s)"
  for attempt in $(seq 1 "$attempts"); do
    status="$(az containerapp job execution show \
      --resource-group "$resource_group" \
      --name "$job_name" \
      --job-execution-name "$execution" \
      --query properties.status \
      --output tsv 2>/dev/null || true)"
    case "$status" in
      Succeeded)
        return 0
        ;;
      Failed|Degraded)
        echo "${description} failed (execution ${execution})." >&2
        return 1
        ;;
    esac
    # A heartbeat every two minutes. Silence for fourteen minutes is
    # indistinguishable from a hang, and the difference decides whether
    # somebody cancels a deploy that was about to succeed.
    if (( attempt % 12 == 0 )); then
      elapsed=$(( $(date -u +%s) - started ))
      printf '    %s still running after %dm%02ds (status: %s)\n' \
        "$description" $(( elapsed / 60 )) $(( elapsed % 60 )) "${status:-unknown}"
    fi
    sleep 10
  done

  echo "${description} did not complete within $((attempts / 6)) minutes (execution ${execution})." >&2
  return 1
}

# Migrate before the app is promoted, not after. The container app and the jobs
# share one `containerImage` parameter, so deploying the stack would move the
# app to the new image too; updating the job's image directly is what lets the
# schema go first. Migrations must therefore be expand-only - the old code is
# still serving traffic while this runs, and must keep working afterwards.
phase "Schema migration"
az containerapp job update \
  --resource-group "$resource_group" \
  --name "job-${prefix}-migrate" \
  --image "${login_server}/vietra:latest" \
  --output none

run_job "job-${prefix}-migrate" "Schema migration"

phase "Promote application (stack deployment)"
deploy_stack "${login_server}/vietra:latest" "$build_revision"

# The catalogue job imports, reconciles and then drains the index queue, and
# `run_reindex` deliberately waits out a lease before giving up. Its bound has
# to outlast the job's own 3600s timeout rather than inherit the migration's,
# so that a job which fails on time reports as failed rather than as a script
# that gave up on something still running.
phase "Catalogue import, reconcile and index"
run_job "job-${prefix}-catalog" "Catalog job" 380

phase "Wait for the application to answer"
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

# Only now, with the stack deployed, migrated, seeded and answering. A
# fingerprint recorded any earlier would let the next deployment skip the
# infrastructure step on the strength of a run that never finished.
phase ""
az group update \
  --name "$resource_group" \
  --set "tags.stackFingerprint=${stack_fingerprint}" \
  --output none

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "app_url=${app_url}" >> "$GITHUB_OUTPUT"
fi
echo "$app_url"
