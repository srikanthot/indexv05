Tell your frontend dev: when text_bbox.w_in >= 8 and text_bbox.h_in >= 10, treat it as a whole-page highlight (use low opacity ~0.15). When the rectangle is smaller, it's a precise highlight (use higher opacity ~0.35). This gives users a visual cue: precise yellow box = "this exact text" vs. faint full-page tint = "somewhere on this page."

python -c @"
import httpx, json
from azure.identity import DefaultAzureCredential
cfg = json.loads(open('deploy.config.json').read())
endpoint = cfg['search']['endpoint'].rstrip('/')
token = DefaultAzureCredential().get_token('https://search.azure.us/.default').token
r = httpx.get(f'{endpoint}/datasources?api-version=2024-11-01-preview',
              headers={'Authorization': f'Bearer {token}'}, timeout=30)
print('STATUS:', r.status_code)
print('BODY:', r.text[:2000])
"@

$myIp = (Invoke-RestMethod -Uri "https://api.ipify.org")
Write-Host "Adding $myIp to search service firewall..."
az search service update -n psegtmsrchuatv01 -g psegtmrguatv01 --ip-rules $myIp
Start-Sleep -Seconds 60
python scripts/deploy_search.py --config deploy.config.json


python -c @"
import httpx, json, base64, subprocess
from azure.identity import DefaultAzureCredential

cfg = json.loads(open('deploy.config.json').read())
endpoint = cfg['search']['endpoint'].rstrip('/')
search_name = endpoint.replace('https://','').split('.')[0]
rg = cfg['functionApp']['resourceGroup']

print('=' * 70)
print('CHECK 1: Subscription match')
print('=' * 70)
my_sub = subprocess.run(['az','account','show','--query','id','-o','tsv'],
                         capture_output=True, text=True).stdout.strip()
print(f'  az current sub:    {my_sub}')
res = subprocess.run(['az','resource','list','--resource-type','Microsoft.Search/searchServices',
                       '--name',search_name,'--query','[0].id','-o','tsv'],
                      capture_output=True, text=True).stdout.strip()
search_sub = res.split('/')[2] if res else 'NOT FOUND'
print(f'  search service sub: {search_sub}')
print(f'  MATCH: {my_sub == search_sub}')

print()
print('=' * 70)
print('CHECK 2: Identity Python uses vs az CLI uses')
print('=' * 70)
cred = DefaultAzureCredential()
token = cred.get_token('https://search.azure.us/.default').token
parts = token.split('.')
pad = '=' * ((4 - len(parts[1]) % 4) % 4)
claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
print(f'  Python token upn:  {claims.get(\"upn\") or claims.get(\"appid\")}')
print(f'  Python token oid:  {claims.get(\"oid\")}')
print(f'  Python token tid:  {claims.get(\"tid\")}')
az_user = subprocess.run(['az','account','show','--query','user.name','-o','tsv'],
                          capture_output=True, text=True).stdout.strip()
print(f'  az CLI user:       {az_user}')

print()
print('=' * 70)
print('CHECK 3: Roles ACTUALLY visible on this search service')
print('=' * 70)
my_oid = claims.get('oid')
search_id = subprocess.run(['az','search','service','show','-n',search_name,'-g',rg,
                             '--query','id','-o','tsv'],
                            capture_output=True, text=True).stdout.strip()
print(f'  search id: {search_id}')
roles = subprocess.run(['az','role','assignment','list',
                         '--assignee-object-id',my_oid,
                         '--scope',search_id,
                         '--query','[].roleDefinitionName','-o','tsv'],
                        capture_output=True, text=True).stdout.strip()
print(f'  roles found:')
for line in (roles.split('\n') if roles else ['  (NONE)']):
    print(f'    - {line}')

print()
print('=' * 70)
print('CHECK 4: The actual 403 body')
print('=' * 70)
r = httpx.get(f'{endpoint}/datasources?api-version=2024-11-01-preview',
              headers={'Authorization': 'Bearer ' + token}, timeout=30)
print(f'  STATUS: {r.status_code}')
print(f'  BODY (first 1200 chars):')
print(r.text[:1200])
"@

az cosmosdb sql database create `
  --account-name psegtmcosmuatv01 `
  --resource-group psegtmrguatv01 `
  --name indexing `
  --throughput 400
