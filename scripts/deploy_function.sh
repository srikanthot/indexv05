#!/usr/bin/env bash
# Publish the Function App code and apply App Settings from deploy.config.json.
# Requires: az CLI (logged in) + jq + curl. Azure Functions Core Tools (func)
# is OPTIONAL -- if it is not on PATH (common on locked-down CI agents; func
# exits 127 "command not found"), this falls back to a Kudu /api/zipdeploy with
# a SERVER-SIDE (Oryx) build -- the same thing `func ... --build remote` does
# under the hood, so requirements.txt is pip-installed on the server. We do NOT
# use `az functionapp deployment source config-zip`: it force-sets
# SCM_DO_BUILD_DURING_DEPLOYMENT=false and skips the build, which ships the code
# WITHOUT its dependencies (functions then fail to import at runtime).
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
FUNC_DIR="${REPO_ROOT}/function_app"

echo "==> Publishing function code to ${FUNC_APP}"

if command -v func >/dev/null 2>&1; then
  FUNC_AVAILABLE=1
else
  FUNC_AVAILABLE=0
  echo "==> 'func' not found -- deploying via Kudu zip deploy with a server-side (Oryx) build."
fi

# Server-side (Oryx) build MUST be on so 'pip install -r requirements.txt' runs
# on the server. Set BEFORE the deploy. WEBSITE_RUN_FROM_PACKAGE would bypass
# the build, so remove it. Kudu /api/zipdeploy honors these settings (unlike
# `az ... config-zip`, which force-disables the build).
az functionapp config appsettings set -g "${RG}" -n "${FUNC_APP}" \
  --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true --output none
az functionapp config appsettings delete -g "${RG}" -n "${FUNC_APP}" \
  --setting-names WEBSITE_RUN_FROM_PACKAGE --output none 2>/dev/null || true

# kudu_zipdeploy: POST the zip to Kudu with a server-side build, then poll the
# deployment to completion. Sets OK=1 on success. Needs SCM host + an AAD token
# (the SP's Website Contributor role authenticates to Kudu). Uses only python3
# (stdlib urllib) + az -- no dependency on curl or jq being on the agent.
kudu_zipdeploy() {
  OK=0
  local zip scm_host token rc
  zip="$(mktemp -u).zip"
  # Zip the CONTENTS of function_app (host.json / requirements.txt at the zip
  # ROOT -- Oryx needs requirements.txt at the root). python3 is guaranteed by
  # the pipeline, so we don't depend on a `zip` binary; skip __pycache__.
  python3 - "${FUNC_DIR}" "${zip}" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src))
PY
  scm_host="$(az functionapp show -g "${RG}" -n "${FUNC_APP}" \
    --query "hostNameSslStates[?hostType=='Repository'].name | [0]" -o tsv)"
  token="$(az account get-access-token --query accessToken -o tsv)"
  if [[ -z "$scm_host" || -z "$token" ]]; then
    echo "  could not resolve SCM host or access token" >&2
    rm -f "${zip}"
    return
  fi

  # POST the zip and poll the build to completion. Token is passed via env
  # (not argv) so it doesn't show up in the process list.
  KUDU_TOKEN="$token" python3 - "$scm_host" "$zip" <<'PY'
import json, os, sys, time, urllib.request, urllib.error
scm, zip_path = sys.argv[1], sys.argv[2]
token = os.environ["KUDU_TOKEN"]

def call(url, method="GET", body=None, ctype=None):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if ctype:
        req.add_header("Content-Type", ctype)
    return urllib.request.urlopen(req, timeout=600)

with open(zip_path, "rb") as fh:
    data = fh.read()

deploy_url = "https://%s/api/zipdeploy?isAsync=true" % scm
try:
    resp = call(deploy_url, "POST", data, "application/zip")
    print("  zipdeploy accepted (HTTP %s); building on server + polling ~12 min..." % resp.getcode())
except urllib.error.HTTPError as e:
    detail = e.read()[:600].decode("utf-8", "replace")
    print("  zipdeploy REJECTED: HTTP %s %s" % (e.code, detail))
    if e.code in (401, 403):
        print("  -> Kudu did not accept the SP's AAD token. Tell the maintainer:")
        print("     SCM AAD auth may be disabled, or the SP needs 'Website Contributor'.")
    sys.exit(2)
except Exception as e:
    print("  zipdeploy failed to send: %s" % e)
    sys.exit(2)

status_url = "https://%s/api/deployments/latest" % scm
for _ in range(48):  # 48 * 15s = ~12 min
    time.sleep(15)
    try:
        info = json.load(call(status_url))
    except Exception:
        continue
    if info.get("complete"):
        st = info.get("status")           # Kudu DeployStatus: 4=Success, 3=Failed
        if st == 4:
            print("  server build complete (status=Success).")
            sys.exit(0)
        print("  server build FINISHED but status=%s (not Success)." % st)
        print("  log_url=%s" % info.get("log_url"))
        sys.exit(3)
