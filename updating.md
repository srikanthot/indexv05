# Execution plan — fix the "stuck at ~4 docs" stall (code already pushed)

Branch: `safety-indexing-hardening`. Pull it first. Read-only checks are safe;
everything that re-runs the pipeline needs Srikanth's OK before running.

Resource names (from the last diagnostic):
`FUNC_RG=psegtmrgdevv01`  `FUNC_APP=psegtmfuncdevv01`
`SEARCH=psegtmsrchdevv01`  `INDEXER=psegtmindexdev06-indexer`
`CONTAINER=techmanualsv03`

## Root cause (confirmed)
- The indexer hits Azure Search's **120-min per-run quota** and finishes only
  ~4 heavy docs per run. That part is expected (docs are big; no 429s seen).
- It NEVER ADVANCES because **auto-heal is ON** and treats every not-yet-reached
  doc as "stuck" (no summary record yet), bumps its blob metadata + calls
  `resetdocs` every 30 min → resets the high-water mark → the indexer restarts
  on the same first ~4 docs forever.
- Separately, all 48 `_dicache/*.output.json` caches are `skill_version=1.0.0`,
  so recent enrichment code edits are NOT in the index yet.

## What the pushed code changes do
1. `function_app/shared/auto_heal.py` — auto-heal now checks indexer status and
   **skips healing while the indexer is running or still advancing** (only heals
   a genuinely idle+stalled index). Stops the queue reset.
2. `function_app/shared/ids.py` — `SKILL_VERSION` default bumped `1.0.0 -> 1.1.0`
   so preanalyze's cache-version gate rebuilds the caches with current code.

---

## STEP 1 — stop the stall NOW (no redeploy; instant)
Turn auto-heal off on the live app so it stops resetting the queue:
```
az functionapp config appsettings set -g psegtmrgdevv01 -n psegtmfuncdevv01 --settings AUTO_HEAL_ENABLED=false
az functionapp restart -g psegtmrgdevv01 -n psegtmfuncdevv01
```
Then let the indexer keep running on its 5-min schedule and watch it ADVANCE
(run this a few times over ~1-2 hours; `itemsProcessed`/high-water should climb
4 → 8 → … not reset to 4):
```
az rest --method get --url "https://psegtmsrchdevv01.search.azure.us/indexers/psegtmindexdev06-indexer/status?api-version=2024-05-01-preview" --resource "https://search.azure.us"
```
Each run still caps near ~4 docs (the 120-min quota), but it now moves forward
until all 48 are indexed.

## STEP 2 — deploy the fixed function code (so auto-heal is safe to leave on)
Deploy the function app from the pulled branch (your normal path, e.g.
`scripts/deploy_function.ps1` or the Jenkins `deploy-function` action). After
this the auto_heal guard is live, so `AUTO_HEAL_ENABLED=true` is safe again
(it will no longer reset an in-progress backfill).

## STEP 3 — get your enrichment edits into the index (do after Step 1 is advancing)
Current caches are stale (`skill_version=1.0.0`); your edits aren't in them.
1. In your real `deploy.config.json`, set `"skillVersion": "1.1.0"`.
2. Re-run preanalyze **INCREMENTAL** (NOT `--force`):
   ```
   python scripts/preanalyze.py --config deploy.config.json --incremental
   ```
   It logs `stale-version <pdf> ... -> rebuild output.json` for all 48 docs and
   rebuilds them **cheaply** — DI cache and vision sidecars are reused, so there
   is NO re-DI and NO vision-API cost (minutes, not the 12-13h `--force`).
3. Reindex to pick up the rebuilt records. With Step 1's fix the indexer will
   advance to 48/48 across runs.

## Optional — finish faster
Each 120-min run does ~4 heavy docs. To speed the backfill, scale the plan up/out
(EP1 → EP2/EP3, min-instances 2). Not required; it will complete either way.

## How to confirm it's fixed
- `az rest ... /indexers/.../status` shows `itemsProcessed` climbing run-over-run
  (not pinned at 4).
- Final doc count reaches all 48 source PDFs.
