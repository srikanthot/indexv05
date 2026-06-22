Tier 1 — Skip everything except indexing (most common after initial setup)
ONE command:


python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze
Line-by-line equivalent:


python scripts/deploy_search.py --config deploy.config.json
.\scripts\reset_indexer.ps1
python scripts/heal_until_done.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
Use this when: function code + search artifacts are already deployed, cache is already built. You just want to retrigger the indexer (e.g., after metadata changes, or to retry stuck PDFs).

Tier 2 — New PDFs added, need preanalyze + indexer
ONE command:


python scripts/deploy.py --config deploy.config.json --skip-bootstrap
Line-by-line equivalent:


python scripts/preanalyze.py --config deploy.config.json --incremental
python scripts/deploy_search.py --config deploy.config.json
.\scripts\reset_indexer.ps1
python scripts/heal_until_done.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
Use this when: new PDFs were uploaded to the blob container. --skip-bootstrap skips RBAC, Cosmos creation, function code deploy. preanalyze.py --incremental only processes PDFs that don't already have a complete cache.

Tier 3 — Just retrigger stuck PDFs (no preanalyze, no search redeploy)
ONE command:


python scripts/heal_until_done.py --config deploy.config.json
Line-by-line equivalent: same — it's already one script.

Use this when: indexer got stuck on some PDFs and you just want to retry. No code changes, no metadata changes, just push the indexer along.

Quick reference card
Scenario	Command
First-time setup (RBAC, function deploy, everything)	python scripts/deploy.py --config deploy.config.json --auto-fix
New PDFs added (need preanalyze)	python scripts/deploy.py --config deploy.config.json --skip-bootstrap
Metadata changed or just retrigger (no new PDFs)	python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze
Just heal stuck PDFs (cache is fine, no schema changes)	python scripts/heal_until_done.py --config deploy.config.json
Just check status (zero changes made)	python scripts/check_index.py --config deploy.config.json --coverage
Full teammate-facing message (copy-paste version)

# ===== ONE-TIME PER MACHINE =====
git clone https://github.com/srikanthot/indexv05.git
cd indexv05
pip install -r requirements.txt
# save deploy.config.json in this folder

# ===== ONE-TIME PER SHELL SESSION =====
az cloud set --name AzureUSGovernment
az login
az account set --subscription "sub-pseg-nj-techmanual-dev"

# ===== FIRST-TIME DEPLOY (does RBAC + function code + everything) =====
python scripts/deploy.py --config deploy.config.json --auto-fix

# ===== AFTER NEW PDFS ARE UPLOADED =====
python scripts/deploy.py --config deploy.config.json --skip-bootstrap

# ===== AFTER METADATA / TAG CHANGES (no new PDFs) =====
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --skip-preanalyze

# ===== IF INDEXER GOT STUCK ON SOME PDFS =====
python scripts/heal_until_done.py --config deploy.config.json

# ===== VERIFY ANY TIME =====
python scripts/check_index.py --config deploy.config.json --coverage
That covers every scenario. Your teammates don't need to know bootstrap.py, deploy_search.py, or reset_indexer.ps1 exist — they just pick the right deploy.py invocation based on what changed.

Hey, here's the setup:


git clone https://github.com/srikanthot/indexv05.git
cd indexv05
pip install -r requirements.txt
az cloud set --name AzureUSGovernment
az login
az account set --subscription "sub-pseg-nj-techmanual-dev"
Then save the deploy.config.json I sent you in the indexv05 folder, and run:


python scripts/deploy.py --config deploy.config.json --auto-fix
That single command does everything — RBAC, Cosmos DB setup, blob soft-delete, function app deploy, preanalyze, indexer, healing. Takes a few hours on first run (mostly preanalyze). Watch the console output — if it finishes with ✓ DEPLOY COMPLETE, every PDF is indexed.

python scripts/bootstrap.py --config deploy.config.json --auto-fix --skip-function-app --skip-search-artifacts --skip-smoke-test

az functionapp config appsettings set -g <RG> -n <FuncApp> --settings SCM_DO_BUILD_DURING_DEPLOYMENT=1
Compress-Archive -Path function_app\* -DestinationPath func.zip -Force
az functionapp deployment source config-zip -g <RG> -n <FuncApp> --src func.zip

python scripts/deploy.py --config deploy.config.json --skip-bootstrap

