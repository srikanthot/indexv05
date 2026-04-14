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

Write-Host "==> Publishing function code to $FuncApp"
Push-Location (Join-Path $RepoRoot 'function_app')
func azure functionapp publish $FuncApp --python
Pop-Location

Write-Host "==> Applying App Settings"

$apiVersion = $cfg.azureOpenAI.apiVersion; if (-not $apiVersion) { $apiVersion = '2024-12-01-preview' }
$diApi = $cfg.documentIntelligence.apiVersion; if (-not $diApi) { $diApi = '2024-11-30' }
$prefix = $cfg.search.artifactPrefix; if (-not $prefix) { $prefix = 'mm-manuals' }
$skillVersion = $cfg.skillVersion; if (-not $skillVersion) { $skillVersion = '1.0.0' }

$settings = @(
  "AUTH_MODE=mi",
  "AOAI_ENDPOINT=$($cfg.azureOpenAI.endpoint)",
  "AOAI_API_VERSION=$apiVersion",
  "AOAI_CHAT_DEPLOYMENT=$($cfg.azureOpenAI.chatDeployment)",
  "AOAI_VISION_DEPLOYMENT=$($cfg.azureOpenAI.visionDeployment)",
  "DI_ENDPOINT=$($cfg.documentIntelligence.endpoint)",
  "DI_API_VERSION=$diApi",
  "SEARCH_ENDPOINT=$($cfg.search.endpoint)",
  "SEARCH_INDEX_NAME=$prefix-index",
  "SKILL_VERSION=$skillVersion"
)
if ($cfg.appInsights.connectionString) {
  $settings += "APPLICATIONINSIGHTS_CONNECTION_STRING=$($cfg.appInsights.connectionString)"
}

az functionapp config appsettings set -g $Rg -n $FuncApp --settings $settings --output none

Write-Host "==> Function App $FuncApp ready"
