# Safety-Critical RAG — SME / Architect Design

For a system that answers gas & electric field questions where a wrong answer can kill a lineman.
This is the design I would build as the SME/architect — grounded in the domain, the regulations, and
how other life-critical industries operate — plus the novel features specific to utility manuals.
Companion to `SAFETY_RAG_ACTION_PLAN.md`. Analysis/vision only; no code changes.

---

## 1. The reframe that changes everything

**This is not a chatbot. It is a "Procedure Authority + Verification Assistant" with a hard safety
envelope.** A chatbot's job is to be helpful and fluent. A field-safety assistant's job is to be
**correct, complete, traceable, and honest about uncertainty** — and to **refuse** rather than guess.

Corollary: **the LLM must never *author* safety content.** For anything procedural or hazardous, the
system **retrieves and presents the manual's exact, authoritative text** (extractive), with the LLM
limited to *understanding the question, routing, and formatting* — never inventing, paraphrasing, or
merging steps. Generative freedom is the enemy here; the manual is the authority.

Why: paraphrasing a lockout/tagout step, dropping a clearance value, or blending two manuals' steps is
how people die. The winning answer for "steps to fix a 3-phase transformer" is the **verbatim
procedure from the correct, current manual, complete and in order, with the page** — not a nicely
written summary.

---

## 2. Principles borrowed from other life-critical domains

| Domain | Practice | How it maps here |
|---|---|---|
| Aviation checklists | deterministic, ordered, never improvised | procedures returned verbatim & whole, never generated |
| Nuclear / process (HAZOP, FMEA) | pre-identify hazards; conservative defaults | hazard-classify chunks; fail-safe = abstain/stop |
| Medical (clinical decision support) | source-cited, version-controlled, "do no harm" | provenance + current-revision + refuse when unsure |
| High-reliability ops | two-person / independent verification | cross-check critical values via a second retrieval |
| Regulated industries | full audit trail, traceability to standard | every answer logged with source, revision, confidence |
| Human factors | avoid over-trust; communicate uncertainty | surface confidence + "verify against the manual" |

**The governing rule:** consequence asymmetry. A confident wrong answer = catastrophic; an abstention
= mild inconvenience. So the system must be **biased hard toward abstention** whenever completeness,
applicability, currency, or grounding is in doubt.

---

## 3. Regulatory / standards anchoring (why traceability is non-negotiable)

Utility field work is governed by OSHA 1910.269 (electric power generation/T&D), OSHA 1910.147
(LOTO), NFPA 70E (arc-flash), NESC, NEC, DOT/PHMSA 49 CFR 192 (gas pipeline), plus company O&M
procedures and switching/clearance rules. Implication for the system:
- Every safety answer must be **traceable to the exact source** (manual, section, page, revision,
  effective date) so it can be audited and the operator can verify.
- **Applicability and revision are legal/safety requirements**, not nice-to-haves: the *current*
  procedure for the *specific* equipment class must be what's returned.

---

## 4. The core architecture: the Safety Envelope

A hard, testable gate that **every answer must pass** before it reaches the operator. Think of it as a
circuit breaker: any check fails → the answer is blocked and replaced with a safe response.

```
Query
  → Understand (intent + entities: equipment class, voltage, gas pressure, manual/domain, hazard)
  → Route by hazard  ──►  [deterministic mode]  or  [generative-with-groundedness mode]
  → Retrieve (scoped by applicability + current revision) → rerank → threshold
  → Assemble complete unit (whole procedure / whole table)
  → ── SAFETY ENVELOPE GATE ──
        Applicable?  Current?  Complete?  Grounded?  Confident?  Not-a-prohibited-action?
        ALL pass → answer (verbatim + citations + governing warnings)
        ANY fail → ABSTAIN: cite what was found, say what's missing, escalate / point to manual
  → Log (query, sources, revision, confidence, mode, decision)  [audit trail]
```

The gate is **deterministic code**, not a prompt suggestion. It is unit-testable against the golden
safety set. This is the single most important thing to build.

---

