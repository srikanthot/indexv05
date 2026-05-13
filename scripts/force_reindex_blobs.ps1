# Auto-detect stuck PDFs in the index and force-retry them.
#
# This script does NOT require any hardcoded PDF list. It does the work
# itself:
#   1. Queries the index to find source_files WITHOUT a `summary` record (= stuck)
#   2. Lists all PDF blobs in the source container
#   3. Diffs the two lists -- stuck PDFs are those in the container but
#      with no summary record in the index yet
#   4. Bumps each stuck blob's metadata (forces lastModified to advance)
#   5. Calls indexer's resetdocs API to clear failed-items state
#   6. Triggers an immediate indexer run
#
# Same logic as the in-app auto_heal_timer, but operator-triggered for
# when you don't want to wait for the timer to fire.
#
# Usage:
#   .\scripts\force_reindex_blobs.ps1 deploy.config.json
#
# Optional: --max <N> caps how many blobs to bump per run (default 20)
#           --include-recent  also bumps blobs uploaded < 30 min ago
#                             (default behavior skips them -- they're likely
#                              still in mid-processing)

param(
  [string]$Config = 'deploy.config.json',
  [int]$Max = 20,
  [switch]$IncludeRecent
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Config)) { throw "Config file not found: $Config" }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json

$SearchEp = $cfg.search.endpoint.TrimEnd('/')
$Prefix = $cfg.search.artifactPrefix
$IndexerName = "$Prefix-indexer"
$IndexName = "$Prefix-index"
$StorageAcct = ($cfg.storage.accountResourceId -split '/')[-1]
$Container = $cfg.storage.pdfContainerName

# Detect storage endpoint suffix (Gov vs Public Azure)
$StorageEndpoint = if ($SearchEp -match '\.azure\.us') {
  'blob.core.usgovcloudapi.net'
} else {
  'blob.core.windows.net'
}

# Step 0: Get AAD tokens
Write-Host "==> Step 0: acquiring AAD tokens"
$SearchToken = az account get-access-token --resource https://search.azure.us --query accessToken -o tsv
if (-not $SearchToken) { throw "Failed to acquire AAD token for Search service" }

# Step 1: Query index for source_files that already have a summary record
Write-Host "==> Step 1: querying index for already-done PDFs"
$SearchUrl = "$SearchEp/indexes/$IndexName/docs/search?api-version=2024-05-01-preview"
$Body = @{
  search = "*"
  filter = "record_type eq 'summary'"
  select = "source_file"
  top = 1000
} | ConvertTo-Json

try {
  $Response = Invoke-RestMethod -Uri $SearchUrl -Method POST `
    -Headers @{ "Authorization" = "Bearer $SearchToken"; "Content-Type" = "application/json" } `
    -Body $Body
} catch {
  throw "Failed to query index: $($_.Exception.Message)"
}
$DonePdfs = @($Response.value | ForEach-Object { $_.source_file } | Where-Object { $_ })
Write-Host "  index has summary records for $($DonePdfs.Count) PDFs"

