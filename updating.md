Hey Copilot — one comprehensive diagnostic. It gathers everything at once so we can finalize.
It does NOT change anything and does NOT reindex — it just collects facts. Run it in PowerShell.
Do NOT set $ErrorActionPreference='Stop'. Paste me the FULL output of every "=== X ===" section.

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$hostUrl = "https://$app.azurewebsites.us"
Write-Host "app=$app rg=$rg host=$hostUrl"
$mk = az functionapp keys list -g $rg -n $app --query masterKey -o tsv 2>$null

# === A: KEY APP SETTINGS (build + run-from-package + provider) ===
try {
  az functionapp config appsettings list -g $rg -n $app --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'||name=='SCM_DO_BUILD_DURING_DEPLOYMENT'||name=='ENABLE_ORYX_BUILD'||name=='FUNCTIONS_WORKER_RUNTIME'||name=='FUNCTIONS_EXTENSION_VERSION'||name=='MODEL_PROVIDER'||name=='AOAI_ENDPOINT'||name=='AOAI_EMBED_DEPLOYMENT'||name=='FOUNDRY_PROJECT_ENDPOINT'||name=='FOUNDRY_API_VERSION'||name=='FOUNDRY_CHAT_MODEL'].{name:name,value:value}" -o table
} catch { Write-Host "A error: $($_.Exception.Message)" }

# === B: HOST STATUS + STARTUP ERRORS (the real cause of the 404) ===
try {
  $s = Invoke-RestMethod -Uri "$hostUrl/admin/host/status" -Headers @{ 'x-functions-key' = $mk } -TimeoutSec 60
  Write-Host "HOST STATE: $($s.state)"
  Write-Host "HOST VERSION: $($s.version)"
  if ($s.errors) { Write-Host "HOST ERRORS:"; $s.errors | ForEach-Object { Write-Host "  $_" } } else { Write-Host "HOST ERRORS: (none reported)" }
} catch { Write-Host "B error: $($_.Exception.Message)" }

# === C: FUNCTIONS THE RUNNING HOST LOADED (0 = load failed) ===
try {
  $fns = Invoke-RestMethod -Uri "$hostUrl/admin/functions" -Headers @{ 'x-functions-key' = $mk } -TimeoutSec 60
  Write-Host "LOADED FUNCTION COUNT: $($fns.Count)"
  $fns | ForEach-Object { Write-Host ("  - " + $_.name) }
} catch { Write-Host "C error: $($_.Exception.Message)" }

# === D: DIRECT CALL to process-document (actual HTTP status + body) ===
try {
  $body = '{"values":[{"recordId":"0","data":{"source_file":"t.pdf","source_path":"t.pdf"}}]}'
  $r = Invoke-WebRequest -Uri "$hostUrl/api/process-document?code=$mk" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 120
  Write-Host "process-document HTTP $($r.StatusCode)"
  Write-Host ("BODY: " + $r.Content.Substring(0,[Math]::Min(600,$r.Content.Length)))
} catch {
  $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 'n/a' }
  Write-Host "process-document CALL FAILED: HTTP $code -- $($_.Exception.Message)"
}

# === E: LAST DEPLOYMENT / ORYX BUILD LOG (did pip install run?) ===
try { az webapp log deployment show -g $rg -n $app 2>&1 | Select-Object -Last 60 | ForEach-Object { Write-Host $_ } }
catch { Write-Host "E error: $($_.Exception.Message)" }

# === F: are the installed python packages present on the app? (deps check) ===
try {
  $prof = [xml](az webapp deployment list-publishing-profiles -g $rg -n $app --xml 2>$null)
  $pp = $prof.publishData.publishProfile | Where-Object { $_.publishMethod -eq 'MSDeploy' } | Select-Object -First 1
  $pair = "$($pp.userName):$($pp.userPWD)"
  $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
  $scm = "https://$app.scm.azurewebsites.us"
  Write-Host "wwwroot listing:"
  (Invoke-RestMethod -Uri "$scm/api/vfs/site/wwwroot/" -Headers @{ Authorization = "Basic $b64" } -TimeoutSec 60) | Select-Object name | ForEach-Object { Write-Host ("  " + $_.name) }
  Write-Host "shared/ listing:"
  (Invoke-RestMethod -Uri "$scm/api/vfs/site/wwwroot/shared/" -Headers @{ Authorization = "Basic $b64" } -TimeoutSec 60) | Select-Object name | Where-Object { $_.name -match 'content_classifiers|procedures|prompt_safety|aoai|page_label' } | ForEach-Object { Write-Host ("  " + $_.name) }
} catch { Write-Host "F error (SCM basic auth may be disabled -- that's OK, skip): $($_.Exception.Message)" }

Write-Host "=== DONE -- paste sections A through F ==="
