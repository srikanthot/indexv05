# Branch protection setup

Apply once after the repo is created. These rules make CI gates
mandatory and prevent direct pushes to `main`.

## 1. Enable branch protection on `main`

GitHub → **Settings** → **Branches** → **Add branch protection rule**

- **Branch name pattern**: `main`
- **Require a pull request before merging**: on
  - **Require approvals**: `1`
  - **Dismiss stale pull request approvals when new commits are pushed**: on
  - **Require review from Code Owners**: on (requires `.github/CODEOWNERS`)
- **Require status checks to pass before merging**: on
  - **Require branches to be up to date before merging**: on
  - **Status checks required**:
    - `test` (from `CI` workflow)
    - `bicep-validate` (from `CI` workflow)
    - `lint` (from `CI` workflow)
- **Require linear history**: on
- **Do not allow bypassing the above settings**: on

## 2. Environment protection for prod

GitHub → **Settings** → **Environments** → **New environment** → `prod`

- **Required reviewers**: 1–2 operators
- **Wait timer**: `5` minutes
- **Deployment branches and tags**:
  - Restrict to `main` only
- **Environment secrets** (OIDC federation, no stored client secret):
  - `AZURE_CLIENT_ID`
  - `AZURE_TENANT_ID`
  - `AZURE_SUBSCRIPTION_ID`

Repeat with lighter settings for `staging` and `dev`.

## 3. OIDC federation (one-time)

On the Azure side, create a user-assigned MI or AAD app with federated
credentials scoped to this repo:

```
azure federated credential:
  issuer: https://token.actions.githubusercontent.com
  audience: api://AzureADTokenExchange
  subject: repo:<org>/<repo>:environment:prod
```

Grant it `Contributor` + `User Access Administrator` on the subscription
(Bicep creates role assignments at deploy time).

## 4. Verify

Open a PR that intentionally breaks a unit test. CI should show the
`test` check in red and the merge button should be disabled.
