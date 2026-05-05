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

