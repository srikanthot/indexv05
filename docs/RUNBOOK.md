# Indexing Repo — Operator Runbook

End-to-end runbook for deploying and operating the Azure AI Search
multimodal indexing pipeline in this repository. Read this before you
touch production.

Scope: this repo owns the **indexing application layer only**
(Function App code, search artifacts, deployment scripts, preanalyze).
It does **not** provision Azure resources — those must already exist.

---

## 1. What this pipeline does

Given PDFs in a blob container, it produces an Azure AI Search index
where every chunk is searchable via keyword (BM25) + vector (ada-002)
+ semantic reranker (hybrid retrieval).

Four peer record types live in one index:

| record_type | source                                      | chunk_id prefix |
|-------------|---------------------------------------------|-----------------|
| `text`      | page-sized markdown chunks                  | `txt_`          |
| `diagram`   | one record per figure (GPT-4.1 vision)      | `dgm_`          |
| `table`     | one record per table (DI → markdown)        | `tbl_`          |
| `summary`   | one record per PDF (GPT-4.1 summary)        | `sum_`          |

High-level flow:

```
Blob (PDFs + _dicache/)
     |
     v
preanalyze.py (offline)  ->  caches DI + crops + GPT-4V to _dicache/
     |
     v
Azure AI Search Indexer -> Skillset -> Index
   (schedule: every 15 min by default, batchSize=1, .pdf only)
```

---

## 2. Azure resources required (provision first)

The repo does not create these. They must exist and be wired together.

| Resource                                   | Purpose                                                              | Notes |
|--------------------------------------------|----------------------------------------------------------------------|-------|
| Azure Storage account + container          | Holds source PDFs and `_dicache/` cache blobs                        | Shared-key auth can be disabled. **Enable blob soft-delete** for deletion tracking. |
| Azure AI Search (Basic+; Standard for prod) | Hosts the index, indexer, skillset, datasource                       | **System-assigned managed identity enabled.** |
| Azure OpenAI                               | Embeddings + vision + summaries                                      | Two deployments: `text-embedding-ada-002` (1536 dims) and `gpt-4.1` (vision-capable). |
| Azure Document Intelligence                | Prebuilt-layout extraction                                           | Used by preanalyze and the built-in layout skill. |
| Azure AI Services (multi-service)          | Billing account for the built-in `DocumentIntelligenceLayoutSkill`   | Referenced via `AIServicesByIdentity`. |
| Azure Function App (Linux, Python 3.11, Functions v4) | Hosts the 6 custom WebApi skills                          | **System-assigned managed identity enabled.** |
| Application Insights                       | Telemetry for the Function App                                       | Recommended. |
| Azure Container Apps environment           | Hosts the preanalyze automation jobs (see §15)                       | Required only if you want production automation. |
| Storage Queue                              | Buffers Event Grid blob events for the event-driven job (see §15)    | Same storage account is fine. |

---

## 3. Required role assignments (AAD / managed identity)

All outbound auth is AAD. Create these once per environment.

| Principal               | Scope                     | Role                                                       |
|-------------------------|---------------------------|------------------------------------------------------------|
| Function App MI         | Storage account           | Storage Blob Data Reader                                   |
| Function App MI         | Azure OpenAI              | Cognitive Services OpenAI User                             |
| Function App MI         | Document Intelligence     | Cognitive Services User                                    |
| Function App MI         | Search service            | Search Index Data Reader                                   |
| Search service MI       | Storage account           | Storage Blob Data Reader                                   |
| Search service MI       | Azure OpenAI              | Cognitive Services OpenAI User                             |
| Search service MI       | AI Services account       | Cognitive Services User                                    |
| Container App Job MI    | Storage account           | Storage Blob Data Contributor + Storage Queue Data Message Processor |
| Container App Job MI    | Azure OpenAI              | Cognitive Services OpenAI User                             |
| Container App Job MI    | Document Intelligence     | Cognitive Services User                                    |
| Container App Job MI    | Search service            | Search Service Contributor (to POST `/indexers/run`)       |
| Deploying principal     | Search service            | Search Service Contributor **+** Search Index Data Contributor |
| Deploying principal     | Function App's RG         | Contributor (to set App Settings, fetch function key)      |

The only secret in the whole system is the Function App's `default`
function key, which the deploy script embeds in the skillset URIs at
deploy time. Rotate it by re-running `deploy_search.py`.

---

## 4. Local dependencies

Tools needed on the machine that runs deploy / preanalyze:

- Azure CLI (`az`) — logged in via `az login`
- Azure Functions Core Tools v4 (`func`)
- Python 3.11+
- `jq` (used by `deploy_function.sh`)
- PowerShell 5.1+ or bash

Python deps (`pip install -r requirements.txt`):

- `azure-identity`, `httpx`, `azure-storage-blob`, `pymupdf`,
  `azure-functions`, `ruff` (dev)