# 1. Enable blob soft-delete on storage account (fixes FAIL #1)
az storage account blob-service-properties update `
  --account-name psegtmstacuatv01 `
  --enable-delete-retention true `
  --delete-retention-days 7


# 3. Create the Cosmos database (only if step 2 didn't show it)
az cosmosdb sql database create `
  --account-name psegtmcosmuatv01 `
  --resource-group psegtmrguatv01 `
  --name indexing `
  --throughput 400

# Setup fixes
az storage account blob-service-properties update --account-name psegtmstacuatv01 --enable-delete-retention true --delete-retention-days 7

az cosmosdb sql database list --account-name psegtmcosmuatv01 --resource-group psegtmrguatv01 --query "[].name" -o tsv

# If 'indexing' not in output above:
az cosmosdb sql database create --account-name psegtmcosmuatv01 --resource-group psegtmrguatv01 --name indexing --throughput 400

# Re-verify
python scripts/preflight.py --config deploy.config.json

# Big bootstrap
python scripts/bootstrap.py --config deploy.config.json --auto-fix

# Upload PDFs (do via portal or az upload-batch — your choice)

# Preanalyze (1.5-3 hours for 50 PDFs)
python scripts/preanalyze.py --config deploy.config.json --concurrency 3 --vision-parallel 50

# Reset + verify
.\scripts\reset_indexer.ps1
python scripts/check_index.py --config deploy.config.json --coverage


# ─── Read your config values ───
$CFG = Get-Content deploy.config.json | ConvertFrom-Json
$DB  = $CFG.cosmos.database
$COSMOS_ACCOUNT = ($CFG.cosmos.endpoint -replace 'https://', '' -split '\.')[0]
$ACCOUNT_RG = (az cosmosdb show --name $COSMOS_ACCOUNT --query resourceGroup -o tsv)

Write-Host "Cosmos account: $COSMOS_ACCOUNT (in $ACCOUNT_RG)"
Write-Host "Will create database: $DB"

# ─── Create the database ───
az cosmosdb sql database create `
  --account-name $COSMOS_ACCOUNT `
  --resource-group $ACCOUNT_RG `
  --name $DB `
  --throughput 400

Write-Host "Database '$DB' created."

# ─── Re-run bootstrap (should now pass all 8 STEPs) ───
python scripts/bootstrap.py --config deploy.config.json --auto-fix


# Step 1 — Get the Function App's MI principal ID
$CFG = Get-Content deploy.config.json | ConvertFrom-Json
$RG  = $CFG.functionApp.resourceGroup
$FN  = $CFG.functionApp.name
$PRINCIPAL = (az functionapp identity show -g $RG -n $FN --query principalId -o tsv)
Write-Host "Function App MI: $PRINCIPAL"

# Step 2 — Check what roles the MI has on the storage account
$STORAGE_ID = $CFG.storage.accountResourceId
az role assignment list --assignee $PRINCIPAL --scope $STORAGE_ID --query "[].{role:roleDefinitionName, scope:scope}" -o table


# Step 3 — Assign Storage Blob Data Reader
az role assignment create --assignee $PRINCIPAL --role "Storage Blob Data Reader" --scope $STORAGE_ID

# Step 4 — Wait 5-10 min for propagation
Start-Sleep -Seconds 600

# Step 5 — Restart Function App so it picks up new tokens
az functionapp restart -g $RG -n $FN
Start-Sleep -Seconds 30

# Step 6 — Reset and re-run indexer (existing records will be overwritten with correct page resolution)
.\scripts\reset_indexer.ps1

# Step 7 — Wait for indexer, then validate
Start-Sleep -Seconds 120
python scripts/smoke_test.py --config deploy.config.json --skip-run

# List the _dicache folder, looking specifically for the .di.json files
$STORAGE = ($CFG.storage.accountResourceId -split '/')[-1]
$CONTAINER = $CFG.storage.pdfContainerName
az storage blob list --account-name $STORAGE --container-name $CONTAINER --auth-mode login --prefix "_dicache/" --query "[?ends_with(name, '.di.json')].{name:name, size:properties.contentLength}" -o table


# Download
az storage blob download --account-name sapsegtmandev01 --container-name techmanualsv06 --name "_dicache/ED-DC-IRE.pdf.di.json" --file ".\test_di.json" --auth-mode login

# Quick structure check
$j = Get-Content .\test_di.json -Raw | ConvertFrom-Json
Write-Host "Has analyzeResult key: $($j.analyzeResult -ne $null)"
Write-Host "Top-level keys: $($j.PSObject.Properties.Name -join ', ')"
if ($j.analyzeResult) {
    Write-Host "analyzeResult.paragraphs count: $($j.analyzeResult.paragraphs.Count)"
    Write-Host "analyzeResult.sections count: $($j.analyzeResult.sections.Count)"
} else {
    Write-Host "paragraphs count: $($j.paragraphs.Count)"
    Write-Host "sections count: $($j.sections.Count)"
}

?filter=record_type eq 'text' and physical_pdf_page ne null&select=chunk_id,physical_pdf_page,printed_page_label,text_bbox,callouts,page_resolution_method&$top=3
