#!/usr/bin/env bash
# Publish the Function App code and apply App Settings from deploy.config.json.
# Requires: az CLI (logged in) + jq. Azure Functions Core Tools (func) is
# OPTIONAL -- if it is not on PATH (common on locked-down CI agents; func
# exits 127 "command not found"), this falls back to
# `az functionapp deployment source config-zip` with a server-side Oryx
# build, which needs only the Azure CLI. Mirrors deploy_function.ps1.
#
# Usage:
#   scripts/deploy_function.sh [deploy.config.json]

set -euo pipefail

CONFIG="${1:-deploy.config.json}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Config file not found: $CONFIG" >&2
  exit 1
fi

FUNC_APP=$(jq -r '.functionApp.name' "$CONFIG")
RG=$(jq -r '.functionApp.resourceGroup' "$CONFIG")
[[ -z "$FUNC_APP" || -z "$RG" ]] && { echo "functionApp.name and functionApp.resourceGroup must be set in $CONFIG" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Publishing function code to ${FUNC_APP}"
FUNC_DIR="${REPO_ROOT}/function_app"

# Prefer `func` (it waits for the remote build). If it is NOT installed --
# e.g. Azure Functions Core Tools is missing/blocked on the CI agent, which
# surfaces as exit 127 "command not found" -- fall back to
# `az functionapp deployment source config-zip`, which needs only the Azure CLI.
if command -v func >/dev/null 2>&1; then
  FUNC_AVAILABLE=1
else
  FUNC_AVAILABLE=0
  echo "==> 'func' not found -- deploying with 'az' (config-zip + server-side build) instead."
fi

# Server-side (Oryx) build MUST be on so 'pip install -r requirements.txt' runs
# on the server. Without it, config-zip uploads files with NO dependencies and
# 0 functions load. Set BEFORE the zip deploy. Harmless for the func path.
az functionapp config appsettings set -g "${RG}" -n "${FUNC_APP}" \
  --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true --output none
az functionapp config appsettings delete -g "${RG}" -n "${FUNC_APP}" \
  --setting-names WEBSITE_RUN_FROM_PACKAGE --output none 2>/dev/null || true

# Capture output so we can scan for transient-failure markers that neither
# tool always reflects in its exit code (ServiceUnavailable, 504, etc.).
# Retry up to 3x -- publish/Kudu failures are frequently transient.
PUBLISHED=0
for attempt in 1 2 3; do
  echo "==> publish attempt ${attempt}/3"
  PUBLISH_LOG="$(mktemp)"
  set +e
  if [[ $FUNC_AVAILABLE -eq 1 ]]; then
    pushd "${FUNC_DIR}" >/dev/null
    func azure functionapp publish "${FUNC_APP}" --python 2>&1 | tee "${PUBLISH_LOG}"
    PUBLISH_RC=${PIPESTATUS[0]}
    popd >/dev/null
  else
    # Zip the CONTENTS of function_app (host.json / requirements.txt at the zip
    # ROOT -- Azure requires that). Use python3 (guaranteed by the pipeline) so
    # we don't depend on a `zip` binary being present; skip __pycache__.
    ZIP="$(mktemp -u).zip"
    python3 - "${FUNC_DIR}" "${ZIP}" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src))
PY
    az functionapp deployment source config-zip \
      -g "${RG}" -n "${FUNC_APP}" --src "${ZIP}" 2>&1 | tee "${PUBLISH_LOG}"
    PUBLISH_RC=${PIPESTATUS[0]}
    rm -f "${ZIP}"
  fi
  set -e

  if [[ $PUBLISH_RC -eq 0 ]] && \
     ! grep -qE 'Remote build failed|Deployment failed|Error Uploading archive|ServiceUnavailable|GatewayTimeout|Connection Timed Out|Application Error' "${PUBLISH_LOG}"; then
    PUBLISHED=1
    rm -f "${PUBLISH_LOG}"
    break
  fi
  rm -f "${PUBLISH_LOG}"

  if [[ $attempt -lt 3 ]]; then
    echo "==> attempt ${attempt} failed (transient?); restarting app + waiting 45s, then retry..."
    az functionapp restart -g "${RG}" -n "${FUNC_APP}" --output none || true
    sleep 45
  fi
done

