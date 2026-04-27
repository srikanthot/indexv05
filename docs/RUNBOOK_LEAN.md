# Indexing Pipeline — Engineering Runbook (Lean)

Decision-focused companion to [RUNBOOK.md](RUNBOOK.md). Same system,
half the words. Read this first; drop into the long runbook only
when you need a specific command or full schema.

Audience: senior engineer who knows Azure, owns the deployment, and
is here to make decisions — not to be taught what a managed identity
is.

---

## 1. Overview

### 1.1 What this is

Multimodal RAG indexing for technical PDF manuals on Azure AI Search.
Hybrid retrieval (BM25 + vector + semantic reranker). Four record
types in one index: `text`, `diagram`, `table`, `summary`.

### 1.2 What this repo owns

Application layer only: function code, search artifacts, deploy
scripts, preanalyze. Not infra provisioning, not the SharePoint→blob
sync.

### 1.3 Pipeline shape

```
Blob (PDF) ─► Event Grid ─► Queue ─► Container App Job
                                       ├─ preanalyze --only <pdf>   (DI + GPT-4V cache)
                                       └─ POST /indexers/run
                                            │
                                            ▼
                                  Search indexer ─► skillset ─► index
                                       (custom skills hit Function App,
                                        which reads the cache)
```

---

## 2. Architecture & Design

### 2.1 Why we cache offline (`preanalyze`)

Three Azure constraints make end-to-end-in-the-skillset infeasible:

| Constraint | Number | Implication |
|---|---|---|
| Custom WebApi skill timeout | 230 s, immutable | DI + vision can't run live |
| DI prebuilt-layout on 500-page PDF | 3–15 min | Times out inside skill |
| GPT-4 Vision × 1,500 figures | 10–75 min / PDF | Times out inside skill |

`preanalyze.py` runs offline, writes results to `_dicache/`. Skills
read cache in ms. Re-indexing reuses the cache → vision paid once.

### 2.2 Why custom skills exist alongside built-ins

Built-ins handle: layout extraction, chunk-splitting, ada-002
embeddings. Everything else (per-figure vision with OCR, page-label
mapping, table-to-markdown with caption, semantic-string assembly,
per-PDF summary) has no built-in equivalent. The Import-and-Vectorize
wizard produces generic image tags only — useless for "what is wired
to terminal X7".

### 2.3 Why Container Apps Jobs for automation (not Functions)

| Host                  | Time limit | Verdict |
|-----------------------|------------|---------|
| Functions (Y1)        | 10 min     | Fails mid-vision |
| Functions (Premium)   | 30 min     | Fails on big PDFs |
| **Container Apps Job**| **none**   | Right tool |
| App Service WebJob    | none, but always-warm | Wasteful at idle |

### 2.4 Service constraints to memorize

| Service | Limit | Affects |
|---|---|---|
| AI Search WebApi skill | 230 s timeout | Drove preanalyze |
| AI Search indexer | 24 h cap (S1+) | Initial-load planning |
| AI Search index | 16 MB doc, 1,000 fields, 3,072 vector dims | We're well inside |
| Functions Y1 / Premium | 10 / 30 min | Why custom skills only do cache reads |
| AOAI ada-002 default | ~240K TPM (S0) | Embedding throughput |
| AOAI gpt-4.1 default | ~80K TPM (S0) | Vision throughput → `--vision-parallel 40` |
| Document Intelligence | 500 MB, 2,000 pages, ~30 min | Use `urlSource` for big PDFs |
| Storage Queue message | 64 KB | Pass blob name only |
| Event Grid | At-least-once, ~24 h retry | Idempotent worker + DLQ |
| Gov Cloud | 6–18 month feature lag | Stick to GA features |

---

## 3. Azure Infrastructure

### 3.1 Resource inventory

| # | Resource | SKU | Notes |
|---|---|---|---|
| 1 | Storage account | Standard_LRS | Soft-delete ON (30 d). Container `manuals`, queue `pdf-events`. |
| 2 | Azure AI Search | S1 | System-assigned MI |
| 3 | Azure OpenAI | S0 | Deployments: `text-embedding-ada-002` (240K TPM), `gpt-4.1` (80K TPM) |
| 4 | Document Intelligence | S0 | Region with prebuilt-layout |
| 5 | AI Services (multi-service) | S0 | Bills built-in Layout skill |
| 6 | Function App | Linux Y1, Python 3.11, Functions v4 | System-assigned MI |
| 7 | Application Insights | Workspace-based | Wired into Function App |
| 8 | Container Apps env + 2 jobs | Consumption | Event-triggered + cron |
| 9 | Event Grid system topic + 2 subs | — | BlobCreated + BlobDeleted, filter `.pdf`, exclude `_dicache/` |
| 10 | ACR | Basic | Hosts the job image |

