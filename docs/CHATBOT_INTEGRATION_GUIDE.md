# Chatbot Integration Guide — How to Use This Index at Its Fullest

**For:** the frontend/backend chatbot repo (and its Copilot).
**Purpose:** everything you need to build the retrieval + answer layer on this Azure AI Search index so
that safety‑critical questions (live/energized electric, gas) get **verbatim, complete, correctly‑scoped**
answers with a **page + highlight**, or a **safe refusal** — never a paraphrased "generic steps" answer.

This is the *practical* companion to the three reference docs:
- `INDEX_FIELD_REFERENCE.md` — every field, what it holds.
- `INDEX_CAPABILITIES_FOR_CHATBOT.md` — the safety contract.
- `INDEXING_ANSWERS_FOR_CHATBOT.md` — what's guaranteed vs. weak (READ THIS — it defines the limits).

If you build only from this one file, you'll get a working, safe chatbot. Use the others for depth.

---

## 0. Connect to the index

| Thing | Value |
|---|---|
| Search endpoint | `https://<your-search>.search.azure.us` (US Gov cloud — note `.us`) |
| Index name | `<artifactPrefix>-index` (find `artifactPrefix` in `deploy.config.json`; example `mm-manuals-index`) |
| Query API version | **`2024-07-01`** (GA; supports semantic ranker + integrated query vectorization). Newer preview OK if you need it. |
| Semantic config | **`mm-semantic-config`** |
| Vector field | **`text_vector`** — 1536‑dim, profile `mm-hnsw-profile`, vectorizer `aoai-vectorizer` |
| Embedding model | **`text-embedding-ada-002` (1536‑dim)** |
| Auth (backend) | Entra token for resource `https://search.azure.us` with role **Search Index Data Reader**, OR a query API key. |

