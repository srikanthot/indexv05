# Deployment

End-to-end, reproducible deploy for any environment (`dev`, `staging`,
`prod`). Everything is Infrastructure-as-Code; nothing is clicked in
the portal.

## One-time per environment

1. **Create a GitHub Environment** named `dev`, `staging`, or `prod` and
   set these secrets (from a workload-identity-federated service
   principal):
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`

2. The service principal must have on the target subscription:
   - `Contributor`
   - `User Access Administrator` (needed to create the MI role
     assignments in the Bicep template)
   - `Search Service Contributor` and `Search Index Data Contributor` on
     the search service after it is created (or simply inherited from
     Contributor if you accept that scope).

## Deploy

From a workstation with `az login` completed:

```bash
scripts/deploy.sh dev
# or, with an immediate indexer run:
scripts/deploy.sh dev --run-indexer
```

Or from GitHub Actions: **Actions → Deploy → Run workflow** and pick the
environment.

The script is idempotent: running it again reconciles infra drift,
re-publishes the function, and re-PUTs the search artifacts.

## What the deploy does

1. **Bicep**: `az deployment sub create` on `infra/main.bicep`. Creates
   the resource group and every resource inside it. Emits outputs used
   downstream (endpoints, principal IDs, storage resource ID).

2. **Function code**: `func azure functionapp publish` with Run-From-
   Package.

3. **Search artifacts**: `scripts/deploy_search.py` reads Bicep outputs,
   fetches a function key, substitutes every `<PLACEHOLDER>` in
   `search/*.json`, and `PUT`s the four artifacts with AAD auth.

## Environment-specific values

All environment differences live in `infra/parameters/<env>.bicepparam`:
region, base name, deployment SKUs. Application code has zero
environment-specific constants.

## Post-deploy validation

See [validation.md](validation.md). The quick check:

```bash
# Documents should appear after the indexer completes.
az search query --service-name <search-name> --index-name mm-manuals-index \
   --search-text "*" --top 1
```

## Rollback

Infra and search artifacts are declarative: redeploy the previous
commit. The function app supports slot-less rollback via
`func azure functionapp publish` with a previous package — or cut a
hotfix branch and redeploy.
