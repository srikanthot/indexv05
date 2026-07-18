# Gotchas & Fixes — a living log

A running record of errors/limitations we hit on this indexing pipeline and **how we
solved them**, so nobody burns a day re-discovering the same thing. Add a new entry
whenever you hit (and fix) something.

**Format for new entries** — copy this:
```
### <short title>
- **Symptom:** what you actually saw (error text / behavior).
- **Cause:** the real reason.
- **Fix:** exactly what made it work.
- **Ref:** file / command / commit (optional).
```
Newest at the top of each section.

---

## 1. Models & Foundry

### "Why is Azure OpenAI still here? We moved to Foundry."
- **Symptom:** the config + index reference `azureOpenAI` / a vectorizer even though we deploy models in Azure AI **Foundry**.
- **Cause:** there are TWO model callers. (a) The **function app** (summary/vision) uses Foundry via `modelProvider: "foundry"` + the `foundry` block. (b) **Azure AI Search itself** does the embeddings (chunk embedding at index time + query embedding at search time), and Search's built-in embedder ONLY speaks the "Azure OpenAI" protocol — there is no "Foundry vectorizer" kind.
- **Fix:** point the `azureOpenAI` block at your **Foundry resource's OpenAI-compatible endpoint** (`https://<foundry>.openai.azure.us`) + your Foundry embedding deployment. It's still Foundry — just addressed through the OpenAI-compatible door Search requires. One Foundry resource, two endpoints, no separate Azure OpenAI resource.
- **Ref:** `deploy.config.example.json`; `deploy_search.py:131-132` (substitutes `azureOpenAI.endpoint`/`embedDeployment` into index vectorizer + skillset embed skills); `bootstrap.py:397-413`.

### Vectorizer rejects the endpoint ("Invalid resourceUri … must be openai.azure.us")
- **Symptom:** deploy/search vectorizer fails with an invalid `resourceUri`.
- **Cause:** the vectorizer needs the OpenAI-flavored endpoint host, not the Foundry project host.
- **Fix:** set `azureOpenAI.endpoint` to `https://<foundry-resource>.openai.azure.us/` (the `.openai.azure.us` one, not `.services.ai.azure.us`).

### Embedding dimension mismatch
- **Symptom:** vectors don't index / dimension errors.
- **Cause:** the index `text_vector` field is **1536-dim** (text-embedding-ada-002). A different embed model (e.g. text-embedding-3-large = 3072) won't fit.
- **Fix:** deploy a 1536-dim embed model (ada-002) in Foundry, OR change the index's `dimensions` to match. Index-time and query-time embedding MUST be the same model.
- **Ref:** `search/index.json` (`text_vector` dimensions).

### GPT-5.1 (reasoning model) rejects `temperature`
- **Symptom:** HTTP 400 on chat/vision calls that pass `temperature=0.0`.
- **Cause:** GPT-5.1 is a reasoning model; it only accepts the default temperature (1) and rejects any explicit non-1 value.
- **Fix:** never hardcode temperature. Use `shared/config.model_gen_kwargs()`, which omits temperature and is env-tunable (`AOAI_TEMPERATURE`, `AOAI_MAX_COMPLETION_TOKENS`, `AOAI_REASONING_EFFORT`). Proven: 590 figures, 0 × 400s.
- **Ref:** `function_app/shared/config.py`.

---

## 2. Deployment, RBAC, function registration

### Indexer returns 404 "Web API" errors
- **Symptom:** indexer skill calls fail with 404; figures/tables missing.
- **Cause:** the custom-skill functions weren't registered on the function app (deploy didn't publish them).
- **Fix:** `func azure functionapp publish <app> --python --build remote` → confirm all functions register (should be 8).

### `az role assignment create` → "MissingSubscription" (Gov CLI glitch)
- **Symptom:** RBAC grant fails with MissingSubscription even though the sub is set.
- **Cause:** intermittent Gov CLI glitch on the ARM role-assignment path.
- **Fix:** use an `az rest` ARM PUT to create the role assignment directly.

### RBAC grant "succeeded" but you get 403s later
- **Symptom:** deploy said role granted, but the app/search MI hits 403 at runtime.
- **Cause:** `az role assignment create` was called with the failure ignored — a failed grant reported success.
- **Fix:** `assign_roles.py` now re-lists after each create and FAILS LOUDLY if the grant didn't take. If you see the loud failure, the caller lacks Owner/User-Access-Administrator on the scope.
- **Ref:** `scripts/assign_roles.py:grant_rbac` / `grant_cosmos_data_role`.

