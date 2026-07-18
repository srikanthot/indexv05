# RBAC — Least‑Privilege Setup (avoiding Contributor / User Access Administrator on the pipeline)

**Problem this solves:** the deploy pipeline was asking for **Contributor** and **User Access
Administrator** on the Jenkins service principal — heavyweight, privileged roles that need a security
change ticket and can't be self‑granted under an ABAC guardrail. You don't actually need them on the
pipeline. This document is the least‑privilege model.

## The key idea: separate ONE‑TIME provisioning from the RECURRING pipeline

The only reason bootstrap demanded **User Access Administrator** is that it **assigns roles to the
managed identities at runtime** (bootstrap Step 4 → `assign_roles.py`). Assigning roles is a *setup*
task, not a per‑run task. Split it:

| Tier | Who | How often | Needs privileged access? |
|---|---|---|---|
| **A. One‑time provisioning** — create resources + assign the managed identities their roles | An Azure admin (Owner/UAA) **or** IaC (Bicep/Terraform) | Once per environment | Yes — but done ONCE, by the platform team |
| **B. Recurring pipeline** — deploy code, preanalyze, create index, run indexer | The Jenkins SP | Every deploy + nightly | **No** — least‑privilege only |

With this split the **Jenkins SP never needs Contributor or User Access Administrator.**

---

## Tier A — one‑time provisioning (admin / IaC, once per environment)

Pre‑provision the resources (storage, search service, Cosmos account, function app, AOAI, Document
Intelligence) and assign the **managed identities** their roles. The exact role map (from
`assign_roles.py`):

**Search service managed identity**
- Storage Blob Data Reader — on Storage account
- Cognitive Services OpenAI User — on AOAI
- Cognitive Services User — on the AI Services / DI account

**Function App managed identity**
- Storage Blob Data Reader — on Storage account
- Cognitive Services OpenAI User — on AOAI
- Cognitive Services User — on Document Intelligence
- Search Index Data Reader — on Search service

**Easiest way to do Tier A:** an admin (who already has Owner/UAA) runs, once, from their own session:
```bash
python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal
```
`--skip-deploy-principal` = grant only the managed identities (Tier A), not the Jenkins SP. The admin's
own account has the UAA needed to create these assignments. **This is the only step that ever needs
privileged access, and it's run once per environment.** (Or bake the same assignments into your IaC so
new resource groups / UAT / prod get them automatically — no ticket each time.)

---

## Tier B — the Jenkins SP's own roles (LEAST PRIVILEGE — you can self‑grant these)

The pipeline SP needs only the roles it uses **directly**. None are privileged; none are Contributor or
UAA. Grant at the subscription (or resource‑group) scope:

| Role | Why | You already granted? |
|---|---|---|
| **Reader** | read resource metadata in preflight (the `storage account show` that was failing) | grant it |
| **Website Contributor** | deploy the function app code | ✅ already had it |
| **Search Service Contributor** | create/update the index, skillset, indexer, datasource | grant it |
| **Search Index Data Contributor** | write/query index documents | ✅ done |
| **Storage Blob Data Contributor** | read/write the preanalyze cache blobs | ✅ done |
| **Cognitive Services OpenAI User** | call AOAI embeddings / vision | grant it |
| **Cognitive Services User** | call Document Intelligence | ✅ done |
| **Cosmos DB Built‑in Data Contributor** | write run history / pdf_state (CLI‑assigned) | grant it |

So you still need to self‑grant: **Reader**, **Search Service Contributor**, **Cognitive Services OpenAI
User**, and the **Cosmos data role** — all non‑privileged, all within your ABAC allowance.

```powershell
$sub="b41d2ec9-3c69-41f3-8dc7-b1500baeedf1"; $sp="6be27496-7668-454b-ac68-1a8bcffac97e"
$scope="/subscriptions/$sub"
az role assignment create --assignee $sp --role "Reader"                         --scope $scope
az role assignment create --assignee $sp --role "Search Service Contributor"     --scope $scope
az role assignment create --assignee $sp --role "Cognitive Services OpenAI User" --scope $scope
# Cosmos data-plane role (different command):
$cosmos="<cosmos-account-name>"; $rg="<cosmos-resource-group>"
az cosmosdb sql role assignment create --account-name $cosmos --resource-group $rg `
  --role-definition-name "Cosmos DB Built-in Data Contributor" --principal-id $sp --scope "/"
```

---

## Tier B — make the pipeline skip role assignment

So the recurring pipeline doesn't try to assign roles (and therefore doesn't need UAA), run bootstrap
with the new flag:

```
python scripts/bootstrap.py --config deploy.config.json --skip-roles --skip-deploy-principal \
    [--skip-cosmos if the Cosmos DB/containers are pre-provisioned]
```

`--skip-roles` skips Step 4 entirely. Update **Jenkinsfile.deploy** to add `--skip-roles` (and drop
`--auto-fix`, which also attempts privileged control‑plane fixes). The pipeline then runs with only the
Tier‑B least‑privilege roles.

---

## What to put in your change ticket

> The CI/CD pipeline service principal for the Tech Manual index needs **only data‑plane + service
> roles** (Search Service Contributor, Search Index Data Contributor, Storage Blob Data Contributor,
> Cognitive Services OpenAI User, Cognitive Services User, Cosmos DB Built‑in Data Contributor) plus
> **Reader** and **Website Contributor**. It does **NOT** need Owner, Contributor, or User Access
> Administrator. All privileged role‑assignment (wiring the managed identities) is performed **once**
> during environment provisioning by the platform team / IaC, not by the pipeline.

That's a routine, defensible ask — no standing privileged access on the automation identity.

---

## For new resource groups / UAT / prod
Repeat **Tier A once** per environment (the admin runs `assign_roles.py --skip-deploy-principal`, or IaC
does it), and self‑grant **Tier B** to that environment's pipeline SP. No recurring privileged tickets.
Bake Tier A into IaC and it's fully automatic.
