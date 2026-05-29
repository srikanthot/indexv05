# Chatbot Integration Spec — Azure AI Search Index

**Audience:** the engineer or LLM agent building/modifying the chatbot
back-end and front-end that sits on top of this Azure AI Search index.

**Purpose:** make sure the chatbot uses **every** signal the index
provides — diagrams, tables, table rows, callouts, glossary, safety
warnings, cover metadata — not just plain text.

> If your chatbot only returns body text and ignores images, diagrams,
> table cells, and warnings, you are using ~30% of what this index can
> do. This document explains how to use the other 70%.

---

## 0. TL;DR — the five things most chatbots get wrong

1. **They filter to `record_type=text` only** (or don't filter at all and
   then drop non-text hits). Result: diagrams, tables, and table rows
   never surface even when the user explicitly asks for them.
2. **They don't render `diagram_description`** in the answer, so the LLM
   has no visual context to cite.
3. **They never query `record_type='table_row'`** for lookup questions
   ("what's the value for 200A at 277V?"), so the LLM has to reason
   over a full markdown table and often picks the wrong cell.
4. **They ignore `figures_referenced` / `tables_referenced`** on text
   hits. When a paragraph says "see Figure 18-117", the chatbot doesn't
   then fetch the diagram record for Figure 18-117. The user gets a
   text answer that *talks about* a figure but never *shows* it.
5. **They don't surface `safety_callout=true` / `callouts`** — so
   WARNING / CAUTION / DANGER blocks get treated as ordinary prose and
   no safety badge is shown in the UI.

The rest of this document explains the index in detail and gives you
the exact query patterns to fix all five.

---

## 1. What the index is

A **single multimodal Azure AI Search index** populated from PDFs in
blob storage by one Azure Search indexer + skillset. The skillset fans
each PDF into **five child record types** that all land in the **same
flat index**. They are distinguished only by `record_type`.

Source-of-truth files in this repo:

- Index schema: [search/index.json](search/index.json)
- Skillset (record-production pipeline): [search/skillset.json](search/skillset.json)
- Field projection (skillset → index): see `indexProjections.selectors[]` in skillset.json
- Custom skill code: [function_app/shared/](function_app/shared/)

The index supports:
- **BM25** (lexical)
- **HNSW vector search** with cosine similarity, 1536-dim Ada-002 embeddings
- **Hybrid** (BM25 + vector together)
- **Semantic ranker** with a configured reranker
- **Integrated vectorizer** — the index embeds the user query for you

## 2. The five record types — and what each is for

Every document carries `record_type`. **This is the single most
important field for retrieval routing.**

| `record_type` | `chunk_id` prefix | One row per… | Built by |
|---|---|---|---|
| `text` | `txt_` | Page chunk of body markdown (~1200 chars w/ 200-char overlap) | [page_label.py](function_app/shared/page_label.py) |
| `diagram` | `dgm_` | Figure crop + GPT-4 Vision description | [diagram.py](function_app/shared/diagram.py) |
| `table` | `tbl_` | Whole table as a markdown pipe-table | [process_table.py](function_app/shared/process_table.py) |
| `table_row` | `trow_` | One row of a "shaped" table (5–80 row tables only) | [process_table.py](function_app/shared/process_table.py) |
| `summary` | `sum_` | One per PDF (whole-document summary) | [summary.py](function_app/shared/summary.py) |

All five record types:
- Share the same `parent_id` (per-PDF stable hash).
- Carry the same per-doc cover metadata (`document_revision`, `effective_date`, `document_number`, `pdf_total_pages`).
- Carry the same routing taxonomy (`operationalarea`, `functionalarea`, `doctype`, `filetype`).
- Each have their **own** `text_vector` embedding of `chunk_for_semantic`.

There is one sub-flag: text rows can have `record_subtype="glossary"`
on detected definition pages.

## 3. The 70+ fields — full catalog

Field types follow Azure Search EDM. Capabilities: `S=searchable,
F=filterable, R=retrievable, So=sortable, Fa=facetable`. Source of
truth: [search/index.json](search/index.json).

