# Validation

Two layers: **local** (no Azure required) and **cloud** (against a
deployed environment).

## Local

```bash
python tests/test_unit.py             # deterministic unit checks
python tests/test_e2e_simulator.py    # full handler simulation
ruff check function_app tests scripts
```

- `test_unit.py`: page-span parsing, section index walking, table
  extraction with multi-page merge, semantic-string assembly,
  chunk-id uniqueness, OData escaping, config error handling.
- `test_e2e_simulator.py`: drives every handler through the exact JSON
  envelope Azure AI Search sends and validates each record type
  against the index schema.

## Cloud — automated

```bash
python scripts/smoke_test.py --config deploy.config.json
```

Runs the indexer, waits for completion, then asserts:

1. Indexer `status == success`, `itemsProcessed > 0`.
2. Every `record_type` (text, diagram, table, summary) has ≥ 1 record.
3. Required fields are populated on a sample of each record type.
4. `physical_pdf_pages` on text/table records covers both the declared
   start and end.

Non-zero exit on any failure.

## Cloud — manual spot-checks

Worth eyeballing the first time you bring up an environment or after
changing the skillset.

### 1. Multi-figure page → multiple diagram records
Pick a PDF page with 2+ figures:

```
$filter=record_type eq 'diagram' and physical_pdf_page eq <page>
```

Should return one record per figure, not one collapsed record.

### 2. Diagram → section linking
For 5 random diagram records, confirm `header_1/2/3` match the
chapter/section the figure visually belongs to.

### 3. `surrounding_context` populated
For 5 random diagram records, confirm it contains real body prose —
not just headers, not empty.

### 4. Table records are structured
For a known spec table:

```
$filter=record_type eq 'table' and contains(table_caption, '<caption>')
```

`chunk` should be a real markdown grid (`|` separators, `---` row), not
a vision description.

### 5. Multi-page table merge
For a multi-page table, one record should cover both pages:

```
physical_pdf_page lt physical_pdf_page_end
```

`chunk` contains data rows from all covered pages, with the
continuation-page header deduplicated.

### 6. Multi-page text chunks
For text records crossing a page boundary:

```
$filter=record_type eq 'text' and physical_pdf_page lt physical_pdf_page_end
```

- `physical_pdf_pages` is the full sorted list of every page covered
  (citation UIs use this to highlight every grounded page).
- `printed_page_label_end` matches the printed label on the final
  physical page the chunk covers.

### 7. Hash-cache hits on re-index
Reset + re-run the indexer:

```
$filter=record_type eq 'diagram' and processing_status eq 'cache_hit'
```

Second run should produce cache_hit records — no new vision calls.

### 8. Vectorizer query (no client embedding)

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

### 9. `chunk_id` uniqueness
No collisions. Prefixes: `txt_`, `dgm_`, `tbl_`, `sum_`.