$cfg = Get-Content deploy.config.json -Raw | ConvertFrom-Json; $FuncApp = $cfg.functionApp.name; $RG = $cfg.functionApp.resourceGroup; "FuncApp = $FuncApp   |   RG = $RG"


Hi, for the PSG Tech Manual indexing Jenkins pipeline, I need shared Jenkins credentials created at the TechManual folder level or Jenkins Global/System level, not under my personal Jenkins user.

Jenkins job:
TechManual / psegtechmanualindex

Please create these Secret File credentials:

1. deploy-config-dev
    File: deploy.config.dev.json
2. deploy-config-qa
    File: deploy.config.qa.json
3. deploy-config-prod
    File: deploy.config.prod.json

Each JSON file contains the exact environment resource references: Function App name/resource group, Azure AI Search endpoint/artifact prefix, Azure OpenAI endpoint/deployments, Document Intelligence endpoint, Storage account resource ID/blob container, Cosmos endpoint/database, and App Insights connection string if applicable.

Please also create these Secret Text credentials:

1. azure-client-id
2. azure-client-secret
3. azure-tenant-id
4. DEV_AZURE_SUBSCRIPTION_ID
5. QA_AZURE_SUBSCRIPTION_ID
6. PROD_AZURE_SUBSCRIPTION_ID

The credential IDs must match exactly because the Jenkinsfile uses these names. These credentials should be accessible to the TechManual/psegtechmanualindex pipeline so any team member can run the job.



python scripts/check_index.py --config <prod-config>.json --check-stuck-indexer


az rest --method get --resource "https://search.azure.us" --url "https://<prod-search-service>.search.azure.us/indexers/<prefix>-indexer/status?api-version=2024-05-01-preview"


python scripts/check_index.py --config <prod-config>.json --coverage


python scripts/reconcile.py --config <prod-config>.json --dry-run


python scripts/preanalyze.py --config deploy.config.json --pdf MYDOC.pdf --force


$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az storage blob metadata update --account-name ($cfg.storage.accountResourceId.Split('/')[-1]) --container-name $cfg.storage.pdfContainerName --name MYDOC.pdf --metadata force_reindex=1 --auth-mode login


python scripts/check_index.py --config deploy.config.json --coverage


$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az rest --method post --url "$($cfg.search.endpoint.TrimEnd('/'))/indexers/$($cfg.search.artifactPrefix)-indexer/run?api-version=2024-11-01-preview" --resource "https://search.azure.us"

python scripts/preanalyze.py --config deploy.config.json --pdf MYDOC.pdf --force

$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az storage blob metadata update --account-name ($cfg.storage.accountResourceId.Split('/')[-1]) --container-name $cfg.storage.pdfContainerName --name MYDOC.pdf --metadata force_reindex=1 --auth-mode login

$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az rest --method post --url "$($cfg.search.endpoint.TrimEnd('/'))/indexers/$($cfg.search.artifactPrefix)-indexer/run?api-version=2024-11-01-preview" --resource "https://search.azure.us"


python scripts/check_index.py --config deploy.config.json --coverage


python scripts/preanalyze.py --config deploy.config.json --pdf MYDOC.pdf --force

$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az rest --method post --url "$($cfg.search.endpoint.TrimEnd('/'))/indexers/$($cfg.search.artifactPrefix)-indexer/run?api-version=2024-11-01-preview" --resource "https://search.azure.us"

python scripts/check_index.py --config deploy.config.json --coverage



python scripts/preanalyze.py --config deploy.config.json --pdf MYDOC.pdf --force

$cfg=Get-Content deploy.config.json -Raw|ConvertFrom-Json; az rest --method post --url "$($cfg.search.endpoint.TrimEnd('/'))/indexers/$($cfg.search.artifactPrefix)-indexer/run?api-version=2024-11-01-preview" --resource "https://search.azure.us"

python scripts/check_index.py --config deploy.config.json --coverage

You are working in our chatbot's front-end + back-end repository. This app sits on top of an Azure AI Search index (multimodal, built from technical-manual PDFs). You do NOT have access to the indexing repo, so below is the authoritative contract for the index. Audit our code against it. Primary problem: citations/highlights are mostly wrong — clicking a citation jumps to the wrong page and highlights scattered/irrelevant regions. Fix that first, then report what index capabilities we are not using.

INDEX BASICS