### 3.1 Identity & lineage
| Field | Type | Caps | Notes |
|---|---|---|---|
| `id` | String **(key)** | S R | Azure Search-internal key. |
| `chunk_id` | String | S F R So | Stable, prefix-encoded (see §2). Use for citation linking + cache keys. |
| `parent_id` | String | F R | Per-PDF hash. Group records of the same PDF. |
| `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `tbl_row_parent_id` / `sum_parent_id` | String | F R | Internal projection keys — chatbot should use `parent_id`. |
| `record_type` | String | F R Fa | `text` \| `diagram` \| `table` \| `table_row` \| `summary` |
| `record_subtype` | String | F R Fa | Only `glossary` on text currently. |

### 3.2 Content
| Field | Type | Caps | Per record_type |
|---|---|---|---|
| `chunk` | String | S R | **What to feed the LLM.** text=page markdown, diagram=vision description, table=markdown table, table_row=`"Header: value; ..."`, summary=summary text. |
| `chunk_for_semantic` | String | S only (NOT retrievable) | Used only for embedding + semantic reranker. **Never display.** |
| `highlight_text` | String | S R | Plain-text form for PDF viewer text-search. |
| `surrounding_context` | String | S only (NOT retrievable) | Diagram rows: ±400 chars around caption. Semantic-only. |

### 3.3 Vectors + semantic config
- `text_vector` — `Collection(Edm.Single)`, 1536 dims, **not stored, not retrievable**.
- HNSW: `m=8, efConstruction=400, efSearch=500`, cosine.
- Built-in vectorizer `aoai-vectorizer` (text-embedding-ada-002) — the index embeds queries at search time. The front-end does **not** need to call AOAI itself.
- Semantic config name: **`mm-semantic-config`** (default).
  - Title field: `source_file`
  - Content fields: `chunk_for_semantic`, `diagram_description`, `surrounding_context`
  - Keyword fields: `header_1/2/3`, `figure_ref`, `table_caption`, `diagram_category`, `callouts`, `record_subtype`

### 3.4 Page + location (for citations & PDF "open at page")
| Field | Type | Notes |
|---|---|---|
| `physical_pdf_page` | Int32 | 1-based physical page — what the PDF viewer needs. |
| `physical_pdf_page_end` | Int32 | For multi-page records. |
| `physical_pdf_pages` | Coll(Int32) | All pages this record spans. |
| `printed_page_label` | String | Printed page label as shown in the PDF (e.g. `"A-12"`, `"iv"`). |
| `printed_page_label_end` | String | For ranges. |
| `printed_page_label_is_synthetic` | Bool | `true` = label was synthesized from `physical_pdf_page` (tables/diagrams don't go through label extraction). Show physical page instead in this case. |
| `pdf_total_pages` | Int32 | Total pages in source PDF. |
| `page_resolution_method` | String, Fa | `di_input` \| `extract_page_label` \| `document_summary` \| `missing`. Hide "jump to page" button when `document_summary`. |
| `layout_ordinal` | Int32, So | Section position. Use to order within one PDF. |
| `text_bbox`, `figure_bbox`, `table_bbox` | String | JSON-encoded bbox for PDF-viewer overlay. |

### 3.5 Section hierarchy
| Field | Type | Notes |
|---|---|---|
| `header_1`, `header_2`, `header_3` | String, Fa | Chapter ▸ Section ▸ Subsection. Display as breadcrumb above each citation. |

### 3.6 Cross-reference graph — **critical for diagram/table follow-ups**
Use these when a question names a figure or table — they let you find
the diagram chunk **and** the text chunk talking about it.

| Field | Type | Notes |
|---|---|---|
| `figure_id` | String | DI's figure id; on diagram rows. |
| `figure_ref` | String | Canonical reference like `"Figure 18.117"` (the figure this row IS, on diagram rows; the figure this row HOSTS, on text rows). |
| `table_ref` | String | Same idea for tables. |
| `figures_referenced` | Coll(String) | Every figure mentioned in this chunk's text. |
| `figures_referenced_normalized` | Coll(String) | Lowercased / dedup'd, for exact filter matches. |
| `tables_referenced` | Coll(String) | Tables mentioned in this chunk. |
| `sections_referenced` | Coll(String) | Section numbers mentioned. |
| `pages_referenced` | Coll(String) | Page numbers referenced in the text. |

### 3.7 Diagram-specific
| Field | Type | Notes |
|---|---|---|
| `has_diagram` | Bool, Fa | `false` = vision decided the crop is empty/text. **Always filter `has_diagram eq true`** when retrieving diagrams. |
| `diagram_description` | String, searchable | The GPT-4-Vision description. **The only field that carries visual content as text — feed this to the LLM.** |
| `diagram_category` | String (keyword analyzer), Fa | `schematic_wiring` \| `nameplate` \| `block_flow` \| `parts_exploded` \| `default`. Use exact-match filters. |
| `image_hash`, `image_phash` | String, F | Exact/perceptual hashes for cross-PDF dedup of identical figures. |

### 3.8 Table-specific
| Field | Type | Notes |
|---|---|---|
| `table_row_count`, `table_col_count` | Int32 | Sizes. |
| `table_caption` | String | E.g. `"Table 18-3 Conductor Sizes"`. |
| `table_row_index` | Int32, So | 0-based; only on `record_type='table_row'`. |
| `table_parent_chunk_id` | String, F | On `table_row` rows → points to the parent `table` `chunk_id`. Use to render parent context when a row is cited. |
| `chunk_content_hash` | String | Re-embed gate; ignore for retrieval. |

### 3.9 Safety / callouts / footnotes — **don't ignore**
| Field | Type | Notes |
|---|---|---|
| `callouts` | Coll(String), S F Fa | `"WARNING"`, `"CAUTION"`, `"NOTE"`, `"DANGER"` etc. Surface as badges. |
| `safety_callout` | Bool, Fa | `true` = chunk has a safety callout. **Bias retrieval toward these on safety-themed questions.** |
| `footnotes` | Coll(String) | Footnotes harvested from the page. |
| `ocr_min_confidence` | Double, So | Lower = noisier OCR. Optionally penalize. |

### 3.10 Document-level metadata (same on every record of a PDF)
| Field | Type | Notes |
|---|---|---|
| `source_file` | String, So, Fa | Display name. Used as semantic-ranker title. |
| `source_url` | String, R | Blob URL — what the UI opens. May need SAS-token minting at the backend. |
| `source_path` | String, F | Internal. |
| `document_revision` | String, Fa | e.g. `"Rev C"`. |
| `effective_date` | String, So, Fa | ISO date. Sort newest-first to prefer current revisions. |
| `document_number` | String, Fa | Document number. |
| `equipment_ids` | Coll(String), S Fa | Equipment tags / model numbers extracted from text. Great as a facet. |
| `language` | String, Fa | ISO language code. |

### 3.11 Routing taxonomy — **canonical sidebar facets**
Projected unchanged from blob metadata onto every record:
- `operationalarea` (S F R Fa)
- `functionalarea` (S F R Fa)
- `doctype` (S F R Fa)
- `filetype` (F R Fa)

### 3.12 Ops / freshness / quality
- `processing_status` — `"ok"` \| `"needs_preanalyze_output"` \| `"all_figures_dropped"` \| `"partial_figure_loss"` \| `"no_content"` \| `"summary_error:*"`. **Always filter `processing_status eq 'ok'`** in chatbot queries.
- `skill_version`, `embedding_version` — bump-and-reindex signals.
- `chunk_token_count` — for prompt-budget bookkeeping.
- `chunk_quality_score` — heuristic.
- `last_indexed_at` — sortable timestamp.

## 4. The retrieval algorithm the chatbot should use

### 4.1 Default query (hybrid + semantic + integrated vectorizer)

```jsonc
POST https://<search>.search.azure.us/indexes/<index>/docs/search?api-version=2024-05-01-preview

