# Integration Guide — Connecting to the Search Index

This is a complete, standalone guide for the teams that consume the search index: the chatbot
back-end (retrieval API) and the front-end (chat UI and PDF viewer). It contains everything
needed to connect, authenticate, query, and use the results — no other document is required.

---

## 1. What the index provides

A single **Azure AI Search index** contains every technical manual, split into small records
with rich citation and safety metadata. The index supports:

- **Keyword (BM25)** search — exact part numbers, codes, literal phrases.
- **Vector search** — 1536-dimension embeddings for concepts, paraphrases, synonyms.
- **Hybrid** — keyword and vector together (the recommended default).
- **Semantic re-ranker** — reorders the top results by relevance and can return captions and
  extractive answers.
- **Integrated vectorizer** — the index embeds the user's query itself, so the consumer does
  **not** need to call an embedding model.

The consumer sends a query, reads the returned records, and uses their fields to build a cited
answer and (optionally) open the source PDF at the correct page and highlight a figure.

---

## 2. Connection details

| Item | Value |
|---|---|
| Cloud | Azure Government |
| Service endpoint | `https://<search-service>.search.azure.us` |
| Index name | `<prefix>-index` (the artifact prefix is set per environment) |
| REST API version | A current Azure AI Search version, e.g. `2024-11-01-preview` |
| Semantic configuration | `mm-semantic-config` (this is the default on the index) |
| Vector field | `text_vector` (embedded by the index's built-in vectorizer) |

The endpoint and prefix are environment-specific and come from that environment's
configuration.

---

## 3. Authentication

Authentication is **Microsoft Entra (AAD) / managed identity** — no API keys are shared. The
consuming service authenticates with its own managed identity and sends an AAD bearer token on
each request.

Role required on the Search service for a read-only consumer:

- **Search Index Data Reader** — query the index.

Query-time vectorization happens inside the index, so no embedding-model permission is needed
just to search. To obtain the token, request the scope `https://search.azure.us/.default` for
the search service and set `Authorization: Bearer <token>` on each call.

---

## 4. The five record types

Every record has a `record_type`. **This is the most important field for retrieval routing.**

| `record_type` | `chunk_id` prefix | One record per… | Use it for |
|---|---|---|---|
| `text` | `txt_` | Page chunk of body text (~1200 chars, 200 overlap) | General questions and context |
| `diagram` | `dgm_` | One figure (AI description + OCR) | "Show/describe the diagram for…" |
| `table` | `tbl_` | One whole table as markdown | Questions about a table as a whole |
| `table_row` | `trow_` | One row of a lookup table (5–80 row tables) | Precise value lookups |
| `summary` | `sum_` | One per document | Document-level questions, routing, overview |

All record types share the same `parent_id` (per-PDF), the same document metadata (revision,
number, page count), and each has its own embedding. A common mistake is to filter to `text`
only — that hides diagrams, tables, and row-level answers even when the user asks for them.
Search across all types and route with `record_type`.

---

## 5. Field catalog

`chunk` is the field to feed the language model; the rest are for routing, filtering, citation,
safety, and UI. Capabilities: **S**=searchable, **F**=filterable, **R**=retrievable,
**Fa**=facetable.

### 5.1 Identity and routing
| Field | Type | Caps | Notes |
|---|---|---|---|
| `id` | String (key) | S R | Internal key. |
| `chunk_id` | String | S F R | Stable, prefix-encoded id; good for citation linking and de-duplication. |
| `parent_id` | String | F R | Per-PDF id; groups all records from one manual. |
| `record_type` | String | F R Fa | `text` \| `diagram` \| `table` \| `table_row` \| `summary`. |
| `record_subtype` | String | F R Fa | E.g. `glossary` on definition pages. |
| `content_class` | String | F R Fa | Coarse content classification. |

### 5.2 Content
| Field | Type | Caps | Notes |
|---|---|---|---|
| `chunk` | String | S R | **Feed this to the LLM.** Page text / diagram description / table markdown / row key-values / summary, per record type. |
| `chunk_for_semantic` | String | S | Used only for embedding and re-ranking. **Never display.** |
| `highlight_text` | String | S R | Plain-text form for the PDF viewer's text search. |
| `surrounding_context` | String | S | Text around a figure/table (semantic use only). |

### 5.3 Citation and navigation
| Field | Type | Caps | Notes |
|---|---|---|---|
| `source_file` | String | S F R Fa | The manual's filename. |
| `source_url` | String | R | Full blob URL to the PDF (open-document link). |
| `physical_pdf_page` | Int32 | F R | First physical page (1-based) — what the viewer opens. |
| `physical_pdf_page_end` | Int32 | F R | Last page for multi-page records. |
| `physical_pdf_pages` | Coll(Int32) | F R | All pages the record spans. |
| `printed_page_label` | String | S F R | Page number as printed in the manual. |
| `header_1` / `header_2` / `header_3` | String | S F R | Section header chain for the citation. |

### 5.4 Document revision / currency
| Field | Type | Caps | Notes |
|---|---|---|---|
| `document_number` | String | S F R | The manual's document number. |
| `document_revision` | String | S F R | Revision label. |
| `effective_date` | String | F R | Effective date. |
| `is_current_revision` | Boolean | F R | **True for the current revision.** Filter on this to exclude superseded manuals. |
| `pdf_total_pages` | Int32 | F R | Total pages in the source PDF. |

### 5.5 Diagram fields
| Field | Type | Caps | Notes |
|---|---|---|---|
| `diagram_description` | String | S R | AI description of the figure. Include in the LLM context. |
| `diagram_ocr_text` | String | S R | Text read off the figure. |
| `diagram_category` | String | S F R Fa | E.g. wiring diagram, schematic, nameplate. |
| `figure_ref` / `figure_number` / `figure_title` | String | S F R | Figure reference and title. |
| `figure_bbox` | String (JSON) | R | Bounding box (page + coordinates) to highlight the figure. |
| `has_diagram` | Boolean | F R Fa | True for figures that carry useful content. |

### 5.6 Table and table-row fields
| Field | Type | Caps | Notes |
|---|---|---|---|
| `table_caption` / `table_title` / `table_number` | String | S F R | Table identity. |
| `table_row_count` / `table_col_count` | Int32 | F R | Table dimensions. |
| `table_row_key` | String | S F R | The row's key (left-most/identifying cell). |
| `table_columns` | Coll(String) | S F R | Column headers. |
| `table_row_cells` | Coll(String) | S R | The row's cell values. |
| `table_row_semantic_key` / `table_row_semantic_value` | String | S R | Normalised key/value for lookups. |
| `table_row_quality` | String | F R Fa | Row quality signal; filter out low-quality rows if desired. |

### 5.7 Safety and callouts
| Field | Type | Caps | Notes |
|---|---|---|---|
| `safety_callout` | Boolean | F R Fa | True if the record contains a WARNING/CAUTION/DANGER block. |
| `callouts` | Coll(String) | S F R Fa | The callout texts. |
| `hazard_class` | Coll(String) | S F R Fa | Hazard classification. |
| `criticality` | String | F R Fa | Criticality level. |
| `is_prohibition` / `prohibitions` | Bool / Coll(String) | F R | "Do not…" statements. |

The index also has a `safety-boost` scoring profile that raises records containing callouts.

### 5.8 Cross-references and procedures
| Field | Type | Caps | Notes |
|---|---|---|---|
| `figures_referenced` / `tables_referenced` / `sections_referenced` | Coll(String) | S F R | References mentioned in a text chunk (e.g. "see Figure 18-117"). Use these to fetch the referenced diagram/table record. |
| `procedure_id` / `procedure_title` / `procedure_step_*` | String / Int32 | S F R | Procedure and step structure for step-by-step content. |

### 5.9 Health and provenance
| Field | Type | Caps | Notes |
|---|---|---|---|
| `processing_status` | String | F R Fa | `ok` for healthy records; filter on this to exclude failures. |
| `retrieval_eligible` | Boolean | F R Fa | True for records intended to be returned to users. |
| `last_indexed_at` | DateTimeOffset | F R | When the record was last indexed (a `freshness-boost` profile exists). |
| `language` | String | F R Fa | Record language. |

---

## 6. Query patterns

### 6.1 Hybrid semantic search (recommended default)

Send the user's question as both keyword and vector, with semantic ranking on, and select only
the fields you need:

```json
POST /indexes/<prefix>-index/docs/search?api-version=2024-11-01-preview
Authorization: Bearer <aad-token>
Content-Type: application/json

{
  "search": "<user question>",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "vectorQueries": [
    { "kind": "text", "text": "<user question>", "fields": "text_vector" }
  ],
  "captions": "extractive",
  "answers": "extractive|count-3",
  "filter": "retrieval_eligible eq true and is_current_revision eq true",
  "top": 10,
  "select": "chunk_id, record_type, source_file, source_url, physical_pdf_page, printed_page_label, header_1, header_2, header_3, chunk, figure_ref, figure_bbox, diagram_description, table_caption, safety_callout, callouts"
}
```

> Filtering on `is_current_revision eq true` returns only current manuals. Filtering on
> `retrieval_eligible eq true` (and optionally `processing_status eq 'ok'`) excludes internal
> or failed records.

### 6.2 Restrict to a content type

```json
{ "search": "fault indicator", "filter": "record_type eq 'diagram'", "top": 20 }
```

### 6.3 Precise value lookup (table rows)

```json
{ "search": "200A 277V rating", "filter": "record_type eq 'table_row'", "top": 10 }
```

### 6.4 Within one manual / page range

```json
{ "search": "grounding",
  "filter": "source_file eq '<file>.pdf' and physical_pdf_page ge 100 and physical_pdf_page le 150" }
```

### 6.5 Records that touch a specific page

```json
{ "search": "*", "filter": "physical_pdf_pages/any(p: p eq 137)" }
```

### 6.6 Only safety content

```json
{ "search": "arc flash", "filter": "safety_callout eq true", "top": 20 }
```

---

## 7. Recommended retrieval flow for the chatbot back-end

1. **Send the question** as a hybrid semantic query (Section 6.1), filtered to
   `is_current_revision eq true` and `retrieval_eligible eq true`, retrieving the top N across
   all record types.
2. **Assemble context** from the `chunk` field of the top results. For diagram hits include
   `diagram_description` (and `diagram_ocr_text`); for table/table_row hits include the table
   markdown or the row's key/value cells.
3. **Follow references** — when a text chunk lists `figures_referenced` or `tables_referenced`,
   optionally fetch the matching `diagram`/`table` record so the answer can show it.
4. **Surface safety** — if any retrieved record has `safety_callout eq true`, include its
   `callouts` and show a safety badge in the UI.
5. **Generate the answer** with the language model, grounded only in the retrieved chunks.
6. **Cite** every claim with `source_file`, `printed_page_label` / `physical_pdf_page`, and the
   `header_1/2/3` chain.
7. **Enable navigation** — the front-end uses `source_url` + `physical_pdf_page` to open the
   PDF at the page and `figure_bbox` to highlight the figure.

---

## 8. Front-end usage

- Open the source PDF with `source_url` and jump to `physical_pdf_page`.
- Highlight a figure using `figure_bbox` (page + coordinates).
- Show the citation as `source_file`, page (`printed_page_label`), and section
  (`header_1/2/3`).
- Distinguish result kinds in the UI using `record_type` (diagram thumbnail vs. text snippet
  vs. table).
- Show a safety badge when `safety_callout` is true and render the `callouts` prominently.

---

## 9. Configuration the consumer needs

All non-secret, from the environment configuration and Entra:

| Value | Purpose |
|---|---|
| Search service endpoint | Where to send queries. |
| Index name (`<prefix>-index`) | Which index to query. |
| Semantic configuration name (`mm-semantic-config`) | Enables the re-ranker. |
| Managed identity with **Search Index Data Reader** | Authenticates the queries. |

No embedding-model endpoint or key is required — the index vectorizes queries internally.

---

## 10. Do and don't

**Do**
- Use hybrid + semantic queries by default.
- Search across all record types and route with `record_type`.
- Filter to `is_current_revision eq true` and `retrieval_eligible eq true`.
- Include `diagram_description` and table content in the LLM context, not just `text`.
- Surface `safety_callout` / `callouts` in the UI.
- Cite with file + page + headers on every claim.

**Don't**
- Don't filter to `record_type eq 'text'` only — you lose diagrams, tables, and row lookups.
- Don't display `chunk_for_semantic` (it is for ranking only).
- Don't call an external embedding model for queries — the index does it.
- Don't store or share Search API keys — use managed identity.
