# Indexer Testing — Portal-Only Runbook (no CLI needed)

Two paths in this document:

- **PATH A — Portal-only quick scale-up**: most practical. Edit skillset JSON in Portal, enable auto-heal, reset, run. ~10 min of clicks. Recommended first.
- **PATH B — CLI runbook** (only if you regain VS Code credentials): single-PDF isolation test for surgical diagnosis.

---

# PATH A — Portal-Only (do this if no CLI access)

You'll do everything in the Azure Portal. Five sections of click instructions below.

## A.1 — Raise dop on heavy skills (Portal: Search service)

1. Open Azure Portal → navigate to your Search service: **srch02-pseg-tman-dev01**
2. Left blade → click **Skillsets**
3. Click on **psegtechmanuals-v01-skillset** (or whatever it's named — there should only be one)
4. At the top of the skillset detail page, click **Skillset definition (JSON)** or **Edit JSON**
5. In the JSON editor, **use Ctrl+F** (Find) to locate `"degreeOfParallelism"`
6. You'll find 7 entries. Update them as follows:

| Skill name | Current dop | New dop |
|---|---|---|
| process-document-skill | 1 | leave at 1 |
| extract-page-label-skill | 2 | **6** |
| analyze-diagram-skill | 2 | **4** |
| shape-table-skill | 2 | **4** |
| build-semantic-string-text | 2 | **4** |
| build-semantic-string-diagram | 2 | **4** |
| build-doc-summary-skill | 1 | leave at 1 |

7. Click **Save** at the top
8. Confirm the save dialog

## A.2 — Enable auto-heal (Portal: Function App)

1. Portal → navigate to your Function App: **azureindex-functionv05**
2. Left blade → under **Settings** → click **Environment variables** (or **Configuration** in older Portal views)
3. Find the row **AUTO_HEAL_ENABLED**
4. Click on its value, change from `false` to `true`
5. Click **Save** at the top
6. Confirm the prompt about restarting

## A.3 — Restart the Function App (Portal)

1. Same Function App page → left blade → click **Overview**
2. At the top, click the **Restart** button
3. Confirm

Wait 60 seconds for the function app to come back up.

## A.4 — Reset and run the indexer (Portal: Search service)

1. Portal → Search service: **srch02-pseg-tman-dev01**
2. Left blade → click **Indexers**
3. Click on **psegtechmanuals-v01-indexer**
4. At the top, click the **Reset** button
5. Confirm
6. Click the **Run** button right next to Reset
7. Confirm

The indexer immediately starts a fresh run.

## A.5 — Monitor progress (Portal)

Three places to watch:

### Indexer execution history (real-time)

1. Same indexer page → scroll down to **Execution History**
2. You'll see the new run with status **In progress**
3. Click on it to see live counters: items processed, errors

### Function App health (App Insights)

1. Portal → Function App **azureindex-functionv05**
2. Left blade → **Application Insights** (or look for Insights icon at top)
3. Click **Failures** in left blade
4. Set time range to **Last 30 minutes**
5. Watch the failure count. Should stay low (under ~50 per hour).

### Records in index (lightweight)

1. Portal → Search service → **Indexes** → click **psegtechmanuals-v01-index**
2. **Document count** at top of the page is the total chunk count
3. Refresh every 15-30 min to see it climb

---

# PATH A — Decision after first 2-hour cycle

After the first run completes (Success / Partial / Failed):

### If Status = "Success" and document count climbed significantly
**Great** — the dop increase was enough. Auto-heal will continue retrying any stuck docs every 30 min.

### If Status = "Partial success" with some docs done
**Acceptable** — auto-heal will pick up the failed ones automatically. Wait 30-60 min, check again. Doc count should keep climbing across runs.

### If Status = "Failed" with 0 docs succeeded again
**Need PATH C** — upgrade Function App SKU. See A.6 below.

## A.6 — Upgrade Function App SKU (only if Path A wasn't enough)

This costs more $$ but adds memory headroom for big PDFs.

1. Portal → Function App **azureindex-functionv05** → top of page, look for **App Service Plan** name (e.g., `ASP-...-E1P1`)
2. Click on the App Service Plan name (it's a link)
3. On the App Service Plan page, left blade → **Scale up (App Service plan)**
4. Choose **Premium V3** → **EP2** (7 GB RAM, 2 cores) or **EP3** (14 GB RAM, 4 cores)
5. Click **Apply**

After upgrade completes (~3-5 min):

6. Restart the Function App (A.3)
7. Reset + Run the indexer (A.4)

---

# PATH B — CLI Single-PDF Validation (only if you have CLI access again)

If/when you regain VS Code CLI access, this is the more methodical approach. Skip this section if doing Portal-only.

## B.1 — Acquire token

```
$TOKEN = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
```

## B.2 — Disable auto-heal

```
az functionapp config appsettings set -n azureindex-functionv05 -g rg-pseg-tman-dev01 --settings AUTO_HEAL_ENABLED=false
```

```
az functionapp restart -n azureindex-functionv05 -g rg-pseg-tman-dev01
```

```
Start-Sleep -Seconds 60
```

## B.3 — Reset only ED-EM-SSM.pdf

```
$body = '{"datasourceDocumentIds":["https://sapsegmandev01.blob.core.usgovcloudapi.net/techmanualsv07/ED-EM-SSM.pdf"]}'
```

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/resetdocs?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d $body
```

## B.4 — Bump its lastModified

```
az storage blob metadata update --account-name sapsegmandev01 --container-name techmanualsv07 --name "ED-EM-SSM.pdf" --metadata "test_run=$(Get-Date -Format yyyyMMddHHmmss)" --auth-mode login
```

## B.5 — Trigger run

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexers/psegtechmanuals-v01-indexer/run?api-version=2024-05-01-preview" -H "Authorization: Bearer $TOKEN" -H "Content-Length: 0"
```

## B.6 — Record start time

```
Get-Date -Format "yyyy-MM-dd HH:mm:ss"
```

## B.7 — Count records produced after run completes

```
$KEY = az search admin-key show --service-name srch02-pseg-tman-dev01 --resource-group rg-pseg-tman-dev01 --query primaryKey -o tsv
```

```
$body2 = '{"search":"*","filter":"source_file eq ''ED-EM-SSM.pdf''","facets":["record_type"],"top":0,"count":true}'
```

```
curl -X POST "https://srch02-pseg-tman-dev01.search.azure.us/indexes/psegtechmanuals-v01-index/docs/search?api-version=2024-05-01-preview" -H "Content-Type: application/json" -H "api-key: $KEY" -d $body2
```

---

# Quick Reference Tables

## Where to find each Azure resource in Portal

| Thing you need | Portal path |
|---|---|
| Function App | Subscriptions → resource group **rg-pseg-tman-dev01** → **azureindex-functionv05** |
| Function App App Settings | Function App → left blade **Environment variables** (or **Configuration**) |
| Function App Restart | Function App → **Overview** → **Restart** button at top |
| App Service Plan | Function App page → click the App Service Plan link near top |
| Search Service | Subscriptions → resource group → **srch02-pseg-tman-dev01** |
| Indexers list | Search service → left blade **Indexers** |
| Skillsets list | Search service → left blade **Skillsets** |
| Indexer Reset/Run | Open indexer → buttons at top of page |
| Edit skillset JSON | Open skillset → **Skillset definition** or **Edit JSON** button |
| App Insights | Function App → top of page or left blade **Application Insights** |
| Storage Account | Subscriptions → resource group → **sapsegmandev01** |
| Container | Storage Account → **Containers** → **techmanualsv07** |
| Blob metadata | Open a blob → **Metadata** tab → add/edit/save |

## Status interpretation

| Indexer Status | Docs succeeded | Meaning |
|---|---|---|
| In progress | N/A | Currently running |
| Success | > 0 | Completed successfully |
| Success | 0 | Completed but no work to do (everything already done OR all on failed-items list) |
| Partial success | > 0 | Some docs succeeded, some failed |
| Partial success | 0 | Some skill calls failed but no doc fully completed |
| Failed | 0 | Run aborted (often hit max failed items or fatal error) |

---

# What to do RIGHT NOW (no CLI access)

Execute Path A sections **A.1 → A.2 → A.3 → A.4** in order. ~10 minutes of Portal clicks. Then monitor with A.5 for the next 1-3 hours.

If after the first run cycle DONE count is climbing, you're done — auto-heal will handle the rest.

If after 2 hours the indexer fails with 0 docs again, do A.6 (upgrade SKU to EP2) and re-run A.3 + A.4.
