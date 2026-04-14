# One-shot deployment: infra -> function code -> search artifacts.
#
# Usage:
#   ./scripts/deploy.ps1 -Env dev
#   ./scripts/deploy.ps1 -Env prod -RunIndexer

param(
  [Parameter(Mandatory = $true)][ValidateSet('dev', 'staging', 'prod')][string]$Env,
  [switch]$RunIndexer,
  [string]$Location = 'eastus2'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Infra = Join-Path $RepoRoot 'infra'
$FuncDir = Join-Path $RepoRoot 'function_app'
$DeploymentName = "mm-manuals-$Env"

Write-Host "==> Deploying infra (env=$Env, location=$Location)"
az deployment sub create `
  --name $DeploymentName `
  --location $Location `
  --template-file (Join-Path $Infra 'main.bicep') `
  --parameters (Join-Path $Infra "parameters/$Env.bicepparam") `
  --output none

$FuncApp = az deployment sub show -n $DeploymentName --query properties.outputs.functionAppName.value -o tsv
$Rg = az deployment sub show -n $DeploymentName --query properties.outputs.resourceGroupName.value -o tsv

Write-Host "==> Publishing function app $FuncApp"
Push-Location $FuncDir
func azure functionapp publish $FuncApp --python
Pop-Location

Write-Host "==> Applying Azure AI Search artifacts"
$args = @('--env', $Env)
if ($RunIndexer) { $args += '--run-indexer' }
python (Join-Path $RepoRoot 'scripts/deploy_search.py') @args

Write-Host "==> Done. Function App: $FuncApp  RG: $Rg"
