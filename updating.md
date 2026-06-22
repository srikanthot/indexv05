Continue from the final indexing audit report and implementation report you already produced.

Important context:
This is only the indexing repository.
Do not discuss frontend/backend chatbot code.
Do not discuss UI, chat history, streaming, API response handling, or query-router implementation.
The indexing repo has already indexed documents and produced a final audit/report. Now I want a continuation and final merged plan based on that report plus additional production lifecycle concerns.

Do not start from scratch.
Use your previous indexing audit/implementation report as the baseline.
Do not discard your previous findings.
Merge the previous report with the new weekly document update concerns and produce one final indexing-side roadmap.

Important rules:

* Do not edit files yet.
* Do not apply patches yet.
* Do not change production configs yet.
* Do not modify .env files.
* Do not expose secrets.
* Give only the continuation audit, challenge review, and final merged implementation roadmap.
* Mark uncertain findings as NEEDS VERIFICATION.
* Verify against actual repository files wherever possible.

==================================================
CURRENT SITUATION
=================

This indexing repository is used for a production technical-manual RAG chatbot.

The repository handles PDF/manual indexing using Azure AI Search, Azure Document Intelligence, OCR, custom skills, preanalysis/cache, tables, diagrams/images, summaries, citations, and search index projections.

The previous audit already found important indexing-side issues such as:

* indexer failure tolerance
* possible hidden partial indexing
* table split coherence gaps
* missing or incomplete table_cluster_id / table_split_index / table_split_count
* small table row record gaps
* diagram OCR projection gaps
* multi-page diagram handling gaps
* schema/skillset/projection mismatches
* documentation drift
* summary truncation risk
* huge PDF handling risk
* stale or partial indexing risk
* coverage validation gaps

Now I want to add one more major production concern:

The document set is not static.

PDFs will keep changing every week:

* New PDFs will be added.
* Existing PDFs may be edited and uploaded again.
* Existing PDFs may keep the same file name but have changed content.
* Existing PDFs may be renamed.
* PDFs may be deleted.
* Same PDF may be uploaded twice in different folders.
* Same manual may get a new revision/version.
* Old revision may remain, but new revision should be treated as current.
* Large PDFs may change only in a few pages.
* Indexing may fail halfway.
* Cache may exist from a previous version.
* Old index records may remain after source PDF changes.
* Citations may point to deleted/stale PDF content.
* Weekly indexing may add new files but fail to clean removed files.
* Reindex may happen while old records are still searchable.
* A PDF may appear indexed, but some pages/tables/diagrams/OCR failed silently.

==================================================
WHAT I AM THINKING — PLEASE REVIEW AND CHALLENGE
================================================

My current thinking is:

Before calling this indexing pipeline production-ready, we should make sure it can prove:

1. Every active PDF is detected.
2. Every active PDF has a stable document identity.
3. Every active PDF has a source hash.
4. Every active PDF has source last modified metadata.
5. Every active PDF has document status.
6. Every page has extraction status.
7. Every page produced expected records or is flagged.
8. Tables are captured.
9. Important small tables produce table_row records.
10. Large tables are treated as one logical table even if split into multiple chunks.
11. Every table split and table row can be connected back to the full logical table.
12. Diagrams are captured.
13. Diagram OCR text is searchable/retrievable.
14. Multi-page diagrams are not silently truncated.
15. Table images are not treated only as normal prose if they contain structured data.
16. OCR inside wiring diagrams, labels, nameplates, terminal tags, and callouts is captured.
17. Each record has source file, source path, source hash, page, bbox, record type, and citation metadata.
18. Partial or failed documents are not treated as production-ready.
19. Updated PDFs invalidate old cache and old index records.
20. Deleted PDFs remove active records from the index.
21. Renamed PDFs do not create duplicate stale records.
22. Same PDF with small edits is detected and reprocessed.
23. New revisions can be identified.
24. Old revisions can be marked stale/current appropriately.
25. Coverage report clearly shows what was captured and what was missed.
26. Alerts fire when counts drop, failures happen, stale records remain, or indexing quality drops.
27. Schema, skillset, indexer, Python output contract, README, RUNBOOK, and CHATBOT_INTEGRATION docs are aligned.
28. The pipeline can handle very large PDFs and weekly updates safely.
29. Rollback is possible if a weekly indexing run fails.
30. Production should not trust “indexer succeeded” alone.

Please challenge this thinking:

* Is this approach correct?
* Is anything missing?
* Is any part unnecessary?
* Is there a better indexing-side design?
* What should be done now vs later?
* What requires schema/index rebuild?
* What requires full reindex?
* What can be done without schema change?
* What can be implemented safely as validation/reporting first?
* What must be done before the next production indexing run?

Use language like:

* “Your approach is reasonable, but…”
* “I would challenge this part…”
* “This is likely missing…”
* “This needs verification in code…”
* “An alternative approach is…”
* “I would not implement this yet because…”