{
  "search": "<user question>",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "queryLanguage": "en-us",
  "captions": "extractive",
  "answers": "extractive|count-3",
  "top": 20,

  "vectorQueries": [
    {
      "kind": "text",
      "text": "<user question>",
      "fields": "text_vector",
      "k": 50
    }
  ],

  "select": "chunk_id,record_type,record_subtype,parent_id,source_file,source_url,header_1,header_2,header_3,physical_pdf_page,printed_page_label,printed_page_label_is_synthetic,page_resolution_method,figure_ref,table_ref,figures_referenced_normalized,tables_referenced,table_caption,table_parent_chunk_id,table_row_index,diagram_category,has_diagram,safety_callout,callouts,footnotes,document_revision,effective_date,document_number,operationalarea,functionalarea,doctype,chunk,highlight_text,figure_bbox,table_bbox,text_bbox,layout_ordinal,chunk_token_count",

  "filter": "processing_status eq 'ok'"
}
```

Key points:
- `vectorQueries.kind="text"` ⇒ the index embeds the query for you via
  the integrated vectorizer. **No need to call AOAI from the back-end.**
- Hybrid (BM25 + vector) with the semantic reranker on top is the
  default chatbot ranking. Do not disable semantic on a whim.
- **NEVER `select` `chunk_for_semantic`, `surrounding_context`, or
  `text_vector`** — they are non-retrievable or noisy.

### 4.2 Intent routing — pick filters per question type

The chatbot should classify intent (cheap regex is fine) and add a
filter clause. This is the part most chatbots get wrong.

| User intent | Add to `filter` | Why |
|---|---|---|
| Plain procedural / "how do I…" | `record_type eq 'text'` | Prose chunks. |
| "What does Figure 18-117 show?" / "diagram of…" / "wiring of…" / "show me the schematic" | `record_type eq 'diagram' and has_diagram eq true` | Vision-described figures. |
| Diagram by category | add `diagram_category eq 'schematic_wiring'` etc. | Exact keyword match. |
| "Show me Table 18-3" / "table of conductor sizes" | `record_type eq 'table'` | Whole tables. |
| Lookup question ("value for 200A at 277V?") | `record_type eq 'table_row'`, then dereference `table_parent_chunk_id` for parent table context | Per-row records hit the exact cell. |
| "What is this manual about?" / cold-start | `record_type eq 'summary'` | One per PDF. |
| Safety / warning questions | `safety_callout eq true` | Bias toward safety paragraphs. |
| Glossary / "what does X mean?" | `record_subtype eq 'glossary'` | Definition pages. |
| "Latest revision of doc X" | `source_file eq '<file>'`, `orderby: effective_date desc` | Newest cover rev. |

Combine record types with `search.in`:
```text
filter: search.in(record_type, 'text,diagram,table', ',') and processing_status eq 'ok'
```

### 4.3 The two-pass strategy (do this for ambiguous intent)

1. **Pass 1:** wide hybrid+semantic query with `processing_status eq 'ok'` only. `top=30`.
2. Inspect the top 5 hits' `record_type` mix.
3. **Pass 2:** if a diagram/table dominates, re-run constrained to that type with `top=10` for richer focused context.

### 4.4 Cross-reference expansion (FIXES "chatbot ignores diagrams")

When Pass 1's top text hit references a figure or table, **fetch the
referenced record explicitly**:

```text
# Text hit said: figures_referenced_normalized = ["figure 18.117"]
# Fetch the diagram:
filter: parent_id eq '<same parent_id>'
        and record_type eq 'diagram'
        and has_diagram eq true
        and search.in(figures_referenced_normalized, 'figure 18.117', ',')
