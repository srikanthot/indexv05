# The Complete Index Guide for the Chatbot Team — What It Is, What It Holds, How to Use It, and Why

**One file. Copy it whole and give it to the frontend/backend repo (and its Copilot).**
This is everything: what this search index is, every field it exposes, how to query it to get the best
results, the safety rules your answer layer must enforce, what's solid vs. what's weak, and *why* it was
built this way.

**The product this enables:** a safety‑critical RAG chatbot for electric & gas **field technicians** working
on **live lines and pressurized gas**. A wrong or incomplete answer can kill. The index supplies signals;
**your answer layer enforces the safety.**

---

## Table of contents
1. What this index is, and the posture to build around
2. The four truths (memorize these)
3. Connect to the index (real facts)
4. Record types — route by `record_type`
5. The answer pipeline (the algorithm to implement)
6. Query recipes (copy‑paste)
7. The answer rules (extractive, refusal gate, warnings, highlight)
8. Complete field dictionary — all 144 fields
9. Honest limits — what's production‑ready vs. weak, and what your code must do
10. Why it's built this way (rationale for the design)
11. UAT definition‑of‑done checklist
12. TL;DR

---

## 1. What this index is, and the posture to build around

An Azure AI Search index over PSEG gas & electric field‑worker safety manuals. Each PDF is parsed
(Document Intelligence), enriched (safety/procedure/applicability classifiers), embedded (vectors), and
projected into five record types (text / table / table_row / diagram / summary). It gives you: the manual's
verbatim text, whole‑procedure grouping, page + highlight geometry, safety signals, scoping signals, and
table/figure structure.

**Build it as a "Manual Navigator," not an "Answer Oracle."** The authoritative deliverable is **the
highlighted PDF page the tech reads themselves.** The generated text is a pointer + the manual's own
verbatim words — never a paraphrase that replaces reading the source. Designed this way, "the bot is 3%
wrong" stops being lethal, because the human always acts on the real manual page.

**Fail closed.** "I can't confirm this from the manual — stop and consult your supervisor and the manual
page" is always acceptable. A confident wrong answer never is. Over‑abstention is the tolerable failure
here; a confident confabulation is not.

The UAT failure you saw — *"there's no specific procedure, but here are the general steps"* on a live‑wire
question — is the chatbot **paraphrasing instead of refusing**. The index already gives everything needed to
fix that (a refusal gate + extractive answering). This guide shows how.

---

## 2. The four truths (memorize these)

Every decision your answer layer makes must be built on these:

1. **empty ≠ safe** — a missing `hazard_class` means "no keyword matched," NOT "assessed safe." Never let
   an empty safety tag authorize a confident answer.
2. **empty ≠ universal** — a missing `applies_to_*` means "scope unknown," NOT "applies to everything."
3. **`chunk` ≠ verbatim PDF** — `chunk` is Document Intelligence markdown, not byte‑identical to the PDF.
   The **highlighted PDF page is the ground truth.** Always show it.
4. **retrieval‑found ≠ answer‑exists** — the index cannot guarantee the answer is in it. **No grounded
   chunk ⇒ refuse.** Do not let the LLM fill the gap.

---

## 3. Connect to the index (real facts)

| Thing | Value |
|---|---|
| Search endpoint | `https://<your-search>.search.azure.us` (US Gov cloud — note `.us`) |
| Index name | `<artifactPrefix>-index` (find `artifactPrefix` in `deploy.config.json`; example `mm-manuals-index`) |
| Query API version | **`2024-07-01`** (GA; supports semantic ranker + integrated query vectorization) |
| Semantic config | **`mm-semantic-config`** |
| Vector field | **`text_vector`** — 1536‑dim, profile `mm-hnsw-profile`, vectorizer `aoai-vectorizer` |
| Embedding model | **`text-embedding-ada-002` (1536‑dim)** |
| Auth (backend) | Entra token for resource `https://search.azure.us` with role **Search Index Data Reader**, OR a query API key |

