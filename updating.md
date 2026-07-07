Hi Copilot — great progress: the endpoint fix worked (`deploy_search` succeeded,
vectorizer error gone). Two things remain:

1. The Search identity's role grant failed with the same `MissingSubscription`
   CLI glitch you hit before on the Function identity — so grant it the reliable
   way via direct ARM REST (the way that returned `RoleAssignmentExists` last time).
2. The indexer was still `inProgress` with 0 processed, so the "zero records" /
   smoke_test fail was just because indexing hadn't finished. We need to grant the
   role, run a fresh indexer pass, and WAIT for it to actually complete before
   checking counts.

No code edits. Run the steps, be patient on the poll, and print the REPORT block.
SSL env as usual; US Gov. Subscription = 5c58d830-b35f-458a-ab5d-65ad9d0b9815.

---

### Run these

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815
SV=2024-05-01-preview
SUB=5c58d830-b35f-458a-ab5d-65ad9d0b9815

# 1. Grant SEARCH identity 'Cognitive Services OpenAI User' on the Foundry resource via ARM REST
#    (bypasses the MissingSubscription CLI wrapper bug, same as the function-identity grant).
SEARCH_PRINCIPAL=4d05c1b2-a915-44a2-b29c-dc614b2601ac
FOUNDRY_SCOPE=$(az resource show -g psegtmrguatv01 -n psegtmfdryuatv01 --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)
ROLE_DEF_ID=$(az role definition list --name "Cognitive Services OpenAI User" --query "[0].name" -o tsv)
ASSIGN_ID=$(python - <<'PY'
import uuid; print(uuid.uuid4())
PY
)
BODY=$(printf '{"properties":{"roleDefinitionId":"/subscriptions/%s/providers/Microsoft.Authorization/roleDefinitions/%s","principalId":"%s","principalType":"ServicePrincipal"}}' "$SUB" "$ROLE_DEF_ID" "$SEARCH_PRINCIPAL")
echo "FOUNDRY_SCOPE=$FOUNDRY_SCOPE"
az rest --method put --url "https://management.usgovcloudapi.net${FOUNDRY_SCOPE}/providers/Microsoft.Authorization/roleAssignments/${ASSIGN_ID}?api-version=2022-04-01" --body "$BODY" 2>&1 | tail -5 || echo "(if RoleAssignmentExists, that's fine)"

# small pause for RBAC propagation
sleep 30

# 2. Endpoint + names
SEARCH_EP=$(python -c "import json;print(json.load(open('deploy.config.json'))['search']['endpoint'].rstrip('/'))")
PFX=$(python -c "import json;print(json.load(open('deploy.config.json'))['search'].get('artifactPrefix') or 'mm-manuals')")
INDEXER="${PFX}-indexer"; INDEX="${PFX}-index"
echo "SEARCH_EP=$SEARCH_EP INDEXER=$INDEXER INDEX=$INDEX"

# 3. Reset + run a FRESH indexer pass (now that Search identity has the role)
az rest --method post --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/reset?api-version=$SV" -o none
az rest --method post --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/run?api-version=$SV" -o none
echo "indexer reset+run issued"

# 4. Poll patiently until it leaves inProgress (up to ~25 min). DO wait — the 535-figure doc + embeddings take time.
for i in $(seq 1 50); do
  S=$(az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "lastResult.status" -o tsv)
  P=$(az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "lastResult.itemsProcessed" -o tsv)
  echo "poll $i: status=$S processed=$P"
  if [ "$S" != "inProgress" ]; then break; fi
  sleep 30
done

# 5. Final status
az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "{status:lastResult.status, processed:lastResult.itemsProcessed, failed:lastResult.itemsFailed, errors:lastResult.errors[0:5].errorMessage, warnings:lastResult.warnings[0:5].message}" -o json
```

Only if step 5 shows status is NOT inProgress (i.e. success/failed), run the checks:

```bash
# 6. Per-doc record_type counts
for F in "GD-GA-CTP.pdf" "GD-AS-DWM.pdf" "ED-EO-OPM.pdf"; do
  echo "=== $F ==="
  for RT in text diagram table table_row summary; do
    N=$(az rest --method post --resource "https://search.azure.us" \
        --url "$SEARCH_EP/indexes/$INDEX/docs/search?api-version=$SV" \
        --headers "Content-Type=application/json" \
        --body "{\"search\":\"*\",\"filter\":\"source_file eq '$F' and record_type eq '$RT'\",\"count\":true,\"top\":0}" \
        --query "\"@odata.count\"" -o tsv)
    echo "  $RT: $N"
  done
done

# 7. Contract + vector checks
python scripts/smoke_test.py --config deploy.config.json --skip-run
python scripts/index_query_guide.py --config deploy.config.json --demo
```

### Print this REPORT block

```
SEARCH_ROLE_ON_FOUNDRY: <created / RoleAssignmentExists / error text>
INDEXER_FINAL_STATUS: <success/failed/STILL_inProgress>  processed=<n>  failed=<n>
INDEXER_ERRORS: <first few, or none>
PER_DOC COUNTS (only if finished):
  GD-GA-CTP.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  GD-AS-DWM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  ED-EO-OPM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
SMOKE_TEST: <pass / the issues>
VECTOR_DEMO: <yes/no>
```

If the indexer is still inProgress after the poll window, that's OK — just report
`STILL_inProgress` with the processed count, skip the checks, and I'll send a
quick status re-check next. If a command errors, stop and paste it.
