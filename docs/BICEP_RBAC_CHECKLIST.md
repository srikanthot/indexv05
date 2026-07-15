# Bicep provisioning checklist — Tech Manual indexing (per environment)

Bake everything below into the Bicep template so each new environment (dev / UAT / prod,
any RG/subscription) comes up fully wired. After this, Jenkins needs only credentials +
config values — no manual `az role assignment` commands.

Three identities need roles: **(A) the Jenkins pipeline SP**, **(B) the Search service MI**,
**(C) the Function App MI**. Enable system-assigned managed identity on the Search service
and Function App FIRST, then assign roles to their principalIds.

---

## A. Jenkins pipeline SP  (scope: the resource group is fine)
The CI/CD service principal. Pass its objectId into Bicep as a param.

| Role | roleDefinitionId (built-in) | On resource |
|---|---|---|
| Reader | acdd72a7-3385-48ef-bd42-f606fba81ae7 | resource group |
| Website Contributor | de139f84-1756-47ae-9be6-808fbbe84772 | Function App (or RG) |
| Search Service Contributor | 7ca78c08-252a-4471-8644-bb5ff32d4ba0 | Search service |
| Search Index Data Contributor | 8ebe5a00-799e-43f5-93ac-243d3dce84a7 | Search service |
| Storage Blob Data Contributor | ba92f5b4-2d11-453d-a403-e96b0029c9fe | Storage account |
| Cognitive Services OpenAI User | 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd | AOAI / Foundry |
| Cognitive Services User | a97b65f3-24c7-4388-baec-2e87135dc908 | Document Intelligence / AI Services |
| Cosmos DB Built-in Data Contributor | 00000000-0000-0000-0000-000000000002 (Cosmos **SQL** role) | Cosmos account |

## B. Search service system-assigned MI
| Role | roleDefinitionId | On resource |
|---|---|---|
| Storage Blob Data Reader | 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 | Storage account |
| Cognitive Services OpenAI User | 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd | AOAI / Foundry |
| Cognitive Services User | a97b65f3-24c7-4388-baec-2e87135dc908 | AI Services account |

## C. Function App system-assigned MI
| Role | roleDefinitionId | On resource |
|---|---|---|
| Storage Blob Data Reader | 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 | Storage account |
| Cognitive Services OpenAI User | 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd | AOAI / Foundry |
| Cognitive Services User | a97b65f3-24c7-4388-baec-2e87135dc908 | Document Intelligence |
| Search Index Data Reader | 1407120a-92aa-4202-b7e9-c0e197c71c8f | Search service |
| Cosmos DB Built-in Data Contributor | 00000000-0000-0000-0000-000000000002 (Cosmos **SQL** role) | Cosmos account |

> Cosmos roles are **data-plane SQL role assignments**
> (`Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments`), NOT normal `roleAssignments`.
> Use roleDefinitionId `.../sqlRoleDefinitions/00000000-0000-0000-0000-000000000002`, scope =
> the Cosmos account. This is what was missing (the `Sub Status 5300` error).

---

## Resource configuration flags Bicep must set (these bit us)
- **Storage account**: blob **soft-delete ON**, 7-day retention (preflight hard-fails without it).
  Keyless is fine — access is all AAD/RBAC; `allowSharedKeyAccess` can stay off.
- **Search service**: `identity: SystemAssigned`; auth allows AAD (`aadOrApiKey`); public network
  access enabled (or add the pipeline/agent IPs).
- **Function App**: `identity: SystemAssigned`; **Linux, Python 3.12**; app settings
  `SCM_DO_BUILD_DURING_DEPLOYMENT=true`, `ENABLE_ORYX_BUILD=true`; do **NOT** set
  `WEBSITE_RUN_FROM_PACKAGE` (it bypasses the server build). Leave SCM/Kudu **Entra (AAD) auth
  enabled** so the pipeline can zip-deploy with its token.
- **Cosmos DB**: SQL (core) API; create database `indexing`; native AAD RBAC (see role note above).
- **AI resources**: AOAI/Foundry, Document Intelligence, and the multi-service AI Services
  (Cognitive) account the skillset attaches to — each reachable at its endpoint.

---

## After Bicep, per environment Jenkins just needs
1. Credentials (already the pattern): `azure-client-id/secret/tenant`, `<ENV>_AZURE_SUBSCRIPTION_ID`,
   and the `deploy-config-<env>` secret file (`deploy.config.json` with that env's endpoints/names).
2. Run the pipeline: **ACTION = `deploy`** once (creates index + indexes docs), then `run` nightly.

No `assign_roles.py` and no manual `az role assignment` — Bicep already did A/B/C.
(`assign_roles.py --skip-deploy-principal` remains a handy one-shot fallback if a grant is ever missed.)
