# Index Field Reference (Data Dictionary) — for the Chatbot Team

The complete list of fields in the search index (144 total), generated from the live schema. For
each: **type** · **attributes** (search=full-text searchable, filter=`$filter`able, facet=facetable,
sort=sortable; all are retrievable unless noted) · **what it holds** · **how the chatbot uses it**.

**Record types** (`record_type`): `text`, `table`, `table_row`, `diagram`, `summary`. A field is only
populated on the record types where it makes sense (noted below). Vector field `text_vector` (1536‑dim)
is searchable but not retrievable.

Legend for "chatbot use": 🟢 = core signal you should use · ⚪ = supporting/optional · 🔧 = internal
plumbing (usually ignore).

---

## A. Identity & record type
| field | type | attrs | holds / use |
|---|---|---|---|
| `id` | String | search | 🔧 index key (auto). |
| `chunk_id` | String | search,filter,sort | 🔧 stable per-record id. |
| `record_type` | String | filter,facet | 🟢 text / table / table_row / diagram / summary — filter/route by this. |
| `record_subtype` | String | filter,facet | ⚪ e.g. `glossary`. |
| `content_class` | String | filter,facet | ⚪ operational_content / table_content / figure_content / summary_content / locator_artifact. |
| `parent_id` / `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `tbl_row_parent_id` / `sum_parent_id` | String | filter | 🔧 document/record grouping keys (Search projection). |

## B. The actual answer content
| field | type | attrs | holds / use |
|---|---|---|---|
| `chunk` | String | search | 🟢 **the RAW, verbatim manual text.** Answer FROM this — quote it, don't paraphrase. |
| `procedure_step_text` | String | search | 🟢 verbatim numbered steps parsed from the chunk (procedure chunks). |
| `chunk_for_semantic` | String | search (not retrievable) | 🔧 the embedded form (headers+refs+callouts+clean text). |
| `highlight_text` | String | search | 🟢 sanitized text for citation matching / PDF text-layer search. |
| `surrounding_context` | String | search (not retrievable) | ⚪ body context around a figure (embedding aid). |
| `footnotes` | [String] | search | ⚪ footnote text on the chunk's pages. |
| `text_vector` | [Single] | search only | 🔧 1536‑dim embedding (ada‑002); used by vector search + the query vectorizer. |

## C. Highlighting / citation geometry (open the page + draw the box)
| field | type | attrs | holds / use |
|---|---|---|---|
| `physical_pdf_page` | Int32 | filter,sort | 🟢 the physical PDF page to open. |
| `physical_pdf_page_end` | Int32 | filter | 🟢 last page (multi-page chunk). |
| `physical_pdf_pages` | [Int32] | filter,facet | 🟢 all pages the chunk touches. |
| `printed_page_label` / `_end` | String | search,filter | 🟢 the printed page number ("A‑12"). |
| `printed_page_label_is_synthetic` | Boolean | filter,facet | ⚪ true if we synthesized the label. |
| `text_bbox` | String(JSON) | — | 🟢 **single tight box per page that hugs the chunk** ("hold box" highlight). |
| `line_bboxes` | String(JSON) | — | 🟢 precise per-line boxes (precise-mode highlight). |
| `chunk_bboxes` | String(JSON) | — | ⚪ per-page union of line boxes. |
| `bbox_mode_available` | [String] | filter,facet | ⚪ which modes exist ("chunk","line"). |
| `figure_bbox` / `table_bbox` | String(JSON) | — | 🟢 figure / table region box. |
| `page_width_in` / `page_height_in` / `bbox_padding_hint_in` | Double | — | ⚪ page dims + pad for rendering. |
| `bbox_version` | String | filter,facet | 🔧 "2.1.0" (line-level precision). |
| `page_resolution_method` | String | filter,facet | ⚪ how the page was resolved. |
| `pdf_total_pages` | Int32 | filter | ⚪ total pages in the PDF. |

## D. Safety signals (the life-critical fields)
| field | type | attrs | holds / use |
|---|---|---|---|
| `hazard_class` | [String] | search,filter,facet | 🟢 {live_line, energized, high_voltage, arc_flash, gas, confined_space, fall, excavation, traffic, lifting, chemical}. **Trigger the strict answer-or-refuse gate when present.** |
| `criticality` | String | filter,facet | 🟢 critical / high / normal. |
| `governing_callouts` | [String] | search,filter,facet | 🟢 the WARNING/DANGER/CAUTION text governing this chunk's steps (bound at section scope). **Always show with the steps.** |
| `safety_callout` | Boolean | filter,facet | 🟢 chunk has a safety callout. |
| `callouts` | [String] | search,filter,facet | ⚪ callout keywords (WARNING/DANGER…) for badges/boost. |
| `is_prohibition` | Boolean | filter,facet | 🟢 chunk contains a "do NOT" prohibition. |
| `prohibitions` | [String] | search | 🟢 the verbatim "do not / never…" clauses. |
| `low_confidence_ocr` | Boolean | filter,facet | 🟢 **a NUMBER on this chunk was OCR'd below a high bar — distrust/caveat any value here.** |
| `ocr_min_confidence` | Double | filter,sort | ⚪ raw worst word confidence (0‑1). |

## E. Procedure model (whole, complete, in order)
| field | type | attrs | holds / use |
|---|---|---|---|
| `procedure_id` | String | search,filter,facet | 🟢 **barcode shared by every chunk of one procedure.** Expand: `$filter procedure_id eq '…'`. |
| `procedure_step_order` | Int32 | filter,sort | 🟢 sort key for the pieces. |
| `procedure_step_count` | Int32 | filter,sort | 🟢 **TOTAL steps in the procedure — verify you assembled all of them (detect a dropped step).** |
| `procedure_title` | String | search,filter | 🟢 the procedure heading. |
| `procedure_step_id` | String | search,filter | ⚪ this chunk's step anchor. |
| `procedure_branch_label` | String | search,filter,facet | ⚪ "if/when…" conditional branch text. |
| `chunk_prev_id` / `chunk_next_id` | String | filter | ⚪ neighbor links (reserved; use procedure_id + order for now). |

## F. Applicability / scoping (answer from the RIGHT context)
| field | type | attrs | holds / use |
|---|---|---|---|
| `applies_to_voltage` | [String] | search,filter,facet | 🟢 "12.47kV","medium_voltage","distribution"… scope by voltage. |
| `applies_to_equipment` | [String] | search,filter,facet | 🟢 classes: transformer, recloser, gas_valve, gas_meter, cable… |
| `applies_to_domain` | [String] | search,filter,facet | 🟢 gas / electric / substation / metering. |
| `applies_to_phase` | [String] | search,filter,facet | ⚪ single_phase / three_phase. |
| `applies_to_system` | [String] | search,filter,facet | ⚪ header-derived system tags. |
| `equipment_ids` | [String] | search,filter,facet | ⚪ raw equipment tag strings ("GE‑THQL‑1120") for exact-match lookup. |

## G. Revision & provenance (answer from the CURRENT manual)
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

## H. Table model (deterministic value lookup — bug‑64009 class)
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
| `table_rows_truncated` | Boolean | filter,facet | 🟢 **the table exceeded the row cap — per-row lookup is partial for this table; fall back to the parent `table` markdown.** |
| `table_rows_suppressed_count` | Int32 | filter,sort | ⚪ how many rows were suppressed. |
| `table_row_min_confidence` | Double | filter,sort | ⚪ (reserved) per-row OCR confidence. |
| `table_ref` / `tables_referenced` | String / [String] | search,filter(,facet) | 🟢 table references from a text chunk (join text→table). |

## I. Figure / diagram model (SHOW, never assert)
| field | type | attrs | holds / use |
|---|---|---|---|
| `diagram_description` | String | search | 🟢 dense description of the figure (for retrieval; not authoritative for values). |
| `figure_callouts` | [String] | search | 🟢 discrete OCR tokens on the figure (labels/values) — a search aid, NOT ground truth. |
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

## J. Cross-references (link a step to its figure/table/section)
| field | type | attrs | holds / use |
|---|---|---|---|
| `sections_referenced` | [String] | search,filter,facet | ⚪ "Section 4.2" refs in the chunk. |
| `pages_referenced` | [String] | search,filter | ⚪ page refs in the chunk. |

## K. Locator artifacts (suppress TOC/index for value/procedure asks)
| field | type | attrs | holds / use |
|---|---|---|---|
| `is_locator_artifact` | Boolean | filter,facet | 🟢 true for TOC / list-of-figures / index pages — **suppress for value/procedure queries.** |
| `locator_type` | String | filter,facet | ⚪ toc / list_of_figures / index. |
| `locator_value` | String | search,filter | ⚪ the locator entry. |
| `artifact_reason_codes` | [String] | search,filter,facet | ⚪ why it was tagged locator. |

## L. Retrieval control
| field | type | attrs | holds / use |
|---|---|---|---|
| `retrieval_eligible` | Boolean | filter,facet | 🟢 **filter to `true`** — excludes TOC/low-signal chunks. |
| `retrieval_eligible_reason` | String | search,filter,facet | ⚪ why eligible/ineligible. |
| `chunk_quality_score` | Double | filter,sort | ⚪ tie-breaker score. |
| `suggested_for_eval_question` | Boolean | filter,facet | ⚪ good chunk for eval-set generation. |
| `header_1` / `header_2` / `header_3` | String | search,filter,facet | 🟢 section path (great for scoping + display). |
| `layout_ordinal` | Int32 | filter,sort | ⚪ section order in the doc. |

## M. Taxonomy & ops
| field | type | attrs | holds / use |
|---|---|---|---|
| `operationalarea` / `functionalarea` / `doctype` | String | search,filter,facet | 🟢 taxonomy for routing (from blob metadata or derived). |
| `filetype` | String | filter,facet | ⚪ pdf/docx… |
| `language` | String | filter,facet | ⚪ en/es/fr. |
| `processing_status` | String | filter,facet | 🔧 ok / partial_* — data-quality signal. |
| `skill_version` / `embedding_version` / `index_run_id` / `last_indexed_at` | String/Date | filter(,facet,sort) | 🔧 versioning/ops. |
| `chunk_token_count` | Int32 | filter,sort | ⚪ token budget math. |
| `chunk_content_hash` | String | filter | 🔧 re-embed gate. |

---

## The short list — the fields the chatbot MUST use for the SME requirements
- **Verbatim / complete:** `chunk`, `procedure_step_text`, `procedure_id`, `procedure_step_order`, `procedure_step_count`.
- **Right context:** `applies_to_voltage/equipment/domain/phase`, `source_file`, `is_current_revision`, `retrieval_eligible`.
- **Refuse safely:** `hazard_class`, `criticality` (trigger strict gate; no matching `procedure_id` ⇒ refuse).
- **Warnings/prohibitions:** `governing_callouts`, `prohibitions`, `is_prohibition`, `safety_callout`.
- **Highlight:** `text_bbox`, `line_bboxes`, `physical_pdf_page`, `printed_page_label`.
- **Numbers/tables/figures:** `low_confidence_ocr`, `table_row_key`/`table_columns`/`table_row_cells`/`table_variant_id`, `figure_number`/`figure_callouts` (SHOW, don't assert).

If the chatbot Copilot finds a capability it needs that isn't here, that's a genuine indexing gap —
list it back and we add it.