### 3.2 Region choice

One region for everything. Binding constraint: a region that has
**gpt-4.1** (`az cognitiveservices account list-models`). Pick that
first; everything else follows.

### 3.3 Identity model

| Principal | Scope | Role |
|---|---|---|
| Function App MI | Storage / AOAI / DI / Search | Blob Reader / OpenAI User / CogSvc User / Index Data Reader |
| Search MI | Storage / AOAI / AI Services | Blob Reader / OpenAI User / CogSvc User |
| Job MI | Storage (RW + Queue) / AOAI / DI / Search | Blob Contributor + Queue Data Message Processor / OpenAI User / CogSvc User / Service Contributor |
| Deployer | Search / Function App RG | Service Contributor + Index Data Contributor / Contributor |

Wait 5–10 min after grants. Only in-band secret is the function key
(rotated by re-running `deploy_search.py`).

### 3.4 Capacity planning

Per typical 500-page / 1,500-figure manual:

| Phase | Token consumer | Time on default S0 | Quota lever |
|---|---|---|---|
| Vision | gpt-4.1 (~5M tok / PDF) | ~60 min | Bump TPM before bulk loads |
| Embedding | ada-002 (~1M tok / PDF) | ~5 min | Rarely the bottleneck |
| DI | per-page billing | 5–15 min | n/a |

Concurrency knobs we set:

| Knob | Value | Reason |
|---|---|---|
| Indexer `batchSize` | 1 | Big-PDF safety |
| `analyze-diagram` parallelism | 4 | AOAI vision TPM |
| Job replicas (event) | 0–3 | Cap concurrent vision |
| `--vision-parallel` (preanalyze) | 40 | Saturates 80K TPM without 429 storms |

---

## 4. Deployment

### 4.1 Single source of truth

`deploy.config.json` — copy from example, fill in. Function key is
fetched live, never stored.

### 4.2 First-time bootstrap

```
az login
python scripts/preflight.py             # verify resources + roles
scripts/deploy_function.sh               # publish code + App Settings
python scripts/deploy_search.py          # PUT datasource/index/skillset/indexer
python scripts/preanalyze.py             # offline DI + vision (phased)
python scripts/deploy_search.py --run-indexer
python scripts/smoke_test.py             # validates record counts + page math
```

### 4.3 Code & config promotion

- Function code change → `deploy_function.sh` (idempotent).
- Search artifact change → `deploy_search.py` (idempotent; placeholders
  in `search/*.json` rendered from config + live function key).
- Skill behaviour change without code change → bump `skillVersion` in
  config, redeploy function. New records carry new version; old
  records keep theirs until re-touched.
- Schema change that's not backwards-compatible → DELETE index,
  redeploy, reset + run indexer.

---

## 5. Operations

### 5.1 Production automation (target state)

```
Blob soft-delete ON
   │
   ├── Event Grid → Storage Queue → Container App Job (event-triggered)
   │     replicas 0–3
   │     BlobCreated  : invalidate cache → preanalyze --only <pdf> --force → POST /indexers/run
   │     BlobDeleted  : delete _dicache/<pdf>.*  (index cleaned via deletion policy)
   │
   └── Container App Job (cron 0 2 * * *)
         preanalyze --incremental
         preanalyze --cleanup
         POST /indexers/run

Indexer schedule: PT1H   (third independent safety net)
```

Three safety nets layered: events (5–15 min lag) → nightly
reconciliation (catches missed events) → indexer schedule (worst
case ≤1 h).

### 5.2 The correctness gap to close before automation

Naïve cron is wrong for the **update** case: `--incremental` skips
a re-uploaded PDF because `output.json` still exists, so the indexer
serves stale cache.

Required `preanalyze.py` changes (~100 LOC):

1. `--only <blob-name>` flag.
2. LMT-aware invalidation: if PDF LMT > cache LMT, re-run even
   under `--incremental`.
3. Per-PDF cleanup helper for `BlobDeleted`.

Phase 1 (this week): #2 above, single cron job. Correct for
add/update/delete with ≤30 min lag.

Phase 2 (when latency matters): add Event Grid + Queue + event
worker. Cron stays as nightly reconciliation.

### 5.3 Steady-state runbooks

| Task | Action |
|---|---|
| Re-index one file | Rewrite blob (new LMT triggers HWM detection) |
| Full re-index | `POST /indexers/<name>/reset` then `--run-indexer` |
| Rotate function key | `az functionapp keys set` then `deploy_search.py` |
| Bump skill version | Edit config, redeploy function |
| Clear index, keep schema | `DELETE /indexes/<name>`, redeploy, reset, run |
| Delete one PDF's records | Filter on `source_file`, batch delete by `id` |
| Index health | `python scripts/check_index.py` |

