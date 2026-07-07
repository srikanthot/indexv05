# Safety-Grade RAG — Action Plan (100%-or-abstain)

Context from the team call: (1) the bot gives **descriptive/paraphrased** answers, but for "what are
the steps to fix a three-phase transformer" the field needs the **exact, complete steps from the
right page(s)** — nothing invented, nothing missing. (2) **Cross-context contamination**: a
"transformer" chunk from an *unrelated* manual gets retrieved and blended in → ~90-95% right, but
5-10% pulls in random/general content. (3) For gas/electric field work, **97% is a failure** — the
3% variance can kill someone. Target = 100%, and where we can't be certain, **safe abstention**, not
a confident wrong answer.

This plan is companion to `RETRIEVAL_QUALITY_ANALYSIS.md` and `CONTENT_COMPLETENESS_AUDIT.md`.

---

## 1. The two problems, and their root causes

### Problem A — cross-manual / cross-context contamination (the 5-10%)
**Why:** every chunk from every manual lives in **one flat vector space**, and retrieval is a plain
top-K similarity search with **no scoping**. A near-identical transformer chunk from manual Y scores
as high as the correct one from manual X. Compounding:
- **Applicability tags are empty** (`applies_to_voltage` 0%, `applies_to_equipment` ~16%) → retrieval
  can't restrict to the right equipment/voltage/domain.
- **No query-time filtering/routing** by manual, section, taxonomy, or applicability.
- **No re-ranker + relevance threshold** → low-relevance hits aren't dropped.
- **No abstention** → the LLM blends whatever came back.

### Problem B — descriptive instead of exact + complete
**Why:** (1) the LLM answers **abstractively** (summarizes) rather than **extractively** (returns the
actual text). (2) A 2-3 page procedure is **fragmented** by the 1200-char splitter and only *some*
fragments are retrieved, so the model paraphrases and fills gaps. (3) There is **no procedure object**
and **no whole-unit retrieval** — nothing guarantees "return ALL steps, in order, verbatim, with the page."

---

## 2. The standard everything is measured against

An answer ships **only if all five hold**; otherwise **abstain with a citation and state what's needed**:

> **Complete** (the whole procedure/table, no missing steps) · **Grounded** (every sentence supported by
> retrieved text) · **Applicable** (right equipment/voltage/domain) · **Current** (current revision) ·
> **Confident** (retrieval relevance + OCR confidence above threshold).

For live-line / energized / gas work, "I don't have a confident, current, applicable, complete answer —
here's the manual/section to check / escalate to a supervisor" is the **safe** output. A confident wrong
answer is the fatal one. This policy lives in the chatbot/agent layer but **depends on signals the index
must provide** (applicability, hazard class, revision, OCR confidence, procedure grouping).

---

## 3. What to implement — split by INDEXING (this repo) vs CHATBOT (app team)

### A. Kill cross-context contamination (precision)
**Indexing (provide the scoping signals):**
- Populate real **applicability tags**: `applies_to_equipment / voltage / phase / domain` on text,
  table, diagram (currently ~0%). This is the single biggest anti-contamination lever.
- Add a **hazard/criticality class** per chunk (see §C).
- Ensure **doc taxonomy** (`operationalarea/functionalarea/doctype`) is set on every blob.
- Add `document_family_id` + `is_current_revision`.

**Chatbot/backend (use them):**
- **Query understanding:** extract the equipment class / voltage / manual / domain from the question
  (+ conversation context) before retrieving.
- **Scoping filter / two-stage retrieval:** first route to the right manual/section (by taxonomy +
  applicability), then retrieve *within* that scope. Restrict by `source_file` / `applies_to_*` /
  `operationalarea` when the question implies them.
- **Cross-encoder re-rank + relevance threshold:** re-rank the candidate set with a stronger reranker
  and **drop hits below a score floor** (this removes the "random general" 5-10%).
- **Abstain** when the top evidence isn't confidently in-scope.

### B. Exact, complete answers (not descriptive)
**Indexing (provide complete, ordered units):**
- **Procedure-step model** (currently stubbed): `procedure_id`, `procedure_step_id`,
  `procedure_step_order`, sub-steps (5a/5b), `procedure_branch_label`, **verbatim `procedure_step_text`**
  — so a whole procedure is reassemblable and returnable in order.
- **Neighbor / parent structure:** `chunk_prev_id` / `chunk_next_id` and a section/procedure group id,
  so any fragment can pull its whole unit.
