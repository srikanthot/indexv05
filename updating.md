Hi Copilot — RBAC is done (RoleAssignmentExists) and deploy_search works. But the
indexer is stuck at processed=0 after ~25 min, which is a red flag (even one doc
should have finished). Likely the 37MB / 535-figure doc is stalling on
memory/timeout. This round is READ-ONLY diagnostics to see the scope and whether
it's slow vs genuinely stuck. Do NOT reset/edit anything. Run and print the REPORT.
SSL env as usual; US Gov; sub 5c58d830-b35f-458a-ab5d-65ad9d0b9815.

---

### Run these (read-only)

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815
SV=2024-05-01-preview
SEARCH_EP=$(python -c "import json;print(json.load(open('deploy.config.json'))['search']['endpoint'].rstrip('/'))")
PFX=$(python -c "import json;print(json.load(open('deploy.config.json'))['search'].get('artifactPrefix') or 'mm-manuals')")
INDEXER="${PFX}-indexer"; INDEX="${PFX}-index"
SA=$(python -c "import json;print(json.load(open('deploy.config.json'))['storage']['accountResourceId'].rstrip('/').split('/')[-1])")
CONT=$(python -c "import json;print(json.load(open('deploy.config.json'))['storage']['pdfContainerName'])")

# 1. Container scope: how many PDFs + total size + top 10 largest
echo "STORAGE=$SA CONTAINER=$CONT"
az storage blob list --account-name "$SA" --container-name "$CONT" --auth-mode login --num-results 5000 \
  --query "[?ends_with(name,'.pdf')].{mb: to_number(properties.contentLength), name:name}" -o tsv \
  | awk -F'\t' '{tot+=$1; n++; printf "%.1fMB\t%s\n",$1/1048576,$2} END{printf "TOTAL_PDFS=%d TOTAL_MB=%.0f\n", n, tot/1048576}' \
  | sort -rn | head -15

# 2. Indexer execution history — does it ever complete, and how long? (last 5 runs)
az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" \
  --query "executionHistory[0:5].{status:status, processed:itemsProcessed, failed:itemsFailed, start:startTime, end:endTime, err:errorMessage}" -o json

# 3. Current run detail (status, processed, and up to 10 errors/warnings)
az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" \
  --query "{status:lastResult.status, processed:lastResult.itemsProcessed, failed:lastResult.itemsFailed, errors:lastResult.errors[0:10], warnings:lastResult.warnings[0:10]}" -o json

# 4. Patient poll (every 60s, up to ~20 min): watch processed + whether the SMALL test doc gets records
for i in $(seq 1 20); do
  S=$(az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "lastResult.status" -o tsv)
  P=$(az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "lastResult.itemsProcessed" -o tsv)
  C=$(az rest --method post --resource "https://search.azure.us" --url "$SEARCH_EP/indexes/$INDEX/docs/search?api-version=$SV" --headers "Content-Type=application/json" --body "{\"search\":\"*\",\"filter\":\"source_file eq 'GD-GA-CTP.pdf'\",\"count\":true,\"top\":0}" --query "\"@odata.count\"" -o tsv)
  echo "poll $i: status=$S processed=$P  GD-GA-CTP_records=$C"
  if [ "$S" != "inProgress" ] || [ "${C:-0}" -gt 0 ]; then break; fi
  sleep 60
done

# 5. Final per-doc counts for all 3 (whatever exists so far)
for F in "GD-GA-CTP.pdf" "GD-AS-DWM.pdf" "ED-EO-OPM.pdf"; do
  echo "=== $F ==="
  for RT in text diagram table table_row summary; do
    N=$(az rest --method post --resource "https://search.azure.us" --url "$SEARCH_EP/indexes/$INDEX/docs/search?api-version=$SV" --headers "Content-Type=application/json" --body "{\"search\":\"*\",\"filter\":\"source_file eq '$F' and record_type eq '$RT'\",\"count\":true,\"top\":0}" --query "\"@odata.count\"" -o tsv)
    echo "  $RT: $N"
  done
done

# 6. Function app health — recent restarts / 5xx (best-effort; OOM shows as restarts)
FUNC=$(python -c "import json;print(json.load(open('deploy.config.json'))['functionApp']['name'])")
FRG=$(python -c "import json;print(json.load(open('deploy.config.json'))['functionApp']['resourceGroup'])")
az monitor metrics list --resource "/subscriptions/5c58d830-b35f-458a-ab5d-65ad9d0b9815/resourceGroups/$FRG/providers/Microsoft.Web/sites/$FUNC" --metric "Http5xx" "FunctionExecutionCount" --interval PT5M --query "value[].{metric:name.value, points:timeseries[0].data[-6:].{t:timeStamp,v:total}}" -o json 2>&1 | tail -30
```

### Print this REPORT block

```
CONTAINER: TOTAL_PDFS=<n>  TOTAL_MB=<n>   (and top few largest names)
EXECUTION_HISTORY (last 5): <paste — do any show status=success with processed>0? how long did runs take?>
CURRENT_RUN: status=<>  processed=<>  failed=<>   errors=<paste>   warnings=<paste>
POLL_TREND: <paste the poll lines — did processed or GD-GA-CTP_records ever move above 0?>
PER_DOC COUNTS:
  GD-GA-CTP.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  GD-AS-DWM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
  ED-EO-OPM.pdf  text/diagram/table/table_row/summary = <n/n/n/n/n>
FUNC_HEALTH: <any Http5xx > 0? execution count moving? paste the metric summary>
```

Run everything and give me the REPORT. Do not reset or edit anything.