## 5. Answer-mode routing by risk (deterministic vs generative)

Not every question is life-critical. Classify each query:
- **High-hazard / procedural** (LOTO, switching, clearances, energized work, gas purge/bleed,
  confined space, PPE/arc-flash): **deterministic mode** — retrieve and present the exact procedure/
  value verbatim with citation; **no LLM paraphrase**; warnings mandatory; strict gate; abstain freely.
- **Conceptual / informational** ("what is a recloser", "which chapter covers metering"):
  **generative-with-groundedness** — LLM may summarize, but every claim must be grounded and cited.

The hazard classification comes from the **index tags** (§7) — which is why indexing must supply them.

---

## 6. Novel / high-value features I would implement (curated)

Beyond the standard playbook (scoping, reranking, parent-doc retrieval, groundedness, abstention —
all in the action plan), these are the domain-specific moves that raise this from "good RAG" to
"safety-grade":

1. **Extractive-verbatim procedure mode.** For procedural/high-hazard answers, return the manual's
   *exact words*, complete and ordered, with the page — the LLM only formats and cites. Removes the
   "descriptive answer" failure and the paraphrase hazard in one move.

2. **Independent cross-verification for critical values** ("two-person rule" for machines). For a
   clearance, torque, PPE class, fuse/conductor rating, run a **second, independently-formulated
   retrieval** (different query + different field, e.g. BM25 on `diagram_ocr_text` vs vector on text)
   and **require the values to agree**. Disagreement → abstain and flag for SME review. This is how you
   catch an OCR digit error or a wrong-table pull *before* it reaches the operator.

