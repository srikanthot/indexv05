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
