Hi Copilot — the diagnosis is clear. Our Foundry resource `psegtmfdryuatv01`
exposes an `openai.azure.us` endpoint (from its endpoint map:
`"OpenAI Language Model Instance API": "https://psegtmfdryuatv01.openai.azure.us/"`),
and both GPT-5.1 and ada-002 (`textembeddingadapsegtmandev01`) are deployed on it.
The index vectorizer failed only because the config used the `services.ai.azure.us`
project URL, which Azure Search rejects — it requires the `openai.azure.us` form.

Fix = point the embedding/vectorizer endpoint at the Foundry `openai.azure.us`
URL (keeps everything in Foundry), grant the Search identity access, redeploy,
then run the indexer on the 3 test docs and verify.

Make the config edit + RBAC, then run. If `deploy_search.py` still errors, STOP and
paste the error before doing the indexer steps. Set your usual SSL env; US Gov.

---

### EDIT — `deploy.config.json`
In the **`azureOpenAI`** block, change `endpoint` to the Foundry openai.azure.us URL.
Leave `foundry.projectEndpoint` and everything else unchanged.

Change:
```json
"endpoint": "https://psegtmfdryuatv01.services.ai.azure.us/"
```
to (this is inside the `azureOpenAI` block only):
```json
"endpoint": "https://psegtmfdryuatv01.openai.azure.us/"
```
(Keep `embedDeployment` = `textembeddingadapsegtmandev01`.)

---

### Run these

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment
SV=2024-05-01-preview

# 1. Grant the SEARCH service managed identity 'Cognitive Services OpenAI User' on the Foundry resource
#    (needed for the embedding skill + query-time vectorizer to authenticate).
SEARCH_NAME=$(python -c "import json;print(json.load(open('deploy.config.json'))['search']['endpoint'].split('//')[1].split('.')[0])")
SEARCH_RG=$(az resource list --name "$SEARCH_NAME" --resource-type Microsoft.Search/searchServices --query "[0].resourceGroup" -o tsv)
SEARCH_PRINCIPAL=$(az search service show -n "$SEARCH_NAME" -g "$SEARCH_RG" --query "identity.principalId" -o tsv)
FOUNDRY_ID=$(az resource show -g psegtmrguatv01 -n psegtmfdryuatv01 --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)
echo "SEARCH_NAME=$SEARCH_NAME  SEARCH_PRINCIPAL=$SEARCH_PRINCIPAL"
# If SEARCH_PRINCIPAL is empty, the search service has no system identity yet — tell me and stop.
az role assignment create --assignee "$SEARCH_PRINCIPAL" --role "Cognitive Services OpenAI User" --scope "$FOUNDRY_ID" 2>&1 | tail -3 || echo "(role may already exist; continuing)"

# 2. Redeploy search artifacts — the vectorizer error should now be gone. STOP and report if it errors.
python scripts/deploy_search.py --config deploy.config.json

# 3. Get search endpoint + names
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
ep=c["search"]["endpoint"].rstrip("/"); pfx=c["search"].get("artifactPrefix") or "mm-manuals"
print("SEARCH_EP=",ep); print("INDEXER=",f"{pfx}-indexer"); print("INDEX=",f"{pfx}-index")
PY

# 4. Reset + run the indexer (substitute SEARCH_EP / INDEXER)
az rest --method post --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/reset?api-version=$SV" -o none
az rest --method post --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/run?api-version=$SV" -o none
echo "indexer reset+run issued"

# 5. Poll until not inProgress (every ~30s)
for i in $(seq 1 40); do
  S=$(az rest --method get --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/status?api-version=$SV" --query "lastResult.status" -o tsv)
  echo "poll $i: $S"; [ "$S" != "inProgress" ] && break; sleep 30
done

# 6. Final status
az rest --method get --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/status?api-version=$SV" --query "{status:lastResult.status, processed:lastResult.itemsProcessed, failed:lastResult.itemsFailed, errors:lastResult.errors[0:5].errorMessage, warnings:lastResult.warnings[0:5].message}" -o json

# 7. Per-document record_type counts (substitute SEARCH_EP / INDEX)
for F in "GD-GA-CTP.pdf" "GD-AS-DWM.pdf" "ED-EO-OPM.pdf"; do
  echo "=== $F ==="
  for RT in text diagram table table_row summary; do
    N=$(az rest --method post --resource "https://search.azure.us" \
        --url "<SEARCH_EP>/indexes/<INDEX>/docs/search?api-version=$SV" \
        --headers "Content-Type=application/json" \
        --body "{\"search\":\"*\",\"filter\":\"source_file eq '$F' and record_type eq '$RT'\",\"count\":true,\"top\":0}" \
        --query "\"@odata.count\"" -o tsv)
    echo "  $RT: $N"
  done
done

# 8. Vector + contract checks
python scripts/smoke_test.py --config deploy.config.json --skip-run
python scripts/index_query_guide.py --config deploy.config.json --demo
```

### Print this REPORT block

```
CONFIG_EDIT: <applied yes/no>
SEARCH_PRINCIPAL: <id or empty>   SEARCH_ROLE_ON_FOUNDRY: <created/exists/failed>
DEPLOY_SEARCH: <ok / error text>
INDEXER_STATUS: <success/failed>  processed=<n>  failed=<n>
INDEXER_ERRORS: <first few, or none>
PER_DOC COUNTS:
  GD-GA-CTP.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  GD-AS-DWM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  ED-EO-OPM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
SMOKE_TEST: <pass/fail + key lines>
VECTOR_DEMO: <did vector/semantic queries return results? yes/no>
OTHER_ERRORS: <any, or none>
```

If `deploy_search.py` still errors, stop after step 2 and paste the error. Otherwise run everything and give me the REPORT block.