==================================================
MERGE PREVIOUS AUDIT + NEW LIFECYCLE CONCERNS
=============================================

Use your previous final indexing audit/implementation report as the baseline.

Now merge the new document lifecycle concerns into that previous report.

Specifically, reconcile previous findings such as:

* indexer failure tolerance
* table_cluster_id / table split coherence
* small table row records
* diagram OCR projection
* multi-page diagram handling
* schema/skillset/projection mismatches
* documentation drift
* coverage validation
* huge PDF handling
* partial indexing

with new weekly-update concerns:

* new PDFs added
* existing PDFs edited
* same filename but changed content
* renamed PDFs
* deleted PDFs
* duplicate PDFs
* new revisions
* stale cache
* stale index records
* stale citations
* rollback if weekly indexing fails
* changed large PDFs
* partial weekly reindex
* full reindex decision rules

Produce one merged final plan.

Do not produce two separate plans.
Do not repeat the previous audit word-for-word.
Instead, create a consolidated final recommendation:

1. What previous P0 items still remain P0.
2. What new lifecycle/update items should be added to P0.
3. What previous P1 items should move to P0 because of weekly updates.
4. What items can stay P1.
5. What items should be postponed.
6. Which items require schema rebuild.
7. Which items require full reindex.
8. Which items can be added safely as validation scripts.
9. Which items must be implemented before the next production indexing run.

==================================================
FINAL AUDIT AREAS TO REVIEW
===========================

1. Document lifecycle and source tracking

Check whether the repo supports:

* new PDF ingestion
* edited existing PDF detection
* same filename but changed content
* same content but renamed file
* renamed file cleanup
* deleted file cleanup
* duplicate file detection
* revision/version detection
* source hash tracking
* source last modified tracking
* index_run_id tracking
* current active version vs old version
* cache invalidation when source changes
* old index record deletion when source changes
* stale record detection
* stale cache cleanup
* rollback if new indexing run fails

Evaluate these fields:

* document_id
* source_file
* source_path
* source_hash
* source_last_modified
* document_revision
* effective_date
* is_current
* index_run_id
* last_indexed_at
* processing_status
* document_status
* failure_reason

Tell me what exists, what is missing, and what should be added.

2. Document/page processing status

Check whether each document and page has clear status:

* ok
* partial
* failed
* skipped
* low_confidence
* needs_review
* unsupported

For large PDFs, we need to know:

* total page count
* processed page count
* failed pages
* pages with zero records
* pages with text only
* pages with tables
* pages with diagrams
* pages with OCR confidence issues

Recommend whether a page-level manifest is required.

3. Page-level manifest

Review whether we should create:
_dicache/<document_id>/manifest.json

Manifest should include:

* source_file
* source_path
* source_hash
* source_last_modified
* file_size
* page_count
* processed_pages
* failed_pages
* low_confidence_pages
* text_chars per page
* tables found per page
* figures found per page
* embedded images found per page
* OCR confidence per page
* records emitted per page
* table records emitted
* table_row records emitted
* diagram records emitted
* summary records emitted
* status per page
* overall document readiness

Challenge this idea:

* Is this enough?
* Is there a better manifest structure?
* Should manifest be stored in Blob, Cosmos, index, or local cache?
* How should manifest be used by validation scripts?
* How should manifest block bad documents from production use?

4. Table capture and logical table coherence

Review current table handling.

Check:

* Are all real tables captured?
* Are small 1–4 row tables captured as table_row records?
* Are large tables split?
* If split, do all splits share one logical identity?
* Is there table_cluster_id?
* Is there table_split_index?
* Is there table_split_count?
* Is there full table artifact?
* Are table rows connected to parent table?
* Are table rows connected to full logical table, not only split 0?
* Are table page ranges correct?
* Are table captions preserved?
* Are table headers preserved?
* Are units preserved?
* Are repeated headers handled?
* Are merged cells handled?
* Are rotated/wide tables handled?
* Are table footnotes captured?
* Are table images converted into structured table records?

My thought:
A big table should not necessarily be one giant embedding chunk. Instead, it should be one logical table object with linked split records and row records.

Please confirm or challenge this.

Expected table design to evaluate:

* table_cluster_id
* table_split_index
* table_split_count
* table_page_start
* table_page_end
* table_caption
* table_title
* table_headers
* table_units
* table_full_markdown
* table_full_json if useful
* table_row records for all meaningful rows
* row_index
* row_text
* row_cells_json
* row_page
* row_bbox if possible

5. Diagram, image, and OCR capture

Review current diagram/image handling.

Check:

* Are diagrams captured?
* Are wiring diagrams captured?
* Are fold-out diagrams captured?
* Are multi-page diagrams captured?
* Are embedded images missed by Document Intelligence?
* Is there secondary image extraction using PyMuPDF or similar?
* Are image hashes too aggressive and deduplicating useful diagrams?
* Is OCR text from diagrams stored?
* Is OCR text from diagrams projected into Azure Search?
* Is diagram OCR searchable?
* Are diagram labels, wire tags, terminal labels, nameplates, equipment IDs searchable?
* Is diagram description stored?
* Is figure_ref stored?
* Is diagram category stored?
* Is nearby page text stored?
* Is bbox stored?
* Is page range stored for multi-page figures?
* Are table images processed as tables?

My concern:
Normal page OCR is not enough. Diagram OCR must be captured separately because important labels may exist only inside images.

Please verify whether this is implemented and what is missing.

Recommended fields to evaluate:

* diagram_ocr_text
* ocr_text
* diagram_description
* diagram_category
* figure_ref
* diagram_pages
* diagram_page_start
* diagram_page_end
* multi_page_figure
* nearby_text
* bbox
* image_hash
* perceptual_hash
* source_file
* source_hash

6. OCR confidence and low-quality pages

Review whether OCR confidence is captured and used.

Check:

* scanned PDFs
* low contrast pages
* rotated pages
* landscape pages
* small font
* handwritten notes
* corrupted pages
* pages with symbols/formulas
* OCR confidence per page
* OCR confidence per record
* low-confidence gating
* needs_review flags

Tell me:

* Should low-confidence pages be indexed?
* Should they be blocked?
* Should they be indexed but flagged?
* How should coverage report show them?

7. Chunking quality

Review current text chunking.

Check:

* Are chunks split by arbitrary characters?
* Are procedure steps split across chunks?
* Are warnings separated from procedure steps?
* Are figure/table references separated from actual table/diagram?
* Are cross-page paragraphs split badly?
* Are headers included in chunk_for_semantic?
* Is previous/next sentence context included?
* Are page numbers and section names included?
* Are formulas/symbols preserved?
* Are bullet lists and step numbers preserved?

We cannot change embedding model, but we can improve chunk construction.

Recommend:

* what should be changed now
* what can wait
* whether sentence-aware chunking is needed
* whether overlap should be changed
* whether chunk_for_semantic should include more context

8. Summary records

Review current summary approach.

Check:

* Are summaries created only from first N chars?
* Do large manuals miss later chapters?
* Do summaries include tables/diagrams/procedures/warnings?
* Should section-level summaries be created?
* Should summary records be used only for routing, not final answers?
* Should summaries have weaker citation behavior?

9. Citation metadata completeness

Review whether every record type has complete citation metadata.

Every record should have:

* chunk_id
* parent_id
* record_type
* source_file
* source_path
* source_hash
* page_start
* page_end
* physical_pdf_page
* printed_page_label if available
* bbox if available
* processing_status

Tables should also have:

* table_cluster_id
* table_ref
* table_caption
* row_index for table_row
* table_page_start
* table_page_end

Diagrams should also have:

* figure_ref
* diagram_pages
* bbox
* diagram_ocr_text

Tell me:

* what exists
* what is missing
* what could cause wrong citations
* what could cause incomplete citations
* what could cause citations to deleted/stale documents

10. Index schema / skillset / projection alignment

Compare:

* search/index.json
* search/skillset.json
* search/indexer.json
* Python output contract
* README.md
* RUNBOOK.md
* CHATBOT_INTEGRATION.md

Find:

* fields in code but missing in schema
* fields in schema but never populated
* fields in docs but missing in code
* stale documentation
* wrong row limits
* unsupported file type mismatch
* missing projection fields
* missing semantic prioritized fields
* missing scoring profiles
* wrong searchable/filterable/retrievable flags

11. Indexer settings and failure handling

Review:

* maxFailedItems
* maxFailedItemsPerBatch
* failOnUnsupportedContentType
* failOnUnprocessableDocument
* schedule
* batch size
* indexed file extensions

Concern:
The indexer may look successful even when many things failed.

Please verify:

* Can failures be hidden?
* Can partial documents be indexed?
* Can unsupported files be silently skipped?
* Can failed pages still produce a successful run?
* What fail-fast strategy should be used in dev?
* What fail-safe strategy should be used in prod?

12. Incremental update and weekly refresh process

This is very important.

Design or audit the process for weekly updates:

Scenario A:
New PDF added.

Scenario B:
Existing PDF edited but same file name.

Scenario C:
Existing PDF edited and renamed.

Scenario D:
Old PDF deleted.

Scenario E:
Same PDF uploaded twice in different folders.

Scenario F:
New revision of same manual added while old revision still exists.

Scenario G:
Indexing run fails halfway.

Scenario H:
Large PDF changed only on a few pages.

Scenario I:
Cache exists from previous PDF version.

Scenario J:
Index records exist for an old source hash.

For each scenario, explain:

* how current repo handles it
* what can go wrong
* what metadata is required
* how to invalidate cache
* how to delete stale records
* whether full reindex is needed
* whether partial reindex is possible
* how to avoid duplicate records
* how to avoid stale citations
* what alert should fire

13. Alerts and monitoring

Recommend indexing-side alerts.

Examples:

* PDF failed to process
* page failed to process
* page has zero records
* document status partial
* OCR confidence below threshold
* table count changed drastically from previous version
* diagram count changed drastically from previous version
* table_row count is zero for table-heavy document
* diagram records missing OCR text
* multi-page figure detected but not fully processed
* stale records found for deleted PDF
* duplicate source_hash found
* same source_file has multiple active hashes
* indexer failed
* indexer succeeded with warnings
* cache manifest missing
* skillset projection mismatch
* records missing required citation fields
* old index records not deleted after update
* run duration abnormal
* record count abnormal
* Azure Document Intelligence throttling
* Azure OpenAI embedding throttling
* Azure Search throttling
* storage access failure

Tell me which alerts are P0 vs P1.

14. Coverage report

Design or audit:

* coverage_report.json
* coverage_report.md

It should show:

* total PDFs
* total pages expected
* total pages processed
* pages with zero records
* failed pages
* low-confidence pages
* records by type
* text records
* table records
* table_row records
* diagram records
* summary records
* tables without row records
* diagrams without OCR text
* records missing page
* records missing source_file
* records missing bbox
* records missing processing_status
* partial documents
* stale documents
* deleted/renamed file reconciliation results
* changed PDFs since last run
* new PDFs since last run
* deleted PDFs since last run
* duplicate PDFs
* active vs stale document versions

Tell me:

* what already exists
* what is missing
* what should block production
* what should only warn

15. Huge PDF scaling

Review handling for:

* 90 MB PDFs
* 3,000+ page PDFs
* page batching
* checkpoint/resume
* memory usage
* timeouts
* rate limits
* retry behavior
* partial output
* long-running Document Intelligence jobs
* vision model cost
* duplicate image processing
* reprocessing only changed documents
* avoiding full reprocess when not needed

Tell me:

* what is production-safe
* what is risky
* what must be added

16. Rollback strategy

For weekly updates, design rollback behavior.

Check:

* If new indexing run fails, should old active index records remain active?
* Should new records be staged first?
* Should records be promoted only after validation passes?
* Do we need an active_index_run_id concept?
* Do we need status active/staged/stale?
* How should stale records be removed after successful validation?
* How should failed new runs avoid corrupting production search data?

17. Final P0/P1/P2 reconciliation

Reconcile everything into one final roadmap.

For every item, classify:

* P0 before next production indexing run
* P1 next sprint
* P2 later
* Postpone to backend retrieval later
* Needs verification

For every P0 item, include:

* problem
* why it matters
* files likely affected
* schema change needed yes/no
* skillset change needed yes/no
* full reindex needed yes/no
* can be implemented safely first as validation yes/no
* acceptance criteria
* rollback plan
* risk if skipped

==================================================
OUTPUT FORMAT
=============

Please produce one final merged continuation report in this exact structure:

A. Short executive verdict
B. Is the indexing-first approach still correct?
C. Where the previous audit is still valid
D. What the previous audit missed or under-emphasized
E. Where my new lifecycle/update thinking is correct
F. Where my new lifecycle/update thinking may be incomplete or risky
G. Better options or alternative approaches
H. Final indexing-side missing items
I. Document lifecycle/update/delete/rename strategy
J. Source hash, document identity, versioning, and stale cleanup design
K. Cache invalidation design
L. Page-level manifest recommendation
M. Table capture and large table coherence recommendation
N. Diagram/OCR/image capture recommendation
O. OCR confidence and low-quality page recommendation
P. Chunking and context recommendation
Q. Summary record recommendation
R. Citation metadata recommendation
S. Schema/skillset/projection alignment recommendation
T. Indexer failure-handling recommendation
U. Weekly update process recommendation
V. Alerts and monitoring recommendation
W. Coverage report recommendation
X. Huge PDF scaling recommendation
Y. Rollback/staging/promotion recommendation
Z. P0 items before next production indexing run
AA. P1 items after P0
AB. P2/later items
AC. Items to postpone to backend retrieval later
AD. Exact files/functions likely affected
AE. Schema changes required
AF. Skillset/indexer changes required
AG. Python code changes required
AH. Validation scripts to add or update
AI. Which items require index rebuild
AJ. Which items require full reindex
AK. Which items can be implemented without schema change
AL. Acceptance criteria
AM. Final recommended implementation sequence
AN. Questions you need me to answer before implementation

Final instruction:
Do not edit files yet.
Do not apply patches.
Do not change production configs.
Give only the final merged indexing-side continuation audit and recommended plan.
