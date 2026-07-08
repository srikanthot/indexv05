Hey Copilot — I've manually pasted an updated set of indexing files into this repo
(new safety/quality fields, a search schema change with ~28 new index fields, and
blob→index lifecycle fixes). The code changes are already in place. Please run the steps
below **in PowerShell, in order**, and STOP and tell me if any step fails or prints an error.

Notes before you start:
- This IS a search schema change, so the index + skillset get redeployed and the indexer
  reprocesses the documents. The single deploy.py command in STEP 1 handles all of that
  (RBAC roles, function-code deploy, preanalyze, search artifacts, reset+reindex, heal).
- `search/index.json` and `search/skillset.json` contain placeholder tokens like
  `<INDEX_NAME>` and `<AOAI_ENDPOINT>` — do NOT edit them; deploy_search.py fills them
  automatically from deploy.config.json.
- We are on Azure US Government + Azure AI Foundry (modelProvider: foundry). Don't switch
  anything to a classic Azure OpenAI resource.
- Run each python command on its OWN line. Do NOT chain them with bash `&&` / `$LASTEXITCODE`
  — that produced "-ne is not recognized" errors last time.

# ---------------------------------------------------------------------------
# STEP 0 — confirm the pasted files are clean (catches copy/encoding corruption)
# ---------------------------------------------------------------------------
python tests/test_content_classifiers.py
python tests/test_procedures.py
python tests/test_bbox_precision.py
python tests/test_unit.py
# Expected: the first three print "ALL PASSED"; test_unit prints "Results: 291/294 passed".
# If ANY of them crash on import (e.g. ImportError, or a ValueError about str.maketrans),
# a file did not copy cleanly — tell me which file and stop. Do NOT deploy until this is green.

# ---------------------------------------------------------------------------
# STEP 1 — the full deploy (this is the "super command"). It does, in order:
#   1) Bootstrap: assign RBAC roles + DEPLOY THE FUNCTION CODE + set app settings
#   2) Preanalyze the documents (DI + vision cache)
#   3) Deploy the search index + skillset (the schema change)
#   4) Reset + run the indexer (reprojects all docs so the new fields populate)
#   5) Heal loop until done
#   6) Check index coverage
# ---------------------------------------------------------------------------
python scripts/deploy.py --config deploy.config.json --auto-fix
# This runs for a while (RBAC has a ~5 min propagation wait; preanalyze + indexing take
# longer on big PDFs). Let it finish. If the "Deploy Function App code" sub-step fails on a
# benign PowerShell warning, tell me — there is a known one-line fix for deploy_function.ps1.
#
# If the documents were ALREADY preanalyzed (cache built) and you only want to reproject
# them with the new code + schema WITHOUT re-running the expensive vision pass, use instead:
#   python scripts/deploy.py --config deploy.config.json --auto-fix --skip-preanalyze

# ---------------------------------------------------------------------------
# STEP 2 — run the quality gates. Must PASS before we trust the index.
# ---------------------------------------------------------------------------
python scripts/validate_index_quality.py --config deploy.config.json
# Expected final line: "RESULT: PASS". If it prints "RESULT: FAIL", paste me the
# "CRITICAL gates" and "Top problem documents" sections and stop.

# When everything is green, tell me the coverage summary from STEP 2
# (applicability coverage % + record counts) and the check_index coverage from STEP 1.