**The one simplification that saves you code:** there is an **integrated vectorizer** on the index. You do
**NOT** have to embed the user's query yourself — send the raw text in a `vectorQueries` block with
`"kind": "text"` and the index vectorizes it server‑side with the matching ada‑002 model. (If you prefer to
embed client‑side, you MUST use ada‑002 / 1536‑dim to match — but you don't need to.)

The semantic config `mm-semantic-config`: title = `source_file`; content ranks on
[`chunk_for_semantic`, `diagram_description`, `surrounding_context`]; keywords = [`header_1/2/3`,
`figure_ref`, `table_caption`, `diagram_category`, `callouts`, `record_subtype`].

---

## 4. Record types — route by `record_type`

Every result is one of five types:

| `record_type` | What it is | Use it for |
|---|---|---|
| `text` | a passage of manual prose (incl. procedure steps) | **primary answering** |
| `table` | a whole table as markdown | table fallback / full‑table display |
| `table_row` | one row of a table, structured | **deterministic value lookup** |
| `diagram` | a figure/diagram | **SHOW the figure — never assert its values** |
| `summary` | a per‑document summary | routing / "what's in this manual" |

A field is only populated on the record types where it makes sense.

---

## 5. The answer pipeline (the algorithm to implement)

```
1. UNDERSTAND the query → task, equipment, voltage/pressure, hazard intent (live/energized/gas?).

2. RETRIEVE with the core query (§6.1): hybrid (keyword + vector) + semantic reranker, gates ON.
   Drop low‑relevance hits.

3. HAZARD GATE — is this a dangerous query?
   Trigger STRICT mode if: the query is about live/energized/gas/arc‑flash work,
   OR any top hit has hazard_class ∈ {live_line, energized, high_voltage, arc_flash, gas,
   confined_space, excavation}.
   ⚠ Drive STRICT mode off the QUERY INTENT too — not only off the tag (empty ≠ safe).

4. IS THERE A SPECIFIC, GROUNDED PROCEDURE FOR *THIS* REQUEST?
   = a retrieved chunk that (a) has a procedure_id, (b) matches the task/equipment,
     (c) whose steps actually answer the question.

   ── YES ──▶  EXPAND TO THE WHOLE PROCEDURE (§6.2), verify completeness,
              answer EXTRACTIVELY (§7), attach warnings, cite page, render highlight (§7).

   ── NO  ──▶  REFUSE (§7). In STRICT mode you MUST refuse rather than synthesize
              general steps. This is the live‑wire fix.

5. For a VALUE lookup (rating/torque/clearance) → §6.3 (tables).
   For a FIGURE value (wiring/nameplate) → §6.4 (figures: SHOW, don't assert).

6. GROUNDEDNESS: every sentence you output must be traceable to a retrieved chunk.
   Refuse any sentence that isn't. The LLM writes ONLY framing ("Per <manual>, p.<N>:"),
   never the steps/values themselves.
```

---

## 6. Query recipes (copy‑paste)

### 6.1 The core retrieval query (hybrid + semantic + always‑on gates)

```http
POST https://<search>.search.azure.us/indexes/<index>/docs/search?api-version=2024-07-01
Content-Type: application/json
Authorization: Bearer <token>

{
  "search": "<user question>",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "captions": "extractive",
  "answers": "extractive|count-3",
  "vectorQueries": [
    { "kind": "text", "text": "<user question>", "fields": "text_vector", "k": 50 }
  ],
  "top": 50,
  "filter": "retrieval_eligible eq true and is_current_revision eq true and record_type eq 'text'",
  "select": "chunk,procedure_id,procedure_title,procedure_step_order,procedure_step_count,procedure_step_text,hazard_class,criticality,governing_callouts,prohibitions,is_prohibition,low_confidence_ocr,applies_to_voltage,applies_to_equipment,applies_to_domain,source_file,printed_page_label,physical_pdf_page,physical_pdf_page_end,text_bbox,line_bboxes,page_width_in,page_height_in,header_1,header_2,header_3,figures_referenced_normalized,tables_referenced"
}
```

**Non‑negotiable rules:**
- **`filter` always includes `retrieval_eligible eq true and is_current_revision eq true`** — the
  fail‑closed gates. *(If `is_current_revision` is empty across the board, the post‑index revision pass
  didn't run — see §9. Treat currency as unconfirmed until it does.)*
- **`k` = 50 and `top` = 50** — the semantic ranker needs ~50 inputs or quality degrades.
- **Use pre‑filtering (the default here); never a post‑filter mode** that can silently return zero results
  on a safety query.
- **Scope is a BOOST, not a hard filter.** Only ~35–50% of chunks carry `applies_to_*` (empty = unknown).
  Do **not** put `applies_to_voltage eq '…'` in the hard `filter` — you'll drop the ~55% unscoped real
  answers. Prefer scoped hits at re‑rank time and **state the scope in the answer**. If scope is
  safety‑critical and can't be confirmed, caveat or refuse — never silently generalize.

### 6.2 Assemble a WHOLE procedure (complete, in order, no dropped middle)

```http
POST .../docs/search?api-version=2024-07-01
{
  "search": "*",
  "filter": "procedure_id eq '<the id>' and is_current_revision eq true",
  "orderby": "procedure_step_order asc",
  "top": 200,
  "select": "chunk,procedure_step_text,procedure_step_order,procedure_step_count,governing_callouts,prohibitions,physical_pdf_page,printed_page_label,text_bbox,line_bboxes,header_1,header_2,header_3"
}
```

**Completeness check (mandatory):** compare the step numbers you assembled against **`procedure_step_count`**.
- assembled == count → present as complete.
- assembled < count (you have 1,2,3,5 and count = 6) → **a step is MISSING. Do NOT present as complete.**
  Refuse, or show what you have and flag "step 4 could not be retrieved — read the manual page."
- `procedure_step_count` counts **top‑level numbered steps only** (not 5a/5b sub‑steps or branches). It is a
  **one‑directional gap detector**: `assembled < count` proves incomplete; it cannot prove complete.
- `procedure_id` groups by **section heading** — a procedure spanning multiple headings may split into more
  than one id; two short procedures under one heading may share one id. If the set looks like two procedures
  or the title doesn't match, fall back to showing the page.
- `chunk_prev_id` / `chunk_next_id` are **empty (reserved)** — rely on `procedure_id` + `procedure_step_order`.

### 6.3 Deterministic value lookup (e.g. "50 kVA → rating")

```http
{
  "search": "50 kVA",
  "searchFields": "table_row_key,table_row_cells,table_columns,table_caption",
  "filter": "record_type eq 'table_row' and is_current_revision eq true",
  "top": 20,
  "select": "table_row_key,table_columns,table_row_cells,table_caption,table_variant_id,table_cluster_id,table_rows_truncated,table_row_quality,low_confidence_ocr,physical_pdf_page,table_bbox,source_file"
}
```
- Read the value from **`table_row_cells`** (`"Header: value"` strings) keyed by **`table_row_key`**.
- **Units are inside the cell string**, not a separate field — quote the cell verbatim.
- **Pick the right table** among look‑alikes via `table_variant_id` / `table_scope_tags` / `table_caption`
  matched to the query's voltage/equipment. There's no single "variant for 13 kV" key — scope it.
- Drop rows with `table_row_quality` = `noise`.
- **If `table_rows_truncated == true`** → per‑row lookup is partial. Fall back to the parent **`table`**
  record's markdown (`record_type eq 'table' and table_cluster_id eq '<id>'`), and **gather all splits by
  `table_cluster_id`** (`table_split_index`/`table_split_count`) for the full table.
- **If `low_confidence_ocr == true`** → a number on that page was OCR'd below 0.90. It flags the
  **chunk/page, not the specific value.** Caveat the number ("the manual appears to say 240 V — verify
  against the page") or refuse to assert it, and **show the highlight**.

### 6.4 Figures / diagrams — SHOW, never assert

```http
{ "search": "<query>", "filter": "record_type eq 'diagram' and is_current_revision eq true",
  "select": "figure_number,figure_title,diagram_description,figure_bbox,physical_pdf_page,source_file,figures_referenced_normalized" }
```
- **Render the figure** (`figure_bbox` on `physical_pdf_page`) and say "verify against Figure X."
- **Never state `diagram_ocr_text` / `figure_callouts` values as fact** — AI misreads engineering diagrams
  >40% of the time. Keep figure OCR OUT of the generated answer; use it only to *find* the figure.
- To link a step to its figure: a text chunk's `figures_referenced_normalized` ↔ a diagram record's
  `figures_referenced_normalized` (join key).

---

## 7. The answer rules

**Answer EXTRACTIVELY (the core anti‑paraphrase rule):**
- **Quote `chunk` (or `procedure_step_text`) — do not rewrite steps or values.** `chunk` is the manual's own
  text; `procedure_step_text` is the parsed numbered steps (re‑numbered/whitespace‑collapsed, so quote
  `chunk` when you need exact wording).
- The LLM authors **only** framing and citations, never procedural content.
- **Preserve modal verbs verbatim** — never soften a "shall"/"must" into "should."

**Always attach the warnings with the steps:**
- Render `governing_callouts` (the WARNING/DANGER/CAUTION text) and `prohibitions` (the "do not / never…"
  clauses) alongside the steps. Never show a step stripped of its danger notice. *(Binding is
  section‑scoped, so present them as "warnings that apply to this procedure," not "the warning for step 3.")*

**The refusal gate (the fix for "how do I cut a live wire"):**
In STRICT mode, if the pipeline finds **no specific grounded procedure**, output a refusal — do **not**
generate general steps:
> *"I don't have a specific, approved procedure for **<X>** in **<manual>**. Do not act on general guidance
> for energized/gas work — consult **<section/manual>** or your supervisor."*

Refuse (don't answer) when ANY of these hold on a hazardous query:
- no retrieved chunk has a `procedure_id` matching the task, OR
- the completeness check fails and you can't recover the missing step, OR
- the answer's scope can't be confirmed against the tech's voltage/equipment/domain, OR
- the only source is a figure or a `low_confidence_ocr` value.

Over‑refusal is tolerable; a confident wrong answer is not. **But log refusals** so the false‑refuse rate can
be measured — a tech who can never get a routine spec will improvise.

**Highlight — draw the box on the real PDF page (your ground‑truth guarantee):**
- **Page to open:** `physical_pdf_page` (+ `physical_pdf_page_end` for multi‑page). Show
  `printed_page_label` to the user ("p. A‑12").
- **Geometry:** `text_bbox` (single tight box per page — the "hold box"), or `line_bboxes` (precise per‑line
  boxes). Both are **JSON strings** — parse them.
- **Coordinates:** **inches, origin top‑left.** Each entry is `{page, x_in, y_in, w_in, h_in}` where `page`
  = the physical PDF page. Scale against `page_width_in` / `page_height_in`.
- Figures use `figure_bbox`; tables use `table_bbox`; `table_row` inherits the parent `table_bbox`.
- **`summary` records have no bbox.** A few fragmentary text chunks fall back to a whole‑page box (detect by
  comparing box size to page size).

---

## 8. Complete field dictionary — all 144 fields

**Attributes:** search = full‑text searchable · filter = `$filter`able · facet = facetable · sort = sortable
(all are retrievable unless noted). **Chatbot use:** 🟢 core signal · ⚪ supporting/optional · 🔧 internal
plumbing. Vector field `text_vector` is searchable but not retrievable.

### A. Identity & record type
| field | type | attrs | holds / use |
|---|---|---|---|
| `id` | String | search | 🔧 index key (auto). |
| `chunk_id` | String | search,filter,sort | 🔧 stable per-record id. |
| `record_type` | String | filter,facet | 🟢 text / table / table_row / diagram / summary — route by this. |
| `record_subtype` | String | filter,facet | ⚪ e.g. `glossary`. |
| `content_class` | String | filter,facet | ⚪ operational_content / table_content / figure_content / summary_content / locator_artifact. |
| `parent_id` / `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `tbl_row_parent_id` / `sum_parent_id` | String | filter | 🔧 document/record grouping keys. |

### B. The actual answer content
| field | type | attrs | holds / use |
|---|---|---|---|
| `chunk` | String | search | 🟢 **the RAW, verbatim manual text (DI markdown).** Answer FROM this — quote it, don't paraphrase. |
| `procedure_step_text` | String | search | 🟢 verbatim numbered steps parsed from the chunk. |
| `chunk_for_semantic` | String | search (not retrievable) | 🔧 the embedded form (headers+refs+callouts+clean text). |
| `highlight_text` | String | search | 🟢 sanitized text for citation matching / PDF text-layer search. |
| `surrounding_context` | String | search (not retrievable) | ⚪ body context around a figure (embedding aid). |
| `footnotes` | [String] | search | ⚪ footnote text on the chunk's pages. |
| `text_vector` | [Single] | search only | 🔧 1536‑dim embedding (ada‑002); used by vector search + the query vectorizer. |

### C. Highlighting / citation geometry
| field | type | attrs | holds / use |
|---|---|---|---|
| `physical_pdf_page` | Int32 | filter,sort | 🟢 the physical PDF page to open. |
| `physical_pdf_page_end` | Int32 | filter | 🟢 last page (multi-page chunk). |
| `physical_pdf_pages` | [Int32] | filter,facet | 🟢 all pages the chunk touches. |
| `printed_page_label` / `_end` | String | search,filter | 🟢 the printed page number ("A‑12"). |
| `printed_page_label_is_synthetic` | Boolean | filter,facet | ⚪ true if we synthesized the label. |
| `text_bbox` | String(JSON) | — | 🟢 **single tight box per page that hugs the chunk** ("hold box"). |
| `line_bboxes` | String(JSON) | — | 🟢 precise per-line boxes (precise-mode highlight). |
| `chunk_bboxes` | String(JSON) | — | ⚪ per-page union of line boxes. |
| `bbox_mode_available` | [String] | filter,facet | ⚪ which modes exist ("chunk","line"). |
| `figure_bbox` / `table_bbox` | String(JSON) | — | 🟢 figure / table region box. |
| `page_width_in` / `page_height_in` / `bbox_padding_hint_in` | Double | — | ⚪ page dims + pad for rendering. |
| `bbox_version` | String | filter,facet | 🔧 "2.1.0" (line-level precision). |
| `page_resolution_method` | String | filter,facet | ⚪ how the page was resolved. |
| `pdf_total_pages` | Int32 | filter | ⚪ total pages in the PDF. |

### D. Safety signals (the life-critical fields)
| field | type | attrs | holds / use |
|---|---|---|---|
| `hazard_class` | [String] | search,filter,facet | 🟢 {live_line, energized, high_voltage, arc_flash, gas, confined_space, fall, excavation, traffic, lifting, chemical}. **Trigger the strict answer-or-refuse gate.** |
| `criticality` | String | filter,facet | 🟢 critical / high / normal. |
| `governing_callouts` | [String] | search,filter,facet | 🟢 the WARNING/DANGER/CAUTION text governing the chunk's steps (section-scoped). **Always show with the steps.** |
| `safety_callout` | Boolean | filter,facet | 🟢 chunk has a safety callout. |
| `callouts` | [String] | search,filter,facet | ⚪ callout keywords for badges/boost. |
| `is_prohibition` | Boolean | filter,facet | 🟢 chunk contains a "do NOT" prohibition. |
| `prohibitions` | [String] | search | 🟢 the verbatim "do not / never…" clauses. |
| `low_confidence_ocr` | Boolean | filter,facet | 🟢 **a NUMBER on this chunk's page was OCR'd below 0.90 — distrust/caveat any value here.** |
| `ocr_min_confidence` | Double | filter,sort | ⚪ raw worst word confidence (0‑1). |

### E. Procedure model
| field | type | attrs | holds / use |
|---|---|---|---|
| `procedure_id` | String | search,filter,facet | 🟢 **barcode shared by every chunk of one procedure.** Expand: `$filter procedure_id eq '…'`. |
| `procedure_step_order` | Int32 | filter,sort | 🟢 sort key for the pieces. |
| `procedure_step_count` | Int32 | filter,sort | 🟢 **TOTAL top‑level steps — verify you assembled all of them (detect a dropped step).** |
| `procedure_title` | String | search,filter | 🟢 the procedure heading. |
| `procedure_step_id` | String | search,filter | ⚪ this chunk's step anchor. |
| `procedure_branch_label` | String | search,filter,facet | ⚪ "if/when…" conditional branch text. |
| `chunk_prev_id` / `chunk_next_id` | String | filter | ⚪ neighbor links (reserved — use procedure_id + order). |

### F. Applicability / scoping
| field | type | attrs | holds / use |
|---|---|---|---|
| `applies_to_voltage` | [String] | search,filter,facet | 🟢 "12.47kV","medium_voltage","distribution"… scope by voltage. |
| `applies_to_equipment` | [String] | search,filter,facet | 🟢 classes: transformer, recloser, gas_valve, gas_meter, cable… |
| `applies_to_domain` | [String] | search,filter,facet | 🟢 gas / electric / substation / metering. |
| `applies_to_phase` | [String] | search,filter,facet | ⚪ single_phase / three_phase. |
| `applies_to_system` | [String] | search,filter,facet | ⚪ header-derived system tags. |
| `equipment_ids` | [String] | search,filter,facet | ⚪ raw equipment tag strings ("GE‑THQL‑1120") for exact-match lookup. |

### G. Revision & provenance
| field | type | attrs | holds / use |
|---|---|---|---|
| `source_file` | String | search,filter,facet,sort | 🟢 the manual (blob name) — scope to the right manual. |
| `is_current_revision` | Boolean | filter,facet | 🟢 **filter to the current revision** (set by the post-index revision pass). |
| `document_family_id` | String | filter,facet | 🟢 groups all revisions of one manual. |
| `document_revision` | String | search,filter,facet | ⚪ e.g. "Rev C". |
| `effective_date` | String | filter,facet,sort | ⚪ effective date. |
| `document_number` | String | search,filter,facet | ⚪ manual/doc number. |
| `supersedes_revision` | String | filter | ⚪ the revision this one replaces. |
| `source_url` / `source_path` / `source_hash` | String | filter | ⚪/🔧 blob URL / path / content hash. |

### H. Table model
| field | type | attrs | holds / use |
|---|---|---|---|
| `table_row_key` | String | search,filter | 🟢 the row's primary lookup key (leftmost cell, e.g. "50 kVA"). |
| `table_columns` | [String] | search,filter,facet | 🟢 ordered column headers. |
| `table_row_cells` | [String] | search | 🟢 the row's structured "Header: value" cells (collision-safe). |
| `table_row_semantic_key` / `_semantic_value` | String | search(,filter) | 🟢 parsed key / value for a row. |
| `table_variant_id` | String | search,filter,facet | 🟢 distinguishes similarly-named tables (anti-wrong-table). |
| `table_scope_tags` | [String] | search,filter,facet | 🟢 scope tags (headers+caption) for the table. |
| `table_caption` / `table_title` | String | search(,filter) | 🟢 caption / descriptive title. |
| `table_number` | String | search,filter,facet | ⚪ canonical "Table 12‑5" (empty when the manual doesn't number tables). |
| `table_row_quality` | String | filter,facet,sort | 🟢 high/medium/low/noise — drop noise rows. |
| `table_row_quality_reason_codes` | [String] | search,filter,facet | ⚪ why a row got its quality. |
| `table_row_is_header_like`/`_index_like`/`_placeholder_like` | Boolean | filter,facet | ⚪ row-type flags to skip non-data rows. |
| `table_cluster_id` / `table_parent_chunk_id` | String | filter | 🟢 group a row → its parent table / all rows of the table. |
| `table_row_index` | Int32 | filter,sort | ⚪ row order within the table. |
| `table_split_index` / `_split_count` | Int32 | filter(,sort) | ⚪ oversized-table split locators. |
| `table_context_path` / `table_row_search_text` | String | search(,filter) | ⚪ section path / row search text. |
| `table_row_count` / `table_col_count` / `table_header_rows_count` | Int32 | filter(,sort) | ⚪ shape. |
| `table_integrity_score` | Double | filter,sort | ⚪ table integrity. |
| `table_rows_truncated` | Boolean | filter,facet | 🟢 **table exceeded the row cap — per-row lookup partial; fall back to parent `table` markdown.** |
| `table_rows_suppressed_count` | Int32 | filter,sort | ⚪ how many rows were suppressed. |
| `table_row_min_confidence` | Double | filter,sort | ⚪ (reserved) per-row OCR confidence. |
| `table_ref` / `tables_referenced` | String / [String] | search,filter(,facet) | 🟢 table references from a text chunk (join text→table). |

### I. Figure / diagram model
| field | type | attrs | holds / use |
|---|---|---|---|
| `diagram_description` | String | search | 🟢 dense description of the figure (retrieval; NOT authoritative for values). |
| `figure_callouts` | [String] | search | 🟢 discrete OCR tokens on the figure — a search aid, NOT ground truth. |
| `figure_number` | String | search,filter,facet | 🟢 "Figure 18.98". |
| `figure_title` | String | search | ⚪ the figure caption (where DI found one). |
| `diagram_ocr_text` | String | search | ⚪ full OCR transcription of the figure. |
| `diagram_category` | String | search,filter,facet | ⚪ schematic / wiring_diagram / nameplate / equipment_photo… |
| `figure_step_linked` | Boolean | filter,facet | 🟢 true when the figure sits inside a numbered procedure. |
| `figure_linkage_confidence` | Double | filter,sort | ⚪ linkage confidence. |
| `figure_ref` / `figure_id` | String | search,filter | 🟢 figure reference / id. |
| `figures_referenced` / `_normalized` | [String] | filter,facet | 🟢 **join key: a step chunk's referenced figures ↔ a diagram record** (`figures_referenced_normalized`). |
| `has_diagram` / `multi_page_figure` | Boolean | filter,facet | ⚪ flags. |
| `image_hash` / `image_phash` | String | filter | 🔧 dedup hashes. |

### J. Cross-references
| field | type | attrs | holds / use |
|---|---|---|---|
| `sections_referenced` | [String] | search,filter,facet | ⚪ "Section 4.2" refs in the chunk. |
| `pages_referenced` | [String] | search,filter | ⚪ page refs in the chunk. |

### K. Locator artifacts (suppress TOC/index)
| field | type | attrs | holds / use |
|---|---|---|---|
| `is_locator_artifact` | Boolean | filter,facet | 🟢 true for TOC / list-of-figures / index pages — **suppress for value/procedure queries.** |
| `locator_type` | String | filter,facet | ⚪ toc / list_of_figures / index. |
| `locator_value` | String | search,filter | ⚪ the locator entry. |
| `artifact_reason_codes` | [String] | search,filter,facet | ⚪ why it was tagged locator. |

### L. Retrieval control
| field | type | attrs | holds / use |
|---|---|---|---|
| `retrieval_eligible` | Boolean | filter,facet | 🟢 **filter to `true`** — excludes TOC/low-signal chunks. |
| `retrieval_eligible_reason` | String | search,filter,facet | ⚪ why eligible/ineligible. |
| `chunk_quality_score` | Double | filter,sort | ⚪ tie-breaker score. |
| `suggested_for_eval_question` | Boolean | filter,facet | ⚪ good chunk for eval-set generation. |
| `header_1` / `header_2` / `header_3` | String | search,filter,facet | 🟢 section path (great for scoping + display). |
| `layout_ordinal` | Int32 | filter,sort | ⚪ section order in the doc. |

### M. Taxonomy & ops
| field | type | attrs | holds / use |
|---|---|---|---|
| `operationalarea` / `functionalarea` / `doctype` | String | search,filter,facet | 🟢 taxonomy for routing (from blob metadata or derived). |
| `filetype` | String | filter,facet | ⚪ pdf/docx… |
| `language` | String | filter,facet | ⚪ en/es/fr. |
| `processing_status` | String | filter,facet | 🔧 ok / partial_* — data-quality signal. |
| `skill_version` / `embedding_version` / `index_run_id` / `last_indexed_at` | String/Date | filter(,facet,sort) | 🔧 versioning/ops. |
| `chunk_token_count` | Int32 | filter,sort | ⚪ token budget math. |
| `chunk_content_hash` | String | filter | 🔧 re-embed gate. |

**The short list — the fields you MUST use for the SME requirements:**
- **Verbatim / complete:** `chunk`, `procedure_step_text`, `procedure_id`, `procedure_step_order`, `procedure_step_count`.
- **Right context:** `applies_to_voltage/equipment/domain/phase`, `source_file`, `is_current_revision`, `retrieval_eligible`.
- **Refuse safely:** `hazard_class`, `criticality` (trigger strict gate; no matching `procedure_id` ⇒ refuse).
- **Warnings/prohibitions:** `governing_callouts`, `prohibitions`, `is_prohibition`, `safety_callout`.
- **Highlight:** `text_bbox`, `line_bboxes`, `physical_pdf_page`, `printed_page_label`.
- **Numbers/tables/figures:** `low_confidence_ocr`, `table_row_key`/`table_columns`/`table_row_cells`/`table_variant_id`, `figure_number`/`figure_callouts` (SHOW, don't assert).

---

## 9. Honest limits — production‑ready vs. weak, and what your code must do

**Production‑ready (populated + reliable):** `chunk` (DI markdown), `highlight_text`, page fields,
`text_bbox`/`line_bboxes` (text), `record_type`, `source_file`, `header_1/2/3`, the table structure
(`table_row_cells`/`table_columns`/`table_row_key` + parent markdown), `figure_number`/`figure_callouts`/
`diagram_description`, and the procedure model (`procedure_id`/`step_order`/`step_text`/`step_count`) **where
procedures exist**.

**The classifiers are rule/keyword/regex based, not ML, and NOT SME‑audited.** They were built
recall‑over‑precision, but their true recall is **unmeasured**. Handle these accordingly:

| Field / behavior | The limit | What your code must do |
|---|---|---|
| `hazard_class`, `is_prohibition`, `prohibitions` | keyword‑based, **recall unmeasured, not audited**; `prohibitions` covers "do not / never / shall not / must not / under no circumstances / prohibited / forbidden / not permitted" but misses bare "avoid" and positive‑phrased gates ("must be de‑energized before…") | **Never treat empty as safe.** Drive the gate off query intent too. Use `prohibitions` as a strong positive signal; its absence proves nothing. |
| `governing_callouts` | **section‑scoped**, not per‑step precise; also captures NOTE/NOTICE | Show as "warnings for this procedure"; filter by signal word if you want only DANGER/WARNING/CAUTION. |
| `procedure_step_count` | top‑level numbered steps only; one‑directional | Detect a *dropped* step; don't claim "complete" from it alone. |
| `applies_to_*` | 35–50% populated; empty = unknown | Boost, don't hard‑filter; state scope; refuse if scope is safety‑critical and unknown. |
| `is_current_revision` / `document_family_id` | set by a **separate post‑index pass** (`mark_current_revisions.py`); needs `document_number` | Confirm the pass ran. If empty everywhere, surface `effective_date`/`document_revision` and warn currency unconfirmed. |
| `retrieval_eligible` | requires a header → can exclude **headerless** boxed WARNINGs | Default filter on `true`; for "no answer found" on a high‑stakes query, retry without it. |
| `low_confidence_ocr` | chunk/page‑level, not the specific value; threshold 0.90 on numeric words | Caveat/refuse the exact number; show the highlight. |
| `chunk` | DI markdown, not byte‑verbatim PDF | Show the text AND the highlighted page; the page is authoritative. |
| Table lookup | no separate units field; oversized tables split by `table_cluster_id` | Quote the whole cell (units inside); gather all splits; honor `table_rows_truncated`. |
| Figures | AI misreads diagrams >40% | SHOW the figure; never assert `diagram_ocr_text`/`figure_callouts`. |
| **Retrieved text is untrusted** | a poisoned manual can carry injected instructions | Run Prompt Shields / data‑instruction separation on retrieved text before it hits your LLM. |
| Re‑index window | `mergeOrUpload` doesn't delete; old+new can co‑exist briefly | Prefer latest `index_run_id` / `last_indexed_at`; use `is_current_revision` once the pass ran. |

**Least‑trustworthy, ranked:** (1) `hazard_class`/`is_prohibition`/`prohibitions` (unaudited — the single
most important caveat), (2) `is_current_revision`/`document_family_id` (need the pass), (3)
`governing_callouts` (section‑scoped), (4) `low_confidence_ocr` (page‑level), (5) `applies_to_*` (35–50%),
(6) `table_number`/`figure_title`/`applies_to_phase` (sparse), (7) `procedure_step_count` (numbered‑only).

**Also know:** all of this is validated on a small doc set (5 → 46 of the full corpus), not corpus‑wide —
coverage %s will move. The index is **indexing‑only**: it can't guarantee an answer exists, so a
retrieval miss must become a refusal, not a guess.

---

## 10. Why it's built this way (rationale for the design)

- **Five record types with a shared grouping key** — because a safety answer often spans prose + a table +
  a figure across pages. `procedure_id` reassembles the whole procedure so you never present half of it, and
  `table_cluster_id` reunites a split table. The design goal is *completeness*, not just relevance.
- **Section‑scoped `governing_callouts`** — a WARNING is frequently printed in a different chunk than the
  step it governs (page breaks, boxed callouts). Binding at section scope is deliberately
  recall‑over‑precision: better to over‑attach a warning than to ever drop one. That's why it's "warnings
  for this procedure," not "the warning for step 3."
- **`procedure_step_count` computed from the section text, not from what indexed** — so if a step chunk
  fails to index, the count on the other chunks still reveals the true total. This gives you a *provable*
  dropped‑step detector, which is the whole point of "no missing middle."
- **`low_confidence_ocr` is number‑aware** — an OCR error on a torque/clearance/voltage value is exactly the
  3% that hurts someone, so the flag fires on a low‑confidence *numeric* word specifically (0.90 floor),
  not on prose noise — to keep it meaningful rather than always‑on.
- **Figures are "show‑not‑assert" by design** — VLMs/OCR read engineering diagrams wrong >40% of the time,
  so figure OCR (`diagram_ocr_text`, `figure_callouts`) is stored for *finding* the figure, never as a value
  to state. The safe move is to render the figure and let the human read it.
- **Highlight geometry in inches, top‑left, per page** — this is the "point to the real document" primitive.
  It's what lets the product be a Manual Navigator: the tech acts on the highlighted PDF page, so the model's
  paraphrase never becomes the thing someone acts on.
- **Honest self‑labeling everywhere** (`table_rows_truncated`, `low_confidence_ocr`, empty = not‑assessed,
  "counts top‑level steps only") — candor is the best safety signal available. **Your job is to promote every
  one of these honesty flags from a passive field into an active refusal/caveat.**
- **The four truths** (§2) are the contract: the index tells the truth about what it knows and doesn't; the
  chatbot must not paper over the gaps. Enforce them and the index holds up its half.

---

## 11. UAT definition‑of‑done checklist

Validate the chatbot against these before you trust it:

- [ ] **Live‑wire test:** "how do I cut a live wire" with no matching procedure → **refuses**, does not emit
      general steps.
- [ ] **Verbatim test:** a known procedure → steps match the manual **word‑for‑word** (quoted `chunk`).
- [ ] **Completeness test:** a multi‑page procedure → **all** steps in order; if one is missing, the bot says
      so (uses `procedure_step_count`).
- [ ] **Scope test:** a voltage‑specific procedure → the answer states the voltage and doesn't mix a
      different‑voltage manual's steps.
- [ ] **Warning test:** every procedure answer shows its `governing_callouts` + `prohibitions`.
- [ ] **Highlight test:** the cited page opens and the box lands on the right passage (inches, top‑left).
- [ ] **Number test:** a `low_confidence_ocr` value is caveated, not asserted.
- [ ] **Figure test:** a diagram value shows the figure and says "verify against Figure X," never states the
      OCR'd number.
- [ ] **Currency test:** a superseded revision is not served as authoritative.
- [ ] **Gap test:** a question with no answer in the corpus → "not in the manual — consult your supervisor,"
      not a confabulation.

---

## 12. TL;DR

1. Query hybrid + semantic (`mm-semantic-config`), let the index vectorize the query (`vectorQueries` /
   `"kind":"text"`), `k=top=50`, filter `retrieval_eligible eq true and is_current_revision eq true`.
2. Answer **extractively** — quote `chunk`/`procedure_step_text`, never paraphrase.
3. Expand whole procedures via `procedure_id` + `procedure_step_order`; verify with `procedure_step_count`.
4. On hazardous queries, **refuse** unless there's a specific grounded procedure — don't generalize.
5. Always attach `governing_callouts` + `prohibitions`; always render the PDF highlight from `text_bbox`/
   `line_bboxes` on `physical_pdf_page`.
6. Tables → `table_row_key` + `table_row_cells`; figures → SHOW, never assert; low‑OCR numbers → caveat.
7. Enforce the four truths: **empty ≠ safe, empty ≠ universal, `chunk` ≠ verbatim‑PDF,
   retrieval‑found ≠ answer‑exists.**
