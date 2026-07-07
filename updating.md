Hi Copilot — we're switching to a DIFFERENT storage account that already contains
the 12 performance-test PDFs. Re-point the pipeline to it: find that account,
update the config, grant the identities access, redeploy the datasource, and run
preanalyze on all 12. No app code edits (only deploy.config.json's storage block).
Run the steps and print the REPORT. SSL env as usual; US Gov;
sub 5c58d830-b35f-458a-ab5d-65ad9d0b9815.

---

### Steps

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
az cloud set --name AzureUSGovernment
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815

# 1. Find the storage account + container holding the 12 PDFs.
#    List every storage account, then its containers with PDF counts. Identify the
#    account+container that has the 12 performance-test PDFs.
for A in $(az storage account list --query "[].name" -o tsv); do
  RGN=$(az storage account show -n "$A" --query resourceGroup -o tsv)
  for C in $(az storage container list --account-name "$A" --auth-mode login --query "[].name" -o tsv 2>/dev/null); do
    N=$(az storage blob list --account-name "$A" --container-name "$C" --auth-mode login --num-results 5000 --query "length([?ends_with(name,'.pdf')])" -o tsv 2>/dev/null)
    [ "${N:-0}" -gt 0 ] && echo "ACCOUNT=$A RG=$RGN CONTAINER=$C PDFs=$N"
  done
done
```

Identify the account+container with the 12 PDFs from that list, then set these
(replace the placeholders) and continue:

```bash
NEW_ACCT=<the account name with 12 PDFs>
NEW_RG=<its resource group>
NEW_CONT=<the container name>
NEW_ID=$(az storage account show -n "$NEW_ACCT" -g "$NEW_RG" --query id -o tsv)
echo "NEW_ID=$NEW_ID"

# 2. Update deploy.config.json storage block: accountResourceId=$NEW_ID, pdfContainerName=$NEW_CONT
python - <<PY
import json
c=json.load(open("deploy.config.json"))
c.setdefault("storage",{})
c["storage"]["accountResourceId"]="$NEW_ID"
c["storage"]["pdfContainerName"]="$NEW_CONT"
json.dump(c, open("deploy.config.json","w"), indent=2)
print("updated storage ->", c["storage"]["accountResourceId"], c["storage"]["pdfContainerName"])
PY

# 3. Enable blob soft delete on the new account if not enabled (the datasource needs it)
EN=$(az storage account blob-service-properties show --account-name "$NEW_ACCT" --query "deleteRetentionPolicy.enabled" -o tsv 2>/dev/null)
echo "soft_delete_enabled=$EN"
if [ "$EN" != "true" ]; then
  az storage account blob-service-properties update --account-name "$NEW_ACCT" --enable-delete-retention true --delete-retention-days 7 -o none && echo "enabled soft delete"
fi

# 4. Grant identities access on the NEW storage account (Storage Blob Data Reader for the MIs,
#    Contributor for your signed-in user so preanalyze can write _dicache). Uses ARM REST to
#    dodge the MissingSubscription CLI glitch. Role GUIDs: Reader=2a2b9908-6ea1-4ae2-8e65-a410df84e7d1,
#    Contributor=ba92f5b4-2d11-453d-a403-e96b0029c9fe.
SUB=5c58d830-b35f-458a-ab5d-65ad9d0b9815
USER_OID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null)
FUNC_MI=33182d42-e64e-4449-9e64-fd03f683222e
SEARCH_MI=4d05c1b2-a915-44a2-b29c-dc614b2601ac
grant() { # $1=principalId $2=roleGuid $3=principalType
  AID=$(python -c "import uuid;print(uuid.uuid4())")
  BODY=$(printf '{"properties":{"roleDefinitionId":"/subscriptions/%s/providers/Microsoft.Authorization/roleDefinitions/%s","principalId":"%s","principalType":"%s"}}' "$SUB" "$2" "$1" "$3")
  az rest --method put --url "https://management.usgovcloudapi.net${NEW_ID}/providers/Microsoft.Authorization/roleAssignments/${AID}?api-version=2022-04-01" --body "$BODY" 2>&1 | tail -2 || echo "(exists ok)"
}
grant "$FUNC_MI"  2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal   # func MI reader
grant "$SEARCH_MI" 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 ServicePrincipal  # search MI reader
[ -n "$USER_OID" ] && grant "$USER_OID" ba92f5b4-2d11-453d-a403-e96b0029c9fe User   # you: contributor (for preanalyze writes)
echo "USER_OID=$USER_OID"
sleep 30  # RBAC propagation

# 5. Redeploy search artifacts (datasource now points at the new storage account)
python scripts/deploy_search.py --config deploy.config.json

# 6. Preanalyze ALL 12 docs in the new container (all phases, from scratch). Long step (1-3h) — let it finish.
python scripts/preanalyze.py --config deploy.config.json --phase all --concurrency 3 --vision-parallel 20

# 7. Status summary + the model TPM quotas (in case we need to bump ada-002)
python scripts/preanalyze.py --config deploy.config.json --status
az cognitiveservices account deployment list -n psegtmfdryuatv01 -g psegtmrguatv01 --query "[].{name:name, model:properties.model.name, capacity_k_tpm:sku.capacity}" -o table
```

### Print this REPORT block

```
NEW_STORAGE: account=<name> container=<name> PDFs=<n>   (list the 12 names + sizes)
CONFIG_UPDATED: <yes/no>
SOFT_DELETE: <already-on / enabled-now>
RBAC: func_reader=<ok/exists>  search_reader=<ok/exists>  user_contributor=<ok/exists/skipped>
DEPLOY_SEARCH: <ok / error>
PREANALYZE per-PDF (name: DI ok?, vision total/present/errored/missing, output.json?):
  <one line per PDF>
ANY_HTTP_400: <yes/no>   RATE_LIMIT_429_COUNT: <n>
TPM_QUOTAS: ada-002=<k TPM>  gpt-5.1=<k TPM>
OTHER_ERRORS: <any, or none>
```

If step 1 doesn't clearly show one container with 12 PDFs, paste me the list and
stop — I'll tell you which to use. If preanalyze is heavily 429-throttled, report
it and we'll bump ada-002 TPM. Otherwise let it finish and give me the REPORT.
