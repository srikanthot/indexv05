# Overnight run — POWERSHELL. Config already updated (new container + new prefix).
# Paste the setup lines, then run the ONE deploy.py line. Then sleep.

# 1) env (AOAI_REASONING_EFFORT=low keeps GPT-5.1 fast)
$env:SSL_CERT_FILE = 'C:/Users/C90255306/Downloads/combined-ca.crt'
$env:REQUESTS_CA_BUNDLE = 'C:/Users/C90255306/Downloads/combined-ca.crt'
$env:AOAI_REASONING_EFFORT = 'low'
az cloud set --name AzureUSGovernment | Out-Null
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815
New-Item -ItemType Directory -Force reports | Out-Null

# 2) sanity check the config (container = new one, prefix = new, account = UNCHANGED)
python -c "import json;c=json.load(open('deploy.config.json'));print('container:',c['storage']['pdfContainerName']);print('prefix:',c['search']['artifactPrefix']);print('account:',c['storage']['accountResourceId'].split('/')[-1])"

# 3) THE FULL RUN — this ONE line does preanalyze -> deploy_search -> reset+run -> heal loop x8 -> coverage.
#    It wraps on screen but it is a single command. Runs for hours; let it finish.
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --preanalyze-vision-parallel 40 --heal-max-iterations 8 --heal-wait-minutes 60 2>&1 | Tee-Object -FilePath reports\overnight_run.log

# In the morning (PowerShell):
#   Get-Content reports\overnight_run.log -Tail 40
#   python scripts/check_index.py --config deploy.config.json --coverage
Hi Jason,

Thank you, and hope your daughter recovers soon.

We can keep the discussion to 30 minutes. I will resend the invite as well.

The main HA items we wanted to confirm are:

1. For production regional redundancy, which secondary region should we use along with Arizona? Should we go with Texas, or is there another recommended GCC High region?

2. For Cosmos DB, do you recommend single-write multi-region with failover, or multi-write for our chatbot use case?

3. For Azure AI Search, should we increase the replica count from 1 to 2 or 3 for production? Also, does increasing replicas help only retrieval/query availability, or does it help indexing as well?

4. Can you also help us understand the cost impact for these recommended HA/DR changes, mainly Cosmos DB multi-region, Azure AI Search replicas, App Service scaling, and storage redundancy?

Thanks,
Srikanth
