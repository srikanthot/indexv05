Hey Copilot — the deploy got through RBAC fine, but the "Deploy Function App code" step
failed with: `Error Uploading archive... (ServiceUnavailable)`. That's a transient 503,
almost certainly because the function app was mid-restart (bootstrap had just changed app
settings) when the code upload hit it. Please just fix THIS step — restart the app, wait for
it to settle, and publish the code directly. Do NOT run deploy.py or preanalyze; I'll run
the rest myself once the code is published.

Run these in PowerShell, in order. Stop and tell me the result of the publish.

# 1) Read the app name + resource group from config (nothing to fill in)
$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json
$app = $cfg.functionApp.name
$rg  = $cfg.functionApp.resourceGroup
Write-Host "App: $app   RG: $rg"

# 2) Restart the function app and let it settle (this is what fixes the 503)
az functionapp restart -g $rg -n $app
Start-Sleep -Seconds 60

# 3) Confirm the app is running before we upload
az functionapp show -g $rg -n $app --query state -o tsv
# ^ must print "Running". If not, wait another 60s and re-check before continuing.

# 4) Publish the function code DIRECTLY (bypasses the fragile deploy_function.ps1 wrapper,
#    and lets func do its own upload retries). Runs from the function_app folder where
#    host.json lives.
Push-Location function_app
func azure functionapp publish $app --python --build remote
Pop-Location

# Expected: it ends by listing the deployed functions (should be 8). Tell me that it
# succeeded and how many functions registered.
#
# If it fails AGAIN with the same "ServiceUnavailable" 503: wait ~2-3 minutes (Gov
# transient) and re-run ONLY step 4 — `func ... publish` is safe to repeat. If it keeps
# failing after 2-3 tries, paste me the full error and stop.
