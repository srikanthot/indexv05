# ============================================================================
# ROOT CAUSE FOUND — indexer "stuck at ~4 docs" — and THE FIX (Copilot: execute)
# ============================================================================
# CONFIRMED from diagnostics (2026-07-22): NOT out-of-memory. The real error is
#   "Indexer execution time quota of 120 minutes has been reached ... processed
#    4 documents" — Azure Search's HARD 120-min-per-run limit. The docs are heavy,
# so only ~4 finish per run. Plan is EP1 (1 vCPU / 3.5GB / capacity 1).
#
# WHY IT NEVER ADVANCES (the actual bug): the indexer resumes across runs via a
# high-water-mark on metadata_storage_last_modified. But heal_until_done.py +
# the auto_heal timer BUMP metadata (force_reindex=NOW) + resetdocs on every doc
# that "has no summary yet". With the 120-min quota, ~44 docs simply haven't been
# REACHED yet -- they are NOT stuck -- but the heal loop re-stamps them all to
# lastModified=NOW every cycle, which RESETS the queue. So the indexer re-does the
# same first ~4 heavy docs forever. "no summary yet" != "failed".
#
# ---- FIX PART 1: stop the churn so the indexer can ADVANCE (do this first) ----
#   # Turn OFF auto-heal (it re-stamps not-yet-reached docs and resets the queue):
#   az functionapp config appsettings set -g <FUNC_RG> -n psegtmfuncdevv01 --settings AUTO_HEAL_ENABLED=false
#   az functionapp restart -g <FUNC_RG> -n psegtmfuncdevv01
#   # And DO NOT run heal_until_done.py in bump mode during the bulk backfill.
#   # Just let the indexer's PT5M schedule run (or trigger it), and it will
#   # process the next ~4 docs each run and ADVANCE the high-water-mark until 48/48.
#
# ---- FIX PART 2: make each 120-min run finish MANY more than 4 docs ----
#   # You are on EP1 (smallest). Scale UP (more CPU/RAM per instance) + OUT
#   # (more instances processing docs in parallel). Zero code change, keeps speed:
#   az appservice plan update -g <PLAN_RG> -n psegtmfuncplandevv01 --sku EP3
#   az functionapp plan update -g <PLAN_RG> -n psegtmfuncplandevv01 --min-instances 2 --max-burst 3
#   # (EP2 if EP3 is too costly. This is the SPEED lever the user wants.)
#
# ---- FIX PART 3: confirm + remove the per-doc bottleneck (why 4 docs = 120 min) --
#   # Almost certainly the embedding endpoint is THROTTLING (429). If so, raising
#   # the embedding deployment's TPM quota is the single biggest speedup.
#   # a) Look for throttling/backoff warnings on the indexer:
#   python -c "import httpx,json; from azure.identity import DefaultAzureCredential; t=DefaultAzureCredential().get_token('https://search.azure.us/.default').token; lr=httpx.get('https://<SEARCH>.search.azure.us/indexers/<INDEXER>/status?api-version=2024-05-01-preview',headers={'Authorization':f'Bearer {t}'}).json().get('lastResult',{}); print(json.dumps(lr.get('warnings',[])[:20],indent=1))"
#   # b) In the portal: the embedding deployment -> Metrics -> "Rate limited
#   #    requests" (429s). If >0, raise that deployment's TPM (Tokens Per Minute).
#
# ---- After 1-3: verify progress ----
#   # Run the indexer, then check the count climbs past 4 toward 48 (no reset):
#   python scripts/check_index.py   # or your usual count check; expect 48/48
# ============================================================================

