#!/usr/bin/env bash
# One-shot role assignments for a fresh environment.
#
# Run AFTER:
#   - Bicep / portal provisioning created the resources
#   - The Function App and Search service have system-assigned MI enabled
#   - You ran scripts/deploy_function.sh (so the Function App exists)
#
# Run BEFORE:
#   - scripts/deploy_search.py
#   - scripts/preanalyze.py
#
# Usage:
#   1. Fill in the seven names at the top of this file.
#   2. bash scripts/assign_roles.sh
#   3. Wait 10 minutes for RBAC propagation.
#
# Idempotent: re-running is safe. Roles already assigned are skipped.

set -euo pipefail

# ---------- FILL THESE IN ----------
RG="<your-rg>"
SEARCH="<search-service-name>"
STORAGE="<storage-account-name>"
AOAI="<aoai-resource-name>"

# DI and AISVC: if you have a *standalone* Document Intelligence resource
# (kind=FormRecognizer) AND a separate Azure AI multi-service account
# (kind=CognitiveServices), set them to those two distinct names.
#
# If you have only ONE multi-service Cognitive Services account that
# bundles DI together with everything else (common in GCC High and Gov
# Cloud), set DI and AISVC to the SAME name. The script will assign
# both roles to the same resource — Azure handles that cleanly.
DI="<di-or-multi-service-account-name>"
AISVC="<ai-services-multi-service-account-name>"

FUNC="<function-app-name>"
# -----------------------------------

# Sanity check: bail if the user forgot to edit the placeholders.
for v in RG SEARCH STORAGE AOAI DI AISVC FUNC; do
  val="${!v}"
  if [[ "$val" == \<*\> ]]; then
    echo "ERROR: $v is still the placeholder '$val'. Edit the top of this script." >&2
    exit 1
  fi
done

echo "Looking up principal IDs and resource IDs..."
ME=$(az ad signed-in-user show --query id -o tsv)
SEARCH_ID=$(az search service show -n "$SEARCH" -g "$RG" --query id -o tsv)
STORAGE_ID=$(az storage account show -n "$STORAGE" -g "$RG" --query id -o tsv)
AOAI_ID=$(az cognitiveservices account show -n "$AOAI" -g "$RG" --query id -o tsv)
DI_ID=$(az cognitiveservices account show -n "$DI" -g "$RG" --query id -o tsv)
AISVC_ID=$(az cognitiveservices account show -n "$AISVC" -g "$RG" --query id -o tsv)
SEARCH_MI=$(az search service show -n "$SEARCH" -g "$RG" --query "identity.principalId" -o tsv)
FUNC_MI=$(az functionapp identity show -n "$FUNC" -g "$RG" --query principalId -o tsv)

if [[ -z "$SEARCH_MI" || -z "$FUNC_MI" ]]; then
  echo "ERROR: Search or Function App is missing a system-assigned identity." >&2
  echo "Enable it first, then re-run this script:" >&2
  echo "  az functionapp identity assign -n $FUNC -g $RG" >&2
  echo "  az search service update      -n $SEARCH -g $RG --identity-type SystemAssigned" >&2
  exit 1
fi

grant() {
  local who="$1" role="$2" scope="$3"
  echo "  -> $role"
  # --only-show-errors keeps RoleAssignmentExists noise out of the log;
  # we still abort on any other error because of `set -e`.
  az role assignment create --assignee "$who" --role "$role" --scope "$scope" --only-show-errors >/dev/null
}

echo
echo "A. Granting your user (deploying principal) roles..."
grant "$ME"        "Search Service Contributor"      "$SEARCH_ID"
grant "$ME"        "Search Index Data Contributor"   "$SEARCH_ID"
grant "$ME"        "Storage Blob Data Contributor"   "$STORAGE_ID"
grant "$ME"        "Cognitive Services OpenAI User"  "$AOAI_ID"
grant "$ME"        "Cognitive Services User"         "$DI_ID"

echo
echo "B. Granting Search service MI roles..."
grant "$SEARCH_MI" "Storage Blob Data Reader"        "$STORAGE_ID"
grant "$SEARCH_MI" "Cognitive Services OpenAI User"  "$AOAI_ID"
grant "$SEARCH_MI" "Cognitive Services User"         "$AISVC_ID"

echo
echo "C. Granting Function App MI roles..."
grant "$FUNC_MI"   "Storage Blob Data Reader"        "$STORAGE_ID"
grant "$FUNC_MI"   "Cognitive Services OpenAI User"  "$AOAI_ID"
grant "$FUNC_MI"   "Cognitive Services User"         "$DI_ID"
grant "$FUNC_MI"   "Search Index Data Reader"        "$SEARCH_ID"

echo
echo "All 12 role assignments submitted."
echo "Wait 10 minutes for RBAC propagation before running deploy_search.py."
