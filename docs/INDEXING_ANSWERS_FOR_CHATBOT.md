# Indexing Side — Honest Answers to the Chatbot Team's Safety Questions

Straight answers, including where the index is WEAK. Over‑claiming here could get someone hurt, so
where something isn't measured or is only partial, it says so. Read the closing section — it lists the
least‑trustworthy fields and the assumptions you'd otherwise miss.

**One framing you must internalize:** the classifiers here are **rule/keyword/regex based, not ML, and
NOT SME‑audited**. They were built recall‑over‑precision (lean toward flagging), but their true recall is
**unknown**. Therefore an **empty safety tag means "no keyword matched," NOT "assessed safe."** Never
treat empty as safe.

---

## Group 1 — Hazard / safety tag coverage

**1. Measured recall of `hazard_class`?** There is **none**. `hazard_class` comes from a keyword/regex
classifier (`classify_hazard`), not a trained model, and it has **not been SME‑audited** against a
labeled set. On the 5‑doc sample it fired on ~6–8% of text chunks. I cannot give you a recall number —
treat it as **best‑effort, unknown recall**. Design your gate so a missing tag is NOT sufficient to call
a chunk safe.

**2. Empty `hazard_class` = "no hazard" or "not assessed"?** **Not assessed.** It means the regex found
no hazard keyword. **You can never treat empty as safe.** For a live/energized/gas query, drive your
strict gate off the QUERY intent (and `hazard_class` when present), not off the absence of the tag.

**3. Record types where safety fields are systematically empty?** Yes, effectively:
- `table_row`: hazard/prohibition are computed from the row's short text → almost always empty. Don't
  rely on row‑level hazard tags.
- `diagram`: computed from OCR/description/caption/surrounding — weaker (OCR noise).
- `summary`: doc‑level; hazard reflects only what's in the 300–500‑word summary.
Rely on hazard signals at the **text** and parent **table** level; treat row/diagram/summary hazard as
best‑effort.

**4. `is_prohibition` / `prohibitions` — phrasings covered?** The regex matches: `do not`, `don't`,
`never`, `must not`, **`shall not`**, `may not`, **`under no circumstances`**, `not permitted`,
**`prohibited`**, **`forbidden`**, `is/are not allowed`, `avoid ever`. So "shall not / under no
circumstances / prohibited / forbidden" **are** caught. **Gaps:** bare **`avoid`** (only "avoid ever"
matches), implicit prohibitions phrased as positive requirements ("must be de‑energized before…"), and
anything not using those triggers. **No measured recall.** Use `prohibitions` as a strong POSITIVE
signal, but its absence doesn't mean none exist.

**5. `governing_callouts` binding scope + precision?** Binding is **section‑scoped** (the h1–h3 section):
every WARNING/DANGER/CAUTION/NOTICE/NOTE found in the section text is attached to every chunk of that
section. This is deliberately **recall‑over‑precision** — it catches a warning that sits in a different
chunk than its steps (the cross‑chunk case), but it is **NOT per‑step precise**: a warning at the end of
a section is attached to steps at the start of the same section. **Present them as "warnings that apply
in this procedure/section," not "the warning for step 3."** Also note NOTE/NOTICE are captured too (some
are non‑safety notes) — filter by keyword if you only want WARNING/DANGER/CAUTION.

---

## Group 2 — Verbatim & completeness

**6. Is `chunk` byte‑verbatim?** **No.** `chunk` is Azure Document Intelligence's **markdown rendering**
of the page — DI does its own OCR/layout, de‑columnizing, whitespace normalization, and markdown
formatting (headers `#`, pipe‑tables). It's faithful to the *content* but not byte‑identical (no original
ligatures/exact spacing). `highlight_text` is **further** processed (markdown stripped, typography
normalized, hyphenation joined) — it's for search‑matching, **not** a verbatim display string. **Neither
field is byte‑verbatim.** The **true ground truth is the PDF page itself.** So your "verbatim" guarantee
should be: **show `chunk` as the text AND render the highlight on the actual PDF page** so the tech reads
the real source. Don't claim `chunk` is byte‑identical.

**7. Is `procedure_step_text` a verbatim subset of `chunk`?** It's the step bodies **extracted and
re‑formatted** (re‑numbered "N. …", whitespace collapsed) from the chunk. Faithful content, **reformatted**
— not byte‑identical. Quote `chunk` for "exact text"; use `procedure_step_text` for structure.