---

## 5. Repository layout

```
function_app/                 Python Azure Functions app (custom skills)
  function_app.py             HTTP routes -> skill handlers
  host.json
  requirements.txt
  local.settings.json.example
  shared/
    skill_io.py               WebApi envelope + error translation
    credentials.py            Managed identity helper (lazy)
    config.py                 Typed env-var access
    aoai.py                   Azure OpenAI client (MI-first)
    di_client.py              Document Intelligence REST + blob fetch
    search_cache.py           Image-hash cache lookup
    ids.py                    Stable chunk_id helpers
    page_label.py             Printed page label + physical span
    sections.py               DI section index + surrounding text
    pdf_crop.py               PyMuPDF figure cropping
    tables.py                 DI tables -> markdown (merge + split)
    process_document.py       Orchestrates DI + crop + sections + tables
    process_table.py          Per-table shaper
    semantic.py               chunk_for_semantic builder
    diagram.py                Per-figure vision analysis (hash-cached)
    summary.py                Per-document summary

search/                       Azure AI Search REST bodies (templated)
  datasource.json
  index.json
  skillset.json
  indexer.json

scripts/
  deploy_function.sh / .ps1   Publish function code + apply App Settings
  deploy_search.py            Render + PUT search artifacts via AAD
  preanalyze.py               Offline DI + vision pre-analysis (caches to blob)
  run_preanalyze.sh / .ps1    Preanalyze wrappers
  check_index.py              Index health report
  smoke_test.py               Post-deploy validation
  reset_indexer.ps1           Reset + run indexer
  preflight.py                Checks config + role assignments
  diagnose.py                 Misc diagnostics

tests/
  test_unit.py                Unit checks
  test_e2e_simulator.py       Full handler-side e2e simulation

docs/
  ARCHITECTURE.md
  SEARCH_INDEX_GUIDE.md
  RUNBOOK.md                  This file
  validation.md

deploy.config.example.json    Template — copy to deploy.config.json
requirements.txt
ruff.toml
README.md
```

---

## 6. `deploy.config.json` — the single source of truth

Copy the example and fill it in. Every deploy script reads this file.

```bash
cp deploy.config.example.json deploy.config.json
```

| Key                                          | Purpose                                                                                     |
|----------------------------------------------|---------------------------------------------------------------------------------------------|
| `functionApp.name` / `resourceGroup`         | Target Function App                                                                         |
| `search.endpoint`                            | `https://<svc>.search.windows.net` (use `.azure.us` in Gov Cloud)                           |
| `search.artifactPrefix`                      | Name prefix; produces `<prefix>-ds`, `-index`, `-skillset`, `-indexer` (default `mm-manuals`) |
| `azureOpenAI.endpoint` / `apiVersion`        | AOAI endpoint (`2024-12-01-preview`+ for gpt-4.1)                                           |
| `azureOpenAI.chatDeployment`                 | gpt-4.1 deployment name (summaries)                                                         |
| `azureOpenAI.visionDeployment`               | gpt-4.1 deployment name (vision; usually same as chat)                                      |
| `azureOpenAI.embedDeployment`                | ada-002 deployment name (1536 dims)                                                         |
| `documentIntelligence.endpoint` / `apiVersion` | DI resource                                                                               |
| `aiServices.subdomainUrl`                    | AI Services multi-service endpoint (for the built-in Layout skill)                          |
| `storage.accountResourceId`                  | Full ARM ID — used for `ResourceId=…` datasource connection string                          |
| `storage.pdfContainerName`                   | Container with source PDFs                                                                  |
| `appInsights.connectionString`               | Wired into Function App for telemetry                                                       |
| `skillVersion`                               | Stamped on every record; bump to invalidate image-hash cache                                |

The function key is **not** stored here — `deploy_search.py` fetches it
live from Azure at deploy time.

---

## 7. One-time bootstrap (first deploy)

### 7.1 Sign in

```bash
az login
# For Gov Cloud:
# az cloud set --name AzureUSGovernment && az login
```

### 7.2 Check prerequisites

```bash
python scripts/preflight.py --config deploy.config.json
```

Verifies resources exist and role assignments are in place before you
deploy.

### 7.3 Deploy the Function App code

```bash
scripts/deploy_function.sh deploy.config.json
# Windows:
# .\scripts\deploy_function.ps1 -Config .\deploy.config.json
```

This publishes the Python package and applies the required App
Settings on the Function App:

- `AUTH_MODE=mi`
- `AOAI_ENDPOINT`, `AOAI_API_VERSION`, `AOAI_CHAT_DEPLOYMENT`, `AOAI_VISION_DEPLOYMENT`
- `DI_ENDPOINT`, `DI_API_VERSION`
- `SEARCH_ENDPOINT`, `SEARCH_INDEX_NAME`
- `SKILL_VERSION`
- `APPLICATIONINSIGHTS_CONNECTION_STRING` (if provided)

