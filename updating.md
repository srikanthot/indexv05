# Overnight run — config already updated (new container + new prefix). Paste into git-bash, run, sleep.

# 1) env (AOAI_REASONING_EFFORT=low keeps GPT-5.1 fast)
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
az cloud set --name AzureUSGovernment >/dev/null
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815

# 2) sanity check the config (container = new one, prefix = new, account = UNCHANGED)
python -c "import json;c=json.load(open('deploy.config.json'));print('container:',c['storage']['pdfContainerName']);print('prefix   :',c['search']['artifactPrefix']);print('account  :',c['storage']['accountResourceId'].split('/')[-1])"

# 3) THE FULL RUN (preanalyze -> deploy_search -> reset+run -> heal loop x8 -> coverage). Long; let it finish.
mkdir -p reports
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --preanalyze-vision-parallel 40 --heal-max-iterations 8 --heal-wait-minutes 60 2>&1 | tee reports/overnight_run.log

# In the morning:
#   tail -40 reports/overnight_run.log
#   python scripts/check_index.py --config deploy.config.json --coverage
