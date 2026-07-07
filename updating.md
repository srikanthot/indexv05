Hi Copilot — great, the GPT-5.1 vision fix is proven (590 figures, 0 errors,
output.json built for all 3 test PDFs). Now let's run the actual indexer on those
same 3 PDFs and verify that diagram/table/vector records land in the index.

Do NOT edit any code. Run the steps and print the REPORT block. Set your usual
SSL cert env first. If a command errors, stop and tell me the exact error.

The 3 test PDFs from the last step: `GD-GA-CTP.pdf`, `GD-AS-DWM.pdf`, `ED-EO-OPM.pdf`.

---

### Steps

```bash
# 0. Environment
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment

# 1. Refresh the search artifacts (index / skillset / indexer / datasource) to current definitions.
#    If this errors on an index-schema update, STOP and paste me the error.
python scripts/deploy_search.py --config deploy.config.json

# 2. Get search endpoint + indexer name
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
ep=c["search"]["endpoint"].rstrip("/")
prefix=c["search"].get("artifactPrefix") or "mm-manuals"
print("SEARCH_EP=",ep)
print("INDEXER=",f"{prefix}-indexer")
print("INDEX=",f"{prefix}-index")
PY

# 3. Reset + run the indexer (substitute SEARCH_EP / INDEXER from step 2)
SV=2024-05-01-preview
az rest --method post --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/reset?api-version=$SV" -o none
az rest --method post --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/run?api-version=$SV" -o none
echo "indexer reset+run issued"

# 4. Poll status until it is no longer inProgress (check every ~30s)
for i in $(seq 1 40); do
  S=$(az rest --method get --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/status?api-version=$SV" --query "lastResult.status" -o tsv)
  echo "poll $i: $S"
  if [ "$S" != "inProgress" ]; then break; fi
  sleep 30
done

# 5. Final indexer status (status, counts, first errors/warnings)
az rest --method get --resource "https://search.azure.us" --url "<SEARCH_EP>/indexers/<INDEXER>/status?api-version=$SV" --query "{status:lastResult.status, processed:lastResult.itemsProcessed, failed:lastResult.itemsFailed, errors:lastResult.errors[0:5].errorMessage, warnings:lastResult.warnings[0:5].message}" -o json
```

```bash
# 6. Per-document verification: record_type counts for each test PDF (substitute SEARCH_EP / INDEX)
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
```

```bash
# 7. Confirm vectors + new fields landed (contract + null-rate checks)
python scripts/smoke_test.py --config deploy.config.json --skip-run
python scripts/audit_all_retrievable_fields.py --config deploy.config.json
python scripts/index_query_guide.py --config deploy.config.json --demo
```

### Print this REPORT block

```
DEPLOY_SEARCH: <ok / error>
INDEXER_STATUS: <success/transientFailure/failed>  processed=<n>  failed=<n>
INDEXER_ERRORS: <paste first few, or none>
PER_DOC COUNTS:
  GD-GA-CTP.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  GD-AS-DWM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  ED-EO-OPM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
SMOKE_TEST: <pass/fail + key lines>
AUDIT: <any critical/high findings, or clean>
VECTOR_DEMO (index_query_guide): <did semantic+vector queries return results? yes/no>
OTHER_ERRORS: <paste any, or none>
```

If anything errors, stop and give me the error. Otherwise run everything and give me the REPORT block.
