# Azure AI Search Index Guide

A plain-language reference for the Azure AI Search index that powers this
RAG system. Read this when you need to understand what the index holds,
how search works against it, what every field means, and which skills
transform PDFs into indexed records.

For the end-to-end pipeline architecture (preanalyze, custom skills,
cache), see [ARCHITECTURE.md](ARCHITECTURE.md). This guide focuses on
the search/index side only.

---

## 1. What is a search index?

A search index is a database optimized for finding things by text and
meaning, not by primary key. Azure AI Search indexes are:

- **Schema-defined**: you declare fields (name, type, and properties).
- **Inverted**: each word is mapped to documents that contain it (for
  fast text search).
- **Vectorized**: each record can carry a vector embedding for
  semantic/similarity search.
- **Filterable / sortable / facetable**: fields can be marked so the UI
  can slice by category, page range, date, etc.

An index lives inside an Azure AI Search service (our service name is
configured in `deploy.config.json` under `search.endpoint`).

## 2. What is search?

Search here means three things working together:

| Mode | How it finds results | Best at |
|---|---|---|
| **Keyword (BM25)** | Inverted index + TF-IDF-like ranking | Exact part numbers, product codes, literal phrases |
| **Vector** | Embeddings + cosine similarity (HNSW graph) | Conceptual queries, paraphrases, synonyms |
| **Semantic reranker** | L2 reranking on top of keyword/vector hits using Microsoft's transformer model | Lifting the most relevant chunks to the top, even when they would have been ranked lower by BM25/vector alone |

We use all three in **hybrid mode**: the search query is issued as both
keyword + vector, the results are merged, and the semantic reranker
re-orders the top 50 before returning.

This combination handles a wide range of technical-manual queries:

- "W130537" -> keyword (exact part number)
- "how do I install a BUD cable?" -> vector + reranker
- "underground distribution tables on page 18" -> keyword + filter on page

## 3. Index vs indexer vs skillset

Three related but distinct things:

| Object | Purpose | Lives in |
|---|---|---|
| **Index** | The schema + data store. Holds every chunk/record a user can search. | `search/index.json` |
| **Data source** | Points at the blob container that holds the source PDFs. | `search/datasource.json` |
| **Skillset** | An ordered pipeline of transforms. Runs on each source blob to turn it into one or more index records. | `search/skillset.json` |
| **Indexer** | The scheduler + executor. Pulls blobs from the data source, runs the skillset over each, and writes results to the index. | `search/indexer.json` |

Runtime relationship:
```
  Blob container            Data source -> Indexer -> Skillset
  (PDFs, _dicache)                         |              |
                                           v              v
                                           Index         writes records
                                           (our schema)
```

## 4. Our index schema, field by field

Schema source of truth: [search/index.json](../search/index.json).

### 4.1 Identity fields

| Field | Type | Purpose |
|---|---|---|
| `id` | string | Record key. Auto-generated, unique per chunk. |
| `chunk_id` | string | Stable human-readable id for the chunk. Shared across re-indexing if content is unchanged. |
| `parent_id` | string | Hash of the source PDF URL. Groups all records from one PDF. |
| `text_parent_id`, `dgm_parent_id`, `tbl_parent_id`, `sum_parent_id` | string | Only one is populated, per record. They tell you the record-type's parent pointer. Useful for "show me every record from this PDF that came from the text path". |
| `record_type` | string | `text`, `diagram`, `table`, or `summary`. Filter on this to restrict queries to a type. |

### 4.2 Content fields

| Field | Type | Purpose |
|---|---|---|
| `chunk` | string, searchable | Raw chunk text (markdown for text chunks, GPT-4V description for diagrams, markdown for tables). Primary content users see in the citation UI. |
| `chunk_for_semantic` | string, searchable | Chunk augmented with source + section headers + page info, tuned for semantic ranking. |
| `text_vector` | Collection(Single), 1536 dims | ada-002 embedding of `chunk_for_semantic`. Used for vector search. Not retrievable to keep payloads small. |

### 4.3 Page + location fields