# ============================================================================
# FOLLOW-UP (2026-07-22b): "made code changes, re-ran preanalyze, re-indexed
# under a NEW index name, STILL stuck at ~4 docs, index has the SAME 9337
# chunks as before." There are TWO SEPARATE causes here — do not conflate:
# ============================================================================
#
# CAUSE A = THE STALL (why it stops at ~4). This is the SAME root cause as the
#   section at the top of this file: the 120-min per-run indexer execution quota
#   + auto-heal re-stamping not-yet-reached docs and RESETTING the high-water
#   mark. A brand-new index name does NOT change this: the new indexer starts
#   fresh, chews through the same first ~4 heavy docs, hits the 120-min quota,
#   and — if auto-heal is still stamping the unreached docs to lastModified=NOW —
#   resets to the same 4 forever. THIS IS NOT A PRE-ANALYZE PROBLEM. The fix is
#   still FIX PART 1-3 at the top (turn OFF auto-heal for the backfill; scale the
#   plan up/out; clear the embedding 429 throttle), then let the indexer run
#   repeatedly so it ADVANCES the high-water mark past 4 -> 8 -> ... -> 48/48.
#
# CAUSE B = WHY YOUR CODE EDITS DID NOT SHOW UP (why the chunks are identical).
#   preanalyze bakes the fully-enriched records into _dicache/<pdf>.output.json;
#   the process-document skill only SERVES that cache (no live rebuild). In the
#   default --incremental mode, preanalyze SKIPS any PDF that already has an
#   output.json (_is_pdf_done returned True on processing_status="ok"). So after
#   you change enrichment/emitter code and re-run --incremental, the cache is NOT
#   rebuilt and the indexer replays the OLD output.json -> identical chunks, your
#   edits absent. (Independent of Cause A; it would happen even with no quota.)
#
#   CODE FIX APPLIED (branch safety-indexing-hardening, scripts/preanalyze.py,
#   _is_pdf_done): the incremental "done" check now compares the cached
#   `skill_version` against the current shared.ids.SKILL_VERSION and returns
#   NOT-done (rebuild) on a mismatch. Rebuild is CHEAP — phase_di reuses the DI
#   cache and _vision_one_figure reuses each cached vision sidecar, so only
#   output.json is re-assembled with the current code (no re-DI, no re-vision).
#
#   TO ACTIVATE (Srikanth decides/runs; do NOT auto-run): SKILL_VERSION is an
#   env var (shared/ids.py, default "1.0.0"). Bump it to a new value in the
#   environment where preanalyze runs (e.g. SKILL_VERSION=1.1.0 in the Jenkins
#   preanalyze env / app settings), then run the NORMAL --incremental preanalyze.
#   It will log "stale-version <pdf> ... -> rebuild output.json" per PDF and
#   rebuild all caches cheaply. Then re-index. From now on: bump SKILL_VERSION
#   whenever you change enrichment output, and incremental auto-rebuilds — no
#   more 12-13h --force, no more silently-ignored edits.
#
# ---- COPILOT: please CONFIRM these (Azure creds needed; read-only unless noted) ----
#   Q1. AUTO_HEAL_ENABLED on the function app right now (must be false for the
#       backfill). Report the value.
#         az functionapp config appsettings list -g <FUNC_RG> -n <FUNC_APP> \
#           --query "[?name=='AUTO_HEAL_ENABLED'].value" -o tsv
#   Q2. Does the NEW indexer ADVANCE across runs or RESET to 4? Report the
#       indexer's lastResult (itemsProcessed, finalTrackingState/high-water) from
#       TWO consecutive runs so we can see 4 -> 8 (advancing) vs 4 -> 4 (reset).
#         az search ... GET /indexers/<INDEXER>/status?api-version=2024-05-01-preview
#   Q3. The indexer's lastResult.errorMessage + warnings[]: still
#       "execution time quota of 120 minutes ... processed 4 documents"? Any
#       embedding 429 / "rate limited" / throttling warnings? (points to TPM.)
#   Q4. In the _dicache/ prefix: how many "*.output.json" blobs exist, their
#       lastModified timestamps, and the `skill_version` field inside 2-3 of
#       them. This tells us (a) whether preanalyze actually rebuilt with the
#       current code or skipped stale caches, and (b) how many docs even HAVE a
#       cache (if it's ~4, preanalyze itself is dying past doc 4 — a THIRD issue).
#         az storage blob list -c <PDF_CONTAINER> --prefix "_dicache/" \
#           --num-results * --query "[?ends_with(name,'.output.json')].{n:name,m:properties.lastModified}" -o table
#
# WHAT THE ANSWERS DECIDE:
#   - Q1=true            -> auto-heal is still resetting the queue; that's why a
#                           fresh index still pins at 4. Set it false, retry.
#   - Q2 shows 4 -> 4    -> queue is being reset (heal or a metadata bump). Find
#                           what stamps lastModified=NOW during the run.
#   - Q2 shows 4 -> 8    -> nothing is broken; it just needs MORE runs (or scale
#                           up per FIX PART 2) to reach 48. Not stuck — slow.
#   - Q3 shows 429s      -> raise the embedding deployment TPM (biggest speedup).
#   - Q4 output.json ~4  -> preanalyze is crashing past doc 4 (send the preanalyze
#                           log tail for the first un-cached doc). Separate fix.
#   - Q4 old skill_version + your edits missing -> Cause B confirmed; bump
#                           SKILL_VERSION and re-run incremental preanalyze.
# ============================================================================

