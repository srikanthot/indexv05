# Bicep provisioning checklist — Tech Manual indexing

Environment-agnostic. Works for any subscription / resource group (dev, UAT, prod, and any
number of RGs per env). After Bicep applies this, Jenkins needs only credentials + config
values — no manual `az role assignment` commands.

## The one rule (this is the part that's the same everywhere)
- **roleDefinitionId GUIDs below are GLOBAL CONSTANTS** — identical in every subscription and
  in Azure Gov. Safe to hardcode in Bicep.
- **Resource IDs and principal IDs are NEVER hardcoded** — use Bicep references
  (`storage.id`, `search.identity.principalId`, `func.identity.principalId`) and one param for
  the pipeline SP objectId. So the *same* template produces the correct wiring in any sub/RG.

## Three identities (turn these ON, then reference their principalIds)
- `pipelineSpObjectId`  — Bicep **param** (the Jenkins SP; may be same or different per env).
- Search service — set `identity: { type: 'SystemAssigned' }`, then use `.identity.principalId`.
- Function App  — set `identity: { type: 'SystemAssigned' }`, then use `.identity.principalId`.

---

# For EACH resource you create, add these (roles granted ON that resource)

### Storage account
- **Config**: blob **soft-delete ON, 7-day retention** (preflight hard-fails without it). Access is
  all AAD/RBAC — `allowSharedKeyAccess` can stay off.
- **Grant on it**:
  - pipelineSpObjectId → **Storage Blob Data Contributor** `ba92f5b4-2d11-453d-a403-e96b0029c9fe`
  - Search MI          → **Storage Blob Data Reader**      `2a2b9908-6ea1-4ae2-8e65-a410df84e7d1`
  - Function MI        → **Storage Blob Data Reader**      `2a2b9908-6ea1-4ae2-8e65-a410df84e7d1`

### Search service
- **Config**: system-assigned identity ON; AAD auth allowed (`aadOrApiKey`); public network access
  enabled (or allow the agent IPs).
- **Grant on it**:
  - pipelineSpObjectId → **Search Service Contributor**    `7ca78c08-252a-4471-8644-bb5ff32d4ba0`
  - pipelineSpObjectId → **Search Index Data Contributor** `8ebe5a00-799e-43f5-93ac-243d3dce84a7`
  - Function MI        → **Search Index Data Reader**      `1407120a-92aa-4202-b7e9-c0e197c71c8f`

### Function App
- **Config**: system-assigned identity ON; **Linux, Python 3.12**; app settings
  `SCM_DO_BUILD_DURING_DEPLOYMENT=true`, `ENABLE_ORYX_BUILD=true`; do **NOT** set
  `WEBSITE_RUN_FROM_PACKAGE`; leave SCM/Kudu **Entra (AAD) auth enabled**.
- **Grant on it**:
  - pipelineSpObjectId → **Website Contributor** `de139f84-1756-47ae-9be6-808fbbe84772`

### AOAI / Foundry (the OpenAI-embeddings + chat/vision resource)
- **Grant on it**:
  - pipelineSpObjectId → **Cognitive Services OpenAI User** `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`
  - Search MI          → **Cognitive Services OpenAI User** `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`
  - Function MI        → **Cognitive Services OpenAI User** `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`

### Document Intelligence (+ the multi-service AI Services account the skillset attaches to)
> In some envs these are one account; if separate, apply to each as noted.
- **Grant on it**:
  - pipelineSpObjectId → **Cognitive Services User** `a97b65f3-24c7-4388-baec-2e87135dc908`
  - Function MI        → **Cognitive Services User** `a97b65f3-24c7-4388-baec-2e87135dc908` (Document Intelligence)
  - Search MI          → **Cognitive Services User** `a97b65f3-24c7-4388-baec-2e87135dc908` (AI Services account)

### Cosmos DB account
- **Config**: SQL (core) API; create database `indexing`; native AAD RBAC.
- **Grant on it** — ⚠️ these are **data-plane SQL role assignments**, a DIFFERENT resource type:
  `Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments` (NOT `Microsoft.Authorization/roleAssignments`),
  roleDefinitionId = `<account>/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002`, scope = the account:
  - pipelineSpObjectId → **Cosmos DB Built-in Data Contributor** `00000000-0000-0000-0000-000000000002`
  - Function MI        → **Cosmos DB Built-in Data Contributor** `00000000-0000-0000-0000-000000000002`
  > Missing this is the `Sub Status 5300 / cannot be authorized by AAD token` error.

### Resource group (or subscription) — one broad read
- pipelineSpObjectId → **Reader** `acdd72a7-3385-48ef-bd42-f606fba81ae7` (scope = the RG)

---

## Per-environment: only these change
- `pipelineSpObjectId` (param) and the resource names/endpoints — all via Bicep params.
- Jenkins per env: `azure-client-id/secret/tenant`, `<ENV>_AZURE_SUBSCRIPTION_ID`, and the
  `deploy-config-<env>` file. Then run **ACTION = deploy** once, `run` nightly.
- Nothing else. No `assign_roles.py`, no manual grants — Bicep already wired every resource above.
