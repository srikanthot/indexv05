# Retrieval Quality Analysis — Enterprise RAG for Technical Manuals

Scope: how this pipeline captures and retrieves **text, tables, diagrams, images**, where it
loses answers, and the categorized failure taxonomy behind the ~hundreds of real user
questions. Grounded in the actual code (file:line) plus known enterprise-RAG failure modes.
No code changes — analysis only.

---

## 0. The one insight that reframes everything

**Indexing (data capture) is strong. The retrieval/orchestration layer is where you lose answers.**

The index already contains, per document: page-chunked text with header paths; a fully
reassemblable table model (`table_cluster_id` + splits + per-row records + folded headers);
one diagram record per figure with a description **and** an OCR transcription; and cross-reference
join keys populated on both sides (`figures_referenced_normalized`). The 3-doc validation proved
figures/tables/rows/vectors all land.

But the **chatbot/backend does not USE most of that structure**:
- it never pulls the *sibling* chunks of a split table (so it answers from a fraction of the table),
- it never executes the text↔diagram cross-reference join (the query exists only as a reference helper),
- it never filters out locator/TOC pages or routes by applicability,
- it never targets fields (e.g. exact-token search on `diagram_ocr_text`, pinning `figure_ref`).

So **most "wrong/partial answer" bugs are retrieval-orchestration bugs, not indexing bugs.** A
separate, smaller set are genuine indexing losses (citation-bbox precision, diagram sub-region
granularity, stubbed procedure/applicability, header gaps). Getting this boundary right is the
key to bug triage: fix the retrieval layer for the big wins (no reindex), fix indexing for the
precision/coverage items.

`CHATBOT_INTEGRATION.md` already lists several of these retrieval-side items as TODOs for the app
team — they are documented, not shipped.

---

## 1. Chunking — the seed of several problems

- A "chunk" = a **SplitSkill "page": a blind ≤1200-character window over one markdown section,
  with 200-char overlap** (`skillset.json:26-46`, `textSplitMode:"pages"`, `maximumPageLength:1200`,
  `pageOverlapLength:200`). It is a **pure character-count splitter** — it does NOT respect
  paragraph/sentence/row/step boundaries; it cuts wherever 1200 chars lands.
- Consequences: values split across chunks ("240" in one, "V" in the next); a procedure step cut
  in half; a table row cut mid-cell; and — the big one — **chunk edges fall mid-paragraph**, which
  breaks the citation-highlight logic (§2).
- Section-based: long sections → many chunks; short sections → tiny chunks; the same content can
  exist both as text markdown *and* as a separate `table`/`diagram` record (dual representation).

**Fix direction (indexing):** structure-aware / semantic chunking that keeps atomic units intact
(a full step, a full row, a small table) and prefers sentence/paragraph boundaries; carry the
chunk's exact character offsets for citation (§2).

---

## 2. Citation / highlight precision — YOUR #1 COMPLAINT (root cause found)

**Symptom:** a chunk spans page-1 ¶1→¶3, but the highlight box covers only ¶2 (the middle), not
the true start and end.

**Root cause (definitive):**
1. Chunks are cut mid-paragraph by the 1200-char splitter (§1).
2. `text_bbox` is built by **substring-matching DI paragraphs against the chunk** — a 120-char
   head probe `para_norm[:120] in chunk_norm` (`page_label.py:1185`), plus a second-window guard —
   then **unioning the matched paragraphs' boxes** (`page_label.py:1196-1208`).
3. The **first and last paragraphs of the chunk are exactly the ones the cut truncated**, so their
   120-char opening isn't fully present → they **fail the probe → get dropped** → the union covers
   only interior paragraphs = the middle. Exactly the symptom.
4. `line_bboxes` and `chunk_bboxes` are the **same paragraph-union signal, renamed** — not actually
   per-line (`page_label.py:1212-1295`); DI's `pages[].lines[]` polygons are never read.
5. Sub-40-char paragraphs are silently excluded (`page_label.py:524`); markdown-vs-raw normalization
   mismatch drops even interior paragraphs; when nothing matches it falls back to a **whole-page**
   box (`_whole_page_bbox`, over-highlight), and unresolved chunks get dumped on **page 1**
   (`synthetic_default`), while genuinely multi-page chunks are **clamped to one page** (>3-page
   clamp / `bbox_corrected`), dropping the end-page highlight.