# ============================================================================
# REPOSITORY CLEANUP FOR CLIENT HANDOVER  (Copilot: delete these files)
# ============================================================================
# These files are NOT used by the pipeline (verified: 0 code references — they
# are internal dev/analysis artifacts or diagnostic scripts). Safe to delete
# before handing the repo to the client. The KEEP list below must NOT be touched.
#
# COPILOT: delete exactly these files (git rm), commit, push:
#
#   # --- redundant / internal docs (dev analysis, duplicates — not client-facing) ---
#   docs/REAL_WORLD_BUGS_CHECKLIST.md
#   docs/CONTENT_COMPLETENESS_AUDIT.md
#   docs/RETRIEVAL_QUALITY_ANALYSIS.md
#   docs/SPEC_COVERAGE_MAP.md
#   docs/INDEXING_IMPLEMENTATION_STATUS.md
#   docs/SAFETY_RAG_ACTION_PLAN.md
#   docs/SAFETY_CRITICAL_RAG_DESIGN.md
#   docs/CHATBOT_SCENARIOS.md
#   docs/WHATS_NEW_IN_INDEX.md
#   docs/GOTCHAS_AND_FIXES.md
#   docs/INDEXING_FINAL_SPEC.md
#   docs/INDEXING_RUNBOOK.md            # redundant with docs/RUNBOOK.md
#   docs/CHATBOT_INTEGRATION_GUIDE.md   # covered by INDEX_MASTER_GUIDE_FOR_CHATBOT.md
#   docs/INTEGRATION_GUIDE.md           # covered by INDEX_MASTER_GUIDE_FOR_CHATBOT.md
#   docs/INDEXING_ANSWERS_FOR_CHATBOT.md
#   docs/INDEX_CAPABILITIES_FOR_CHATBOT.md
#   docs/INDEX_FIELD_REFERENCE.md       # covered by INDEX_FIELD_GLOSSARY.md
#   docs/BICEP_RBAC_CHECKLIST.md        # covered by RBAC_LEAST_PRIVILEGE.md
#   ss.md
#   CHATBOT_INTEGRATION.md              # old root duplicate
#
#   # --- diagnostic / dev scripts (0 code refs; NOT invoked by the pipeline) ---
#   scripts/bug_evidence_report.py
#   scripts/diagnose.py
#   scripts/diagnose_403.py
#   scripts/diagnose_highlight.py
#   scripts/diagnose_metadata.py
#   scripts/index_query_guide.py
#   scripts/index_schema_diff.py
#   scripts/inspect_pdf.py
#   scripts/page_coordinate_audit_full.py
#   scripts/validate_index_quality.py
#   scripts/index_quality_report.py
#   scripts/reap_stale_rows.py        # also buggy (deletes by chunk_id, key is id)
#   scripts/run_heal_when_auth_ready.sh
#
# DO NOT DELETE (the pipeline / operations need these):
#   function_app/**                                  (the skills — all of it)
#   search/index.json, skillset.json, indexer.json, datasource.json
#   scripts/: preanalyze, deploy, deploy_search, deploy_function.{sh,ps1},
#             run_pipeline, bootstrap, heal_until_done, reconcile, check_index,
#             assign_roles, mark_current_revisions, preflight, cosmos_writer,
#             pipeline_lock, convert, reset_indexer.{sh,ps1}, force_reindex_blobs.ps1,
#             smoke_test, rerun_failed_docs.ps1, cleanup_environment,
#             audit_all_retrievable_fields, validate_index, validate_page_coordinates,
#             audit_index_production, audit_index_accuracy, verify_new_fields
#   Jenkinsfile, Jenkinsfile.run, Jenkinsfile.deploy
#   requirements.txt, ruff.toml, .github/, .gitignore, README.md, deploy.config.example.json
#   docs to KEEP — ONLY these 4 (+ README.md and updating.md at the root):
#     docs/RUNBOOK.md                        (the one operational doc — README links to it)
#     docs/INDEX_MASTER_GUIDE_FOR_CHATBOT.md (the one chatbot/index guide)
#     docs/INDEX_FIELD_GLOSSARY.md           (the field reference)
#     docs/RBAC_LEAST_PRIVILEGE.md           (the roles/permissions to grant)
# ============================================================================

# ############################################################################
# ##  STOP — READ THIS FIRST. DO NOT AUTO-RUN ANY COMMAND IN THIS FILE.      ##
# ############################################################################
#
# TO COPILOT / ANY AGENT READING THIS FILE:
#   This document is a PLAN for a HUMAN to read and decide on. DO NOT execute
#   commands from it automatically. DO NOT run anything top-to-bottom.
#   The ONLY command that is always safe to run is the READ-ONLY audit:
#       python scripts/audit_index_production.py --config deploy.config.json
#   EVERY other command here (heal_until_done.py, deploy.py, run_pipeline.py,
#   deploy_search.py, the indexer) RE-RUNS THE PIPELINE — preanalyze, re-index,
#   or deploy against a PRODUCTION safety index. A human (Srikanth) must
#   explicitly approve each of those before it is run. When in doubt, STOP and
#   ask. Do not "apply the fixes" or "run the steps" without being told to.
#
# TO THE HUMAN: the code fixes below are a to-do list. Reading them changes
# nothing. Applying them is a separate, deliberate step done later. Right now
# the only thing you need to run is the read-only audit above.
# ############################################################################

