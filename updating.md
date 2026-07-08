Hey Copilot — the indexer failed all 5 docs with "Web API skill response is invalid". That
means the function app is crashing (HTTP 500) on every call — almost certainly the deployed
app is missing one of the new shared files or is stale. Please do a CLEAN republish and re-run
the indexer, then capture the result. Run the whole thing in PowerShell, in order. When it
finishes, paste me the FULL output from STEP 4 and STEP 5 (that's what I need to see).

# ---- read config (no placeholders to fill) ----
$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name
$rg  = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/')
$idx = "$($cfg.search.artifactPrefix)-index"
$ixr = "$($cfg.search.artifactPrefix)-indexer"
Write-Host "App=$app RG=$rg Index=$idx Indexer=$ixr"

# ---- STEP 1: confirm the LOCAL function_app has ALL the new files (must print OK) ----
python -c "import sys; sys.path.insert(0,'function_app'); import shared.page_label, shared.process_table, shared.diagram, shared.summary, shared.content_classifiers, shared.procedures, shared.prompt_safety, shared.aoai; print('ALL skill modules import OK')"
# If this does NOT print 'ALL skill modules import OK', STOP and tell me which import failed
# (a file is missing or corrupt locally and must be re-copied before publishing).

# ---- STEP 2: clean republish of the function code + set the working api-version ----
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 60
az functionapp config appsettings set -g $rg -n $app --settings FOUNDRY_API_VERSION=2024-10-21
Write-Host ("AOAI_ENDPOINT = " + (az functionapp config appsettings list -g $rg -n $app --query "[?name=='AOAI_ENDPOINT'].value" -o tsv))
Push-Location function_app
func azure functionapp publish $app --python --build remote
Pop-Location
Start-Sleep -Seconds 60   # let the app warm up after publish

# ---- STEP 3: redeploy search + reset + re-run the indexer (no preanalyze, no heal loop) ----
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop

# ---- STEP 4: wait, then capture the indexer result + errors ----
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== INDEXER STATUS ==="
Write-Host ("lastResult status : " + $st.lastResult.status)
Write-Host ("items processed   : " + $st.lastResult.itemsProcessed)
Write-Host ("items failed      : " + $st.lastResult.itemsFailed)
Write-Host "=== FIRST 3 ERRORS (this is the key part) ==="
$st.lastResult.errors   | Select-Object -First 3 | Format-List
Write-Host "=== FIRST 3 WARNINGS ==="
$st.lastResult.warnings | Select-Object -First 3 | Format-List
# NOTE: if 'lastResult status' says 'inProgress', wait another 3-4 minutes and re-run STEP 4.

# ---- STEP 5: how many docs actually landed in the index ----
$body = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body
Write-Host ("=== TOTAL DOCS IN INDEX: " + $cnt.'@odata.count' + " ===")

# Paste me EVERYTHING printed by STEP 4 and STEP 5.
