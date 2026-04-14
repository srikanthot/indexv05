# Azure AI Search — Multimodal Manual Indexing

Production indexing pipeline for diagram-heavy technical manuals on
Azure AI Search. One multimodal index holds **text**, **diagram**,
**table**, and **summary** records as peers, all embedded with
Azure OpenAI Ada-002 and queryable with a built-in vectorizer.

Custom logic runs as Python Azure Functions exposed through Custom
WebApi skills. Diagram analysis and document summaries use **gpt-4.1**.

**Scope:** this repository owns only the indexing application layer —
function code, search artifacts, deployment scripts, tests. It does
**not** provision Azure resources. The Azure resources (Storage, Search
service, Azure OpenAI, Document Intelligence, AI Services multi-service
account, Function App, App Insights) are expected to already exist and
are referenced from [`deploy.config.json`](deploy.config.example.json).

## Architecture

```
Blob (PDFs)
   |
   v
Data Source -> Indexer -> Skillset -> Index
                            |
                            +-- DocumentIntelligenceLayoutSkill (markdown text path)
                            +-- SplitSkill (1200 / 200)
                            +-- WebApi: process-document   -> Function -> DI direct -> figures + tables
                            +-- WebApi: extract-page-label -> Function
                            +-- WebApi: analyze-diagram    -> Function (per figure, hash-cached, gpt-4.1 vision)
                            +-- WebApi: shape-table        -> Function (per table)
                            +-- WebApi: build-semantic-string -> Function (text + diagram modes)
                            +-- WebApi: build-doc-summary  -> Function (gpt-4.1)
                            +-- AOAI Embedding x4 (text / figures / tables / summary)
```

Four peer record types are projected into the index:

