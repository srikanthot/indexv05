# PSEG Technical Manuals Indexing — Project Status & Architecture

A team explainer of what we're building, the end-to-end pipeline, current
blockers, and the path forward.

---

## 1. What we're trying to achieve

Build a production-quality **Retrieval-Augmented Generation (RAG)** system for
PSEG's technical manuals (56 PDFs, sizes ranging from 100 KB to 95 MB) so that
a chatbot can answer questions like:

- "What's the conductor spec for 200A 4-wire 277/480V?"
- "Show me figure 4-2 of the meter installation manual"
- "What does the gas restoration playbook say about flood scenarios?"

This requires the search index to be **rich enough** to support:

1. **Text retrieval** — every paragraph searchable, with section context
2. **Figure/diagram retrieval** — vision-analyzed descriptions of every diagram, with bounding-box (bbox) coordinates so the UI can highlight the figure on the citation
3. **Table retrieval** — full tables searchable, PLUS per-row records so queries can hit specific spec lines (the 200A row, the 4/0 conductor row, etc.)
4. **Document-level retrieval** — a per-PDF summary record so high-level "what's in this manual" queries route correctly
5. **Citation precision** — every record carries page number, section path, document revision, effective date, document number, etc.

---

## 2. The end-to-end pipeline

```
┌─────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌──────────────┐
│   56    │──▶│  preanalyze   │──▶│ Azure Blob   │──▶│  Azure AI Search │──▶│  Search      │
│  PDFs   │   │   (offline)   │   │ _dicache/    │   │  Indexer +       │   │  Index       │
└─────────┘   └───────────────┘   └──────────────┘   │  Function App    │   │  ~90K-100K   │
                                                     │  custom skills   │   │  records     │
                                                     └──────────────────┘   └──────────────┘
                                                              │
                                                              ▼
                                                     ┌──────────────────┐
                                                     │ Azure OpenAI     │
                                                     │ embeddings       │
                                                     └──────────────────┘
```

### Stage 1 — preanalyze.py (offline batch job, runs BEFORE indexer)

For each PDF, runs these phases:

**Phase `di` — Document Intelligence layout extraction**
- Calls Azure Document Intelligence prebuilt-layout model
- Extracts: paragraphs (with bounding boxes), figures (with bounding regions), tables (with cell structure), sections (header hierarchy)
- Caches as `_dicache/<pdf>.di.json` blob

**Phase `crops` — Figure cropping (PyMuPDF)**
- For each figure DI detected, renders that page region as a PNG using PyMuPDF
- Caches as `_dicache/<pdf>.crop.<fig_id>.json` blob (contains base64 image + bbox)

**Phase `vision` — Per-figure vision analysis (gpt-4.1)**
- For each cropped figure, calls Azure OpenAI vision with the image + section context
- Gets back: diagram description, category (schematic/nameplate/block-diagram/etc.), OCR'd labels, whether the figure is "useful" (vs decorative)
- Caches as `_dicache/<pdf>.vision.<fig_id>.json` blob

**Phase `sections` — Section index build**
- Walks DI sections, builds a hierarchical section tree
- Caches as `_dicache/<pdf>.sections.json`

**Phase `output` — Assembly**
- Reads all the above caches
- Produces final `_dicache/<pdf>.output.json` containing:
  - `enriched_figures`: array of figures with bbox + caption + section path + cached vision result
  - `enriched_tables`: array of tables with markdown + cell structure + section path
  - Cover metadata: document_revision, effective_date, document_number
  - pdf_total_pages

**Why offline?** Vision analysis is slow (5-15s per figure). Doing it inline during indexing would blow past the 230s WebApi skill timeout. We pre-compute everything once and cache it.

### Stage 2 — Azure AI Search Indexer (online, runs on schedule)

The indexer:
1. Discovers PDFs in the blob container (datasource)
2. For each PDF, runs the skillset (described below)
3. For each "record" produced by the skillset, writes it to the search index
4. AOAI embeddings (Azure built-in skill) are generated for every record's `chunk_for_semantic` field

Schedule: every 5 minutes (PT5M). Auto-fires when new blobs are detected.

### Stage 3 — The Skillset (7 custom skills + 2 Azure built-in)

Pipeline order (Azure resolves dependencies automatically):

**Skill 1: layout-skill** (Azure built-in)
- Input: PDF blob
- Output: markdown_document (paginated)

