# Investigation request for Copilot — indexer "stuck at ~4 docs"

You have Azure credentials; this session's agent does not. Please run the
checks below and report the results back. **Read-only** — do NOT re-run the
pipeline, re-index, deploy, or run heal. Just gather the facts.

Fill in these names first (from deploy.config.json / the Azure portal):
`<FUNC_RG>` `<FUNC_APP>` `<SEARCH_SERVICE>` `<INDEXER>` `<PDF_CONTAINER>`

---

## Background (so you know what to look for)

The indexer processes only ~4 documents per run and never advances. Two
things may be in play, and I need to know which:

- **The stall** is believed to be Azure Search's 120-min-per-run execution
  quota (heavy docs), made permanent by auto-heal re-stamping not-yet-reached
  docs to `lastModified=NOW` and resetting the indexer's high-water mark.
- Separately, re-running with a new index name still produced an identical
  chunk count (9337), which suggests the pre-analyze `output.json` cache in
  `_dicache/` was not rebuilt.

---

## Please answer these

**Q1. Is auto-heal off?** (must be `false` for a clean backfill)
```
az functionapp config appsettings list -g <FUNC_RG> -n <FUNC_APP> \
  --query "[?name=='AUTO_HEAL_ENABLED'].value" -o tsv
```

**Q2. Does the indexer ADVANCE across runs or RESET to 4?**
Report `itemsProcessed` and the final tracking / high-water state from the
indexer status. If safe, look at the last TWO runs so we can see 4 → 8
(advancing = just slow) vs 4 → 4 (something is resetting the queue).
```
az rest --method get \
  --url "https://<SEARCH_SERVICE>.search.azure.us/indexers/<INDEXER>/status?api-version=2024-05-01-preview" \
  --resource "https://search.azure.us"
```

**Q3. What does the indexer's last result say?**
From the same status JSON, report `lastResult.errorMessage` and
`lastResult.warnings[]`. Specifically:
- Still `"execution time quota of 120 minutes ... processed 4 documents"`?
- Any embedding `429` / `"rate limited"` / throttling warnings?

**Q4. How many pre-analyze caches exist, and how fresh?**
```
az storage blob list -c <PDF_CONTAINER> --prefix "_dicache/" --num-results * \
  --query "[?ends_with(name,'.output.json')].{name:name, modified:properties.lastModified}" -o table
```
Then open 2-3 of those `*.output.json` blobs and report the `skill_version`
and `processing_status` fields inside each.

---

## What the answers tell us

| Result | Meaning / next step |
|---|---|
| Q1 = `true` | Auto-heal is still resetting the queue — that's why a fresh index still pins at 4. Turn it off and retry. |
| Q2 shows 4 → 4 | Queue is being reset each run. Find what stamps `lastModified=NOW` during the run (heal or a metadata bump). |
| Q2 shows 4 → 8 | Nothing is broken — it's just slow. Needs more runs or a bigger plan (scale up/out). |
| Q3 shows 429s | Raise the embedding deployment's TPM quota — likely the single biggest speedup. |
| Q4 shows only ~4 `output.json` | Pre-analyze itself is dying past doc 4 — send the pre-analyze log tail for the first uncached doc. |
| Q4 shows old `skill_version` and my recent edits are missing | Stale-cache confirmed — bump `SKILL_VERSION` and re-run incremental pre-analyze so the cache rebuilds. |
