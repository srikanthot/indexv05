Hey Copilot — the evidence run worked and it found the REAL bug. Two things: (A) apply a one-command
fix, (B) re-run the evidence so we see Part 3 (it got cut off last time by "First 120" — the bug
verdicts are further down in the file, not missing).

WHAT THE EVIDENCE PROVED
- The fields the backend asked for ARE populated and usable (procedure_step_id/title 100%,
  table_cluster_id/row_index/row_key/columns/cells 100%, figure_number 81%, applies_to_domain 60%,
  criticality 100%, retrieval_eligible 100% true). So "missing fields" is NOT the problem.
- THE REAL BUG: is_current_revision = 0% (True on 0% of records) and document_number = 0% everywhere.
  The backend's own note said "valid docs excluded due to strict filtering" — THIS is it. If the
  chatbot filters `is_current_revision eq true`, it gets ZERO docs, because the currency flag was
  never set. Root cause: the currency pass grouped by document_number (empty), so it skipped every
  record.

I fixed mark_current_revisions.py to fall back to the filename when document_number is empty (each
manual becomes its own family and stays current — safe, hides nothing). Copy the updated file, then:

STEP 1 — copy this updated file to the laptop (Raw copy):
  scripts/mark_current_revisions.py
  scripts/bug_evidence_report.py        (also updated: fixes a step_count display %)

STEP 2 — run the currency pass (PowerShell; do NOT set $ErrorActionPreference='Stop'):
  python scripts/mark_current_revisions.py --config deploy.config.json          # dry run, preview
  python scripts/mark_current_revisions.py --config deploy.config.json --apply   # actually write
Expect: "prepared NNNNNN merge actions" then batches written. This sets is_current_revision=true on
every current manual (all 46 here, since each is a single file).

STEP 3 — confirm it flipped + get the full bug evidence:
  python scripts/bug_evidence_report.py --config deploy.config.json > bug_evidence.txt
  # show the WHOLE file this time (not First 120), and especially Part 3:
  Get-Content bug_evidence.txt
Then paste me bug_evidence.txt IN FULL. I specifically need:
  • PART 1: is_current_revision should now read ~100% (true=~100%) on text + summary.
  • PART 3: the 6 bug verdicts (60014, 61020a, 61020b, 61021, 66009, 67009) — FOUND & POPULATED /
    FOUND-but-empty / NO-CHUNK. (Last run these were below line 120 so they didn't show.)

NOTE on the message to the backend team: for MOST of their 6 bugs the fields already exist and are
populated — once is_current_revision is fixed, their `is_current_revision eq true` filter will stop
excluding everything, which likely clears several "partial" bugs on its own. Send them the Part 3
verdicts and I'll tell you per-bug what (if anything) is a real index change vs a chatbot-query fix
vs a source-content gap.

(document_number being 0% is a separate, lower-priority gap — the manuals may not print a number and
our extractor misses it. The filename fallback means it no longer blocks currency. We can improve
document_number extraction later; it is NOT blocking testing.)