**Skill 2: split-pages** (Azure built-in)
- Input: markdown_document
- Output: pages array (each page is one chunk, 1200 chars with 200 char overlap)

**Skill 3: process-document-skill** (custom WebApi → function app)
- Input: source_file, source_path
- Calls function app, which reads the cached `output.json` from blob
- Output: enriched_figures, enriched_tables, pdf_total_pages, cover_meta fields

**Skill 4: extract-page-label-skill** (custom, runs PER page chunk)
- Input: page text, section content, page number, headers
- Computes: printed page label, page span, text bbox coordinates, figure/table/section references, equipment IDs, callouts, footnotes, OCR confidence, language detection, quality score
- Emits one **text record** per chunk

**Skill 5: analyze-diagram-skill** (custom, runs PER figure)
- Input: image_b64 (from cached crop), caption, headers
- Checks precomputed vision cache, uses cached result if available
- Computes image hash, perceptual hash for cross-doc dedup
- Emits one **diagram record** per figure

**Skill 6: shape-table-skill** (custom, runs PER table)
- Input: table markdown, structure, caption
- Splits table into chunks if >3000 chars
- Builds per-row records for tables in 5-5000 row band
- Emits one **table record** AND multiple **table_row records**

**Skill 7: build-semantic-string-text/diagram** (custom)
- Builds the `chunk_for_semantic` string that gets embedded
- Adds source file, section path, page number prefix for retrieval context

**Skill 8: build-doc-summary-skill** (custom, runs ONCE per PDF)
- Input: full markdown content, section titles, cover metadata
- Calls AOAI chat to generate a 300-500 word summary
- Emits one **summary record** per doc

**Skill 9: embed-text-chunks / embed-summary** (Azure built-in AzureOpenAIEmbedding)
- Calls AOAI embeddings (text-embedding-ada-002) on the chunk_for_semantic field
- Produces 1536-dim vector for each record
- Vector is what the chatbot's semantic search hits

### Stage 4 — Index projections (the "fan-out")

The skillset emits 5 record types to the search index:

| Record type | When emitted | Approx count for one big PDF |
|---|---|---|
| `text` | per page chunk | 1,000-3,000 |
| `diagram` | per figure | 100-1,500 |
| `table` | per table | 10-100 |
| `table_row` | per row in 5-5000-row tables | 1,000-30,000 ← biggest source |
| `summary` | per PDF | 1 |

**Total records per big PDF: 5,000-35,000.**

---

## 3. Where we are right now

### Progress to date

- **42 of 56 PDFs** indexed successfully (small-to-medium docs)
- Total chunks in index: ~32,000
- **14 big PDFs (>30 MB)** are stuck in a failure loop

### The 14 stuck PDFs

| PDF | Size |
|---|---|
| ED-EM-SSM.pdf | 95.46 MB |
| ED-ED-OTC.pdf | 84.06 MB |
| ED-ED-OHC.pdf | 83.32 MB |
| ED-ED-UGC.pdf | 78.38 MB |
| ED-EO-RTM.pdf | 61.61 MB |
| GS-AS-GAP.pdf | 54.69 MB |
| ED-DC-CDS.pdf | 52.22 MB |
| GD-GD-GDS.pdf | 50.78 MB |
| ...+ 6 others 30-50 MB | |

### The failure pattern

1. Indexer triggers run (scheduled every 5 min, runs up to 2 hours)
2. Begins processing all 56 PDFs in parallel through the skillset
3. Small PDFs complete in 5-15 minutes each → records committed to index
4. Big PDFs are still processing thousands of records when the 2-hour wall hits
5. Indexer aborts with status "Failed" or "Partial Success"
6. **Big PDFs go on the indexer's "failed items" list**
7. Next scheduled run skips the failed items → "Success 6s, 0 docs" runs
8. Manual reset required to retry → cycle repeats

---

## 4. Why we're stuck — the math

For ED-EM-SSM.pdf (95 MB):

- ~800-1500 pages
- ~3 text chunks/page → **~3,000 text records**
- ~2 figures/page → **~2,000 diagram records**
- ~1 table/page × avg 30 rows → **~30,000 per-row records**
- Plus ~1,000 parent table records + 1 summary record

**Total: ~36,000 records for ONE doc.**

Each record fans through:
- 1 enrichment skill (extract_page_label, analyze_diagram, shape_table)
- 1 build_semantic_string call
- 1 AOAI embedding call (Azure built-in skill)
- 1 index write

**~4 skill operations per record × 36,000 records = ~144,000 operations for one big PDF.**