if [[ $PUBLISHED -ne 1 ]]; then
  echo "" >&2
  echo "==> ABORT: function publish FAILED after 3 attempts" >&2
  echo "==> Oryx build/deployment log (the real reason):" >&2
  az webapp log deployment show -g "${RG}" -n "${FUNC_APP}" || echo "(could not fetch deployment log)" >&2
  echo "" >&2
  echo "NOT applying App Settings -- new code isn't live." >&2
  exit 1
fi

# config-zip returns after UPLOAD; the server build runs async. Give it time to
# install dependencies + load the functions before later steps call them.
if [[ $FUNC_AVAILABLE -eq 0 ]]; then
  echo "==> uploaded via az; waiting ~2.5 min for the server to install dependencies + load functions..."
  sleep 150
fi

echo "==> Applying App Settings"

# Extract values from the config file. Empty values are skipped so the
# script doesn't clobber existing settings with empty strings.
AOAI_ENDPOINT=$(jq -r '.azureOpenAI.endpoint // ""' "$CONFIG")
AOAI_API_VERSION=$(jq -r '.azureOpenAI.apiVersion // "2024-12-01-preview"' "$CONFIG")
AOAI_CHAT=$(jq -r '.azureOpenAI.chatDeployment // ""' "$CONFIG")
AOAI_VISION=$(jq -r '.azureOpenAI.visionDeployment // ""' "$CONFIG")
DI_ENDPOINT=$(jq -r '.documentIntelligence.endpoint // ""' "$CONFIG")
DI_API_VERSION=$(jq -r '.documentIntelligence.apiVersion // "2024-11-30"' "$CONFIG")
SEARCH_ENDPOINT=$(jq -r '.search.endpoint // ""' "$CONFIG")
SEARCH_PREFIX=$(jq -r '.search.artifactPrefix // "mm-manuals"' "$CONFIG")
APPI_CONN=$(jq -r '.appInsights.connectionString // ""' "$CONFIG")
SKILL_VERSION=$(jq -r '.skillVersion // "1.0.0"' "$CONFIG")

# Storage and indexer-name settings used by the auto_heal timer trigger.
STORAGE_ACCT_RESOURCE_ID=$(jq -r '.storage.accountResourceId // ""' "$CONFIG")
STORAGE_ACCT_NAME="${STORAGE_ACCT_RESOURCE_ID##*/}"
STORAGE_CONTAINER=$(jq -r '.storage.pdfContainerName // ""' "$CONFIG")

SETTINGS=(
  "AUTH_MODE=mi"
  "AOAI_ENDPOINT=${AOAI_ENDPOINT}"
  "AOAI_API_VERSION=${AOAI_API_VERSION}"
  "AOAI_CHAT_DEPLOYMENT=${AOAI_CHAT}"
  "AOAI_VISION_DEPLOYMENT=${AOAI_VISION}"
  "DI_ENDPOINT=${DI_ENDPOINT}"
  "DI_API_VERSION=${DI_API_VERSION}"
  "SEARCH_ENDPOINT=${SEARCH_ENDPOINT}"
  "SEARCH_INDEX_NAME=${SEARCH_PREFIX}-index"
  "SEARCH_INDEXER_NAME=${SEARCH_PREFIX}-indexer"
  "STORAGE_ACCOUNT_NAME=${STORAGE_ACCT_NAME}"
  "STORAGE_CONTAINER_NAME=${STORAGE_CONTAINER}"
  "AUTO_HEAL_ENABLED=false"
  "AUTO_HEAL_STUCK_AFTER_MIN=60"
  "AUTO_HEAL_MAX_BLOBS_PER_RUN=20"
  "SKILL_VERSION=${SKILL_VERSION}"
  # Python worker concurrency. Azure Functions Python defaults to 1 process
  # × 1 thread = no parallelism, which causes every skill call to serialize
  # through one worker -- guaranteeing 230s timeouts under any indexer
  # parallelism. 4 processes × 16 threads = 64 concurrent capacity, which
  # comfortably handles dop=6 across 5 per-record skills (30 max concurrent).
  "FUNCTIONS_WORKER_PROCESS_COUNT=4"
  "PYTHON_THREADPOOL_THREAD_COUNT=16"
)

# Only set App Insights if a connection string is provided.
if [[ -n "$APPI_CONN" ]]; then
  SETTINGS+=("APPLICATIONINSIGHTS_CONNECTION_STRING=${APPI_CONN}")
fi

az functionapp config appsettings set \
  -g "${RG}" -n "${FUNC_APP}" \
  --settings "${SETTINGS[@]}" \
  --output none

echo "==> Function App ${FUNC_APP} ready"
