Hi Copilot — excellent, the indexer finished and all 3 docs are fully indexed
(diagram counts 25/30/535 match the figures exactly; tables + table_rows landed).
The pipeline works end-to-end. Two things left: confirm the VECTORS/embeddings
actually landed (record counts don't prove vectors), and pull a quality baseline
so we know which fields are well-populated vs empty. READ-ONLY — no edits, no
reindex. Run and print the REPORT. SSL env as usual; US Gov.

---

### Run these (read-only)

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815
SV=2024-05-01-preview
SEARCH_EP=$(python -c "import json;print(json.load(open('deploy.config.json'))['search']['endpoint'].rstrip('/'))")
PFX=$(python -c "import json;print(json.load(open('deploy.config.json'))['search'].get('artifactPrefix') or 'mm-manuals')")
INDEXER="${PFX}-indexer"; INDEX="${PFX}-index"

# 1. Final indexer status (should be success, processed=3)
az rest --method get --resource "https://search.azure.us" --url "$SEARCH_EP/indexers/$INDEXER/status?api-version=$SV" --query "{status:lastResult.status, processed:lastResult.itemsProcessed, failed:lastResult.itemsFailed}" -o json

# 2. VECTOR CHECK — a semantic+vector query. If it returns hits, embeddings are present and the vectorizer works.
az rest --method post --resource "https://search.azure.us" \
  --url "$SEARCH_EP/indexes/$INDEX/docs/search?api-version=$SV" \
  --headers "Content-Type=application/json" \
  --body '{"search":"fault indicator installation","top":3,"vectorQueries":[{"kind":"text","text":"fault indicator installation","fields":"text_vector","k":10}],"select":"chunk_id,record_type,source_file,header_1"}' \
  --query "value[].{rt:record_type, src:source_file, h1:header_1}" -o json

# 3. Contract + smoke (should PASS now that records exist)
python scripts/smoke_test.py --config deploy.config.json --skip-run

# 4. Vector/semantic demo via the guide (its queries use vectorQueries)
python scripts/index_query_guide.py --config deploy.config.json --demo

# 5. Quality baseline: full-corpus null/empty rates + which NEW fields are populated vs empty
python scripts/audit_all_retrievable_fields.py --config deploy.config.json

# 6. Contract validation (full corpus, not the default sample of 10)
python scripts/validate_index.py --config deploy.config.json --sample 0
```

### Print this REPORT block

```
INDEXER_FINAL: status=<>  processed=<>  failed=<>
VECTOR_QUERY: <did step 2 return hits? yes/no; paste the few results>
SMOKE_TEST: <pass / the issues>
INDEX_QUERY_DEMO: <did semantic+vector queries return results? yes/no + any error>
AUDIT — key findings (paste the critical/high section, and especially the
  populated-rate for these fields per record_type if shown):
    content_class, retrieval_eligible, table_columns, table_row_cells,
    table_row_semantic_key/value, applies_to_equipment/system/voltage,
    procedure_id/step_id, figure_step_linked, figure_linkage_confidence,
    locator_type, line_bboxes
VALIDATE_INDEX: <pass/fail + top issues>
```

Run everything and give me the REPORT. Do not reindex or edit anything.
