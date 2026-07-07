# Overnight run — ONE block. Fill the 2 values, paste into your git-bash terminal, sleep.

# ===================== FILL THESE 2 (3rd is optional) =====================
NEW_ACCT=<new storage account name that has the 12 PDFs>
NEW_CONT=<the container name>
NEW_PREFIX=perfv01          # any fresh name -> makes a brand-new clean index
# =========================================================================

# --- env (you already have the cert; harmless to re-set) ---
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
SUB=5c58d830-b35f-458a-ab5d-65ad9d0b9815
az cloud set --name AzureUSGovernment >/dev/null
az account set --subscription $SUB

# --- resolve the account + point the config at it ---
NEW_RG=$(az storage account list --query "[?name=='$NEW_ACCT'].resourceGroup | [0]" -o tsv)
NEW_ID=$(az storage account show -n "$NEW_ACCT" -g "$NEW_RG" --query id -o tsv)
echo "RESOLVED  RG=$NEW_RG  ID=$NEW_ID"
python - <<PY
import json
c=json.load(open("deploy.config.json"))
c["storage"]["accountResourceId"]="$NEW_ID"
c["storage"]["pdfContainerName"]="$NEW_CONT"
c["search"]["artifactPrefix"]="$NEW_PREFIX"
json.dump(c, open("deploy.config.json","w"), indent=2)
print("CONFIG UPDATED ->", c["storage"]["accountResourceId"], "|", c["storage"]["pdfContainerName"], "|", c["search"]["artifactPrefix"])
PY

# --- enable blob soft delete (datasource deletion policy needs it) ---
EN=$(az storage account blob-service-properties show --account-name "$NEW_ACCT" --query "deleteRetentionPolicy.enabled" -o tsv 2>/dev/null)
[ "$EN" != "true" ] && az storage account blob-service-properties update --account-name "$NEW_ACCT" --enable-delete-retention true --delete-retention-days 7 -o none && echo "SOFT DELETE: enabled"

# --- grant the 2 managed identities read access on the new storage (ARM REST; avoids the CLI glitch) ---
FUNC_MI=33182d42-e64e-4449-9e64-fd03f683222e
SEARCH_MI=4d05c1b2-a915-44a2-b29c-dc614b2601ac
grant(){ AID=$(python -c "import uuid;print(uuid.uuid4())"); BODY=$(printf '{"properties":{"roleDefinitionId":"/subscriptions/%s/providers/Microsoft.Authorization/roleDefinitions/%s","principalId":"%s","principalType":"%s"}}' "$SUB" "$2" "$1" "$3"); az rest --method put --url "https://management.usgovcloudapi.net${NEW_ID}/providers/Microsoft.Authorization/roleAssignments/${AID}?api-version=2022-04-01" --body "$BODY" 2>&1 | tail -1; }
echo "GRANT func MI:";   grant "$FUNC_MI"   2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal
echo "GRANT search MI:"; grant "$SEARCH_MI" 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal
echo "waiting 30s for RBAC to propagate..."; sleep 30

# --- THE FULL RUN (preanalyze -> deploy_search -> reset+run -> heal loop x8 -> coverage) ---
mkdir -p reports
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --preanalyze-vision-parallel 20 --heal-max-iterations 8 --heal-wait-minutes 60 2>&1 | tee reports/overnight_run.log
```

# In the morning:
#   tail -40 reports/overnight_run.log        # look for "DEPLOY COMPLETE" / coverage table
#   python scripts/check_index.py --config deploy.config.json --coverage
