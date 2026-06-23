Continue from the indexing fixes you already completed.

You said the fixes are completed, including the citation/bbox/highlight fixes. Now I want a final post-fix production-readiness audit.

This is only for the indexing repository.

Please do not restart the old audit from the beginning. Use the changes you already made and inspect the current final code state.

My current thinking is that we should verify the areas below, but please do not limit yourself only to my checklist. You know the repository and the changes you made. If there are better checks, additional risks, missing scenarios, or implementation-specific validations I did not mention, please add them and include them in the final report.

Do not assume something is fixed just because it was changed. Please verify from actual code, schema, skillset, indexer config, scripts, generated outputs, and available validation commands.

Important:

* Do not modify .env files.
* Do not expose secrets.
* Do not run destructive production Azure commands.
* Do not directly change production resources.
* Safe repo inspection, local validation, dry-run validation, and report generation are okay.
* If any command requires production access, secrets, or destructive Azure actions, stop and tell me the command I should run manually.
* If you find a small safe issue, you can fix it.
* If the issue is risky or requires production/index rebuild decisions, report it clearly instead of blindly changing it.
* Do not force exact file names if the implementation uses different names. Map my checklist to the actual files/scripts/artifacts in this repo.

==================================================
WHAT I AM TRYING TO CONFIRM
===========================

My goal is to confirm whether the indexing pipeline is now production-ready for technical-manual RAG indexing.

The main thing I want to know is:

1. Did we really capture the document data correctly?
2. Did we avoid silent partial indexing?
3. Did we handle edited/deleted/renamed/new PDFs safely?
4. Did we fix stale cache and stale index record risks?
5. Did we fix table split and table row issues?
6. Did we fix diagram/OCR indexing issues?
7. Did we fix citation metadata and bbox/highlight metadata issues?
8. Did we create enough validation/reporting to prove the index is healthy?
9. Did we make weekly indexing safer?
10. Is there anything still missing before we can call this production-ready?

Please verify these, and also add anything else you believe is important.

==================================================
SECTION 1 — VERIFY FIXES YOU MADE
=================================

Please list every meaningful fix you made and verify whether it is fully implemented, partially implemented, or still risky.

Include:

* files changed
* new files added
* schema changes
* skillset changes
* indexer changes
* Python code changes
* documentation changes
* validation/reporting changes
* tests/smoke scripts added
* commands available now

For each fix, say:

* implemented / partial / missing / needs verification
* how you verified it
* risk if it is still incomplete
* whether it requires index rebuild
* whether it requires full reindex

==================================================
SECTION 2 — COVERAGE / VALIDATION / REPORTING
=============================================

My thinking is that we should have some kind of final validation/reporting that proves indexing quality. It does not have to use my exact file names, but verify the equivalent exists.

Please verify:

1. Is there a coverage report?
   Examples:

   * coverage_report.json
   * coverage_report.md
   * equivalent report/script/output

2. Does it include:

   * total source PDFs
   * total indexed documents
   * total indexed records
   * records by record_type
   * text count
   * table count
   * table_row count
   * diagram/image count
   * summary count
   * documents with zero records
   * failed/partial documents
   * stale/deleted source references
   * duplicate/renamed/edited PDF candidates
   * records missing source_file
   * records missing page metadata
   * records missing citation metadata
   * records missing processing status
   * tables without row records
   * diagrams without OCR/description
   * bbox/citation completeness checks
   * blockers/warnings/info classification
   * final pass/fail result

3. Is there a coverage gate?

   * Does it return non-zero exit code on blockers?
   * Can it be used in Jenkins or local validation?
   * Does it separate blockers from warnings?
   * Are thresholds configurable or clearly documented?

4. Does it handle large indexes?

   * Does it paginate through all Azure Search results?
   * Does it avoid reading only top 1000 records?
   * Does it handle growing document volume?

