#!/usr/bin/env bash
# Publish the Function App code and apply App Settings from deploy.config.json.
# Requires: az CLI (logged in), Azure Functions Core Tools v4, jq.
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
pushd "${REPO_ROOT}/function_app" >/dev/null
func azure functionapp publish "${FUNC_APP}" --python
popd >/dev/null

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
  "SKILL_VERSION=${SKILL_VERSION}"
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
