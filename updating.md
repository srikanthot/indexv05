# Overnight run — same storage account, NEW container. Fill 1 value, paste into git-bash, sleep.

# ============== FILL THIS (2nd is optional) ==============
NEW_CONT=<the new blob container that has the 12 PDFs>
NEW_PREFIX=perfv01        # any fresh name -> brand-new clean index
# ========================================================

# env (you already have the cert; harmless to re-set)
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
az cloud set --name AzureUSGovernment >/dev/null
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815

# point the config at the new container + fresh index prefix (same storage account)
python - <<PY
import json
c=json.load(open("deploy.config.json"))
c["storage"]["pdfContainerName"]="$NEW_CONT"
c["search"]["artifactPrefix"]="$NEW_PREFIX"
json.dump(c, open("deploy.config.json","w"), indent=2)
print("CONFIG UPDATED -> container:", c["storage"]["pdfContainerName"], "| prefix:", c["search"]["artifactPrefix"])
PY

# THE FULL RUN (preanalyze -> deploy_search -> reset+run -> heal loop x8 -> coverage). Long; let it finish.
mkdir -p reports
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --preanalyze-vision-parallel 20 --heal-max-iterations 8 --heal-wait-minutes 60 2>&1 | tee reports/overnight_run.log
```

# In the morning:
#   tail -40 reports/overnight_run.log        # look for "DEPLOY COMPLETE" / coverage table
#   python scripts/check_index.py --config deploy.config.json --coverage

# (No RBAC / soft-delete steps needed — same storage account, identities already have access.)