```

Same pattern for tables:
```text
filter: parent_id eq '<same parent_id>'
        and record_type eq 'table'
        and table_ref eq 'Table 18-3'
```

When a `table_row` is the best hit, fetch the parent table:
```text
filter: chunk_id eq '<row.table_parent_chunk_id>'
```

This single step is the difference between "chatbot describes a figure
in words" and "chatbot also surfaces the actual diagram record with the
GPT-4-Vision description for the LLM to cite."

### 4.5 Faceted sidebar (recommended for the front-end)

```jsonc
"facets": [
  "record_type,count:10",
  "operationalarea,count:20",
  "functionalarea,count:20",
  "doctype,count:20",
  "diagram_category,count:10",
  "safety_callout",
  "record_subtype",
  "equipment_ids,count:30"
]
```

## 5. Why the chatbot is currently missing content — concrete fixes

Map each common failure to the exact change required.

### 5.1 "Chatbot never returns diagrams / images"

**Why:** the query is either filtered to `record_type='text'` or
unfiltered with a too-small `top` so diagrams rank below text.

**Fix:**
1. Remove any hardcoded `record_type='text'` filter. Use `search.in(record_type, 'text,diagram,table,table_row', ',')` at minimum.
2. Raise `top` to 20 for the wide pass.
3. When intent classifier detects an image/diagram word (`diagram|figure|schematic|wiring|drawing|illustration|sketch|plate|nameplate`), force `record_type eq 'diagram' and has_diagram eq true`.
4. When *any* top-text hit has `figures_referenced` non-empty, run §4.4's diagram-expansion query and include those records in the answer context.

### 5.2 "Chatbot can't describe what's in an image"

**Why:** the chatbot is reading `chunk` but for diagram records `chunk
= diagram_description` and the LLM never sees it.

**Fix:**
1. In the `select` clause, always include `diagram_description` (it is `chunk` for diagram rows, but selecting both is harmless and gives explicit visibility).
2. In the system prompt for the answering LLM, instruct: "When a retrieved chunk has `record_type=diagram`, treat its content as a description of the figure with `figure_ref=<...>` on page `<...>`. Cite the figure by `figure_ref` and `printed_page_label`. If the user asks 'show me' that figure, include the chunk_id so the front-end can render the cropped image / link to the PDF page."

### 5.3 "Chatbot misses specific table values"

**Why:** the chatbot ignores `record_type='table_row'` and tries to
read the full table markdown as a single chunk. The vector hit for the
exact row doesn't surface.

**Fix:**
1. Detect lookup intent (regex on numbers/units in the query, or a phrase like "value for", "rating of", "spec for").
2. For lookup queries, add `record_type='table_row'` to the filter (or `search.in(record_type, 'table_row,table', ',')` if you want both).
3. When a `table_row` hit is returned, also fetch its parent table via `chunk_id eq '<table_parent_chunk_id>'` and pass both to the LLM so it can see surrounding column headers and adjacent rows for context.

### 5.4 "Chatbot doesn't surface WARNINGs"

**Why:** `safety_callout` and `callouts` are never read.

**Fix:**
1. Always `select` `safety_callout` and `callouts`.
2. For any query that contains `warning|caution|danger|hazard|risk|safe|safety|lockout|tagout|loto|de-?energize|grounding`, add `safety_callout eq true` to the filter (or boost weight using a scoring profile if you don't want to hard-filter).
3. In the UI, when `safety_callout=true` show a yellow ⚠ badge; when `callouts` contains `WARNING`/`DANGER` render a red badge.
4. Tell the answering LLM in its system prompt to **lead** with safety information when a retrieved chunk has `safety_callout=true`.

### 5.5 "Chatbot can't answer 'what does this term mean?'"

**Why:** glossary pages exist as text chunks with `record_subtype='glossary'`, but the chatbot never targets them.

**Fix:** when the user asks a definition question (`what (does|is) ... mean`, `define ...`, `definition of ...`, `glossary`, `acronym`), add `record_subtype eq 'glossary'` to the filter.

### 5.6 "Chatbot returns outdated revisions"

**Why:** when the same manual has multiple revisions in the index, the
chatbot doesn't prefer the newest.

**Fix:** sort by `effective_date desc` as a tiebreaker; or include
`document_revision` + `effective_date` in the LLM context and instruct
it to prefer the newest revision. If you have a "current revision"
catalog elsewhere, add a filter like `document_revision eq 'Rev C'`.

## 6. Front-end rendering — per-record-type recipe

For each hit, render with:

- **Citation chip:** `{source_file} — p. {printed_page_label || physical_pdf_page}` linking to `{source_url}#page={physical_pdf_page}` (mint a SAS token if the blob isn't anonymously readable).
- **Breadcrumb:** `{header_1} ▸ {header_2} ▸ {header_3}`.
- **Body to LLM:** `chunk`. (Diagrams = the description, tables = markdown table, etc.)
- **Body to user (preview snippet):** Azure-returned semantic caption (`@search.captions[0].text`); fall back to first ~300 chars of `chunk`.
- **PDF viewer highlight:** feed `highlight_text` to the viewer's text search; for visual bbox use the matching `*_bbox` JSON field.
- **Badges:** `safety_callout=true` → ⚠; `callouts` → render each; `record_subtype="glossary"` → glossary tag; `printed_page_label_is_synthetic=true` → hide printed-label (or show physical).
- **Revision footer:** `{document_number} • {document_revision} • Effective {effective_date}`.
- **Per-record-type extras:**
  - **diagram** → show the figure image. The backend can serve it by downloading the cropped PNG from `_dicache/<source_file>.crop.<figure_id>.png` in blob storage, OR by rendering the bbox region from the source PDF at the indicated page. Display the `diagram_description` as alt text.
  - **table** → render the markdown pipe-table inline; let the user expand to full size.
  - **table_row** → render the row text **plus** a "see full table" link that fetches `table_parent_chunk_id`.
  - **summary** → no page button (because `page_resolution_method = 'document_summary'`).

