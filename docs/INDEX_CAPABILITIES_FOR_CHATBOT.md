# Index Capabilities & Chatbot Safety Contract

For the chatbot (frontend/backend) team. This is what the search index provides and **exactly how
the answer layer must use it** so that safety‑critical questions (live/energized electric, gas) get
**verbatim, complete, correct** answers with a **page + highlight**, or a **safe refusal** — never a
paraphrased or generalized answer. A wrong or generalized answer on a live‑line/gas question can
kill a field worker. Treat "confidently wrong" as the fatal outcome; "I don't know — check the
manual/supervisor" is the safe one.

The indexing repo SUPPLIES signals. The chatbot MUST use them. The current UAT failure
("no specific procedure, but here are general steps") is the chatbot paraphrasing and not refusing —
the index now gives everything needed to fix that.

---

## 1. Fields the index exposes, mapped to each SME requirement

### Verbatim answer — the LLM must NOT invent/paraphrase
- `chunk` — the RAW manual text, verbatim (markdown-clean, not summarized). **Answer FROM this.**
- `procedure_step_text` — the verbatim numbered steps parsed from the chunk.
- `highlight_text` — sanitized text for citation matching.
- **Rule:** EXTRACTIVE answering. Return the manual's own words. The LLM writes only the framing
  ("Per <manual>, p.<N>:") and NEVER authors the steps/values itself.

### Complete steps — no dropped middle
- `procedure_id` — a stable key shared by EVERY chunk of one procedure (even continuation/warning
  chunks). Retrieve one chunk → fetch ALL chunks with the same `procedure_id`.
- `procedure_step_order` — sort key to order the pieces.
- `procedure_step_count` — TOTAL number of steps in the whole procedure. **After assembling, compare
  the step numbers you have against this count. If you have steps 1,2,3,5 and count=6, a step is
  MISSING → do NOT present it as complete; refuse or explicitly flag the gap.**
- `procedure_title` — the procedure's heading.

### Exactly the right span (e.g. the 3 pages, nothing more)
- `physical_pdf_page`, `physical_pdf_page_end`, `physical_pdf_pages`, `printed_page_label`.
- `procedure_id` bounds the answer to that procedure's own chunks — it will NOT pull unrelated
  content. **Return only those chunks; do not append general material.**

### Right situation / no cross-manual contamination
- `applies_to_voltage` (e.g. 12.47kV, medium_voltage), `applies_to_equipment` (transformer, gas_valve…),
  `applies_to_domain` (gas/electric/substation/metering), `applies_to_phase`.
- `source_file`, `document_number`, `is_current_revision` (answer from the CURRENT revision).
- **Rule:** SCOPE-FILTER before/within retrieval. Extract the equipment/voltage/domain from the
  question and restrict to matching chunks + the right manual. This kills the "transformer chunk from
  the wrong manual" contamination.

### Safe refusal — the SME's core requirement
- `hazard_class` — {live_line, energized, high_voltage, arc_flash, gas, confined_space, fall,
  excavation, traffic, lifting, chemical}. `criticality` — {critical, high, normal}.
- **Rule (SUFFICIENT-CONTEXT + REFUSAL GATE):** if the query is hazardous (hazard_class contains
  live_line/energized/high_voltage/arc_flash/gas OR the query text is about live/energized/gas work),
  apply the STRICT gate:
  - Answer ONLY if a retrieved chunk is a **specific, grounded procedure for THIS request**
    (has a `procedure_id`, matches the equipment/voltage/task, and the steps actually answer the
    question).
  - Otherwise **REFUSE**: *"I don't have a specific procedure for <X> in <manual>. Do not act on
    general guidance — consult <section> or your supervisor."*
  - **NEVER** synthesize generalized steps for a hazardous task. No procedure found = refuse.

### Warning must travel with the step
- `governing_callouts` — the WARNING/DANGER/CAUTION text that governs the chunk's steps (bound at
  section scope, so a step never loses its warning even across a page break).
- `safety_callout` (bool), `callouts` (keywords), `is_prohibition` (bool), `prohibitions`
  (the "do NOT…" clauses).
- **Rule:** always render the governing warnings + prohibitions WITH the steps. Never show a step
  stripped of its DANGER notice.