5. Does it have dry-run mode where needed?

   * reconcile dry-run
   * stale cleanup dry-run
   * delete/mark stale dry-run
   * no destructive action by default

Please also add any coverage/validation checks you think are missing.

==================================================
SECTION 3 — DOCUMENT LIFECYCLE / WEEKLY UPDATE SAFETY
=====================================================

The documents are not static. New PDFs will be added, old PDFs may be edited, renamed, or deleted.

Please verify the current implementation for these scenarios:

1. New PDF added
   Expected:

* detected as new
* processed
* indexed
* included in coverage report

2. Existing PDF edited with same filename
   Expected:

* content change detected
* source_hash or equivalent changes
* old cache not reused incorrectly
* old index records detected/replaced/marked stale
* new records validated

3. Existing PDF renamed
   Expected:

* rename/duplicate candidate detected if same content hash
* no duplicate active records if current design supports active/stale
* otherwise clearly reported

4. PDF deleted
   Expected:

* missing source detected
* stale index records reported
* cleanup available in dry-run
* stale citations risk visible

5. Duplicate PDF uploaded
   Expected:

* duplicate source_hash or equivalent detected
* duplicate behavior documented

6. Same manual new revision
   Expected:

* revision metadata captured if available
* current/stale version behavior documented
* if not implemented, mark NEEDS VERIFICATION

7. Indexing run fails halfway
   Expected:

* partial run is visible
* bad run is not considered production-ready
* old good data remains safe if staged/active/rollback exists
* otherwise risk is clearly documented

8. Large PDF changes only in some pages
   Expected:

* current behavior documented
* full reprocess vs partial reprocess behavior clear
* stale cache/index risk handled or reported

Please report each scenario as:

* PASS
* PARTIAL
* FAIL
* NEEDS VERIFICATION

Also include any other lifecycle scenario I missed.

==================================================
SECTION 4 — SOURCE HASH / CACHE / STALE RECORDS
===============================================

Please verify:

1. Do we now track source_hash or equivalent?
2. Do we track source_last_modified or equivalent?
3. Do we track source_path and source_file consistently?
4. Do we track document_id or equivalent stable identity?
5. Do we track index_run_id or equivalent?
6. Do we track document_status / processing_status / failure_reason?
7. Do we have active/staged/stale behavior or at least report-first readiness?
8. Does cache invalidation use content hash or equivalent?
9. Is stale cache detected?
10. Are stale index records detected?
11. Is stale cleanup safe and dry-run-first?
12. Can old citations point to stale/deleted content after fixes?

Add your own checks if the implementation uses a different design.

==================================================
SECTION 5 — TABLE READINESS
===========================

Please verify final table indexing behavior.

My concerns were:

* big tables split into unrelated chunks
* table rows tied only to split 0
* small 1–4 row tables skipped even if important
* missing table page range
* missing full logical table identity
* missing table citation metadata
* table highlighting wrong or incomplete

Please verify:

1. table_cluster_id or equivalent exists and is populated.
2. table_split_index and table_split_count or equivalent exist for split tables.
3. All splits from one logical table are linked.
4. table_row records link to the logical table.
5. table_row records preserve row order/index.
6. Meaningful small tables produce table_row records or are intentionally classified as layout/noise.
7. Large tables can be reconstructed as one logical table.
8. table_page_start/table_page_end or equivalent page range exists.
9. table caption/title/header/unit metadata is preserved where available.
10. table records have citation metadata.
11. table_row records have citation metadata.
12. table bbox exists where available.
13. row-level bbox exists if implemented.
14. If row-level bbox is not implemented, fallback behavior is documented.
15. Coverage/reporting flags table records missing required metadata.

Please also test/think through:

* huge table
* split table
* table across multiple pages
* table with repeated headers
* table with merged cells
* table with blank cells
* table image
* small configuration/rating table
* checklist table

Report:

* PASS / PARTIAL / FAIL / NEEDS VERIFICATION
* remaining risk
* validation command/test if available

