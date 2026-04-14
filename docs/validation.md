# Validation (v3.0)

Two layers: **local** (runs with no Azure access) and **cloud**
(runs against a real deployed environment). Both are wired as mandatory
release gates in [`release-gates.md`](release-gates.md).

## Local (no Azure required)

```bash
python tests/test_unit.py             # 68 deterministic unit checks
python tests/test_e2e_simulator.py    # full handler-side end-to-end run
ruff check function_app scripts       # lint
bicep build infra/main.bicep --stdout > /dev/null   # template syntax
```

- `test_unit.py`: page-span parsing (DI marker timeline), section index
  walking, table extraction with multi-page merge, semantic-string
  assembly, `process_table` shaping, `chunk_id` collision regression,
  `table_caption` flow, OData escaping, config error handling. Should
  report `68/68 passed`.
- `test_e2e_simulator.py`: drives real handler functions through the
  exact JSON envelope Azure AI Search sends and emits one finalized
  record of each type (text, multi-page text, diagram, table, summary),
  validated against `search/index.json`. Should end with
  `ALL E2E SIMULATION CHECKS PASSED`.

## Cloud (after deploy)

### Automated: `scripts/smoke_test.py`

```bash
python scripts/smoke_test.py --env dev
```

This is the canonical cloud validation. It:

1. Triggers the indexer on the deployed environment.
2. Waits up to `--wait-minutes` (default 15) for completion.
3. Asserts `status == success`, `itemsProcessed > 0`.
4. For each `record_type` (text, diagram, table, summary):
   - Asserts at least one record exists.
   - Samples one record and verifies the required fields are populated.
5. Reports the multi-page text chunk count (informational).

Non-zero exit on any failure. Wire into your deploy with
`scripts/deploy.sh <env> --smoke` or the `smoke: true` input on the
GitHub Deploy workflow.

### Manual spot-checks

Everything below is still worth eyeballing the first time you bring up
an environment. The smoke test catches the mechanical failures; the
checks below catch retrieval-quality regressions.

#### 1. Multi-figure page → multiple diagram records
Pick a PDF page with 2+ figures:

```
$filter=record_type eq 'diagram' and physical_pdf_page eq <page>
```

Should return one record per figure, not one collapsed record.

#### 2. Diagram → section linking
For 5 random diagram records, confirm `header_1/2/3` match the
chapter/section the figure visually belongs to.

#### 3. `surrounding_context` populated
For 5 random diagram records, confirm `surrounding_context` contains
real body prose, not just headers or empty strings.

#### 4. Table records structured
For a known spec table:

```
$filter=record_type eq 'table' and contains(table_caption, '<caption>')
```

Confirm `chunk` is a real markdown grid (`|` separators, `---`
separator row). Not a vision description.

#### 5. Multi-page table merge
For a known table that spans pages, confirm one record covers both
pages:

```
physical_pdf_page lt physical_pdf_page_end
```

And the `chunk` contains data rows from both source pages, with the
continuation-page header deduplicated (fixed in v3.0).

#### 6. Multi-page text chunks
For 5 random text records that cross a page boundary:

```
$filter=record_type eq 'text' and physical_pdf_page lt physical_pdf_page_end
```

Confirm:
- `physical_pdf_page_end` matches the last source page.
- `physical_pdf_pages` (v3.2) is the full sorted list of every page
  the chunk touches. For a chunk that spans pages 5–8, this must be
  `[5, 6, 7, 8]`, not just `[5, 8]` — citation UIs rely on this.
- `printed_page_label_end` (v3.0) uses DI markers to slice the chunk
  at the real page boundary, so the end label should match the printed
  label on the final physical page the chunk covers.

#### 7. Hash cache hits on re-index
Reset the indexer and run it a second time on the same PDFs:

```
$filter=record_type eq 'diagram' and processing_status eq 'cache_hit'
```

Count should be > 0 on the second run (no re-vision calls).

#### 8. Vectorizer query (no client embedding)

```
POST /indexes/<INDEX_NAME>/docs/search?api-version=2024-05-01-preview
{
  "vectorQueries": [{
    "kind": "text",
    "text": "wiring diagram for control relay",
    "fields": "text_vector",
    "k": 5
  }]
}
```

Returns results without the caller embedding the query.

#### 9. `chunk_id` uniqueness
No collisions across the index. Prefixes: `txt_`, `dgm_`, `tbl_`, `sum_`.

#### 10. Vision prompt enrichment (log inspection)
Tail Function App logs during an indexer run. The `analyze-diagram`
prompt body must contain:

- `Section: <header path>`
- `Page: <number>`
- `Caption (from layout): <caption>`
- `Surrounding text: "..."`
