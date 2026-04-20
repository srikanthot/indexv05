# Auto-run pre-analysis for all PDFs in the configured container.
#
# Behavior:
#   - Lists every PDF (case-insensitive .pdf / .PDF).
#   - Skips PDFs that already have a cached final output.json.
#   - Processes only the rest. Per-figure caching means a killed
#     run resumes from where it stopped -- just run this again.
#   - Retries transient errors automatically (up to 3 attempts per
#     figure, tracked in blob cache). Permanent failures like
#     content-filter blocks are recorded and never retried.
#
# Usage:
#   ./scripts/run_preanalyze.ps1                      # defaults
#   ./scripts/run_preanalyze.ps1 -VisionParallel 48   # more throughput
#   ./scripts/run_preanalyze.ps1 -Concurrency 2       # 2 PDFs at once
#   ./scripts/run_preanalyze.ps1 -MaxAttempts 5       # wrapper-level sweeps
#
# The wrapper loops up to -MaxAttempts times. Each pass is a full
# incremental run. If the first pass leaves figures in a transient
# error state, the next pass retries them. Once no PDFs remain to
# process, the wrapper exits.

param(
    [string]$Config = "deploy.config.json",
    [int]$VisionParallel = 40,
    [int]$Concurrency = 2,
    [int]$MaxAttempts = 3
)

# NOTE: we intentionally do NOT set $ErrorActionPreference = "Stop" here.
# When Python writes a traceback to stderr, PowerShell turns that into a
# NativeCommandError and with Stop mode would kill the wrapper, hiding
# the actual traceback. Leaving it default lets the traceback print.

if (-not (Test-Path $Config)) {
    Write-Host "Config file not found: $Config" -ForegroundColor Red
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Pre-analyze auto-runner" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Config:          $Config"
Write-Host "  Vision parallel: $VisionParallel"
Write-Host "  PDFs in parallel:$Concurrency"
Write-Host "  Max sweep passes:$MaxAttempts"
Write-Host ""

$env:PYTHONUNBUFFERED = "1"  # force Python to flush stdout line-by-line

# Preflight: verify environment before wasting hours on a bad setup.
& python scripts/preflight.py --config $Config
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Preflight failed. Fix the issues above before running preanalyze." -ForegroundColor Red
    exit $LASTEXITCODE
}

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-Host ""
    Write-Host "--- Pass $attempt of $MaxAttempts ---" -ForegroundColor Yellow

    # Tee to both console (live) and a temp file (so we can parse the summary).
    $logFile = [System.IO.Path]::GetTempFileName()
    try {
        & python scripts/preanalyze.py `
            --config $Config `
            --incremental `
            --vision-parallel $VisionParallel `
            --concurrency $Concurrency 2>&1 |
            Tee-Object -FilePath $logFile

        $pyExit = $LASTEXITCODE
        $output = Get-Content $logFile
    } finally {
        Remove-Item $logFile -ErrorAction SilentlyContinue
    }

    if ($pyExit -ne 0) {
        Write-Host ""
        Write-Host "Python exited with code $pyExit. See the traceback above." -ForegroundColor Red
        Write-Host "Already-processed work is cached and safe. Fix the issue and re-run." -ForegroundColor Red
        exit $pyExit
    }

    # Parse the 'Done: X processed, Y skipped, Z failed' line. We need Z.
    # The 'to process' count from earlier reflects what the pass STARTED with,
    # not what is left AFTER the pass succeeded, so we do not use it for exit logic.
    $doneLine = $output | Select-String -Pattern "Done: \d+ processed, \d+ skipped, (\d+) failed"
    if ($doneLine) {
        $failed = [int]$doneLine.Matches[0].Groups[1].Value
    } else {
        # 'Nothing to process.' path - everything is cached, we are done.
        $failed = 0
    }

    Write-Host ""
    if ($failed -eq 0) {
        Write-Host "Pass $attempt complete with no failures. Done." -ForegroundColor Green
        exit 0
    }

    Write-Host "$failed PDFs failed in pass $attempt." -ForegroundColor Yellow

    if ($attempt -lt $MaxAttempts) {
        Write-Host "Retrying..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}

Write-Host ""
Write-Host "Reached max passes ($MaxAttempts) with failures remaining." -ForegroundColor Red
Write-Host "Run again to continue, or check what is left with:" -ForegroundColor Red
Write-Host "  python scripts/preanalyze.py --config $Config --status" -ForegroundColor Red
exit 1