---

## 3. File-sync / environment

### Whole function app won't import — `ValueError: string keys in translate table must be of length 1`
- **Symptom:** every script/test dies on import at a `str.maketrans({...})` call.
- **Cause:** a file-sync (office laptop → GitHub → local) **silently stripped invisible zero-width characters** (U+200B etc.) out of the source, leaving an empty `""` key that `str.maketrans` rejects. This takes down the ENTIRE app.
- **Fix:** rebuilt those tables keyed by **integer code points** (sync-proof) in `sections.py` + `text_utils.py`. If you add typography maps, key by ordinal (`0x2018: "'"`), never by a raw invisible literal.
- **Ref:** `function_app/shared/sections.py`, `text_utils.py`.

### Git warns "LF will be replaced by CRLF"
- **Symptom:** noisy warnings on `git add` (Windows).
- **Cause:** line-ending normalization; harmless.
- **Fix:** ignore, or set a `.gitattributes` policy. Does not affect runtime.

---

## 4. Indexing pipeline

### Indexer looks stuck at "processed=0"
- **Symptom:** indexer shows 0 processed for a long time; looks hung.
- **Cause:** NOT stuck — just slow. A 37 MB / 500+ figure PDF takes ~15-20 min to fully process.
- **Fix:** be patient; poll status. A fresh run is confirmed done only when it reports items processed AND leaves `inProgress` (see run_pipeline fresh-wait below).

### A PDF is marked "done" but lost figures/tables
- **Symptom:** doc looks indexed but diagrams/tables aren't searchable.
- **Cause:** the "done" gate only checked that a one-line summary record existed.
- **Fix:** `auto_heal.py` now excludes any file whose records carry a loss status (`needs_preanalyze_output`, `all_figures_dropped`, `partial_figure_loss`, `partial_vision`) → it re-heals. `preanalyze._is_pdf_done` no longer treats `partial_vision` as complete (bounded retry; `ACCEPT_PARTIAL_VISION=true` to opt out).
- **Ref:** `function_app/shared/auto_heal.py`, `scripts/preanalyze.py`.

### Big table silently loses all per-row lookup records
- **Symptom:** a huge table's parent text is searchable but per-row lookups aren't; no error.
- **Cause:** `ROW_RECORD_MAX_ROWS = 5000` was an all-or-nothing gate that returned `[]` (dropped ALL rows) with no log — and **merged multi-page tables** can exceed 5000 even when each page passed.
- **Fix:** now truncates to the cap, logs a WARNING, and stamps `table_rows_truncated` / `table_rows_suppressed_count` on the parent table record.
- **Ref:** `function_app/shared/tables.py`, `process_table.py`.

### Table value bound to the WRONG column (e.g. a "3:1" ratio)
- **Symptom:** a value like a ratio (`3:1`) or time (`1:00`) maps to the wrong header.
- **Cause:** rows were serialized as `"Header: value; ..."` then RE-PARSED on `:` / `;` — any value containing those delimiters got mis-split.
- **Fix:** rows now carry structured, grid-bound cell lists (`cell_headers`/`cell_values`); downstream uses those directly, no re-parse.
- **Ref:** `function_app/shared/tables.py`, `process_table.py`.

### Highlight box covers the whole paragraph, not the chunk
- **Symptom:** citation highlight is too big / imprecise.
- **Cause:** the box was a paragraph-substring match (paragraph-level polygons).
- **Fix:** now uses DI **line-level** polygons (`pages[].lines`) to build one tight box that hugs the actual chunk (`bbox_version` 2.1.0). Falls back to the paragraph matcher if lines don't match, so it never regresses. Both modes indexed: `text_bbox` (full "hold box") + `line_bboxes` (precise) + `bbox_mode_available`.
- **Ref:** `function_app/shared/page_label.py`; `tests/test_bbox_precision.py`.

### Multi-page procedure comes back incomplete / a chunk is missing
- **Symptom:** "steps to maintain the 13kV transformer" (pages 2-5) returns only some chunks.
- **Cause:** procedure grouping keyed off each chunk detecting its own steps → a continuation/warning chunk with no visible step numbers got orphaned.
- **Fix:** grouping is now SECTION-based — every chunk of a procedure shares one `procedure_id` (even continuation/warning chunks), and `procedure_step_count` is the TOTAL step count so the chatbot can detect a missing chunk.
- **Ref:** `function_app/shared/procedures.py`; `tests/test_procedures.py`.

