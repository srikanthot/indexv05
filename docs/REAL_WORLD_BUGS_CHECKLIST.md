# Real-World Bugs of Manual/Safety Chatbots — and Where WE Stand

Documented, real failures of RAG/document chatbots (named public incidents + engineering
postmortems + peer-reviewed studies), each mapped to our own system: **will we hit it? is it
already covered in our indexing, or is it a gap? what do we do (our storage side vs the chatbot
app side)?** Sources inline. Companion to the four design/analysis docs.

Plain-language note: "our storage side" = this repo (how we load, chunk, tag, and index the
manuals). "chatbot app side" = the separate app that searches and writes the answer (not in this repo).

---

## The catalog (real bug → how they fixed it → does it apply to us → our status → what we'd do)

### 1. Answers from the WRONG manual / wrong equipment (your cross-contamination)
- **Real:** Navy ship troubleshooting assistant (Fincantieri) — the correct procedure was in the top‑4
  results only **41%** of the time (19% for vague questions); *similar procedures for different
  breakdowns interfered.* Aircraft MRO — retrieved a *similar but wrong* task ("landing‑gear
  inspection" vs "retraction test"), a compliance violation. ([2601.08706](https://arxiv.org/html/2601.08706), [2511.15383](https://arxiv.org/pdf/2511.15383))
- **How fixed:** match on **exact procedure/task codes**, **filter by equipment model**, and **always
  return the source** so the human verifies before acting; group all text for one issue together.
- **Us:** YES, high (the 5–10% you saw). **Status: partial + weak** — we have `source_file` but the
  **equipment/voltage tags are empty**, so nothing can scope. **Do:** (storage) fill real
  equipment/voltage/domain tags; (chatbot) filter to the right manual/class before searching.

### 2. Makes up a policy/procedure that contradicts the manual (confident hallucination)
- **Real:** **Air Canada** — bot invented a refund policy; court held the airline **liable**, ordered
  to pay. **Cursor "Sam"** — invented a login policy → mass cancellations. **NYC MyCity** — told
  businesses they could do **illegal** things. **Legal AI (Westlaw/Lexis)** — invented law **17–33%**,
  incl. a **judge who never existed**. ([CBC](https://www.cbc.ca/news/canada/british-columbia/air-canada-chatbot-lawsuit-1.7116416), [Register/Cursor](https://www.theregister.com/2025/04/18/cursor_ai_support_bot_lies/), [Stanford HAI](https://hai.stanford.edu/news/ai-trial-legal-models-hallucinate-1-out-6-or-more-benchmarking-queries))
- **How fixed:** force answers to **quote the real source**, **check citations**, keep a **human in the loop**; you own what the bot says.
- **Us:** YES. **Status: chatbot‑side** (no answering happens in our storage). **Do:** (chatbot) quote‑and‑cite, verify the citation actually supports the claim, human sign‑off on safety answers. Our storage already provides the source/page/revision to cite.

### 3. Incomplete procedure — grabs part of the steps and invents the rest
- **Real:** documented — the bot retrieves "step 6" **without steps 1–5 and fabricates the missing
  ones**; conditional branches dropped. ([TDS chunking postmortem](https://towardsdatascience.com/your-chunks-failed-your-rag-in-production/), [KEO 2510.05524](https://arxiv.org/pdf/2510.05524))
- **How fixed:** **keep the whole procedure together** — structure‑aware chunking that keeps steps +
  warnings + prerequisites in one unit; parent‑child retrieval that returns the full procedure.
- **Us:** YES, critical (your bug 63078). **Status: gap** — procedures are chopped by size; the
  procedure model is empty. **Do:** (storage) store a procedure as one whole ordered unit; (chatbot)
  return the whole procedure, not fragments.

### 4. Cites a source that doesn't exist / citation doesn't match the claim
- **Real:** Stanford legal study (cited a **fictional judge**); *Mata v. Avianca* (lawyers sanctioned
  for AI‑invented cases). ([2405.20362](https://arxiv.org/pdf/2405.20362))
- **How fixed:** track **citation‑matches‑content** as its own check; block answers whose citation
  doesn't actually support the claim.
- **Us:** YES. **Status: chatbot‑side.** **Do:** (chatbot) verify each cited page actually supports the answer.

### 5. Safety WARNING separated from its step
- **Real:** documented — a **drug‑interaction warning got split from the prescription**, so the answer
  gave the action without the warning. ([chunking guides](https://medium.com/@ThinkingLoop/rag-chunking-9-strategies-that-stop-lost-context-b4777df4c908))
- **How fixed:** structure‑aware chunking that **keeps a warning attached to the step it governs.**
- **Us:** YES, critical. **Status: gap** — we only pull warnings from a chunk's own text; no
  attachment to steps. **Do:** (storage) bind every WARNING/DANGER to the steps under it; (chatbot)
  always show the warning with the procedure.

### 6. Wrong table value (value from the header, wrong column, OCR digit swap)
- **Real:** documented — a value pulled from a **header instead of the cell**; **B→8, l→1, O→0** OCR
  swaps; a flattened table gave arithmetically wrong answers. ([OptyxStack](https://optyxstack.com/rag-reliability/why-your-rag-fails-on-pdf-tables-ocr-header-loss-row-boundary-fixes), [TDS](https://towardsdatascience.com/your-chunks-failed-your-rag-in-production/))
- **How fixed:** keep the **column header attached to every value**, re‑render each row as a sentence,
  **score OCR confidence** and route low‑confidence cells for review, treat tables as atomic.
- **Us:** YES (your bug 64009). **Status: partial** — we do row‑as‑sentence + folded headers, **but**
  cells can mis‑align (a `:`/`;` in a value breaks parsing), tables over **~5,000 rows are silently
  dropped**, and we compute an OCR‑confidence score but **never use it**. **Do:** (storage) fix
  alignment, raise the row cap, flag low‑confidence numbers; (chatbot) sanity‑check the number against
  its unit/range.

### 7. Diagram misread (fuse rating, dimension, wrong connection)
- **Real:** VLMs **misread quantitative details** on engineering diagrams and even **hallucinated a
  connection** to the wrong component; they score **<60%** on engineering diagrams. ([Enginuity 2606.03410](https://arxiv.org/html/2606.03410), [ChatP&ID 2603.22528](https://arxiv.org/pdf/2603.22528))
- **How fixed:** **don't trust the AI's reading of a schematic** — structure it first, and **show the
  operator the actual figure** to read the value themselves.
- **Us:** YES. **Status: partial** — we describe + OCR each figure, but a small value is diluted and
  small callouts get dropped; no protection against a misread. **Do:** (storage) capture per‑callout
  label→value; (chatbot) **show the figure** marked "verify against the figure," never assert a
  schematic value as fact.

### 8. Stale / superseded revision answered as current
- **Real:** documented — a return policy changed **30→15 days** but the bot kept saying 30; a
  **superseded torque value** returned as current; **stale docs out‑rank current ones** (older = longer,
  richer, more weight). ([why RAG fails](https://medium.com/@tommyadeliyi/why-most-rag-systems-fail-in-production-and-how-to-fix-them-82cde6782b50))
- **How fixed:** version + **expiry dates** + **replace‑on‑reingest** + supersession metadata; surface the revision in the answer.
- **Us:** YES, critical. **Status: partial** — we extract the revision date but the chatbot **never
  filters by it**; no "is this current?" flag; no **safety‑bulletin** overlay. **Do:** (storage) add an
  is‑current flag + a bulletin/temporary‑order overlay; (chatbot) default to current revision + cite it.

### 9. Booby‑trapped / poisoned manual (indirect prompt injection)
- **Real:** **EchoLeak (CVE‑2025‑32711, severity 9.3)** — a **zero‑click** attack: a crafted email/doc,
  once retrieved, silently exfiltrated internal data from M365 Copilot. Research: **5 crafted documents
  → ~90% control** of answers. **Chevrolet** dealer bot talked into "selling" a Tahoe for **$1**. ([EchoLeak](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes-microsoft-365-copilot.html), [PoisonedRAG 2402.07867](https://arxiv.org/pdf/2402.07867))
- **How fixed:** treat ingested content as **untrusted** (sanitize, delimit), injection filters
  (Azure **Prompt Shields**), source vetting.
- **Us:** YES — and note **we run AI over scanned pages at load time with zero protection**, so a
  poisoned page could corrupt what we store. **Status: gap.** **Do:** (storage) guard our vision/summary
  AI calls against adversarial page text; (chatbot) Prompt Shields on retrieved text.

### 10. Exact‑highlight is wrong (your #1 complaint)
- **Real:** general chunk‑boundary failures; our own root‑cause analysis.
- **Us:** YES. **Status: gap** — the highlight covers the middle of a chunk, not the true start/end.
  **Do:** (storage) use the exact word positions already sitting in our cache to draw the precise box. Reindex.

### 11. Silent data loss at load (a figure/table dropped, but the doc looks "done")
- **Real:** the general "observability gap" — teams can't tell which stage dropped the answer.
- **Us:** YES. **Status: gap** — our "done" check only looks for a summary record, not that figures/
  tables/searchable‑vectors all landed. **Do:** (storage) checks that assert everything landed per doc.

### 12. Over‑refusal → operators abandon the tool
- **Real:** documented — guardrails default to **defensive refusal**; users **abandon and work from
  memory**; **algorithm aversion** (one wrong refusal and they distrust it). ([distrust 2307.13601](https://arxiv.org/pdf/2307.13601))
- **How fixed:** **calibrate** — show sources/confidence, answer the answerable, track the refusal rate.
- **Us:** YES. **Status: chatbot‑side.** **Do:** (chatbot) two‑sided policy — don't be confidently
  wrong, but don't be so cagey it gets abandoned; give a caveated, sourced partial + "verify page X."

### (also) Cross‑user data leak (ACL bypass)
- **Real:** an intern's query returns the **CFO's board deck** — vector search ignores permissions. ([Oso](https://www.osohq.com/post/right-approach-to-authorization-in-rag))
- **Us:** only relevant if some manuals are access‑restricted; **chatbot‑side** (enforce permissions at
  search time). Likely low priority if all field crews may see all manuals — but confirm.

---

## The canonical checklist to test against — "7 Failure Points of RAG" (Barnett et al., 2024)
[arXiv 2401.05856](https://arxiv.org/abs/2401.05856): (1) **Missing content** (answer isn't indexed →
should refuse, often invents), (2) **Missed the top‑ranked doc** (it's there but ranked too low), (3)
**Not in context** (retrieved but dropped when assembling), (4) **Not extracted** (in context but the
model misses it), (5) **Wrong format** (ignores "give me a checklist"), (6) **Wrong specificity** (too
general/specific), (7) **Incomplete** (partial answer though full info was present). Their meta‑lesson:
**you can only validate this at runtime** — offline tests and vendor "no‑hallucination" claims miss the
real failures; log and evaluate each stage (load → chunk → retrieve → rank → answer).

---

## Bottom line
Every documented failure class **applies to us** — which is expected; these are the universal failures
of this kind of system, and each has a known fix. Roughly: **already handled in our storage (chatbot
just needs to use it):** cross‑ref links, table structure, revision dates, OCR‑confidence score.
**Genuine storage gaps (need a reload):** equipment/voltage tags (#1), whole‑procedure units (#3),
warning↔step binding (#5), table alignment + row cap + OCR‑confidence use (#6), per‑callout diagram
values (#7), is‑current + bulletin overlay (#8), injection‑guarding our load‑time AI (#9), exact
highlight (#10), landed‑everything checks (#11). **Pure chatbot‑side:** quote‑and‑cite + citation
verification (#2,#4), calibrated abstention (#12), Prompt Shields + permissions (#9, ACL).