### 5.4 Failure modes — what breaks and what to do

| Symptom | Cause | Action |
|---|---|---|
| AOAI 429 mid-vision | TPM ceiling hit | Lower `--vision-parallel`, file quota increase |
| `processing_status="content_filter"` on a figure | Vision safety false-positive | Cached as permanent; record still indexed without description |
| 230 s skill timeout | Cache missing for that PDF | `preanalyze --only <pdf>`, then reset+run |
| Indexer 0 docs after 30 min | Bad `indexedFileNameExtensions`, MI missing Blob Reader, or first big PDF still running | Check execution history, role grants |
| Vector search empty | Dim mismatch (1,536 vs 3,072) | Verify deployment model = ada-002 |
| Skillset PUT 403 | Search MI missing CogSvc User on AI Services | Add role, wait 5–10 min |
| Skill 401/403 vs AOAI/DI | Function MI missing role | Add role, wait |
| Updated PDF serves stale content | `--incremental` skipped because `output.json` exists | Implement §5.2 #2; until then, manually delete `_dicache/<pdf>.*` |
| Function key rotated, skills 401 | Skillset still has old key | Re-run `deploy_search.py` |
| EG event missed | At-least-once delivery exhausted | Nightly reconciliation catches it |
| DLQ stuck on poison PDF | Corrupt / encrypted / oversized | Move PDF to `_quarantine/`, delete DLQ msg, notify owner |
| `Execution time quota of 1440 minutes` | Normal at scale | Schedule auto-resumes |
| PyMuPDF crash on encrypted PDF | Password-protected | `processing_status="encrypted"`, skip |
| Indexer reset wipes records | Bad projection mode | Confirm `skipIndexingParentDocuments` |

Detailed walkthroughs in [RUNBOOK.md §17](RUNBOOK.md#17-anticipated-failure-modes-and-runbooks).

---

## 6. Governance

### 6.1 Observability

| Layer | Where | Watch |
|---|---|---|
| Function App | App Insights | 5xx rate, per-skill latency |
| Indexer | Search → Execution history | Failed items, error msg |
| Job | Log Analytics | Exit code, stdout |
| Queue | Portal | Backlog (= depth) |
| Event Grid | Subscription metrics | Delivered, failed, DLQ |
| AOAI | Resource metrics | TPM, 429 rate |

Alerts (minimum set): indexer error 3× consecutive; job non-zero
exit; DLQ > 0; AOAI 429 > 5% / 15 min; Function 5xx > 1% / 10 min.

### 6.2 Cost

Per fresh PDF (~500 pp / ~1,500 figures): **~$11–18**, dominated by
DI + vision. Re-index after schema change: ~$0.10 (cache hit).

Levers in priority: don't break the cache; bump TPM, don't split
deployments; LRS storage (not GRS); drop indexer schedule to PT1H
once events are live.

### 6.3 DR & rollback

| State | DR plan |
|---|---|
| PDFs | SharePoint→blob automation owns it |
| Cache | Regenerable; not DR-critical |
| Index | Rebuild via reset + indexer (PDFs are source of truth) |
| Function code, search artifacts | Redeploy from git |
| `deploy.config.json` | **Back up to KeyVault / ops repo** — only thing not in git |

Rollback bad deploy: `git checkout <last-good>` + rerun the relevant
deploy script. No irrecoverable state in the system.

Cross-region failover: not active-active by design. Provision in
secondary region per §3.1, re-sync PDFs, run §4.2 bootstrap.
~Half a day per 1,000 PDFs, AOAI-bound.

---

## 7. When to break glass

Conditions that justify bypassing automation and running things
manually:

- Large initial load (>50 PDFs) — run preanalyze on a workstation
  with high `--vision-parallel`, then turn on the indexer.
- Skill version bump that re-stamps everything — controlled
  rollout via `--force` on a subset, validate, then enable for all.
- Suspected stale cache after upstream pipeline change — full
  `preanalyze --force` on affected PDFs.

Everything else should run through the automation path.

---

## 8. Pointers

- [RUNBOOK.md](RUNBOOK.md) — full reference (commands, schemas,
  failure walkthroughs)
- [ARCHITECTURE.md](ARCHITECTURE.md) — design rationale deep-dive
- [SEARCH_INDEX_GUIDE.md](SEARCH_INDEX_GUIDE.md) — index concepts
  for non-search engineers
- `search/*.json` — actual artifact bodies
- `scripts/preanalyze.py` — preanalyze reference

End.