### The one simplification that saves you code
**There is an integrated vectorizer on the index.** You do **NOT** have to embed the user's query
yourself. Send the raw text in a `vectorQueries` block with `"kind": "text"` and the index vectorizes it
server‑side with the matching ada‑002 model. (If you prefer to embed client‑side, you MUST use ada‑002 /
1536‑dim to match — but you don't need to.)

---

## 1. The mental model — four truths to build around

The index supplies signals; **your answer layer enforces the safety.** Build every decision on these:

1. **empty ≠ safe** — a missing `hazard_class` means "no keyword matched," NOT "assessed safe." Never let
   an empty safety tag authorize a confident answer.
2. **empty ≠ universal** — a missing `applies_to_*` means "scope unknown," NOT "applies to everything."
3. **`chunk` ≠ verbatim PDF** — `chunk` is Document Intelligence markdown. The **highlighted PDF page is
   the ground truth.** Always show it.
4. **retrieval‑found ≠ answer‑exists** — the index can't guarantee the answer is in it. **No grounded
   chunk ⇒ refuse.** Do not let the LLM fill the gap.

The UAT failure you saw ("no specific procedure, but here are general steps") is truths #1 and #4 not being
enforced. This guide fixes that.

---

## 2. Record types — route by `record_type`

Every result is one of five types. Filter/route by `record_type`:

| `record_type` | What it is | Use it for |
|---|---|---|
| `text` | a passage of manual prose (incl. procedure steps) | **primary answering** |
| `table` | a whole table as markdown | table fallback / full‑table display |
| `table_row` | one row of a table, structured | **deterministic value lookup** |
| `diagram` | a figure/diagram | **SHOW the figure — never assert its values** |
| `summary` | a per‑document summary | routing / "what's in this manual" |

---

## 3. The core retrieval query (hybrid + semantic + always‑on gates)

Use **hybrid** (keyword + vector) with the **semantic reranker**. Always apply the two safety gates in the
filter. Ask for **extractive captions/answers** so you get the manual's own highlighted words server‑side.

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

### Non‑negotiable rules for this query
- **`filter` always includes `retrieval_eligible eq true and is_current_revision eq true`.** These are the
  fail‑closed gates. *(Caveat: `is_current_revision` is set by a post‑index pass — see §9. If you find it
  empty across the board, that pass didn't run; treat currency as unconfirmed until it does.)*
- **Set `k` = 50 and `top` = 50.** The semantic ranker needs ~50 inputs or quality degrades.
- **Use `preFilter` behavior (the default here) — do NOT use post‑filtering** for the safety gates. In
  Azure vector filtering, prefer the filter on the request as shown; never a mode that can return zero
  results silently on a safety query.
- **Scope is a BOOST, not a hard filter.** Only ~35–50% of chunks carry `applies_to_*` (empty = unknown).
  Do **not** put `applies_to_voltage eq '…'` in the hard `filter` — you'll drop the ~55% unscoped real
  answers. Instead prefer scoped hits at re‑rank time and **state the scope in the answer** ("This is the
  12.47 kV procedure…"). If scope truly can't be confirmed, caveat or refuse — never silently generalize.

---

## 4. The answer pipeline (the algorithm to implement)

```
1. UNDERSTAND the query → task, equipment, voltage/pressure, hazard intent (live/energized/gas?).

2. RETRIEVE with §3 (hybrid + semantic, gates on). Drop low‑relevance hits.

3. HAZARD GATE — is this a dangerous query?
   Trigger STRICT mode if: the query is about live/energized/gas/arc‑flash work,
   OR any top hit has hazard_class ∈ {live_line, energized, high_voltage, arc_flash, gas,
   confined_space, excavation}.
   ⚠ Drive STRICT mode off the QUERY INTENT too — not only off the tag (empty ≠ safe).

4. IS THERE A SPECIFIC, GROUNDED PROCEDURE FOR *THIS* REQUEST?
   = a retrieved chunk that (a) has a procedure_id, (b) matches the task/equipment,
     (c) whose steps actually answer the question.

   ── YES ──▶  EXPAND TO THE WHOLE PROCEDURE (see §5), verify completeness,
              answer EXTRACTIVELY (see §6), attach warnings, cite page, render highlight.

   ── NO  ──▶  REFUSE (see §7). In STRICT mode you MUST refuse rather than synthesize
              general steps. This is the live‑wire fix.

5. For a VALUE lookup (rating/torque/clearance) → §8 (tables).
   For a FIGURE value (wiring/nameplate) → §8 (figures: SHOW, don't assert).

6. GROUNDEDNESS: every sentence you output must be traceable to a retrieved chunk.
   Reject/《refuse》 any sentence that isn't. The LLM writes ONLY framing
   ("Per <manual>, p.<N>:"), never the steps/values themselves.
```

---

## 5. Assemble a WHOLE procedure (complete, in order, no dropped middle)

When a hit has a `procedure_id`, fetch **all** its pieces and order them:

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
- assembled steps == count → present as complete.
- assembled < count (e.g. you have 1,2,3,5 and count = 6) → **a step is MISSING. Do NOT present as
  complete.** Either refuse, or show what you have and explicitly flag "step 4 could not be retrieved —
  read the manual page."
- `procedure_step_count` counts **top‑level numbered steps only** (not 5a/5b sub‑steps or branches). It is a
  **one‑directional gap detector**: `assembled < count` proves incomplete; it cannot prove complete.

**Caveats to handle (from the ANSWERS doc):**
- `procedure_id` groups by **section heading**, so a procedure that spans multiple headings may split into
  more than one id, and two short procedures under one heading may share one id. If the assembled set looks
  like two procedures, or the title doesn't match, fall back to showing the page.
- `chunk_prev_id` / `chunk_next_id` are **empty (reserved)** — rely on `procedure_id` + `procedure_step_order`.

---

## 6. Answer EXTRACTIVELY (this is the core anti‑paraphrase rule)

- **Quote `chunk` (or `procedure_step_text`) — do not rewrite the steps or values.** `chunk` is the manual's
  own text; `procedure_step_text` is the parsed numbered steps (re‑numbered/whitespace‑collapsed, so quote
  `chunk` when you need exact wording).
- The LLM authors **only** the framing and citations, never the procedural content.
- **Always attach the warnings with the steps:** render `governing_callouts` (the WARNING/DANGER/CAUTION
  text) and `prohibitions` (the "do not / never…" clauses) alongside the steps. Never show a step stripped
  of its danger notice. *(Binding is section‑scoped, so present them as "warnings that apply to this
  procedure," not "the warning for step 3 specifically.")*
- **Preserve modal verbs verbatim** — never soften a "shall"/"must" into "should."

---

## 7. The refusal gate (the fix for "how do I cut a live wire")

In STRICT mode, if step 4 of the pipeline finds **no specific grounded procedure**, output a refusal — do
**not** generate general steps:

> *"I don't have a specific, approved procedure for **<X>** in **<manual>**. Do not act on general
> guidance for energized/gas work — consult **<section/manual>** or your supervisor."*

Refuse (don't answer) when ANY of these hold on a hazardous query:
- no retrieved chunk has a `procedure_id` matching the task, OR
- completeness check fails (`assembled < procedure_step_count`) and you can't recover the missing step, OR
- the answer's scope can't be confirmed against the tech's voltage/equipment/domain, OR
- the only source is a figure/low‑confidence‑OCR value (see §8).

**Over‑refusal is the tolerable failure here; a confident wrong answer is not.** (But do log refusals so the
false‑refuse rate can be measured — a tech who can never get a routine spec will improvise.)

---

## 8. Values, tables, figures

### Deterministic value lookup (e.g. "50 kVA → rating")
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
  matched to the query's voltage/equipment. There is no single "variant for 13 kV" key — scope it.
- Drop rows with `table_row_quality` = `noise`.
- **If `table_rows_truncated == true`** → per‑row lookup is partial for that table. Fall back to the parent
  **`table`** record's markdown (`filter: record_type eq 'table' and table_cluster_id eq '<id>'`), and
  **gather all splits by `table_cluster_id`** (`table_split_index`/`table_split_count`) for the full table.
- **If `low_confidence_ocr == true`** on the row/chunk → a number on that page was OCR'd below the 0.90 bar.
  It flags the **chunk/page, not the specific value.** Caveat the number ("the manual appears to say 240 V —
  verify against the page") or refuse to assert it, and **show the highlight** so the human reads it.

### Figures / diagrams — SHOW, never assert
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

## 9. Highlighting — draw the box on the real PDF page

This is your ground‑truth guarantee. For any answer chunk:

- **Page to open:** `physical_pdf_page` (and `physical_pdf_page_end` for multi‑page). Show
  `printed_page_label` to the user ("p. A‑12").
- **Highlight geometry:** `text_bbox` (a single tight box per page — the "hold box"), or `line_bboxes`
  (precise per‑line boxes). Both are **JSON strings** — parse them.
- **Coordinate system:** **inches, origin top‑left.** Each entry is `{page, x_in, y_in, w_in, h_in}` where
  `page` = the physical PDF page. Scale against `page_width_in` / `page_height_in`.
- Figures use `figure_bbox`; tables use `table_bbox`; `table_row` inherits the parent `table_bbox`.
- **`summary` records have no bbox** (doc‑level). A few fragmentary text chunks fall back to a whole‑page
  box — detect by comparing box size to page size.

---

## 10. Honest limits you must code around (from `INDEXING_ANSWERS_FOR_CHATBOT.md`)

| Field / behavior | The limit | What your code must do |
|---|---|---|
| `hazard_class`, `is_prohibition`, `prohibitions` | keyword‑based, **recall unmeasured, not SME‑audited** | Never treat empty as safe. Drive the gate off query intent too. |
| `governing_callouts` | **section‑scoped**, not per‑step precise; includes NOTE/NOTICE | Show as "warnings for this procedure"; filter by signal word if you want only DANGER/WARNING/CAUTION. |
| `procedure_step_count` | top‑level numbered steps only; one‑directional | Use to detect a *dropped* step; don't claim "complete" from it alone. |
| `applies_to_*` | 35–50% populated; empty = unknown | Boost, don't hard‑filter; state scope; refuse if scope is safety‑critical and unknown. |
| `is_current_revision` | set by a **separate post‑index pass**; needs `document_number` | Confirm it ran. If empty everywhere, surface `effective_date`/`document_revision` and warn currency unconfirmed. |
| `retrieval_eligible` | requires a header → can exclude **headerless** boxed WARNINGs | Default filter on `true`; for "no answer found" on a high‑stakes query, retry without it. |
| `low_confidence_ocr` | chunk/page‑level, not the specific value | Caveat/refuse the exact number; show the highlight. |
| `chunk` | DI markdown, not byte‑verbatim PDF | Show the text AND the highlighted page; the page is authoritative. |
| **Retrieved text is untrusted** | a poisoned manual can carry injected instructions | Run Prompt Shields / data‑instruction separation on retrieved text before it hits your LLM. |
| Re‑index window | old+new can co‑exist briefly | Prefer latest `index_run_id` / `last_indexed_at`; use `is_current_revision` once the pass ran. |

---

## 11. Definition of done — test checklist for UAT

Validate the chatbot against these before you trust it:

- [ ] **Live‑wire test:** "how do I cut a live wire" with no matching procedure → **refuses**, does not emit
      general steps.
- [ ] **Verbatim test:** a known procedure → the steps match the manual **word‑for‑word** (quoted `chunk`),
      not paraphrased.
- [ ] **Completeness test:** a multi‑page procedure → **all** steps returned in order; if one is missing the
      bot says so (uses `procedure_step_count`).
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

## 12. TL;DR for the Copilot

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
