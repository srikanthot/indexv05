# Azure AI Search — Multimodal Manual Indexing

Azure AI Search–native pipeline for indexing technical manuals into a single multimodal index. Text chunks, diagram chunks, and per-document summary chunks live as **peer records** in one index, all embedded with Ada-002 and ranked with a semantic configuration that does not let OCR fallback dominate.

Custom logic (printed page labels, semantic-string assembly, vision diagram analysis, document summary) runs in an Azure Function exposed to the skillset as four Custom WebApi skills.

## Architecture

```
Blob (PDFs)
   |
   v
Data Source  ->  Indexer  ->  Skillset  ->  mm-manuals-index
                                |
                                +-- DocumentIntelligenceLayoutSkill
                                +-- OcrSkill (fallback only)
                                +-- MergeSkill (ocr_fallback_text)
                                +-- SplitSkill (1200 / 200)
                                +-- WebApi: extract-page-label    --> Function
                                +-- WebApi: analyze-diagram       --> Function
                                +-- WebApi: build-semantic-string --> Function
                                +-- WebApi: build-doc-summary     --> Function
                                +-- AOAI Embedding (text pages)
                                +-- AOAI Embedding (diagrams)
                                +-- AOAI Embedding (summary)
```

Index projections write three peer record types into `mm-manuals-index`:

| record_type | sourceContext                          | chunk_id prefix |
|-------------|----------------------------------------|-----------------|
| text        | /document/markdownDocument/*/pages/*   | txt_            |
| diagram     | /document/normalized_images/*          | dgm_            |
| summary     | /document                              | sum_            |

## Repository layout

```
search/         Azure AI Search REST bodies (datasource, index, skillset, indexer)
function_app/   Python v2 Azure Function App with 4 custom skill routes
docs/           Setup + validation checklists
```

## Prerequisites

- Azure AI Search service
- Azure Blob Storage account with a container of source PDFs
- Azure AI Services (Cognitive Services multi-service) key
- Azure OpenAI with:
  - text-embedding-ada-002 deployment (1536 dims)
  - a vision-capable chat deployment (e.g. gpt-4o) used by both diagram analysis and document summary
- Azure Function App: Linux, Python 3.11, Functions v4

See `docs/setup.md` for permissions and required env vars.

## Deployment / build order

Follow this order exactly. Each step depends on the previous.

1. **Deploy the Azure Function App** from `function_app/`
   ```bash
   cd function_app
   func azure functionapp publish <FUNCTION_APP_NAME>
   ```
   Then in the Azure portal, set the App Settings listed in `local.settings.json.example` (with real values).

2. **Capture the function host and a function key**
   - Host: `https://<FUNCTION_APP_NAME>.azurewebsites.net`
   - Key:  Function App -> App keys -> default (or per-function key)

3. **Replace placeholders** in `search/skillset.json`:
   - `<FUNCTION_APP_HOST>`  -> `<FUNCTION_APP_NAME>.azurewebsites.net`
   - `<FUNCTION_KEY>`       -> the function key
   - `<AOAI_ENDPOINT>`, `<AOAI_API_KEY>`, `<AOAI_EMBED_DEPLOYMENT>`
   - `<AI_SERVICES_KEY>`

   And in `search/datasource.json`:
   - `<STORAGE_CONNECTION_STRING>`
   - `<STORAGE_CONTAINER_NAME>`

4. **Create the data source**
   ```
   PUT {SEARCH_ENDPOINT}/datasources/mm-manuals-ds?api-version=2024-05-01-preview
   ```
   Body: `search/datasource.json`

5. **Create the index**
   ```
   PUT {SEARCH_ENDPOINT}/indexes/mm-manuals-index?api-version=2024-05-01-preview
   ```
   Body: `search/index.json`

6. **Create the skillset**
   ```
   PUT {SEARCH_ENDPOINT}/skillsets/mm-manuals-skillset?api-version=2024-05-01-preview
   ```
   Body: `search/skillset.json`

7. **Create the indexer**
   ```
   PUT {SEARCH_ENDPOINT}/indexers/mm-manuals-indexer?api-version=2024-05-01-preview
   ```
   Body: `search/indexer.json`

8. **Run a Debug Session** in the Azure portal against one PDF. Verify the actual paths emitted by the Document Intelligence Layout skill match the source paths used in the skillset (see `docs/validation.md` step 1). Adjust if needed and re-PUT the skillset.

9. **Run the indexer** against a single sample manual:
   ```
   POST {SEARCH_ENDPOINT}/indexers/mm-manuals-indexer/run?api-version=2024-05-01-preview
   ```

10. **Inspect** results using the validation checklist (`docs/validation.md`).

## Validation (must pass before going wider)

- Real Layout page-number path debugged from actual skill output.
- Printed page labels verified against the visible labels in the source PDF.
- Text **and** diagram peer records both present in `mm-manuals-index` for the same source file.
- `chunk_for_semantic` is a real assembled string, not empty and not raw JSON.
- All `chunk_id` values are unique and use the correct prefix (`txt_`, `dgm_`, `sum_`).
- `text_vector` populates for text, diagram, and summary records.
- Decorative images are correctly skipped (`record_type='diagram' and has_diagram=false` -> spot-check).
- OCR text appears in `ocr_fallback_text` but does NOT appear in the semantic config priority list.

## Open TODOs / blockers

- **Layout skill output paths** (`pageNumber`, `sections/h1..h3`, `ordinal_position`) must be confirmed against the actual JSON emitted by the version of DocumentIntelligenceLayoutSkill in your region.
- **End-page mapping** (`physical_pdf_page_end`, `printed_page_label_end`) is currently set equal to the start values. If the layout skill exposes a real end page per section, wire it in.
- **Vision model availability** in your Azure OpenAI region must be confirmed (Gov / sovereign clouds may not yet have gpt-4o).
- **Diagram-to-section linking**: in this design, diagrams are peer records and are not joined to the nearest text chunk. If you need that join, add a follow-on enrichment that fills `surrounding_context` by looking up the text chunk on the same physical page after first indexing.

## Placeholders to replace

| Placeholder | Where it appears | What to put |
|---|---|---|
| `<STORAGE_CONNECTION_STRING>` | `search/datasource.json` | Blob storage account connection string |
| `<STORAGE_CONTAINER_NAME>` | `search/datasource.json` | Container holding source PDFs |
| `<FUNCTION_APP_HOST>` | `search/skillset.json` (4 places) | e.g. `myfuncapp.azurewebsites.net` |
| `<FUNCTION_KEY>` | `search/skillset.json` (4 places) | Function App function key |
| `<AOAI_ENDPOINT>` | `search/skillset.json` (3 embed skills) + Function App settings | e.g. `https://myaoai.openai.azure.com/` |
| `<AOAI_API_KEY>` | `search/skillset.json` + Function App settings | Azure OpenAI key |
| `<AOAI_EMBED_DEPLOYMENT>` | `search/skillset.json` (3 embed skills) | Ada-002 deployment name |
| `<AOAI_VISION_DEPLOYMENT>` | Function App settings | Vision chat deployment name (e.g. gpt-4o) |
| `<AOAI_CHAT_DEPLOYMENT>` | Function App settings | Chat deployment name for summaries |
| `<AOAI_API_VERSION>` | Function App settings | e.g. `2024-08-01-preview` |
| `<AI_SERVICES_KEY>` | `search/skillset.json` (`cognitiveServices.key`) | AI Services multi-service key |
| `<AZURE_WEBJOBS_STORAGE_CONN>` | `function_app/local.settings.json.example` | Storage account for the Function App itself |
| `<FUNCTION_APP_NAME>` | README deploy command | Your function app resource name |