| Field | Type | Purpose |
|---|---|---|
| `physical_pdf_page` | int | First physical page number in the PDF (1-indexed). For citation links like "jump to page 42". |
| `physical_pdf_page_end` | int | Last physical page. For chunks that span pages. |
| `physical_pdf_pages` | Collection(int), filterable/facetable | Every physical page this chunk touches. Use with `any(p: p eq 42)` filters. |
| `printed_page_label` | string | The label as printed on the page (`"iv"`, `"18-33"`, `"A-12"`). For reader-friendly citations. |
| `printed_page_label_end` | string | End label for multi-page chunks. |
| `layout_ordinal` | int | The DI section's ordinal position in the document. Useful for reconstructing document order. |

### 4.4 Section header chain

| Field | Type | Purpose |
|---|---|---|
| `header_1` / `header_2` / `header_3` | string | The h1/h2/h3 heading chain the chunk sits under. E.g. `"Chapter 18"` / `"18.2 Fusing"` / `"18.2.1 General"`. Used for breadcrumb citations and semantic ranking hints. |

### 4.5 Diagram-only fields

Populated for `record_type = "diagram"` records:

| Field | Type | Purpose |
|---|---|---|
| `figure_id` | string | DI-assigned figure identifier (e.g. `"134.3"`). |
| `figure_ref` | string | Human reference as the manual writes it (e.g. `"Figure 18.117"`). Often differs from figure_id. |
| `figure_bbox` | string (JSON) | `{page, x_in, y_in, w_in, h_in}`. Used by the UI to highlight the figure inside the source PDF page. |
| `diagram_description` | string, searchable | GPT-4V description + OCR labels. The content a user actually reads when they click a diagram result. |
| `diagram_category` | string, filterable (keyword analyzer) | One of: `circuit_diagram`, `wiring_diagram`, `schematic`, `line_diagram`, `block_diagram`, `pid_diagram`, `flow_diagram`, `control_logic`, `exploded_view`, `parts_list_diagram`, `nameplate`, `equipment_photo`, `decorative`, `unknown`. |
| `has_diagram` | bool | True only if `is_useful && category in useful set && description is non-empty`. Use as a filter to exclude junk. |
| `image_hash` | string | SHA-256 of the cropped PNG. Used for dedup of repeated logos/symbols. |

### 4.6 Table-only fields

Populated for `record_type = "table"` records:

| Field | Type | Purpose |
|---|---|---|
| `table_row_count` | int | Number of data rows in this chunk (after splitting oversized tables). |
| `table_col_count` | int | Number of columns. |
| `table_caption` | string, searchable | The caption text above the table in the source PDF. |

### 4.7 Source-reference fields

| Field | Type | Purpose |
|---|---|---|
| `source_file` | string, searchable/filterable/sortable/facetable | Just the PDF filename. For faceted navigation ("filter to this manual"). |
| `source_url` | string | Full blob URL. The UI links users to the source blob. |
| `source_path` | string, filterable | Same as source_url. Used in filter expressions. |

### 4.8 Provenance + health

| Field | Type | Purpose |
|---|---|---|
| `surrounding_context` | string, searchable | A few sentences around the figure/table from the surrounding body text. Makes results readable without opening the PDF. |
| `processing_status` | string, filterable/facetable | `"ok"`, `"no_image"`, `"content_filter"`, etc. Filter to `eq 'ok'` for clean retrieval. |
| `skill_version` | string | Tracks which version of the custom skills wrote the record. Useful when detecting mixed old/new records after schema changes. |

### 4.9 Admin classification fields (currently null)

Recently added to the schema for future classification. Populated via
REST API or a future skill, not by the current pipeline:

| Field | Type | Purpose |
|---|---|---|
| `operationalarea` | string, searchable | Operational area the PDF belongs to (e.g. `"underground_distribution"`). |
| `functionalarea` | string, searchable | Functional scope (e.g. `"power_management"`). |
| `doctype` | string, searchable | Document type (e.g. `"manual"`, `"policy"`, `"drawing"`). |

Leave these null until you add a classification step -- the pipeline
doesn't write to them today.

## 5. Semantic configuration

Semantic ranker settings from [search/index.json](../search/index.json):

