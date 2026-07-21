# Index Field Glossary + Concepts (for the team)

Two parts:
1. **Concepts** — Document Intelligence vs Context Understanding, and how a
   multi-page procedure is linked together.
2. **Field glossary** — every index field, in plain English: *what it is* and
   *what it's used for*.

---

# 1. Concepts

## 1a. Document Intelligence vs Context Understanding

These are **two layers**, and we use **both**.

### Document Intelligence (DI) — *extraction: WHAT is on the page and WHERE*
Azure Document Intelligence reads the PDF's **layout and structure**. It gives us:
- All the **text**, in correct reading order (even multi-column).
- **Tables** broken into rows and columns.
- **Figures / images** and their captions.
- **Headings** (title / section heading).
- **Page numbers** and **bounding boxes** (the exact x/y position of every line, table, and figure on the page).

DI answers: *"What text, tables, and figures are on this page, and where exactly?"*
It is essentially **advanced OCR + layout analysis**. It does **not** understand meaning.

### Context Understanding — *interpretation: what it MEANS and how it RELATES*
On top of DI's raw structure, our **custom skills + GPT vision + classifiers**
add meaning. This layer decides:
- Is this chunk a **procedure**? Which **step**?
- What **voltage / equipment / system** does it apply to?
- Is there a **DANGER / WARNING** here? What **hazard**?
- What does this **diagram** show (GPT vision describes it)?
- Which **figure / table / section** does this text reference?
- Which chunks belong to the **same topic** (so we can return them together)?
- Is this the **current revision**, or superseded?

Context Understanding answers: *"What does this content mean, how risky is it,
what does it apply to, and how does it connect to the rest of the manual?"*

### Why both — the difference it makes
| | DI only | DI + Context Understanding |
|---|---|---|
| Find text/tables by keyword | ✅ | ✅ |
| Show the exact page + highlight | ✅ | ✅ |
| Return a **complete multi-page procedure** in order | ❌ | ✅ (topic_id/procedure_id) |
| Scope an answer to **12kV / a specific system** | ❌ | ✅ (applies_to_*) |
| Surface the governing **DANGER/WARNING** with a step | ❌ | ✅ (callouts/hazard) |
| Avoid citing a **superseded** revision | ❌ | ✅ (is_current_revision) |
| Understand a **diagram's** content | ❌ | ✅ (GPT vision) |

**One-line for a manager:** *"Document Intelligence extracts the manual's text,
tables, figures, and their exact page positions. Context Understanding adds the
meaning on top — what's a procedure, what voltage it applies to, what's a safety
warning, which pieces belong together — so the bot returns complete, correctly-
scoped, safety-aware answers instead of just keyword matches."*

## 1b. How a multi-page procedure is linked together (the manager's question)

**The problem:** a maintenance procedure can span 3 pages and be split into
10–15 chunks (text steps + a figure + a checklist table). If the bot just does a
keyword/vector search, it might return steps 1, 2, and 4 and miss step 3 — or
miss the figure and the table entirely.

**How we solve it — shared keys + an ordering + a completeness check:**

1. **`procedure_id`** — every chunk of the same procedure gets the *same*
   `procedure_id` (derived from the section). So one filter,
   `$filter procedure_id eq '<id>'`, returns **all** chunks of that procedure.
2. **`topic_id`** — broader: the procedure's **text + its figure + its checklist
   table** all share one `topic_id`. `$filter topic_id eq '<id>'` returns the
   **whole topic**, across record types, in one shot.
3. **`procedure_step_order`** — numbers the steps (1, 2, 3…) so they're returned
   **in the right order**.
4. **`procedure_step_count`** — the **total** number of steps in the procedure
   (same on every chunk). The bot compares the steps it actually retrieved
   against this — if it got 1,2,4 but the count says 5, it **knows a chunk is
   missing** and won't answer as if complete.
5. **`physical_pdf_page`** — orders chunks by page for continuous reading.

**The flow the chatbot uses:**
```
1. Search finds ONE chunk of the procedure (a semantic/keyword hit).
2. Read its procedure_id (or topic_id).
3. Re-query: $filter procedure_id eq '<id>'  (or topic_id)
             $orderby procedure_step_order (or physical_pdf_page)
4. Assert: retrieved step count == procedure_step_count  → complete?
5. Return the full, ordered procedure — steps + its figure + its checklist table.
```

So the chunks are **related to each other by the shared `procedure_id` /
`topic_id`**, **ordered by `procedure_step_order` / `physical_pdf_page`**, and
**verified complete by `procedure_step_count`**. That's how a 3-page procedure
comes back whole, in order, nothing missing.

*(Note: `chunk_prev_id` / `chunk_next_id` are reserved for direct
previous/next-chunk links; today the same result is achieved with
`procedure_id` + `procedure_step_order`.)*

---

# 2. Field glossary — what each field is + what it's for

