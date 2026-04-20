# Architecture Deep-Dive: Why This Pipeline Looks The Way It Does

This document explains the design decisions behind the indexing pipeline, the
Azure AI Search skills we use, why we needed a pre-analysis step, what changes
between Azure Commercial and Azure Government, and how the system behaves for
very large technical manuals.

It is intended for engineers joining the project, for reviewers evaluating the
architecture, and for anyone asking "why not just use the Azure AI Search
wizard?"

---

## 1. What this system does

Given a blob container of PDF technical manuals, it:

1. Extracts text sections, tables, and figures with Document Intelligence.
2. Runs GPT-4 Vision on each figure to produce a retrievable description
   plus OCR of every label, part number, wire tag, and callout.
3. Chunks text with overlap, preserving header chain and page numbers.
4. Generates embeddings (ada-002) for every chunk.
5. Writes everything to an Azure AI Search index with semantic ranker and
   hybrid (vector + BM25) retrieval.

The index powers a RAG experience where a user can ask "what's connected to
terminal X7 on the overcurrent relay?" and get back the exact figure, its
section context, and a citation to the page in the source PDF.

---

## 2. The full pipeline end to end

```
Blob container (PDFs)
        |
        v
+------------------------+
|  preanalyze.py         |   <-- runs OFFLINE, caches to blob
|  (scripts/)            |
+------------------------+
        |
        |  Writes to _dicache/ in same container:
        |   - <pdf>.di.json           (DI result)
        |   - <pdf>.crop.<fig>.json   (per-figure PNG + bbox)
        |   - <pdf>.vision.<fig>.json (per-figure GPT-4V output)
        |   - <pdf>.output.json       (assembled enriched output)
        v
+-----------------------------------------------+
|  Azure AI Search indexer                       |
|  --> runs skillset:                            |
|                                                |
|  1. layout-skill       (built-in DI)           |  text markdown extraction
|  2. split-pages        (built-in SplitSkill)   |  chunk text into pages
|  3. extract-page-label (custom -> page_label.py)| page numbers for text
|  4. process-document   (custom -> reads cache) |  pulls figures/tables
|  5. analyze-diagram    (custom -> reads cache) |  one record per figure
|  6. process-table      (custom -> markdown)    |  one record per table
|  7. embed-*            (4x built-in AOAI)      |  vectors
|                                                |
|  --> writes to Azure AI Search index           |
+-----------------------------------------------+
        |
        v
   Index: techmanuals-v02-index
   Each record is either record_type="text", "diagram", "table", or "summary"
```

---

## 3. Azure AI Search skills in use

The skillset uses a mix of **built-in Microsoft skills** and **custom WebAPI
skills** hosted in our Azure Function App.

### Built-in skills (no code, configured in JSON)

| Skill | Purpose |
|---|---|
| `DocumentIntelligenceLayoutSkill` | Turns PDF into markdown with h1-h3 section headers |
| `SplitSkill` | Chunks markdown into ~1200-char pages with 200-char overlap |
| `AzureOpenAIEmbeddingSkill` (x4) | Calls ada-002 to vectorize every chunk (text, diagram, table, summary) |

### Custom WebAPI skills (our Function App)

| Skill | Purpose | Why custom |
|---|---|---|
| `extract-page-label-skill` | Maps every text chunk to its physical PDF page and printed page label | No built-in skill does this |
| `process-document-skill` | Extracts figures + tables per document; reads pre-analyzed cache | DI results too large/slow for built-in path; need per-figure cropping |
| `analyze-diagram-skill` | Produces one record per figure with GPT-4V description and OCR | No native GPT-4V skill in Azure AI Search |
| `process-table-skill` | Emits one record per table with caption, page range, markdown | Tables need their own record type for retrieval |
| `generate-summary-skill` | Produces a per-document summary record | Optional per-doc overview |

All custom skills run in Python inside the Azure Function App defined under
[function_app/](../function_app/).

---

## 4. Why we pre-analyze (the most important design decision)

Pre-analysis exists because **we hit three hard constraints if we tried to do
everything inside custom skills at index time**:

