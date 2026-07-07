# Safety-Critical RAG — Verified Design (v2)

Supersedes the v1 draft. This version was rewritten after a multi-agent, web-grounded
research sweep (retrieval architectures, reranking/chunking, guardrails/evaluation, multimodal,
domain/regulatory) **and an adversarial review that overturned several v1 claims.** Every major
recommendation below is either verified against primary sources or explicitly flagged as
unverified. For a life-safety system, the honesty of "what changed" matters as much as the design.

---

## 0. What changed from v1 (the corrections)

| v1 said | Verdict after research + critique | v2 position |
|---|---|---|
| "LLM must NEVER author safety content; verbatim-only" | **Over-stated.** The hazard isn't wording, it's **selection & boundary** (where a procedure starts/ends, which warning is in scope, a prerequisite 2 pages back). Verbatim also faithfully repeats an OCR "240V" with authority. | **Structured retrieval + HITL** (KEO pattern). Return verbatim *source* text for steps/values, but govern the **selection/boundary** step; allow bounded, cited synthesis for prerequisites/conditionals. Not an absolute. |
| "Independent cross-verification via a 2nd retrieval" | **Oversold.** Two retrievals over one corpus are correlated; a single-source value makes both agree → **false confidence**. | Keep only as a **cross-modal check** (text value vs `diagram_ocr_text`). The real defense against "240 vs 440" is **numeric range/plausibility validation**, not consensus retrieval. |
| "Bias HARD toward abstention; silence is safe" | **Wrong as an absolute.** Over-abstention is a documented failure — automation **disuse** (Parasuraman & Riley) + clinical **alert fatigue**: the tool gets abandoned and the operator works from **memory** (the baseline we're beating). | **Two-sided objective:** minimize confident-wrong **and** abandonment. Add a **useful-answer-rate metric + abstention budget.** A caveated, sourced partial usually beats both a wrong answer and a blank refusal. |
| "Safety Envelope = hard gate; fail any check → abstain" | **Right concept, broken as specified** — its predicates need signals that are **0%/stubbed today**, so it would abstain on ~100% of the queries the tool exists for; "Complete?" is circular (needs the procedure model to check the model). | Keep the gate, but every check **fails toward the strict/deterministic mode while preserving a useful-answer floor**; build it only *after* the enabling signals exist; use a decidable completeness proxy (numbered-step coverage) and flag, don't hard-block, when unknown. |
| "Query-time image re-vision to read a diagram value" | **Dropped.** VLMs score **<60% on engineering diagrams** (GPT-4o ~40% F1 on assembly-state) and re-vision is **nondeterministic**, breaking the audit trail. | **Retrieve-and-SHOW the figure** to the operator with the AI's best-effort reading marked "verify against the figure." Capture per-callout values at **index time** (deterministic, cached) — never assert a schematic reading as fact. |
| "Azure groundedness detection as the safety gate" | **Downgraded.** It's **preview, English-only, GPT-4o-only, and doesn't apply to agents.** | Use it as a **telemetry signal**, not a control. **HITL sign-off** is the control for high-consequence outputs (EU AI Act Art. 14 / NIST AI RMF require it). |
| Regulatory list (NEC, NFPA 70E prominent) | **Over-included.** NEC largely **exempts** utility T&D (90.2(B)); NFPA 70E applicability to utilities is **contested**. | Anchor to **OSHA 1910.269** (electric T&D), **NESC / IEEE C2** (the real clearance code), **49 CFR 192 / PHMSA + MAOP** (gas), plus **NIST AI RMF / EU AI Act Art. 14** for the AI oversight duty. |
| "Blind 1200-char chunking is just imperfect" | **It's the wrong strategy** for manuals (splits headings/steps/tables). | Replace with **structure/layout-aware + parent-child (verbatim) + table-aware** chunking (see §2). Avoid semantic (weak ROI) and propositional (fidelity risk — LLM rewrite drops "de-energize before…"). |

**Also confirmed by the skeptic (grep-verified against the code):** `applies_to_*` empty, `procedure_*`
stubbed, `ocr_min_confidence` unused, callouts chunk-local, `is_current_revision`/`document_family_id`
don't exist, prohibitions uncaptured. Diagnosis was sound; the *philosophy* was one-sided.

---

## 1. The reframe (verified)

A **decision-support assistant requiring human oversight** — **structured retrieval, not open-ended
generation** (KEO aviation-maintenance study, arXiv 2510.05524; NREL "GenAI for Grid Ops"; DOE "AI for
Energy" — all frame LLM/RAG as assist, never autonomous control). Human-in-the-loop for safety-critical
outputs is a **regulatory requirement** (EU AI Act Art. 14; NIST AI RMF). But the objective is
**two-sided**: be neither confidently wrong nor so cagey it gets abandoned.

---

## 2. The retrieval spine — "boring, proven, auditable" (this is the core)

Research consensus (5 passes): ~70% of RAG errors are retrieval; the proven stack beats the exotic one
for high-stakes, and the exotic approaches (GraphRAG/RAPTOR/HyDE/propositional) mostly retrieve from
LLM-generated summaries/rewrites that **drop safety-critical specifics and are hard to audit.**

1. **Chunking:** structure/layout-aware (headings→sections→steps→tables preserved, section path carried)
   + **parent-child / auto-merging** (child = step/paragraph for precise match; parent = whole procedure/
   section for the answer; **verbatim, no rewrite**) + **table-aware** (never split a table; keep header
   row + units; row-as-chunk as an *additional* surface, verbatim table as source of truth). Evidence:
   fixed-512 ≈ semantic in accuracy at 1/300th the cost; layout-aware is highest-ROI for manuals;
   propositional (Dense-X) rewrites at index time and can drop safety qualifiers → **disqualified**.
2. **Stage-1 retrieval:** **hybrid BM25 + vector with RRF** — BM25 protects exact tokens (part numbers,
   valve tags, torque specs, error codes) that embeddings blur. Recall here is decisive (rerankers only
   see the shortlist).
3. **Reranking:** cross-encoder — **Azure semantic ranker** (calibrated **0–4** rubric, ~158 ms/50 docs;
   caps at **top 50**; **truncates >2048 tokens** — split long steps) or Cohere/Voyage/Zerank (cloud) /
   **BGE-v2-m3 or Jina-v2 (self-host / air-gapped)**. Choose empirically on YOUR manuals, not leaderboards.
4. **Context selection:** retrieve **k≈12–20 → rerank to 6–10**. **Bias to recall** — do NOT aggressively
   threshold (a dropped chunk can be a dropped safety interlock). Prefer Azure's calibrated 0–4 rubric
   (keep ≥2) or a **listwise LLM grader tuned for recall** over raw-logit cutoffs.
5. **Agentic/multi-hop:** only surgically — **cap at ~2 iterations** (captures ~95% of the gain), log
   every query/tool call for audit. Azure now offers **agentic retrieval** (decompose→parallel→rerank→merge)
   — verify Gov availability before relying on it.
6. **Avoid as primary:** GraphRAG/RAPTOR (summary-based, audit-hard; GraphRAG's headline numbers are
   vendor, not peer-reviewed; ~$33K indexing), HyDE (hallucination-driven retrieval), query decomposition
   as a blanket (conditional benefit). GraphRAG *may* serve auxiliary "summarize across the fleet" queries.

---

## 3. Diagrams & images — retrieve-and-SHOW (verified correction)

VLM interpretation of engineering diagrams is **not reliable enough to be authoritative** (<60% on
structured/engineering diagrams; GPT-4o ~40% F1 on assembly-state). So:
- **Do not state a value *read from a schematic* as fact** ("fuse rating is 15A"). Instead **retrieve and
  display the original figure** with the AI's best-effort reading clearly marked **"verify against the
  figure."** This is the proven, safe pattern.
- Capture per-figure content at **index time** via **image-summary → text, embedded, original image kept**
  (proven industrial pattern, arXiv 2410.21943 — beats CLIP joint embeddings; unified text+image-summary
  store; multimodal ~80% vs ~60% single-modality). **Image retrieval is the weakest link — invest there.**
- Add index-time **`figure_callouts` {label, value, bbox}** so a single rating is independently rankable —
  deterministic and auditable (replaces the dropped query-time re-vision idea).
- Parser quality varies 55+ pts by doc type — **human-verify table/figure extraction on a sample** before
  trusting it in a safety pipeline.

---

## 4. Provenance is the PRIMARY safety control (the #1 anti-pattern to fix)

"A grounded answer and a confident hallucination look identical." In one audit **8/12 answers cited a
*bogus* document purely on embedding overlap — cosine similarity ≠ truth.** Therefore:
- **Every safety answer carries a resolvable, validated citation**: manual, section, page, **revision +
  effective date**, and the exact quoted text; **post-generation citation validation** (does the cited
  source exist and support the claim?).
- Render the **page image with the exact highlighted region** (this is where the **bbox span-precision fix**
  pays off — use DI word/line `span→polygon` within the chunk offsets, not paragraph-substring union).
- Citations are ~80% of perceived quality *and* the primary defense against confident hallucination.

---

## 5. The two-sided answer policy (replaces "abstain freely")

Tiered response, measured on **both** failure directions:
- **complete + grounded + applicable + current + confident** → verbatim answer + citations + governing warnings.
- **partial / uncertain** → "Here's what I found in [source, rev, page]; I can't confirm it's complete/
  current/applicable — verify page X / consult your supervisor." (usually the best real-world output.)
- **none / low** → "I don't have a confident answer — see [manual/section]; do not proceed without verification."
- **prohibited/dangerous action** → hard refusal + safety statement (already implemented).
Metrics: track **useful-answer rate + max-abstention budget + adoption**, not only wrong-answer rate.
Over-refusal → disuse → operator works from memory = a real safety failure.

---

## 6. Guardrails (layered) + injection defense + RAG red-teaming

- **Layer** (cheapest-first): fast scanner → dialog/scope rails (NeMo) → LLM → **output/citation validation
  (Guardrails AI)** → content classifier (Llama Guard 3/4). No single tool suffices; classifiers catch
  *harmful content*, **not "wrong procedure."**
- **Indirect prompt injection from scanned manuals is a real threat** — malicious/garbled PDF text can
  hijack the model. **Azure Prompt Shields (GA)** is the standout defense in an Azure stack.
- **Red-team the RAG *system*, not just the model** ("RAG LLMs are Not Safer," arXiv 2504.18041): data
  poisoning, unauthorized-doc retrieval, context-window exfiltration. Anchor to **OWASP LLM Top-10 2025
  (esp. LLM08 embedding/RAG)** + **NIST AI RMF**.

---

## 7. Evaluation IS the real gate (metric library matters less than the set)

- **The golden safety set (SME-validated expected answers + workflow-compliance checks) + a domain
  red-team suite** is the real safety asset. LLM-judge metrics (RAGAS ~0.55 human correlation) are a
  **regression tripwire, not a certification.**
- Use the **objective, ground-truth** evaluator where you can label: **Azure AI Evaluation SDK's Document
  Retrieval (qrels → NDCG/Fidelity/Holes)** — not LLM-judge-based — to tune retrieval.
- Wire it as a **promotion gate**: no index/agent change ships if the safety set or red-team regresses.
  Caveat: a curated set says nothing about the long tail — keep expanding it from real field questions.

---

## 8. Governance / audit / access (regulatory table-stakes)

Version embeddings + index snapshot + prompt as a **rollback-able unit**; **per-request retrieval-layer
RBAC/ABAC**; full **audit trail** (corpus, doc versions, retrieval results, prompts, human-review) so a
regulator can reconstruct *why* an answer was given; **HITL sign-off** on safety-critical outputs; loop
in legal/compliance on the corpus + access model before go-live.

---

## 9. Gaps v1 missed entirely (surfaced by the critic — real, add them)

- **Supersession overlay:** **safety bulletins / Temporary Operating Orders (TOOs)** override the manual and
  are what crews actually work to. "Current revision" is document-level and misses this — model a bulletin/
  TOO overlay that supersedes matching manual content.
- **The manual is authoritative-but-fallible** (errata, typos, known-bad steps) — add an erratum/feedback
  channel; verbatim mode faithfully reproduces manual errors otherwise.
- **Two current + applicable sources disagree** → surface **both** with provenance and flag the conflict;
  don't go dark on a real signal.
- **Work-order / asset / session context** → auto-scope applicability (eliminates most disambiguation prompts).
- **Offline / degraded connectivity** — field = poor signal; don't design a stack that needs cloud + double
  retrieval + re-vision to answer. Plan a degraded mode.
- **Non-English crews** — translation = paraphrase, which the safety rules restrict; design for it explicitly.
- **Unsafe-premise questions** ("confirm I can energize now") → challenge the premise, don't just retrieve.
- **Numeric range/plausibility validation** — the real defense against OCR digit errors (240 vs 440).

---

## 10. Applicability disambiguation (the one v1 idea that got *stronger*)

Rated the highest-value, lowest-cost move (mirrors crew-resource-management: "4kV or 13kV?"). Directly kills
the fatal wrong-class hazard **and** the cross-manual contamination. Requirements: (a) it needs the
**applicability tags that are 0% today** to detect genuine ambiguity; (b) **frequency-gate it** (ask only on
real ambiguity) and **feed it work-order/asset context**, or gloved/voice users learn to slam the default
and it becomes theater.

---

## 11. Corrected build order

**Indexing enablers (this repo) — supply the signals + complete, verbatim units:**
1. **Applicability + hazard/criticality tags** (real equipment/voltage/domain *classes*, not the catalog-ID
   regex that's there now) → enables scoping + hazard routing + disambiguation. (Biggest anti-contamination lever.)
2. **Structure-aware + parent-child + table-aware chunking** + **procedure-step model** (ordered, branches,
   verbatim step text) → complete, in-order procedures.
3. **Warning↔step binding** (`governing_callouts`) + **prohibition capture** + **numeric fields / low-confidence flag.**
4. **`is_current_revision` + `document_family_id`** + a **bulletin/TOO supersession** model.
5. **BBox span-precision** (word/line span→polygon) for verifiable highlights.
6. **`figure_callouts`** (per-label value+bbox) for diagram values (index-time, deterministic).

**Chatbot/agent (app team) — use the signals + enforce policy:**
scoping / 2-stage retrieval + hybrid+RRF+**rerank (calibrated threshold, recall-biased)** + **parent-merge /
whole-unit** assembly + **extractive answer with validated citations** + **retrieve-and-show figures** +
**tiered two-sided response** + layered guardrails + **Prompt Shields** + **HITL sign-off** + audit log.

**Then** build the Safety Envelope gate on top — fail-toward-strict, with a measured useful-answer floor.

**Cross-cutting first move:** the **golden safety + red-team eval set** — so every one of the above is
measured against real, expected-safe answers before it ships.

---

## 12. Sources (representative; full URLs in the research transcripts)

KEO aviation-maintenance RAG (arXiv 2510.05524) · industrial multimodal RAG (arXiv 2410.21943) · Enginuity
engineering-diagram benchmark (arXiv 2606.03410) · ColPali (arXiv 2407.01449) · "RAG LLMs are Not Safer"
(arXiv 2504.18041) · chunking benchmark (arXiv 2606.00881) · Dense-X propositions (arXiv 2312.06648) ·
Azure AI Search semantic ranker + agentic retrieval (Microsoft Learn) · Azure AI Content Safety Prompt
Shields / Groundedness (Microsoft Learn) · Azure AI Evaluation SDK RAG evaluators (Microsoft Learn) ·
Azure Architecture Center RAG guide + RAG Experiment Accelerator · RAGAS + reliability critiques
(arXiv 2602.20379, 2508.06401) · TruLens RAG Triad · OWASP LLM Top-10 2025 · NIST AI RMF · EU AI Act Art. 14
· NREL "GenAI for Power Grid Operations" · DOE "AI for Energy" · RAG anti-patterns guide (digitalapplied) ·
kapa.ai listwise context pruning · Parasuraman & Riley, "Humans and Automation: Use, Misuse, Disuse, Abuse."
Note: several vendor/blog percentages (Cohere 20-35%, DocVQA 18%, clinical 87%-vs-13%) did NOT survive
primary-source verification — treated as directional only.

---

## 13. Azure Government feasibility (decisive for what we can actually build)

Confirmed against Microsoft's dated region-support tables (July 2026). **Deploy in USGov Virginia** (only Gov
AI-Search region with availability zones). **Not USGov Texas** (no AI features).

| Capability | In Azure Gov? | Consequence |
|---|---|---|
| Vector/hybrid search, index projections, scoring profiles, filters/facets | ✅ (ships everywhere) | The whole retrieval spine is available. |
| **Semantic ranker** (L2, calibrated 0–4) + **query rewrite** | ✅ (Gov Virginia/Arizona) | Use it for rerank + the calibrated abstention threshold. |
| **Agentic retrieval** (decompose→parallel→rerank→merge) | ✅ partial GA (REST 2026-04-01; planning/synthesis still preview) | Use surgically for multi-part queries; scope each source with a filter. |
| **Index projections + filterable `parent_id`** | ✅ GA | This IS the parent-document pattern: match a chunk → `$filter parent_id eq '…'` → reassemble the whole procedure in app code (no query-time joins in Azure Search). |
| **Content Safety Groundedness Detection** | ❌ NOT in Gov (preview, English-only, GPT-4o-only anyway) | **Build groundedness as LLM-as-judge on your Gov GPT-5.1** + cite-or-abstain + rerankerScore gate; optional self-hosted NLI. |
| **Content Safety Prompt Shields / Protected Material** | ✅ GA in Gov | Use Prompt Shields for indirect injection from scanned manuals. |
| **Managed Foundry Evaluation service / Groundedness Pro / AI Agents** | ❌ NOT in Gov | Run the **`azure-ai-evaluation` SDK locally** against your Gov GPT-5.1, use the **judge-free Document Retrieval (qrels→NDCG/Fidelity/Holes)** evaluator, and/or **RAGAS pointed at the Gov endpoint** (`RAGAS_DO_NOT_TRACK=true`). |
| Azure OpenAI in Gov | ✅ FedRAMP High / IL5 | Verify GPT-5.1 is in your Gov Foundry region (catalog lags commercial). |

**Filter-first is the anti-contamination answer on this stack:** hard `$filter` on `applies_to_*` /
`manual_id` / `document_revision` is GA, cheapest, and structurally beats soft signals — but it's **only as
good as the metadata, which is 0% populated today.** So populating `applies_to_*` is the #1 lever, confirmed
independently by the Azure-feasibility and critic passes.

## 14. The grounding + abstention stack (verified, and the most important dimension)

Order of leverage (all buildable in Gov without the managed groundedness API):
1. **Pre-generation sufficient-context gate (highest leverage).** Google's result: adding *insufficient*
   context can push a model from 10% wrong to **66% wrong** (it fabricates instead of abstaining); a
   sufficient-context classifier hits **93%** with **no ground-truth answer needed** — deployable at
   inference. Check "is the retrieved context enough?" *before* generating; if not → re-retrieve or abstain.
2. **Post-generation per-claim NLI/entailment check** against the cited spans (MiniCheck ≈ GPT-4 accuracy at
   ~400× lower cost; domain-adapt it — generic NLI is weakest on **negation, quantifiers, temporal/sequence**,
   which is exactly where safety steps live). **Report the per-claim entailment rate, not the answer-level
   mean** — one contradicted safety claim among nine good ones is still a hazard.
3. **Mandatory cite-or-abstain**, and independently verify the cited span actually entails the claim
   (correctness ≠ citation faithfulness; audits found systems citing non-existent documents).
4. **External multi-signal abstention** (NOT the model's self-reported confidence — that's miscalibrated and
   "abstention can be a prompt artifact"): abstain unless `rerankerScore ≥ τ` **AND** per-claim entailment ≥ τ
   **AND** every actionable claim has a citation. **Conformal abstention** gives finite-sample error guarantees
   — best-in-class for a regulated setting.
5. **Behavioral calibration in the prompt + eval** (OpenAI "Why LMs Hallucinate"): encode the asymmetric cost
   ("answer only if >X% confident; a wrong answer costs far more than 'I don't know'"), and **evaluate on a
   set that rewards abstention** (binary 0/1 grading trains guessing).
Caveats that keep us honest: **RAG reduces but never eliminates hallucination** (purpose-built legal RAG still
hallucinated **17–33%**, Stanford); **reasoning models hallucinate *more* on grounded summarization**;
**abstention is formally unsolved and non-transferable across domains** → every threshold/detector must be
tuned and measured on OUR manuals. **Corrective-RAG (CRAG)** pattern fits: a retrieval evaluator flags
weak evidence → for a *closed* safety corpus, wire that to **abstain**, not a web fallback.

## 15. Regulatory anchoring — precise scope (corrected)

- **OSHA 1910.269** governs electric-utility T&D field work; **1910.269(c) mandates a job briefing** on the
  actual work procedures before each job; its de-energizing/grounding rules generally govern over 1910.147 for
  this work. Approach-distance/PPE values are voltage-specific and revision-controlled.
- **NESC / IEEE C2** is the utility-side clearance + arc-flash code (Rule 410) — the utility analog of 70E.
- **NFPA 70E and NEC do NOT cover utility T&D** (70E excludes utility T&D; NEC 90.2(B) exempts it) — cite them
  only for the utility's own buildings/premises, never for line/substation field tasks.
- **49 CFR 192.605** is a **literal current-revision mandate**: the operator's written O&M/emergency manual must
  be kept **at field locations** and **reviewed/updated at least every 15 months**. **Subpart N (OQ)** requires
  qualification per covered task + recognizing abnormal operating conditions. (**ANSI Z244.1 alternative LOTO
  methods are NOT OSHA-accepted** — 2024 LOI.)
- **AI-governance duties are explicit:** **NIST AI 600-1** names **Confabulation** and **Information Integrity**
  as GenAI risks and flags RAG for special attention; **EU AI Act Art. 14** requires human oversight and names
  **automation bias** in the black-letter text; **FDA CDS guidance** notes that **time-critical decisions
  generally fail the "independently review the basis" test** — directly relevant to a field tool used mid-task,
  and a strong argument for **retrieve-and-show + human authority**, not a terse verdict.

**Human-factors verdict (regulation-grounded):** "never author / extract-verbatim / abstain-when-uncertain" is
strongly supported; **"bias toward refusal" is not** — Parasuraman & Riley (disuse) and algorithm-aversion show
a high-false-alarm tool gets abandoned and the operator reverts to memory. Correct target = **calibrated
abstention with a helpful fallback** (say you can't answer, *point to where the current-revision procedure is*,
and route to the human authority). Confirmed by §5.

## 16. Retrieval-spine additions confirmed in the final passes

- **Contextual Retrieval (Anthropic):** prepend an LLM-generated one-line context to each chunk before
  embedding *and* BM25 — reported **−35% to −67% retrieval failure**. High-ROI for manuals where a bare chunk
  ("torque to 40 Nm") is meaningless without "Step 4, Pump P-101." Add it to the ingestion path.
- **Corrective-RAG (CRAG):** a lightweight retrieval-quality evaluator → weak evidence triggers **abstain** (not
  web fallback, on a closed corpus).
- **Security is part of safety:** the corpus itself is an attack surface — **RAG context can weaken guardrails**
  ("RAG Makes Guardrails Unsafe"), and OWASP LLM Top-10 2025 flags knowledge-base poisoning / embedding
  inversion / cross-boundary leakage. Enforce access control **at the retrieval layer**, add Prompt Shields.
