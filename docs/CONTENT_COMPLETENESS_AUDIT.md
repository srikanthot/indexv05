# Content Completeness Audit — Safety-Grade RAG for Technical Manuals

Purpose: technical/utility manuals are safety-critical (a wrong or partial answer can get a field
operator hurt or killed). "We handle text / tables / diagrams / images" is NOT the same as "we
captured everything." This audit enumerates **every** content type in these manuals and checks, for
each: is it **extracted**, **stored/embedded**, and **retrievable** — with the safety-critical
omissions called out first. Companion to `RETRIEVAL_QUALITY_ANALYSIS.md`. Analysis only, no code changes.

---

## 0. Reframe: it is not 4 categories, it is ~40 content types

Your four buckets (text, tables, diagrams, images) actually contain ~40 distinct content types.
The danger is **silent omission** — a content type that isn't extracted at all, or is extracted but
scattered/diluted so it can't be retrieved intact. The rest of this doc is that inventory. First,
the subset where an omission can cause physical harm.

---

## 1. SAFETY-CRITICAL gaps (fix these first) — the "operator gets hurt" class

### SC1. Safety callouts separated from the step they govern  🔴 CRITICAL
- **Scenario:** "How do I re-energize the transformer?" A `DANGER: verify de-energized and grounded
  before…` sits in the paragraph above the step. The 1200-char splitter puts the DANGER in one chunk
  and the step in the next. Retrieval returns the step chunk → the operator sees the action **without
  the danger notice**.