### Constraint 1: The 230-second skill timeout

Every custom WebAPI skill invocation in Azure AI Search has a hard 230-second
(3 min 50 sec) timeout. This is enforced at the service layer and cannot be
increased. It applies to Azure Commercial and Azure Government equally.

### Constraint 2: Document Intelligence on big PDFs takes minutes

For a typical 500-page technical manual with figures and tables, a DI layout
call takes **3-15 minutes**. If we run DI inside a custom skill, the skill
times out before DI finishes. The document fails to index.

### Constraint 3: Vision analysis scales with figure count

A typical manual has **500-1,500 figures**. Each figure needs a GPT-4 Vision
call (~1-3 seconds). Total vision time per PDF: **10-75 minutes**. This cannot
fit into a single 230-second skill invocation no matter how parallel we make
it.

### How preanalyze solves this

We run `scripts/preanalyze.py` offline, BEFORE the indexer:

- DI runs with a 15-minute timeout, not 230 seconds
- Vision calls run in parallel (~40 concurrent) across all figures
- Both results are cached to blob storage under `_dicache/`
- At index time, custom skills read from the cache in milliseconds

The indexer never has to wait for DI or vision calls. Skill invocations finish
well under 230 seconds. Huge PDFs index reliably.

### The cost savings angle

Preanalyze also **protects us from paying vision costs twice**. If we
re-index after a schema change or a bug fix, the vision results are already
cached. We don't re-invoke GPT-4V and re-pay for 1,500 calls per PDF.

---

## 5. Cache layout under `_dicache/`

For a PDF named `manual.pdf`, preanalyze creates these blobs:

| Blob | Contents | When written |
|---|---|---|
| `_dicache/manual.pdf.di.json` | Full DI layout result (markdown, figures, tables) | After DI analyze succeeds |
| `_dicache/manual.pdf.crop.<fig_id>.json` | One per figure: base64 PNG + polygon bbox | After cropping each qualifying figure |
| `_dicache/manual.pdf.vision.<fig_id>.json` | One per figure: GPT-4V JSON output (category, description, OCR, figure_ref) | After each vision call succeeds |
| `_dicache/manual.pdf.output.json` | Final assembled enriched figures + tables | After all phases succeed |

`output.json` is the **completion marker**. If present, the PDF is fully
pre-analyzed and the indexer will skip all the expensive work.

Error-caching: if a vision call fails with a transient error, we store an
`_error` record with an `_attempts` count. After 3 retries across runs we
treat it as permanent (e.g. a content-filter block on a nameplate that
triggered a false-positive violence filter) and stop retrying.

---

## 6. Azure Commercial vs Azure Government

The US Gov Cloud typically lags Commercial by 6-18 months on new features.
Teams often ask "can we skip all this custom code if we move to commercial?"
The honest answer is: **partly yes for simple PDFs, no for technical manuals
at scale**.

### What's the same in both clouds

- The 230-second WebAPI skill timeout
- `DocumentIntelligenceLayoutSkill` (built-in)
- `SplitSkill`, embedding skills
- Document Intelligence API
- Azure OpenAI (though model catalog differs)

### What Commercial has that Gov doesn't yet

| Feature | Commercial | Gov |
|---|---|---|
| Integrated Vectorization wizard | Full support | Partial |
| Multimodal embeddings / vision-aware chunking | Preview/GA | Not available |
| `GenAI Prompt Skill` (built-in GPT-4/4o call) | Preview/GA in some regions | Not available |
| Latest GPT-4o / o-series models | Full catalog | Subset |
| Knowledge Agents / AI Foundry agents | GA | Rolling out |

### Could commercial let us drop preanalyze?

For **simple PDFs** (policy documents, contracts, mostly text, few figures):
Yes. The Import and Vectorize wizard + built-in multimodal skills can
ingest a 50-page contract end to end without any Function App code.

For **technical manuals with 500-2,000 figures each**:
**No.** Even in commercial, the architecture converges to preanalyze for three
reasons:

