# Azure AI Search — Multimodal Manual Indexing (v2.2)

Azure AI Search–native pipeline for diagram-heavy technical manuals. One multimodal index holds **text**, **diagram**, **table**, and **summary** records as peers — all embedded with Ada-002 and re-queryable via a built-in Azure OpenAI vectorizer.

Custom logic runs in an Azure Function exposed as Custom WebApi skills.

## v2.2 targeted fixes (over v2.1)

| Fix | What changed |
|---|---|
| **chunk_id collision** | `text_chunk_id` now mixes a `chunk_content_hash` of the chunk text instead of a hardcoded `0`, so multiple SplitSkill pages produced from the same layout section get distinct, deterministic IDs. New `chunk_content_hash` and `table_chunk_id` helpers. |
| **`table_caption` first-class** | Added `table_caption` field to the index, populated by `process_table`, projected by the table selector, and added to the semantic config keywords priority list. Tables no longer reuse `figure_ref` for captions. |
| **OData injection hardening** | `search_cache.lookup_existing_by_hash` now (a) escapes single quotes in OData literals, (b) whitelists the parent_id/image_hash inputs against `^[A-Za-z0-9_\-]+$`, (c) limits the SELECT to a fixed list of fields known to exist in the index, (d) is feature-gated and silently no-ops if the search env vars are not set. |
| **Env validation** | New `shared/config.py` with `required_env`/`optional_env`/`feature_enabled` + `ConfigError`. `aoai.py` and `di_client.py` now route through it. `skill_io.handle_skill_request` translates `ConfigError` into a clear per-record `processing_status=config_error` instead of a 500. |
| **Local end-to-end simulator** | New `tests/test_e2e_simulator.py` drives the actual handler functions through the same JSON envelope Azure AI Search sends and emits one finalized record of every type (text / multi-page text / diagram / table / summary), then cross-checks every projected field against `index.json`. |

## v2.1 targeted fixes (over v2)

| Fix | What changed |
|---|---|
| **Multi-page text spans** | `extract-page-label` skill now parses DI's `<!-- PageNumber="N" -->` and `<!-- PageBreak -->` markers in the section markdown to compute real `physical_pdf_page` and `physical_pdf_page_end` per chunk. The skill now also takes `section_content` as input. |
| **OCR path removed** | The built-in `OcrSkill` and `MergeSkill` are gone. They were only feeding `ocr_fallback_text`, which was never on the semantic priority list and added no retrieval value over DI Layout's markdown. Removed `ocr_fallback_text` field, projection mapping, and `merged_text` input on the summary skill. |
| **Diagram semantic input renamed** | `build-semantic-string-diagram` now takes `context_text` (was misleadingly named `ocr_text`). The string label changed from "Visible text:" to "Context:". |
| **Tables drop misleading `figure_ref`** | `process_table` no longer reuses the `figure_ref` field for the table caption. The caption is rendered into `chunk_for_semantic` instead. Removed from the projection mapping. |
| **Dead `chunk_hash` field removed** | Was added speculatively, never populated. |

## What v2 already changed vs v1

| Capability | v1 | v2 |
|---|---|---|
| Diagram extraction | one vision call per PDF page (multi-figure pages collapse) | one vision call **per figure**, cropped from the PDF |
| Diagram → section linking | none | `header_1/2/3` populated from DI section index |
| `surrounding_context` | field existed, never populated | populated from the section text around the figure caption |
| Tables | sent through vision (lossy prose) | first-class `record_type=table` with structured markdown, multi-page merging, oversized split |
| Vision prompt | image + OCR hint only | image + section path + page + caption + body figure refs + surrounding text |
| Image hash caching | none | re-index skips vision calls when `parent_id + image_hash` already exists |
| Doc summary source | OCR-merged text | DI markdown content |
| Query-time vectorizer | none | Azure OpenAI vectorizer attached to the HNSW profile |
| Dead `chunk_type` field | present | removed |

## Architecture

```
Blob (PDFs)
   |
   v
Data Source -> Indexer -> Skillset -> mm-manuals-index
                            |
                            +-- DocumentIntelligenceLayoutSkill (built-in: markdown text path)
                            +-- SplitSkill (1200 / 200)
                            +-- WebApi: process-document   --> Function -> DI direct -> figures + tables
                            +-- WebApi: extract-page-label --> Function
                            +-- WebApi: analyze-diagram    --> Function (per figure, hash-cached, vision-enriched)
                            +-- WebApi: shape-table        --> Function (per table)
                            +-- WebApi: build-semantic-string --> Function (text + diagram modes)
                            +-- WebApi: build-doc-summary  --> Function (markdown source)
                            +-- AOAI Embedding (text pages)
                            +-- AOAI Embedding (figures)
                            +-- AOAI Embedding (tables)
                            +-- AOAI Embedding (summary)
```

