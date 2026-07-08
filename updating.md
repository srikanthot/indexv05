Hey Copilot — ROOT CAUSE FOUND: the host loaded 0 functions because the last deployment was an
az config-zip "Extract zip" with NO build, so the Python dependencies were never pip-installed
(the worker can't load azure.functions -> 0 functions -> 404 on every route). Settings are now
correct (SCM_DO_BUILD_DURING_DEPLOYMENT=true, ENABLE_ORYX_BUILD=true, no WEBSITE_RUN_FROM_PACKAGE).
We just need ONE deployment that actually runs the build. This script does that (func via cmd to
avoid the PS stderr trap; Kudu build-deploy as fallback), VERIFIES functions loaded, then reindexes.
Run in PowerShell. Do NOT set $ErrorActionPreference='Stop'. Paste me every "===" line.

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/'); $idx = "$($cfg.search.artifactPrefix)-index"; $ixr = "$($cfg.search.artifactPrefix)-indexer"
$hostUrl = "https://$app.azurewebsites.us"
$mk = az functionapp keys list -g $rg -n $app --query masterKey -o tsv

# make sure build stays on and run-from-package is gone
az functionapp config appsettings set -g $rg -n $app --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true | Out-Null
az functionapp config appsettings delete -g $rg -n $app --setting-names WEBSITE_RUN_FROM_PACKAGE 2>$null | Out-Null
Get-ChildItem -Path function_app -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
az functionapp restart -g $rg -n $app | Out-Null
Start-Sleep -Seconds 30

# === ATTEMPT 1: func publish WITH remote build, run through cmd.exe (no PS stderr trap) ===
Write-Host "=== A1: func publish (remote build)... ==="
Push-Location function_app
cmd /c "func azure functionapp publish $app --python --build remote > ..\pub1.log 2>&1"
Pop-Location
Get-Content pub1.log | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
Start-Sleep -Seconds 90
try { $fns = (Invoke-RestMethod -Uri "$hostUrl/admin/functions" -Headers @{ 'x-functions-key' = $mk } -TimeoutSec 60).Count } catch { $fns = -1 }
Write-Host "=== A1 RESULT: LOADED FUNCTIONS = $fns ==="

# === ATTEMPT 2 (only if still 0): Kudu build-deploy via AAD (honors the build setting) ===
if ($fns -le 0) {
  Write-Host "=== A2: func didn't load functions; trying Kudu build-deploy... ==="
  if (Test-Path funcapp.zip) { Remove-Item funcapp.zip -Force }
  Compress-Archive -Path function_app\* -DestinationPath funcapp.zip -Force
  $armTok = az account get-access-token --query accessToken -o tsv
  try {
    Invoke-RestMethod -Uri "$($hostUrl -replace '\.azurewebsites\.us','.scm.azurewebsites.us')/api/zipdeploy?isAsync=false" -Headers @{ Authorization = "Bearer $armTok" } -Method Post -InFile funcapp.zip -ContentType "application/zip" -TimeoutSec 1800 | Out-Null
    Write-Host "=== A2: kudu build-deploy submitted ==="
  } catch { Write-Host "=== A2: kudu deploy error: $($_.Exception.Message) ===" }
  Start-Sleep -Seconds 200
  try { $fns = (Invoke-RestMethod -Uri "$hostUrl/admin/functions" -Headers @{ 'x-functions-key' = $mk } -TimeoutSec 60).Count } catch { $fns = -1 }
  Write-Host "=== A2 RESULT: LOADED FUNCTIONS = $fns ==="
}

# === CHECK: functions loaded? ===
if ($fns -le 0) {
  Write-Host "=== STILL 0 FUNCTIONS. Build/deploy log (the reason): ==="
  az webapp log deployment show -g $rg -n $app 2>&1 | Select-Object -Last 50 | ForEach-Object { Write-Host $_ }
  Write-Host "=== STOP: paste me the log above. ==="
  exit 1
}
Write-Host "=== FUNCTIONS LOADED OK ($fns). Reindexing... ==="

# === REINDEX + CAPTURE ===
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== STATUS: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="
$st.lastResult.errors | Select-Object -First 2 | Format-List
$body = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body
Write-Host "=== TOTAL DOCS IN INDEX: $($cnt.'@odata.count') ==="