1. The 230s timeout still applies. Vision across 1,500 figures cannot fit.
2. Built-in multimodal skills in commercial produce **generic image tags**
   ("diagram", "chart", "text"). They do not understand circuit schematics,
   wire tags, terminal labels, or nameplate OCR. For deep technical queries
   ("what is connected to terminal X7 on the overcurrent relay?") generic
   tags fail.
3. Cost. 1,500 vision calls per PDF x every indexer retry x every schema
   iteration. Caching is non-negotiable at scale.

### What would change if we ran on commercial today

- We could replace our custom `analyze-diagram-skill` with `GenAI Prompt Skill`
  to reduce custom-code lines, but still need preanalyze for scale
- Simple non-technical PDFs could go through the wizard instead
- We would get access to GPT-4o and newer vision models for better OCR on
  low-contrast diagrams
- No change to the overall architecture

The preanalyze + cache pattern is correct for technical-manual-scale RAG in
any Azure cloud.

---

## 7. What happens for 500-page and 2000-page PDFs with heavy diagrams

Realistic scenarios we've seen or expect:

### 500-page PDF with ~1,500 figures (typical electrical/mechanical manual)

| Phase | Duration | Notes |
|---|---|---|
| Preanalyze DI | ~5-10 min | 30MB+ PDF sent via SAS URL to avoid POST body limits |
| Preanalyze crop upload | ~1-3 min | Parallel upload of ~1,000 crops after triage |
| Preanalyze vision | ~5-20 min | 40 parallel GPT-4V calls |
| Index (per-PDF contribution) | ~5-15 min | Hits fast cache path, dominated by embeddings |
| **Total end-to-end** | **~30-60 min** | First-time ingestion |

Re-runs (after bugfix, schema change, etc.):
- Preanalyze incremental: seconds (skips fully-cached PDFs)
- Index rerun: minutes (reads cache, re-embeds if schema changed)

### 2,000-page PDF with ~6,000 figures (rare edge case, massive reference manual)

| Phase | Duration | Notes |
|---|---|---|
| Preanalyze DI | ~15-30 min | At the upper edge of DI's server-side limits |
| Preanalyze crop upload | ~5-10 min | |
| Preanalyze vision | ~30-90 min | Mostly bound by AOAI TPM quota |
| Index (per-PDF contribution) | ~15-30 min | |
| **Total end-to-end** | **~2-4 hours** | |

At this scale a few things matter:

- **AOAI deployment TPM quota** is the throughput ceiling. S0 deployments
  cap around 240K TPM for ada-002 and ~80K TPM for GPT-4 vision. For 6,000
  figures the embedding phase alone needs ~1-2 hours on S0.
- **Azure Search indexer 24-hour execution cap** (on S1+ tiers) is not
  reached for a single PDF even at this size, but we still recommend smaller
  batches (one PDF at a time if possible).
- **Preanalyze parallelism**: --vision-parallel 40-48 usually saturates the
  AOAI quota without 429 storms. Higher numbers cause throttling.

### What breaks at scale and how we handle it

| Failure mode | Our mitigation |
|---|---|
| DI timeout on >100MB PDF | `urlSource` path with SAS URL (no POST body limit) |
| Vision 429 rate limit | Bounded Retry-After wait (cap 120s), retry 3x |
| Vision content filter | Cache as permanent failure, never retry |
| Vision JSON parse failure | Cache as transient, retry up to 3 times across runs |
| Cache blob upload flake | Retry 3x with backoff before treating as failure |
| Indexer skill crashes | Per-PDF failure doesn't stop the rest |
| One figure crash in a batch | Logged, next figure continues |
| Blob flake mid-run | All blob ops retry 3x; connection pools timeout at 600s |
| 230s skill timeout | Preanalyze: the whole point |

---

## 8. Text, diagrams, tables - how each becomes a record

### Text chunks

1. `DocumentIntelligenceLayoutSkill` produces section-level markdown
2. `SplitSkill` chops each section into overlapping 1200-char pages
3. `extract-page-label-skill` adds physical_pdf_page + printed_page_label
4. Embedding skill vectorizes the chunk_for_semantic field
5. Index projection writes one record per chunk with `record_type="text"`

