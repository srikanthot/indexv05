Hey Copilot — I've manually pasted an updated set of indexing files into this repo
(new safety/quality fields, a search schema change with ~28 new index fields, and
blob→index lifecycle fixes). The code changes are already in place. Please run the steps
below **in PowerShell, in order**, and STOP and tell me if any step fails or prints an error.

Notes before you start:
- This IS a search schema change, so the index + skillset must be redeployed and the
  indexer must reprocess the documents (that's what step 2 does).
- `search/index.json` and `search/skillset.json` contain placeholder tokens like
  `<INDEX_NAME>` and `<AOAI_ENDPOINT>` — do NOT edit them; deploy_search.py fills them
  automatically from deploy.config.json.
- We are on Azure US Government + Azure AI Foundry (modelProvider: foundry). Don't switch
  anything to a classic Azure OpenAI resource.

# ---------------------------------------------------------------------------
# STEP 0 — confirm the pasted files are clean (catches copy/encoding corruption)
# ---------------------------------------------------------------------------
python tests/test_content_classifiers.py
python tests/test_procedures.py
python tests/test_bbox_precision.py
python tests/test_unit.py
# Expected: the first three print "ALL PASSED"; test_unit prints "Results: 291/294 passed".
# If ANY of them crash on import (e.g. a ValueError about str.maketrans), a file did not
# copy cleanly — tell me which file and stop.

# ---------------------------------------------------------------------------
# STEP 1 — publish the updated function code (the new custom-skill emitters must run
#          server-side, otherwise the indexer runs the OLD skill code)
# ---------------------------------------------------------------------------
$cfg = Get-Content deploy.config.json | ConvertFrom-Json
Push-Location function_app
func azure functionapp publish $cfg.functionApp.name --python --build remote
Pop-Location
# Expected: 8 functions registered. If it prints 0 functions or a build error, stop.

# ---------------------------------------------------------------------------
# STEP 2 — deploy the updated search index + skillset (schema change) and reindex.
#          deploy.py --skip-bootstrap runs: preanalyze (incremental) -> deploy_search
#          -> reset indexer (resetdocs) -> heal loop until done.
# ---------------------------------------------------------------------------
python scripts/deploy.py --config deploy.config.json --skip-bootstrap
# If the documents were ALREADY preanalyzed (cache built) and you only want to reproject
# them with the new code + schema, add --skip-preanalyze:
#   python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze

# ---------------------------------------------------------------------------
# STEP 3 — run the quality gates. This must PASS before we trust the index.
#          Exits non-zero (fails) if any critical gate fails.
# ---------------------------------------------------------------------------
python scripts/validate_index_quality.py --config deploy.config.json
# Expected final line: "RESULT: PASS". If it prints "RESULT: FAIL", paste me the
# "CRITICAL gates" and "Top problem documents" sections and stop.

# When all three steps are green, tell me the coverage summary line from step 3
# (applicability coverage % and record counts).
