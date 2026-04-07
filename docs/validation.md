# Validation checklist (v2.1)

## Local (no Azure required)

```bash
python tests/test_unit.py             # 68 unit assertions
python tests/test_e2e_simulator.py    # full handler-side end-to-end run
```

`test_unit.py` exercises page-span parsing (DI marker timeline), section
index walking, table extraction with multi-page merge, semantic-string
assembly, process_table shaping, chunk_id collision regression,
table_caption flow, OData escaping, and config error handling. Should
report `68/68 passed`.

`test_e2e_simulator.py` drives the real handler functions through the
exact JSON envelope Azure AI Search sends and prints one finalized
record of every type (text / multi-page text / diagram / table /
summary), then validates each against the index schema. Should end
with `ALL E2E SIMULATION CHECKS PASSED`.

## Azure runtime checks

Run these after the first indexer execution.

## 1. Built-in Layout output paths
The skillset still uses the built-in `DocumentIntelligenceLayoutSkill` for the markdown text path. Verify these paths in a Debug Session:

- /document/markdownDocument/*/sections/h1
- /document/markdownDocument/*/pageNumber
- /document/markdownDocument/*/ordinal_position

If they differ in your region's API version, update `search/skillset.json`.

## 2. process-document skill
In the Debug Session, expand the `/document` enrichment after `process-document-skill` runs and confirm:

- `enriched_figures[]` is non-empty for any PDF that contains figures.
- Each figure entry has `image_b64`, `bbox`, `caption`, `header_1/2/3`, `surrounding_context`.
- `enriched_tables[]` is non-empty for any PDF that contains tables.
- Each table entry has `markdown` (an actual markdown grid), `row_count`, `col_count`, `page_start`, `page_end`.

## 3. Records actually projected
Query the index after a single-document run:

  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'text'&$count=true
  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'diagram'&$count=true
  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'table'&$count=true
  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'summary'&$count=true

All four counts must be > 0 for a manual that contains diagrams and tables.

## 4. Multi-figure pages
Pick a PDF page that visually contains 2+ figures. Confirm:

  $filter=record_type eq 'diagram' and physical_pdf_page eq <page>

Returns one record per figure (not one collapsed record per page).

## 5. Diagram → section linking
For 5 random diagram records confirm `header_1`, `header_2`, `header_3` are populated and match the chapter/section the figure visually belongs to.

## 6. surrounding_context populated
For 5 random diagram records confirm `surrounding_context` contains real prose from the body around the figure caption (not empty, not just headers).

## 7. Table records are structured
For a known specification table:

  $filter=record_type eq 'table'

Confirm `chunk` contains a real markdown grid (`| col1 | col2 |` and `| --- | --- |` rows). Not a vision description.

## 8. Multi-page table merge
For a known multi-page table confirm:

  physical_pdf_page < physical_pdf_page_end

And the markdown grid spans both page contents in one record.

## 9. Vision prompt enrichment
Tail the Function App logs while the indexer runs and confirm the `analyze-diagram` prompt body contains:
- `Section: <header path>`
- `Page: <number>`
- `Caption (from layout): <caption>`
- `Surrounding text: ...`

## 10. Hash cache hits on re-index
Run the indexer twice on the same PDF. Second run should produce diagram records with `processing_status=cache_hit` (no new vision calls).

  $filter=record_type eq 'diagram' and processing_status eq 'cache_hit'

## 11. Vectorizer query (no client embedding)
Send a vector query as raw text:

  POST /indexes/mm-manuals-index/docs/search?api-version=2024-05-01-preview
  {
    "vectorQueries": [{
      "kind": "text",
      "text": "wiring diagram for control relay",
      "fields": "text_vector",
      "k": 5
    }]
  }

Should return results without the client embedding the query.

## 12. chunk_id uniqueness
  GET /indexes/mm-manuals-index/docs?search=*&$select=chunk_id&$top=1000

No collisions. Prefixes: `txt_`, `dgm_`, `tbl_`, `sum_`.

## 13. Multi-page text spans
For 5 random text records that visually cross a page boundary in the source PDF, confirm:

  $filter=record_type eq 'text' and physical_pdf_page lt physical_pdf_page_end

Returns at least one record per multi-page chunk. Spot-check that the
`physical_pdf_page_end` matches the actual last page the chunk text
appears on in the PDF.

## 14. Page grounding
For 5 random text records, confirm `physical_pdf_page` matches the PDF page and `printed_page_label` matches the visible page label.
