# Security

## Auth model

All service-to-service calls use Entra ID (managed identity) tokens by
default. API keys remain supported for local dev only, behind
`AUTH_MODE=key`.

| Caller | Callee | Mechanism |
|---|---|---|
| Function App | Azure OpenAI | MI → `Cognitive Services OpenAI User` |
| Function App | Document Intelligence | MI → `Cognitive Services User` |
| Function App | Blob Storage (PDF fetch) | MI → `Storage Blob Data Owner` |
| Function App | Azure AI Search (hash cache) | MI → `Search Index Data Reader` |
| Function App | Azure Functions host storage | MI (no connection string) |
| Search service | Blob Storage (data source) | MI → `Storage Blob Data Reader` |
| Search service | Azure OpenAI (vectorizer + embedding skill) | MI → `Cognitive Services OpenAI User` |
| Search service | AI Services (Layout skill billing) | MI → `Cognitive Services User` |
| Azure AI Search | Function App (WebApi skills) | Function key (query param) |

The only secret still in rotation is the function key embedded in
`skillset.json`. Rotate by calling:

```bash
az functionapp keys set -g <rg> -n <func> --key-type functionKeys --key-name default
python scripts/deploy_search.py --env <env>   # re-renders skillset.json with new key
```

## Storage hardening

- `allowSharedKeyAccess: false` — no shared-key auth on the storage
  account.
- `allowBlobPublicAccess: false` — containers cannot be made public.
- TLS 1.2 minimum.
- PDF container has `publicAccess: None`.

## Network posture

Public endpoints are enabled by default so the built-in Search skills
(which are multi-tenant) can reach your resources. To harden for prod:
- Add Private Endpoints on Storage, AOAI, DI, Search, and the Function
  App.
- Put the Search service on a Shared Private Link to the Function App.
- Restrict the storage firewall to the Search service's resource
  instance.

These are additive changes to `infra/modules/resources.bicep`; they are
not included by default because they add per-tenant complexity and cost.

## Secret surface

| Secret | Where | Rotation |
|---|---|---|
| Function key | `search/skillset.json` (rendered at deploy) | `az functionapp keys set …` + re-run `deploy_search.py` |
| `AzureWebJobsStorage` | Replaced with MI; no secret | — |
| AOAI / DI / Search keys | Not used in production | — |

## Threat model notes

- Prompt injection via body text is mitigated in `diagram.py` by
  stripping quotes from the surrounding-text window before
  interpolation, and by capping it at 1500 characters.
- OData filter injection into the hash-cache lookup is mitigated by a
  strict `^[A-Za-z0-9_\-]+$` whitelist on `parent_id` + `image_hash`
  plus single-quote escaping.
- The vision model can still hallucinate; callers should treat
  `diagram_description` as a retrieval hint, not ground truth.
