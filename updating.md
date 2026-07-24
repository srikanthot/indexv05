# Run the indexing pipeline end-to-end (team KT)

Two phases: **A) set up the machine + log in to Azure**, then **B) run the
pipeline**. Commands are for Windows PowerShell (VS Code); the Linux/Mac
equivalents are noted where they differ.

Prerequisite: you need `deploy.config.json` for the target environment. It is
NOT in git (per-environment secrets). Copy `deploy.config.example.json` to
`deploy.config.json` and fill in the resource names for that subscription.

---

## A. One-time setup on the machine

```powershell
# 1. Get the code (on the fixed branch)
git clone https://github.com/srikanthot/indexv05.git
cd indexv05
git checkout safety-indexing-hardening
# (already cloned? just: git pull)

# 2. Python virtual env + dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

# 3. Log in to the Azure US Government cloud
az cloud set --name AzureUSGovernment
az login
az account set --subscription <SUBSCRIPTION_ID>   # only if you have more than one
```

---

## B. Run the pipeline (2 commands)

### Step 1 — build everything (one command)
Runs: bootstrap (preflight + **deploy Function App code** + app settings) ->
pre-analyze (DI + vision -> cache) -> create search artifacts (index / skillset
/ indexer / datasource) -> trigger a fresh full indexer pass.

```powershell
python scripts/deploy.py --config deploy.config.json --skip-roles --skip-heal-loop
```

- `--skip-roles`  : RBAC is granted once, separately (the service principal has
  no admin rights), so the pipeline never tries to assign roles.
- `--skip-heal-loop` : skip the old heal loop; Step 2 below is the reliable way
  to drive the indexer to completion.

### Step 2 — index until every document is done
The indexer only finishes a few heavy docs per 120-min run, so this drives it
run-after-run (forcing any stragglers) until all documents are indexed.

```powershell
python scripts/backfill_indexer.py --config deploy.config.json
```

- When it prints `[DONE] all N documents are indexed` -> finished.
- If it prints `[BUDGET] ... hit the ...h cap` -> just run the same command
  again; it continues (nothing is reset).
- If it prints `[WARN] ... did not index` -> it lists each stuck doc with the
  reason (missing pre-analyze cache vs. index-time error) so you know the fix.

That's it: **Step 1, then Step 2.**

---

## Notes
- To make NEW enrichment-code changes take effect: set `"skillVersion"` to a new
  value in `deploy.config.json` BEFORE Step 1, so pre-analyze rebuilds the cache
  with the current code. Otherwise it reuses the existing cache.
- Prefer running each stage separately (e.g. to debug)? Equivalent sequence:
  ```powershell
  python scripts/bootstrap.py       --config deploy.config.json --skip-roles   # incl. function code
  python scripts/preanalyze.py      --config deploy.config.json --incremental
  python scripts/deploy_search.py   --config deploy.config.json
  python scripts/backfill_indexer.py --config deploy.config.json
  ```
- Jenkins equivalent of Step 1 = the `deploy` action; of Step 2 = the
  `index-resume` action.
