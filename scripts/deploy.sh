#!/usr/bin/env bash
# One-shot deployment: infra -> function code -> search artifacts.
#
# Usage:
#   scripts/deploy.sh <env>           # e.g. scripts/deploy.sh dev
#   scripts/deploy.sh <env> --run-indexer
#
# Prereqs:
#   - az login (Contributor + User Access Administrator on the target sub)
#   - bicep CLI (installed via `az bicep install`)
#   - Python 3.11 with `azure-identity` and `httpx` for deploy_search.py
#   - `func` Azure Functions Core Tools v4

set -euo pipefail

ENV="${1:?usage: deploy.sh <env> [--run-indexer]}"
RUN_INDEXER=""
[[ "${2:-}" == "--run-indexer" ]] && RUN_INDEXER="--run-indexer"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA="${REPO_ROOT}/infra"
FUNC_DIR="${REPO_ROOT}/function_app"
LOCATION="${LOCATION:-eastus2}"
DEPLOYMENT_NAME="mm-manuals-${ENV}"

echo "==> Deploying infra (env=${ENV}, location=${LOCATION})"
az deployment sub create \
  --name "${DEPLOYMENT_NAME}" \
  --location "${LOCATION}" \
  --template-file "${INFRA}/main.bicep" \
  --parameters "${INFRA}/parameters/${ENV}.bicepparam" \
  --output none

RG=$(az deployment sub show -n "${DEPLOYMENT_NAME}" --query properties.outputs.resourceGroupName.value -o tsv)
FUNC_APP=$(az deployment sub show -n "${DEPLOYMENT_NAME}" --query properties.outputs.functionAppName.value -o tsv)

echo "==> Publishing function app ${FUNC_APP}"
pushd "${FUNC_DIR}" >/dev/null
func azure functionapp publish "${FUNC_APP}" --python
popd >/dev/null

echo "==> Applying Azure AI Search artifacts"
python "${REPO_ROOT}/scripts/deploy_search.py" --env "${ENV}" ${RUN_INDEXER}

echo "==> Done. Function App: ${FUNC_APP}  RG: ${RG}"