Fields: chunk_id, parent_id, chunk, chunk_for_semantic, text_vector,
header_1/2/3, physical_pdf_page(s), printed_page_label(s), figure_ref,
source_file, source_url, skill_version.

### Diagram records

1. Preanalyze already wrote `_dicache/<pdf>.vision.<fig>.json`
2. `process-document-skill` emits `enriched_figures` array
3. `analyze-diagram-skill` runs per figure, reads cache, returns record
4. Embedding skill vectorizes the dgm_chunk_for_semantic field
5. Index projection writes one record with `record_type="diagram"`

Fields: same citation fields + figure_id, figure_bbox, figure_ref,
image_hash, has_diagram, diagram_description, diagram_category,
processing_status.

Key decisions:
- `image_b64` is **not** stored in the index (would bloat it 10-100x). The
  PNG stays in the `_dicache/` blob; the index stores only bbox and hash.
- `diagram_description` contains both the model's narrative description and
  the OCR labels, concatenated so a BM25 query for a part number can hit.
- `image_hash` dedupes identical figures (logos, repeated nameplates).

### Table records

1. DI gives us table cells + bounding regions
2. `tables.py` merges continuation tables, drops repeated header rows
3. Oversized tables (> 3000 chars) split at row boundaries
4. `process-table-skill` emits one record per (possibly split) table
5. Embedding skill vectorizes tbl_chunk_for_semantic

Fields: chunk_id, parent_id, chunk (markdown), table_caption,
table_row_count, table_col_count, physical_pdf_page(s), header_1/2/3,
source_file, source_url.

### Summary records (optional)

One per document. Generated from the first N sections. Useful for "tell me
what this manual is about" type queries.

---

## 9. Format decisions and why

### Why markdown instead of plain text

DI produces markdown by default, and headers survive as `# / ## / ###` so we
can extract `header_1`, `header_2`, `header_3` per chunk. Plain text would
lose the hierarchy and make navigation harder.

### Why chunk size 1200 with 200 overlap

- 1200 chars (~200-300 tokens) is small enough for precise retrieval and
  large enough to preserve local context
- 200 char overlap prevents sentence-boundary cuts losing important context
  at chunk edges
- The reranker downweights near-duplicates so overlap doesn't pollute results

### Why ada-002 for embeddings