**8. How is `procedure_step_count` computed? Sub‑steps/branches? Does it reflect the true total if a chunk
fails to index?** It's `len(numbered steps parsed from the whole SECTION text)`. **Good news:** it's
computed from the section text, **not from what indexed** — so if a step chunk fails to index, the count
on the OTHER chunks **still reflects the true total**, letting you detect the gap (assembled < count).
**Caveats:** it counts only **top‑level numbered steps** (1., 2., …) — **NOT lettered sub‑steps (5a/5b)
or branches** — and it's only as accurate as the regex parse (a mis‑parsed numbered list can over/undercount).
Use it as a **gap detector**, not a precise count.

**9. Chunking edge cases:**
- **A single step split across two chunks?** **Yes, possible** — the splitter is size‑based (~1200 chars,
  header‑aware but **not step‑aware**), so a long step can be cut. Assembling by `procedure_id` and
  returning full chunks recovers the text, but a step's text can span two chunks.
- **Two procedures in one chunk / one `procedure_id`?** **Yes, possible** — `procedure_id` groups by
  **section heading**, so two short procedures under one heading share one id and merge.
- **A callout in a different chunk than its steps?** **Yes** — which is exactly why `governing_callouts`
  is section‑scoped (to re‑attach it). Binding is at section granularity, not per‑step.

---

## Group 3 — Procedure assembly

**10. Does one `procedure_id` span all chunks across pages/sections?** It spans all chunks under the
**same deepest heading (h3 section)**, across pages — good. **But** if a procedure continues under a
*new* subheading, it gets a **different `procedure_id`** (split). So: one procedure within one section →
one id; a procedure spanning multiple headings → multiple ids. Group by `procedure_id`, but be aware the
boundary is the section heading, not a guaranteed true‑procedure boundary.

**11. Branches when sorted by `procedure_step_order`?** `procedure_branch_label` is just a captured
"if/when…" phrase; there is **no structured branch tree**. Sorting by step order gives **numeric order**,
which is correct for linear procedures but may **not** reflect the decision flow of a branching procedure
("if X, go to step 8"). Present steps in numeric order and surface `procedure_branch_label`, but don't
claim a coherent branch tree.

**12. `chunk_prev_id` / `chunk_next_id`?** **Confirmed empty (reserved).** Rely on `procedure_id` +
`procedure_step_order` (and `layout_ordinal` / `physical_pdf_page` for general ordering).

---

## Group 4 — Applicability vocabulary

**13. Controlled or free text? Value lists? Normalization?**
- `applies_to_domain`: **controlled** — {`gas`, `electric`, `substation`, `metering`}.
- `applies_to_phase`: **controlled** — {`single_phase`, `three_phase`}.
- `applies_to_equipment`: **controlled** (~26 classes): transformer, regulator, recloser, sectionalizer,
  capacitor, circuit_breaker, switch, fuse, relay, meter, arrester, conductor, cable, insulator, pole,
  instrument_transformer, switchgear, grounding, gas_valve, gas_regulator, gas_meter, gas_main,
  gas_service, gas_pipe, cathodic_protection, compressor.
- `applies_to_voltage`: **semi‑controlled** — exact magnitudes as extracted (`12.47kV`, `480V`) **plus**
  computed bands (`low_voltage` <1kV, `medium_voltage` 1–35kV, `high_voltage` 35–230kV,
  `extra_high_voltage` ≥230kV) **plus** named tiers (`primary`, `secondary`, `distribution`,
  `transmission`, `subtransmission`, `service`). **No reconciliation** of "12.47kV" vs "15 kV class" —
  filter by **band** for coarse scoping, exact magnitude for fine.

**14. Empty = "applies to all" or "unknown"?** **Unknown / unscoped.** A transformer chunk that doesn't
mention voltage has empty `applies_to_voltage` — that means "voltage not detected," NOT "all voltages."
**Treat empty as unscoped, never universal.**

