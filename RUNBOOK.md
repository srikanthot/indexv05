# RUNBOOK — PSEG technical-manuals indexing pipeline

Copy-paste runbook for the full lifecycle: from-scratch clone, full deploy,
single-PDF test run, and the most common recovery paths.

All commands are **PowerShell on Windows**. Bash users can substitute the
obvious equivalents (`Activate.ps1` → `activate`, `\` → `/`, etc.).

---

## TABLE OF CONTENTS

1. [One-time machine setup](#1-one-time-machine-setup)
2. [Clone the repo + Python environment](#2-clone-the-repo--python-environment)
3. [Create deploy.config.json](#3-create-deployconfigjson)
4. [Local sanity check (no cloud cost)](#4-local-sanity-check-no-cloud-cost)
5. [Bootstrap Azure RBAC + permissions](#5-bootstrap-azure-rbac--permissions)
6. [Upload a test PDF](#6-upload-a-test-pdf)
7. [Preanalyze (DI + crops + vision + output assembly)](#7-preanalyze)
8. [Deploy Function App + Search artifacts](#8-deploy-function-app--search-artifacts)
9. [Reset + run indexer](#9-reset--run-indexer)
10. [Validate end-to-end](#10-validate-end-to-end)
11. [RECOVERY: When the indexer hits "DefaultAzureCredential failed"](#11-recovery-defaultazurecredential-failed)
12. [RECOVERY: When the smoke test fails on a specific field](#12-recovery-smoke-test-field-failure)
13. [RECOVERY: Stale rows after a reindex](#13-recovery-stale-rows-after-reindex)
14. [Troubleshooting cheat sheet](#14-troubleshooting-cheat-sheet)

---

## 1. One-time machine setup

Skip if already done on this laptop.

```powershell
# Verify Python 3.11+ installed
python --version

# Verify Azure CLI installed
az --version

# Set Azure to Government cloud (PSEG)
az cloud set --name AzureUSGovernment

# Log in
az login

# (If behind Forcepoint corporate proxy) — set SSL trust env vars
$env:SSL_CERT_FILE = "C:\path\to\corp-ca-bundle.crt"
$env:REQUESTS_CA_BUNDLE = "C:\path\to\corp-ca-bundle.crt"
```

---

## 2. Clone the repo + Python environment

```powershell
# Pick a working folder
cd C:\

# Clone fresh
git clone https://github.com/srikanthot/azureindex.git psegindexv01
cd psegindexv01

# Create + activate virtual env
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Create deploy.config.json

```powershell
# Copy from the example
Copy-Item deploy.config.example.json deploy.config.json
notepad deploy.config.json
```

Fill in your own values:

| Field | Example |
|---|---|
| `functionApp.name` | `azureindex-functionv03` |
| `functionApp.resourceGroup` | `rg-pseg-tman-dev01` |
| `search.endpoint` | `https://srch02-pseg-tman-dev01.search.azure.us` |
| `search.artifactPrefix` | `techmanuals-v05` |
| `azureOpenAI.endpoint` | `https://aopenai-pseg-tman-dev01.openai.azure.us/` |
| `azureOpenAI.embedDeployment` / `chatDeployment` / `visionDeployment` | your AOAI deployment names |
| `aiServices.endpoint` | `https://aiservicesforocr.cognitiveservices.azure.us/` |
| `storage.accountResourceId` | full ARM ID `/subscriptions/.../sapsegtmandevv01` |
| `storage.pdfContainerName` | `techmanualsv04` |
| `cosmos.endpoint` / `database` / containers | optional, recommended |

Save and close.

---

## 4. Local sanity check (no cloud cost)

```powershell
python scripts/smoke_test.py --local
python tests/test_unit.py
```

Both must say **PASSED**. If either fails, stop and post the output.

---

## 5. Bootstrap Azure RBAC + permissions

This assigns the Function App's Managed Identity the right roles on
Storage / Search / AOAI / DI / Cosmos. **Run this every time you create
a new Function App or recreate one** — the MI's principalId changes and
old role assignments don't carry over.

```powershell
python scripts/bootstrap.py --config deploy.config.json --auto-fix
```

**Then wait 10 minutes** for Azure RBAC to propagate. Don't skip this.

```powershell
Write-Host "Waiting 10 min for RBAC propagation..."
Start-Sleep -Seconds 600
Write-Host "Done waiting."
```

---

## 6. Upload a test PDF

Skip if your PDF is already in the container.

```powershell
$STORAGE_ACCOUNT = "sapsegtmandevv01"
$CONTAINER       = "techmanualsv04"
$LOCAL_PATH      = "C:\path\to\ED-ED-ATD.pdf"
$BLOB_NAME       = "ED-ED-ATD.pdf"

az storage blob upload `
  --account-name $STORAGE_ACCOUNT `
  --container-name $CONTAINER `
  --name $BLOB_NAME `
  --file $LOCAL_PATH `
  --auth-mode login
```

---

## 7. Preanalyze

Runs DI + figure cropping + GPT-4 vision + output assembly. Takes 5–15 min
depending on PDF size and figure count.

```powershell
# Full run (DI + vision + output)
python scripts/preanalyze.py --config deploy.config.json
```

If you already have valid DI + vision cache (just need to refresh
output.json after a code change):

```powershell
python scripts/preanalyze.py --config deploy.config.json --phase output --force
```

---

## 8. Deploy Function App + Search artifacts

```powershell
# Deploy the Function App code
.\scripts\deploy_function.ps1

# Wait for it to start
Start-Sleep -Seconds 60

# Push the search index, skillset, datasource, indexer
python scripts/deploy_search.py --config deploy.config.json
```

Expected output from `deploy_search.py`:

```
ok  datasources/<prefix>-ds (201 or 204)
ok  indexes/<prefix>-index (201 or 204)
ok  skillsets/<prefix>-skillset (201 or 204)
ok  indexers/<prefix>-indexer (201 or 204)
```

---

## 9. Reset + run indexer

```powershell
.\scripts\reset_indexer.ps1
```

Wait ~60 seconds for the indexer to complete (the heavy work was already
done in preanalyze).

```powershell
Start-Sleep -Seconds 60
```

---

## 10. Validate end-to-end

```powershell
python scripts/smoke_test.py --config deploy.config.json --skip-run
```

Expected ending:

```
text: <N> record(s)
diagram: <N> record(s)
table: <N> record(s)
table_row: <N> record(s)   (or 0 if no 5-80 row tables)
summary: 1 record(s)
SMOKE TEST PASSED
```

---

## 11. RECOVERY: "DefaultAzureCredential failed"

You see this in the indexer execution status:

```
ClientAuthenticationError DefaultAzureCredential failed to retrieve a token
ManagedIdentityCredential authentication unavailable, no response from the IMDS endpoint
```

This means the Function App's Managed Identity has no role assignments on
the resources our code calls. Almost always happens after creating a new
Function App.

```powershell
# 1. Read config
$CFG = Get-Content deploy.config.json | ConvertFrom-Json
$RG  = $CFG.functionApp.resourceGroup
$FN  = $CFG.functionApp.name
Write-Host "Function App: $FN / Resource Group: $RG"

# 2. Verify MI is enabled, get principalId
$PRINCIPAL = (az functionapp identity show -g $RG -n $FN --query principalId -o tsv)
Write-Host "Principal ID: $PRINCIPAL"

if (-not $PRINCIPAL) {
    az functionapp identity assign -g $RG -n $FN
    Start-Sleep -Seconds 5
    $PRINCIPAL = (az functionapp identity show -g $RG -n $FN --query principalId -o tsv)
    Write-Host "Enabled MI. New principalId: $PRINCIPAL"
}

# 3. Assign roles
python scripts/bootstrap.py --config deploy.config.json --auto-fix

# 4. WAIT 10 MINUTES for RBAC propagation (mandatory)
Write-Host "Waiting 10 min for RBAC propagation..."
Start-Sleep -Seconds 600
Write-Host "Done waiting."

# 5. Restart Function App so it re-acquires identity tokens
az functionapp restart -g $RG -n $FN
Start-Sleep -Seconds 30

# 6. Verify the NEW Function App has the latest code
$KUDU_USER = (az webapp deployment list-publishing-credentials -g $RG -n $FN --query publishingUserName -o tsv)
$KUDU_PASS = (az webapp deployment list-publishing-credentials -g $RG -n $FN --query publishingPassword -o tsv)
$AUTH = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${KUDU_USER}:${KUDU_PASS}"))
$URL  = "https://$FN.scm.azurewebsites.us/api/vfs/site/wwwroot/shared/skill_io.py"

try {
    $resp = Invoke-WebRequest -Uri $URL -Headers @{Authorization="Basic $AUTH"} -UseBasicParsing
    if ($resp.Content -match '"data": None') {
        Write-Host "OK Function App has the latest skill_io.py"
        $needs_redeploy = $false
    } else {
        Write-Host "STALE - skill_io.py is OLD"
        $needs_redeploy = $true
    }
} catch {
    Write-Host "Could not fetch from Kudu: $_"
    $needs_redeploy = $true
}

# 7. Redeploy if stale
if ($needs_redeploy) {
    .\scripts\deploy_function.ps1
    Start-Sleep -Seconds 30
    az functionapp restart -g $RG -n $FN
    Start-Sleep -Seconds 30
}

# 8. Reset + run indexer
.\scripts\reset_indexer.ps1

# 9. Validate
Start-Sleep -Seconds 60
python scripts/smoke_test.py --config deploy.config.json --skip-run
```

---

## 12. RECOVERY: smoke test field failure

The smoke test prints the exact field that didn't pass its contract:

```
text.callouts: empty list
diagram.figure_bbox: expected JSON array, got dict
```

Most common causes:

| Failure | Meaning | Fix |
|---|---|---|
| `text.callouts: empty list` | No WARNING/DANGER/CAUTION in chunk OR extractor regressed | Verify by running `_extract_callouts()` in tests |
| `diagram.figure_bbox: expected JSON array, got dict` | Function App is running pre-Sprint-2 code | Redeploy: `.\scripts\deploy_function.ps1` |
| `*.physical_pdf_pages missing start=N` | bbox cross-validation regression | Pull latest, redeploy |
| `figures_referenced_normalized: empty list` | Function App pre-Sprint-2 OR text has no Figure refs | Verify by chunk content; redeploy if needed |

---

## 13. RECOVERY: stale rows after reindex

After re-indexing with new code, old rows from previous skill_versions
hang around as near-duplicates. Reap them:

```powershell
# Dry-run first (safe — won't delete)
python scripts/reap_stale_rows.py --config deploy.config.json --dry-run

# Actually delete
python scripts/reap_stale_rows.py --config deploy.config.json --yes
```

---

## 14. Troubleshooting cheat sheet

| Symptom | Most likely cause | Fix |
|---|---|---|
| `pip install` SSL error | Forcepoke env vars not set | Section 1, set SSL_CERT_FILE |
| `bootstrap.py` "AuthorizationFailed" | Need Owner/User Access Admin on RG | Have an admin run it |
| `deploy_search.py` 400 on skillset | Schema attribute change rejected | Use a fresh `artifactPrefix` |
| Indexer "Web Api response contains both data and errors" | Function App running pre-`228762f` code | Redeploy: `.\scripts\deploy_function.ps1` |
| Indexer "DefaultAzureCredential failed" | New MI without roles | Section 11 |
| Indexer "did not execute within 00:01:00" | Skill timeout (was PT60S) | Pull latest (PT230S now), redeploy |
| Indexer "Missing or empty value '/.../pdf_total_pages'" | Old output.json missing per-item field | `preanalyze.py --phase output --force` |
| Function App 500 errors with no detail | Cold-start import crash | Pull latest (lazy openai, Pillow declared), redeploy |
| Function App returns null vectors | ConditionalSkill misbehaving | Pull latest (`1f0df2a` reverted them) |
| Cosmos `run_history upsert failed` locally | Local CLI lacks Cosmos data role | Non-fatal, ignore — preanalyze still completes |

---

## Quick sanity checks anytime

```powershell
# Local schema-consistency (free)
python scripts/smoke_test.py --local

# Unit tests (free)
python tests/test_unit.py

# Index status: how many records of each type
python scripts/check_index.py --config deploy.config.json

# Reap stale rows
python scripts/reap_stale_rows.py --config deploy.config.json --dry-run
```