| record_type | sourceContext                          | chunk_id prefix |
|-------------|----------------------------------------|-----------------|
| text        | /document/markdownDocument/*/pages/*   | `txt_`          |
| diagram     | /document/enriched_figures/*           | `dgm_`          |
| table       | /document/enriched_tables/*            | `tbl_`          |
| summary     | /document                              | `sum_`          |

Text and table records carry `physical_pdf_pages: Collection(Edm.Int32)`
— the full sorted list of every page the chunk covers, for citation
and page-highlight UIs.

## Repository layout

```
function_app/                   Python Functions app (the WebApi skills)
  function_app.py
  host.json
  requirements.txt
  local.settings.json.example
  shared/
    skill_io.py                 WebApi envelope + error translation
    credentials.py              Managed identity helper (lazy)
    config.py                   Typed env-var access with ConfigError
    aoai.py                     Azure OpenAI client (MI-first)
    di_client.py                Document Intelligence REST + blob fetch (MI-first)
    search_cache.py             Image-hash cache lookup (MI-first)
    ids.py                      Stable chunk_id helpers
    page_label.py               Printed page label + physical page span
    sections.py                 DI section index + surrounding-text extractor
    pdf_crop.py                 PyMuPDF figure cropping
    tables.py                   DI tables -> markdown (merge + split)
    process_document.py         Orchestrates DI + crop + sections + tables
    process_table.py            Per-table shaper
    semantic.py                 chunk_for_semantic builder (text + diagram)
    diagram.py                  Per-figure vision analysis (hash-cached)
    summary.py                  Per-document summary

search/                         Azure AI Search REST bodies (templated)
  datasource.json
  index.json
  skillset.json
  indexer.json

scripts/
  deploy_function.sh / .ps1     Publish function code + apply App Settings
  deploy_search.py              Render + PUT search artifacts via AAD
  smoke_test.py                 Post-deploy validation

tests/
  test_unit.py                  Unit checks (79 assertions)
  test_e2e_simulator.py         Full handler-side end-to-end simulation

docs/
  validation.md                 Manual + automated validation checklist

deploy.config.example.json      Template — copy to deploy.config.json
.github/workflows/ci.yml        Tests + lint on every PR
ruff.toml
README.md
```

## Prerequisites

These Azure resources must exist before you deploy. The repo does not
create them:

- **Azure AI Search** (Basic or higher; Standard recommended for prod).
  System-assigned managed identity enabled.
- **Azure Storage** account + a container for source PDFs. Shared-key
  auth can be disabled.
- **Azure OpenAI** with two deployments:
  - `text-embedding-ada-002` (1536 dims)
  - `gpt-4.1` (vision-capable; used for both diagrams and summaries)
- **Azure Document Intelligence** (prebuilt-layout).
- **Azure AI Services** multi-service account (billing for the built-in
  Layout skill — referenced by `AIServicesByIdentity`).
- **Azure Function App** — Linux, Python 3.11, Functions v4, with
  system-assigned managed identity enabled.
- **Application Insights** component (recommended).

### Required role assignments

All auth is AAD / managed identity. Create these once:

| Principal | Scope | Role |
|---|---|---|
| Function App MI | Storage account | Storage Blob Data Reader |
| Function App MI | Azure OpenAI | Cognitive Services OpenAI User |
| Function App MI | Document Intelligence | Cognitive Services User |
| Function App MI | Search service | Search Index Data Reader |
| Search service MI | Storage account | Storage Blob Data Reader |
| Search service MI | Azure OpenAI | Cognitive Services OpenAI User |
| Search service MI | AI Services account | Cognitive Services User |
| Deploying principal | Search service | Search Service Contributor + Search Index Data Contributor |

The deploying principal also needs `Contributor` on the Function App's
resource group (to set App Settings and fetch the function key).

## Deploy

Single flow, same every time. No portal clicks.

### 1. Configure

```bash
cp deploy.config.example.json deploy.config.json
# Fill in the resource identifiers for your environment
```

Every script reads from this file. It is the only thing that changes
between environments.

### 2. Deploy the Function App code

```bash
az login
scripts/deploy_function.sh deploy.config.json
# Windows:
# .\scripts\deploy_function.ps1 -Config .\deploy.config.json
```

This publishes the function package and applies the required App
Settings (`AUTH_MODE=mi`, `AOAI_*`, `DI_*`, `SEARCH_*`,
`SKILL_VERSION`, App Insights connection string).

### 3. Create / update the Search objects

```bash
python scripts/deploy_search.py --config deploy.config.json
```

Renders every `<PLACEHOLDER>` in `search/*.json` from the config +
live-fetched function key, then `PUT`s the four artifacts (datasource,
index, skillset, indexer) using AAD. The script is idempotent and fails
loud if any placeholder is left unrendered.

### 4. Run the indexer + validate

```bash
python scripts/deploy_search.py --config deploy.config.json --run-indexer
python scripts/smoke_test.py --config deploy.config.json
```

The smoke test triggers the indexer, waits for `status=success`, then
asserts record counts, required fields, and that `physical_pdf_pages`
covers the declared start + end on text/table records. Non-zero exit
on any failure, so CI can gate on it.

## Configuration reference

Everything the scripts need is in `deploy.config.json`:

| Key | Purpose |
|---|---|
| `functionApp.name` / `resourceGroup` | Target Function App |
| `search.endpoint` | `https://<svc>.search.windows.net` |
| `search.artifactPrefix` | Prefix for `*-ds`, `*-index`, `*-skillset`, `*-indexer` (defaults to `mm-manuals`) |
| `azureOpenAI.endpoint` / `apiVersion` | AOAI endpoint (use `2024-12-01-preview` or newer for gpt-4.1) |
| `azureOpenAI.chatDeployment` / `visionDeployment` / `embedDeployment` | Deployment names |
| `documentIntelligence.endpoint` / `apiVersion` | DI resource |
| `aiServices.subdomainUrl` | AI Services multi-service endpoint used by the built-in Layout skill |
| `storage.accountResourceId` | Full ARM ID (`/subscriptions/…/storageAccounts/<name>`) — used for the `ResourceId=…` datasource connection string |
| `storage.pdfContainerName` | Container with source PDFs |
| `appInsights.connectionString` | Wired into the Function App for telemetry |
| `skillVersion` | Stamped on every record; bump to invalidate the image-hash cache |

The function key embedded in the skillset is fetched live by
`deploy_search.py` — it never sits in the config file.

## Local development

Tests run with no Azure credentials:

```bash
python tests/test_unit.py
python tests/test_e2e_simulator.py
```

To run the function locally against real Azure services, copy
`function_app/local.settings.json.example` to
`function_app/local.settings.json`, fill in endpoints, and
`func start`. `AUTH_MODE=mi` uses your `az login` credential chain;
set `AUTH_MODE=key` and populate the `*_API_KEY` fields only if you
must run without AAD.

## Validation

See [`docs/validation.md`](docs/validation.md) for the full checklist.
The mechanical checks are automated by `scripts/smoke_test.py`; the
retrieval-quality checks are manual spot-checks against the deployed
index.

## Operations

### Re-index a single file

Indexer change detection is high-water-mark on
`metadata_storage_last_modified`. Rewriting the blob (same content, new
timestamp) forces a re-pickup.

### Full re-index

```bash
az rest --method post \
  --url "https://<search>.search.windows.net/indexers/<prefix>-indexer/reset?api-version=2024-05-01-preview"
python scripts/deploy_search.py --config deploy.config.json --run-indexer
```

### Rotate the function key

```bash
az functionapp keys set -g <rg> -n <func> \
  --key-type functionKeys --key-name default
python scripts/deploy_search.py --config deploy.config.json
```

The skillset is re-PUT with the new key; no code change needed.

### Bump `skillVersion`

Edit `deploy.config.json`, re-run `scripts/deploy_function.sh`. Any
record re-processed from that point stamps the new version; older
records keep the old one until touched.

## Security

- All outbound service calls (Azure OpenAI, Document Intelligence,
  Storage, Search) use AAD bearer tokens from the Function App's
  managed identity by default (`AUTH_MODE=mi`).
- Search artifacts use identity-based auth: datasource uses
  `ResourceId=…` (no keys), embedding skill / vectorizer omit `apiKey`
  so they use the Search service MI, and the `cognitiveServices` block
  uses `AIServicesByIdentity`.
- The one remaining secret is the function key embedded in the
  skillset. Rotate by re-running `scripts/deploy_search.py`.
- API keys remain supported for local dev only via `AUTH_MODE=key`.

## CI

`.github/workflows/ci.yml` runs on every PR and every push to `main`:

- `python tests/test_unit.py`
- `python tests/test_e2e_simulator.py`
- `ruff check function_app tests scripts`

Enforce these as required checks in GitHub branch protection so broken
builds cannot merge.