3. **Applicability disambiguation dialog.** If the question's equipment/voltage/gas-class is ambiguous
   and the manuals differ by class, the assistant **asks one clarifying question** ("Overhead or
   padmount? 4 kV or 13 kV?") instead of guessing. Ambiguity is resolved by the human, never by
   similarity score. Directly kills the cross-manual contamination.

4. **Safety-notice binding (warnings travel with steps).** Every WARNING/DANGER/CAUTION is bound at
   index time to the steps in its scope, so a step is **never** retrievable without its governing
   warning, and the warning is pinned to the top of any procedure answer.

5. **Prohibition/negation as a first-class signal.** Extract "do not / never / shall not / de-energize
   before" as `prohibitions[]`/`is_prohibition`, head-load them in retrieval, and surface them — so the
   system can't answer "yes, work on it" when the manual says "do NOT."

6. **Revision governance / effective-dating.** Only current-revision content answers by default;
   superseded revisions are **quarantined** (retrievable only with explicit "show me the old rev"
   intent). `is_current_revision` + `document_family_id`. Every answer cites the revision + effective date.

7. **Query-time image re-vision fallback.** When a diagram-value question ("fuse rating on F1",
   "pole diameter") isn't answerable from indexed OCR, **re-run vision on that specific crop** with a
   targeted prompt at query time. Guarantees the small in-figure values are recoverable even if index-
   time OCR missed them.

8. **Completeness contract for procedures & tables.** A procedure/table is an **atomic object**:
   returned **whole or not at all**. The system reassembles by `procedure_id`/`table_cluster_id`,
   verifies the expected step/split count, and **abstains if it can't prove completeness** — no more
   "5 of 6 steps."

9. **Confidence-calibrated, tiered response** (not binary):
   - complete+grounded+applicable+current → **verbatim answer + citations**;
   - partial/uncertain → **"Here's what I found from [source]; I can't confirm it's complete/current/
     applicable — verify page X / ask your supervisor";**
   - none/low → **"I don't have a confident answer — consult [manual/section]; do not proceed without
     verification";**
   - prohibited/dangerous action → **hard refusal + safety statement** (already implemented).

10. **Provenance-first, "show me the source" transparency.** Every safety answer offers the **exact
    page image with the highlighted region** (this is where the bbox span-precision fix pays off) so the
    operator can verify against the manual itself. Trust through verifiability, plus an audit trail.

11. **Hazard/criticality tagging + safety-lexicon recall.** Classify every chunk (`hazard_class`,
    `criticality`) and maintain a domain **safety lexicon** (de-energize, ground, clearance, LOTO,
    tagout, arc-flash, purge, bleed-down, PPE class, MAOP) to boost recall so a safety notice is never
    ranked out.

12. **Continuous safety evaluation + red-team as a promotion gate.** A **golden safety Q&A set**
    (per manual family, expected exact answers), plus adversarial red-team questions (your ~1000
    scenarios + the known bugs), run as an automated gate: no index/agent change ships if faithfulness,
    context-completeness, applicability, or refusal-correctness regresses.

13. **Answer accountability log.** Every response stored with query, retrieved sources+revisions,
    confidence, mode, and gate decision — for incident review and regulatory audit. (In this domain,
    "why did the assistant say that?" must be answerable after the fact.)

---

## 7. What we HAVE vs what we NEED (grounded in the current index/code)

| Capability | Have today | Need |
|---|---|---|
| Record typing / content_class | ✅ | + `hazard_class`, `criticality` |
| Safety callouts | ✅ extracted (chunk-local) | bind to steps (`governing_callouts`); pin in answers |
| Prohibitions / negation | 🔴 | extract as first-class (`prohibitions`, `is_prohibition`) |
| Applicability (equip/voltage/domain) | 🔴 ~0% populated | real extraction + controlled vocabulary |
| Procedure/step model | 🔴 stubbed | ordered step objects + branches + verbatim text |
| Revision governance | 🟡 fields exist, unused | `is_current_revision`, `document_family_id`, filter+cite |
| OCR confidence | ✅ stored, unused | gate/caveat below threshold |
| Citation precision (bbox) | 🟡 imprecise | DI span→polygon exact region (verifiability) |
| Cross-verification of values | 🔴 | second-retrieval agreement check (chatbot) |
| Deterministic/extractive mode | 🔴 | verbatim procedure answering (chatbot) |
| Safety Envelope gate | 🔴 | the hard answer-gate (chatbot) |
| Abstention tiers | 🟡 hard-refusal only | full tiered/escalation policy (chatbot) |
| Applicability disambiguation dialog | 🔴 | ask-one-question routing (chatbot) |
| Query-time image re-vision | 🔴 | targeted crop re-vision (both) |
| Golden safety eval + red-team gate | 🔴 | build the set + wire as gate (both) |

Split: the **index (this repo) supplies the signals + complete units** (hazard/applicability/procedure/
revision/prohibition tags, verbatim step objects, exact citations); the **chatbot/agent runs the
Safety Envelope, deterministic mode, cross-verification, disambiguation, and abstention.**

---

## 8. What to build FIRST (so the envelope can exist)

The Safety Envelope and every chatbot-side guardrail are **blocked** until the index emits the signals.
So the indexing enablers come first, in this order (each unlocks a gate check):

1. **Applicability + hazard/criticality tagging** → enables scoping + hazard routing + the
   Applicable/hazard checks. *(Kills most cross-manual contamination.)*
2. **Procedure-step model (ordered, branches, verbatim)** → enables extractive-verbatim mode +
   the Completeness check. *(Fixes descriptive/incomplete answers.)*
3. **`is_current_revision` + `document_family_id`** → enables the Current check.
4. **Safety-notice binding + prohibition capture** → enables warnings-with-steps + the negation safety.
5. **Golden safety eval set** → so every change is measured against real, expected safe answers.

Then the chatbot team builds the **Safety Envelope gate, deterministic mode, cross-verification,
disambiguation dialog, and tiered abstention** on top of those signals.

---

## 9. The north-star statement

> For live/energized/gas work, the assistant returns the **complete, current, applicable, grounded**
> procedure or value **verbatim, with a verifiable citation and its governing warnings** — or it
> **abstains, cites what it found, and tells the operator to verify with the manual/supervisor.** It
> never invents, never paraphrases a safety step, never blends manuals, never answers across
> applicability or revision, and never presents low-confidence content as certain. Silence is safe;
> a confident wrong answer is fatal.