```json
"semantic": {
  "defaultConfiguration": "mm-semantic-config",
  "configurations": [{
    "name": "mm-semantic-config",
    "prioritizedFields": {
      "titleField": { "fieldName": "source_file" },
      "prioritizedContentFields": [
        { "fieldName": "chunk_for_semantic" },
        { "fieldName": "chunk" },
        { "fieldName": "diagram_description" },
        { "fieldName": "surrounding_context" }
      ],
      "prioritizedKeywordsFields": [
        { "fieldName": "header_1" }, { "fieldName": "header_2" },
        { "fieldName": "header_3" }, { "fieldName": "figure_ref" },
        { "fieldName": "table_caption" }, { "fieldName": "printed_page_label" },
        { "fieldName": "diagram_category" }
      ]
    }
  }]
}
```

Interpretation:
- The ranker treats `source_file` as the "title" of each record.
- Content-priority fields are re-read with extra attention by the ranker.
- Keyword-priority fields bias matches for header chain, figure ref,
  table caption, page label, and category.

## 6. Vector search configuration

```json
"vectorSearch": {
  "algorithms": [{
    "name": "mm-hnsw-algo",
    "kind": "hnsw",
    "hnswParameters": { "m": 8, "efConstruction": 400, "efSearch": 500, "metric": "cosine" }
  }],
  "profiles": [
    { "name": "mm-hnsw-profile", "algorithm": "mm-hnsw-algo", "vectorizer": "aoai-vectorizer" }
  ],
  "vectorizers": [{
    "name": "aoai-vectorizer",
    "kind": "azureOpenAI",
    "azureOpenAIParameters": {
      "resourceUri": "<AOAI endpoint>",
      "deploymentId": "<your embedding deployment>",
      "modelName": "text-embedding-ada-002"
    }
  }]
}
```

- **HNSW**: approximate nearest neighbor graph. Fast vector search.
- **m=8, efConstruction=400, efSearch=500**: conservative graph that
  trades slightly higher build time for strong recall.
- **cosine**: the metric for embeddings this model produces.
- **vectorizer**: at query time the search service embeds the query text
  itself using the same ada-002 model. You don't need to embed queries
  client-side.

## 7. Our skillset, skill by skill

Skillset source of truth: [search/skillset.json](../search/skillset.json).

The skillset runs for every source PDF the indexer sees. Skills execute
in dependency order.

### 7.1 Built-in skills (no code, configured via JSON)

#### layout-skill (DocumentIntelligenceLayoutSkill)

- **Input**: the PDF file bytes.
- **Output**: a collection `markdownDocument[*]`, each with `content`
  (section markdown), and `sections` (h1/h2/h3 objects).
- **Purpose**: turn the PDF into markdown while preserving header
  structure and page markers.

#### split-pages (SplitSkill)

- **Input**: each section's `content` (markdown).
- **Output**: an array `pages` of ~1200-char overlapping chunks.
- **Purpose**: break long sections into chunks small enough to embed and
  retrieve precisely, with a 200-char overlap so sentence boundaries
  don't cut important context.

#### embed-text-chunks, embed-diagram-chunks, embed-table-chunks, embed-summary (AzureOpenAIEmbeddingSkill)

- **Input**: the chunk_for_semantic string for the record type.
- **Output**: a 1536-dim vector.
- **Purpose**: produce embeddings used by the index's `text_vector`
  field for vector search.

### 7.2 Custom skills (our Function App)

#### process-document-skill

- **Context**: once per source PDF.
- **Reads**: the DI cache blob (`_dicache/<pdf>.di.json`) written by
  preanalyze.
- **Outputs**:
  - `enriched_figures` - one entry per qualifying figure with cached
    crop + DI metadata.
  - `enriched_tables` - one entry per table (possibly split for size)
    with markdown + page range + caption.
  - `processing_status` - ok or needs_preanalyze.
- **Fails fast** if the cache is missing (we removed the live-DI fallback
  because it always timed out on big PDFs).

#### extract-page-label-skill

