Hi — I'm debugging why our Azure AI Search indexing pipeline fails at ~10–12 documents (works for 1–2). Before we change any code, I need you to gather diagnostics from Azure. You have Azure CLI access and we're already logged in.

Please do exactly this:

Run the PowerShell script below exactly as-is, from the repo root. It is strictly read-only — it only reads config and queries Azure. Do NOT modify any Azure resource, app setting, index, or file. Do NOT change any code.
It reads deploy.config.json to auto-discover all resource names, so nothing needs to be filled in.
It saves everything to index_diag_output.txt. When it finishes, paste the full console output back to me (and confirm the file was created). If any section errors out, that's fine — let it continue and include the errors in what you return.

# ============================================================
#  READ-ONLY indexing diagnostics. Makes NO changes to anything.
#  Reads deploy.config.json, checks Function App, model, Search, storage.
#  Run from the indexing repo root. Requires: az CLI already logged in.
# ============================================================
$ErrorActionPreference = "Continue"
$out = "index_diag_output.txt"
Start-Transcript -Path $out -Force | Out-Null
function Sec($t){ Write-Host "`n========== $t ==========" }

# ---- 0. Load config ----
$cfgPath = "deploy.config.json"
if (-not (Test-Path $cfgPath)) { Write-Host "ERROR: deploy.config.json not found in $(Get-Location). Run from the repo root."; Stop-Transcript; return }
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json

$faName   = $cfg.functionApp.name
$faRg     = $cfg.functionApp.resourceGroup
$aoaiEp   = ($cfg.azureOpenAI.endpoint).TrimEnd('/')
$chatDep  = $cfg.azureOpenAI.chatDeployment
$visDep   = $cfg.azureOpenAI.visionDeployment
$apiVer   = $cfg.azureOpenAI.apiVersion
$searchEp = ($cfg.search.endpoint).TrimEnd('/')
$prefix   = $cfg.search.artifactPrefix
$indexer  = "$prefix-indexer"; $indexName = "$prefix-index"; $skillset = "$prefix-skillset"
$saName   = ($cfg.storage.accountResourceId -split '/')[-1]
$container = $cfg.storage.pdfContainerName
$isGov = $searchEp -like "*.azure.us*" -or $aoaiEp -like "*.azure.us*"

Sec "0. CONFIG (derived)"
Write-Host "FunctionApp=$faName  RG=$faRg"
Write-Host "AOAI endpoint=$aoaiEp  chat=$chatDep  vision=$visDep  apiVer=$apiVer"
Write-Host "Search endpoint=$searchEp  prefix=$prefix"
Write-Host "Storage=$saName  container=$container  cloud=$(if($isGov){'GOV'}else{'COMMERCIAL'})"
Write-Host "AOAI endpoint shape: $(if($aoaiEp -like '*services.ai.azure*' -or $aoaiEp -like '*api/projects*'){'!!! FOUNDRY PROJECT URL - WILL BREAK EMBEDDINGS/CLIENT'}else{'classic (ok)'})"

# ---- 1. Function App ----
Sec "1. FUNCTION APP state / plan / python"
az functionapp show -n $faName -g $faRg --query "{state:state, defaultHostName:defaultHostName, appServicePlanId:appServicePlanId}" -o json
$planId = az functionapp show -n $faName -g $faRg --query appServicePlanId -o tsv
if ($planId) { az appservice plan show --ids $planId --query "{tier:sku.tier, size:sku.name, workers:sku.capacity}" -o json }
Write-Host "linuxFxVersion (python ver):"; az functionapp config show -n $faName -g $faRg --query linuxFxVersion -o tsv

Sec "1b. REGISTERED FUNCTIONS (expect 6: analyze-diagram, build-doc-summary, build-semantic-string, extract-page-label, process-document, shape-table)"
az functionapp function list -n $faName -g $faRg --query "[].name" -o tsv

# ---- 2. App settings (filtered; no secrets printed) ----
Sec "2. APP SETTINGS (model/auth/heal/runtime)"
az functionapp config appsettings list -n $faName -g $faRg --query "[?contains(name,'AOAI')||contains(name,'FOUNDRY')||contains(name,'MODEL_PROVIDER')||contains(name,'AUTO_HEAL')||contains(name,'DI_')||contains(name,'SEARCH_')||name=='FUNCTIONS_EXTENSION_VERSION'||name=='FUNCTIONS_WORKER_RUNTIME'||name=='WEBSITE_RUN_FROM_PACKAGE'||name=='FUNCTIONS_WORKER_PROCESS_COUNT'].{name:name,value:value}" -o table

