Hey Copilot — the real indexer error is a Python NameError in the extract-page-label skill:
"NameError: name 'retrieval_eligible' is not defined". The source SHOULD define that variable
before using it, so either the local page_label.py is a truncated/partial copy, OR the server
is running stale compiled bytecode. This script checks which, clears stale bytecode, does a
forced clean redeploy, and re-runs the indexer. Run it all in PowerShell, in order, and paste
me the output of every line that starts with "===".

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/')
$idx = "$($cfg.search.artifactPrefix)-index"
$ixr = "$($cfg.search.artifactPrefix)-indexer"

# === CHECK A: is the retrieval_eligible DEFINITION present in the local file? ===
$hit = Select-String -Path function_app/shared/page_label.py -Pattern 'retrieval_eligible = bool\('
if ($hit) { Write-Host "=== A: DEFINITION PRESENT at line $($hit.LineNumber) ===" }
else       { Write-Host "=== A: DEFINITION MISSING -- page_label.py is a BAD/partial copy. STOP: re-copy page_label.py from the branch (use the Raw button, Ctrl+A, Ctrl+C), then rerun this script. ===" }

# === CHECK B: does the local code actually run end to end? ===
Write-Host "=== B: running test_unit ==="
python tests/test_unit.py 2>&1 | Select-String "Results:"
# If A says MISSING, or B does not print "Results: 291/294 passed", STOP here and re-copy
# page_label.py from the branch. Do NOT deploy a bad file.

# === STEP C: delete stale compiled bytecode so no old .pyc gets shipped ===
Get-ChildItem -Path function_app -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "=== C: cleared __pycache__ ==="

# === STEP D: forced clean republish (restart -> publish -> restart again to reload) ===
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 45
Push-Location function_app
func azure functionapp publish $app --python --build remote
Pop-Location
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 60
Write-Host "=== D: republished + restarted ==="

# === STEP E: reset + re-run the indexer ===
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop

# === STEP F: wait, then capture the result ===
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== F STATUS: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="
Write-Host "=== F FIRST ERRORS ==="
$st.lastResult.errors | Select-Object -First 2 | Format-List
$body = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body
Write-Host "=== F TOTAL DOCS IN INDEX: $($cnt.'@odata.count') ==="

# Paste me every "===" line, especially CHECK A, CHECK B, and STEP F.