# Step 2: List PDFs in the storage container
Write-Host "==> Step 2: listing PDFs in container $Container"
$BlobJson = az storage blob list `
  --account-name $StorageAcct `
  --container-name $Container `
  --auth-mode login `
  --query "[?ends_with(name, '.pdf')].{name:name, modified:properties.lastModified}" `
  -o json
$Blobs = $BlobJson | ConvertFrom-Json
Write-Host "  container has $($Blobs.Count) PDFs"

if ($Blobs.Count -eq 0) {
  Write-Host "ERROR: container is empty or you lack permission" -ForegroundColor Red
  exit 1
}

# Step 3: Find stuck PDFs = in container but no summary record
$Now = Get-Date
$RecentCutoff = $Now.AddMinutes(-30)

$Stuck = @($Blobs | Where-Object {
  $name = $_.name
  $modified = [DateTime]$_.modified
  ($name -notin $DonePdfs) -and ($IncludeRecent -or ($modified -lt $RecentCutoff))
})

if ($Stuck.Count -eq 0) {
  Write-Host ""
  Write-Host "==> No stuck PDFs found." -ForegroundColor Green
  Write-Host "    All $($Blobs.Count) PDFs have a summary record in the index."
  Write-Host "    (Or any newly-uploaded blobs are still within their 30-min grace window."
  Write-Host "     Use --IncludeRecent to also retry those.)"
  exit 0
}

# Cap at $Max to prevent runaway
$Stuck = $Stuck | Select-Object -First $Max
Write-Host ""
Write-Host "==> Found $($Stuck.Count) stuck PDF(s) to heal:" -ForegroundColor Yellow
$Stuck | ForEach-Object {
  Write-Host "  - $($_.name)  (last_modified=$($_.modified))" -ForegroundColor Yellow
}

# Step 4: Bump metadata on each stuck blob
Write-Host ""
Write-Host "==> Step 3: bumping lastModified on $($Stuck.Count) blobs"
$Stamp = Get-Date -Format 'yyyyMMddHHmmss'
$Updated = @()
$Skipped = @()
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
foreach ($s in $Stuck) {
  $name = $s.name
  Write-Host "  - $name" -NoNewline
  $null = az storage blob metadata update `
    --account-name $StorageAcct `
    --container-name $Container `
    --name $name `
    --metadata "force_reindex=$Stamp" `
    --auth-mode login 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host "  -> updated" -ForegroundColor Green
    $Updated += $name
  } else {
    Write-Host "  -> FAILED" -ForegroundColor Yellow
    $Skipped += $name
  }
}
$ErrorActionPreference = $prevEAP

if ($Updated.Count -eq 0) {
  Write-Host ""
  Write-Host "ERROR: no blobs were updated. Aborting." -ForegroundColor Red
  exit 1
}

# Step 5: Reset failed-items state for updated blobs
Write-Host ""
Write-Host "==> Step 4: clearing failed-items list for updated blobs"
$BlobUrls = $Updated | ForEach-Object {
  "https://${StorageAcct}.${StorageEndpoint}/${Container}/${_}"
}
$ResetUrl = "$SearchEp/indexers/$IndexerName/resetdocs?api-version=2024-05-01-preview"
$ResetBody = @{ datasourceDocumentIds = $BlobUrls } | ConvertTo-Json -Depth 3

try {
  Invoke-RestMethod -Uri $ResetUrl -Method POST `
    -Headers @{ "Authorization" = "Bearer $SearchToken"; "Content-Type" = "application/json" } `
    -Body $ResetBody | Out-Null
  Write-Host "  -> reset accepted" -ForegroundColor Green
} catch {
  Write-Host "  -> reset failed: $($_.Exception.Message)" -ForegroundColor Yellow
  Write-Host "     Continuing -- metadata bump alone usually triggers reprocessing"
}

# Step 6: Trigger indexer run immediately
Write-Host ""
Write-Host "==> Step 5: triggering indexer run"
$RunUrl = "$SearchEp/indexers/$IndexerName/run?api-version=2024-05-01-preview"
try {
  Invoke-RestMethod -Uri $RunUrl -Method POST `
    -Headers @{ "Authorization" = "Bearer $SearchToken"; "Content-Length" = "0" } | Out-Null
  Write-Host "  -> run triggered" -ForegroundColor Green
} catch {
  if ($_.Exception.Response.StatusCode -eq 409) {
    Write-Host "  -> indexer already running -- blobs will be picked up" -ForegroundColor Green
  } else {
    Write-Host "  -> run trigger failed: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

# Summary
Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor Cyan
Write-Host "  Updated: $($Updated.Count) blob(s)"
if ($Skipped.Count -gt 0) {
  Write-Host "  Skipped: $($Skipped.Count) blob(s) (storage failed)" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Each large PDF (50-95 MB) takes 5-15 min through the full pipeline."
Write-Host "Expected: all healed PDFs done in 1-3 hours."
Write-Host ""
Write-Host "Monitor with:"
Write-Host "  python scripts/check_index.py --config $Config --coverage"