### Highlight so the user can explore/verify
- `text_bbox` — a single tight box per page that hugs the actual chunk (the "hold box").
- `line_bboxes` — precise per-line boxes (for a precise-mode highlight).
- `chunk_bboxes`, `bbox_mode_available`, `page_width_in`, `page_height_in`, `bbox_padding_hint_in`.
- `physical_pdf_page` — which page to open.
- **Rule:** open the cited page and draw the highlight from `text_bbox` (attractive) or `line_bboxes`
  (precise). Both are indexed so the UI can switch modes.

### Distrust shaky numbers (a mis-read number can kill)
- `low_confidence_ocr` (bool) — a NUMBER on this chunk's page was OCR'd below a high confidence bar.
- `ocr_min_confidence` (float) — raw worst word confidence.
- **Rule:** if a value you're about to state sits on a `low_confidence_ocr` chunk, caveat it
  ("the manual appears to say 240V, but verify against the page — OCR confidence is low") or refuse to
  assert the exact number. Show the highlight so the human reads it.

### Figures / diagrams — SHOW, never assert
- `figure_number`, `figure_title`, `figure_callouts`, `figure_step_linked`, `diagram_description`,
  `figure_bbox`, `figures_referenced_normalized` (join key from a step chunk to its figure).
- **Rule:** for a figure value (fuse rating, dimension, wiring), **SHOW the figure** and say "verify
  against the figure." AI reads engineering diagrams wrong >40% of the time — NEVER state a diagram
  value as fact.

---

## 2. The exact recipe (for "how do I cut a live wire" and every hazardous query)

1. **Understand** the query: task (cut/de-energize), equipment (conductor/wire), hazard (live/energized).
2. **Scope-filter**: restrict to the electric domain + the applicable manual(s) via `applies_to_*` /
   `source_file` / `is_current_revision`.
3. **Retrieve** (hybrid BM25 + vector) within scope; re-rank; drop low-relevance hits.
4. **Hazard gate**: this query is live_line/energized → STRICT mode.
5. **Specific procedure?**
   - **Yes** (a grounded chunk with a `procedure_id` matching the task): expand to the WHOLE procedure
     — `$filter procedure_id eq '<id>'`, sort by `procedure_step_order`, take all chunks, verify count
     vs `procedure_step_count`. Attach `governing_callouts` + `prohibitions`. Return the steps
     **verbatim**, cite the page(s), render the highlight.
   - **No**: **REFUSE** — "No specific procedure for cutting a live conductor in <manual>. Do not
     proceed on general guidance; consult <section>/supervisor." Do NOT generate general steps.
6. **Answer** = extractive only, groundedness-checked (every sentence supported by a cited chunk),
   with warnings, page, and highlight.

---

## 3. What indexing ACHIEVED vs what the CHATBOT must do

**Indexing (supplied, live in the index):** verbatim `chunk` + `procedure_step_text`; whole-procedure
grouping (`procedure_id` + order + `procedure_step_count`); exact page + tight `text_bbox`/`line_bboxes`;
`hazard_class` + `criticality`; scoping (`applies_to_*`, `source_file`, `is_current_revision`);
warning-binding (`governing_callouts`) + `prohibitions`; `low_confidence_ocr`; figure fields.

**Chatbot (must implement — this is the fix for the UAT complaint):**
1. Extractive answering (quote the manual, never paraphrase a step/value).
2. Whole-procedure expansion via `procedure_id` + completeness check via `procedure_step_count`.
3. Sufficient-context + REFUSAL gate on hazard queries (no specific procedure → refuse, don't generalize).
4. Scope-filtering by `applies_to_*` before answering.
5. Groundedness check (reject any sentence not supported by a retrieved chunk).
6. Render highlight from `text_bbox`/`line_bboxes`; SHOW figures instead of asserting their values.
7. Honor `low_confidence_ocr` (caveat/refuse the number).

The failure the SMEs saw — "no specific procedure, but here are general steps" — is items 1 and 3 not
being done. The index now makes both straightforward.

---

## 4. Triage rule (so bugs go to the right team)
- Expected answer is NOT present in the retrieved structured chunks → **indexing/data-prep** issue.
- The structured answer IS retrievable but the bot paraphrased, dropped steps, generalized, or failed
  to refuse → **chatbot/answer-layer** issue.
The live-wire complaint is the second kind.