# ============================================================================
# PART 1 — CODE FIXES FOR COPILOT (index quality remediation)
# ============================================================================
#
# CONTEXT: This is a SAFETY-CRITICAL RAG index (electric live-wire manuals).
# A 45-agent cross-checked audit + a full-index run of
# `scripts/audit_index_production.py` produced verdict = FAIL
# (4 critical, 13 high, 97 medium over 210,985 records, 100% coverage).
# Coverage and vector presence PASSED. The failures are field-quality issues.
#
# HOW TO USE THIS DOC (Copilot): Work top-down. Each TASK has: the file(s), a
# grep anchor to find the exact spot, the problem, the exact change, and an
# ACCEPTANCE check. Do NOT change line numbers blindly — grep for the quoted
# token. After a batch, re-run the audit (bottom of Part 1) and confirm the
# target finding category drops. Ask Srikanth before any change marked
# [DECISION]. Do the [SAFE] tasks first — they are mechanical and low-risk.
#
# ----------------------------------------------------------------------------
# ALREADY DONE — do NOT redo (in commit b0c1c8c on branch
# safety-indexing-hardening; run `git pull` first):
#   - scripts/audit_index_production.py  (the audit command itself — NEW)
#   - run_pipeline.py    : added the real --trigger-indexer flag
#   - check_index.py     : removed a pasted duplicate that made it a SyntaxError
#   - deploy_search.py   : rejects a non *.openai.azure.us embedding endpoint
# ----------------------------------------------------------------------------
#
# ============================================================================
# STEP 1 — TRIAGE YOUR ACTUAL CRITICAL/HIGH FINDINGS FIRST
# ============================================================================
# Print your real findings from the report the audit already wrote:
#
#   python -c "import json; d=json.load(open('reports/production_audit.json',encoding='utf-8')); [print(f['severity'].upper(),'|',f['category'],'|',f['message'],'| e.g.',f.get('examples')) for f in d['findings'] if f['severity'] in ('critical','high')]"
#
# Map each finding's `category` to the fix task below:
#
#   category                              -> TASK
#   ------------------------------------- -> -----------------------------------
#   required_missing / retrieval_missing  -> TASK 0 (real data gap — MUST FIX)
#   empty_chunk / placeholder_chunk       -> TASK 0
#   vectors_missing_for_documents         -> TASK 1
#   locator_artifact_retrieval_eligible   -> TASK 2
#   noise_row_retrieval_eligible          -> TASK 2
#   safety_callout_overflag               -> TASK 3
#   safety_callout_never_set              -> TASK 3
#   placeholder_value                     -> TASK 4 (per field)
#   constant_stub_field                   -> TASK 5 (mostly EXPECTED — see list)
#   physical_page_* / non_contiguous      -> TASK 6
#   applies_to_system_is_header_echo      -> TASK 7
#
# The 97 medium are DOMINATED by `constant_stub_field` on already-known stub
# fields (TASK 5) — those are expected and mostly harmless; triage them LAST.
#
# ============================================================================
# TASK 0 [SAFE, HIGHEST PRIORITY] — required/grounding fields empty on some records
# ============================================================================
# The audit flags critical `required_missing::<type>::<field>` or
# `retrieval_missing::<field>` when a retrieval-eligible chunk is missing a
# field it MUST have (source_file, chunk, physical_pdf_page, header_1,
# content_class, retrieval_eligible_reason, etc.). For a safety bot this means a
# chunk that can be served with no citation/page/grounding.
# ACTION:
#   1. From STEP 1 output, note the exact <type>.<field> and the example
#      chunk_id(s).
#   2. Pull those records and find the pattern:
#        python scripts/verify_new_fields.py --config deploy.config.json --source-file <PDF>
#   3. If it is a WHOLE class of records (e.g. every diagram missing X), it is a
#      pipeline emitter bug — find where that record type is built
#      (function_app/shared/: page_label.py=text, process_table.py=table/row,
#      diagram.py=diagram, summary.py=summary) and populate the field.
#   4. If it is a HANDFUL of records, they are likely partial-index failures —
#      re-heal those PDFs:  python scripts/heal_until_done.py --config deploy.config.json
# ACCEPTANCE: re-run audit -> 0 findings of that required_missing/retrieval_missing key.
#
# ============================================================================
# TASK 1 [SAFE] — some documents have NO vectors (vectors_missing_for_documents)
# ============================================================================
# Those PDFs are invisible to semantic search. Cause is almost always a
# per-document embedding failure during indexing (throttling / empty input),
# NOT the endpoint (global vectors passed).
# ACTION: re-run the indexer for the listed source_file(s), then re-heal:
#   python scripts/heal_until_done.py --config deploy.config.json
# If it persists, check the indexer for embedding WARNINGS (see TASK 8).
# ACCEPTANCE: re-run audit -> vectors_missing_for_documents count = 0.
#
# ============================================================================
# TASK 2 [SAFE] — non-retrievable content is retrieval-eligible (leak)
# ============================================================================
# `locator_artifact_retrieval_eligible` = a TOC/index/locator artifact is being
# served as an answer. `noise_row_retrieval_eligible` = a table row graded
# 'noise' is still eligible. Both pollute answers.
# FILES: function_app/shared/page_label.py (is_locator_artifact / status),
#        function_app/shared/table_row_quality.py + process_table.py (row quality).
# CHANGE: where `retrieval_eligible` is computed for a record, force it False
#   when `is_locator_artifact` is True (or content_class == 'locator_artifact'),
#   and for table_row when table_row_quality == 'noise'. Grep for
#   "retrieval_eligible" in those files and add the guard at the assignment.
# ACCEPTANCE: audit -> both categories = 0.
#
# ============================================================================
# TASK 3 [DECISION+SAFE] — safety_callout over/under-flagging
# ============================================================================
# PROBLEM: safety_callout is TRUE on an implausibly high share of text records
# because the callout regex is case-insensitive and treats NOTE / NOTICE as
# safety callouts. Over-flagging dilutes the `safety-boost` scoring profile so
# real DANGER/WARNING chunks lose their ranking edge.
# FILE: grep for the callout keyword list — likely
#   function_app/shared/content_classifiers.py and/or process_document.py
#   (search: "WARNING", "DANGER", "CAUTION", "NOTICE", "callout").
# CHANGE:
#   - Treat only DANGER / WARNING / CAUTION as SAFETY callouts (these are the
#     ANSI Z535 safety signal words). NOTE and NOTICE are informational —
#     keep classifying them as callouts if you like, but they must NOT set
#     safety_callout = True.
#   - Keep matching case-insensitively for detection, but require the signal
#     word to be a standalone boxed callout token, not a substring.
# [DECISION for Srikanth]: confirm the exact safety-word set for PSEG manuals.
# ACCEPTANCE: audit -> safety_callout is TRUE on a plausible minority (<~20%)
#   of text records; safety_callout_overflag finding gone. Spot-check 10 TRUE
#   rows are genuine WARNING/DANGER.
#
# ============================================================================
# TASK 3b [SAFE] — table rows carry safety_callout but no `callouts` keyword field
# ============================================================================
# PROBLEM: table_row records set safety_callout but never emit the `callouts`
# collection, so a callout-keyword filter/boost misses every table row.
# FILE: function_app/shared/process_table.py (the table_row record dict).
# CHANGE: when a row is a safety callout, also populate `callouts` (and
# `governing_callouts` if applicable) the same way text records do.
# ACCEPTANCE: query rows with safety_callout=true and confirm `callouts` is non-empty.
#
# ============================================================================
# TASK 3c [SAFE] — multi-line boxed callouts truncated to first line
# ============================================================================
# PROBLEM: governing_callouts keeps only the FIRST line of a multi-line boxed
# callout, dropping the actionable clause (e.g. keeps "WARNING" but drops
# "De-energize before servicing").
# FILE: grep "governing_callouts" in function_app/shared/ (content_classifiers.py
#   / process_document.py).
# CHANGE: capture the full callout block (all lines until the box ends), not
#   just the first line. Preserve the verbatim safety text.
# ACCEPTANCE: spot-check a known multi-line WARNING — governing_callouts holds
#   the full text.
#
# ============================================================================
# TASK 4 [SAFE] — placeholder values that pass as "populated"
# ============================================================================
# `placeholder_value` findings = a field holds 'unknown'/'N/A'/'none' etc. The
# main known case is diagram_category defaulting to "unknown".
# FILE: function_app/shared/diagram.py (grep "unknown").
# CHANGE: keep "unknown" only as a true last resort; prefer leaving the field
#   empty OR add a real fallback category so the audit/chatbot can tell
#   "unclassified" from a real class. [DECISION]: confirm desired behavior.
# ACCEPTANCE: audit -> placeholder_value for that field drops.
#
# ============================================================================
# TASK 5 [DECISION] — confirmed STUB fields (source of most of the 97 medium)
# ============================================================================
# These fields are hardcoded constants for some/all record types. They are NOT
# corrupting answers (constants, not garbage), but they advertise capabilities
# the chatbot cannot use. For EACH, pick ONE: (A) wire real data, or
# (B) remove the field from the skillset projection (search/skillset.json) AND
# from search/index.json so the schema stops advertising it. Coordinate with the
# chatbot team + Srikanth before removing anything they read.
#
#   FIELD                                  WHERE HARDCODED                      NOTE
#   -------------------------------------- ------------------------------------ ----------------------------
#   applies_to_system                      page_label/process_table/diagram     echoes header_1/2 — see TASK 7
#   figure_step_linked                     page_label,process_table,summary     real ONLY on diagram
#   figure_linkage_confidence              (same)                               real ONLY on diagram
#   locator_type = 'none'                  all 4 emitters                       real anchor is in figure_ref/table_ref
#   locator_value = ''                     all 4 emitters                       consider deriving from refs
#   chunk_prev_id / chunk_next_id = ''     page_label.py                        documented "reserved"; leave or wire
#   table_variant_id                       process_table.py                     == table_cluster_id (redundant)
#   table_integrity_score                  process_table.py                     constant, not computed (see TASK 6b)
#   embedding_version                      page_label/process_table/diagram     constant; NOT proof vectors landed
#
# RECOMMENDED for a first production pass: remove figure_step_linked/
# figure_linkage_confidence/locator_type/locator_value/table_variant_id from
# the TEXT/TABLE/SUMMARY projections (keep figure_step_linked/confidence on
# diagram where it is real). This makes the schema honest and clears most of the
# 97 medium. Keep chunk_prev/next as documented-reserved.
# ACCEPTANCE: audit -> constant_stub_field count drops to only the fields you
#   deliberately keep.
#
# ============================================================================
# TASK 6 [SAFE] — page-coordinate integrity (physical_pdf_page vs pages list)
# ============================================================================
# `physical_page_not_min_of_list` / `_end_not_max_of_list` /
# `physical_pdf_pages_non_contiguous`: the single-page anchor disagrees with the
# page list, which breaks "jump to the exact page" citations.
# FILE: function_app/shared/page_label.py (grep "physical_pdf_page",
#   "_sanitize_page_span"). CHANGE: ensure physical_pdf_page == min(pages) and
#   physical_pdf_page_end == max(pages), and the list is contiguous, before emit.
# ACCEPTANCE: audit -> those three categories = 0.
#
# TASK 6b [DECISION] — page_width_in/height_in hardcoded 8.5x11 for diagram/table
# ============================================================================
# diagram/table records stamp page_width_in=8.5, page_height_in=11.0 while their
# bboxes are in real-page DI inches. On non-Letter pages this yields wrong crop
# coords for the "show me the figure" feature.
# FILE: function_app/shared/diagram.py, process_table.py (grep "8.5", "11.0",
#   "page_width_in"). CHANGE: read the real page dimensions from the DI page
#   (as text records already do) instead of the constant.
# ACCEPTANCE: diagram/table page_width_in/height_in vary by document, match text
#   records on the same page.
#
# ============================================================================
# TASK 7 [DECISION] — applies_to_system is not a real system tag
# ============================================================================
# It just copies header_1/header_2, so a controlled-vocabulary filter
# (applies_to_system eq 'distribution') never matches. Either add a real
# classify_system() in content_classifiers.py (controlled vocab: distribution/
# transmission/substation/metering/protection/gas-distribution/…) and call it in
# all emitters, OR drop applies_to_system from the routing contract and document
# it as a header alias. ACCEPTANCE: audit -> applies_to_system_is_header_echo = 0.
#
# ============================================================================
# TASK 8 [DECISION] — silent-partial-index hardening (do LAST, needs care)
# ============================================================================
# search/indexer.json has maxFailedItems=10 + failOnUnprocessableDocument=false,
# and embedding failures are indexer WARNINGS — so docs can drop or land with
# null vectors while the indexer reports success. The new audit is now your
# safety net for this, but consider: (a) after each run, alert on indexer
# warning counts; (b) keep audit_index_production.py as a required gate in
# Jenkins (it exits 1 on critical). [DECISION with Srikanth] before lowering
# maxFailedItems, which changes fail behavior.
#
# ============================================================================
# VERIFY LOOP — after each batch of fixes, re-run and drive criticals to 0
# ============================================================================
#   python scripts/audit_index_production.py --config deploy.config.json
#   # GOAL: "VERDICT: PASS" (0 critical, full coverage). Then with --strict to
#   # also clear high findings:
#   python scripts/audit_index_production.py --config deploy.config.json --strict
# NOTE: emitter-code fixes only take effect after the affected PDFs are
# re-preanalyzed + re-indexed (heal_until_done.py or a deploy). Schema/projection
# changes (TASK 5) require re-running deploy_search.py + a full reindex.
#
# ============================================================================
# ============================================================================


