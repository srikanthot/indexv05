Hey Copilot — the 5-doc verification showed text/diagram/summary fields are correct, but ALL
table + table_row fields are empty because the deployed process_table.py/tables.py were a stale
copy. The user has re-copied process_table.py, tables.py, page_label.py, semantic.py (full,
correct versions). Now: redeploy (the build-deploy that worked), reindex, and re-run the two
verification scripts so we can confirm tables populate. Run in PowerShell. Do NOT set
$ErrorActionPreference='Stop'. Paste me every "===" line + the verify_new_fields table output.

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/'); $idx = "$($cfg.search.artifactPrefix)-index"; $ixr = "$($cfg.search.artifactPrefix)-indexer"
$hostUrl = "https://$app.azurewebsites.us"
$mk = az functionapp keys list -g $rg -n $app --query masterKey -o tsv

# keep build on; also lower the OCR flag threshold via app setting (belt + suspenders)
az functionapp config appsettings set -g $rg -n $app --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true OCR_CONFIDENCE_FLOOR=0.6 | Out-Null
Get-ChildItem -Path function_app -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# === DEPLOY: func publish with remote build (via cmd, no PS stderr trap) ===
Write-Host "=== deploying... ==="
Push-Location function_app
cmd /c "func azure functionapp publish $app --python --build remote > ..\pub.log 2>&1"
Pop-Location
Get-Content pub.log | Select-Object -Last 25 | ForEach-Object { Write-Host $_ }
Start-Sleep -Seconds 90
try { $fns = (Invoke-RestMethod -Uri "$hostUrl/admin/functions" -Headers @{ 'x-functions-key' = $mk } -TimeoutSec 60).Count } catch { $fns = -1 }
Write-Host "=== LOADED FUNCTIONS = $fns (must be > 0) ==="
if ($fns -le 0) { Write-Host "=== deploy did not load functions; STOP and tell me ==="; exit 1 }

# === REINDEX ===
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== INDEXER: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="

# === RE-VERIFY (the key part -- tables should now be populated) ===
Write-Host "=== GATES ==="
python scripts/validate_index_quality.py --config deploy.config.json
Write-Host "=== FIELDS ==="
python scripts/verify_new_fields.py --config deploy.config.json

# Paste me the INDEXER line, the GATES RESULT+coverage, and the full FIELDS table
# (especially the record_type = table and table_row sections).
