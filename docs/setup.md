# Setup notes

## Azure resources required

- Azure AI Search service (Basic tier or higher; Standard recommended for skillset throughput)
- Azure Blob Storage account + container holding source PDFs
- Azure AI Services (Cognitive Services multi-service) — required by built-in OCR / Layout skills
- Azure OpenAI service with two deployments:
  - text-embedding-ada-002  (1536 dims)
  - a vision-capable chat model for diagram analysis and summary (e.g. gpt-4o)
- Azure Function App (Linux, Python 3.11, Functions v4) for the custom WebApi skills

## Permissions

- The Search service must be able to read from Blob Storage (connection string in datasource OR managed identity).
- The Search service must be able to call the Function URL with the function key embedded in the skillset.
- The Function App's identity must be able to reach the Azure OpenAI endpoint (network + key).

## Environment variables on the Function App

Set these in App Settings (mirrors `local.settings.json.example`):

- AOAI_ENDPOINT
- AOAI_API_KEY
- AOAI_API_VERSION
- AOAI_VISION_DEPLOYMENT
- AOAI_CHAT_DEPLOYMENT
- SKILL_VERSION