# RUN THE INDEXING PIPELINE ON YOUR LAPTOP — step by step

Follow these in order, top to bottom. Commands are for Windows PowerShell. Do not skip a step.

============================================================================
>>> JENKINS PIPELINE FAILED IN "PREFLIGHT" WITH "not found" / "AuthorizationFailed"? DO THIS <<<
============================================================================
SYMPTOM (what you saw in the Jenkins log):
  - Blob soft-delete check:  Storage account 'psegtmstacdevv01' not found
  - Cosmos check:            AuthorizationFailed for SP object
                             d21336b2-a818-4e2c-a8b7-278aa5113fd7 on Microsoft.DocumentDB
  - Preflight is a GATING stage, so every later stage was skipped and the build failed.

ROOT CAUSE (both errors are the SAME problem):
  The Jenkins service principal (SP) d21336b2-a818-4e2c-a8b7-278aa5113fd7 has NO "Reader"
  role, so it cannot even READ resource metadata. "not found" and "AuthorizationFailed" are
  both just the permission being denied. The fix is to grant the SP its least-privilege roles.

  NOTE: `scripts/assign_roles.py` alone does NOT fix this -- its --jenkins-principal-id path
  does not grant "Reader", and "Reader" is exactly what the two failing preflight checks need.
  You must self-grant the roles below.