**15. Population rate?** ~**35–50%** of text chunks are scoped (5‑doc: voltage 36%, equipment 36%,
domain 44–48%). **More than half are unscoped.** So do **not** use these as a hard exclusion filter (you'd
drop the ~55% unscoped, some of which are real answers). Use them as a **boost/preference**, and when
scoping matters for safety, fall back to broader retrieval + show the source for the human to confirm.

---

## Group 5 — Revision & retrieval eligibility

**16. `is_current_revision` reliability?** **Weak / conditional.** It is **NOT** set by the indexer — it's
populated by a **separate post‑index pass** (`scripts/mark_current_revisions.py`) that groups by normalized
`document_number` and marks the newest. **If that pass hasn't been run, the field is unset for everything.**
Two revisions could both read true only on a `document_number`+`effective_date` tie (the script picks one).
If `document_number` wasn't extracted, the doc isn't grouped → field unset. **Confirm the pass was run and
`document_number` coverage is good before you trust it.** `document_family_id` coverage = wherever
`document_number` was extractable.

**17. Does `retrieval_eligible=false` ever false‑exclude a real answer?** **Yes, it can.** For text,
eligible = `status=='ok'` AND page present AND **has a header (h1/h2/h3)**. A real answer chunk that DI
didn't assign a header (content before the first heading, odd layouts) → `false` → excluded. Default to
`retrieval_eligible eq true`, but for "no answer found" on a high‑stakes query, consider a fallback query
without the filter. Reason codes: text → `eligible_operational_content` /
`ineligible_missing_header_or_page_or_status`; table/diagram/summary have their own.

**18. Re‑index coexistence window?** **Yes, there is one.** `mergeOrUpload` doesn't delete; if content
changed (new chunk_ids) or a new revision is added, old and new can co‑exist until purged
(`reconcile.py` / `reap_stale_rows.py`). **Detect/avoid mixing** via `index_run_id` (stamped per run) and
`last_indexed_at`; prefer the latest run, and use `is_current_revision` once the pass has run. Also check
`embedding_version` if the embed model ever changes.

---

## Group 6 — Vector & semantic config

**19. `text_vector` model/dims? Integrated vectorizer?** **text‑embedding‑ada‑002, 1536‑dim.** There **is**
an **integrated vectorizer** on the index (`aoai-vectorizer`, kind `azureOpenAI`, pointing at the Foundry
`.openai.azure.us` endpoint) — so the index can **vectorize your text query server‑side**. Either send a
raw text query and let the vectorizer embed it, **or** embed client‑side with the **same model (ada‑002,
1536‑dim)** and match. Do **not** mix a different embedding model — dims and space must match.

**20. Semantic config?** **Yes: `mm-semantic-config`** (the default). title = `source_file`; content =
[`chunk_for_semantic`, `diagram_description`, `surrounding_context`]; keywords = [`header_1/2/3`,
`figure_ref`, `table_caption`, `diagram_category`, `callouts`, `record_subtype`]. Use
`semanticConfiguration=mm-semantic-config`. Note content ranks on `chunk_for_semantic` (the cleaned
embedded form), not raw `chunk`.

---

## Group 7 — Tables, figures, OCR

**21. Deterministic value lookup via `table_row_key` + `table_row_cells`?** Mostly yes: `table_row_key` =
the row's leftmost cell (usually the key, e.g. "50 kVA" — but not guaranteed to be the key), `table_row_cells`
= collision‑safe `"Header: value"` strings, `table_columns` = headers. **Units are verbatim inside the cell
string but NOT a separate field** (`table_row_units` was not built). **Picking the right variant:** there's
no clean "variant for 13kV" key — you scope by `table_scope_tags` / `applies_to_*` / `table_caption` /
`table_variant_id`. So it's **deterministic on the row, heuristic on choosing the table.** Always show the
highlight so the human can confirm the value.

**22. When `table_rows_truncated=true`, is the parent markdown complete?** The parent `table` record's
`chunk` (markdown) includes **all** rows (only the per‑row *records* are capped, not the markdown). **But**
an oversized table's markdown may be **split across multiple `table` records** (`table_split_index` /
`table_split_count`, same `table_cluster_id`). So the full table = **gather all splits by
`table_cluster_id`.** It's complete, possibly across several records.