### 7.4 Create / update the Search artifacts

```bash
python scripts/deploy_search.py --config deploy.config.json
```

Renders every `<PLACEHOLDER>` in `search/*.json` from the config and a
live-fetched function key, then `PUT`s four artifacts via AAD:

1. `datasources/<prefix>-ds`
2. `indexes/<prefix>-index`
3. `skillsets/<prefix>-skillset`
4. `indexers/<prefix>-indexer`

Idempotent. Fails loud if any placeholder is unrendered.

### 7.5 Pre-analyze the PDFs (offline)

The custom skills read a pre-computed cache instead of calling DI and
vision live at index time (the Azure AI Search skill timeout is 230 s —
large PDFs cannot finish inside that window).

```bash
# Full preanalyze (DI + crops + vision), sequential phases
python scripts/preanalyze.py --config deploy.config.json

# Or phased / parallel for speed
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output

# PowerShell wrapper
.\scripts\run_preanalyze.ps1 -VisionParallel 40
```

Cache artifacts land under `<container>/_dicache/`:

| Blob                                      | Contents                                      |
|-------------------------------------------|-----------------------------------------------|
| `_dicache/<pdf>.di.json`                  | Full DI layout result                         |
| `_dicache/<pdf>.crop.<fig>.json`          | Per-figure base64 PNG + bbox                  |
| `_dicache/<pdf>.vision.<fig>.json`        | Per-figure GPT-4V JSON output                 |
| `_dicache/<pdf>.output.json`              | Final assembled figures + tables (= "done" marker) |

### 7.6 Run the indexer + validate

```bash
python scripts/deploy_search.py --config deploy.config.json --run-indexer
python scripts/smoke_test.py      --config deploy.config.json
```

`smoke_test.py` triggers the indexer, waits for `status=success`, then
asserts record counts, required fields, and that `physical_pdf_pages`
covers the declared start+end on text/table records. Non-zero exit on
any failure — safe to gate CI on.

---

## 8. Search artifacts — what each one is

### 8.1 Data source (`search/datasource.json`)

Points the indexer at the blob container.

- Type: `azureblob`
- Credentials: `ResourceId=<storage ARM id>;` (MI auth, no keys)
- Change detection: `HighWaterMarkChangeDetectionPolicy` on
  `metadata_storage_last_modified`
- Deletion detection: `NativeBlobSoftDeleteDeletionDetectionPolicy`
  (requires blob soft-delete enabled on the storage account)

### 8.2 Index (`search/index.json`)

Schema for all four record types. Full field reference in §10.

Key points:

- `id` is the key (string, keyword analyzer).
- `text_vector` is `Collection(Edm.Single)` 1536 dims, HNSW profile
  `mm-hnsw-profile`, cosine.
- Semantic config `mm-semantic-config` sets `source_file` as title and
  prioritizes `chunk_for_semantic`, `chunk`, `diagram_description`,
  `surrounding_context`.
- Vectorizer `aoai-vectorizer` lets the service embed queries
  server-side — clients do not need to embed themselves.

### 8.3 Skillset (`search/skillset.json`)

Ordered pipeline of 13 skills (see §9).