**The fix data is already in the cache, unused.** DI's `analyzeResult` (cached verbatim,
`preanalyze.py:975`) gives every **word and line** a `span` (char offset+length) **and** a
`polygon`, and the code already computes the chunk's exact character offsets in the section
(`_locate_chunk_in_section`, `page_label.py:1611`; `chunk_end`, `:1730`). The correct method:
**select every DI word/line whose `span` ∈ `[chunk_start, chunk_end]` and union their `polygon`s** →
pixel-exact box including the partial first/last words. (One caveat: DI `spans[].offset` index into
the *document-global* content, while `chunk_start` is *section-relative* — align the two bases.)

**Priority: HIGH. Indexing-side. Highest-visibility fix (it's what users see and complain about).**

---

## 3. Table retrieval completeness — "5 chunks, how do all 5 reach top-5?"

**Extraction is solid.** A big/multi-page table → oversize `table` splits (>3000 chars, header
repeated in each) + per-row `table_row` records, **all sharing one `table_cluster_id`** with
`split_index`/`split_count` ordering; multi-row headers are folded (`Voltage — 277/480`) and every
row carries headers inline (`"Voltage — 277/480: 4/0 AWG; ..."`) so a cell is answerable out of
context (`tables.py`, `process_table.py`).

**The gap is retrieval-time (this is the answer to the scenario): the siblings are NOT pulled in.**
Azure Search has no group-by-cluster/expansion; when a query matches one split/row, the other
splits (different rows, different terms) don't score into top-K → the app gets one chunk and
misses the rest. The mechanism exists — `build_table_cluster_query(table_cluster_id)` — **but only
as a reference helper in `index_query_guide.py`; nothing in the live path calls it.** The one
documented follow-up, `table_parent_chunk_id`, resolves to **split 0 only** (drops splits 1..N).

**Other table failure modes:**
- **>5000-row tables get NO row records** (silent holes for specific-row lookups); >5000-row/>200-col
  tables are rejected entirely (`tables.py:71`).
- **Column↔cell misalignment risk:** `zip(folded_headers, row_cells)` truncates to the shorter list;
  `table_columns`/`table_row_cells` are re-derived by splitting `row_text` on `;`/`:` — mis-parses any
  value containing `:`/`;`; spanned cells duplicate across columns.
- **Caption/title not in split markdown** (only column headers repeat) — splits 1..N show columns but
  not "Table 18-3" unless the app renders `table_caption`.
- **Multi-key value lookup** ("200A AND 4-wire AND 277/480V") relies on BM25/vector over the full
  `row_text`, not structured per-cell filtering (`table_row_cells` is searchable but **not filterable**).
- `retrieval_eligible` is computed but **not filtered on** by any query.

**Fix direction:** (a) RETRIEVAL — post-retrieval cluster expansion: on any `table`/`table_row` hit,
issue a second filter on `table_cluster_id` (order by `split_index`, then `row_index`) and merge
before the LLM; (b) INDEXING — make per-cell fields filterable; fix column/cell alignment; put the
caption in each split; raise/replace the 5000-row cap.

---

## 4. Diagram / image fine-grained retrieval — "fuse rating in this circuit", "pole diameter"

**How it's captured:** one record per figure crop, with **one `diagram_description` blob + one
`diagram_ocr_text` blob** (`diagram.py`). The vision prompt *does* ask to "transcribe ALL visible
text labels, part numbers, values, wire tags, terminal IDs..." — so on paper the fuse rating should
be captured. But:
- A single small value lives **inside** a 3-8-sentence blob → **vector search dilutes a 1-3 token
  value**; only exact-token BM25 on `diagram_ocr_text` reliably hits it.
- **"Do not guess"** + the 3-8 sentence cap + token limit + 10 KB crop floor mean **small leader-line
  callouts (a fuse rating, a dimension) are frequently dropped or hedged** ("value unclear").
- `build_diagram_query` (`index_query_guide.py:128`) does **not pin `figure_ref`** → on a multi-figure
  page "Figure 18.7" competes as free text; the wrong figure can outrank the target.
- **No per-callout / sub-region records** → no way to rank "the fuse callout" above the rest of the figure.
- **Cross-PDF phash dedup** can substitute a *different manual's* transcribed values for a
  visually-identical crop (`diagram.py:735-748`) — wrong fine-grained answers for context-dependent figures.
- **Diagram-only answers** (value exists only in the image, not captured at index time) are
  unrecoverable — there's no query-time re-vision of the crop.

**Fix direction:** (a) INDEXING — structured per-callout extraction (label→value pairs, or one
sub-record per callout) so a single fuse rating is independently rankable; disable cross-parent phash
for context-dependent figures; (b) RETRIEVAL — field-targeted queries that boost/pin `figure_ref` and
BM25-search `diagram_ocr_text` for exact value tokens; optional query-time image fallback.

---

## 5. Cross-references (text ↔ diagram ↔ table ↔ procedure) — keys present, joins not fired

- **Text↔diagram keys ARE populated on both sides with matched normalization** —
  `figures_referenced_normalized` on text (mined from chunk) and on diagram (from `figure_ref`),
  both via `normalize_figure_ref` ("Figure 18.117"→"18117"). Filterable both sides.
- **But the join query is LATENT** — `build_cross_ref_diagram_query` exists **only** in
  `index_query_guide.py` and is **called by nothing**. No orchestrator, on "user asks about Figure
  18.7," fetches the diagram record *and* the procedural text. It's also one-directional
  (text→diagram only); no diagram→text builder.
- `tables_referenced` / `sections_referenced` / `pages_referenced` are populated on text records but
  have **no query consumer at all** — pure unused facets.
- `figure_step_linked` / `figure_linkage_confidence` / all `procedure_*` are **STUBS**
  (`figure_step_linked = bool(figure_ref)`, confidence a constant 0.6/0.0; procedure fields empty).
  **There is no procedure-step model and no figure→step linkage** — so text→figure→procedure chains
  are broken at two links. (These are bugs 67003/66011.)

**Fix direction:** (a) RETRIEVAL — a query-time cross-ref executor that fires the join when a question
names a figure (keys already exist); (b) INDEXING — a real procedure-step model + figure↔step linkage
to replace the stubs.

---

## 6. Applicability / routing (bug 66009) — near-empty fields

- Baseline audit: `applies_to_voltage` = **0% everywhere**; `applies_to_equipment` = ~16% on text,
  0% elsewhere; `applies_to_system` is just headers. So similar tables/procedures for different
  equipment classes (overhead vs padmounted/BUD, 13kV vs 4kV, fired-gas vs generic) can't be routed —
  the wrong variant is retrieved.
- `table_variant_id` exists but there's **no query-time routing** by it.

**Fix direction:** (a) INDEXING — real `applies_to_equipment/voltage/domain` extraction for text &
diagram (not just table headers); (b) RETRIEVAL — filter/boost by applicability when the query implies
a class.

---

## 7. Locator / artifact contamination — computed but not enforced

- `content_class` / `is_locator_artifact` / `retrieval_eligible` are populated (~100%), and TOC/
  List-of-Figures/Index detection works — **but no retrieval query filters on them**. So locator pages
  can pollute value/procedure answers.

**Fix direction (RETRIEVAL, easy win):** for non-locator intents, filter `retrieval_eligible eq true`
(and/or `is_locator_artifact eq false`). Immediate quality gain, no reindex.

---

## 8. Header / section-path grounding gaps

- `header_1`/`header_2` missing on **11-26%** of records (baseline audit) → weak section context,
  ambiguous citations, worse reranking (semantic config uses headers as keyword fields).

**Fix direction (INDEXING):** improve section detection / header carry-down for header-less chunks.

---

## 9. Numeric / unit / entity precision

- Values split across chunks; units separated from magnitudes; OCR confusions ("0"/"O", "l"/"1",
  "S"/"5"); no query-time unit expansion ("277/480V" vs "277/480 volt"). `equipment_ids` regex
  extraction is partial. Table rows normalize some units (`kv`, `ma`, `degc`) but the semantic
  key/value split takes only the FIRST `Header: value` pair on multi-column rows.

**Fix direction:** numeric-aware chunking (don't split a value from its unit); query-time
normalization/synonyms; structured numeric fields where feasible.

---

## 10. Retrieval mechanics / ranking

- **One hybrid query shape for every intent** — no intent-specific field targeting, filters, or
  expansion. `build_default_query` selects everything and filters only `processing_status eq 'ok'`.
- No **query decomposition** for multi-hop / multi-key questions.
- No cross-record **dedup/MMR** — the same content as a text chunk, a table row, and a diagram can all
  appear, crowding top-K.
- The semantic (L2) reranker helps ordering but **cannot recover a chunk that never entered top-K**
  (the table-sibling and cross-ref problems).

**Fix direction (RETRIEVAL):** intent classification → per-intent query (fields, filters, expansion);
multi-query/decomposition for compound questions; MMR/dedup across record types.

---

## 11. Data-loss / silent-drop (indexing durability)

- preanalyze counts vision-errored figures as "not missing" → figures can be silently dropped; the
  coverage gate passes as `partial_vision` (being addressed in the make-failures-loud phase).
- `check_index` coverage = "has a summary record" only — it does **not** verify diagrams/tables/vectors
  landed per PDF, so partial docs read as DONE.
- `maxFailedItems` historically masked failures.
- Silent structural drops: >5000-row/>200-col table rejection; sub-40-char paragraph exclusion (bbox).

**Fix direction (Phase 2 — make failures loud):** coverage must assert figure/table/vector presence
per PDF; count vision-errored as missing; low `maxFailedItems` during validation; add promotion gates.

---

## 12. The indexing-vs-retrieval boundary (use this for bug triage)

| Failure | Fix lives in | Reindex needed? |
|---|---|---|
| Table siblings not retrieved (whole-table) | **Retrieval/chatbot** | No |
| Cross-ref (figure↔text) not fired | **Retrieval/chatbot** | No |
| Locator/TOC contamination | **Retrieval/chatbot** (filter) | No |
| Applicability routing (pick right variant) | Retrieval (filter) + Indexing (extract tags) | Tags: yes |
| Field-targeted diagram/value lookup | **Retrieval/chatbot** (partly) | No |
| Citation bbox precision (your #1) | **Indexing** | Yes |
| Diagram per-callout granularity | **Indexing** | Yes |
| Procedure-step model + figure→step link (67003/66011) | **Indexing** | Yes |
| Header extraction gaps | **Indexing** | Yes |
| applies_to extraction (66009) | **Indexing** | Yes |
| Structure-aware chunking | **Indexing** | Yes |

Triage rule (already in your spec): *if the expected data isn't in the retrieved chunks → indexing;
if it's present but the answer is still wrong → retrieval/ranking/agent.* This table tells you which
side each class lands on.

---

## 13. Prioritized roadmap

**Tier 1 — Retrieval-side quick wins (chatbot/backend team; no reindex, biggest immediate gains):**
1. Filter `retrieval_eligible eq true` for non-locator intents (kills TOC/index contamination).
2. Post-retrieval **table cluster expansion** (fetch all `table_cluster_id` siblings, order by split/row).
3. **Cross-ref executor**: when a query names a figure, fire the figure↔text join (keys already exist).
4. **Field-targeted diagram retrieval**: pin `figure_ref`, BM25 on `diagram_ocr_text` for exact tokens.
5. Cross-record **dedup/MMR** in top-K.

**Tier 2 — Indexing precision/coverage (this repo; requires reindex):**
6. **BBox span-precision fix** (your #1) — word/line polygons by DI span within the chunk offsets.
7. **Real applies_to extraction** (66009) for text + diagram.
8. **Procedure-step model + figure↔step linkage** (67003/66011) — replace the stubs.
9. **Header extraction** improvements.
10. **Per-callout diagram extraction** (structured label→value) for fine-grained value lookup.
11. **Structure-aware chunking** (don't cut steps/rows/values; keep atomic units).

**Tier 3 — Durability (this repo):**
12. Make failures loud + promotion gates (coverage asserts figures/tables/vectors; vision-errored
    counted as missing; regression query suite with expected answers).

---

## 14. Enterprise-RAG failure modes (general) → where this system sits

| Classic RAG failure | This system |
|---|---|
| Chunk-boundary breakage | Present — blind 1200-char split (§1) |
| "Lost in the middle" / partial context | Present — table siblings & cross-refs not expanded (§3,§5) |
| Table blindness | Extraction good; retrieval expansion missing (§3) |
| Figure/diagram blindness | Captured as text; fine-grained value dilution (§4) |
| Citation hallucination / imprecise highlight | Present — bbox precision (§2) |
| Multi-hop / compound questions | No decomposition (§10) |
| Applicability / version routing | Fields near-empty (§6) |
| Locator/boilerplate contamination | Detected, not filtered (§7) |
| Numeric/unit precision | Partial (§9) |
| Silent data loss at ingest | Partial gates (§11) |

**Bottom line:** the hard extraction work is largely done and correct. The highest-leverage next
moves are (1) a smarter **retrieval/orchestration layer** that actually uses the structure already in
the index (cluster expansion, cross-ref joins, eligibility filtering, field targeting) — mostly on
the chatbot side, no reindex — and (2) a focused set of **indexing precision fixes** led by the
citation-bbox span fix and the applicability/procedure extraction that close your named bugs.