- **Context**: one call per text chunk (after split-pages).
- **Reads**: chunk text, section content, header_1/2/3 (for section matching).
- **Outputs**: `physical_pdf_page`, `physical_pdf_page_end`,
  `physical_pdf_pages`, `printed_page_label`, `printed_page_label_end`,
  `chunk_id`, `parent_id`, `record_type="text"`, `figure_ref`,
  `processing_status`, `skill_version`.
- **Purpose**: attach precise page info to every text chunk, using the
  DI cache as a fallback when the Layout Skill doesn't expose section
  pageNumber directly.

#### analyze-diagram-skill

- **Context**: one call per figure in `enriched_figures`.
- **Reads**: the pre-cropped image (from cache) and the pre-computed
  vision result (from `_dicache/<pdf>.vision.<fig>.json`).
- **Outputs**: `chunk_id`, `parent_id`, `record_type="diagram"`,
  `figure_id`, `figure_bbox`, `physical_pdf_page/end`, `header_1/2/3`,
  `diagram_description`, `diagram_category`, `figure_ref`,
  `has_diagram`, `image_hash`, `processing_status`, `skill_version`.

#### shape-table-skill

- **Context**: one call per table in `enriched_tables`.
- **Outputs**: `chunk_id`, `parent_id`, `record_type="table"`,
  `table_caption`, `table_row_count`, `table_col_count`,
  `physical_pdf_page/end`, `physical_pdf_pages`, `header_1/2/3`,
  `processing_status`, `skill_version`.

#### build-semantic-string-text, build-semantic-string-diagram

- **Context**: once per chunk / figure.
- **Outputs**: the `chunk_for_semantic` string that the embedding skill
  then vectorizes. We build it to include source + header chain + page
  info + caption, which the semantic ranker uses as signal.

#### build-doc-summary-skill

- **Context**: once per PDF.
- **Outputs**: a document-level summary record with `record_type="summary"`.
- **Purpose**: enables "tell me what this manual is about" queries.

### 7.3 Index projections

Projections map the skillset's nested outputs into flat index records.
Four parent-key selectors:

| Selector | Produces records with | record_type |
|---|---|---|
| `text_parent_id` | One per text chunk | `"text"` |
| `dgm_parent_id` | One per figure | `"diagram"` |
| `tbl_parent_id` | One per table | `"table"` |
| `sum_parent_id` | One per PDF | `"summary"` |

## 8. How to create (deploy) the index

You don't hand-create the index. It's deployed from `search/index.json`
via the deploy script.

```powershell
# Pre-req: az login + deploy.config.json filled in
python scripts/deploy_search.py --config deploy.config.json
```

What the script does:

1. Reads `deploy.config.json`.
2. Fetches the Function App host + function key.
3. Reads all four JSON files (`datasource.json`, `index.json`,
   `skillset.json`, `indexer.json`).
4. Substitutes placeholders (`<INDEX_NAME>`, `<FUNCTION_APP_HOST>`,
   `<FUNCTION_KEY>`, etc.) with real values.
5. PUTs each artifact to the Azure AI Search REST API using AAD auth.

Existing artifacts with the same name get updated in place. To rebuild
from scratch you must delete the index first (portal or REST DELETE).

### Reset and re-run the indexer

After you deploy (or after a schema change), reset the indexer so
it re-processes every blob:

```powershell
./scripts/reset_indexer.ps1
```

Or in the Azure portal: Search service -> Indexers -> your indexer ->
Reset -> then Run.

## 9. What's in our current index (summary)

Our index name is configured by `search.artifactPrefix` in
`deploy.config.json` (e.g. `mm-manuals-index`, or `techmanuals-v03-index`).

A fully populated run typically has:

- One `summary` record per PDF
- Hundreds to thousands of `text` records per PDF (every split chunk)
- One `diagram` record per useful figure
- One `table` record per extracted table (possibly split for size)

Sanity checks you can run:

```powershell
# Count total records (run in portal's Search explorer)
?search=*&$count=true&$top=0

# Count per record_type
?search=*&facet=record_type,count:5

# Count per source_file
?search=*&facet=source_file,count:50

# Quick query
?search=what is BUD&queryType=semantic&semanticConfiguration=mm-semantic-config&answers=extractive|count-3&captions=extractive&top=5
```