# ---- 3. MODEL TEST: temperature=0 vs none (the decisive GPT-5.1 test) ----
Sec "3. GPT-5.1 TEMPERATURE TEST"
$aoaiRes = if ($isGov) { "https://cognitiveservices.azure.us" } else { "https://cognitiveservices.azure.com" }
$aoaiTok = az account get-access-token --resource $aoaiRes --query accessToken -o tsv
$mUri = "$aoaiEp/openai/deployments/$chatDep/chat/completions?api-version=$apiVer"
$mHdr = @{ Authorization = "Bearer $aoaiTok"; "Content-Type" = "application/json" }
Write-Host "--- TEST A: WITH temperature=0 (what the code sends now) ---"
$bA = @{ messages=@(@{role="user";content="say hi"}); temperature=0; max_completion_tokens=40 } | ConvertTo-Json -Depth 6
try { $null = Invoke-RestMethod -Uri $mUri -Method Post -Headers $mHdr -Body $bA; Write-Host "RESULT A: PASSED (temperature=0 accepted)" }
catch { Write-Host "RESULT A: FAILED status=$($_.Exception.Response.StatusCode.value__)"; Write-Host $_.ErrorDetails.Message }
Write-Host "--- TEST B: WITHOUT temperature (proposed fix) ---"
$bB = @{ messages=@(@{role="user";content="say hi"}); max_completion_tokens=40 } | ConvertTo-Json -Depth 6
try { $null = Invoke-RestMethod -Uri $mUri -Method Post -Headers $mHdr -Body $bB; Write-Host "RESULT B: PASSED (works without temperature)" }
catch { Write-Host "RESULT B: FAILED status=$($_.Exception.Response.StatusCode.value__)"; Write-Host $_.ErrorDetails.Message }

# ---- Search admin key (auto-discover RG) ----
$searchName = ([uri]$searchEp).Host.Split('.')[0]
$searchRg = az resource list --name $searchName --resource-type Microsoft.Search/searchServices --query "[0].resourceGroup" -o tsv
$searchKey = az search admin-key show --service-name $searchName -g $searchRg --query primaryKey -o tsv 2>$null
$sHdr = @{ "api-key" = $searchKey; "Content-Type" = "application/json" }
$sv = "2024-05-01-preview"

# ---- 4. Indexer status + errors ----
Sec "4. INDEXER STATUS + ERRORS/WARNINGS"
try {
  $st = Invoke-RestMethod -Uri "$searchEp/indexers/$indexer/status?api-version=$sv" -Headers $sHdr
  Write-Host "status=$($st.status)  lastResult.status=$($st.lastResult.status)"
  Write-Host "itemsProcessed=$($st.lastResult.itemsProcessed)  itemsFailed=$($st.lastResult.itemsFailed)"
  Write-Host "--- errors (first 10) ---";   $st.lastResult.errors   | Select-Object -First 10 | ForEach-Object { Write-Host "ERR : $($_.errorMessage)" }
  Write-Host "--- warnings (first 10) ---"; $st.lastResult.warnings | Select-Object -First 10 | ForEach-Object { Write-Host "WARN: $($_.message)" }
} catch { Write-Host "indexer status failed: $($_.Exception.Message)  body:$($_.ErrorDetails.Message)" }

# ---- 5. Skillset skill URIs (real host or placeholder?) ----
Sec "5. SKILLSET WEBAPI URIS (must be real host, not <FUNCTION_APP_HOST>)"
try {
  $sk = Invoke-RestMethod -Uri "$searchEp/skillsets/$skillset`?api-version=$sv" -Headers $sHdr
  $sk.skills | Where-Object { $_.uri } | ForEach-Object { Write-Host "$($_.name) -> $(($_.uri -split '\?')[0])" }
} catch { Write-Host "skillset read failed: $($_.Exception.Message)" }

# ---- 6. Index fields + doc counts by record_type ----
Sec "6. INDEX FIELDS (which schema fields exist)"
try {
  $ix = Invoke-RestMethod -Uri "$searchEp/indexes/$indexName`?api-version=$sv" -Headers $sHdr
  ($ix.fields | ForEach-Object { $_.name }) -join ", "
} catch { Write-Host "index read failed: $($_.Exception.Message)" }
Sec "6b. DOC COUNTS by record_type"
try {
  $body = @{ search="*"; top=0; count=$true; facets=@("record_type,count:20") } | ConvertTo-Json
  $r = Invoke-RestMethod -Uri "$searchEp/indexes/$indexName/docs/search?api-version=$sv" -Method Post -Headers $sHdr -Body $body
  Write-Host "TOTAL docs: $($r.'@odata.count')"
  $r.'@search.facets'.record_type | ForEach-Object { Write-Host "  $($_.value): $($_.count)" }
} catch { Write-Host "count failed: $($_.Exception.Message)  body:$($_.ErrorDetails.Message)" }

# ---- 7. Precompute cache presence in _dicache ----
Sec "7. _dicache PRECOMPUTE BLOBS (di.json / output.json / vision / crop)"
try {
  $names = az storage blob list --account-name $saName --container-name $container --prefix "_dicache/" --auth-mode login --num-results 5000 --query "[].name" -o tsv 2>$null
  if ($names) {
    $arr = $names -split "`n"
    Write-Host ("di.json     : {0}" -f ($arr | Where-Object {$_ -like '*.di.json'}).Count)
    Write-Host ("output.json : {0}" -f ($arr | Where-Object {$_ -like '*.output.json'}).Count)
    Write-Host ("sections    : {0}" -f ($arr | Where-Object {$_ -like '*.sections.json'}).Count)
    Write-Host ("vision.*    : {0}" -f ($arr | Where-Object {$_ -like '*.vision.*'}).Count)
    Write-Host ("crop.*      : {0}" -f ($arr | Where-Object {$_ -like '*.crop.*'}).Count)
  } else { Write-Host "no _dicache blobs found (or no Storage Blob Data Reader access on this login)" }
} catch { Write-Host "storage list failed: $($_.Exception.Message)" }

Sec "DONE"
Stop-Transcript | Out-Null
Write-Host "`nAll output saved to: $out  (send that file back)"