- `cognitiveServices`: `AIServicesByIdentity` + `subdomainUrl` — the
  Search service MI authenticates to the AI Services multi-service
  account (that's what bills the built-in Layout skill).
- `indexProjections.parameters.projectionMode`:
  `skipIndexingParentDocuments` — only the flattened child records are
  indexed, never the raw parent document.

### 8.4 Indexer (`search/indexer.json`)

- `schedule.interval`: `PT15M` (drop to `PT1H` once event-driven
  automation is in place — see §15)
- `parameters.batchSize`: `1` (one PDF at a time; big PDFs need full
  quota)
- `maxFailedItems`: `-1` (never fail the whole run on one bad PDF)
- `parameters.configuration.indexedFileNameExtensions`: `.pdf` — the
  indexer only touches `.pdf` blobs, so `_dicache/*.json` is ignored
- `dataToExtract`: `contentAndMetadata`
- `imageAction`: `none` (we do image extraction ourselves)

---

## 9. Skillset — skill-by-skill

Execution is data-dependency driven; logically the order is:

### Built-in

| # | Skill                              | Context                                      | Purpose |
|---|------------------------------------|----------------------------------------------|---------|
| 1 | `DocumentIntelligenceLayoutSkill`  | `/document`                                  | PDF → markdown with h1–h3 and page markers |
| 2 | `SplitSkill`                       | `/document/markdownDocument/*`               | Split each section into ~1200-char pages with 200-char overlap |
| 3 | `AzureOpenAIEmbeddingSkill` ×4     | text / figures / tables / summary            | 1536-dim ada-002 embeddings |

### Custom (WebApi → our Function App)

All six POST to `https://<FUNCTION_APP_HOST>/api/<route>?code=<FUNCTION_KEY>`.

| # | Skill name (route)              | Context                                  | Batch / parallelism | Purpose |
|---|----------------------------------|------------------------------------------|---------------------|---------|
| 4 | `process-document-skill` (`/api/process-document`) | `/document` | 1 / 2   | Reads `_dicache/<pdf>.output.json`; emits `enriched_figures` + `enriched_tables` + `processing_status` |
| 5 | `extract-page-label-skill` (`/api/extract-page-label`) | `/document/markdownDocument/*/pages/*` | 5 / 4   | Printed page label + physical page span + `chunk_id` per text chunk |
| 6 | `analyze-diagram-skill` (`/api/analyze-diagram`) | `/document/enriched_figures/*` | 1 / 4   | Per-figure vision output (description, category, figure_ref, image_hash) |
| 7 | `shape-table-skill` (`/api/shape-table`) | `/document/enriched_tables/*` | 5 / 4   | Per-table record (markdown, row/col count, caption, page span) |
| 8 | `build-semantic-string-text` (`/api/build-semantic-string`) | `/document/markdownDocument/*/pages/*` | 10 / 4  | `chunk_for_semantic` for text chunks |
| 9 | `build-semantic-string-diagram` (`/api/build-semantic-string`) | `/document/enriched_figures/*` | 10 / 4  | `chunk_for_semantic` for diagram chunks |
| 10 | `build-doc-summary-skill` (`/api/build-doc-summary`) | `/document` | 1 / 2   | One summary record per PDF (gpt-4.1) |

Every custom skill returns `processing_status` and `skill_version` so
you can filter out broken records at query time.

### Index projections

Four selectors flatten the nested outputs into one index per record
type:

| Selector (parentKeyFieldName) | Source context                           | Produces |
|-------------------------------|------------------------------------------|----------|
| `text_parent_id`              | `/document/markdownDocument/*/pages/*`   | 1 record per text chunk |
| `dgm_parent_id`               | `/document/enriched_figures/*`           | 1 record per figure |
| `tbl_parent_id`               | `/document/enriched_tables/*`            | 1 record per table |
| `sum_parent_id`               | `/document`                              | 1 record per PDF |

---

## 10. Index schema — field reference

### Identity

| Field                                                      | Type       | Notes |
|------------------------------------------------------------|------------|-------|
| `id`                                                       | string     | Key. Keyword analyzer. Auto-generated. |
| `chunk_id`                                                 | string     | Stable, human-readable (`txt_…`, `dgm_…`, `tbl_…`, `sum_…`). |
| `parent_id`                                                | string     | Hash of source PDF URL; groups all records from one PDF. |
| `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `sum_parent_id` | string | Only the one matching `record_type` is populated. |
| `record_type`                                              | string     | `text` / `diagram` / `table` / `summary`. |

### Content

| Field                 | Type                        | Notes |
|-----------------------|-----------------------------|-------|
| `chunk`               | string, searchable          | Raw content (markdown text / vision description / table markdown / summary). |
| `chunk_for_semantic`  | string, searchable          | Chunk + source + headers + page info, tuned for the semantic reranker. |
| `text_vector`         | `Collection(Edm.Single)`, 1536, `stored=false`, `retrievable=false` | ada-002 embedding of `chunk_for_semantic`. |

### Page + location

| Field                     | Type                             | Notes |
|---------------------------|----------------------------------|-------|
| `physical_pdf_page`       | Int32, filterable, sortable      | First physical page (1-indexed). |
| `physical_pdf_page_end`   | Int32, filterable                | Last physical page. |
| `physical_pdf_pages`      | `Collection(Edm.Int32)`, filterable, facetable | Every page the chunk touches. Use `physical_pdf_pages/any(p: p eq 42)`. |
| `printed_page_label`      | string, searchable, filterable   | Label as printed (`"iv"`, `"18-33"`). |
| `printed_page_label_end`  | string                           | End label for multi-page chunks. |
| `layout_ordinal`          | Int32, filterable, sortable      | DI section ordinal; reconstruct document order. |

### Header chain

| Field                         | Type                | Notes |
|-------------------------------|---------------------|-------|
| `header_1` / `header_2` / `header_3` | string, searchable | h1/h2/h3 chain the chunk sits under. |

### Diagram-only

| Field                 | Type                                  | Notes |
|-----------------------|---------------------------------------|-------|
| `figure_id`           | string                                | DI-assigned id (`"134.3"`). |
| `figure_ref`          | string, searchable, filterable        | Human ref as the manual writes it (`"Figure 18.117"`). |
| `figure_bbox`         | string (JSON)                         | `{page, x_in, y_in, w_in, h_in}` for UI highlight. |
| `diagram_description` | string, searchable                    | GPT-4.1 description + OCR labels. |
| `diagram_category`    | string, filterable, facetable, keyword | Controlled vocab: `circuit_diagram`, `wiring_diagram`, `schematic`, `line_diagram`, `block_diagram`, `pid_diagram`, `flow_diagram`, `control_logic`, `exploded_view`, `parts_list_diagram`, `nameplate`, `equipment_photo`, `decorative`, `unknown`. |
| `has_diagram`         | bool, filterable, facetable           | True only for useful diagrams. Filter with `has_diagram eq true`. |
| `image_hash`          | string                                | SHA-256 of cropped PNG; dedupes repeated logos. |

### Table-only

| Field              | Type             | Notes |
|--------------------|------------------|-------|
| `table_row_count`  | Int32            | After continuation-merge + split. |
| `table_col_count`  | Int32            |   |
| `table_caption`    | string, searchable, filterable | Caption text above the table. |

### Source reference

| Field         | Type                              | Notes |
|---------------|-----------------------------------|-------|
| `source_file` | string, searchable/filterable/sortable/facetable | Just the filename. |
| `source_url`  | string, retrievable                | Full blob URL (for the UI link). |
| `source_path` | string, filterable                 | Same as `source_url`, used in filters. |

### Provenance + health

| Field                  | Type                             | Notes |
|------------------------|----------------------------------|-------|
| `surrounding_context`  | string, searchable               | Sentences around the figure/table from body text. |
| `processing_status`    | string, filterable, facetable    | `"ok"`, `"no_image"`, `"content_filter"`, … — filter `eq 'ok'` for clean retrieval. |
| `skill_version`        | string, filterable, facetable    | Stamp from `SKILL_VERSION`. |

### Admin classification (reserved / currently null)

| Field            | Type                | Notes |
|------------------|---------------------|-------|
| `operationalarea`| string, searchable  | Populated out-of-band (not by this pipeline). |
| `functionalarea` | string, searchable  |   |
| `doctype`        | string, searchable  |   |

### Vector search config

```json
"vectorSearch": {
  "algorithms": [{
    "name": "mm-hnsw-algo", "kind": "hnsw",
    "hnswParameters": { "m": 8, "efConstruction": 400, "efSearch": 500, "metric": "cosine" }
  }],
  "profiles": [
    { "name": "mm-hnsw-profile", "algorithm": "mm-hnsw-algo", "vectorizer": "aoai-vectorizer" }
  ],
  "vectorizers": [{
    "name": "aoai-vectorizer", "kind": "azureOpenAI",
    "azureOpenAIParameters": { "resourceUri": "...", "deploymentId": "...", "modelName": "text-embedding-ada-002" }
  }]
}
```

### Semantic config

```json
"semantic": {
  "defaultConfiguration": "mm-semantic-config",
  "configurations": [{
    "name": "mm-semantic-config",
    "prioritizedFields": {
      "titleField":            { "fieldName": "source_file" },
      "prioritizedContentFields": [
        { "fieldName": "chunk_for_semantic" },
        { "fieldName": "chunk" },
        { "fieldName": "diagram_description" },
        { "fieldName": "surrounding_context" }
      ],
      "prioritizedKeywordsFields": [
        { "fieldName": "header_1" }, { "fieldName": "header_2" }, { "fieldName": "header_3" },
        { "fieldName": "figure_ref" }, { "fieldName": "table_caption" },
        { "fieldName": "printed_page_label" }, { "fieldName": "diagram_category" }
      ]
    }
  }]
}
```

---

## 11. Querying the index

### Hybrid (recommended default)

```http
POST /indexes/<prefix>-index/docs/search?api-version=2024-11-01-preview
Authorization: Bearer <aad-token>

{
  "search": "buried underground distribution",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "captions": "extractive",
  "answers": "extractive|count-3",
  "vectorQueries": [
    { "kind": "text", "text": "buried underground distribution", "fields": "text_vector" }
  ],
  "top": 10
}
```

### Diagrams only

```json
{ "search": "fault indicator",
  "filter": "record_type eq 'diagram' and has_diagram eq true",
  "top": 20 }
```

### Page range inside one PDF

```json
{ "search": "fusing",
  "filter": "source_file eq 'ED-ED-UGC.pdf' and physical_pdf_page ge 1000 and physical_pdf_page le 1100" }
```

### Records that touch a specific page

```json
{ "search": "*",
  "filter": "physical_pdf_pages/any(p: p eq 1337)" }
```

### Citation-UI projection

```json
{ "search": "...",
  "select": "chunk_id, source_file, physical_pdf_page, printed_page_label, header_1, header_2, header_3, chunk, figure_bbox, record_type",
  "top": 5 }
```

---

## 12. Steady-state operations

### Re-index a single file

Indexer change detection is high-water-mark on
`metadata_storage_last_modified`. Rewrite the blob (same content, new
timestamp) to force re-pickup.

### Full re-index

```bash
az rest --method post \
  --url "https://<search>.search.windows.net/indexers/<prefix>-indexer/reset?api-version=2024-05-01-preview"
python scripts/deploy_search.py --config deploy.config.json --run-indexer
```

Or:

```powershell
.\scripts\reset_indexer.ps1
```

### Rotate the function key

```bash
az functionapp keys set -g <rg> -n <func> --key-type functionKeys --key-name default
python scripts/deploy_search.py --config deploy.config.json
```

The skillset is re-PUT with the new key; no code change needed.

### Bump `skillVersion`

Edit `deploy.config.json`, re-run `scripts/deploy_function.sh`. Records
re-processed from that point carry the new version; older records keep
the old one until touched. Use it to invalidate the image-hash cache.

### Incremental preanalyze (manual catch-up)

```bash
python scripts/preanalyze.py --config deploy.config.json --incremental
python scripts/preanalyze.py --config deploy.config.json --cleanup
az rest --method post --url "<search>/indexers/<prefix>-indexer/run?api-version=2024-11-01-preview"
```

### Clear the index (keep schema, drop documents)

Azure AI Search has no native truncate. Do:

1. `DELETE /indexes/<prefix>-index?api-version=…` (portal or REST).
2. `python scripts/deploy_search.py --config deploy.config.json`.
3. Reset + run the indexer.

### Delete one PDF's records

Query for its ids, then batch-delete:

```json
{ "search": "*", "filter": "source_file eq 'OLD.pdf'", "select": "id", "top": 10000 }
```

```http
POST /indexes/<prefix>-index/docs/index?api-version=2024-11-01-preview
{ "value": [ { "@search.action": "delete", "id": "<record-id>" } ] }
```

### Index health

```bash
python scripts/check_index.py --config deploy.config.json
```

Shows total docs, per-type breakdown, flags fields that are 100% null
(schema vs. skillset drift signal).

### Indexer status

```bash
az rest --method get \
  --url "https://<search>.search.windows.net/indexers/<prefix>-indexer/status?api-version=2024-11-01-preview" \
  -o json
```

---

## 13. Troubleshooting

| Symptom                                                      | Likely cause                                               | What to check |
|--------------------------------------------------------------|------------------------------------------------------------|---------------|
| Vector search returns nothing                                | Dim mismatch                                               | `text_vector.dimensions == 1536`; AOAI deployment is ada-002. |
| Text chunks have `physical_pdf_page: null`                   | Old records from before the DI-cache fallback fix          | Redeploy function app, reset + run indexer. |
| Diagrams have `diagram_description: ""`                      | Vision failed (rate limit or content filter)               | Inspect `processing_status`; re-run preanalyze for transient failures. |
| Indexer shows 0 docs after 30 min                            | Fired before `.pdf`-only filter was deployed, or first big PDF still processing | Execution history → cancel + reset + run. |
| Blob count mismatch vs. indexer count                        | `az storage blob list` default 5000-item cap               | Use `--num-results *` or count via REST. |
| `Execution time quota of 120 minutes reached`                | Normal for large fresh loads                               | The 15-minute schedule auto-resumes; nothing to do. |
| `Unrendered placeholders remain: …`                          | Missing key in `deploy.config.json`                        | Fill the matching field or extend the mapping in `scripts/deploy_search.py`. |
| Skillset PUT fails with 403 on AI Services                   | Search MI missing `Cognitive Services User` on AI Services | Add role; wait ~5 min for propagation. |
| Function skill returns 401 / 403 from AOAI or DI             | Function App MI missing role on AOAI / DI                  | Add the roles listed in §3. |
| Updated PDF still serves old content                         | Cache was not invalidated; preanalyze `--incremental` skipped because `output.json` exists | See §15.5 — the LMT-aware invalidation pattern. |
| `content_filter` on a legitimate figure                      | Vision safety false-positive                               | Cached as permanent; record still indexed without description. Manual review if needed. |

Log sources:

- Search Service → Indexers → Execution history → Errors
- Function App → Log stream / Application Insights (`az webapp log tail`)
- `python scripts/preanalyze.py --status` (cache state per PDF)
- `python scripts/diagnose.py` (misc checks)

---

## 14. CI / Testing

Runs on every PR and push to `main` via `.github/workflows/ci.yml`:

- `python tests/test_unit.py`
- `python tests/test_e2e_simulator.py`
- `ruff check function_app tests scripts`

Tests require no Azure credentials. Make these required checks in
branch protection.

Local dev against real Azure:

```bash
cp function_app/local.settings.json.example function_app/local.settings.json
# Fill in endpoints; AUTH_MODE=mi uses your az login credential chain.
cd function_app && func start
```

---

## 15. Production automation — add / update / delete

In production the operator never runs preanalyze or the indexer
manually. SharePoint→blob is automated upstream; everything from the
blob container onwards must also be automated. This section is the
target architecture and the rationale.

### 15.1 The correctness problem to be aware of

A naïve "cron every 30 min runs preanalyze and triggers the indexer"
loop has one silent bug:

| Event | Blob emits | What we need to do | Naïve cron behaviour |
|---|---|---|---|
| **Add** (new PDF) | `BlobCreated` | preanalyze → index | ✅ `--incremental` handles it |
| **Update** (overwrite, same name) | `BlobCreated` (new LMT) | invalidate cache → re-preanalyze → re-index | ❌ `--incremental` skips because `_dicache/<pdf>.output.json` already exists. Indexer reads stale cache. |
| **Delete** | `BlobDeleted` | drop records from index + drop `_dicache/<pdf>.*` | ✅ if blob soft-delete is enabled and the indexer's `NativeBlobSoftDeleteDeletionDetectionPolicy` is in place |

The update case is the killer — pure cron silently serves stale
content after any PDF overwrite. The recommended architecture solves
it explicitly.

### 15.2 Recommended architecture: event-driven + nightly reconciliation

```
Storage Account  (blob soft-delete ON)
   │
   ├── Event Grid System Topic
   │      ├── Subscription: BlobCreated   (subject endsWith ".pdf",
   │      │                                 NOT under "_dicache/")
   │      └── Subscription: BlobDeleted   (same filter)
   │                  │
   │                  ▼
   │         Storage Queue  (visibility 5 min, 5 dequeues → DLQ)
   │                  │
   │                  ▼
   │   Container App Job — event-triggered (KEDA queue-length scaler)
   │     replicas: min 0, max 2-3   (bounded by AOAI TPM)
   │     Created  -> invalidate cache for that PDF
   │                 -> preanalyze --only <pdf> --force
   │                 -> POST /indexers/<prefix>-indexer/run
   │     Deleted  -> remove _dicache/<pdf>.* blobs
   │                 (index cleaned by deletion policy on next indexer run)
   │
   └── Container App Job — cron 0 2 * * *   (nightly reconciliation)
          preanalyze --incremental    (catch any events that were missed)
          preanalyze --cleanup         (orphan _dicache/ sweep)
          POST /indexers/<prefix>-indexer/run

   Indexer schedule: PT1H   (drops from PT15M; becomes a 3rd safety net)
```

Two Container App Jobs, one queue, one Event Grid subscription. That's
the whole production surface area.

### 15.3 Why these specific choices

**Why Container Apps Jobs and not Azure Functions**

PDFs in scope go up to 2,000 pages / 6,000 figures, which the
architecture doc clocks at 2–4 hours end to end. Consumption Functions
die at 10 min, Premium at 30 min — both will fail mid-vision on a real
manual and leave half-written cache. Container Apps Jobs have **no
execution time limit**, pay-per-run, and KEDA scales them on queue
length. This is the right tool for the workload.

**Why event-driven, not pure cron**

- **Update correctness:** `BlobCreated` fires on overwrite, so the
  worker can force-invalidate the cache per event. Pure cron cannot
  distinguish "unchanged" from "overwritten" without extra LMT logic.
- **Latency:** 5–15 min upload-to-searchable instead of 30–60 min.
- **Cost shape:** worker sleeps at $0 when the queue is empty, which
  is most of the day for a weekly-ingest workload.
- **Burst absorption:** the queue smooths a SharePoint bulk upload
  into a steady stream the worker can drain without hammering AOAI.

**Why the nightly reconciliation sweep is non-negotiable**

Event Grid is **at-least-once but not guaranteed-once**. Delivery does
fail (region blips, subscription misconfig, transient errors past
retry budget). The 2 a.m. `--incremental` sweep catches anything that
slipped through, for ~$0.05/day of compute. Don't skip it.

**Why keep an indexer schedule at all**

Even with events + nightly sweep, the indexer's own `PT1H` schedule
is a third independent safety net. If both the event path and the
nightly job fail, the worst-case lag is one hour, not "until someone
notices". Cost is negligible (the indexer is a no-op when nothing
changed).

### 15.4 Pros and cons

**Pros**

- Correct for add / update / delete without manual intervention.
- 5–15 min end-to-end latency from upload to searchable.
- Self-healing: events → nightly sweep → `PT1H` indexer schedule (three
  layers before a missed event becomes user-visible).
- Bounded AOAI cost — replica cap caps concurrent vision calls below
  TPM ceiling.
- Works unchanged for the 2,000-page edge-case PDFs.
- Handles bursts (SharePoint bulk upload) via the queue buffer
  instead of overwhelming AOAI in parallel.

**Cons**

- More infra than a single cron: Event Grid topic, subscription,
  queue, DLQ, two Container App Jobs, alerts (~1–2 days to stand up
  cleanly).
- Two small code changes in `preanalyze.py` are needed before this is
  fully turnkey — see §15.5.
- DLQ requires monitoring; poison PDFs (corrupt, password-protected)
  must be surfaced, not silently retried forever.
- One 2,000-page PDF ties up a worker for ~3 hrs; bursts queue behind
  it until a second replica spins up.
- Extra auth surface: the Container App Jobs need their own MI role
  grants (storage, queue, AOAI, DI, search — listed in §3).

### 15.5 Code gaps to close in `preanalyze.py`

Localized changes, ~100 LOC total. Required before §15.2 is turnkey:

1. **`--only <blob-name>`** flag — process a single PDF instead of
   scanning the whole container. The event-driven worker invokes it
   per blob event.
2. **LMT-aware invalidation** — when running against a specific PDF,
   if `pdf.lastModified > output.json.lastModified`, treat the cache
   as stale and re-run even under `--incremental`. Makes both the
   event path and the nightly sweep robust against updates.
3. **Per-PDF cleanup helper** — a small function that deletes
   `_dicache/<pdf>.*` for one name. Used by the event worker on
   `BlobDeleted`, and reusable from `--cleanup`.

None of these are structural changes — same module, same I/O
surface, no new Azure SDK dependencies.

### 15.6 Two-phase rollout (recommended)

**Phase 1 — make it correct on a cron (this week):** add the
LMT-aware invalidation to `preanalyze.py`, deploy a single
Container App Job on cron every 30 min running:

```
preanalyze.py --incremental
preanalyze.py --cleanup
POST /indexers/<prefix>-indexer/run
```

This is correct for add / update / delete with ≤ 30 min worst-case
lag. One piece of infra, one MI to grant. Most teams stop here.

**Phase 2 — add the event path (when latency matters):** stand up
Event Grid + Storage Queue + the event-triggered Container App Job
in front of it. The cron job from Phase 1 stays as the nightly
reconciliation sweep, untouched. No rework — Phase 2 is an additive
fast path, not a replacement.

Ship correctness before latency. Only graduate to Phase 2 if the
business actually complains about 30-minute lag.

### 15.7 Required prerequisites checklist

Before turning automation on:

- [ ] Blob soft-delete is enabled on the storage account
- [ ] `dataDeletionDetectionPolicy` is present in
      `search/indexer.json` (it already is — verify after any redeploy)
- [ ] LMT-aware invalidation is implemented in `preanalyze.py`
      (Phase 1 minimum)
- [ ] `--only <pdf>` and per-PDF cleanup helpers exist
      (Phase 2 prerequisite)
- [ ] Container App Job MI has the role grants in §3
- [ ] Event Grid subscription filters: `subject` endsWith `.pdf` AND
      `subject` does NOT contain `/_dicache/` (otherwise vision
      caching writes will trigger preanalyze of themselves)
- [ ] DLQ has an alert wired to the team's on-call channel
- [ ] Job stdout is shipped to Log Analytics; non-zero exit triggers
      an alert

---

## 16. Licensing note

`PyMuPDF` (used for figure cropping in `shared/pdf_crop.py`) is
**AGPL-3.0**. For a closed-source internal Azure Function App this is
generally fine — the AGPL network clause is triggered by distributing
modified source, not by running the library behind a function
endpoint. If this pipeline ships as part of a public SaaS, review
PyMuPDF's terms or swap to `pypdfium2`.

---

## 17. Quick reference — commands

```bash
# Bootstrap
cp deploy.config.example.json deploy.config.json
az login
python scripts/preflight.py --config deploy.config.json

# Deploy function + search artifacts
scripts/deploy_function.sh deploy.config.json
python scripts/deploy_search.py --config deploy.config.json

# Preanalyze (offline DI + vision caching)
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output

# Run + validate
python scripts/deploy_search.py --config deploy.config.json --run-indexer
python scripts/smoke_test.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json

# Reset + re-run indexer
.\scripts\reset_indexer.ps1

# Rotate function key
az functionapp keys set -g <rg> -n <func> --key-type functionKeys --key-name default
python scripts/deploy_search.py --config deploy.config.json
```

---

## 18. Related docs

- [README.md](../README.md) — top-level project readme
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — design rationale, why
  preanalyze, scaling envelope
- [docs/SEARCH_INDEX_GUIDE.md](SEARCH_INDEX_GUIDE.md) — index concepts
  + schema reference for non-search engineers
- [docs/validation.md](validation.md) — manual validation checklist
- [scripts/PREANALYZE_README.md](../scripts/PREANALYZE_README.md) —
  team-facing preanalyze runbook
- [search/index.json](../search/index.json) / [skillset.json](../search/skillset.json)
  / [indexer.json](../search/indexer.json) / [datasource.json](../search/datasource.json)
  — the actual search artifact bodies
