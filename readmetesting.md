# Indexer Testing — Validate one big PDF first, then scale

This runbook walks through the methodical approach for diagnosing why big PDFs
(50-95 MB) don't fit in the indexer's 2-hour run window. Test one big PDF in
isolation first; based on result, scale dop and/or upgrade SKU.

All commands are PowerShell single-line. Copy and paste each block one at a
time. Hit Enter after each.

---

## Step 0 — Acquire the Search service AAD token

```
$TOKEN = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
```

Verify it set:

```
$TOKEN.Length
```

(Should print a number > 1000.)

---

## Step 1 — Disable auto-heal temporarily (so it doesn't interfere with the test)

```
az functionapp config appsettings set -n azureindex-functionv05 -g rg-pseg-tman-dev01 --settings AUTO_HEAL_ENABLED=false
```

```
az functionapp restart -n azureindex-functionv05 -g rg-pseg-tman-dev01
```

Wait 60 seconds before continuing.

```
Start-Sleep -Seconds 60
```

---

## Step 2 — Pick ONE big PDF to test (ED-EM-SSM.pdf, 95.46 MiB)

### 2a — Reset just this one PDF in the indexer

```
$body = '{"datasourceDocumentIds":["https://sapsegmandev01.blob.core.usgovcloudapi.net/techmanualsv07/ED-EM-SSM.pdf"]}'
```

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/resetdocs?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d $body
```

### 2b — Bump the blob's lastModified so the indexer sees it as "new work"

```
az storage blob metadata update --account-name sapsegmandev01 --container-name techmanualsv07 --name "ED-EM-SSM.pdf" --metadata "test_run=$(Get-Date -Format yyyyMMddHHmmss)" --auth-mode login
```

### 2c — Trigger the indexer run

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/run?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Length: 0"
```

### 2d — Note the START TIME

```
Get-Date -Format "yyyy-MM-dd HH:mm:ss"
```

Write this down. You'll compare against END TIME later.

---

## Step 3 — Monitor the test run

Open Azure Portal → Search service `srch02-pseg-tman-dev01` → Indexers → `psegtechmanuals-v01-indexer` → click on the new "In progress" execution row.

Watch:
- **Items processed** counter — should climb steadily
- **Errors or warnings** — should stay at 0/0 ideally
- **Status** — In Progress → Success / Partial Success / Failed

Also open Application Insights → Failures for the function app `azureindex-functionv05`. Filter to last 30 minutes. Any 500s during this test are critical.

---

## Step 4 — Once the run completes, record results

### 4a — Check final indexer status (Success / Partial Success / Failed)

In the Portal, look at the latest execution row.

### 4b — Record how many records the test PDF produced (broken down by type)

```
$KEY = az search admin-key show --service-name srch02-pseg-tman-dev01 --resource-group rg-pseg-tman-dev01 --query primaryKey -o tsv
```

```
$body2 = '{"search":"*","filter":"source_file eq ''ED-EM-SSM.pdf''","facets":["record_type"],"top":0,"count":true}'
```

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexes/psegtechmanuals-v01-index/docs/search?api-version=2024-05-01-preview" -H "Content-Type: application/json" -H "api-key: $KEY" -d $body2
```

The response shows total record count and breakdown by record_type:
- text
- diagram
- table
- table_row
- summary

### 4c — Check failure rate during the test

In App Insights, run this KQL:

```
requests | where timestamp > ago(2h) | where cloud_RoleName == "azureindex-functionv05" | summarize total = count(), failed = countif(success == false), failure_rate_pct = round(100.0 * countif(success == false) / count(), 3) by operation_Name | order by failed desc
```

Note total calls and failure rate per skill.

---

## Step 5 — Decision tree based on results

### Result A — ED-EM-SSM completed in < 30 min alone:
**Diagnosis**: Single-PDF capacity is fine. Problem is concurrent runs.

**Action**: Raise dop on the highest-volume skills. Edit `search/skillset.json`:
- `extract-page-label-skill`: dop 2 → 6
- `analyze-diagram-skill`: dop 2 → 4
- `shape-table-skill`: dop 2 → 4
- Keep `build-doc-summary-skill` and `process-document-skill` at 1

Then redeploy search artifacts:
```
python scripts/deploy_search.py --config deploy.config.json
```

No SKU upgrade needed.

### Result B — ED-EM-SSM completed in 30-90 min alone:
**Diagnosis**: Memory tight, need both more headroom AND more parallelism.

**Action**: Upgrade Function App SKU first. Find your App Service Plan name:
```
az functionapp show -n azureindex-functionv05 -g rg-pseg-tman-dev01 --query "appServicePlanId" -o tsv
```

Then upgrade (replace `<plan-name>` with the last segment of the plan ID from above):
```
az appservice plan update -g rg-pseg-tman-dev01 --name <plan-name> --sku EP2
```

After SKU upgrade, raise dop as in Result A.

### Result C — ED-EM-SSM did NOT finish in 2 hours alone:
**Diagnosis**: One PDF consumes the whole budget. Capacity alone won't fix this; record volume is the issue.

**Action**: Implement smart row-record filtering. The largest record source (per-row table records) is producing noise. We can keep useful rows and skip noise rows. See `function_app/shared/tables.py` — adjust `_build_row_records_for_cluster` to skip rows that are:
- Mostly empty (<20 chars of meaningful text)
- Pure numeric/symbol with no descriptive text
- Duplicate of an earlier row in the same table

This preserves the feature (per-row retrieval) but cuts noise records by 40-60%.

After filtering change, redeploy function app:
```
.\scripts\deploy_function.ps1 deploy.config.json
```

---

## Step 6 — Re-enable auto_heal

After whichever path you took completes, turn auto_heal back on:

```
az functionapp config appsettings set -n azureindex-functionv05 -g rg-pseg-tman-dev01 --settings AUTO_HEAL_ENABLED=true
```

```
az functionapp restart -n azureindex-functionv05 -g rg-pseg-tman-dev01
```

---

## Step 7 — Roll out to all 56 PDFs

Once the test PDF succeeds with the new configuration, reset the indexer fully and let it process all 56:

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/reset?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Length: 0"
```

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/run?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Length: 0"
```

Monitor with:

```
python scripts/check_index.py --config deploy.config.json --coverage
```

Run every 30 min. DONE count climbs from 0 → 56 over 1-3 hours depending on which option (A/B/C) you took.

---

## Troubleshooting

### If `$TOKEN` expires (it lasts ~1 hour):

```
$TOKEN = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
```

### If the indexer shows "Success 6s 0 docs" repeatedly:

The blob's lastModified didn't advance. Re-run the metadata update from step 2b.

### If a single skill is failing repeatedly:

Get the exception details in App Insights:

```
exceptions | where timestamp > ago(1h) | where cloud_RoleName == "azureindex-functionv05" | project timestamp, type, outerMessage, operation_Name | order by timestamp desc | take 20
```

The `type` and `outerMessage` columns tell you the specific failure mode.

---

## Quick reference — Status check anytime

How many PDFs have summary records (= fully done):

```
python scripts/check_index.py --config deploy.config.json --coverage
```

Total chunks in index:

```
curl "https://srch02-pseg-tman-dev01.search.azure.us/indexes/psegtechmanuals-v01-index/docs/`$count?api-version=2024-05-01-preview" -H "api-key: $KEY"
```

Latest indexer status:

```
curl "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/status?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN"
```
