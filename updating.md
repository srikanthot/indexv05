Hey Copilot — the "route mismatch" theory is wrong: I checked the code. The skillset calls
/api/process-document and function_app.py declares @app.route(route="process-document") (the
function is just *named* process_document_route). They match. A 404 on a matching route means the
FUNCTION HOST FAILED TO LOAD the functions -- i.e. a Python import error at startup, so no routes
register and every call 404s. This script pulls the host status + the real startup error, then
restarts and re-runs the indexer. Run in PowerShell. Paste me every "===" section.

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name; $rg = $cfg.functionApp.resourceGroup
$ep  = $cfg.search.endpoint.TrimEnd('/'); $idx = "$($cfg.search.artifactPrefix)-index"; $ixr = "$($cfg.search.artifactPrefix)-indexer"
$hostUrl = "https://$app.azurewebsites.us"
$mk = az functionapp keys list -g $rg -n $app --query masterKey -o tsv

# === 1: HOST STATUS -- did the function host load, or error out? ===
try {
  $s = Invoke-RestMethod -Uri "$hostUrl/admin/host/status" -Headers @{ 'x-functions-key' = $mk }
  Write-Host "=== 1 HOST STATE: $($s.state) ==="
  if ($s.errors) { Write-Host "=== 1 HOST ERRORS (this is the real cause) ==="; $s.errors | ForEach-Object { Write-Host $_ } }
} catch { Write-Host "=== 1 host status ERROR: $($_.Exception.Message) ===" }

# === 2: FUNCTIONS THE RUNNING HOST ACTUALLY LOADED (is process-document present?) ===
try {
  $fns = Invoke-RestMethod -Uri "$hostUrl/admin/functions" -Headers @{ 'x-functions-key' = $mk }
  Write-Host "=== 2 LOADED FUNCTIONS: $($fns.Count) ==="
  $fns | ForEach-Object { Write-Host (" - " + $_.name) }
} catch { Write-Host "=== 2 functions list ERROR: $($_.Exception.Message) ===" }

# === 3: call process-document directly -- what status does the host really return? ===
$body = '{"values":[{"recordId":"0","data":{"source_file":"t.pdf","source_path":"t.pdf"}}]}'
try {
  $r = Invoke-WebRequest -Uri "$hostUrl/api/process-document?code=$mk" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing
  Write-Host "=== 3 process-document HTTP $($r.StatusCode) ==="
  Write-Host ($r.Content.Substring(0,[Math]::Min(400,$r.Content.Length)))
} catch {
  $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 'n/a' }
  Write-Host "=== 3 process-document CALL FAILED: HTTP $code -- $($_.Exception.Message) ==="
}

# === 4: restart + wait, then reset/reindex + capture (in case it was just not warmed up) ===
az functionapp restart -g $rg -n $app | Out-Null
Start-Sleep -Seconds 90
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze --skip-heal-loop
Start-Sleep -Seconds 240
$tok = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
$st  = Invoke-RestMethod -Uri "$ep/indexers/$ixr/status?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok" }
Write-Host "=== 4 STATUS: $($st.lastResult.status)  processed=$($st.lastResult.itemsProcessed)  failed=$($st.lastResult.itemsFailed) ==="
$st.lastResult.errors | Select-Object -First 2 | Format-List
$body2 = '{"search":"*","count":true,"top":0}'
$cnt  = Invoke-RestMethod -Method Post -Uri "$ep/indexes/$idx/docs/search?api-version=2024-05-01-preview" -Headers @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" } -Body $body2
Write-Host "=== 4 TOTAL DOCS IN INDEX: $($cnt.'@odata.count') ==="