------------------------------------------------------------
PART A — GRANT THE ROLES (run in VS Code terminal, ONE LINE AT A TIME)
------------------------------------------------------------
Run these as YOURSELF (your admin account that is allowed to create role assignments) --
NOT as the Jenkins SP. The SP cannot grant itself roles.

>>> IMPORTANT: run EXACTLY ONE line at a time. Paste one line, press Enter, wait for it to
>>> finish, THEN do the next. Do NOT paste several lines together -- that is what caused the
>>> "unrecognized arguments" error. If any single line errors, copy that error to Copilot
>>> and ask it to fix that one line.

A1. Set the Azure Government cloud:
      az cloud set --name AzureUSGovernment

A2. Log in as yourself (device-code login -- open the URL it prints and enter the code):
      az login --use-device-code

A3. Point at the DEV subscription:
      az account set --subscription "b41d2ec9-3c69-41f3-8dc7-b1500baeedf1"

A4. Save the subscription id into a variable:
      $sub = az account show --query id -o tsv

A5. Save the Jenkins SP id into a variable:
      $sp = "d21336b2-a818-4e2c-a8b7-278aa5113fd7"

A6. Save the scope into a variable:
      $scope = "/subscriptions/$sub"

A7. Confirm $sub printed a value (should show b41d2ec9-...):
      echo $sub

A8. Confirm $scope printed a value (should show /subscriptions/b41d2ec9-...):
      echo $scope

A9. Grant Reader  <-- THIS is the one that fixes your preflight error:
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Reader" --scope $scope

A10. Grant Website Contributor (deploy the function app code):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Website Contributor" --scope $scope

A11. Grant Search Service Contributor (create/update index, skillset, indexer):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Search Service Contributor" --scope $scope

