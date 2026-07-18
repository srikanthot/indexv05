# Bicep role assignments to add — Tech Manual indexing

Add these role assignments to your EXISTING Bicep template, next to each resource. Nothing
here is subscription- or RG-specific — it's the same for dev, UAT, prod, and any number of
RGs. When you deploy the template to a new RG, the resources come up with these permissions
already in place, and the indexing Jenkins pipeline just reads the resource names from
`deploy.config.json` and runs.

## Identities (who gets the roles)
- **Pipeline SP** — the Jenkins service principal. Pass its objectId in as a Bicep param.
- **Search MI** — enable system-assigned identity on the Search service; use its principalId.
- **Function MI** — enable system-assigned identity on the Function App; use its principalId.

> In Bicep each role assignment = { principalId: <reference/param>, scope: <the resource>,
> roleDefinitionId: <the fixed role ID from the table at the bottom> }. Only the roleDefinitionId
> is a literal, and it's an Azure constant — never a per-environment value.

---

# Permissions per resource (grant these ON each resource)

**Storage account**  — also set: blob soft-delete ON, 7-day retention.
- Pipeline SP → Storage Blob Data Contributor
- Search MI   → Storage Blob Data Reader
- Function MI → Storage Blob Data Reader

**Search service**  — also set: system-assigned identity ON.
- Pipeline SP → Search Service Contributor
- Pipeline SP → Search Index Data Contributor
- Function MI → Search Index Data Reader

**Function App**  — also set: system-assigned identity ON; Linux Python 3.12;
app settings SCM_DO_BUILD_DURING_DEPLOYMENT=true + ENABLE_ORYX_BUILD=true; do NOT set
WEBSITE_RUN_FROM_PACKAGE.
- Pipeline SP → Website Contributor

**AOAI / Foundry**
- Pipeline SP → Cognitive Services OpenAI User
- Search MI   → Cognitive Services OpenAI User
- Function MI → Cognitive Services OpenAI User

**Document Intelligence**
- Pipeline SP → Cognitive Services User
- Function MI → Cognitive Services User

**AI Services account** (the Cognitive account the skillset attaches to)
- Search MI → Cognitive Services User

**Cosmos DB account**  — also set: SQL (core) API; database named `indexing`.
- Pipeline SP → Cosmos DB Built-in Data Contributor
- Function MI → Cosmos DB Built-in Data Contributor
- NOTE: Cosmos uses a DIFFERENT resource type for this — `sqlRoleAssignments`, not the normal
  role assignment. (Skipping this = the "cannot be authorized by AAD token / Sub Status 5300" error.)

**Resource group**
- Pipeline SP → Reader

---

## For the Bicep author — fixed role IDs (Azure built-in constants; same in every subscription)
| Role name | roleDefinitionId | Type |
|---|---|---|
| Reader | acdd72a7-3385-48ef-bd42-f606fba81ae7 | normal |
| Website Contributor | de139f84-1756-47ae-9be6-808fbbe84772 | normal |
| Search Service Contributor | 7ca78c08-252a-4471-8644-bb5ff32d4ba0 | normal |
| Search Index Data Contributor | 8ebe5a00-799e-43f5-93ac-243d3dce84a7 | normal |
| Search Index Data Reader | 1407120a-92aa-4202-b7e9-c0e197c71c8f | normal |
| Storage Blob Data Contributor | ba92f5b4-2d11-453d-a403-e96b0029c9fe | normal |
| Storage Blob Data Reader | 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 | normal |
| Cognitive Services OpenAI User | 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd | normal |
| Cognitive Services User | a97b65f3-24c7-4388-baec-2e87135dc908 | normal |
| Cosmos DB Built-in Data Contributor | 00000000-0000-0000-0000-000000000002 | Cosmos sqlRoleAssignment |

## Per environment, the only things that differ (all Bicep params, not edits)
Pipeline SP objectId + the resource names/endpoints. Jenkins per env: its credentials,
`<ENV>_AZURE_SUBSCRIPTION_ID`, and the `deploy-config-<env>` file. Then ACTION=deploy once,
`run` nightly. No manual grants.