### Identity — which record is this
| Field | What it is / what it's for |
|---|---|
| `id` | Unique key for the record (auto-generated). |
| `chunk_id` | Stable ID for this chunk — survives re-indexing. |
| `parent_id` | The document (PDF) this chunk belongs to. |
| `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `tbl_row_parent_id` / `sum_parent_id` | Per-record-type parent links (text/diagram/table/row/summary). |
| `record_type` | text / diagram / table / table_row / summary. |
| `record_subtype` | Finer classification within a type. |
| `topic_id` | Groups a whole topic's chunks (text + figure + table) so the bot returns them together. |

### Content
| Field | What it is / what it's for |
|---|---|
| `chunk` | **The actual text** the bot reads, quotes, and shows the user. |
| `chunk_for_semantic` | A cleaned/condensed copy of the chunk used to build the embedding (better semantic matching). Not shown to users. |
| `highlight_text` | Sanitized version of the text used to match/locate it in the PDF's text layer for highlighting. |
| `text_vector` | The numeric **embedding** of the chunk (1536 numbers) — powers semantic/vector search. Not human-readable. |
| `surrounding_context` | Nearby text around a figure/chunk, for extra context when embedding. |

### Location / page (citations)
| Field | What it is / what it's for |
|---|---|
| `physical_pdf_page` / `_end` | The **real sequential PDF page** (1,2,3…) — used to open/jump to the page and to draw highlights. `_end` for multi-page chunks. |
| `physical_pdf_pages` | The full list of pages the chunk covers. |
| `printed_page_label` / `_end` | The page number **printed on the page** ("9-1") — shown to the user. |
| `printed_page_label_is_synthetic` | True if we had to **estimate** the label (page didn't print one) — lets the UI mark it "approximate." |
| `pdf_total_pages` | Total pages in the PDF (sanity bound for citations). |
| `layout_ordinal` | Position/order of the chunk within the document. |
| `page_resolution_method` | How we determined the page (di_input / header_match / …) — a confidence signal. |

### Highlighting geometry
| Field | What it is / what it's for |
|---|---|
| `chunk_span_bboxes` | **The recommended highlight** — one continuous box per page covering the whole chunk (no gaps). |
| `text_bbox` | A single tight box per page (the "hold box"). |
| `line_bboxes` | Precise per-line boxes (accurate but can have gaps). |
| `chunk_bboxes` | Per-page union box of the matched lines. |
| `figure_bbox` / `table_bbox` | The box around a figure / table. |
| `page_width_in` / `page_height_in` | The real page size (inches) — the frontend scales the boxes against this. |
| `bbox_padding_hint_in` | Suggested padding around the box. |
| `bbox_mode_available` | Which box types this record has (span / chunk / line). |
| `bbox_version` | Version of the bbox logic (for debugging). |

### Structure / headings
| Field | What it is / what it's for |
|---|---|
| `header_1` / `header_2` / `header_3` | The section heading hierarchy this chunk sits under. |
| `section_path` | The breadcrumb "H1 › H2 › H3" — shows *where in the manual* a result came from. |
| `chapter_label` | The chapter as printed ("Chapter 5") — for citations. |
| `chapter_number` | The normalized chapter number ("5") — for filtering/grouping. |

### Safety
| Field | What it is / what it's for |
|---|---|
| `safety_callout` | True if a DANGER/WARNING/CAUTION is present (boosts ranking). |
| `callouts` | The callout keywords found (e.g. DANGER, WARNING) — filterable. |
| `governing_callouts` | The **full text** of the callouts (the actual safety instruction). |
| `hazard_class` | The type(s) of hazard (electrical, gas, fall…). |
| `criticality` | How critical the content is. |
| `is_prohibition` | True if this says "do NOT / never…". |
| `prohibitions` | The prohibition text. |
| `ocr_min_confidence` | Lowest OCR confidence for a number on the page. |
| `low_confidence_ocr` | True if a **number** may have been misread (safety-critical — e.g. 240 vs 440). |

### Document identity / revision
| Field | What it is / what it's for |
|---|---|
| `document_title` | The PDF's built-in Title (from its metadata). |
| `document_number` | The manual/document number. |
| `document_revision` | The revision (e.g. "Rev C"). |
| `effective_date` | When this revision took effect. |
| `document_family_id` | Groups **all revisions of the same manual** together — so "current vs old revision" can be compared within one family. |
| `is_current_revision` | True if this is the **latest** revision — the bot filters to this so it never cites a superseded manual. |
| `supersedes_revision` | Which older revision this one replaces. |

### Your taxonomy (blob metadata you set)
| Field | What it is / what it's for |
|---|---|
| `operationalarea` | Your scoping tag (e.g. Electric / Gas). |
| `functionalarea` | Your functional scoping tag. |
| `doctype` | Document type (Manual / Standard / Procedure…). |
| `title` | Your custom title metadata (the value you set on the blob). |
| `filetype` | File extension (pdf/docx…). |

### Figures
| Field | What it is / what it's for |
|---|---|
| `figure_id` | ID of the figure. |
| `figure_ref` / `figure_number` | The figure's reference/number ("Figure 18-117"). |
| `figure_title` | The figure caption/title. |
| `diagram_description` | GPT-vision's description of what the diagram shows. |
| `diagram_ocr_text` | Text/labels read from inside the image. |
| `diagram_category` | Type of diagram (circuit / wiring / schematic…). |
| `has_diagram` | True if this is a real, useful diagram. |
| `multi_page_figure` | True if the figure spans pages. |
| `figures_referenced` / `_normalized` | Figures this text points to ("see Figure X") — normalized form is the join key. |
| `figure_callouts` | Callout labels inside the figure. |
| `figure_step_linked` / `figure_linkage_confidence` | Whether the figure is tied to a procedure step, + confidence. |

### Tables
| Field | What it is / what it's for |
|---|---|
| `table_number` / `table_title` / `table_caption` | Table identity. |
| `table_columns` | The column headers. |
| `tables_referenced` / `_normalized` | Tables this text points to ("see Table X") — normalized form is the join key. |
| `table_row_cells` | The cells of a single row (for exact-value lookup). |
| `table_row_key` / `table_row_semantic_key` / `table_row_semantic_value` | Row key + a key→value pair for precise lookups. |
| `table_row_search_text` | Searchable text of a row. |
| `table_row_index` | Row position within the table. |
| `table_parent_chunk_id` | Links a row back to its parent table. |
| `table_cluster_id` | Groups the pieces/splits of one logical table. |
| `table_row_quality` / `_reason_codes` | Is this row real data vs. noise/header? |
| `table_row_is_header_like` / `_is_index_like` / `_is_placeholder_like` | Row-type flags. |
| `table_row_count` / `table_col_count` / `table_row_token_count` / `table_row_char_count` | Sizes. |
| `table_integrity_score` / `table_rows_truncated` / `table_rows_suppressed_count` / `table_row_min_confidence` | Table quality signals. |
| `table_split_index` / `table_split_count` / `table_header_rows_count` | Split/header bookkeeping for multi-page tables. |
| `table_context_path` / `table_variant_id` / `table_scope_tags` | Table context + scoping. |

### Applicability (routing / scoping)
| Field | What it is / what it's for |
|---|---|
| `applies_to_voltage` | Voltage class(es) — e.g. 12kV, 4.16kV (used to scope answers by voltage). |
| `applies_to_equipment` | Equipment this applies to. |
| `applies_to_system` | System (distribution/transmission…). |
| `applies_to_domain` | Electric / gas / etc. |
| `applies_to_phase` | Phase applicability. |

### Procedures
| Field | What it is / what it's for |
|---|---|
| `procedure_id` | Shared ID for all chunks of one procedure (used to gather the whole procedure). |
| `procedure_title` | The procedure's name. |
| `procedure_step_id` | ID of this chunk's step slice. |
| `procedure_step_order` | The step number (for ordering). |
| `procedure_step_text` | The full text of the step(s) in this chunk. |
| `procedure_step_count` | **Total** steps in the procedure — used to detect if any step is missing. |
| `procedure_branch_label` | Conditional/branch label ("if pressure > 2 psi…"). |
| `chunk_prev_id` / `chunk_next_id` | Reserved for direct previous/next-chunk links. |

### Cross-references & retrieval control
| Field | What it is / what it's for |
|---|---|
| `sections_referenced` | Sections this text points to ("see Section 5"). |
| `pages_referenced` | Pages this text points to. |
| `locator_type` / `locator_value` | Classifies/holds a locator anchor. |
| `is_locator_artifact` / `artifact_reason_codes` | Flags TOC/index/locator junk so it isn't served as an answer. |
| `retrieval_eligible` | Should the bot use this chunk as an answer? |
| `retrieval_eligible_reason` | Why it is / isn't eligible. |
| `content_class` | Content category (operational / table / figure / procedure_step / …). |
| `suggested_for_eval_question` | Flags a chunk suitable for evaluation/testing. |

### Source & housekeeping
| Field | What it is / what it's for |
|---|---|
| `source_file` | The **PDF file name** the chunk came from (e.g. "ED-DC-TPP.pdf") — shown in citations and used to group by document. |
| `source_url` | The blob URL of the PDF (to open/download it). |
| `source_path` | The full storage path of the PDF. |
| `source_hash` | Content hash of the file — detects when a PDF changed. |
| `processing_status` | Did this record process OK? (ok / precomputed / vision_error…). |
| `skill_version` | Version of the enrichment code that built it. |
| `embedding_version` | Which embedding model was used. |
| `last_indexed_at` | When it was last indexed. |
| `index_run_id` | Which indexing run produced it. |
| `chunk_token_count` | Size in tokens (for prompt budgeting). |
| `chunk_quality_score` | A quality score for the chunk. |
| `chunk_content_hash` | Hash of the chunk text (dedup). |
| `image_hash` / `image_phash` | Image hashes (figure dedup — same figure across manuals = one vision call). |
| `equipment_ids` | Equipment IDs mentioned. |
| `language` | Detected language. |
