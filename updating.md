Hey Copilot — you were right: `az config-zip` forces SCM_DO_BUILD_DURING_DEPLOYMENT=false and
sets WEBSITE_RUN_FROM_PACKAGE, so Oryx never installs the Python deps AND it left the app's deploy
settings broken. So do NOT use config-zip. This script: (1) restores the remote-build settings,
(2) publishes with func (remote build) but CAPTURES all output and tolerates the func "No process"
flake, (3) prints the real deployment/build log, then (4) reindexes and reports.

IMPORTANT: do NOT set `$ErrorActionPreference = 'Stop'` this time — we must tolerate the func flake
and stderr noise. Run in PowerShell, in order. Paste me every "===" section, especially "3 FUNC
OUTPUT", "4 DEPLOYMENT LOG", and "5".

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/')
$idx = "$($cfg.search.artifactPrefix)-index"
$ixr = "$($cfg.search.artifactPrefix)-indexer"

# === 1: UNDO what config-zip changed -- restore remote build, remove run-from-package ===
az functionapp config appsettings set -g $rg -n $app --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true | Out-Null
az functionapp config appsettings delete -g $rg -n $app --setting-names WEBSITE_RUN_FROM_PACKAGE 2>$null | Out-Null
Write-Host "=== 1: restored SCM_DO_BUILD_DURING_DEPLOYMENT=true, removed WEBSITE_RUN_FROM_PACKAGE ==="
az functionapp config appsettings list -g $rg -n $app --query "[?name=='SCM_DO_BUILD_DURING_DEPLOYMENT'||name=='WEBSITE_RUN_FROM_PACKAGE'||name=='ENABLE_ORYX_BUILD'||name=='FUNCTIONS_WORKER_RUNTIME'].{name:name,value:value}" -o table

# === 2: restart so the settings take effect ===
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 45
Write-Host "=== 2: restarted ==="

# === 3: publish with func (remote build). Capture ALL output; ignore the func 'No process' flake ===
Write-Host "=== 3 FUNC OUTPUT (also saved to funcpub.log) ==="
if (Test-Path funcpub.log) { Remove-Item funcpub.log -Force }
Push-Location function_app
cmd /c "func azure functionapp publish $app --python --build remote > ..\funcpub.log 2>&1"
Pop-Location
Get-Content funcpub.log
Write-Host "=== 3 END FUNC OUTPUT ==="

# === 4: wait for the server-side build to finish, then show the REAL deployment/build log ===
Start-Sleep -Seconds 120
Write-Host "=== 4 DEPLOYMENT LOG ==="
az webapp log deployment show -n $app -g $rg 2>&1

# === 5: restart + reset/reindex + capture result ===
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 60
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== 5 STATUS: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="
Write-Host "=== 5 FIRST ERRORS ==="
$st.lastResult.errors | Select-Object -First 2 | Format-List
$body = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body
Write-Host "=== 5 TOTAL DOCS IN INDEX: $($cnt.'@odata.count') ==="