- Available in both Commercial and Gov (including Gov's limited catalog)
- 1536 dimensions - good precision-to-cost ratio
- Cheapest per-token of the embedding family
- Hybrid (vector + BM25) retrieval with semantic reranker covers for any
  weakness of the embedding model

### Why GPT-4 Vision (not a smaller model)

- Smaller vision models (3.5-turbo-vision, etc.) don't read tiny technical
  labels reliably
- Nameplate OCR requires recognizing degraded, rotated, low-contrast text
- Technical diagrams have dense component-level information
- Cost is dominated by the number of calls, not the per-call token price,
  and we cache every result

---

## 10. Automation and ongoing operations

### Initial load (first 50 PDFs)

Manual one-time run:
1. `./scripts/run_preanalyze.ps1 -VisionParallel 48` -- expect 1-3 hours for 50 PDFs
2. Delete the existing index (clean slate)
3. `python scripts/deploy_search.py --config deploy.config.json --run-indexer`
4. Verify via portal Execution History that all 50 succeeded

### Steady state - weekly-ish additions and deletions

Two options, documented together so teams can choose:

#### Option A - scheduled batch (simplest)

An Azure Container App Job (or a Windows Task Scheduler on a VM) runs every
4 hours:

```
1. python scripts/preanalyze.py --config deploy.config.json --incremental
2. python scripts/preanalyze.py --config deploy.config.json --cleanup
3. az rest ... /indexers/<name>/run
```

Lag from upload to searchable: up to 4 hours.

#### Option B - event-driven (near real-time)

Event Grid fires on blob Created / Deleted -> queue -> Container App Job
worker -> preanalyze that PDF -> trigger indexer.

Lag from upload to searchable: ~5-15 minutes.

Requires:
- Event Grid subscription on the blob container
- Storage Queue for debouncing
- Container App Job configured for event-triggered execution
- Indexer configured with `NativeBlobSoftDeleteDeletionDetectionPolicy`
- Storage account with blob soft-delete enabled

### Deletion handling

Unless you add a deletion detection policy, PDFs deleted from the blob
**stay in the index forever**. To handle deletions:

1. Enable blob soft-delete on the storage account (Azure portal)
2. Add to `search/indexer.json`:
   ```json
   "dataDeletionDetectionPolicy": {
     "@odata.type": "#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy"
   }
   ```
3. Ensure something periodically runs `preanalyze.py --cleanup` to remove
   orphaned `_dicache/` blobs (otherwise your storage bill grows)

---

## 11. Trade-offs summary

| Decision | Reason | Trade-off accepted |
|---|---|---|
| Preanalyze offline | 230s skill timeout + DI duration + vision call count | Extra step before indexing; one more thing to monitor |
| Cache in blob storage | Cheap, durable, same account as source PDFs | Need to version-control cache schema via skill_version |
| GPT-4 Vision per figure | Deep technical understanding + OCR | Higher cost per figure; mitigated by cache |
| Custom Function App | Built-in skills can't do per-figure vision cache-aware | More infra to deploy and monitor |
| Hybrid vector + BM25 + semantic reranker | Covers embedding model weakness for technical jargon | Slightly higher query latency than pure vector |
| Single index field `text_vector` for all record types | Simpler projections; same ada-002 model | Can't filter by record_type before vector search (filter at search time instead) |
| Record per figure (not per-page) | Precise figure citations | More records in the index |
| Don't store image_b64 in index | Index stays small and cheap | Need separate blob access to fetch the PNG for display |
| 1200/200 chunk settings | Good balance for technical prose | Near-duplicate overlap; reranker handles it |

---

## 12. Frequently asked

### Can we use Azure AI Search's Import and Vectorize Data wizard?

For simple PDFs (mostly text, few figures) - yes, in Azure Commercial. Point
it at the blob container, pick defaults, done. This pipeline is overkill for
that case.

For technical manuals with per-figure vision requirements - no. The wizard's
built-in image handling produces generic tags only.

### Why do we have both preanalyze AND a custom skill that reads the cache?

Preanalyze populates the cache offline. The custom skill reads the cache at
index time. They have to be two separate things because the indexer is what
writes to the search index - preanalyze can't do that alone. The split also
lets us re-run indexing independently of re-running vision (e.g. after a
schema change).

### Can we put preanalyze inside an Azure Function Timer Trigger?

On Consumption plan (10 min timeout), no -- one large PDF exceeds 10 min.
On Premium plan (30 min timeout), yes for typical PDFs but a 2000-page
manual can still exceed 30 min.

Best fit: Azure Container App Job. No execution time limit, pay only when
running, straightforward Docker packaging.

### What if a content filter blocks a legitimate figure?

Preanalyze caches content-filter responses as permanent failures. The figure
will have `processing_status="content_filter"` and no vision description,
but it still appears in the index with its page, section headers, and crop
bounding box. Retrieval via text surrounding context still works for it.

### How do we know if something silently failed?

- Portal -> Search service -> Indexers -> Execution history -> Errors tab
  (for indexer-level failures)
- App Insights or `az webapp log tail` for Function App errors
- `preanalyze.py --status` for per-PDF cache state
- End-of-run error summary prints failed PDFs

If you see `processing_status` not equal to `"ok"` on a chunk in the index,
that's the skill telling you something was off for that specific record.

---

## 13. Related docs

- [README.md](../README.md) - top-level project readme
- [scripts/PREANALYZE_README.md](../scripts/PREANALYZE_README.md) - team-facing
  runbook for running preanalyze
- [docs/validation.md](validation.md) - validation notes
- [search/skillset.json](../search/skillset.json) - full skillset definition
- [search/index.json](../search/index.json) - index schema
- [function_app/shared/](../function_app/shared/) - custom skill implementations