## 7. Prompt-assembly rules for the answering LLM

When stuffing retrieved chunks into the chat completion prompt:

1. **De-duplicate by `(parent_id, header_1, header_2, header_3)`** so text + diagram + table_row of the same section aren't all repeated.
2. **Order by `(parent_id, layout_ordinal, table_row_index, physical_pdf_page)`** within each doc so the LLM reads in natural flow.
3. **Tag every chunk with its citation key** in the system prompt, e.g. `[doc:<chunk_id>] (record_type=<type>, page=<page>) <chunk>`. Instruct the LLM to cite by `chunk_id`.
4. **Token budget:** use `chunk_token_count` to fill ~70% of the prompt with text/table chunks; reserve room for diagram descriptions (typically 300–800 tokens each) and 1 summary chunk.
5. **System prompt clauses to add (cargo-cult these):**
   - "When a chunk has `record_type=diagram`, treat its content as a description of a figure. Cite `figure_ref` and `printed_page_label`."
   - "When a chunk has `record_type=table_row`, treat its content as a single row of the table named in `table_caption`. The full table is available via the parent table chunk if needed for context."
   - "When a chunk has `safety_callout=true`, lead the answer with the safety information and surface the callout type from `callouts`."
   - "Prefer chunks with newer `effective_date` when multiple revisions of the same `source_file` are returned."

