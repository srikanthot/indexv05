# Validation checklist

Run these after the first indexer execution.

## 1. Layout output paths
The skillset references paths like:
- /document/markdownDocument/*/sections/h1
- /document/markdownDocument/*/pageNumber
- /document/markdownDocument/*/ordinal_position

These vary by DocumentIntelligenceLayoutSkill API version. Use the
indexer **Debug Session** in the portal to inspect the actual JSON
emitted by the layout skill, then update the source paths in
`search/skillset.json` if they differ.

## 2. Records actually projected
Query the index after a single-document run:

  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'text'&$count=true
  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'diagram'&$count=true
  GET /indexes/mm-manuals-index/docs?search=*&$filter=record_type eq 'summary'&$count=true

All three counts must be > 0 for a manual that contains diagrams.

## 3. chunk_for_semantic is a real string
Pick any record and confirm `chunk_for_semantic` contains:
  - Source: ...
  - Section / Diagram header line
  - Page label or page number
  - Real chunk text (not just JSON or empty)

## 4. chunk_id uniqueness
  GET /indexes/mm-manuals-index/docs?search=*&$select=chunk_id&$top=1000

Confirm no collisions and prefixes are correct (txt_, dgm_, sum_).

## 5. Vector populated
  GET /indexes/mm-manuals-index/docs/$count

Run a vector query against `text_vector` and confirm non-zero scores.

## 6. Diagram triage
  $filter=record_type eq 'diagram' and has_diagram eq false

Inspect a sample to confirm decorative images were correctly skipped
(processing_status = 'skipped_decorative').

## 7. Page grounding
For 5 random text records, open the source PDF and confirm:
  - physical_pdf_page matches the actual PDF page
  - printed_page_label matches the visible page label printed on the page