Index projections write four peer record types into `mm-manuals-index`:

| record_type | sourceContext                          | chunk_id prefix |
|-------------|----------------------------------------|-----------------|
| text        | /document/markdownDocument/*/pages/*   | `txt_`          |
| diagram     | /document/enriched_figures/*           | `dgm_`          |
| table       | /document/enriched_tables/*            | `tbl_`          |
| summary     | /document                              | `sum_`          |

## Repository layout

```
search/         Azure AI Search REST bodies (datasource, index, skillset, indexer)
function_app/   Python v2 Azure Function App with 6 custom skill routes
  shared/
    skill_io.py            Skill request/response envelope
    ids.py                 Stable chunk_id helpers
    aoai.py                Azure OpenAI client
    di_client.py           Azure Document Intelligence REST client + blob fetch
    pdf_crop.py            PyMuPDF figure cropping
    sections.py            DI section index + surrounding context
    tables.py              DI tables -> markdown (with multi-page merge + split)
    search_cache.py        Image-hash cache lookup against the index
    process_document.py    Orchestrates DI + crop + sections + tables
    process_table.py       Per-table shaper
    page_label.py          Printed page label extractor
    semantic.py            chunk_for_semantic builder (text + diagram modes)
    diagram.py             Per-figure vision analysis (hash-cached)
    summary.py             Per-document summary (markdown source)
docs/           Setup + validation checklists
```

## Prerequisites

- Azure AI Search service
- Azure Blob Storage account with a container of source PDFs
- Azure AI Services (Cognitive Services multi-service) key
- Azure Document Intelligence resource (prebuilt-layout, API `2024-11-30`)
- Azure OpenAI with:
  - `text-embedding-ada-002` deployment (1536 dims)
  - vision-capable chat deployment (e.g. gpt-4o)
- Azure Function App: Linux, Python 3.11, Functions v4
- A SAS token (or public read) so the Function App can fetch PDFs from blob to send to DI

See `docs/setup.md` for permissions and required env vars.

## Function App settings

Set in App Settings (mirrors `local.settings.json.example`):

| Setting | Purpose |
|---|---|
| `AOAI_ENDPOINT` / `AOAI_API_KEY` / `AOAI_API_VERSION` | Azure OpenAI |
| `AOAI_VISION_DEPLOYMENT` | Vision chat deployment for figures |
| `AOAI_CHAT_DEPLOYMENT` | Chat deployment for summaries |
| `DI_ENDPOINT` / `DI_API_KEY` / `DI_API_VERSION` | Document Intelligence direct call |
| `STORAGE_BLOB_SAS` | Container-level SAS so the function can fetch source PDFs by URL |
| `SEARCH_ENDPOINT` / `SEARCH_ADMIN_KEY` / `SEARCH_INDEX_NAME` | Image-hash cache lookup |
| `SKILL_VERSION` | Stamped on every record (e.g. `2.0.0`) |

## Deployment / build order

1. **Deploy the Azure Function App** from `function_app/`
   ```bash
   cd function_app
   func azure functionapp publish <FUNCTION_APP_NAME>
   ```
   Then set the App Settings listed above.

2. **Capture the function host and a function key**
   - Host: `https://<FUNCTION_APP_NAME>.azurewebsites.net`
   - Key: Function App -> App keys -> default

3. **Replace placeholders** in `search/skillset.json`:
   - `<FUNCTION_APP_HOST>`, `<FUNCTION_KEY>` (multiple places)
   - `<AOAI_ENDPOINT>`, `<AOAI_API_KEY>`, `<AOAI_EMBED_DEPLOYMENT>`
   - `<AI_SERVICES_KEY>`

   And in `search/index.json`:
   - `<AOAI_ENDPOINT>`, `<AOAI_API_KEY>`, `<AOAI_EMBED_DEPLOYMENT>` (vectorizer block)

   And in `search/datasource.json`:
   - `<STORAGE_CONNECTION_STRING>`, `<STORAGE_CONTAINER_NAME>`

4. **Create the data source**
   ```
   PUT {SEARCH_ENDPOINT}/datasources/mm-manuals-ds?api-version=2024-05-01-preview
   ```

5. **Create the index**
   ```
   PUT {SEARCH_ENDPOINT}/indexes/mm-manuals-index?api-version=2024-05-01-preview
   ```

6. **Create the skillset**
   ```
   PUT {SEARCH_ENDPOINT}/skillsets/mm-manuals-skillset?api-version=2024-05-01-preview
   ```

7. **Create the indexer**
   ```
   PUT {SEARCH_ENDPOINT}/indexers/mm-manuals-indexer?api-version=2024-05-01-preview
   ```

8. **Run a Debug Session** in the Azure portal against one PDF. Verify:
   - The built-in Layout skill emits the markdown paths the text projection uses (`pageNumber`, `sections/h1..h3`, `ordinal_position`).
   - The `process-document` skill returns non-empty `enriched_figures[]` and `enriched_tables[]`.
   - Each figure record has `dgm_header_1/2/3` and `dgm_surrounding_context` populated.

9. **Run the indexer** on a single sample manual:
   ```
   POST {SEARCH_ENDPOINT}/indexers/mm-manuals-indexer/run?api-version=2024-05-01-preview
   ```

10. **Inspect** results using `docs/validation.md`.

## Validation (must pass before going wider)

- Multi-figure page → multiple `record_type=diagram` records (not one).
- Diagram records have `header_1/2/3` populated.
- Diagram records have `surrounding_context` populated.
- Specification table → `record_type=table` with full markdown grid (not a vision description).
- Multi-page table → single record with `physical_pdf_page < physical_pdf_page_end`.
- Multi-page text chunk → record with `physical_pdf_page < physical_pdf_page_end` (DI page markers parsed by `extract-page-label`).
- Re-running the indexer on an unchanged PDF → vision call count drops (cache hits visible as `processing_status=cache_hit`).
- Vector query against `text_vector` works **without** client-side embedding (the vectorizer handles it).
- All `chunk_id` values are unique with the correct prefix (`txt_`, `dgm_`, `tbl_`, `sum_`).
- No `ocr_fallback_text` field in the index — the OCR path was removed in v2.1.

## Local tests

Two test files run without any Azure credentials:

```bash
python tests/test_unit.py             # 68 unit assertions
python tests/test_e2e_simulator.py    # full handler-side end-to-end run
```

`test_unit.py` covers the deterministic helpers: page-span parser,
section index walking, table extractor with multi-page merge, semantic
string builders, chunk_id helpers (including the v2.2 collision
regression), OData escaping, and config error handling.

`test_e2e_simulator.py` drives the actual handler functions through the
exact JSON envelope Azure AI Search sends, with a stub for the AOAI
vision/chat call and the search-cache REST call. It produces one
finalized record of each type (text, multi-page text, diagram, table,
summary), validates page spans, and confirms every projected field
exists in `index.json`.

## Open TODOs / blockers

- **Layout skill output paths** (`pageNumber`, `sections/h1..h3`, `ordinal_position`) on the built-in skill must be confirmed against the actual JSON in your region. The `process-document` custom skill bypasses this entirely for the figure/table path.
- **Blob fetch auth**: simplest path is a container SAS in `STORAGE_BLOB_SAS`. For production, prefer a managed identity on the Function App with Storage Blob Data Reader.
- **End-page mapping for text chunks**: still set equal to start. Wire in real spans if Layout exposes them.
- **Vision model availability** in Gov / sovereign cloud regions must be confirmed.

## Placeholders to replace

| Placeholder | Where | What to put |
|---|---|---|
| `<STORAGE_CONNECTION_STRING>` | `search/datasource.json` | Blob storage connection string |
| `<STORAGE_CONTAINER_NAME>` | `search/datasource.json` | Container holding source PDFs |
| `<FUNCTION_APP_HOST>` | `search/skillset.json` | e.g. `myfuncapp.azurewebsites.net` |
| `<FUNCTION_KEY>` | `search/skillset.json` | Function App function key |
| `<AOAI_ENDPOINT>` | `search/skillset.json`, `search/index.json`, Function settings | e.g. `https://myaoai.openai.azure.com/` |
| `<AOAI_API_KEY>` | `search/skillset.json`, `search/index.json`, Function settings | Azure OpenAI key |
| `<AOAI_EMBED_DEPLOYMENT>` | `search/skillset.json`, `search/index.json` | Ada-002 deployment name |
| `<AOAI_VISION_DEPLOYMENT>` | Function settings | Vision chat deployment (e.g. gpt-4o) |
| `<AOAI_CHAT_DEPLOYMENT>` | Function settings | Chat deployment for summaries |
| `<DI_ENDPOINT>` / `<DI_API_KEY>` | Function settings | Document Intelligence resource |
| `<AI_SERVICES_KEY>` | `search/skillset.json` | AI Services multi-service key |
| `<SEARCH_ENDPOINT>` / `<SEARCH_ADMIN_KEY>` | Function settings | For image-hash cache lookup |
| `STORAGE_BLOB_SAS` | Function settings | Container SAS so the function can fetch PDFs |