At measured average of 100ms per skill call and current `degreeOfParallelism=2`:

**144,000 × 100ms ÷ 2 = 7,200 seconds = 2 HOURS.**

**One big PDF alone consumes the entire 2-hour indexer execution budget.**

With 10+ big PDFs competing for the same 2-hour budget, none of them complete.
The indexer hits 2 hours, gives up, marks docs as failed.

---

## 5. Why each error type happens

### Error: "Web Api response status: 'InternalServerError'"

The function app's worker process crashed mid-call. Most often this is **memory pressure**:

- A 95 MB PDF has figures that, when base64-decoded and processed by PIL for perceptual hashing, can allocate 100-300 MB per image
- With multiple concurrent calls in the same worker (16 threads × 4 workers = 64 concurrent capacity), large images stack up in memory
- Linux OOM killer terminates the worker → all in-flight requests return 500 with no Python stack trace

We've addressed this in code by:
- Skipping perceptual hash on images > 8 MB
- Capping PIL MAX_IMAGE_PIXELS at 30 megapixels
- Catching MemoryError explicitly

### Error: "0 traces, 0 events" for a 500 response

Means the worker died at the OS level before any Python log line was emitted. Confirms it's an OOM crash, not a Python exception.

### Error: Indexer "Failed" with 0 docs succeeded after 2 hours

Even when individual skill calls succeed, big PDFs can't complete ALL their records (text + figures + tables + rows + summary) within the 2-hour indexer execution window. With even one record failure, the parent doc isn't marked complete.

### Error: "Success 6s, 0 docs" repeatedly after failures

Failed items go on Azure Search's persistent failed-items list. Subsequent scheduled runs skip those items → indexer has nothing to do → exits in 6 seconds.

---

## 6. What we've done so far

### Code fixes (all deployed to function app v05)

| Fix | Why |
|---|---|
| AOAI `max_retries=0, timeout=60s` | Was retrying for up to 5 minutes per call, blowing the 230s skill timeout |
| Per-scope token cache + IMDS retry | AAD token storms during vision bursts |
| Sharded fixed-size lock arrays | Lock-dict eviction race causing concurrent rebuilds and OOM |
| LRU cache bound 5 → 32 | 50-PDF batches were thrashing the per-PDF derived-data cache |
| Pre-normalized section headers | 12 million regex calls per huge PDF eliminated |
| Lazy phash computation | Deferred PIL work until cache-miss path only |
| PIL image size guards | Skip phash on > 8 MB images, cap at 30 MP |
| Narrowed exception scope on vision call | Auth errors now propagate instead of silently emitting empty records |
| Vision-empty status (not skipped_decorative) | Empty descriptions now retry on next run instead of permanent skip |
| ROW_RECORD_MAX_ROWS 80 → 5000 | Per-row records were being dropped for big tables |
| maxFailedItems 200 → 1000 | More tolerance before the indexer aborts |
| Tightened TOC and equipment-ID regexes | Bounded ReDoS-prone patterns |
| `_EQUIPMENT_ID_RE` bounded quantifiers | Catastrophic backtracking risk on PSEG part numbers |
| Sharded sections-build locks | Per-PDF concurrent build was double-allocating |
| `auto_heal_timer` (new Azure Function) | Self-heals stuck blobs every 30 min: bumps metadata + resetdocs + triggers run |

### Configuration changes

| Setting | Before | After |
|---|---|---|
| `degreeOfParallelism` on per-record skills | 1 | 2 (current) |
| `maxFailedItems` / `maxFailedItemsPerBatch` | 200/200 | 1000/1000 |
| `FUNCTIONS_WORKER_PROCESS_COUNT` | (default 1) | 4 |
| `PYTHON_THREADPOOL_THREAD_COUNT` | (default 1) | 16 |
| `functionTimeout` | (default 10 min) | 30 min |
| `AUTO_HEAL_ENABLED` | true (initially) | currently false, will re-enable |

---

## 7. What's left — the unblocking path

### Step 1 — Raise dop further (current: 2 → target: 4-6 on heavy skills)

Edit `search/skillset.json` in the Search service Portal:

- `extract-page-label-skill`: dop 2 → **6** (most-called, 59k calls/2h)
- `analyze-diagram-skill`: dop 2 → **4**
- `shape-table-skill`: dop 2 → **4**
- `build-semantic-string-text`: dop 2 → **4**
- `build-semantic-string-diagram`: dop 2 → **4**