### A booby-trapped scanned page could hijack the ingest LLM
- **Symptom:** (risk) hidden "ignore previous instructions" text in a scanned page.
- **Cause:** vision/summary calls sent raw page text with no injection defense.
- **Fix:** `prompt_safety.py` hardens the system prompt + fences the untrusted text on all 3 ingest LLM calls.
- **Ref:** `function_app/shared/prompt_safety.py`, `diagram.py`, `summary.py`, `preanalyze.py`.

---

## 5. Blob ↔ index lifecycle (delete / update / reap)

### Deleted blob's chunks stay in the index
- **Symptom:** you delete a PDF from blob but its chunks still return in search.
- **Cause (old):** reconcile deleted by a reconstructed `parent_id` that **hardcoded the Gov storage suffix** — any mismatch silently deleted nothing.
- **Fix:** reconcile now deletes by exact `source_file` (the blob name, no reconstruction), covering all record types. Or force it: `reconcile.py --purge-files <name.pdf>`.
- **Ref:** `scripts/reconcile.py`.

### Edited blob (same name, new timestamp) leaves stale/orphaned chunks
- **Symptom:** you re-upload an edited PDF; old chunks from removed/changed pages linger.
- **Cause:** the indexer re-projects an edited blob but `mergeOrUpload` never DELETES the old chunks (chunk IDs include a content hash, so edited pages produce new IDs and orphan the old ones).
- **Fix:** on EDIT, reconcile purges ALL of that PDF's chunks first, then preanalyze + indexer rebuild fresh. Edit detection uses blob-LMT-vs-Cosmos AND a Cosmos-independent cached `source_size`. For Jenkins: detect the timestamp change and call `reconcile.py --purge-files <name.pdf>` before re-indexing.
- **Ref:** `scripts/reconcile.py`, `scripts/preanalyze.py` (stores `_dicache/<pdf>.source_size`).

### `reap_stale_rows.py` deletes nothing
- **Symptom:** stale (old skill_version) rows never get removed.
- **Cause:** it deleted by `chunk_id`, but the index KEY is `id`; Azure delete actions must use the key → silent no-op.
- **Fix:** select + delete by `id`.
- **Ref:** `scripts/reap_stale_rows.py`.

### `run_pipeline` reports success before the real run happened
- **Symptom:** pipeline says indexer succeeded, but it used a stale run from before your new caches.
- **Cause:** the wait returned as soon as the indexer wasn't `inProgress` — if idle between scheduled runs, that's the PREVIOUS run.
- **Fix:** `wait_for_indexer_idle` now requires a run that STARTED after the pipeline began (`fresh_after_iso`).
- **Ref:** `scripts/run_pipeline.py`.

### git push rejected (remote ahead)
- **Symptom:** push fails, remote has commits you don't.
- **Fix:** `git pull --rebase <remote> <branch>` then push.

---

## 6. Azure Search index specifics (quick reference)

- **Delete actions must use the KEY field `id`** (not `chunk_id`, not `parent_id`). Select `id`, then POST `{"@search.action":"delete","id":<id>}`.
- **Enumerate all distinct `source_file`s** via a facet with `count:0` (returns all values): `{"facets":["source_file,count:0"],"top":0}` and read `@search.facets` (note: `@search.facets`, plural — NOT `@odata.facets`).
- **Skip window caps at 100k** — for very large result sets, partition by `source_file` instead of paging `$skip` past 100k.
- **New index fields need THREE touches:** `search/index.json` (schema) + `search/skillset.json` skill `outputs` (`{name, targetName:"<prefix>_"+name}`) + the index-projection `mappings` (`{name, source:".../<prefix>_"+name}`). Prefixes: `text_` / `dgm_` / `tbl_` / `sum_`; table-row mappings use the nested source directly (no prefix). Adding a field to only one place = silently dropped or a broken skillset.
- **Run the gates before promotion:** `python scripts/validate_index_quality.py --config deploy.config.json` — exits non-zero on critical gate failure (wire into Jenkins to block promotion).

---

## 7. Quick "is it me or the data?" triage
- Expected answer NOT present in the structured chunks after reindex → **indexing/data-prep** issue.
- Structured data IS present but the answer is wrong → **retrieval/ranking/agent** issue (chatbot side).
Enforce this boundary in bug triage to avoid endless code-side workarounds.