## 10. How to query the index

### Simple text search

```http
POST /indexes/<index-name>/docs/search?api-version=2024-11-01-preview
Authorization: Bearer <aad-token>

{
  "search": "buried underground distribution"
}
```

### Semantic + hybrid (recommended for user-facing queries)

```json
{
  "search": "buried underground distribution",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "captions": "extractive",
  "answers": "extractive|count-3",
  "vectorQueries": [{
    "kind": "text",
    "text": "buried underground distribution",
    "fields": "text_vector"
  }],
  "top": 10
}
```

### Restrict to diagrams only

```json
{
  "search": "fault indicator",
  "filter": "record_type eq 'diagram' and has_diagram eq true",
  "top": 20
}
```

### Restrict to a page range within one PDF

```json
{
  "search": "fusing",
  "filter": "source_file eq 'ED-ED-UGC.pdf' and physical_pdf_page ge 1000 and physical_pdf_page le 1100"
}
```

### Find every page a chunk touches (collection filter)

```json
{
  "search": "*",
  "filter": "physical_pdf_pages/any(p: p eq 1337)"
}
```

### Find a specific figure

```json
{
  "search": "*",
  "filter": "figure_ref eq 'Figure 18.117' and record_type eq 'diagram'"
}
```

### Retrieve only what the citation UI needs

```json
{
  "search": "...",
  "select": "chunk_id, source_file, physical_pdf_page, printed_page_label, header_1, header_2, header_3, chunk, figure_bbox, record_type",
  "top": 5
}
```

## 11. Common operations

### Verify index health

```powershell
python scripts/check_index.py --config deploy.config.json
```

Shows document count, per-type breakdown, and flags any fields that are
null on 100% of records (a schema vs. skillset mismatch signal).

### Check indexer status

```powershell
az rest --method get `
  --url "https://<search>.search.azure.us/indexers/<name>/status?api-version=2024-11-01-preview" `
  --resource "https://search.azure.us" -o json
```

### Clear the index (keep schema, drop documents)

Azure AI Search doesn't have a native "truncate". To clear:

1. Delete the index (portal or REST DELETE).
2. Redeploy via `deploy_search.py`.
3. Reset and run the indexer to repopulate.

### Delete a single PDF's records

```http
POST /indexes/<index-name>/docs/index?api-version=2024-11-01-preview
{
  "value": [
    { "@search.action": "delete", "id": "<record-id>" }
  ]
}
```

Or query for all chunks of that PDF first:

```json
{ "search": "*", "filter": "source_file eq 'OLD.pdf'", "select": "id", "top": 10000 }
```

then batch-delete by id.

## 12. Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| `text_vector` search returns nothing | Embedding dimension mismatch | Confirm `text_vector.dimensions == 1536` and the AOAI deployment is ada-002. |
| Text chunks have `physical_pdf_page: null` | Old data from before the DI-cache fallback fix | Redeploy function app, reset + run indexer. |
| Diagram records have `diagram_description: ""` | Vision API failed (rate limit or content filter) | Check `processing_status`; re-run preanalyze to retry transient failures. |
| Indexer shows 0 documents after 30 min | Indexer was fired before the `.pdf`-only filter was deployed OR first big PDF still processing | Check execution history; cancel + reset + run if using old config. |
| Wrong indexer index count vs. actual blobs | Azure CLI blob list defaulting to 5000 cap | Use `--num-results *` or count via the REST API. |
| `Execution time quota of 120 minutes reached` | Normal for large fresh loads | The 15-minute schedule auto-resumes. |

## 13. Related docs

- [ARCHITECTURE.md](ARCHITECTURE.md) - end-to-end pipeline design and
  why we preanalyze
- [scripts/PREANALYZE_README.md](../scripts/PREANALYZE_README.md) -
  team-facing runbook for preanalyze
- [search/index.json](../search/index.json) - exact schema
- [search/skillset.json](../search/skillset.json) - exact skillset
- [search/indexer.json](../search/indexer.json) - indexer config
- [search/datasource.json](../search/datasource.json) - blob datasource
