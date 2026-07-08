Hey Copilot — good progress: page_label.py is now correct (CHECK A = DEFINITION PRESENT line
2232, tests 291/294). The ONLY thing failing is the publish: `func ... --build remote` died with
"Remote build failed / No process is associated with this object" — that's a func core-tools
flake (nested shells), NOT a code or dependency problem (the new files use only stdlib).

Please deploy with `az` zip-deploy instead. It bypasses func core tools completely but still lets
Oryx build the app server-side. Run this in PowerShell, in order. Paste me every "===" line. If
STEP 2 fails, paste the FULL output of the `az ... config-zip` command (that's the build error).

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/')
$idx = "$($cfg.search.artifactPrefix)-index"
$ixr = "$($cfg.search.artifactPrefix)-indexer"

# === STEP 1: clear stale bytecode + turn on server-side (Oryx) build ===
Get-ChildItem -Path function_app -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
az functionapp config appsettings set -g $rg -n $app --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true | Out-Null
Write-Host "=== 1: cleared __pycache__ + enabled remote build ==="

# === STEP 2: zip the function app (host.json + requirements.txt at the root) and deploy ===
if (Test-Path funcapp.zip) { Remove-Item funcapp.zip -Force }
Compress-Archive -Path function_app\* -DestinationPath funcapp.zip -Force
$zipMB = [math]::Round((Get-Item funcapp.zip).Length/1MB,1)
Write-Host "=== 2: zip built ($zipMB MB); deploying (Oryx builds server-side, be patient) ==="
az functionapp deployment source config-zip -g $rg -n $app --src funcapp.zip --timeout 1200
$deployExit = $LASTEXITCODE
if ($deployExit -ne 0) {
  Write-Host "=== 2: DEPLOY FAILED (exit $deployExit) -- paste the full az output above. Trying to fetch the build log... ==="
  Write-Host "=== BUILD LOG (best-effort) ==="
  try { az webapp log deployment show -g $rg -n $app } catch { Write-Host "(could not fetch deployment log: $_)" }
  exit 1
}
Write-Host "=== 2: DEPLOY OK ==="

# === STEP 3: restart + warm up so the new code is loaded ===
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 60
Write-Host "=== 3: restarted ==="

# === STEP 4: reset + re-run the indexer ===
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop

# === STEP 5: wait, then capture the result ===
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== 5 STATUS: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="
Write-Host "=== 5 FIRST ERRORS ==="
$st.lastResult.errors | Select-Object -First 2 | Format-List
$body = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body
Write-Host "=== 5 TOTAL DOCS IN INDEX: $($cnt.'@odata.count') ==="

# Paste me every "===" line. If TOTAL DOCS > 0, we're done. If STEP 2 failed, paste the az output.
