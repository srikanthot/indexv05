# Runbook

## Dashboards

- **Application Insights** — `{baseName}-{env}-appi`
  - Failures tab: WebApi skill 500s, AOAI rate-limit errors.
  - Performance tab: DI poll duration, vision call duration.
- **Search service portal** — Indexers → `mm-manuals-indexer`
  - Execution history with per-document errors and warnings.

## Common alerts to wire

Create these in Azure Monitor (not automated yet):

| Condition | Severity | Action |
|---|---|---|
| Indexer execution status = transientFailure for 2 consecutive runs | 2 | Page on-call |
| `processing_status` with value `config_error` seen in last 1h | 2 | Check Function App settings |
| AOAI 429 rate > 5/min for 10 min | 3 | Raise deployment capacity |
| Function App memory > 80% for 15 min | 3 | Investigate DI payloads |

## Incident responses

### Indexer stuck at "in progress"
- Check the Function App logs for a 504/timeout on `process-document`.
  The DI poll timeout (210 s) is below the skill timeout (230 s); if
  both hit, the root cause is almost always a very large PDF.
- Mitigation: exclude the file via
  `metadata_storage_name ne 'big.pdf'` on the indexer query, re-run,
  then investigate separately.

### "config_error" processing_status
- The Function App is missing a required env var. Inspect
  `infra/modules/resources.bicep` for the canonical list, compare
  against App Settings in the portal, and re-run `scripts/deploy.sh`.

### AOAI 429s
- Rate limit is per-deployment. Either raise capacity
  (`infra/modules/resources.bicep`, `aoaiChat.sku.capacity`) and
  redeploy, or lower `degreeOfParallelism` in `search/skillset.json` for
  the diagram skill.

### Vision cache not hitting
- Confirm `SEARCH_ENDPOINT` is set and Function MI has
  `Search Index Data Reader` on the service. Check App Insights logs
  for the warning "hash cache lookup failed".

## Re-indexing

Full re-index (wipe and reprocess):

```bash
az search indexer reset -g <rg> --service-name <svc> --name mm-manuals-indexer
az search indexer run   -g <rg> --service-name <svc> --name mm-manuals-indexer
```

Partial re-index (single file): change-detection is high-water-mark on
`metadata_storage_last_modified`. Touch the blob (rewrite with the same
content) to force pickup.

## Version bumps

`SKILL_VERSION` is stamped on every record. Bump it in
`infra/modules/resources.bicep` (app setting) when behavior changes in a
way that should invalidate cached records.