A12. Grant Search Index Data Contributor (write/query index documents):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Search Index Data Contributor" --scope $scope

A13. Grant Storage Blob Data Contributor (read/write cache blobs):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope $scope

A14. Grant Cognitive Services OpenAI User (embeddings/vision):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $scope

A15. Grant Cognitive Services User (Document Intelligence):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Cognitive Services User" --scope $scope

A16. Grant the Cosmos data role (SEPARATE command -- different API, do not skip):
      az cosmosdb sql role assignment create --account-name psegtmcosmdevv01 --resource-group psegtmrgdevv01 --role-definition-name "Cosmos DB Built-in Data Contributor" --principal-id $sp --scope "/"

A17. Wire up the managed identities (function app + search service). ONE TIME per
     environment. Needs deploy.config.json in the repo root (see STEP 4) and the venv
     activated (see STEP 2). If Srikanth already did this, skip it:
      python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal

A18. Wait ~2 minutes for the roles to take effect, then go to Jenkins and run ACTION=check
     (see PART B below). If storage STILL says "not found" after this, the account name or
     subscription in the config is wrong -- ask Copilot/Srikanth.

Each successful "az role assignment create" prints a JSON block describing the assignment.
If a role already exists you may see "already exists" -- that is fine, it means it is done.
If you see "AuthorizationFailed" on these commands, YOUR account is not allowed to grant
roles -- an Azure admin must run Part A for you.

------------------------------------------------------------
PART B — WHAT TO PICK IN THE JENKINS "ACTION" DROPDOWN
------------------------------------------------------------
  check      READ-ONLY, ~5 min, changes nothing. RUN THIS FIRST after granting the roles --
             it re-runs preflight + coverage and confirms the permission fix worked. Safe default.
  bootstrap  One-time setup (function app + search index/skillset/indexer). Skips preflight.
             Use only if this environment was never set up.
  deploy     FULL + DESTRUCTIVE + long (hours: preanalyze/vision over every PDF + heal). It
             already includes bootstrap. Use for the first full build of the environment.
  run        Routine nightly ops (reconcile -> preanalyze changed docs -> indexer -> heal).

  RECOMMENDED ORDER:  grant roles (Part A)  ->  ACTION=check (verify)  ->  ACTION=deploy
                      (first full setup)     ->  ACTION=run (day-to-day thereafter).

  CHECKBOXES:
    SKIP_TESTS  -> leave UNCHECKED (emergencies only).
    DRY_RUN     -> leave UNCHECKED. Heads-up: it is declared in the Jenkinsfile but NOT wired
                   into any stage, so toggling it currently does nothing. Do not rely on it.

------------------------------------------------------------
THE PERMISSIONS (ROLES) THIS INDEXING PIPELINE NEEDS
------------------------------------------------------------
These are the built-in Azure roles the pipeline uses. All are least-privilege
(data-plane / service-specific) -- NO Owner, Contributor, or User Access
Administrator. They are shared across three identities (the Jenkins pipeline SP,
the Search service identity, the Function App identity).

  Permission (role)                    | What it lets the identity do
  -------------------------------------|-------------------------------------------------
  Reader                               | See/list the resources in the resource group
  Website Contributor                  | Deploy the code to the Function App
  Search Service Contributor           | Create/update the search index, indexer, skillset
  Search Index Data Contributor        | Write and query documents in the search index
  Search Index Data Reader             | Read documents from the search index
  Storage Blob Data Contributor        | Read and write files/cache in the storage account
  Storage Blob Data Reader             | Read files from the storage account
  Cognitive Services OpenAI User       | Call the AI models (embeddings + vision)
  Cognitive Services User              | Call Document Intelligence / AI Services
  Cosmos DB Built-in Data Contributor  | Read/write data in Cosmos DB (run history + state)

(For which identity gets which role on which resource, see docs/BICEP_RBAC_CHECKLIST.md.)

----------------------------------------------------------------------------

============================================================================
>>> ALREADY STARTED AND GOT "'func' is not recognized"? DO EXACTLY THIS <<<
============================================================================
You got far (preflight + preanalyze passed). It only stopped because Azure
Functions Core Tools (the `func` command) is not installed. Fix it and continue:

1. Install func (Azure Functions Core Tools v4):
      winget install Microsoft.Azure.FunctionsCoreTools
   # (or the v4 x64 MSI: https://github.com/Azure/azure-functions-core-tools/releases)
   # (or, if you have Node.js:  npm install -g azure-functions-core-tools@4 --unsafe-perm true)

2. CLOSE the terminal completely, open a NEW one, and confirm func is found:
      func --version
   # must print a 4.x number. If it says "not recognized", the install did not
   # land on PATH -- reopen the terminal again, or use the MSI installer.

3. Go back into the repo folder and re-activate the environment:
      cd <REPO-FOLDER>
      .\.venv\Scripts\Activate.ps1

4. Make sure Azure is still logged in (re-login if it says not):
      az account show -o table
      # if that errors:  az login   then   az account set --subscription "<DEV_SUBSCRIPTION_ID>"

5. Re-run the SAME command -- it is idempotent, it resumes where it stopped
   (preanalyze is already cached, it will jump to deploying the function + indexing):
      python scripts/deploy.py --config deploy.config.json --skip-roles