- **Current code:** callouts are extracted from **the chunk's own text only** (`semantic.py
  _extract_callouts`, `page_label.py` `callouts`/`safety_callout`). There is **no propagation** of a
  governing WARNING/DANGER to the other chunks/steps in its scope.
- **Fix:** (indexing) attach every in-scope safety callout to *every* step/chunk under it (scope =
  until the next callout or section break); never split a callout from its step; carry a
  `governing_callouts` field. (retrieval) always surface attached callouts with any procedure answer.

### SC2. Prohibitions / negations lost or diluted  🔴 CRITICAL
- **Scenario:** "Can I work on the line?" The manual says "do **NOT** work on this line while
  energized." Vector search retrieves the near-identical affirmative context; or chunking splits "do
  not" from the action; the LLM answers "yes, work on the line."
- **Current code:** no explicit capture of prohibition/negation. Embedding treats "do not energize"
  and "energize" as near-neighbors. No negation-preserving field or flag.
- **Fix:** extract prohibition statements as a first-class signal (`prohibitions[]` / `is_prohibition`
  flag), head-load them in the embedded string like callouts, and never chunk-split a negation from
  its verb.

### SC3. Wrong applicability — right topic, wrong equipment/voltage class  🔴 CRITICAL
- **Scenario:** operator asks about a 4kV padmounted transformer; the bot returns the **13kV overhead**
  procedure (bug 66009). Different class = different safety procedure.
- **Current code:** `applies_to_voltage` = **0% populated**, `applies_to_equipment` = ~16% on text /
  0% elsewhere; `applies_to_system` is just headers. `table_variant_id` exists but nothing routes by
  it. So applicability is effectively unenforced.
- **Fix:** (indexing) real `applies_to_equipment/voltage/phase/domain` extraction on text, table,
  diagram; (retrieval) filter/boost by the class implied in the question, and **refuse or disambiguate**
  when applicability is ambiguous rather than guessing.

### SC4. Stale / superseded revision answered as current  🔴 CRITICAL
- **Scenario:** two revisions of the same manual are indexed (or a manual references a superseded
  procedure). The bot answers from the **old** revision whose clearances/limits changed.
- **Current code (confirmed):** `document_revision` / `effective_date` are populated and **displayed**
  (`index_query_guide.py:43` SELECT only) but **no query filters or ranks by them**. There is no
  "is_current"/supersession flag.
- **Fix:** (indexing) an `is_current_revision` flag per source_file (max effective_date per
  document family); (retrieval) filter to current revision by default, and cite the revision/effective
  date in every answer.

### SC5. Value precision — OCR digit errors on safety values  🔴 CRITICAL
- **Scenario:** a scanned "440 V" reads as "240 V", or a torque "75 ft-lb" as "15 ft-lb". The bot
  answers the wrong value confidently.
- **Current code:** `ocr_min_confidence` per chunk **is** computed and stored (worst per-word
  confidence). But nothing **uses** it — no caveat, no refusal, no re-OCR below a threshold. Numbers
  aren't validated against units or ranges.
- **Fix:** (retrieval) caveat or refuse-with-citation when `ocr_min_confidence` on the answer chunk is
  low; (indexing) flag low-confidence numeric spans; consider a numeric-validation pass.

### SC6. Incomplete procedures — missing steps or branches  🔴 CRITICAL
- **Scenario:** a 6-step lockout/tagout returns only 5 steps (your bug 63078 was literally an
  incomplete checklist), or a conditional branch ("if no curb valve available, then…") is dropped, so
  the operator follows the wrong path.
- **Current code:** the procedure model is **entirely stubbed** — `procedure_id`, `procedure_step_id`,
  `procedure_step_order`, `procedure_branch_label` all empty. Steps are just prose inside 1200-char
  chunks; a step sequence can be split across chunks and only part retrieved. No mechanism guarantees
  a **complete** step set is returned.
- **Fix:** (indexing) a real procedure-step model (step id, order, sub-steps 5a/5b, branch labels)
  keeping a whole procedure reassemblable; (retrieval) expand to the full step sequence, like tables.

### SC7. Table value mis-alignment (row × column lookup)  🔴 CRITICAL
- **Scenario:** "conductor size for 200A, 4-wire, 277/480V" returns the wrong cell (bug 64009).
- **Current code:** structured rows exist, but column↔cell alignment has risks (`zip` truncation;
  re-parsing `row_text` on `:`/`;` mis-parses values containing those chars; spanned cells duplicated);
  multi-key lookup relies on BM25/vector over `row_text`, not structured per-cell filtering
  (`table_row_cells` is searchable but not filterable).
- **Fix:** (indexing) preserve exact column↔cell alignment; make per-cell values filterable; (retrieval)
  structured multi-key filtering for value lookups.

### SC8. Silent data loss at ingest (figure/table dropped, doc looks "done")  🔴 CRITICAL
- **Scenario:** a figure or table is silently dropped during preanalyze/index; the bot answers from
  text only or says "not covered," and no one knows the data never landed.
- **Current code:** preanalyze counts vision-errored figures as "not missing" (passes as
  `partial_vision`); `check_index` coverage = "has a summary record" only — it does **not** verify
  diagrams/tables/vectors landed per PDF. So a partial doc reads as DONE.
- **Fix:** (durability) coverage must assert figure+table+vector presence per PDF; count vision-errored
  as missing; regression suite with expected answers; block promotion on gaps.

### SC9. Cross-reference instructions not followed  🔴 CRITICAL
- **Scenario:** "Install the clamp per Figure 18.98" — the referenced figure (and its steps) is never
  fetched (bugs 67003/66011), so the operator gets an incomplete instruction.
- **Current code:** the figure↔text join keys are populated on both sides but the join query is
  **never executed** (latent helper only); figure→procedure linkage is a stub.
- **Fix:** (retrieval) execute the cross-ref join when a figure/table/section is named; (indexing) real
  figure↔step linkage.

> These nine are where "we must not miss an inch" literally applies. Everything below is the full
> inventory that these draw from.

---

## 2. Complete content-type inventory (extract / embed / retrieve / gap)

Legend: ✅ good · 🟡 partial/lossy · 🔴 missing or stubbed.

### A. Text / prose
| # | Content type | Extract | Embed | Retrieve | Gap |
|---|---|---|---|---|---|
| 1 | Body paragraphs | ✅ | ✅ | ✅ | chunk boundaries cut mid-paragraph (§4) |
| 2 | Section headings (h1–h3) | 🟡 | ✅ | ✅ | 11–26% missing (grounding) |
| 3 | Numbered/bulleted lists | 🟡 | ✅ | 🟡 | list structure flattened; items can split |
| 4 | Procedure steps / sub-steps | 🔴 | 🟡 | 🔴 | procedure model stubbed (SC6) |
| 5 | Conditional branches ("if…then") | 🔴 | 🟡 | 🔴 | `procedure_branch_label` empty (SC6) |
| 6 | Safety callouts (WARNING/DANGER/CAUTION/NOTE) | ✅ (chunk-local) | ✅ (head-loaded) | 🟡 | not linked to their step (SC1) |
| 7 | Prohibitions / negations | 🔴 | 🔴 | 🔴 | not captured (SC2) |
| 8 | Definitions / glossary / acronyms | 🟡 | ✅ | 🟡 | `record_subtype=glossary` heuristic only |
| 9 | Footnotes | ✅ | ✅ | 🟡 | stored; not marker-linked to body |
| 10 | Standard/citation refs (IEEE/NEC/OSHA) | 🟡 | ✅ | 🟡 | inside prose; not a first-class field |
| 11 | Cross-references (see Fig/Table/Sec/Page) | ✅ | ✅ | 🔴 | join keys present; **join not fired** (SC9) |
| 12 | Equations / formulas | 🔴 content / ✅ refs | 🟡 | 🔴 | only "Equation 4-2" ref; formula content lossy in DI markdown |
| 13 | Units & measurements | 🟡 | ✅ | 🟡 | value/unit can split; no query-time unit expansion |
| 14 | Part/model/catalog numbers | 🟡 (`equipment_ids` regex) | ✅ | 🟡 | regex partial; misses many formats |
| 15 | Wire tags / terminal IDs / ref designators | 🟡 (in diagram OCR / prose) | 🟡 | 🔴 | not a structured field |
| 16 | Revision / effective-date / doc-number | ✅ | n/a | 🔴 | not used to filter (SC4) |
| 17 | Applicability statements | 🔴 | 🔴 | 🔴 | `applies_to_*` near-empty (SC3) |
| 18 | TOC / List-of-Figures / Index (locators) | ✅ | ✅ | 🟡 | detected; not filtered out at query (locator contamination) |
| 19 | Running headers/footers, page numbers | ✅ (stripped from embed) | n/a | ✅ | printed vs physical page tracked |
| 20 | Boilerplate / legal / disclaimer | 🟡 | ✅ | 🟡 | not down-weighted |

### B. Visual (figures / images / diagrams)
| # | Content type | Extract | Embed | Retrieve | Gap |
|---|---|---|---|---|---|
| 21 | Circuit / wiring / schematic / single-line | ✅ vision desc+OCR | ✅ | 🟡 | one blob; fine-grained value diluted (§ diagram) |
| 22 | P&ID / block / control-logic | ✅ | ✅ | 🟡 | dense diagrams overflow the 3–8 sentence cap |
| 23 | Line diagrams w/ callout labels | 🟡 | 🟡 | 🔴 | per-callout values not structured (fuse-rating case) |
| 24 | Photos / equipment photos | ✅ | ✅ | ✅ | — |
| 25 | Nameplates / rating plates | ✅ (structured hint) | ✅ | 🟡 | fields in one blob, not separate |
| 26 | Exploded views / parts diagrams | ✅ | ✅ | 🟡 | callout-number→part not structured |
| 27 | Symbols / legends / keys | 🔴 | 🔴 | 🔴 | legend→symbol meaning not captured or linked to diagrams |
| 28 | Charts / graphs / plots | 🟡 (described) | ✅ | 🔴 | data points/axes not extracted |
| 29 | Maps / GIS / geographic layouts | 🟡 | ✅ | 🟡 | described as image; no geo structure |
| 30 | Icons / logos / decorative | ✅ (filtered out) | n/a | n/a | correctly skipped |

### C. Tabular
| # | Content type | Extract | Embed | Retrieve | Gap |
|---|---|---|---|---|---|
| 31 | Spec / rating tables | ✅ | ✅ | 🟡 | siblings not expanded at query (§ tables) |
| 32 | Multi-page tables | ✅ merge | ✅ | 🟡 | merge heuristic (same col count, no caption) |
| 33 | Merged-cell / multi-row-header | 🟡 fold | ✅ | 🟡 | span duplication; alignment risk |
| 34 | 2D matrix / lookup (row×col) | 🟡 | ✅ | 🔴 | multi-key value lookup weak (SC7) |
| 35 | Checklists | 🟡 (as table/text) | ✅ | 🔴 | completeness not guaranteed (SC6) |
| 36 | Forms / fill-in fields | 🔴 | 🟡 | 🔴 | field labels/values not structured |
| 37 | Tables rendered as IMAGES / DI-HTML | 🔴 | 🟡 | 🔴 | not in DI `tables[]` → neither table record nor clean text (silent hole) |

### D. Structural / metadata
| # | Content type | Extract | Embed | Retrieve | Gap |
|---|---|---|---|---|---|
| 38 | Document hierarchy / chapter-part | 🟡 headers | n/a | 🟡 | h1–h3 only; deeper nesting flattened |
| 39 | Appendices | ✅ (as sections) | ✅ | ✅ | — |
| 40 | Applicability metadata | 🔴 | — | 🔴 | (SC3) |
| 41 | Taxonomy (operational/functional/doctype) | ✅ (blob meta) | n/a | ✅ | depends on blob metadata being set |
| 42 | OCR-confidence / quality signals | ✅ | n/a | 🟡 | stored; not used to caveat (SC5) |

**Biggest silent holes (content types we capture poorly or not at all):** procedure steps (4,5),
prohibitions (7), equations content (12), wire-tag/terminal structure (15), legends/symbols (27),
chart data (28), forms (36), and **tables-that-are-images / DI-HTML tables (37)**.

---

## 3. Index schema audit — what exists vs what's missing

**Well-covered field groups today:** identity/provenance (`chunk_id`, `parent_id`, `source_*`,
`index_run_id`), record typing (`record_type`, `record_subtype`, `content_class`), text
(`chunk`, `chunk_for_semantic`, `highlight_text`), page/citation (`physical_pdf_page(s)`,
`printed_page_label`, `text_bbox`/`line_bboxes`/`chunk_bboxes`), tables (full cluster/row/cell model),
diagrams (`diagram_description`, `diagram_ocr_text`, `figure_ref`, hashes), callouts (`callouts`,
`safety_callout`, `footnotes`), refs (`figures/tables/sections/pages_referenced`), quality
(`chunk_quality_score`, `ocr_min_confidence`, `retrieval_eligible`), version (`document_revision`,
`effective_date`, `document_number`).

**Missing fields worth adding (mapped to gaps above):**
| Proposed field | Purpose / fixes |
|---|---|
| `governing_callouts` (Collection) | attach in-scope WARNING/DANGER to each step (SC1) |
| `is_prohibition` (bool) + `prohibitions` (Collection) | preserve negations (SC2) |
| `procedure_id`, `procedure_step_id`, `procedure_step_order`, `procedure_branch_label`, `procedure_step_text` | **real** step model (SC6) — currently stub |
| `figure_callouts` (Collection of {label,value,bbox}) | per-callout diagram values (fuse rating, pole dia.) |
| `applies_to_equipment/voltage/phase/domain` | populate for real (SC3) — currently ~0% |
| `is_current_revision` (bool) + `document_family_id` | supersession filter (SC4) |
| `numeric_values` (Collection) / `low_confidence_numeric` (bool) | value precision + OCR caveat (SC5) |
| `wire_tags` / `terminal_ids` / `reference_designators` (Collections) | structured schematic entities (15) |
| `equation_text` / `equations` (Collection) | formula content (12) |
| `legend_entries` (Collection {symbol,meaning}) + `legend_figure_id` | symbol legends (27) |
| `char_span_start/end` (per chunk) | exact citation offsets → bbox span fix (RETRIEVAL_QUALITY §2) |
| `chunk_prev_id` / `chunk_next_id` | neighbor expansion (context windows) |

---

## 4. Storage & chunking — where data is dropped or scattered

1. **Blind 1200-char split** cuts mid-paragraph/step/row/value — scatters an atomic unit across
   chunks, splits value from unit, splits warning from step (SC1), splits a step from its number.
   *→ structure-aware chunking; keep steps/rows/values/warnings atomic.*
2. **Sub-40-char paragraphs dropped** from the bbox/geometry path — short warnings, one-line labels
   lose their highlight.
3. **>5000-row / >200-col tables rejected**, and >5000-row tables get no row records — silent holes
   for big rating tables.
4. **DI-HTML / image tables** never enter `tables[]` and are stripped from the text embedding — a
   whole table can vanish (37).
5. **Cross-PDF phash dedup** can substitute another manual's diagram description — wrong values for
   context-dependent figures.
6. **Vision "do not guess" + 3–8 sentence cap + crop floor** drop small in-figure values (27, 23).
7. **Neighbor context not stored** — a chunk doesn't know its previous/next chunk, so an answer split
   across a chunk boundary can't be stitched.
8. **Silent-success gates** (SC8) let partial docs pass as complete.

---

## 5. Retrieval completeness

See `RETRIEVAL_QUALITY_ANALYSIS.md` — the short version: the index holds the structure, but the
chatbot doesn't yet (a) expand table clusters, (b) fire cross-ref joins, (c) filter locators/
applicability/revision, (d) target fields for value/figure lookups, (e) guarantee complete step sets.
Those are the highest-leverage, no-reindex wins.

---

## 6. Prioritized roadmap (by SAFETY impact)

**P0 — safety, do first:**
- SC8 make ingest failures loud + per-doc completeness gates (so nothing is silently missing).
- SC4 current-revision filter; SC5 OCR-confidence caveat/refuse; SC1 warning↔step attachment.
- SC3 applicability populate+route; SC6 procedure step model + complete-sequence retrieval.

**P1 — accuracy:**
- SC7 table cell alignment + multi-key lookup; SC9 cross-ref execution; §2 bbox span precision.
- Content types 7 (prohibitions), 27 (legends), 12 (equations), 37 (image/HTML tables), 23/figure callouts.

**P2 — coverage/quality:**
- structure-aware chunking; neighbor expansion; header extraction; charts/forms/wire-tags.

**Guardrail principle for safety RAG:** when the retrieved evidence is incomplete, low-confidence,
ambiguous on applicability, or from a non-current revision — the bot should **refuse with a citation
and say what it needs**, not guess. For live-line / energized work, "I don't have a confident,
current, applicable answer" is the safe output; a confident wrong answer is the fatal one.