print("  timed out waiting ~12 min for the server build to complete.")
sys.exit(4)
PY
  rc=$?
  rm -f "${zip}"
  if [[ $rc -eq 0 ]]; then
    OK=1
  fi
}

PUBLISHED=0
for attempt in 1 2 3; do
  echo "==> publish attempt ${attempt}/3"
  set +e
  if [[ $FUNC_AVAILABLE -eq 1 ]]; then
    PUBLISH_LOG="$(mktemp)"
    pushd "${FUNC_DIR}" >/dev/null
    func azure functionapp publish "${FUNC_APP}" --python 2>&1 | tee "${PUBLISH_LOG}"
    PUBLISH_RC=${PIPESTATUS[0]}
    popd >/dev/null
    OK=0
    if [[ $PUBLISH_RC -eq 0 ]] && \
       ! grep -qE 'Remote build failed|Deployment failed|Error Uploading archive|ServiceUnavailable|GatewayTimeout' "${PUBLISH_LOG}"; then
      OK=1
    fi
    rm -f "${PUBLISH_LOG}"
  else
    kudu_zipdeploy
  fi
  set -e

  if [[ $OK -eq 1 ]]; then
    PUBLISHED=1
    break
  fi

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

# Give the workers a moment to load the freshly built package before later
# steps (indexer skills) call them.
echo "==> published; waiting 60s for workers to load the new package..."
sleep 60

echo "==> Applying App Settings"

# Extract values from the config file. Empty values are skipped so the
# script doesn't clobber existing settings with empty strings.
# Provider-aware, mirroring deploy_function.ps1 + bootstrap.py. In foundry mode the
# function uses FOUNDRY_* for chat/vision; MODEL_PROVIDER selects the path. Missing
# these on a fresh function app leaves it misconfigured for Foundry.
MODEL_PROVIDER=$(jq -r '.modelProvider // "aoai"' "$CONFIG")
AOAI_ENDPOINT=$(jq -r '.azureOpenAI.endpoint // ""' "$CONFIG")
AOAI_API_VERSION=$(jq -r '.azureOpenAI.apiVersion // "2024-12-01-preview"' "$CONFIG")
AOAI_CHAT=$(jq -r '.azureOpenAI.chatDeployment // ""' "$CONFIG")
AOAI_VISION=$(jq -r '.azureOpenAI.visionDeployment // ""' "$CONFIG")
AOAI_EMBED=$(jq -r '.azureOpenAI.embedDeployment // ""' "$CONFIG")
FOUNDRY_ENDPOINT=$(jq -r '.foundry.projectEndpoint // ""' "$CONFIG")
FOUNDRY_API_VERSION=$(jq -r '.foundry.apiVersion // "2024-10-21"' "$CONFIG")
FOUNDRY_CHAT=$(jq -r '.foundry.chatModel // ""' "$CONFIG")
FOUNDRY_EMBED=$(jq -r '.foundry.embedModel // ""' "$CONFIG")
DI_ENDPOINT=$(jq -r '.documentIntelligence.endpoint // ""' "$CONFIG")
DI_API_VERSION=$(jq -r '.documentIntelligence.apiVersion // "2024-11-30"' "$CONFIG")
SEARCH_ENDPOINT=$(jq -r '.search.endpoint // ""' "$CONFIG")
SEARCH_PREFIX=$(jq -r '.search.artifactPrefix // "mm-manuals"' "$CONFIG")
APPI_CONN=$(jq -r '.appInsights.connectionString // ""' "$CONFIG")
SKILL_VERSION=$(jq -r '.skillVersion // "1.1.0"' "$CONFIG")

# Storage and indexer-name settings used by the auto_heal timer trigger.
STORAGE_ACCT_RESOURCE_ID=$(jq -r '.storage.accountResourceId // ""' "$CONFIG")
STORAGE_ACCT_NAME="${STORAGE_ACCT_RESOURCE_ID##*/}"
STORAGE_CONTAINER=$(jq -r '.storage.pdfContainerName // ""' "$CONFIG")

SETTINGS=(
  "AUTH_MODE=mi"
  "MODEL_PROVIDER=${MODEL_PROVIDER}"
  "AOAI_ENDPOINT=${AOAI_ENDPOINT}"
  "AOAI_API_VERSION=${AOAI_API_VERSION}"
  "AOAI_CHAT_DEPLOYMENT=${AOAI_CHAT}"
  "AOAI_VISION_DEPLOYMENT=${AOAI_VISION}"
  "AOAI_EMBED_DEPLOYMENT=${AOAI_EMBED}"
  "FOUNDRY_PROJECT_ENDPOINT=${FOUNDRY_ENDPOINT}"
  "FOUNDRY_API_VERSION=${FOUNDRY_API_VERSION}"
  "FOUNDRY_CHAT_MODEL=${FOUNDRY_CHAT}"
  "FOUNDRY_EMBED_MODEL=${FOUNDRY_EMBED}"
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
  # Keep the server-side build enabled for future deploys.
  "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
  "ENABLE_ORYX_BUILD=true"
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
