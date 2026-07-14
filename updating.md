Copilot — wire the indexing Jenkins pipeline to these commands. Roles are handled ONCE, manually
(outside the pipeline), so every pipeline ACTION runs with `--skip-roles` and needs NO User Access
Administrator / Contributor on the Jenkins SP.

============================================================================
ONE-TIME, MANUAL (run from VS Code, NOT in the pipeline) -- do this once per environment
============================================================================
# a) self-grant the Jenkins SP its data/service roles (all self-grantable; no admin):
$sp="6be27496-7668-454b-ac68-1a8bcffac97e"; $sub="b41d2ec9-3c69-41f3-8dc7-b1500baeedf1"; $scope="/subscriptions/$sub"
az role assignment create --assignee $sp --role "Reader"                         --scope $scope
az role assignment create --assignee $sp --role "Search Service Contributor"     --scope $scope
az role assignment create --assignee $sp --role "Cognitive Services OpenAI User" --scope $scope
az role assignment create --assignee $sp --role "Storage Blob Data Contributor"  --scope $scope   # (already done)
az role assignment create --assignee $sp --role "Search Index Data Contributor"  --scope $scope   # (already done)
az role assignment create --assignee $sp --role "Cognitive Services User"        --scope $scope   # (already done)
az cosmosdb sql role assignment create --account-name "<cosmos>" --resource-group "<rg>" `
  --role-definition-name "Cosmos DB Built-in Data Contributor" --principal-id $sp --scope "/"
# b) wire the managed identities (function + search) -- data roles only, your account can do it:
python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal

============================================================================
PIPELINE ACTIONS (all use --skip-roles; pick via the ACTION parameter)
============================================================================

ACTION = full        # first time, config change, or index-schema change
  python scripts/deploy.py --config deploy.config.json --skip-roles
  # bootstrap(function code + app settings, roles skipped) -> preanalyze ALL (incremental)
  #   -> deploy_search (PUT: create index if missing, update if exists, NEVER drops data)
  #   -> reset+run indexer -> heal until 100% -> mark_current_revisions -> coverage.
  # Add --force-preanalyze ONLY when you changed preanalyze/DI logic (rebuilds the OCR cache).

ACTION = deploy-function   # you changed function/enrichment code
  python scripts/deploy.py --config deploy.config.json --skip-roles --skip-preanalyze
  # deploy new function code + (re)deploy artifacts (safe PUT) -> reset+run indexer so docs
  #   are RE-ENRICHED with the new code -> heal -> currency. NO preanalyze (OCR cache unaffected).
  # If the code change is trivial (no enrichment impact) and you do NOT want a reindex, use:
  #   python scripts/bootstrap.py --config deploy.config.json --skip-roles --skip-search-artifacts --skip-cosmos --skip-app-settings --skip-smoke-test

ACTION = nightly     # the cron (02:00) + daily incremental
  python scripts/run_pipeline.py --config deploy.config.json --triggered-by nightly
  # reconcile (detect NEW / edited / DELETED PDFs) -> preanalyze ONLY new -> run indexer on
  #   changes (deletes removed docs, guarded by MAX_PURGES) -> coverage -> mark_current_revisions
  #   -> optional heal. Does NOT deploy code and does NOT redeploy the index schema.

============================================================================
WHY THIS IS SAFE (no data loss, no overwrite)
============================================================================
- deploy_search uses PUT (create-or-update), NEVER DELETE. Index missing -> created; unchanged ->
  no-op; field ADDED -> updated in place, data kept; INCOMPATIBLE change -> Azure ERRORS (does not
  silently drop). So re-running never wipes the index.
- The indexer MERGES documents (mergeOrUpload) -> never deletes existing records.
- ACTION=nightly never touches the index schema -- only preanalyze + indexer. So the daily run
  cannot overwrite/recreate the index. "We won't lose the one document" -> correct, it can't happen.
- Treat index-SCHEMA changes (rename/retype a field) as a deliberate ACTION=full, never nightly --
  an incompatible schema change is the ONLY thing that needs a drop+rebuild.

============================================================================
NOTES
============================================================================
- Every step is idempotent -- a failed run is safe to re-run; it resumes.
- mark_current_revisions runs after EVERY index (full + nightly) so the chatbot's
  `is_current_revision eq true` filter is always correct (skipping it makes the corpus look empty).
- Only revisit the manual role step if you ADD a brand-new resource (e.g., a 2nd AOAI) -- grant that
  one resource its roles once. Never per run.
- disableConcurrentBuilds on the nightly job so two runs don't collide.