One flat index. Each row is a "record" distinguished by the field record_type ∈ {text, diagram, table, table_row, summary}.
All records of one PDF share parent_id. Stable per-record key is chunk_id. The Azure-internal key id is NOT stable — use chunk_id for deep-links/caching.
Supports BM25 + HNSW vector (1536-dim ada-002) + hybrid + semantic ranker, with an integrated vectorizer (the index embeds the query itself — pass vectorQueries.kind="text", no need to call the embedding model yourself).
Semantic config name: mm-semantic-config. Always filter processing_status eq 'ok'.
CITATION / HIGHLIGHT FIELD CONTRACT (this is the focus)
Each text record has TWO different "page" concepts — do not confuse them:

physical_pdf_page (Int) = the chunk's TRUE start page. This is the page to OPEN the PDF at.
physical_pdf_page_end (Int) and physical_pdf_pages (Int list) = the chunk's REAL, contiguous page span (e.g. [3,4]). This is the source of truth for where the chunk actually is.
text_bbox (JSON string) = a list of highlight rectangles: [{ "page":N, "x_in":.., "y_in":.., "w_in":.., "h_in":.. }, ...]. IMPORTANT: this list is built by matching the chunk text against ALL paragraphs in the whole PDF and is NOT constrained to the chunk's real pages. It can therefore contain FALSE rectangles on pages 100–300 away, caused by repeated boilerplate (safety notices, headers, footers, table-of-contents lines).
highlight_text (string) = plain text to feed the PDF viewer's text-search highlighter.
printed_page_label (string) = the human-visible page label to DISPLAY (e.g. "A-12", "iv"). printed_page_label_is_synthetic=true means it was synthesized — show physical_pdf_page instead. printed_page_label_end for ranges.
page_resolution_method (string) = confidence of the page resolution; header_match = high confidence.
For diagram records use figure_bbox; for table records use table_bbox (these are single-region, not scattered). Coordinates are in INCHES, origin top-left: x_in,y_in = top-left corner; w_in,h_in = width/height. pdf_total_pages gives the PDF length.
THE CITATION BUG WE ARE HITTING
Our front-end appears to navigate to the LAST entry of text_bbox and render ALL boxes. Because text_bbox contains false far-away boxes, the user lands on the wrong page and sees scattered highlights; the real text isn't highlighted in view.

REQUIRED CORRECT BEHAVIOR — implement exactly this

To OPEN the PDF, navigate to physical_pdf_page (NEVER the last text_bbox entry).
To DRAW highlights, parse text_bbox and render ONLY rectangles whose page is within physical_pdf_pages (i.e. between physical_pdf_page and physical_pdf_page_end). DISCARD every box on a page outside that set — they are false matches.
For text-search highlighting, feed highlight_text to the viewer.
Display the label as printed_page_label; fall back to physical_pdf_page when printed_page_label_is_synthetic=true or the label is null.
For record_type=diagram use figure_bbox; for record_type=table use table_bbox.
If page_resolution_method != header_match, still show the citation but de-emphasize the "jump to exact spot" action.
RETRIEVAL CAPABILITIES WE MAY NOT BE USING — audit each
The index supports far more than plain text search. Check whether our back-end does each of these; report which are missing:

 Uses hybrid (BM25+vector) + queryType:"semantic" with semanticConfiguration:"mm-semantic-config", not plain keyword.
 Does NOT hard-filter to record_type='text' — so diagrams/tables/rows can surface. Use search.in(record_type,'text,diagram,table,table_row',',').
 Intent routing: figure/diagram/schematic/wiring words → record_type eq 'diagram' and has_diagram eq true; value/lookup questions → record_type eq 'table_row'; "what is X / define X" → record_subtype eq 'glossary'; safety/warning/LOTO words → safety_callout eq true; "what is this manual about" → record_type eq 'summary'.
 Cross-reference expansion: when a top text hit has non-empty figures_referenced_normalized or tables_referenced, run a second query (same parent_id, record_type='diagram'/'table') to fetch the referenced figure/table and include it in the answer context.
 table_row hits: dereference table_parent_chunk_id to fetch and show the parent table.
 Selects and renders diagram_description (for diagram rows this carries the only visual content as text).
 Surfaces safety_callout/callouts as UI badges and leads safety answers with them.
 Prefers current revisions via effective_date desc / document_revision.
 Always filters processing_status eq 'ok'.
 Never $selects non-retrievable fields (chunk_for_semantic, surrounding_context, text_vector) — those error/return null.
 Builds citation chip from source_file + printed_page_label linking to source_url#page=physical_pdf_page (mint a SAS token if blob isn't anonymously readable).
