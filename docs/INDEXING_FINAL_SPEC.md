# Indexing — FINAL Spec (locked scope for the "no coming back" rebuild)

This is the definitive, code-verified list of what the **indexing side (this repo)** must add/fix before
we call it done. Every item below was confirmed against the **current** code by a line-by-line audit
(file:line evidence inline) — not from memory or an old snapshot. After these items land, the indexing
side supplies every signal and complete unit the chatbot needs; the remaining safety behavior
(scoping filter, abstention, quote-and-cite) is the separate chatbot app's job and is out of scope here.

## First: what KIND of problems are these? (the meta-question)
- **~4 are true BUGS** — code silently produces wrong/incomplete output today: table `:`/`;` delimiter
  collision, silent >5000-row drop, imprecise highlight bbox, and the "done" gate that marks a doc
  complete on a single summary record. (Plus one latent, default-OFF bug: cross-parent figure reuse.)
- **~The rest are "things we're lagging"** — capabilities the design intended but that were **never
  built**: the fields exist as empty stubs (or don't exist), so nothing is *wrong*, it's *absent*.
- **None are crashes.** The pipeline runs end to end (proven yesterday). This is about correctness,
  completeness, and safety signals — exactly the two field complaints (incomplete/descriptive answers,
  and wrong-manual contamination) plus the safety floor.

Because most items are **new index fields**, they all require a **fresh index rebuild** — which fits the
planned clean 5-manual run. Do all schema additions in one schema bump, then implement, then run 5
manuals, cross-check, then all manuals.

---

## TIER A — Safety-critical: the two fatal failure modes + anti-contamination (do first)

### A1. Whole-procedure model + step-aware chunking  *(LAGGING — biggest item)*
**Why:** guarantees "give me the steps to fix a 3-phase transformer" returns the **complete, ordered,
verbatim** procedure with its page — never a fragment the model paraphrases and fills in. Directly fixes
the "descriptive instead of exact/complete" complaint and the documented "grabs step 6, invents 1–5" bug.
**Confirmed state:** `procedure_id/procedure_step_id/procedure_step_order/procedure_branch_label` are in
the schema but written **empty everywhere** (`page_label.py:2209-2212`, `process_table.py:295-298/385-388`,
`summary.py:97-100/221-224`); **no** verbatim `procedure_step_text` field and **no** sub-step (5a/5b)
support exist at all; **no** `chunk_prev_id/chunk_next_id` (docs only); the splitter is the stock
`SplitSkill` 1200-char/200-overlap (`skillset.json:31-33`) — header-aware (h1–h3) but **step-blind**, so it
cuts mid-step; the only whole-doc record is a lossy 300–500-word GPT summary (`summary.py:4,22-30`).
**Build:** a step parser (numbered/lettered markers, if/then branches, sub-steps) that populates the
procedure fields + a new verbatim `procedure_step_text`; neighbor links; and a **whole-procedure parent
record** carrying the full ordered verbatim procedure — mirror the table-cluster pattern that already
works (`table_cluster_id`). Make the splitter snap to step boundaries so a step (and its warning) stays intact.

### A2. Warning→step binding + cross-chunk propagation  *(LAGGING — a real kill-path)*
**Why:** guarantees a WARNING/DANGER/CAUTION is **never returned without the step it governs.**
**Confirmed state:** callouts are extracted **chunk-locally** (`extract_callout_keywords(page_text)`,
`page_label.py:2090`) and emitted as a flat keyword list + a coarse `safety_callout` boolean — **text
records only** (tables/diagrams/summaries never set it). `governing_callouts`, `is_prohibition`,
cross-chunk propagation: **none exist in code** (docs only). Concrete kill-path confirmed: a WARNING at the
bottom of chunk N governing steps that continue into chunk N+1 → chunk N+1 ships `safety_callout=False`
with the warning stripped.
**Build:** `governing_callouts` field populated per step/chunk by attaching every in-scope callout —
**including across chunk boundaries**; extend `safety_callout` to table/diagram/summary records; add
`is_prohibition` (+ a "do NOT / never / prohibited" detector — today `SAFETY_CALLOUT_RE` only matches
WARNING|DANGER|CAUTION|NOTICE|NOTE, `semantic.py:52-55`).

### A3. Applicability scoping tags — voltage / equipment / domain  *(LAGGING — top anti-contamination lever)*
**Why:** the single biggest lever against pulling a transformer chunk from the **wrong manual** (the
5–10%). Gives the chatbot something to filter on before it searches.
**Confirmed state:** `applies_to_voltage` is **always `[]`** on every record type (`page_label.py:2208`,
`diagram.py:569`, `process_table.py:294/384`, `summary.py:96/220`); `applies_to_equipment` is only a
surface regex over the **text** record (`page_label.py:2206`) and **empty** on table/diagram/summary;
there is **no** domain (gas/electric/substation) field or classifier at all.
**Build:** a real voltage-class extractor (4kV/12kV/69kV/115kV, primary/secondary, distribution/
transmission), an equipment classifier (not just a tag regex) applied to **all** record types, and a new
`applies_to_domain` field + classifier.

### A4. Hazard + criticality classification  *(LAGGING)*
**Why:** lets the chatbot apply the **strict answer-or-abstain gate** only where lives are on the line
(live-line/energized/gas/confined-space/fall) and boost safety chunks so a notice is never ranked out.
**Confirmed state:** `hazard_class` and `criticality` **don't exist**; `content_class` exists but only
carries structural roles (`operational_content/table_content/figure_content/summary_content/
locator_artifact`) — never hazard (`page_label.py:2151`, etc.). Do **not** overload `content_class`.
**Build:** `hazard_class` + `criticality` fields + a classifier over voltage/energized/gas/confined-space/fall cues.

---

## TIER B — Correct values + freshness (do second)

### B1. Current-revision + document family  *(LAGGING)*
**Why:** stops the bot answering from a **superseded** revision (documented: old torque value returned as
current; stale docs out-rank current ones).
**Confirmed state:** `document_family_id` and `is_current_revision` **don't exist** (docs only); the
pipeline records raw `document_revision`/`effective_date` (`page_label.py:2191-2192`) but never computes
which is current; **no** supersession/bulletin/temporary-order overlay.
**Build:** `document_family_id` (stable key per manual across revisions) + `is_current_revision` (mark the
max effective-date/revision current). A bulletin/TOO overlay is a larger, separate model — flag it but it
can be a fast-follow.

### B2. Table integrity fixes  *(BUGS — the "wrong number" cluster)*
**Why:** guarantees a torque/clearance number is correct and attached to the right header.
**Confirmed state:** (a) **delimiter collision** — rows serialize as `"Header: value; ..."` but `_cell_text`
escapes only `|`/newline (`tables.py:42-43`), and re-parsing splits on `;`/`:` (`process_table.py:241-243`,
`table_row_quality.py:124-131`), so a value like a `3:1` ratio or `1:00` time is mis-split and re-bound to
the **wrong column**. (b) **silent >5000-row drop** — the cap is all-or-nothing `return []` with no log
(`tables.py:285-287`), and **merged multi-page clusters** (`tables.py:431`) can exceed 5000 even when each
page passed, dropping **all** per-row lookup records silently. (c) empty cells are dropped losing column
position (`tables.py:325-326`); `table_header_rows_count` hardcoded 1 (`process_table.py:283`);
`table_integrity_score` a constant 0.95 (`process_table.py:195`).
**Build:** carry the grid's structured per-row cells (with explicit empties/positions) instead of
re-parsing the string; escape/avoid `:`/`;` collision; on oversized tables **log + marker + paginate**
rather than drop-all; real header-count and integrity score.

### B3. OCR-confidence flags actually consumed  *(BUG-ish / LAGGING)*
**Why:** lets the chatbot distrust a shaky OCR'd number (B→8, l→1).
**Confirmed state:** `ocr_min_confidence` is computed and stored on **text** records but **never consumed**
by anything (`page_label.py:543-550,2108,2190`; retrieval gate ignores it, `:2146-2150`); **table/table_row
records carry no OCR confidence at all** (only stubs like `figure_linkage_confidence=0.0`).
**Build:** a `low_confidence_ocr` boolean on text records from the existing value; propagate word-level DI
confidence into table cells (spatial join — cells and words share page bboxes) and emit a per-row
low-confidence flag.

### B4. Exact-highlight bbox  *(BUG — the user's #1 complaint)*
**Why:** highlight must sit tight on the actual chunk text, not a whole paragraph.
**Confirmed state:** bbox is built by a **120-char paragraph substring probe** and unions the **paragraph**
polygon (`page_label.py:1185-1208`, candidates from paragraph `boundingRegions` only, `:527-530`). The
exact **word-level `span`+`polygon` already sit in the cached DI `analyzeResult`** (`preanalyze.py:975`) but
are read only for confidence, never for the box (`page_label.py:544-545`).
**Build:** resolve each chunk's character range to DI word/line spans and union their polygons (already cached).

### B5. Diagram per-callout capture  *(LAGGING)*
**Why:** so a fuse rating / pole depth / dimension is captured as a discrete value, and we know which step a
figure belongs to — while the chatbot still **shows the figure** (VLMs <60% on eng. diagrams, so never
assert a schematic value as fact).
**Confirmed state:** vision emits prose `description` + a flat pipe-joined `ocr_text` blob — **no**
label→value structure; `figure_step_linked = bool(figure_ref)` is a stub (`diagram.py:570`),
`figure_linkage_confidence` a constant; `figure_callouts` **doesn't exist** (docs only).
**Build:** a `figure_callouts` field (`{label, value, bbox}` collection) from a structured vision prompt; a
real `figure_step_linked`/`linkage_confidence` from actual step↔figure_ref matching.

### B6. Taxonomy derived in-pipeline  *(PARTIAL → close it)*
**Why:** so `operationalarea/functionalarea/doctype` are reliable scoping keys even when nobody hand-tagged the blob.
**Confirmed state:** fields are wired but populated **only** from blob user-metadata via a HEAD request;
default to `None` when absent (`process_document.py:83-96`, `di_client.py:254-267`) — the pipeline never
derives them.
**Build:** derive from cover page / document_number / path as a fallback when blob metadata is missing.

---

## TIER C — Pipeline integrity + security (do third; small but important)

### C1. Durability "done" gate by artifact manifest  *(BUG)*
**Why:** stop marking a document complete when figures/tables/rows/vectors silently didn't land.
**Confirmed state:** the index-side done check filters `record_type eq 'summary'` and treats a PDF as done
if **one summary record exists** (`auto_heal.py:70-100`); the preanalyze skip accepts `partial_vision` as
done (`preanalyze.py:824-859`).
**Build:** a per-document manifest of expected vs landed artifact counts (figures/tables/rows/vectors);
don't count `partial_vision` as complete; loud warning on any shortfall.

### C2. Ingest prompt-injection guard  *(LAGGING — security)*
**Why:** a booby-trapped scanned page (EchoLeak-class; 5 docs → ~90% control in research) could corrupt what
we store, since we run LLMs over page text/images at load time.
**Confirmed state:** vision/summary/diagram calls send raw document text/images with **no** delimiting or
untrusted-content instruction (`diagram.py:379-391/71-87`, `preanalyze.py:210-217/71`, `summary.py:140-142/22-30`);
the only "injection" handling in the repo is unrelated OData/markdown escaping.
**Build:** wrap document-derived text in explicit untrusted-content delimiters + a system instruction to
never follow instructions found inside the document/OCR; treat OCR as data only.

### C3. Cross-parent figure reuse scoping  *(latent BUG, default OFF — lowest priority)*
**Why:** prevents a figure/caption from a **different manual** being attached by perceptual-hash match.
**Confirmed state:** the phash lookup has **no parent scoping** (`search_cache.py:236-240`) and copies a
different doc's description/figure_ref on hit (`diagram.py:741-747`) — but it's gated off by default
(`SEARCH_CACHE_CROSS_PARENT`, `search_cache.py:225`).
**Build:** keep it disabled, or if ever enabled, scope reuse to context-independent figures (e.g.
nameplates) and verify with a strict `phash_distance` threshold.

---

## Locked implementation order
1. **One schema bump** adding all new fields (procedure_step_text, chunk_prev/next_id, whole-procedure
   parent record type, governing_callouts, is_prohibition, hazard_class, criticality, applies_to_domain,
   document_family_id, is_current_revision, figure_callouts, low_confidence flags).
2. **A1 → A2 → A3 → A4** (safety floor + anti-contamination).
3. **B1–B6** (values + freshness + highlight + diagrams + taxonomy).
4. **C1 → C2 → C3** (durability + security).
5. **Run 5 manuals → cross-check against a golden safety Q&A set → then all manuals.**

## The boundary (so nobody expects indexing to do the chatbot's job)
Indexing **supplies signals + complete units**: applicability/hazard tags, whole-procedure records,
warning↔step binding, current-revision flags, table integrity, OCR-confidence flags, exact bbox,
figure callouts, durability, injection-safe ingest. The chatbot app **uses** them: scope/filter to the
right manual, whole-unit retrieval, extractive quote-and-cite, groundedness gate + safe abstention,
show-the-figure, Prompt Shields. Both must move together; this repo's half is the 13 items above.