==================================================
SECTION 6 — DIAGRAM / IMAGE / OCR READINESS
===========================================

Please verify final diagram/image/OCR indexing behavior.

My concerns were:

* diagram OCR not searchable
* wiring labels/nameplates not captured
* figures across multiple pages silently truncated
* embedded images missed by Document Intelligence
* diagram bbox/highlight shifted
* table images treated only as normal image prose

Please verify:

1. diagram_ocr_text / ocr_text or equivalent exists.
2. Diagram OCR is emitted by processing code.
3. Diagram OCR is projected into Azure Search.
4. Diagram OCR is searchable.
5. Diagram OCR is retrievable.
6. diagram_description exists and is populated.
7. figure_ref exists where available.
8. diagram_category exists where available.
9. diagram_pages/page_start/page_end exists.
10. multi_page_figure detection exists.
11. multi-page diagrams are not silently treated as one first-page-only figure.
12. embedded images missed by Document Intelligence are detected/reported, if not fully extracted.
13. table images are detected/reported, if not fully structured.
14. bbox covers actual diagram region.
15. rotated/landscape page bbox is handled or flagged.
16. coverage/reporting flags diagrams missing OCR/description/bbox/page metadata.

Please also include any diagram/image risks you think are still missing.

==================================================
SECTION 7 — CITATION / BBOX / HIGHLIGHT FINAL CHECK
===================================================

This is very important.

Current chatbot behavior:
The chatbot answer shows citations below the response. If it shows 2 citations, those should be the supporting source chunks used for the answer. If it shows 5 citations, those should be the supporting source chunks. When user clicks a citation, the PDF should open to the correct page and highlight the correct area.

Indexing-side responsibility:
The index must store clean citation and bbox metadata so downstream code can render the right PDF/page/highlight.

Please verify:

1. Do all citation-capable record types have citation metadata?

   * text
   * table
   * table_row
   * diagram
   * summary

2. Does each record have:

   * chunk_id or id
   * record_type
   * source_file
   * source_path
   * source_hash if implemented
   * page_start
   * page_end
   * physical_pdf_page if available
   * printed_page_label if available
   * bbox or bounding regions if available
   * parent_id if applicable
   * table_ref if applicable
   * figure_ref if applicable
   * table_cluster_id if applicable

3. Text chunk bbox:

   * Is bbox available for full text chunk?
   * If chunk has multiple lines, does bbox cover all lines or only first line?
   * If chunk spans multiple pages, is bbox stored per page?
   * Is there bbox_pages or equivalent?
   * Are text span offsets stored or available?
   * Is bbox_source documented?

4. Table bbox:

   * Does table bbox cover the table?
   * Does split table bbox cover only the split or the full table?
   * Does table_row have row-level bbox?
   * If not, is table-level bbox fallback documented?
   * Does large table citation avoid highlighting unrelated table area?

5. Diagram bbox:

   * Does bbox cover the actual diagram/crop?
   * Does bbox work for landscape/rotated pages?
   * Does bbox handle multi-page figures?
   * Is half-highlight/shifted highlight risk fixed or flagged?

6. Known highlight bugs to verify:

   * only one line highlighted for a multi-line chunk
   * only 2–3 lines highlighted for a larger chunk
   * correct page but wrong area
   * highlight shifted left/right
   * highlight shifted up/down
   * image/diagram half highlighted
   * table header highlighted instead of body
   * table_row highlights full table when row expected
   * index/TOC page citation highlights only one line
   * chunk spanning multiple pages highlights only first page
   * rotated/landscape page highlight offset
   * physical PDF page differs from printed page label

7. Please answer directly:

   * Are citation/bbox fixes fully implemented?
   * What is still not guaranteed?
   * Is highlight accuracy now indexing-safe?
   * Which remaining highlight issues are indexing-side?
   * Which remaining highlight issues are downstream viewer/rendering-side?

Also tell me whether missing bbox should be:

* BLOCKER
* WARNING
* INFO