TASKS — do these in order and report findings

Locate our citation/highlight code (PDF viewer component + the back-end search response mapping). Quote the current logic for choosing the page and the bbox.
Compare it to "REQUIRED CORRECT BEHAVIOR" above. List every deviation (e.g., "uses text_bbox[last]", "renders all boxes", "navigates to printed label").
Do a trial run: take one real search result, log its physical_pdf_page, physical_pdf_pages, and parsed text_bbox, and show which boxes our current code would render vs which it SHOULD render after filtering by physical_pdf_pages. Report any parsing errors (e.g., text_bbox is a JSON string that must be JSON.parsed; null/empty when processing_status != ok).
Apply the citation fix (open at physical_pdf_page; render only in-span boxes; use highlight_text; correct label fallback; diagram/table use their own bbox).
Then audit the "RETRIEVAL CAPABILITIES" checklist against our query code and list what we're not using, with the specific code change for each.


We have a defect in the chatbot retrieval/answer generation flow.

User prompt:
"give me UEOC 24hr checklist as per SDR"

Actual behavior:
The chatbot returns only 5 checklist items and citations point to the "UEOC and Division 12-Hour Checklist" source/page.

Expected behavior:
It should retrieve and answer from the "UEOC and Division 24-Hour Checklist" source/page, which contains 6 checklist items.

Observed from the document:
The wrong cited source is the 12-hour checklist page. It has 5 responsibilities.
The next/correct page is the 24-hour checklist page and has 6 responsibilities:
1. Assure 48-hour checklist completed
2. Verify facility status, circuits, all reclosers and station alarms
3. Top off all gasoline and diesel fuel tanks
4. Reserve lodging and arrange meals for UEOC personnel if necessary
5. Arrange for emergency vehicles for Damage Assessment Strike team if needed
6. Review Board Order for storm response - Regulatory Timing Requirements - Updated from 48-hour review

Please inspect the backend RAG/search flow and identify why the query "UEOC 24hr checklist" retrieves the 12-hour checklist chunk instead of the 24-hour checklist chunk.

Check the following areas:
1. Azure AI Search query construction/query rewriting: is "24hr" being normalized incorrectly or ignored?
2. Search ranking/topK: is the 12-hour chunk ranked higher than the 24-hour chunk because both contain similar terms like UEOC/checklist/SDR?
3. Chunking/indexing: are 12-hour and 24-hour checklist pages split correctly, or are they merged/mislabelled?
4. Metadata fields: check page number, title/header, document name, section heading, and citation mapping.
5. Filters: verify whether the backend applies any filter for SDR/manual/source and whether it is too broad.
6. Prompt/context assembly: confirm which retrieved chunks are passed to the LLM before answer generation.
7. Citation generation: confirm citations are coming from the same chunks used to generate the answer.
8. Add debug logs to print retrieved documents with score, rerankerScore, page number, title/header, chunk text preview, and source path for this exact query.
9. Add a regression test where "UEOC 24hr checklist as per SDR" must retrieve the 24-hour checklist chunk and return exactly 6 items, not the 12-hour checklist.

Please suggest the code changes needed so exact checklist duration terms like "24hr", "24-hour", "24 hour", and "24 hours" are strongly matched before semantic/reranker results are sent to the LLM.

Act as a principal architect, senior Azure AI Search engineer, senior RAG engineer, and production reliability lead.

Context:
This repository is used to create an Azure AI Search index for a technical-manual RAG chatbot. The pipeline processes technical PDFs/manuals and creates searchable records such as text, table, table_row, diagram, and summary. The chatbot depends on this index for retrieval, citations, page references, table answers, diagram answers, safety/warning answers, and procedural answers.

Important rules:

* Do NOT modify code yet.
* Do NOT refactor anything yet.
* Do NOT create golden test sets yet.
* Do NOT generate PDF-based evaluation questions yet.
* First perform a deep code audit only.
* Verify every claim against actual repository files.
* Mark anything uncertain as NEEDS VERIFICATION.
* Do not invent fields, functions, or behavior that do not exist.
* Compare README, CHATBOT_INTEGRATION.md, index schema, skillset, indexer, scripts, and Python code for mismatches.

Main goal:
Find every possible production failure scenario in this RAG indexing pipeline.

Think through at least 10,000 realistic production scenarios and edge cases, including:

