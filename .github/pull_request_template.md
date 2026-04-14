## Summary
<!-- What changed and why. -->

## Scope
- [ ] Code (function_app/)
- [ ] Infra (infra/)
- [ ] Search artifacts (search/)
- [ ] Docs
- [ ] CI / release gates

## Release gate checklist
- [ ] `python tests/test_unit.py` passes locally
- [ ] `python tests/test_e2e_simulator.py` passes locally
- [ ] `ruff check function_app scripts` is clean
- [ ] `bicep build infra/main.bicep --stdout > /dev/null` succeeds
- [ ] `CHANGELOG.md` updated (if user-visible behaviour changes)
- [ ] `SKILL_VERSION` bumped (if projected record shape changes or cache
      invalidation is desired)

## Deploy impact
- [ ] No Bicep changes
- [ ] Requires `scripts/deploy.sh <env>` to pick up infra changes
- [ ] Requires `scripts/deploy.sh <env> --run-indexer --smoke` to re-index

## Smoke test plan
<!-- For non-trivial changes: what will you check in the dev environment
     after deploy, beyond the automated smoke test? -->
