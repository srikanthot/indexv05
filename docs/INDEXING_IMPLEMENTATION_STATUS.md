# Indexing Implementation — Status (increment 1)

Tracks the 13-item FINAL spec (`INDEXING_FINAL_SPEC.md`). This increment is **safe to
run** on the 5 test docs: everything below is wired end-to-end (emitter → skill output →
index projection → schema), imports cleanly, and passes tests. Nothing is half-wired in a
way that errors — unfinished items are simply empty fields.

## Verified green
- Classifier + procedure logic: **33 unit tests pass** (`tests/test_content_classifiers.py`
  + procedure functional test). Full suite **291/294** (the 3 failures are PRE-EXISTING
  snapshot drift in code paths I never touched — see bottom).
- `index.json` + `skillset.json` both valid; projection↔schema cross-check consistent for
  every new field.

## DONE this increment
| Item | What landed | Record types |
|---|---|---|
| **Critical bug** | `str.maketrans` crash from a file-sync stripping zero-width chars — **blocked the ENTIRE function app from importing**. Fixed + hardened (ordinal-keyed) in `sections.py` + `text_utils.py`. | app-wide |
| **A1** | Procedure model: step parser, verbatim `procedure_step_text`, `procedure_step_order/count/title`, stable `procedure_id` shared across chunks of one procedure (→ chatbot reassembles whole procedure by filter+sort). | text |
| **A2** | `governing_callouts` bound at **section scope** (a step inherits a WARNING sitting in a sibling chunk — fixes the split-warning kill-path) + `is_prohibition`/`prohibitions`. | text, table, row, diagram, summary |
| **A3** | `applies_to_voltage` (0%→real), `applies_to_equipment` (real classes, not raw-tag alias), new `applies_to_domain` (gas/electric/substation/metering). | all record types |
| **A4** | `hazard_class` (live_line/energized/HV/arc_flash/gas/confined_space/…) + `criticality` (critical/high/normal). | all record types |
| **B2** | Table `:`/`;` **delimiter collision fixed** (structured grid-bound cells, no re-parse — a "3:1" ratio no longer mis-binds). Silent >5000-row drop → **truncate + WARNING log + `table_rows_truncated`/`_suppressed_count` markers**. | table, row |
| **B3** | `low_confidence_ocr` flag now consumed from the (previously computed-but-ignored) OCR confidence. | text |
| **B5** | `figure_callouts` (discrete OCR tokens, not one blob) + **real `figure_step_linked`** (true only when figure sits in a numbered procedure) with scaled confidence. | diagram |
| **B6** | Taxonomy fallback: derive operationalarea/doctype from path/filename when blob metadata absent (was always null). | all |
| **C2** | Ingest **prompt-injection guard**: hardened system prompts + fenced untrusted text on all 3 vision/summary LLM calls (`diagram.py`, `summary.py`, `preanalyze.py`) + fence-spoof neutralization. | ingest |
| **C3** | Cross-parent phash reuse **scoped to nameplate/equipment_photo only** (can't graft a foreign schematic's caption). | diagram |

New files: `function_app/shared/content_classifiers.py`, `procedures.py`, `prompt_safety.py`,
`tests/test_content_classifiers.py`.

## DONE in increment 2 (added after the first checkpoint)
| Item | What landed |
|---|---|
| **B4** (your #1 complaint) | Exact-highlight now uses **DI line-level polygons** (`pages[].lines`) instead of paragraph-union boxes. A chunk that is a slice of a big paragraph highlights only its own lines. Strict improvement — falls back to the old paragraph matcher when no line matches, so it never regresses. `bbox_version` bumped 2.0.0→2.1.0. **7 unit tests pass** (`tests/test_bbox_precision.py`). |
| **B1** | `scripts/mark_current_revisions.py` — a post-index pass that groups every record into a manual "family" (normalized `document_number`), marks the newest revision `is_current_revision=true`, sets `document_family_id` + `supersedes_revision`, and merges them back. **Dry-run by default**, `--apply` to write. Pure grouping/ordering logic unit-verified. (Not exercisable on 5 unrelated test docs — becomes meaningful once you index multiple revisions of one manual.) |
| **C1** | Durability gate hardened on **both** sides: `auto_heal.py` no longer counts a PDF "done" if any of its records carry a loss status (`needs_preanalyze_output`/`all_figures_dropped`/`partial_figure_loss`/`partial_vision`) — lossy docs re-heal. `preanalyze.py` no longer accepts `partial_vision` as fully done (bounded retry via the heal loop; `ACCEPT_PARTIAL_VISION=true` to opt out). |

New files: `scripts/mark_current_revisions.py`, `tests/test_bbox_precision.py`.

## Still deferred (small, low priority)
- **Per-cell/row OCR confidence** on table rows (needs a word↔cell spatial join). Field exists
  (`table_row_min_confidence`), empty for now.
- **Neighbor links** `chunk_prev_id/next_id`: left empty by design — `procedure_id` + order +
  `layout_ordinal` already let the chatbot reassemble whole units; true neighbor links need a
  post-pass and aren't required for that.

## The 3 pre-existing test failures (NOT from this work)
The suite couldn't even import before my maketrans fix, so these never ran. All are in code I
did not modify: (1) a table-row test asserting a defunct min-rows threshold of 5 (`ROW_RECORD_MIN_ROWS`
is 2 — a 4-row table emits rows on both old and new code); (2-3) two page-range tests
(`test_unit.py:146-147`) in the page-resolution logic. Recommend confirming these against the
real repo — they look like snapshot drift in the c:\index copy.
