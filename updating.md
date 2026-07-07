# Overnight one-command run (paste into your git-bash TERMINAL, not Copilot)

Goal: preanalyze + index the 12 perf-test PDFs from the NEW storage account into a
NEW fresh index, unattended. The single command is `deploy.py --skip-bootstrap`,
which runs: preanalyze -> deploy_search -> reset+run indexer -> heal loop (up to 8
iterations) -> coverage. No prompts; safe to start and go to sleep.

Do the 3 PREREQ steps once, then run THE COMMAND at the bottom.

---

## PREREQ 0 — one-time env + login (fresh login so the token survives ~overnight)
```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
az cloud set --name AzureUSGovernment
az login   # do a FRESH login so the refresh token is valid through the night
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815
```

## PREREQ 1 — point deploy.config.json at the new storage + a new index prefix
Set these to YOUR new storage account and a fresh prefix (e.g. perfv01):
```bash
NEW_ACCT=<new storage account name>
NEW_RG=<its resource group>
NEW_CONT=<container name that has the 12 PDFs>
NEW_PREFIX=perfv01                      # any fresh name -> makes a brand-new clean index
NEW_ID=$(az storage account show -n "$NEW_ACCT" -g "$NEW_RG" --query id -o tsv)
python - <<PY
import json
c=json.load(open("deploy.config.json"))
c["storage"]["accountResourceId"]="$NEW_ID"
c["storage"]["pdfContainerName"]="$NEW_CONT"
c["search"]["artifactPrefix"]="$NEW_PREFIX"
json.dump(c, open("deploy.config.json","w"), indent=2)
print("config ->", c["storage"]["accountResourceId"], "|", c["storage"]["pdfContainerName"], "|", c["search"]["artifactPrefix"])
PY
```

## PREREQ 2 — grant access on the new storage account + enable soft delete
(ARM REST avoids the MissingSubscription CLI bug. Or do it in the portal: the new
storage account -> Access control (IAM) -> add "Storage Blob Data Reader" to both
the function app and the search service managed identities, and "Storage Blob Data
Contributor" to yourself.)
```bash
# soft delete (the datasource's deletion policy needs it)
EN=$(az storage account blob-service-properties show --account-name "$NEW_ACCT" --query "deleteRetentionPolicy.enabled" -o tsv 2>/dev/null)
[ "$EN" != "true" ] && az storage account blob-service-properties update --account-name "$NEW_ACCT" --enable-delete-retention true --delete-retention-days 7 -o none && echo "soft delete enabled"

# RBAC via ARM REST. Reader=2a2b9908-6ea1-4ae2-8e65-a410df84e7d1  Contributor=ba92f5b4-2d11-453d-a403-e96b0029c9fe
SUB=5c58d830-b35f-458a-ab5d-65ad9d0b9815
FUNC_MI=33182d42-e64e-4449-9e64-fd03f683222e
SEARCH_MI=4d05c1b2-a915-44a2-b29c-dc614b2601ac
USER_OID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null)
grant() {  # $1=principalId $2=roleGuid $3=type
  AID=$(python -c "import uuid;print(uuid.uuid4())")
  BODY=$(printf '{"properties":{"roleDefinitionId":"/subscriptions/%s/providers/Microsoft.Authorization/roleDefinitions/%s","principalId":"%s","principalType":"%s"}}' "$SUB" "$2" "$1" "$3")
  az rest --method put --url "https://management.usgovcloudapi.net${NEW_ID}/providers/Microsoft.Authorization/roleAssignments/${AID}?api-version=2022-04-01" --body "$BODY" 2>&1 | tail -1
}
grant "$FUNC_MI"   2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal
grant "$SEARCH_MI" 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal
[ -n "$USER_OID" ] && grant "$USER_OID" ba92f5b4-2d11-453d-a403-e96b0029c9fe User
```

## PREREQ 3 — (recommended) bump the ada-002 TPM if it's low
Check current capacity; if ada-002 is small (e.g. under ~50K TPM), raise it in the
portal (Foundry -> Deployments -> textembeddingadapsegtmandev01 -> Edit -> capacity),
or you'll get 429s during the big embedding run.
```bash
az cognitiveservices account deployment list -n psegtmfdryuatv01 -g psegtmrguatv01 --query "[].{name:name, model:properties.model.name, capacity_k_tpm:sku.capacity}" -o table
```

---

## THE COMMAND — start this, then go to sleep
```bash
mkdir -p reports
python scripts/deploy.py --config deploy.config.json \
  --skip-bootstrap \
  --preanalyze-vision-parallel 20 \
  --heal-max-iterations 8 \
  --heal-wait-minutes 60 \
  2>&1 | tee reports/overnight_run.log
```
- `--skip-bootstrap`: don't redeploy the function app / re-run RBAC (already done).
- `--preanalyze-vision-parallel 20`: gentle on the GPT-5.1 TPM (raise/lower to taste).
- `--heal-wait-minutes 60`: give big docs time before the heal loop re-bumps them.
- `tee` writes everything to `reports/overnight_run.log` to read in the morning.

## In the morning — check it finished clean
```bash
tail -40 reports/overnight_run.log          # look for "DEPLOY COMPLETE" or the coverage table
python scripts/check_index.py --config deploy.config.json --coverage
```
Exit 0 + "every PDF is indexed" = done. If some docs are stuck, the log's
heal_until_done section names them; usually re-running the same command clears
transient ones. If a big doc is stuck with 5xx, that's a Function App memory tier
issue — tell me and we'll bump the plan.