That's it. If it stops again, read the error and check the troubleshooting section
at the bottom. Everything below is the full setup from scratch (for a fresh laptop).

----------------------------------------------------------------------------

============================================================================
STEP 0 — INSTALL THESE ONCE (skip any you already have)
============================================================================
- Python 3.12 (or 3.11):  https://www.python.org/downloads/     check:  python --version
- Azure CLI (az):          https://aka.ms/installazurecliwindows  check:  az --version
- Git:                     https://git-scm.com/download/win        check:  git --version
- Azure Functions Core Tools v4 (the `func` command -- REQUIRED to deploy the
  function app code):
      winget install Microsoft.Azure.FunctionsCoreTools
      # (or the v4 x64 MSI from https://github.com/Azure/azure-functions-core-tools/releases)
      # (or, if you have Node.js:  npm install -g azure-functions-core-tools@4 --unsafe-perm true)
      check:  func --version     (must print a 4.x version)
(Close and reopen your terminal after installing, so the commands are found.)

NOTE: LibreOffice is NOT required. If preflight prints "[WARN] LibreOffice (optional)"
that is safe to ignore for PDF manuals -- it only affects figure extraction from
.docx/.pptx/.xlsx files, not PDFs.

============================================================================
STEP 1 — GET THE CODE
============================================================================
# if you do NOT have the code yet:
git clone <REPO-URL>
cd <REPO-FOLDER>

# if you ALREADY have the code, just update it:
cd <REPO-FOLDER>
git pull

============================================================================
STEP 2 — PYTHON ENVIRONMENT + DEPENDENCIES
============================================================================
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# (Mac/Linux instead:  source .venv/bin/activate)
# You should now see (.venv) at the start of your prompt.
python -m pip install --upgrade pip
pip install -r requirements.txt

# NOTE: every NEW terminal window, re-run:  .\.venv\Scripts\Activate.ps1

============================================================================
STEP 3 — LOG INTO AZURE (US Government cloud)
============================================================================
az cloud set --name AzureUSGovernment
az login
# ^ a browser opens; sign in with your PSEG work account.
az account set --subscription "<DEV_SUBSCRIPTION_ID>"
az account show -o table
# ^ confirm the Name/Id shown is the DEV subscription.

============================================================================
STEP 4 — PUT THE CONFIG FILE IN PLACE
============================================================================
# Copy deploy.config.json into the ROOT of the repo folder (get the file from Srikanth).
# Confirm it is there:
Test-Path deploy.config.json
# ^ must print: True

============================================================================
STEP 5 — ONE TIME PER ENVIRONMENT: wire the identity roles  (skip if already done)
============================================================================
# This grants the function-app + search managed identities their data roles.
# Only needs to be done ONCE per environment, by an account allowed to grant
# data roles. If Srikanth already did it, SKIP this step.
python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal

============================================================================
STEP 6 — THE SUPER COMMAND  (does EVERYTHING in one go)
============================================================================
python scripts/deploy.py --config deploy.config.json --skip-roles

# What it does, in order:
#   1. deploys the function app code
#   2. preanalyzes every PDF in the blob container (slow on big PDFs -- be patient)
#   3. creates the search index if it does not exist (never deletes an existing one)
#   4. runs the indexer over all documents and waits until they are all done
#   5. sets is_current_revision so the chatbot's currency filter works
#   6. prints a coverage report
# Leave it running to the end. It can take a while the first time.

============================================================================
STEP 7 — CHECK IT WORKED
============================================================================
python scripts/check_index.py --config deploy.config.json --coverage
# ^ shows which PDFs are indexed and how many chunks each has.

============================================================================
LATER — DAILY / INCREMENTAL RUN (only new or deleted PDFs)
============================================================================
python scripts/run_pipeline.py --config deploy.config.json --triggered-by manual
# reconcile new/deleted PDFs -> preanalyze only the new ones -> index the changes
# -> set currency. Safe to run anytime; it never re-does what is already indexed.

============================================================================
IF SOMETHING FAILS — quick fixes
============================================================================
- "unrecognized arguments: --skip-roles"
      -> your scripts are OLD. Re-run STEP 1 (git pull) to get the latest code.
- "AuthorizationFailed" / 403
      -> your Azure account is missing a role on that resource. Tell Srikanth the
         resource name from the error; it is a one-time role grant.
- "config not found: deploy.config.json"
      -> the file is not in the repo root. Redo STEP 4.
- "(.venv) not showing" or "module not found"
      -> you did not activate the venv in this terminal. Run:  .\.venv\Scripts\Activate.ps1
         then  pip install -r requirements.txt  again if needed.
- az command not found
      -> Azure CLI not installed / terminal not reopened. Redo STEP 0.

============================================================================
THE WHOLE THING AS A COPY-PASTE BLOCK (after Step 0 is done once)
============================================================================
cd <REPO-FOLDER>
git pull
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
az cloud set --name AzureUSGovernment
az login
az account set --subscription "<DEV_SUBSCRIPTION_ID>"
# make sure deploy.config.json is in this folder, then:
python scripts/deploy.py --config deploy.config.json --skip-roles
python scripts/check_index.py --config deploy.config.json --coverage
