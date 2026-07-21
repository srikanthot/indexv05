# What's New in the Index — Before → After

A summary of the indexing improvements shipped this cycle, framed as *what we
had before* → *what we added* → *why it gives better results*. Everything is
live after a reindex.

---

## 1. New metadata field: `title` (team request) ✅
- **Before:** documents carried 3 taxonomy metadata fields — `operationalarea`, `functionalarea`, `doctype`. No title.
- **Now:** a 4th field, **`title`**, captures each PDF's blob `title` metadata verbatim (e.g. "Outage Restoration Plan", "Electrical Manual"), on every record, exactly like the other three.
- **Benefit:** documents are identifiable and filterable by their real title; better citations and scoping.

## 2. Whole-procedure retrieval: `topic_id` (biggest win)
- **Before:** a procedure spanning 4 pages / 10–15 chunks — plus its figure and its checklist table — had **no shared key**. Asking "give me the maintenance procedure for a 12kV transformer" could return a few matching chunks and **silently miss steps**, the figure, or the table.
- **Now:** every record of one section (text steps + figure + table) shares one **`topic_id`**. One filter returns the **complete procedure, in order, with its figure and checklist table** — nothing dropped.
- **Benefit:** complete, trustworthy multi-page answers — critical for safety procedures where a missing step is dangerous.

## 3. Citation fields: `chapter_label`, `chapter_number`, `section_path`
- **Before:** no chapter was captured; citations could only show file + page.
- **Now:** citations can show **"Chapter 5 › <section> › page 5-7"** (chapter detected from headings only, so cross-references in prose don't create false chapters).
- **Benefit:** precise, professional citations users can verify in the source manual.

## 4. Working text→table link: `tables_referenced_normalized`
- **Before:** text that said "see Table 5-2" couldn't reliably pull that table (no normalized join key; `table_number` often empty).
- **Now:** a normalized table-reference key mirroring the existing figure link.
- **Benefit:** the bot can attach the referenced table to the answer.

## 5. Never-blank citation title
- **Before:** `document_title` (from the PDF's Title property) was **blank** for scanned / Office-to-PDF manuals → blank citation titles.
- **Now:** documented coalesce rule using existing fields: `document_title || document_number || filename`.
- **Benefit:** every citation shows a real, identifiable title.

---

## Safety / accuracy fixes (same fields, better data)

## 6. Distribution voltages now captured (2.4 / 4.16 / 4.8 / 7.2 / 8.32 kV)
- **Before:** the voltage classifier required 2+ digits, so single-leading-digit distribution classes were **dropped** → wrong-voltage routing.
- **Now:** all distribution kV classes are tagged in `applies_to_voltage`.
- **Benefit:** correct voltage scoping — a real safety concern (right clearances/procedures for the right voltage).

## 7. Full procedure steps (not just the first line)
- **Before:** `procedure_step_text` captured only the **first line** of each step — sub-steps (a/b/c) and wrapped text were dropped.
- **Now:** the full step body, including sub-steps and continuation lines.
- **Benefit:** the bot returns the complete step, not half of it.

## 8. Complete multi-line safety callouts
- **Before:** a boxed "WARNING / De-energize before servicing" was **truncated to the first line** ("WARNING") — the actionable clause was lost.
- **Now:** the full callout text is kept.
- **Benefit:** the actual safety instruction survives to the answer.

## 9. Cleaner safety signal (`safety_callout`)
- **Before:** NOTE and NOTICE were counted as safety callouts → almost everything looked "safety", diluting the safety-boost ranking.
- **Now:** only ANSI signal words **DANGER / WARNING / CAUTION** set `safety_callout`.
- **Benefit:** genuine DANGER/WARNING content ranks higher; less noise.

## 10. More accurate page labels & revisions
- **Before:** a mis-tagged page marker (a date, "Attachment") could become the printed page label; "Revision History" could be captured as the revision id.
- **Now:** page labels are validated before use; boilerplate is rejected as a revision.
- **Benefit:** citations point to the right page; revision data is trustworthy.

## 11. Semantic ranker weights safety + chapter terms
- **Before:** the ranker's keyword fields didn't include hazard/callout/chapter fields.
- **Now:** `hazard_class`, `governing_callouts`, `chapter_label` added.
- **Benefit:** safety-relevant chunks rank higher for safety queries.

---

## New quality-assurance tooling

## 12. Whole-index health auditor — `audit_index_production.py`
Scans **every** record: coverage (did all chunks land?), **vector presence** (is semantic search alive?), required-field gaps, stub/garbage detection. Exits non-zero on any critical issue. **Before:** the old auditors could report "all green" on a broken index.

## 13. Sample-based accuracy scorecard — `audit_index_accuracy.py`
Reads a stratified sample (~2,000 chunks) and scores **every field**: fill% + validity% + bad-value examples. Answers "how good is the data?" in minutes.

## 14. Per-document spot-check — `verify_new_fields.py`
Shows each field's fill-rate + example value for one PDF (`--source-file`), so you can eyeball a document's `title`, `topic_id`, `chapter`, safety fields.

## 15. 551-scenario coverage catalog — `docs/CHATBOT_SCENARIOS.md`
Realistic user questions across 12 categories, each scored for whether the index can answer it — the roadmap driver.

---

## Pipeline reliability fixes
- Fixed a Jenkins flag crash, a corrupted `check_index.py`, and added embedding-endpoint validation that prevents a silent all-null-vector deploy.

---

**Net:** the index moved from "retrieves matching chunks" to "returns complete,
correctly-scoped, well-cited, safety-accurate answers" — with tooling to prove
it. All changes take effect after the next full reindex.
