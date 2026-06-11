Tier 1 — Skip everything except indexing (most common after initial setup)
ONE command:


python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze
Line-by-line equivalent:


python scripts/deploy_search.py --config deploy.config.json
.\scripts\reset_indexer.ps1
python scripts/heal_until_done.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
Use this when: function code + search artifacts are already deployed, cache is already built. You just want to retrigger the indexer (e.g., after metadata changes, or to retry stuck PDFs).

Tier 2 — New PDFs added, need preanalyze + indexer
ONE command:


python scripts/deploy.py --config deploy.config.json --skip-bootstrap
Line-by-line equivalent:


python scripts/preanalyze.py --config deploy.config.json --incremental
python scripts/deploy_search.py --config deploy.config.json
.\scripts\reset_indexer.ps1
python scripts/heal_until_done.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
Use this when: new PDFs were uploaded to the blob container. --skip-bootstrap skips RBAC, Cosmos creation, function code deploy. preanalyze.py --incremental only processes PDFs that don't already have a complete cache.

Tier 3 — Just retrigger stuck PDFs (no preanalyze, no search redeploy)
ONE command:


python scripts/heal_until_done.py --config deploy.config.json
Line-by-line equivalent: same — it's already one script.

Use this when: indexer got stuck on some PDFs and you just want to retry. No code changes, no metadata changes, just push the indexer along.

Quick reference card
Scenario	Command
First-time setup (RBAC, function deploy, everything)	python scripts/deploy.py --config deploy.config.json --auto-fix
New PDFs added (need preanalyze)	python scripts/deploy.py --config deploy.config.json --skip-bootstrap
Metadata changed or just retrigger (no new PDFs)	python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze
Just heal stuck PDFs (cache is fine, no schema changes)	python scripts/heal_until_done.py --config deploy.config.json
Just check status (zero changes made)	python scripts/check_index.py --config deploy.config.json --coverage
Full teammate-facing message (copy-paste version)

# ===== ONE-TIME PER MACHINE =====
git clone https://github.com/srikanthot/indexv05.git
cd indexv05
pip install -r requirements.txt
# save deploy.config.json in this folder

# ===== ONE-TIME PER SHELL SESSION =====
az cloud set --name AzureUSGovernment
az login
az account set --subscription "sub-pseg-nj-techmanual-dev"

# ===== FIRST-TIME DEPLOY (does RBAC + function code + everything) =====
python scripts/deploy.py --config deploy.config.json --auto-fix

# ===== AFTER NEW PDFS ARE UPLOADED =====
python scripts/deploy.py --config deploy.config.json --skip-bootstrap

# ===== AFTER METADATA / TAG CHANGES (no new PDFs) =====
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze

# ===== IF INDEXER GOT STUCK ON SOME PDFS =====
python scripts/heal_until_done.py --config deploy.config.json

# ===== VERIFY ANY TIME =====
python scripts/check_index.py --config deploy.config.json --coverage
That covers every scenario. Your teammates don't need to know bootstrap.py, deploy_search.py, or reset_indexer.ps1 exist — they just pick the right deploy.py invocation based on what changed.

Hey, here's the setup:


git clone https://github.com/srikanthot/indexv05.git
cd indexv05
pip install -r requirements.txt
az cloud set --name AzureUSGovernment
az login
az account set --subscription "sub-pseg-nj-techmanual-dev"
Then save the deploy.config.json I sent you in the indexv05 folder, and run:


python scripts/deploy.py --config deploy.config.json --auto-fix
That single command does everything — RBAC, Cosmos DB setup, blob soft-delete, function app deploy, preanalyze, indexer, healing. Takes a few hours on first run (mostly preanalyze). Watch the console output — if it finishes with ✓ DEPLOY COMPLETE, every PDF is indexed.

python scripts/bootstrap.py --config deploy.config.json --auto-fix --skip-function-app --skip-search-artifacts --skip-smoke-test

az functionapp config appsettings set -g <RG> -n <FuncApp> --settings SCM_DO_BUILD_DURING_DEPLOYMENT=1
Compress-Archive -Path function_app\* -DestinationPath func.zip -Force
az functionapp deployment source config-zip -g <RG> -n <FuncApp> --src func.zip

python scripts/deploy.py --config deploy.config.json --skip-bootstrap

