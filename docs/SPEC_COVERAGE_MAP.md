# Original Spec — Full Coverage Map

Maps every item in your "Detailed Indexing and Data-Prep Specification for Whole Index Quality"
(22 bug IDs + schema + gates) to what's actually implemented on this branch. Honest status.

## Bugs you marked "resolved by CODE" (retrieval/agent side — not indexing's job)
63078, 62015, 67009, 67008/66003, 67007, 66010, 67014, 66006, 62002, 60019/60011 — you already
fixed these on the retrieval/answer side. 63078 (procedure completeness) is **also reinforced** by the
new procedure model here. Nothing owed from indexing.

## Bugs you marked "needs indexing/data-prep" (MY responsibility)
| Bug | Need | Status |
|---|---|---|
| **64009** | deterministic table key→value (50kVA/13kV → 80A) | **Core done** (`:`/`;` collision fixed, structured cells, applies_to voltage/equipment on rows, table_variant_id). **Partial:** `table_number`, `table_row_key`, `table_row_units` still to add for fully deterministic lookup. |
| **67003 / 66011** | figure→procedure linkage (Figure 18.98 → full procedure) | **Partial:** `figure_step_linked` now real (true when figure sits in a numbered procedure), `figure_callouts` added. **Missing:** canonical `figure_number`, `figure_title`, `figure_parent_chunk_id` join from step chunks. |
| **66009** | fired-gas-equipment applicability | **Core done** (`applies_to_domain=gas`, equipment classes). **Partial:** `applies_to_phase` + specific controlled vocab (`fired_gas_equipment`, `overhead_transformer` vs `padmounted_transformer`). |
| Mixed (56016, 67015, 61021, 61020, 61003, 60014, 61013) | need repro / frontend | not pure indexing — out of scope here. |

## Schema fields from the spec
| Field group | Status |
|---|---|
| Provenance (source_file/hash/index_run_id/parent_id/chunk_id) | ✅ exist |
| `retrieval_eligible` + `_reason` | ✅ exist |
| Procedure: `procedure_step_id/step_text/sequence_order/branch_label` | ✅ **done this work** (missing: 5a/5b substep split) |
| Applicability: `applies_to_voltage/equipment/domain` | ✅ **done** · ⏳ `applies_to_phase` = in progress |
| Table: `table_variant_id/scope_tags/columns/row_cells/row_quality(+reasons)` | ✅ exist · ❌ `table_number`, `table_title`, `table_row_key`, `table_row_units` |
| Figure: `figure_ref`, `figure_linkage_confidence`, `figure_callouts` | ✅ · ❌ canonical `figure_number`, `figure_title`, `figure_parent_chunk_id` |
| Locator: `locator_type` | ✅ · ⚠️ `locator_target_refs` partial |
| `content_class` controlled enum (procedural_step, warning_safety, locator_only, …) | ⚠️ current values differ from the spec's vocabulary |

## Highlighting (explicit spec requirement)
> "I need BOTH chunk-level full bbox (attractive full-region highlight) AND line-level bbox (precise
> mode), indexed so frontend can switch modes."

✅ **Covered.** `text_bbox` (full-region "hold box"), `line_bboxes` (precise), `chunk_bboxes`,
`bbox_mode_available` — and B4 tightened the full box to hug the chunk. This matches both your modes.

## Whole-Index Quality Gates (spec "Definition of Done" — mandatory before promotion)
Schema-completeness gate · table-alignment gate · figure-linkage gate · locator-suppression gate ·
applicability-coverage gate · per-PDF gate report · regression query suite.
❌ **Not yet built as a dedicated gate/validator.** (`check_index.py --coverage` +
`audit_all_retrievable_fields.py` exist but don't enforce the spec's gates.) → building
`validate_index_quality.py`.

## Lifecycle (your Jenkins ask + spec "Phase 4 continuous enforcement")
✅ **Done + committed:** blob-delete → purge chunks; blob-edit (same name, new timestamp) → purge all
old chunks then re-index; explicit `--purge-files` for Jenkins; reap/RBAC/wait bugs fixed.

## What's being done next (this session)
1. `applies_to_phase` (single/three-phase)
2. `table_number` + `table_title` + `table_row_key` (64009 deterministic lookup)
3. canonical `figure_number` + `figure_title` + `figure_parent_chunk_id` (67003 linkage)
4. `validate_index_quality.py` — the mandatory gate validator (Jenkins block-on-fail)

## Explicitly deferred (with reason)
- `content_class` enum remap to the spec vocabulary — risky to remap the existing field live;
  better added as a parallel field after the 5-doc validation confirms the current values.
- 5a/5b substep splitting, `table_row_units`, `locator_target_refs` — lower retrieval impact;
  fast-follow.
