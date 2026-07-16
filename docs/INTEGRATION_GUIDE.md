# Integration Guide — Connecting to the Search Index

## 1. Purpose

This guide is for the teams that **consume** the search index built by the indexing
component: the chatbot back-end (retrieval API) and the front-end (chat UI, PDF viewer). It
explains how to connect to the index, how to authenticate, what the index contains, and the
query patterns that get the most value out of it.

It does **not** cover how the index is built — see the **Indexing Runbook** for that.

---

## 2. What the indexing component provides

A single **Azure AI Search index** containing every technical manual, broken into small
records with rich citation metadata. The index supports:

- **Keyword (BM25)** search — exact part numbers, codes, literal phrases.
- **Vector search** — 1536-dimension embeddings for concepts, paraphrases, synonyms.
- **Hybrid** — keyword + vector together (recommended default).
- **Semantic re-ranker** — re-orders the top results by relevance.
- **Integrated vectorizer** — the index embeds the user's query itself, so the consumer does
  **not** need to call an embedding model.

The consumer's job is to send a query, read the returned records, and use their fields to
build a cited answer and (optionally) open the source PDF at the right page.

---

## 3. Connection details

| Item | Value / source |
|---|---|
| Service endpoint | `https://<search-service>.search.azure.us` (Azure Government) |
| Index name | `<artifactPrefix>-index` (the prefix is per environment) |
| API version | A current Azure AI Search REST API version (e.g. `2024-11-01-preview`) |
| Semantic configuration | `mm-semantic-config` |
| Vector field | `text_vector` (embedded by the index's built-in vectorizer) |

The search endpoint and prefix are environment-specific and come from that environment's
configuration — they are not hardcoded.

---

## 4. Authentication

Authentication is **Microsoft Entra (AAD) / managed identity** — the same model the rest of
the platform uses. The consuming service (back-end API or its host) authenticates with its own
managed identity; no API keys are shared.

**Role required on the Search service** for a read-only consumer:

- **Search Index Data Reader** — query the index.

The back-end obtains an AAD token for the search service and sends it as a bearer token on
each request. Query-time vectorization is handled inside the index, so no additional AI model
permissions are needed just to search.

---

## 5. The five record types

Every record has a `record_type`. **This is the most important field for retrieval routing** —
filter or boost by it depending on the question.

| `record_type` | One record per… | Use it for |
|---|---|---|
| `text` | Page chunk of body text | General questions and context |
| `diagram` | One figure (AI description + OCR) | "Show/describe the diagram for…" |
| `table` | One whole table (markdown) | Questions about a table as a whole |
| `table_row` | One row of a lookup table | Precise value lookups |
| `summary` | One per document | Document-level questions, routing, overview |

A common mistake is to filter to `text` only — that hides diagrams, tables, and row-level
answers even when the user explicitly asks for them. Prefer to search across all types and let
relevance (and `record_type` boosting) decide.

---

## 6. Key fields for consumers

`chunk` is the field to feed the language model; the rest are for filtering, citation, and UI.

**Content**
- `chunk` — the text to send to the LLM (page text, diagram description, table markdown, row
  key/values, or summary depending on `record_type`).
- `chunk_for_semantic` — internal, used for ranking only. **Do not display.**

**Citation / navigation**
- `source_file` — the manual's filename.
- `source_url` — full blob URL to the PDF (for a "open document" link).
- `physical_pdf_page` / `physical_pdf_page_end` / `physical_pdf_pages` — page number(s) to open
  the PDF at.
- `printed_page_label` — the page number as printed in the manual.
- `header_1` / `header_2` / `header_3` — the section header chain for the citation.

**Diagram records**
- `diagram_description` — the AI description + OCR text. Include it in the LLM context.
- `figure_ref` — human reference (e.g. "Figure 18-117").
- `figure_bbox` — bounding box (page + coordinates) for highlighting the figure in the viewer.
- `diagram_category` — e.g. wiring diagram, schematic, nameplate.

**Table / table_row records**
- `table_caption` — the table's caption.
- `table_row_*` fields — structured key/value and quality signals for row-level lookups.

**Health / filtering**
- `record_type` — routing.
- `processing_status` — filter to healthy records (`ok`) if desired.

The complete field catalog (70+ fields with types and capabilities) is in `search/index.json`
and the detailed chatbot spec `CHATBOT_INTEGRATION.md`.

---

## 7. Query patterns

### 7.1 Hybrid semantic search (recommended default)

Send the user's question as both a keyword search and a vector query, with semantic ranking on:

```json
POST /indexes/<prefix>-index/docs/search?api-version=<api-version>
Authorization: Bearer <aad-token>

{
  "search": "<user question>",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "vectorQueries": [
    { "kind": "text", "text": "<user question>", "fields": "text_vector" }
  ],
  "captions": "extractive",
  "answers": "extractive|count-3",
  "top": 10,
  "select": "chunk_id, record_type, source_file, source_url, physical_pdf_page, printed_page_label, header_1, header_2, header_3, chunk, figure_ref, figure_bbox, diagram_description"
}
```

### 7.2 Restrict to a content type

```json
{ "search": "fault indicator",
  "filter": "record_type eq 'diagram'",
  "top": 20 }
```

### 7.3 Precise value lookup (table rows)

```json
{ "search": "200A 277V rating",
  "filter": "record_type eq 'table_row'",
  "top": 10 }
```

### 7.4 Within one manual / page range

```json
{ "search": "grounding",
  "filter": "source_file eq '<file>.pdf' and physical_pdf_page ge 100 and physical_pdf_page le 150" }
```

### 7.5 Records that touch a specific page

```json
{ "search": "*",
  "filter": "physical_pdf_pages/any(p: p eq 137)" }
```

---

## 8. Recommended retrieval flow for the chatbot back-end

1. **Send the question** as a hybrid semantic query (§7.1), retrieving the top N across all
   record types.
2. **Assemble context** from the `chunk` field of the top results. For diagram hits, include
   `diagram_description`; for table/table_row hits, include the markdown/row content.
3. **Follow references** — when a text chunk mentions a figure or table, optionally fetch the
   matching `diagram`/`table` record so the answer can show it.
4. **Generate the answer** with the language model, grounded in the retrieved chunks.
5. **Cite** every claim using `source_file`, `printed_page_label` / `physical_pdf_page`, and
   the `header_*` chain.
6. **Enable navigation** — the front-end uses `source_url` + `physical_pdf_page` to open the
   PDF at the page, and `figure_bbox` to highlight the figure.

---

## 9. Front-end usage notes

- Open the source PDF with `source_url` and jump to `physical_pdf_page`.
- Highlight a figure using `figure_bbox` (page + coordinates).
- Show the citation as `source_file`, page (`printed_page_label`), and section (`header_1/2/3`).
- Distinguish result kinds in the UI using `record_type` (e.g. a diagram thumbnail vs. a text
  snippet vs. a table).

---

## 10. Configuration values needed to connect

The consuming service needs only these per environment (all non-secret; obtained from the
environment's configuration and Entra):

| Value | Purpose |
|---|---|
| Search service endpoint | Where to send queries |
| Index name (`<prefix>-index`) | Which index to query |
| Semantic configuration name (`mm-semantic-config`) | Enables the re-ranker |
| Managed identity with **Search Index Data Reader** | Authenticates the queries |

No embedding-model endpoint or key is required — the index vectorizes queries internally.

---

## 11. Do's and don'ts

**Do**
- Use hybrid + semantic queries by default.
- Search across all record types; route with `record_type`.
- Include `diagram_description` and table content in the LLM context, not just `text`.
- Always cite with file + page + headers.

**Don't**
- Don't filter to `record_type eq 'text'` only — you lose diagrams, tables, and row lookups.
- Don't display `chunk_for_semantic` (it is for ranking only).
- Don't call an external embedding model for queries — the index does it.
- Don't store or share Search API keys — use managed identity.