- **Structure-aware chunking:** keep a step / row / value / warning **atomic** (don't cut them).

**Chatbot/backend:**
- **Parent-document / auto-merge retrieval ("small-to-big"):** match on small precise chunks, but
  **return the whole parent section/procedure/table** for completeness.
- **Whole-unit expansion:** on any procedure/table hit, fetch all siblings (by procedure_id /
  table_cluster_id) and merge before the LLM.
- **Extractive + citation-first answering** for "what are the steps": return the **actual step text,
  in order, with the exact page** — not a paraphrase. Verbatim mode.
- **Groundedness check** before responding: verify every output sentence is supported by retrieved
  text; strip or flag anything unsupported (Azure AI Content Safety *groundedness detection*, or an
  LLM-judge pass).

### C. Safety tagging + guardrails (your "tag the critical chunks" idea — yes, do this)
**Indexing:**
- Extend `content_class` with a **hazard/criticality classifier**: e.g. `hazard_class ∈
  {live_line, energized, high_voltage, gas, confined_space, fall, none}` and a `criticality ∈
  {critical, high, normal}` per chunk. Classify from callouts + keywords + section context.
- Populate `safety_callout`, `is_prohibition`, `governing_callouts` (attach every in-scope
  WARNING/DANGER to each step under it — so a step is never retrieved without its warning).

**Chatbot/agent:**
- For **high-hazard intents**, apply the strict gate (Complete+Grounded+Applicable+Current+Confident);
  else **refuse/escalate to a human**.
- Always surface **governing warnings + prohibitions** alongside any procedure answer.
- Prefer/boost `hazard_class`/`safety_callout` chunks so a safety notice never gets ranked out.

### D. Evaluation + gates (so you can *prove* it and stop regressions)
- A **golden safety Q&A set** per manual family with expected **exact** answers (steps, values).
- Metrics: **context precision/recall** (did we retrieve the right + complete evidence?),
  **faithfulness/groundedness** (is the answer supported?), **applicability accuracy**,
  **refusal correctness** (did it correctly abstain when it should?).
- Wire it as a **promotion gate** — no index/agent change ships if the safety set regresses.

---

## 4. What the market / leading safety-RAG systems do (mapped to us)

| Technique | What it buys | Where |
|---|---|---|
| **Metadata filtering + hierarchical (2-stage) retrieval** — route to doc/section, then retrieve | kills cross-context contamination (Problem A) | chatbot + indexing tags |
| **Cross-encoder re-ranking + score threshold** | precision; drops the random 5-10% | chatbot |
| **Parent-document / auto-merging ("small-to-big") retrieval** | completeness; whole procedure, not fragments (Problem B) | chatbot + indexing structure |
| **Structure/semantic chunking** | stops splitting steps/rows/values/warnings | indexing |
| **Query rewriting / decomposition + entity routing** | multi-key + right-scope retrieval | chatbot |
| **Groundedness / faithfulness detection + abstention** | no invented/blended answers; the 100%-or-refuse policy | chatbot |
| **Extractive + citation-first answering** | exact steps, verbatim, with page | chatbot |
| **Knowledge-graph / entity linking** | cross-refs (figure↔step↔table) | both |
| **Human-in-the-loop escalation for high-hazard** | safe fallback | chatbot |
| **Continuous eval (RAGAS-style) + golden sets** | prove 100%, prevent regressions | both |

Reference frameworks people build this with: Azure AI Search semantic ranker + your own reranker,
Azure AI Content Safety **groundedness detection**, LangChain/LlamaIndex parent-document & auto-merging
retrievers, RAGAS / Azure AI evaluation for faithfulness & context precision.

---

## 5. The boundary (so triage is unambiguous)

- **Indexing (this repo) = provide SIGNALS + COMPLETE UNITS:** applicability + hazard tags, procedure
  model, parent/neighbor structure, current-revision flag, warning↔step attachment, table cell
  alignment, OCR-confidence flags, bbox precision. **Indexing alone cannot stop contamination** — but
  without these tags the chatbot has nothing to scope on.
- **Chatbot/backend = USE signals + enforce policy:** query understanding, scoping/two-stage retrieval,
  reranking + threshold, parent-merge & whole-unit expansion, extractive answering, groundedness gate,
  abstention, hazard guardrails.

Net: the 5-10% contamination and the descriptive-answer problem are **fixed mostly on the chatbot side**
— *using* tags and complete units that **this repo must supply.** Both sides move together.

---

## 6. Prioritized plan of action

**P0 — directly fixes the two complaints + the safety floor:**
1. **Applicability + hazard tagging** (indexing) → **scoping filter / 2-stage retrieval** (chatbot).
   *Kills most of the cross-manual contamination.*
2. **Procedure-step model** (indexing) → **parent-merge / whole-procedure retrieval + extractive,
   citation-first answering** (chatbot). *Exact, complete steps instead of descriptive.*
3. **Groundedness gate + abstention policy** (chatbot). *The 3% becomes safe refusal, not a wrong answer.*
4. **Current-revision flag + filter; warning↔step attachment; OCR-confidence gate** (indexing+chatbot).
5. **Golden safety eval set + promotion gate** (both). *Prove it, keep it.*

**P1 — accuracy:** cross-encoder reranker + threshold tuning; cross-ref (figure↔step) execution;
table cell alignment + cluster expansion; bbox span precision; prohibition capture.

**P2 — coverage:** structure-aware chunking; legends/equations/forms/image-tables; charts;
neighbor-context expansion.

**Guardrail restated:** for anything involving live/energized/gas work, the bot must return a
**complete, grounded, applicable, current** answer with citations, or **abstain and escalate**. Never
blend, never guess, never paraphrase a safety step.
