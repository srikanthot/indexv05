# Runbook — finish the backfill from Jenkins (no local runs, no reset)

Branch: `safety-indexing-hardening` — pull it first. The indexer has ~12 of 48
docs indexed and is advancing. Goal: drain the remaining ~36 from Jenkins
WITHOUT resetting what's done.

## Why it was stuck (confirmed)
- Azure Search's 120-min per-run quota => only a few heavy docs finish per run.
- Auto-heal (ON) treated every not-yet-reached doc as "stuck", bumped its blob
  metadata + `resetdocs` every 30 min => reset the high-water mark => the
  indexer restarted on the same first docs forever.

## What the code now provides
- `function_app/shared/auto_heal.py` — a guard so auto-heal NEVER resets a
  running/advancing indexer (only heals a genuinely idle+stalled one).
- `scripts/backfill_indexer.py` — a resume driver: re-triggers the indexer
  (NO reset, NO heal, NO preanalyze) until the backlog drains. Safe to re-run;
  it CONTINUES, never restarts.
- New Jenkins action **`index-resume`** that runs that driver.

---

## DO THIS (two Jenkins runs)

### 1) Jenkins ACTION = `deploy-function`
Deploys the fixed function code (the auto_heal guard). Brief function restart;
the indexer (Search side) is unaffected and keeps its progress. After this,
auto-heal can never reset an in-progress backfill again.

### 2) Jenkins ACTION = `index-resume`
Drives the indexer to drain the remaining docs. It:
- best-effort sets `AUTO_HEAL_ENABLED=false` (extra safety),
- re-triggers the indexer run after each 120-min-quota stop,
- NEVER resets / heals / re-preanalyzes,
- prints progress and a final coverage number.

Each run is time-boxed (6 rounds / 8 h, under the Jenkins 10-h timeout). If it
prints `[BUDGET] ... NOT complete`, just **run `index-resume` again** — it picks
up exactly where it left off. When it prints `[DONE] ... status=success`, the
backlog is drained (all 48 indexed).
- `[STUCK]` (exit fail) = one specific document the indexer can't get past;
  nothing was reset — check that doc / the indexer error printed above.

That's it. Nothing here resets the 12 already-done docs.

---

## NOT NOW — separate, deliberate step (your new enrichment fields)
This backfill indexes with the EXISTING caches (`skill_version=1.0.0`), so your
recent enrichment code edits are NOT in it. When you actually want those fields:
1. Set `"skillVersion": "1.1.0"` in `deploy.config.json`.
2. Jenkins ACTION = `preanalyze` (rebuilds all 48 `output.json` cheaply — reuses
   DI + vision caches; the version bump triggers the rebuild).
3. Then `index-resume` again to reindex with the new records.
Do this only after step 2 above proves the pipeline reaches 48/48.

## Optional — go faster
Each 120-min run does only a few heavy docs. Scaling the Function App plan
up/out (EP1 -> EP2/EP3, min-instances 2) makes each run cover more. Not
required; the backfill completes either way, just in more `index-resume` runs.
