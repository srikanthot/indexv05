Continue from the indexing-side audit/report you already produced.

This is only for the indexing repository. Please do not discuss frontend/backend chatbot code, UI, chat history, query-router, or retrieval implementation right now.

I want to share my current thinking and get your architectural opinion before we approve any code changes.

The report you gave looks good. Based on that, this is what I am thinking:

1. The current indexing pipeline has already indexed documents, but I am not fully confident that it is production-safe yet.
2. The main concern is not just whether the indexer ran successfully. The concern is whether the indexed data is complete, current, clean, correctly cited, and safe for weekly document changes.
3. PDFs are not static. New PDFs will be added weekly. Existing PDFs can be edited. Some files may keep the same name but have new content. Some may be renamed. Some may be deleted. Some manuals may have new revisions.
4. Because of that, I am thinking we need stronger lifecycle handling around source hash, source last modified, document identity, index run ID, stale cleanup, cache invalidation, active/staged records, rollback, and coverage validation.
5. I am also thinking the earlier P0 items still matter: table split coherence, table row linkage, diagram OCR projection, multi-page diagram handling, required citation metadata, and indexer failure tolerance.
6. I do not want to blindly implement everything. I want to understand what you think should be implemented first, what can wait, what is risky, and what needs verification in the code.

Please review this thinking and challenge it.

Questions I want your opinion on:

1. Does this approach make sense?
2. Am I overthinking any part?
3. Is anything important still missing?
4. Which items are truly P0 before the next production indexing run?
5. Which items can be P1 or later?
6. Which items can be added first as validation/reporting without schema changes?
7. Which items require schema/index rebuild?
8. Which items require full reindex?
9. Which items are risky to implement immediately?
10. Is there a safer rollout approach than changing everything at once?

Here is the direction I am considering. Please confirm, challenge, or improve it:

A. Validation-first items
These seem safer to do first because they do not necessarily change the index schema:

* improve coverage report
* validate required fields by record type
* detect stale records
* detect deleted/renamed/edited PDFs
* detect partial documents
* detect pages with zero records
* detect diagrams without OCR text
* detect tables without row records
* detect missing citation metadata
* detect old active records after update
* detect cache/source mismatch
* add clear pass/fail coverage gate

B. Lifecycle metadata items
These may need schema and code changes:

* source_hash
* source_last_modified
* index_run_id
* is_active
* document_status
* processing_status
* failure_reason
* document_id
* version_group_id if needed
* active/staged/stale state

C. Table coherence items
These seem important for large manuals:

* table_cluster_id
* table_split_index
* table_split_count
* table_page_start
* table_page_end
* table row to logical table linkage
* full table artifact or full table reference
* small critical tables should still produce table_row records

D. Diagram/OCR items
These seem important for image-heavy PDFs:

* diagram_ocr_text or ocr_text should be emitted, projected, searchable, and retrievable
* diagram page range should be captured
* multi-page diagrams should at least be detected and flagged
* full multi-page diagram support may be P1 if too risky
* DI-missed embedded images should at least be counted/reported first
* secondary image extraction can be P1 if implementation is risky

E. Weekly update / rollback items
This is what I am thinking for safer production updates:

* detect source changes using hash, not only filename or last modified time
* if same file changed, invalidate old cache and old records
* if file deleted, mark/remove old records
* if renamed but same hash, avoid duplicate active records
* if new run fails, keep old active records
* new run should be staged first
* promote staged records only after coverage validation passes
* if validation fails, do not promote
* stale cleanup should happen only after successful promotion
* consider blue/green index or new dev index for schema-changing rollout

Please tell me if this staged/active approach is the right design for this repository, or if there is a simpler option that is safer.

Please produce a P0-focused review, not code changes.

For each possible P0 item, please give:

1. Your recommendation: keep as P0 / move to P1 / postpone / needs verification
2. Why it matters
3. What can go wrong if skipped
4. Files/functions likely affected
5. Schema change required? yes/no
6. Skillset projection change required? yes/no
7. Full reindex required? yes/no
8. Can we implement validation-only first? yes/no
9. Suggested safe rollout order
10. Acceptance criteria
11. Rollback consideration

Please also separate the plan into:

1. Things we can do without schema change
2. Things that require schema/index rebuild
3. Things that require full reindex
4. Things that should be tested only in dev first
5. Things that should not be implemented yet

Tone:
Please do not blindly agree with me. I want your honest architecture review. Use wording like:

* “This makes sense because…”
* “I would challenge this because…”
* “This may be too much for P0 because…”
* “This should probably move to P1 because…”
* “This needs verification in the code because…”
* “A safer alternative is…”

Final instruction:
Do not edit files yet.
Do not apply patches yet.
Do not change production configs.
Give only your opinion, challenge review, and P0-focused patch planning recommendation.
