# Setup

> For the full deploy flow, see [deployment.md](deployment.md). This
> page is a reference for the resources and settings the pipeline
> uses — everything below is provisioned automatically by
> `scripts/deploy.sh`.

## Azure resources

All created by `infra/main.bicep`:

- Azure AI Search (Basic for dev; Standard recommended for prod)
- Storage account + PDF container (shared-key disabled)
- Azure AI Services multi-service (billing for built-in Layout skill)
- Azure Document Intelligence (prebuilt-layout)
- Azure OpenAI:
  - `text-embedding-ada-002` (1536 dims)
  - `gpt-4.1` (vision-capable; covers both diagram analysis and summary)
- Log Analytics workspace
- Application Insights component
- Linux consumption plan + Python 3.11 Function App with system-assigned MI

## RBAC

Created by the same Bicep template:

| Principal | Scope | Role |
|---|---|---|
| Function App MI | Storage account | Storage Blob Data Owner |
| Function App MI | Azure OpenAI | Cognitive Services OpenAI User |
| Function App MI | Document Intelligence | Cognitive Services User |
| Function App MI | Azure AI Search | Search Index Data Reader |
| Search service MI | Storage account | Storage Blob Data Reader |
| Search service MI | Azure OpenAI | Cognitive Services OpenAI User |
| Search service MI | AI Services | Cognitive Services User |

## Function App settings

Set automatically by Bicep (in addition to the Functions host defaults):

| Setting | Purpose |
|---|---|
| `AUTH_MODE=mi` | Force managed-identity auth on every outbound call |
| `AOAI_ENDPOINT` / `AOAI_API_VERSION` | Azure OpenAI (no key needed) |
| `AOAI_VISION_DEPLOYMENT` / `AOAI_CHAT_DEPLOYMENT` | gpt-4.1 deployment name |
| `DI_ENDPOINT` / `DI_API_VERSION` | Document Intelligence (no key needed) |
| `SEARCH_ENDPOINT` / `SEARCH_INDEX_NAME` | Hash-cache lookup |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry |
| `SKILL_VERSION` | Stamped on every record (`3.0.0`) |

## Local dev overrides

`function_app/local.settings.json.example` shows the per-key fallback:

- Set `AUTH_MODE=key`
- Populate `AOAI_API_KEY`, `DI_API_KEY`, `SEARCH_ADMIN_KEY`,
  `STORAGE_BLOB_SAS`
- `func start`

Leave `AUTH_MODE=mi` (unset or `mi`) to use the Azure CLI credential
chain locally — simpler if you're already `az login`'d.

## Placeholders in the search artifacts

These are filled at deploy time by `scripts/deploy_search.py`:

| Placeholder | Source |
|---|---|
| `<STORAGE_RESOURCE_ID>` | Bicep output `storageAccountId` |
| `<STORAGE_CONTAINER_NAME>` | Bicep parameter `pdfContainerName` |
| `<FUNCTION_APP_HOST>` | Bicep output `functionAppHost` |
| `<FUNCTION_KEY>` | `az functionapp keys list` |
| `<AOAI_ENDPOINT>` | Bicep output `aoaiEndpoint` |
| `<AOAI_EMBED_DEPLOYMENT>` | Env var (defaults to `text-embedding-ada-002`) |
| `<AI_SERVICES_SUBDOMAIN_URL>` | Bicep output `aiServicesSubdomainUrl` |

`deploy_search.py` fails if any `<PLACEHOLDER>` remains unrendered.
