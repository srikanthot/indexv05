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