by record type.

==================================================
SECTION 8 — CHUNKING / OCR CONFIDENCE / SUMMARY READINESS
=========================================================

Even if these were not the main fixes, please do a final check.

1. Chunking:

* procedure steps not badly split
* warnings/cautions not separated from steps
* table/figure references keep enough context
* page/section headers included where possible
* chunk_for_semantic has useful context
* cross-page chunks handled or flagged

2. OCR confidence:

* low-confidence pages detected if available
* scanned/rotated/poor quality pages flagged
* needs_review behavior exists or is documented

3. Summaries:

* summary truncation risk for huge manuals documented
* section-level summary need marked P1/P2 if not implemented
* summary records not treated as complete coverage evidence

Add anything else you think is important here.

==================================================
SECTION 9 — FINAL PRODUCTION SCENARIO TEST MATRIX
=================================================

Please run or at least reason through a final scenario matrix.

Include:

1. New PDF
2. Edited same-name PDF
3. Deleted PDF
4. Renamed PDF
5. Duplicate PDF
6. New revision
7. Large PDF
8. Failed/partial document
9. Big split table
10. Small critical table
11. Multi-page table
12. Table image
13. Wiring diagram
14. Nameplate image
15. Multi-page diagram
16. Rotated/landscape diagram
17. Low OCR page
18. Citation text chunk highlight
19. Citation table highlight
20. Citation table_row highlight
21. Citation diagram highlight
22. Stale citation after delete/update
23. Coverage gate blocker
24. Coverage gate warning
25. Pagination beyond 1000 records
26. Dry-run cleanup
27. Overlapping run/lock scenario
28. Cache/source hash mismatch
29. Schema/projection mismatch
30. Full reindex/index rebuild impact

For each scenario:

* expected behavior
* current status: PASS / PARTIAL / FAIL / NEEDS VERIFICATION
* evidence from code/script/report
* remaining risk

Add more scenarios if you believe I missed important ones.

==================================================
SECTION 10 — FINAL GO / NO-GO DECISION
======================================

Please give one clear final verdict:

Options:

1. Production-ready
2. Production-ready with warnings
3. Dev-ready only
4. Not ready

Do not say production-ready unless the evidence supports it.

Use this rule:
Production-ready means:

* no silent partial indexing
* lifecycle/stale detection works
* coverage gate works
* table coherence is implemented or safely gated
* diagram OCR is implemented or safely gated
* citation/bbox metadata is reliable enough
* stale records are detectable and cleanup is safe
* required-field validation passes
* rollback/staged/active risk is handled or explicitly accepted
* reindex/index rebuild impact is understood
* remaining warnings are not data-loss/stale-citation/highlight-breaking issues

==================================================
FINAL OUTPUT FORMAT
===================

Please produce the final post-fix report in this structure:

A. Executive summary
B. Final readiness verdict
C. Your own additions beyond my checklist
D. Fixes verified as complete
E. Fixes verified as partial
F. Fixes still missing
G. Files inspected
H. Files changed
I. New files added
J. Schema changes made
K. Skillset/indexer changes made
L. Python code changes made
M. Documentation changes made
N. Coverage report/gate status
O. Lifecycle/stale-data status
P. Cache invalidation status
Q. Table readiness status
R. Diagram/OCR readiness status
S. Citation/bbox/highlight readiness status
T. Chunking/OCR confidence/summary status
U. Scenario matrix results
V. Commands to run
W. Dry-run command
X. Coverage report command
Y. Coverage gate command
Z. Smoke validation commands
AA. What requires index rebuild
AB. What requires full reindex
AC. Rollback steps
AD. Remaining risks
AE. Remaining P1/P2 items
AF. NEEDS VERIFICATION items
AG. Final go/no-go recommendation

Final instruction:
Please use my checklist as a starting point, but add your own checks and concerns based on the actual repository and the fixes you made. Do not limit the final audit only to what I listed.
