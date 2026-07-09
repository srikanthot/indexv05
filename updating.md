Hey Copilot — the backend/frontend team sent an "indexing work request" (the "17 bugs" note)
asking us to ADD ~28 fields (procedure_id, procedure_step_*, applies_to_*, hazard_class,
criticality, prohibitions, governing_callouts, table_cluster_id, table_row_index, table_row_key,
table_columns, processing_status, retrieval_eligible, is_current_revision, etc.) plus
"parsed numeric value+unit", a "manual catalog", and "synonym maps".

IMPORTANT: every one of those ~28 fields ALREADY EXISTS in the index schema. So this is NOT a
"build fields" job. The real question is whether those fields are POPULATED with real data on the
chunks that matter — a schema is not proof. So we answer with EVIDENCE, not another document.

I added scripts/bug_evidence_report.py. Please RUN it against the live index and paste me the FULL
output. It produces exactly what the backend team asked us to share:
  PART 1  field completeness  — fill-rate % + a real example value, per record_type
  PART 2  manual catalog      — lists every manual (proves "list all manuals" already works)
  PART 3  per-bug evidence     — runs the 6 bug queries (60014, 61020a, 61020b, 61021, 66009,
                                 67009) and prints a VERDICT for each: FOUND & POPULATED /
                                 FOUND-but-fields-empty / NO-CHUNK (source gap)
  PART 4  run header           — index name, doc count, index_run_id (reproducible + stamped)

Run in PowerShell (do NOT set $ErrorActionPreference='Stop'):

python scripts/bug_evidence_report.py --config deploy.config.json > bug_evidence.txt
Get-Content bug_evidence.txt | Select-Object -First 120 | ForEach-Object { Write-Host $_ }

Then paste me bug_evidence.txt in full. That output is our reply to the backend team. Based on it:
  • Bugs that come back FOUND & POPULATED  -> tell backend to USE the fields (expand by
    procedure_id, read table_row_cells, show governing_callouts) — not an index change.
  • Bugs that come back FOUND-but-fields-empty -> those are the REAL coverage gaps; I'll fix the
    enrichment/parse and reindex just those.
  • Bugs that come back NO-CHUNK -> confirm the manual even contains it; adding fields can't create
    content that isn't in the source.

If the script errors on auth, do `az login` first (US Gov). If PART 1 shows is_current_revision
mostly null, the currency post-pass (mark_current_revisions.py) hasn't run — tell me and I'll give
the one command.