* user query failures
* indexing failures
* retrieval failures
* citation failures
* table failures
* diagram failures
* OCR failures
* chunking failures
* metadata failures
* stale data failures
* Azure Search failures
* Azure Document Intelligence failures
* Azure OpenAI/embedding failures
* Function App failures
* Jenkins/deployment failures
* performance/scaling failures
* security/RBAC failures
* operational monitoring failures

Do not only check happy paths. Focus on what can go wrong in real production.

==================================================

1. INDEX SCHEMA AUDIT
   ==================================================

Inspect:

* search/index.json
* any schema-generation scripts
* any documentation that refers to index fields

Check:

* missing fields
* stale fields
* fields used in code but missing in schema
* fields used in documentation but missing in schema
* fields present in schema but never populated
* fields populated in code but not projected into the index
* wrong searchable/filterable/retrievable/facetable/sortable settings
* whether chatbot-needed fields are retrievable
* whether citation-needed fields are retrievable
* whether table/diagram relationship fields exist
* whether revision/date/source metadata can be filtered
* whether status fields can be filtered
* whether vector fields match embedding dimensions
* whether semantic configuration includes the correct fields
* whether scoring profiles exist or are missing

Pay special attention to:

* chunk_id
* parent_id
* table_parent_chunk_id
* table_cluster_id
* table_split_index
* table_split_count
* record_type
* record_subtype
* content
* chunk_for_semantic
* source_file
* source_path
* source_url
* physical_pdf_page
* printed_page_label
* page_start
* page_end
* section/header fields
* figure_ref
* table_ref
* bbox
* ocr_text
* diagram_description
* safety_callout
* processing_status
* chunk_quality_score
* ocr_confidence
* document_revision
* effective_date
* last_indexed_at
* embedding_version
* skill_version

Output:

* confirmed schema issues
* missing fields needed for production chatbot retrieval
* fields that should be filterable
* fields that should be retrievable
* fields that should be searchable
* fields that should be added later
* risk level for each issue

==================================================
2. SKILLSET AND INDEXER AUDIT
=============================

Inspect:

* search/skillset.json
* search/indexer.json
* any deploy/bootstrap scripts that create skillsets/indexers

Check:

* timeout risks
* batch size risks
* maxFailedItems risk
* maxFailedItemsPerBatch risk
* schedule risks
* whether failures can be hidden
* whether partial indexing can still look successful
* whether unsupported files are skipped silently
* whether output projections map all expected fields
* whether all record types get status/version/timestamp/quality metadata
* whether the skillset can handle large documents
* whether custom Web API skills can timeout
* whether indexer schedule can conflict with Jenkins/manual runs
* whether indexer can reprocess stale files correctly
* whether deleted files are removed from index
* whether renamed files create duplicates
* whether modified files invalidate old cache
* whether encrypted/corrupt/huge PDFs fail safely

Output:

* confirmed indexer/skillset issues
* hidden failure risks
* production safety recommendations
* exact files/functions involved

==================================================
3. TABLE PIPELINE AUDIT
=======================

Inspect:

* function_app/shared/tables.py
* process_table.py
* any table extraction/normalization code
* any table projection/indexing code
* any chatbot integration docs about tables

Check:

* multi-page table merge behavior
* continuation table detection false positives
* continuation table detection false negatives
* same-column-count assumptions
* adjacent-page assumptions
* missing caption assumptions
* 1-row, 2-row, 3-row, and 4-row table handling
* whether small critical tables miss table_row records
* row-record min/max limits
* very large table handling
* oversized table splitting behavior
* whether split tables preserve logical coherence
* whether table rows only attach to split 0
* whether parent fetch gets only one split instead of the full logical table
* whether all splits of one logical table are linked
* whether table_cluster_id is needed
* whether table_split_index and table_split_count are needed
* whether row records preserve source page/page range
* whether row records need row-level bbox
* whether table-level bbox is enough
* whether merged cells are handled
* whether multi-row headers are folded correctly
* whether repeated headers are removed or duplicated
* whether blank cells cause wrong row meaning
* whether wide tables become unreadable markdown
* whether rotated tables are handled
* whether continuation captions are preserved
* whether table footnotes are linked
* whether table titles/captions are preserved
* whether units from headers are preserved
* whether table_image figures need structured extraction
* whether numeric lookup works for exact values
* whether exact row lookup can confuse 12 vs 24, 240 vs 480, TB-2 vs TB-12

Output:

* confirmed table bugs
* likely table bugs
* missing table metadata
* high-risk production scenarios
* exact recommended fixes
* code-change plan by file, but do not apply changes

==================================================
4. DIAGRAM / IMAGE PIPELINE AUDIT
=================================

Inspect:

* process_document.py
* diagram.py
* pdf_crop.py
* preanalyze.py
* any image/figure crop logic
* any vision analysis logic
* any diagram record projection logic

Check:

* figures spanning multiple pages
* whether only first page of multi-page figure is cropped
* fold-out drawings
* full-page drawings
* inline images
* embedded images missed by Document Intelligence
* images inside tables
* table images classified as diagram/table_image
* nameplates
* scanned image-only pages
* poor-quality scans
* tiny labels in wiring diagrams
* terminal labels
* component tags
* figure references in nearby text
* figure caption extraction
* bbox accuracy
* crop DPI quality
* crop coordinate conversion
* page rotation issues
* duplicate image hash/phash behavior
* whether useful figures can be accidentally deduplicated
* whether OCR text from diagram is stored
* whether diagram description is stored
* whether figure_ref is populated
* whether diagram_category is populated
* whether has_diagram is populated
* whether diagram pages are correctly cited
* whether vision failures are retried or silently skipped
* whether empty diagram descriptions are allowed into index

Output:

* confirmed diagram/image bugs
* likely missing diagram scenarios
* multi-page figure handling recommendation
* secondary image extraction recommendation
* table_image structured extraction recommendation
* code-change plan by file, but do not apply changes

==================================================
5. TEXT CHUNKING AUDIT
======================

Inspect:

* SplitSkill configuration
* semantic/chunking-related code
* any content-building functions
* any chunk_for_semantic logic

Check:

* character-based splitting issues
* sentence boundary issues
* paragraph boundary issues
* cross-page paragraph continuation
* page overlap length
* chunk max length
* whether important steps are split across chunks
* whether warnings separate from procedure steps
* whether table references separate from table content
* whether figure references separate from diagram record
* whether headers are prepended to chunk text
* whether source file/page/section are prepended
* whether previous/next sentence context is included
* whether chunks are too small
* whether chunks are too large
* whether token limits are respected
* whether chunking is optimized for embedding quality
* whether technical IDs get split
* whether glossary/acronym entries are preserved
* whether equations/formulas are garbled
* whether bullet lists/procedure steps preserve numbering

Output:

* confirmed chunking risks
* missing context risks
* recommended chunking improvements
* code-change plan by file, but do not apply changes

==================================================
6. SUMMARY PIPELINE AUDIT
=========================

Inspect:

* summary.py
* any summary skill/code
* any document-level summary records

Check:

* summary truncation
* whether only first part of large manual is summarized
* whether later chapters are ignored
* whether summary misses procedures/checklists/warnings
* whether table/diagram content is represented in summaries
* whether section-level summaries exist
* whether map-reduce summary is needed
* whether summaries are used for retrieval routing
* whether summary records can mislead the chatbot
* whether summary citations are safe or too broad

Output:

* summary bugs
* summary limitations
* recommended section-summary/document-summary architecture

==================================================
7. METADATA AND CITATION AUDIT
==============================

Inspect:

* citation fields in schema
* bbox generation
* page mapping logic
* source_url/source_path logic
* frontend integration docs if present

Check:

* physical page vs printed page label mismatch
* page_start/page_end correctness
* table page range correctness
* diagram page correctness
* citation source file correctness
* citation source path correctness
* source_url assumptions
* SAS URL/security assumptions
* bbox JSON format
* whether frontend can highlight cited area
* whether table row citations point to exact row or only table
* whether split table citations point to wrong split
* whether diagram citations point to first page only
* whether citation metadata is missing for any record type
* whether citations appear when answer is fallback/no-answer/clarification
* whether wrong citations can be shown even if answer is correct
* whether deleted/renamed files leave stale citations

Output:

* citation failure modes
* high-risk wrong-citation scenarios
* required citation metadata fixes
* code-change plan by file, but do not apply changes

==================================================
8. RETRIEVAL / CHATBOT INTEGRATION AUDIT
========================================

Inspect:

* CHATBOT_INTEGRATION.md
* any backend query/search code if present
* any docs describing retrieval
* any sample queries
* any reranking logic

Compare documentation with actual code.

Design and audit retrieval strategies for these intents:

* exact lookup
* checklist
* procedure
* table row lookup
* whole table
* diagram
* figure reference
* table reference
* glossary/acronym
* safety/warning/caution
* troubleshooting
* revision/latest document
* comparison
* broad/ambiguous
* no-answer
* multi-document/cross-reference
* page-specific question
* equipment-ID-specific question
* numeric/unit-specific question

For each intent, define:

* record_type filter
* exact terms
* negative terms
* vector k
* top
* semantic query settings
* scoring/boosting
* expansion query
* related-record fetch
* rerank logic
* answer policy
* citation policy
* fallback behavior

Pay special attention to:

* 12 hours vs 24 hours
* 240V vs 480V
* Rev A vs Rev C
* open vs close
* before vs after
* minimum vs maximum
* left vs right
* TB-2 vs TB-12
* Figure 18-117 vs Figure 18-171
* page 18 vs section 18
* old manual vs new manual
* current revision vs obsolete revision

Output:

* retrieval gaps
* chatbot integration gaps
* exact query-router recommendation
* related-record expansion recommendation
* reranking recommendation
* code-change plan by file, but do not apply changes

==================================================
9. EXACT VALUE / ENTITY PROTECTION AUDIT
========================================

Check whether the system protects exact:

* numbers
* units
* durations
* revision names
* equipment IDs
* terminal IDs
* wire tags
* figure IDs
* table IDs
* page numbers
* section numbers
* dates
* model numbers
* part numbers
* voltage/current/temperature/pressure values

Find failure cases where vector search may retrieve semantically similar but numerically wrong chunks.

Examples:

* user asks 24-hour checklist, system retrieves 12-hour checklist
* user asks 480V, system retrieves 240V
* user asks TB-2, system retrieves TB-12
* user asks Rev C, system retrieves Rev A
* user asks minimum, system answers maximum
* user asks before startup, system answers after startup
* user asks open breaker, system answers close breaker

Output:

* exact-value failure scenarios
* required parsing logic
* boosting/demotion strategy
* metadata fields needed
* tests to add later

==================================================
10. SAFETY / WARNING / COMPLIANCE AUDIT
=======================================

Inspect:

* warning/caution/danger extraction
* safety_callout logic
* answer policy docs if any

Check:

* WARNING/CAUTION/DANGER detection
* whether safety_callout is populated for all record types
* whether safety records are boosted for safety questions
* whether warnings remain attached to procedure steps
* whether answer should lead with warning if relevant
* whether unsupported safety answers are blocked
* whether LLM can give unsafe answer when evidence is weak
* whether safety citations are mandatory
* whether conflicting safety instructions are detected
* whether obsolete safety instructions can be used

Output:

* safety risks
* required safety retrieval strategy
* answer policy recommendation

==================================================
11. OPERATIONS / RELIABILITY AUDIT
==================================

Inspect:

* Jenkinsfile
* deploy scripts
* run_pipeline
* preanalyze
* reconcile
* heal
* check_index
* Function App triggers
* timer jobs
* App Insights logging
* Cosmos run history if present
* environment variables
* Azure auth logic

Check:

* Jenkins/manual/indexer/timer race conditions
* pipeline locking
* idempotency
* retry strategy
* partial output handling
* cache invalidation
* stale cache cleanup
* corrupt PDF handling
* encrypted PDF handling
* huge PDF handling
* empty PDF handling
* unsupported file handling
* memory/OOM risk
* timeout risk
* rate-limit/throttling risk
* Azure OpenAI quota risk
* Document Intelligence quota risk
* Azure Search quota risk
* storage auth risk
* managed identity risk
* connection string risk
* Azure Gov compatibility risk
* secrets handling
* deployment rollback
* environment mismatch dev/QA/prod
* schedule stop/start behavior
* observability gaps
* alerting gaps
* dashboard gaps

Output:

* operational risks
* reliability gaps
* production runbook gaps
* monitoring/alerting recommendations

==================================================
12. DATA QUALITY / DOCUMENT QUALITY AUDIT
=========================================

Check how the pipeline behaves with:

* scanned PDFs
* low OCR confidence
* handwritten notes
* rotated pages
* landscape pages
* mixed portrait/landscape pages
* duplicate pages
* watermarks
* revision stamps
* TOC pages
* index pages
* appendices
* footnotes/endnotes
* equations/formulas
* symbols/special characters
* multilingual content
* images of tables
* embedded CAD drawings
* very small fonts
* poor contrast
* corrupted pages
* password-protected PDFs