This cuts the per-doc processing time by 2-3x. One big PDF goes from ~2 hours to ~40 minutes.

### Step 2 — Enable `auto_heal_timer`

Set `AUTO_HEAL_ENABLED=true` in function app environment variables. Every 30 min, it detects stuck blobs and force-retries them — this mimics V04's "keep running until everything succeeds" behavior.

### Step 3 — If dop=6 still not enough, upgrade Function App SKU

Current SKU is likely **EP1** (3.5 GB RAM per worker). Upgrade to **EP2** (7 GB) or **EP3** (14 GB) gives memory headroom for higher dop without OOM.

Cost: roughly 2-3x current Function App billing. Trade-off for reliable processing of big PDFs.

### Step 4 — Fallback: smart row-record filtering

If even SKU upgrade isn't enough, implement noise filtering in `_build_row_records_for_cluster` (in `function_app/shared/tables.py`):

- Skip rows that are mostly empty (< 20 chars meaningful text)
- Skip pure numeric/symbol rows with no descriptive text
- Skip duplicate rows within the same table

This preserves the row-record retrieval feature but reduces noise rows by 40-60%, cutting total records per big PDF accordingly.

---

## 8. Why V04 worked (and what we lost)

The earlier V04 indexer reached 92,000 records across all 56 PDFs and is referenced as our baseline. V04 worked because:

1. **Simpler skillset** — no vision-analyzed diagrams, no bbox highlighting, no per-row records, no summary records, no cover metadata
2. **Fewer failure modes per record** — each record only went through ~1-2 skills instead of 4
3. **Manual reset cycles** — the V04 indexer was reset between failed runs (someone on the team did this; the "no intervention" perception was incorrect)

**The current v12 v05 has MORE features but MORE failure surface.** Every record type we added (vision diagrams, bbox, row records, summary, cover_meta) is a real retrieval-quality improvement, but also a new failure mode. We're trading off retrieval depth against indexing reliability.

The path forward keeps all V04's reliability (auto_heal mimics manual reset) while preserving all the new features.

---

## 9. Open risks

1. **AOAI quota exhaustion** during heavy parallel embedding calls. Mitigation: Azure Search's built-in skill auto-throttles on 429s.
2. **Storage account throttling** during simultaneous reads of cached blobs. Mitigation: shared httpx client with 50-connection pool + 429 Retry-After handling.
3. **Function App scale-up cost** if EP2/EP3 needed. Mitigation: optimize first, scale only if necessary.
4. **Big-PDF re-indexing** when source PDFs change. Mitigation: change detection via lastModified means only changed PDFs re-process.

---

## 10. Glossary

- **dop** = `degreeOfParallelism` — Azure Search setting controlling how many parallel HTTP calls the indexer makes to a custom skill at once.
- **DI** = Azure Document Intelligence (prebuilt-layout model). Extracts structured content from PDFs.
- **AOAI** = Azure OpenAI. Used for vision (gpt-4.1), chat (gpt-4.1), and embeddings (text-embedding-ada-002).
- **MI** = Managed Identity. The function app authenticates to other Azure services via MI, no API keys.
- **resetdocs** = Azure Search REST API to clear failed-items state for specific blobs. Not exposed in the Portal UI.
- **High-water-mark** = Azure Search indexer's tracking of the latest `metadata_storage_last_modified` it's seen. Blobs with older timestamps are skipped unless reset.
- **auto_heal_timer** = New timer-triggered function in our function app. Fires every 30 min, detects stuck blobs, force-retries them.
- **EP1/EP2/EP3** = Azure Functions Premium plan SKUs. EP1=3.5GB RAM, EP2=7GB, EP3=14GB per instance.

---

## 11. Quick status snapshot to share

> The indexer is currently stuck on the 10-14 largest PDFs in the corpus. These PDFs generate 5,000-35,000 records each, which combined with the indexer's 2-hour execution window doesn't leave enough time for them to fully complete. The fix is to raise concurrent skill calls (degreeOfParallelism) from 2 to 6 on the high-volume skills and enable the auto_heal_timer feature that retries stuck blobs every 30 min. If that's still insufficient, we'll need to upgrade the Function App's compute SKU for more memory headroom. The retrieval feature set (vision diagrams, bbox highlighting, per-row table records, document summaries, cover metadata) is fully preserved — we're tuning for capacity, not removing features.

---

*Document version: 2026-05-14. Update as configuration changes are applied.*
