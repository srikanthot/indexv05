# Release gates

Every change that lands on `main` and every deploy must pass these
mandatory checks. There is no override path — if a check is broken,
fix the check or fix the change.

## Automated checks

All enforced by `.github/workflows/ci.yml` and re-run by the `gate` job
in `.github/workflows/deploy.yml` (so the deploy is gated on the exact
SHA being deployed, not an earlier one).

| Gate | Where | What it catches |
|---|---|---|
| `python tests/test_unit.py` | CI + deploy gate | 68 deterministic unit checks on helpers, ID generation, page-span parsing, OData escaping, config errors |
| `python tests/test_e2e_simulator.py` | CI + deploy gate | Full end-to-end handler simulation with projected-field validation against `index.json` |
| `ruff check function_app scripts` | CI + deploy gate | Lint |
| `bicep build infra/main.bicep` | CI + deploy gate | Template syntax + schema |
| `scripts/smoke_test.py` | Post-deploy (optional, recommended) | Validates Layout-skill output paths, record counts per record_type, required fields populated |

## GitHub branch protection

Enforce the CI gates on `main` by configuring branch protection rules
(Settings → Branches → Branch protection rules → `main`):

- **Require a pull request before merging**
  - Require approvals: 1
  - Dismiss stale approvals on new commits
- **Require status checks to pass before merging**
  - `CI / test`
  - `CI / bicep-validate`
  - `CI / lint`
  - Require branches to be up to date
- **Require linear history**
- **Do not allow bypassing** (even for admins)

See `docs/branch-protection.md` for a step-by-step.

## Environment approvals

GitHub Environments (Settings → Environments → `prod`) add a second
gate on top of CI:

- **Required reviewers** on `prod` (and optionally `staging`)
- **Wait timer** (e.g., 5 minutes) so recalled deploys can be cancelled
- **Deployment branch rule** restricting `prod` deploys to `main`

## Local pre-push

Before pushing a branch, run the same gates locally:

```bash
python tests/test_unit.py
python tests/test_e2e_simulator.py
ruff check function_app scripts
bicep build infra/main.bicep --stdout > /dev/null
```

If any of these fail the push will fail in CI anyway; running locally
saves the round-trip.

## Post-deploy smoke

```bash
python scripts/smoke_test.py --env dev --wait-minutes 15
```

This triggers the indexer, waits for it, then asserts:
- Indexer status is `success`
- `itemsProcessed > 0`
- Every `record_type` (`text`, `diagram`, `table`, `summary`) has at
  least one record
- Required fields are populated on a sample of each record_type
- Multi-page text chunks count is reported (informational)

Non-zero exit on any failure so the deploy workflow halts.