## 8. Gotchas the chatbot agent MUST respect

1. **One index, many record types.** Always branch on `record_type` before rendering.
2. **Some fields are not retrievable** (`chunk_for_semantic`, `surrounding_context`, `text_vector`). Don't try to `$select` them — the request will fail or return null.
3. **`diagram_category` uses keyword analyzer** — exact-match filters only.
4. **Older PDFs may have empty cover metadata** (`document_revision`, `effective_date`, `document_number`) on table/diagram/summary records — see [process_document.py:80-95](function_app/shared/process_document.py#L80-L95). Tolerate empty strings.
5. **Always filter `processing_status eq 'ok'`.** Otherwise you'll surface incomplete records.
6. **`has_diagram=false` rows exist** — vision skipped them. Always combine with `has_diagram eq true` on diagram queries.
7. **`table_row` records only exist for 5–80 row tables.** Smaller and larger tables only have a `record_type='table'` parent.
8. **Embedding model is `text-embedding-ada-002` @ 1536 dim.** If you ever pass your own pre-computed vector, it MUST be 1536; otherwise prefer `vectorQueries.kind="text"`.
9. **`source_url`** is the blob URL (`metadata_storage_path`); often no anonymous read. Mint a SAS token at the backend before exposing to users.
10. **`id` is the Search-internal key**, not stable for caching. Use **`chunk_id`** for cache keys, deep-links, and analytics.

## 9. Wiring checklist for the back-end agent

- [ ] SDK client (Python / .NET / JS / TS) pointing at the Search endpoint + key (or use MI for prod).
- [ ] Implement `searchManual(query, filters, top)` issuing the §4.1 shape.
- [ ] UI facet selections → OData filter (`and`-joined `eq` predicates).
- [ ] Intent classifier (§4.2). Cheap regex is fine:
  - diagram/figure words → diagram filter
  - table/row/value/lookup words → table or table_row filter
  - safety/warning words → `safety_callout eq true`
  - "what is X" / "define X" / "meaning of X" → `record_subtype eq 'glossary'`
- [ ] Two-pass retrieval (§4.3).
- [ ] Cross-ref expansion (§4.4) when text top-hit has non-empty `figures_referenced` or `tables_referenced`.
- [ ] table_row → parent table fetch via `table_parent_chunk_id`.
- [ ] Hard filter `processing_status eq 'ok'`.
- [ ] Citation builder per §6.
- [ ] Prompt assembler per §7.
- [ ] Front-end renders diagrams (image + description), tables (inline markdown), badges (`safety_callout`, `callouts`), and revision footer.

## 10. Test queries to validate the integration

Run these against the chatbot once wired up. If any of them surfaces
no useful result, walk the §5 fix that matches the failure.

| Query | What should happen |
|---|---|
| "What does Figure X-Y show?" (use a real figure ref from a manual) | At least one `record_type=diagram` hit with `figure_ref` matching, `diagram_description` non-empty. Front-end shows the figure image. |
| "Show me the wiring diagram for ..." | `diagram_category=schematic_wiring` hit at the top. |
| "What is the rating for 200A at 277V?" (or analogous lookup) | A `record_type=table_row` hit. Parent table fetched and shown alongside. |
| "What warnings apply when working on energized equipment?" | Multiple hits with `safety_callout=true`; UI shows ⚠ badges. |
| "What does LOTO mean?" | Hit with `record_subtype=glossary` returning the definition. |
| "What's in <manual name>?" | A `record_type=summary` hit at top. No "jump to page" button. |
| "Latest revision of <manual>" | Sorted by `effective_date desc`; most recent revision appears first. |
| Any random procedural question | Hybrid+semantic returns mostly `text` records, with diagram/table cross-refs surfaced if the relevant text mentions them. |

## 11. Where to look in this repo for ground truth

| You want to know… | Read |
|---|---|
| Exact field list, types, capabilities | [search/index.json](search/index.json) |
| How each record is produced and which skill writes which field | [search/skillset.json](search/skillset.json) |
| How text records get callouts, glossary detection, equipment_ids | [function_app/shared/page_label.py](function_app/shared/page_label.py) |
| How `diagram_description` and `diagram_category` are produced | [function_app/shared/diagram.py](function_app/shared/diagram.py) |
| How tables are sliced into rows | [function_app/shared/process_table.py](function_app/shared/process_table.py) |
| Cover metadata extraction | [function_app/shared/process_document.py](function_app/shared/process_document.py) |
| chunk_id and parent_id construction | [function_app/shared/ids.py](function_app/shared/ids.py) |

Everything else (deployment, ops) is in [README.md](README.md) and
[docs/RUNBOOK.md](docs/RUNBOOK.md) — the chatbot agent does not need those.