**23. `low_confidence_ocr` threshold + granularity?** Threshold `NUMERIC_OCR_FLOOR = 0.90`. It flags the
**chunk** (boolean) when the worst **numeric** word confidence on the chunk's **pages** is below 0.90. It
does **NOT** identify which value is bad, and it's page‑scoped (a shaky number anywhere on the page flags
the chunk). One bad number among good ones → whole chunk flagged. **Mitigation: show the highlight and let
the human read the number; caveat or refuse to state an exact value on a flagged chunk.**

**24. Values only in a figure?** **Yes** — nameplate ratings and schematic values often exist only in the
image; they're OCR'd into `diagram_ocr_text` / `figure_callouts`, but **AI reads engineering diagrams wrong
>40% of the time**, so these are **SHOW‑not‑assert**. If the answer is only in a diagram: **render the
figure** (`figure_bbox` + `physical_pdf_page`) and say "the value is in Figure X — verify against the
figure." Do **not** state the OCR'd number as fact — especially if the figure's chunk is `low_confidence_ocr`.

---

## Group 8 — Highlight geometry & schema stability

**25. bbox origin/units/page? Coverage? Missing types?** Units are **inches**; origin **top‑left** (DI's
convention). Each entry is `{page, x_in, y_in, w_in, h_in}`, where `page` maps to the **physical PDF page**;
`page_width_in`/`page_height_in` give the page size. Coverage: most **text**/**table**/**diagram** records
have a bbox; **summary has none** (doc‑level, no page); a few fragmentary text chunks fall back to a
whole‑page box (detect by comparing box size to page size). `table_row` inherits the parent `table_bbox`.

**26. Is the schema frozen?** **Not yet** — it's on an active branch and I've been adding fields. For
production you should **pin your `select` list** to the fields you use (Azure Search ignores fields you
don't request, and additive new fields won't break you). **Commitment from my side: no field renames or
type/dimension changes without coordinating with you first; additions only.** There's no automated change
feed — treat `skill_version` as the schema/version marker and ping me before you go live so we freeze a
version together.

---

## Closing — what's production‑ready, what's weak, and what you'd otherwise miss

**Production‑ready (populated + reliable):** `chunk` (DI markdown), `highlight_text`, page fields,
`text_bbox`/`line_bboxes` (text), `record_type`, `source_file`, `header_1/2/3`, the table structure
(`table_row_cells`/`table_columns`/`table_row_key` + parent markdown), `figure_number`/`figure_callouts`/
`diagram_description`, and the procedure model (`procedure_id`/`step_order`/`step_text`/`step_count`)
**where procedures exist**.

**Least trustworthy right now (handle with care):**
1. **`hazard_class`, `is_prohibition`, `prohibitions`** — keyword‑based, **unmeasured, un‑audited recall.**
   *Never treat empty as safe.* This is the single most important caveat.
2. **`is_current_revision` / `document_family_id`** — depend on the post‑index pass being run and on
   `document_number` extraction. Confirm before trusting.
3. **`governing_callouts`** — section‑scoped, not per‑step precise.
4. **`low_confidence_ocr`** — chunk/page‑level, not value‑level.
5. **`applies_to_*`** — only 35–50% populated; empty = unknown, not universal; use as boost not hard filter.
6. **`table_number`, `figure_title`, `applies_to_phase`** — sparse/data‑dependent.
7. **`procedure_step_count`** — numbered top‑level steps only; misses sub‑steps/branches; parse‑dependent.

**Things a safety‑critical consumer should know (unprompted):**
- The index is **indexing‑only** — there's **no guarantee the answer exists** in it (missing‑content is a
  real failure). If retrieval returns nothing grounded, **refuse** — don't let the LLM fill the gap.
- **`chunk` is DI markdown, not the raw PDF.** The **highlighted PDF page is the ground truth** — always
  show it for a safety answer.
- **Retrieved text is untrusted** (a poisoned/booby‑trapped manual). We guard our *own* ingest LLM calls,
  but **you must run Prompt Shields / injection defense on the retrieved text you feed your LLM.**
- All of this is validated on **5 (now 46) documents**, not the full corpus — coverage %s will move; re‑run
  `validate_index_quality.py` + `verify_new_fields.py` after each big ingest.
- **Empty ≠ safe, empty ≠ universal, `chunk` ≠ verbatim‑PDF, retrieval‑found ≠ answer‑exists.** Build the
  gate around those four truths and the index will hold up its half.
