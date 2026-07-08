param(
  [string]$Config = 'deploy.config.json'
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Config)) { throw "Config file not found: $Config" }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json

$FuncApp = $cfg.functionApp.name
$Rg = $cfg.functionApp.resourceGroup
if (-not $FuncApp -or -not $Rg) { throw 'functionApp.name and functionApp.resourceGroup must be set' }

$RepoRoot = Split-Path -Parent $PSScriptRoot
$funcDir  = Join-Path $RepoRoot 'function_app'

Write-Host "==> Publishing function code to $FuncApp"

# IMPORTANT: run func through cmd.exe, NOT `& func ... 2>&1`.
# Under PowerShell 5.1, redirecting a native exe's stderr with `2>&1` while
# $ErrorActionPreference='Stop' wraps EVERY stderr line as a terminating
# ErrorRecord (NativeCommandError). func writes progress/warnings to stderr,
# so a benign line -- or a transient 504 -- became
# "No process is associated with this object" and aborted a build that was
# actually fine. cmd.exe redirects stderr to stdout as PLAIN TEXT, so
# PowerShell just captures strings and we judge success by exit code + output.
$published = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
  Write-Host "==> publish attempt $attempt/3"
  Push-Location $funcDir
  $out  = cmd /c "func azure functionapp publish $FuncApp --python --build remote 2>&1"
  $code = $LASTEXITCODE
  Pop-Location
  $out | ForEach-Object { Write-Host $_ }
  $text = ($out -join "`n")

  $failed = ($code -ne 0) -or ($text -match 'Remote build failed|Deployment failed|Error Uploading archive|ServiceUnavailable|GatewayTimeout')
  if (-not $failed) { $published = $true; break }

  if ($attempt -lt 3) {
    Write-Host "==> attempt $attempt failed (transient?); restarting app + waiting 45s, then retry..." -ForegroundColor Yellow
    az functionapp restart -g $Rg -n $FuncApp | Out-Null
    Start-Sleep -Seconds 45
  }
}

if (-not $published) {
  Write-Host ""
  Write-Host "==> ABORT: function publish FAILED after 3 attempts" -ForegroundColor Red
  Write-Host "==> Oryx build/deployment log (the real reason):" -ForegroundColor Yellow
  try { az webapp log deployment show -g $Rg -n $FuncApp 2>&1 | ForEach-Object { Write-Host $_ } }
  catch { Write-Host "(could not fetch deployment log: $_)" }
  Write-Host ""
  Write-Host "NOT continuing with App Settings because the new code isn't live." -ForegroundColor Red
  throw "func azure functionapp publish failed (see the build log above)."
}

Write-Host "==> Applying App Settings"

# Provider-aware. In foundry mode the function app uses FOUNDRY_* for chat/vision
# and the AOAI_* fields address the Foundry OpenAI-compatible endpoint for
# Search embeddings. Mirrors bootstrap.py so this script is correct standalone.
$provider     = $cfg.modelProvider;            if (-not $provider)     { $provider = 'aoai' }
$aoaiApi      = $cfg.azureOpenAI.apiVersion;   if (-not $aoaiApi)      { $aoaiApi = '2024-12-01-preview' }
$foundryApi   = $cfg.foundry.apiVersion;       if (-not $foundryApi)   { $foundryApi = '2024-10-21' }
$diApi        = $cfg.documentIntelligence.apiVersion; if (-not $diApi)  { $diApi = '2024-11-30' }
$prefix       = $cfg.search.artifactPrefix;    if (-not $prefix)       { $prefix = 'mm-manuals' }
$skillVersion = $cfg.skillVersion;             if (-not $skillVersion) { $skillVersion = '1.0.0' }
$storageAcctName = ($cfg.storage.accountResourceId -split '/')[-1]

$settings = @(
  "AUTH_MODE=mi",
  "MODEL_PROVIDER=$provider",
  "AOAI_ENDPOINT=$($cfg.azureOpenAI.endpoint)",
  "AOAI_API_VERSION=$aoaiApi",
  "AOAI_CHAT_DEPLOYMENT=$($cfg.azureOpenAI.chatDeployment)",
  "AOAI_VISION_DEPLOYMENT=$($cfg.azureOpenAI.visionDeployment)",
  "AOAI_EMBED_DEPLOYMENT=$($cfg.azureOpenAI.embedDeployment)",
  "FOUNDRY_PROJECT_ENDPOINT=$($cfg.foundry.projectEndpoint)",
  "FOUNDRY_API_VERSION=$foundryApi",
  "FOUNDRY_CHAT_MODEL=$($cfg.foundry.chatModel)",
  "FOUNDRY_EMBED_MODEL=$($cfg.foundry.embedModel)",
  "DI_ENDPOINT=$($cfg.documentIntelligence.endpoint)",
  "DI_API_VERSION=$diApi",
  "SEARCH_ENDPOINT=$($cfg.search.endpoint)",
  "SEARCH_INDEX_NAME=$prefix-index",
  "SEARCH_INDEXER_NAME=$prefix-indexer",
  "STORAGE_ACCOUNT_NAME=$storageAcctName",
  "STORAGE_CONTAINER_NAME=$($cfg.storage.pdfContainerName)",
  "AUTO_HEAL_ENABLED=true",
  "AUTO_HEAL_STUCK_AFTER_MIN=60",
  "AUTO_HEAL_MAX_BLOBS_PER_RUN=20",
  "SKILL_VERSION=$skillVersion",
  # Python worker concurrency (see bootstrap.py for rationale).
  "FUNCTIONS_WORKER_PROCESS_COUNT=4",
  "PYTHON_THREADPOOL_THREAD_COUNT=16",
  # Ensure server-side build stays enabled (config-zip flips these off).
  "SCM_DO_BUILD_DURING_DEPLOYMENT=true",
  "ENABLE_ORYX_BUILD=true"
)
if ($cfg.appInsights.connectionString) {
  $settings += "APPLICATIONINSIGHTS_CONNECTION_STRING=$($cfg.appInsights.connectionString)"
}

az functionapp config appsettings set -g $Rg -n $FuncApp --settings $settings --output none
# WEBSITE_RUN_FROM_PACKAGE would override remote build -- make sure it's gone.
az functionapp config appsettings delete -g $Rg -n $FuncApp --setting-names WEBSITE_RUN_FROM_PACKAGE 2>$null | Out-Null

Write-Host "==> Function App $FuncApp ready"