Output:

* data quality risks
* how each risk affects retrieval/citation/answering
* required metadata/status fields
* production gating recommendations

==================================================
13. SECURITY / ACCESS CONTROL AUDIT
===================================

Check:

* whether source docs need RBAC filtering
* whether user roles/permissions can filter search results
* whether source URLs expose documents improperly
* whether SAS tokens are safe
* whether deleted documents remain searchable
* whether restricted manuals can be retrieved by unauthorized users
* whether logs leak secrets or document content
* whether managed identity is used consistently
* whether storage account keys are used
* whether environment variables contain secrets

Output:

* security risks
* RBAC filtering recommendation
* source URL recommendation
* secrets/logging recommendation

==================================================
14. PERFORMANCE / SCALABILITY AUDIT
===================================

Check:

* indexing time for large PDFs
* Function App memory usage
* vision model cost and latency
* embedding cost and latency
* Search index size
* vector size/dimensions
* number of records per document
* large table row explosion
* diagram image explosion
* duplicate image processing
* indexer schedule frequency
* query latency impact of top/k
* semantic reranker latency
* related-record expansion latency
* max context size to LLM
* concurrency limits
* throttling behavior

Output:

* performance bottlenecks
* cost bottlenecks
* scaling recommendations
* safe defaults for production

==================================================
15. DOCUMENTATION CONSISTENCY AUDIT
===================================

Compare:

* README
* CHATBOT_INTEGRATION.md
* RUNBOOK.md
* code comments
* actual Python code
* index schema
* skillset
* indexer

Find:

* stale documentation
* wrong limits
* wrong record type descriptions
* wrong model names
* wrong file type support statements
* missing environment variables
* missing deployment steps
* missing operational warnings
* missing chatbot integration requirements

Output:

* documentation mismatches
* exact doc updates needed

==================================================
16. 10,000-SCENARIO PRODUCTION RISK PLAN
========================================

Do not generate golden PDF questions.

Instead, create a categorized scenario matrix of production risks.

Categories:

1. Index schema scenarios
2. Skillset/indexer scenarios
3. Table scenarios
4. Diagram/image scenarios
5. Text chunking scenarios
6. Summary scenarios
7. Metadata/citation scenarios
8. Retrieval/query scenarios
9. Exact number/entity scenarios
10. Safety/warning scenarios
11. Operational failure scenarios
12. Data quality scenarios
13. Security/RBAC scenarios
14. Performance/scaling scenarios
15. Documentation/deployment scenarios

For each category, produce:

* scenario description
* example situation
* expected system behavior
* likely current failure
* severity P0/P1/P2
* file/function to inspect
* recommended fix
* test to add later

Do not literally print 10,000 rows unless practical. Instead:

* Think through at least 10,000 scenarios internally.
* Produce a compressed but comprehensive matrix with the most important scenarios.
* Include at least 20 high-value scenarios per category.
* Highlight the top 50 production-critical scenarios.

==================================================
OUTPUT FORMAT
=============

Produce the final audit report in this exact structure:

A. Executive verdict
B. What is already strong
C. Confirmed bugs with file/function references
D. Likely bugs / NEEDS VERIFICATION
E. Index schema gaps
F. Skillset/indexer gaps
G. Table pipeline gaps
H. Diagram/image pipeline gaps
I. Text chunking gaps
J. Summary gaps
K. Metadata/citation gaps
L. Retrieval/chatbot integration gaps
M. Exact-value/entity protection gaps
N. Safety/warning gaps
O. Operational/reliability gaps
P. Data quality gaps
Q. Security/RBAC gaps
R. Performance/scalability gaps
S. Documentation mismatches
T. What can cause wrong chatbot answers
U. What can cause missing citations or wrong citations
V. What can cause missing tables
W. What can cause missing diagrams
X. What can cause stale/outdated answers
Y. What can cause hallucinations
Z. What can cause production failures
AA. P0 fixes to implement first
AB. P1 fixes next sprint
AC. P2/later strategic improvements
AD. Exact code-change plan by file, but do not apply changes
AE. Tests to add later by file
AF. Top 50 production-critical scenarios
AG. Compressed 10,000-scenario risk matrix by category
AH. Final expected improvement after fixes

Final instruction:
Give me the full audit and recommendations only. Do not edit files. Do not create golden evaluation files. Do not create sample questions from PDFs. Do not change production code until I approve.


