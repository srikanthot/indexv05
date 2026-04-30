# Indexing Repo — Operator & Infrastructure Runbook

The single source of truth for deploying, operating, and reasoning
about the Azure AI Search multimodal indexing pipeline that powers
the RAG layer in front of our PDF technical manuals.

This document is written for an engineer who has to **stand up the
Azure infrastructure from zero, deploy this repository's code into
it, run it in production, and troubleshoot it at 2 a.m.**. It covers
the *why* (why the architecture looks like it does, what we couldn't
use, what limitations we hit), the *how* (every step, with commands),
and the *when it breaks* (anticipated production failures and
runbooks to recover).

If you only have ten minutes, read §1, §2, and §17. Everything else
is depth.

---

## Table of contents

1. Architecture overview
2. Why this architecture exists (the rationale)
3. Service limitations that drove the design
4. Azure resources — what to create, with commands
5. Identity & role assignments
6. Quota & capacity planning
7. Local prerequisites
8. Repository layout
9. `deploy.config.json` reference
10. First-deploy bootstrap
11. Search artifacts (datasource, index, skillset, indexer)
12. Skillset — skill by skill
13. Index schema — field reference
14. Querying the index
15. Steady-state operations
16. Production automation (add / update / delete)
17. Anticipated failure modes and runbooks
18. Monitoring & observability
19. Cost considerations
20. Disaster recovery & rollback
21. CI / Testing
22. Licensing
23. Quick reference — commands
24. Related docs

---

## 1. Architecture overview

```
SharePoint ──(automated upstream)──► Blob container (PDFs)
                                          │
                                          │  BlobCreated / BlobDeleted
                                          ▼
                              ┌──────────────────────┐
                              │  Event Grid          │
                              │  + Storage Queue     │
                              └──────────────────────┘
                                          │
                                          ▼
                              ┌──────────────────────┐
                              │  Container App Job   │   long-running, no timeout
                              │  (event-triggered)   │   1) preanalyze --only <pdf>
                              │                      │   2) POST /indexers/run
                              └──────────────────────┘
                                          │
                                          │  writes _dicache/* alongside PDFs
                                          ▼
                              ┌──────────────────────┐
                              │  Azure AI Search     │
                              │  Indexer ─► Skillset │
                              │  ─► Index            │
                              └──────────────────────┘
                                          │
                                          │  custom skills hit:
                                          ▼
                              ┌──────────────────────┐
                              │  Azure Function App  │
                              │  (Python 3.11)       │
                              │  6 WebApi skills     │
                              │  reads cache + AOAI  │
                              └──────────────────────┘
```

Four peer record types live in one index:

| record_type | source                                      | chunk_id prefix |
|-------------|---------------------------------------------|-----------------|
| `text`      | page-sized markdown chunks                  | `txt_`          |
| `diagram`   | one record per figure (GPT-4.1 vision)      | `dgm_`          |
| `table`     | one record per table (DI → markdown)        | `tbl_`          |
| `summary`   | one record per PDF (GPT-4.1 summary)        | `sum_`          |

Hybrid retrieval at query time = BM25 (keyword) + ada-002 vector
similarity + Microsoft semantic reranker.

---

## 2. Why this architecture exists (the rationale)

This is not the simplest possible pipeline. Several choices look like
over-engineering until you understand the constraints. This section
explains the *why* behind every non-trivial decision so future
maintainers don't unwind something deliberate.

### 2.1 Why an offline `preanalyze` step at all

Three hard constraints make end-to-end-inside-the-skillset impossible
for our document size:

1. **Azure AI Search WebApi custom skills have a hard 230-second
   timeout** (Microsoft service-side, not configurable).
2. **Document Intelligence prebuilt-layout takes 3–15 minutes on a
   500-page manual.** It cannot finish in 230 s.
3. **Per-figure vision analysis × 1,500 figures takes 10–75 minutes
   per PDF.** It cannot finish in 230 s, even fully parallelized.

Calling DI or vision *live* from a custom skill means every large PDF
times out and never indexes. So we run DI + vision offline in
`scripts/preanalyze.py`, write results to blob (`_dicache/`), and the
indexer's custom skills read from cache in milliseconds.

The bonus: **caching protects us from paying vision costs twice**.
Re-indexing after a schema fix re-uses cached vision JSON instead of
re-calling GPT-4 Vision on 1,500 figures.

### 2.2 Why six custom skills instead of using only built-in ones

| Need                                         | Native built-in available?        | What we do |
|----------------------------------------------|-----------------------------------|------------|
| Markdown extraction with h1–h3 headers       | ✅ `DocumentIntelligenceLayoutSkill` | Use it |
| Chunking with overlap                        | ✅ `SplitSkill`                    | Use it |
| ada-002 embeddings                           | ✅ `AzureOpenAIEmbeddingSkill`     | Use it ×4 |
| Map a chunk to its physical PDF page         | ❌                                | `extract-page-label` (custom) |
| Per-figure cropping + GPT-4 Vision JSON      | ❌ (only generic image tags)      | `process-document` + `analyze-diagram` (custom, cache-aware) |
| Tables → markdown with caption + page span   | ❌                                | `shape-table` (custom) |
| Build a tuned `chunk_for_semantic` string    | ❌                                | `build-semantic-string` (custom) |
| One summary record per PDF                   | ❌                                | `build-doc-summary` (custom) |

The Microsoft "Import and Vectorize Data" wizard (commercial only,
not Gov) handles simple text PDFs end-to-end without any code. It
**does not** support per-figure GPT-4 Vision with OCR — its image
handling produces generic tags like `"diagram"` or `"chart"`. For
technical manuals where the user query is *"what is wired to terminal
X7 on the overcurrent relay?"*, generic tags fail. So we keep custom
skills for the parts the wizard can't do, and use built-ins for the
parts it can.

### 2.3 Why Azure Container Apps Jobs (and not Functions) for automation

Production automation has to handle a 2,000-page PDF that takes 2–4
hours to preanalyze.

| Host                          | Execution time limit | Verdict for preanalyze |
|-------------------------------|----------------------|------------------------|
| Azure Functions (Consumption) | 10 min               | ❌ fails mid-vision |
| Azure Functions (Premium)     | 30 min               | ❌ fails on large PDFs |
| Azure App Service WebJob      | indefinite, but always-warm cost | ⚠ wasteful at idle |
| **Azure Container Apps Job**  | **no limit**         | ✅ pay-per-run, scales on queue |
| Azure Batch                   | indefinite           | ⚠ heavyweight for this scale |

Container Apps Jobs match the workload shape: long-running, bursty,
event-triggered, idle most of the day.

### 2.4 Why Azure OpenAI ada-002 for embeddings (still, in 2026)

- Available in **both** Azure Commercial and Gov Cloud.
- 1,536 dimensions — strong precision-to-cost ratio.
- Cheapest per token in the embedding family.
- Hybrid retrieval + semantic reranker covers any weakness vs. newer
  models for technical-manual workloads. We measured.

If a future requirement breaks this, the migration path is: deploy
`text-embedding-3-large` (3,072 dims), bump `text_vector.dimensions`
in the index, re-deploy, reset + run indexer. No other code changes.

### 2.5 Why GPT-4.1 (vision) for diagrams and summaries

- Smaller vision models can't reliably read tiny technical labels,
  rotated nameplates, or low-contrast wire tags.
- Cost is dominated by **call count**, not per-call tokens, and we
  cache every result in blob — so per-PDF vision cost is paid once.
- GPT-4.1 is available in both Commercial and Gov as of writing; the
  exact deployment name is in `deploy.config.json`.

### 2.6 Why hybrid retrieval (BM25 + vector + semantic reranker)

| Mode                | Catches                                       |
|---------------------|-----------------------------------------------|
| BM25 (keyword)      | Exact part numbers (`W130537`), literal phrases |
| Vector (ada-002)    | Conceptual queries, paraphrases, synonyms     |
| Semantic reranker   | Lifts the most relevant chunk to top of N=50   |

Pure vector misses exact-token hits. Pure keyword misses paraphrases.
Reranker alone over either misses the other side. Hybrid covers all
three. The cost is ~50–150 ms extra query latency, which is fine for
RAG.

### 2.7 Why managed identity end-to-end

- No secrets to rotate (except the function key, which is rotated by
  re-running `deploy_search.py`).
- No connection strings in repo or config.
- Simpler audit story for compliance.
- Works the same in Commercial and Gov Cloud.

The only place that supports key auth is local dev (`AUTH_MODE=key`).
Never use it in any deployed environment.

---

## 3. Service limitations that drove the design

Reality check on Azure as of writing. Memorize these — they explain
nearly every "why didn't we just…?" question.

### 3.1 Azure AI Search

| Limitation                                              | Impact |
|---------------------------------------------------------|--------|
| Custom WebApi skill timeout: **230 s, immutable**       | Why preanalyze exists |
| Indexer execution time cap: 2 h (Free/Basic), 24 h (S1+) | Choose Standard for prod |
| Index doc max size: 16 MB                               | Don't store image_b64 in records |
| Index field count max: 1,000                            | We use ~40 — plenty of headroom |
| Vector field max dims: 3,072 (single-vector retrieval)  | We use 1,536, fine |
| Indexers per service: 50 (Basic), 200 (S1+)             | One per environment is enough |
| Indexer batch size: 1–1,000                             | We set `batchSize=1` for big-PDF safety |
| Indexer change detection: **HighWaterMark only** for blob | We use `metadata_storage_last_modified` |
| Indexer deletion detection: **NativeBlobSoftDelete only** | Requires soft-delete on storage |
| Semantic ranker: GA in Commercial, GA in Gov            | Both fine |

### 3.2 Azure Functions

| Plan                | Time limit | MI for outbound | Verdict for our skills |
|---------------------|------------|-----------------|------------------------|
| Consumption (Y1)    | 10 min     | yes             | ✅ Skills finish well under 230 s |
| Premium (EP1+)      | 30 min     | yes             | ✅ if you need VNet integration |
| Dedicated (App Service) | unbounded | yes          | ⚠ wasteful at idle |

Custom skills always finish under 230 s by design (preanalyze does
the heavy lifting). Consumption is the right plan unless you need
VNet integration to reach private endpoints.

### 3.3 Azure OpenAI

| Limitation                                            | Workaround |
|-------------------------------------------------------|------------|
| TPM quota per deployment is **the throughput ceiling** | Plan capacity (§6) |
| ada-002 default S0: ~240K TPM                         | Bump quota for big initial loads |
| GPT-4.1 vision default: ~80K TPM                      | `--vision-parallel 40` saturates without 429 storms |
| Token-per-minute, not requests-per-minute             | Long figures eat budget faster |
| Region availability differs                            | gpt-4.1 not in every region — check before choosing RG region |
| Content filter is **always on** for vision            | False-positives on nameplates → permanent-fail cache (§17) |

### 3.4 Azure Document Intelligence

| Limitation                                  | Workaround |
|---------------------------------------------|------------|
| File size limit: 500 MB                     | Most manuals fine; reject above |
| Page limit per analyze call: 2,000 pages    | Largest manuals near the edge |
| POST body limit: ~50 MB                     | Use `urlSource` + SAS for big PDFs |
| Service-side analyze timeout: ~30 min       | OK for our 5–15 min typical |

### 3.5 Storage / Event Grid

| Limitation                                   | Impact |
|----------------------------------------------|--------|
| Storage Queue message: 64 KB                 | We pass blob name only; fine |
| Queue TTL default: 7 days                    | Set explicitly to avoid surprises |
| Event Grid delivery: **at-least-once**       | Idempotency in worker required |
| Event Grid retry budget: ~24 h               | DLQ after exhaustion |
| Blob soft-delete retention: 7–365 days       | Set to 30 days for our case |

### 3.6 Azure Government Cloud differences

If running in Gov:

- Endpoint suffixes change: `.azure.us` instead of `.windows.net`,
  `.cognitiveservices.azure.us`, etc. The deploy script handles this
  via `search.endpoint` from config.
- Search OAuth scope: `https://search.azure.us/.default` (set in
  `scripts/deploy_search.py`).
- Some preview features lag Commercial by 6–18 months. Verified GA
  in Gov as of writing: `DocumentIntelligenceLayoutSkill`,
  `AzureOpenAIEmbeddingSkill`, semantic ranker, vectorizers.
- "Import and Vectorize Data" wizard: limited in Gov.
- Newer model SKUs (gpt-4o, o-series) may not yet be in Gov; we use
  gpt-4.1 because it is.

---

## 4. Azure resources — what to create, with commands

The repo does **not** provision Azure resources. This section is the
infra-creation guide. All commands assume `az login` and a chosen
subscription (`az account set --subscription <id>`).

### 4.1 Resource list

| # | Resource                                   | Purpose | SKU / config we use |
|---|--------------------------------------------|---------|---------------------|
| 1 | Resource group                             | Container | RG in same region as data sources |
| 2 | Storage account                            | PDFs + `_dicache/` cache + queue | Standard_LRS; **soft-delete ON**; HNS optional |
| 3 | Blob container                             | Source PDFs | name = `manuals` (or per `deploy.config.json`) |
| 4 | Storage Queue                              | Event-driven worker buffer | name = `pdf-events` |
| 5 | Azure AI Search                            | Index + indexer + skillset | Standard (S1) for prod; system-assigned MI |
| 6 | Azure OpenAI                               | Embeddings + vision + summary | Standard; deployments below |
| 7 | AOAI deployment: `text-embedding-ada-002`  | Embeddings | 1,536 dims; ≥240K TPM |
| 8 | AOAI deployment: `gpt-4.1`                 | Vision + summary | ≥80K TPM |
| 9 | Document Intelligence                      | Layout extraction | Standard (S0); region with prebuilt-layout |
| 10 | AI Services multi-service                  | Bills the built-in Layout skill | Standard (S0) |

> **Note on rows 9 + 10.** These can be **the same resource**. A multi-service `kind=CognitiveServices` account bundles Document Intelligence with the AI Services billing surface, so a single account fills both roles. This is the common layout in **GCC High** and Azure Gov, where teams typically provision one multi-service account rather than two separate resources. In `deploy.config.json`, point `documentIntelligence.endpoint` and `aiServices.subdomainUrl` at the same URL; in `scripts/assign_roles.sh`, set `DI` and `AISVC` to the same name. AOAI (row 6) is always a separate `kind=OpenAI` resource — it cannot live inside a multi-service account.
| 11 | Function App (Linux, Python 3.11, v4)      | Hosts custom skills | Consumption (Y1); system-assigned MI |
| 12 | Application Insights                       | Function App telemetry | Workspace-based |
| 13 | Container Apps environment                 | Hosts automation jobs | Consumption profile |
| 14 | Container App Job (event-triggered)        | Per-PDF preanalyze | replicas 0–3, queue scaler |
| 15 | Container App Job (cron)                   | Nightly reconciliation | cron `0 2 * * *` |
| 16 | Event Grid System Topic                    | Blob events fan-out | On the storage account |
| 17 | Event Grid Subscription ×2                 | BlobCreated, BlobDeleted | Filter `.pdf`, exclude `_dicache/` |

### 4.2 Region choice

Use one region for all of: storage, search, function, container apps,
queue, event grid. Cross-region traffic costs money and adds latency.

For Azure OpenAI, **gpt-4.1 region availability is the binding
constraint**. Pick a region where gpt-4.1 is available (check
`az cognitiveservices account list-models`) and provision everything
else there. If gpt-4.1 is in `eastus2` but data residency needs
`westus3`, deploy AOAI in `eastus2` and accept the cross-region hop
for that one service only.

### 4.3 Creation commands (commercial cloud)

```bash
# Variables
SUB=<your-subscription-id>
RG=rg-mm-manuals
LOC=eastus2          # change to a region with gpt-4.1
PREFIX=mmman         # used for naming

az account set --subscription "$SUB"
az group create -n "$RG" -l "$LOC"

# 1. Storage account + container + soft-delete + queue
az storage account create \
  -n ${PREFIX}stg -g "$RG" -l "$LOC" \
  --sku Standard_LRS --kind StorageV2 --allow-blob-public-access false
az storage account blob-service-properties update \
  --account-name ${PREFIX}stg -g "$RG" \
  --enable-delete-retention true --delete-retention-days 30
az storage container create \
  --account-name ${PREFIX}stg -n manuals --auth-mode login
az storage queue create \
  --account-name ${PREFIX}stg -n pdf-events --auth-mode login

# 2. Azure AI Search (Standard, MI on)
az search service create \
  -n ${PREFIX}-search -g "$RG" -l "$LOC" \
  --sku standard --identity-type SystemAssigned --partition-count 1 --replica-count 1

# 3. Azure OpenAI + deployments
az cognitiveservices account create \
  -n ${PREFIX}-aoai -g "$RG" -l "$LOC" --kind OpenAI --sku S0 \
  --custom-domain ${PREFIX}-aoai --yes
az cognitiveservices account deployment create \
  -n ${PREFIX}-aoai -g "$RG" \
  --deployment-name text-embedding-ada-002 \
  --model-name text-embedding-ada-002 --model-version 2 --model-format OpenAI \
  --sku-name Standard --sku-capacity 240
az cognitiveservices account deployment create \
  -n ${PREFIX}-aoai -g "$RG" \
  --deployment-name gpt-4.1 \
  --model-name gpt-4.1 --model-version <current> --model-format OpenAI \
  --sku-name Standard --sku-capacity 80

# 4. Document Intelligence
az cognitiveservices account create \
  -n ${PREFIX}-di -g "$RG" -l "$LOC" --kind FormRecognizer --sku S0 \
  --custom-domain ${PREFIX}-di --yes

# 5. AI Services multi-service (for built-in Layout skill billing)
az cognitiveservices account create \
  -n ${PREFIX}-ais -g "$RG" -l "$LOC" --kind CognitiveServices --sku S0 \
  --custom-domain ${PREFIX}-ais --yes

# 6. App Insights
az monitor app-insights component create \
  -a ${PREFIX}-appi -g "$RG" -l "$LOC" --kind web --application-type web

# 7. Function App (Linux, Python 3.11, Functions v4) + MI
az storage account create -n ${PREFIX}funcstg -g "$RG" -l "$LOC" --sku Standard_LRS --kind StorageV2
az functionapp plan create -g "$RG" -n ${PREFIX}-plan --location "$LOC" --is-linux --sku Y1
az functionapp create \
  -g "$RG" -n ${PREFIX}-func -p ${PREFIX}-plan \
  --runtime python --runtime-version 3.11 --functions-version 4 \
  --os-type Linux --storage-account ${PREFIX}funcstg \
  --app-insights ${PREFIX}-appi
az functionapp identity assign -g "$RG" -n ${PREFIX}-func

# 8. Container Apps environment
az containerapp env create \
  -n ${PREFIX}-cae -g "$RG" -l "$LOC"

# 9. Container App Jobs are deployed by `scripts/deploy_jobs.sh`
#    (see §16). They reference an image in ACR — create one too:
az acr create -n ${PREFIX}acr -g "$RG" --sku Basic --admin-enabled false
```

After these commands run, fill `deploy.config.json` with the resulting
endpoints and resource IDs, then proceed to §10.

### 4.4 What Storage soft-delete actually does for us

The indexer's `dataDeletionDetectionPolicy` is
`NativeBlobSoftDeleteDeletionDetectionPolicy`. It only fires when:

1. Blob soft-delete is enabled on the storage account, **and**
2. A blob is deleted (which marks it soft-deleted, not gone).

When the indexer next runs, it sees the soft-deleted state and emits
delete operations to the index. If soft-delete is off, deletion is
invisible to the indexer and stale records persist forever.

---

## 5. Identity & role assignments

All outbound auth is AAD. Create these once per environment.

| Principal               | Scope                     | Role                                                       | Why |
|-------------------------|---------------------------|------------------------------------------------------------|-----|
| Function App MI         | Storage account           | Storage Blob Data Reader                                   | Reads `_dicache/` for cache hits |
| Function App MI         | Azure OpenAI              | Cognitive Services OpenAI User                             | Embeds + vision fallback |
| Function App MI         | Document Intelligence     | Cognitive Services User                                    | DI fallback path (rarely used after preanalyze) |
| Function App MI         | Search service            | Search Index Data Reader                                   | Image-hash cache lookup against index |
| Search service MI       | Storage account           | Storage Blob Data Reader                                   | Indexer reads PDFs |
| Search service MI       | Azure OpenAI              | Cognitive Services OpenAI User                             | Embedding skill + vectorizer |
| Search service MI       | AI Services account       | Cognitive Services User                                    | Built-in Layout skill billing |
| Container App Job MI    | Storage account           | Storage Blob Data Contributor + Storage Queue Data Message Processor | Reads PDFs, writes cache, drains queue |
| Container App Job MI    | Azure OpenAI              | Cognitive Services OpenAI User                             | Vision in preanalyze |
| Container App Job MI    | Document Intelligence     | Cognitive Services User                                    | DI in preanalyze |
| Container App Job MI    | Search service            | Search Service Contributor                                 | POST `/indexers/run` |
| Deploying principal     | Search service            | Search Service Contributor + Search Index Data Contributor | PUT search artifacts |
| Deploying principal     | Function App's RG         | Contributor                                                | Set App Settings, fetch function key |

Role propagation is not instantaneous. After grants, **wait 5–10
minutes** before retrying a failed call. This is the most common
"why is auth still 403" cause.

The only secret in the system is the Function App's `default`
function key, which `scripts/deploy_search.py` fetches live and
embeds in the skillset URIs at deploy time. Rotate by re-running
the script.

---

## 6. Quota & capacity planning

Plan quota *before* you trigger an initial load on 50+ PDFs. The
default deployment quotas will not survive a full run.

### 6.1 AOAI capacity

| Phase                     | Token consumer                  | Sizing rule of thumb |
|---------------------------|---------------------------------|----------------------|
| Preanalyze: vision        | gpt-4.1                         | 1,500 figures × ~3K input + ~500 output ≈ 5M tokens / PDF. At 80K TPM, ~60 min / PDF. |
| Preanalyze: summary       | gpt-4.1                         | One call / PDF, ~10K tokens. Negligible. |
| Indexer: embeddings       | text-embedding-ada-002          | ~5K chunks × 200 tokens ≈ 1M tokens / PDF. At 240K TPM, ~5 min / PDF. |
| Vectorizer (query time)   | text-embedding-ada-002          | Per query, negligible. |

**Default S0 quotas** (240K ada / 80K vision TPM) are enough for
~50 PDF/day steady state. For a fresh load of 100+ PDFs at once, file
a quota increase request 1–2 weeks ahead.

### 6.2 Search service sizing

| Tier   | Storage | Indexers/Indexes | Use case |
|--------|---------|------------------|----------|
| Basic  | 2 GB    | 5 / 5            | Dev only |
| S1     | 25 GB   | 50 / 50          | Prod for ≤ ~5M records |
| S2     | 100 GB  | 50 / 50          | Larger corpora |

Estimate: **~5K records per 500-page manual** (text + diagrams +
tables + summary). 200 manuals ≈ 1M records ≈ 5 GB. S1 is fine.

### 6.3 Concurrency limits we set

| Knob                                               | Value | Why |
|----------------------------------------------------|-------|-----|
| Indexer `batchSize`                                | 1     | One PDF at a time; big PDFs need full quota |
| `process-document-skill` `degreeOfParallelism`     | 2     | DI cache reads are I/O bound but cheap |
| `analyze-diagram-skill` `degreeOfParallelism`      | 4     | AOAI vision TPM ceiling |
| `extract-page-label-skill` `batchSize`             | 5     | Cheap CPU work |
| Container App Job replicas (event-triggered)       | 0–3   | Cap concurrent vision per worker × replicas ≤ TPM |
| `preanalyze.py --vision-parallel`                  | 40    | Empirically saturates 80K TPM without 429 storms |

---

## 7. Local prerequisites

Tools needed on the machine that runs deploy / preanalyze:

- Azure CLI (`az`) — logged in via `az login`
- Azure Functions Core Tools v4 (`func`)
- Python 3.11+
- `jq` (used by `deploy_function.sh`)
- PowerShell 5.1+ or bash
- Docker (only if you build the Container App Job image locally)

Python deps (`pip install -r requirements.txt`):

- `azure-identity`, `httpx`, `azure-storage-blob`, `pymupdf`,
  `azure-functions`, `ruff` (dev)

---

## 8. Repository layout

```
function_app/                 Python Azure Functions app (custom skills)
  function_app.py             HTTP routes -> skill handlers
  host.json
  requirements.txt
  local.settings.json.example
  shared/
    skill_io.py               WebApi envelope + error translation
    credentials.py            Managed identity helper (lazy)
    config.py                 Typed env-var access
    aoai.py                   Azure OpenAI client (MI-first)
    di_client.py              Document Intelligence REST + blob fetch
    search_cache.py           Image-hash cache lookup
    ids.py                    Stable chunk_id helpers
    page_label.py             Printed page label + physical span
    sections.py               DI section index + surrounding text
    pdf_crop.py               PyMuPDF figure cropping
    tables.py                 DI tables -> markdown (merge + split)
    process_document.py       Orchestrates DI + crop + sections + tables
    process_table.py          Per-table shaper
    semantic.py               chunk_for_semantic builder
    diagram.py                Per-figure vision analysis (hash-cached)
    summary.py                Per-document summary

search/                       Azure AI Search REST bodies (templated)
  datasource.json
  index.json
  skillset.json
  indexer.json

scripts/
  deploy_function.sh / .ps1   Publish function code + apply App Settings
  deploy_search.py            Render + PUT search artifacts via AAD
  preanalyze.py               Offline DI + vision pre-analysis
  run_preanalyze.sh / .ps1    Preanalyze wrappers
  check_index.py              Index health report
  smoke_test.py               Post-deploy validation
  reset_indexer.ps1           Reset + run indexer
  preflight.py                Checks config + role assignments
  diagnose.py                 Misc diagnostics

tests/
  test_unit.py
  test_e2e_simulator.py

docs/
  ARCHITECTURE.md
  SEARCH_INDEX_GUIDE.md
  RUNBOOK.md                  This file
  validation.md

deploy.config.example.json
requirements.txt
ruff.toml
README.md
```

---

## 9. `deploy.config.json` reference

Single source of truth read by every deploy script.

```bash
cp deploy.config.example.json deploy.config.json
```

| Key                                          | Purpose |
|----------------------------------------------|---------|
| `functionApp.name` / `resourceGroup`         | Target Function App |
| `search.endpoint`                            | `https://<svc>.search.windows.net` (or `.azure.us` in Gov) |
| `search.artifactPrefix`                      | Prefix → `<prefix>-ds`, `-index`, `-skillset`, `-indexer` |
| `azureOpenAI.endpoint` / `apiVersion`        | AOAI endpoint (`2024-12-01-preview`+ for gpt-4.1) |
| `azureOpenAI.chatDeployment`                 | gpt-4.1 deployment name (summaries) |
| `azureOpenAI.visionDeployment`               | gpt-4.1 deployment name (vision; same as chat usually) |
| `azureOpenAI.embedDeployment`                | ada-002 deployment name (1,536 dims) |
| `documentIntelligence.endpoint` / `apiVersion` | DI resource |
| `aiServices.subdomainUrl`                    | AI Services multi-service endpoint |
| `storage.accountResourceId`                  | Full ARM ID — used for `ResourceId=…` datasource |
| `storage.pdfContainerName`                   | Container with source PDFs |
| `appInsights.connectionString`               | Wired into Function App |
| `skillVersion`                               | Stamped on every record; bump to invalidate cache |

The function key is **not** stored here. `deploy_search.py` fetches
it live at deploy time.

---

## 10. First-deploy bootstrap

### 10.1 Sign in

```bash
az login
# For Gov Cloud:
# az cloud set --name AzureUSGovernment && az login
```

### 10.2 Preflight check

```bash
python scripts/preflight.py --config deploy.config.json
```

Verifies the resources from §4 exist and the role assignments from §5
are in place.

### 10.3 Deploy the Function App code

```bash
scripts/deploy_function.sh deploy.config.json
# Windows: .\scripts\deploy_function.ps1 -Config .\deploy.config.json
```

Publishes the Python package and applies App Settings:

```
AUTH_MODE=mi
AOAI_ENDPOINT, AOAI_API_VERSION, AOAI_CHAT_DEPLOYMENT, AOAI_VISION_DEPLOYMENT
DI_ENDPOINT, DI_API_VERSION
SEARCH_ENDPOINT, SEARCH_INDEX_NAME
SKILL_VERSION
APPLICATIONINSIGHTS_CONNECTION_STRING
```

### 10.4 Deploy the Search artifacts

```bash
python scripts/deploy_search.py --config deploy.config.json
```

Renders every `<PLACEHOLDER>` in `search/*.json`, fetches the function
key live, then `PUT`s four artifacts via AAD:

1. `datasources/<prefix>-ds`
2. `indexes/<prefix>-index`
3. `skillsets/<prefix>-skillset`
4. `indexers/<prefix>-indexer`

Idempotent. Fails loud if any placeholder is unrendered.

### 10.5 Pre-analyze the PDFs

```bash
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output
```

Cache artifacts land under `<container>/_dicache/`:

| Blob                                      | Contents |
|-------------------------------------------|----------|
| `_dicache/<pdf>.di.json`                  | Full DI layout result |
| `_dicache/<pdf>.crop.<fig>.json`          | Per-figure base64 PNG + bbox |
| `_dicache/<pdf>.vision.<fig>.json`        | Per-figure GPT-4V JSON |
| `_dicache/<pdf>.output.json`              | Final assembled output (= "done" marker) |

### 10.6 Run the indexer + validate

```bash
python scripts/deploy_search.py --config deploy.config.json --run-indexer
python scripts/smoke_test.py      --config deploy.config.json
```

`smoke_test.py` triggers the indexer, waits for `status=success`, then
asserts record counts, required fields, and that `physical_pdf_pages`
covers the declared start+end on text/table records. Non-zero exit
on any failure.

---

## 11. Search artifacts

### 11.1 Data source (`search/datasource.json`)

- Type: `azureblob`
- Credentials: `ResourceId=<storage ARM id>;` (MI auth, no keys)
- Change detection: `HighWaterMarkChangeDetectionPolicy` on
  `metadata_storage_last_modified`
- Deletion detection: `NativeBlobSoftDeleteDeletionDetectionPolicy`

### 11.2 Index (`search/index.json`)

Schema for all four record types. Field reference in §13.

- `id` is the key (string, keyword analyzer).
- `text_vector` is `Collection(Edm.Single)` 1,536 dims, HNSW profile
  `mm-hnsw-profile`, cosine.
- Semantic config `mm-semantic-config` sets `source_file` as title
  and prioritizes `chunk_for_semantic`, `chunk`,
  `diagram_description`, `surrounding_context`.
- Vectorizer `aoai-vectorizer` lets the service embed queries
  server-side — clients do not need to embed themselves.

### 11.3 Skillset (`search/skillset.json`)

Ordered pipeline of 13 skills (see §12).

- `cognitiveServices`: `AIServicesByIdentity` + `subdomainUrl` — the
  Search service MI authenticates to the AI Services account.
- `indexProjections.parameters.projectionMode`:
  `skipIndexingParentDocuments` — only flattened child records are
  indexed.

### 11.4 Indexer (`search/indexer.json`)

- `schedule.interval`: `PT15M` (drop to `PT1H` once event-driven
  automation is in place — §16)
- `parameters.batchSize`: `1`
- `maxFailedItems`: `-1`
- `parameters.configuration.indexedFileNameExtensions`: `.pdf`
- `dataToExtract`: `contentAndMetadata`
- `imageAction`: `none`

---

## 12. Skillset — skill by skill

### Built-in

| # | Skill                              | Context                                      | Purpose |
|---|------------------------------------|----------------------------------------------|---------|
| 1 | `DocumentIntelligenceLayoutSkill`  | `/document`                                  | PDF → markdown with h1–h3 + page markers |
| 2 | `SplitSkill`                       | `/document/markdownDocument/*`               | Split into ~1,200-char pages with 200-char overlap |
| 3 | `AzureOpenAIEmbeddingSkill` ×4     | text / figures / tables / summary            | 1,536-dim ada-002 embeddings |

### Custom (WebApi → our Function App)

All POST to `https://<FUNCTION_APP_HOST>/api/<route>?code=<FUNCTION_KEY>`.

| # | Skill (route)                          | Context                                   | Batch / parallelism | Purpose |
|---|----------------------------------------|-------------------------------------------|---------------------|---------|
| 4 | `process-document` (`/api/process-document`) | `/document`                          | 1 / 2   | Reads `_dicache/<pdf>.output.json`; emits `enriched_figures` + `enriched_tables` |
| 5 | `extract-page-label` (`/api/extract-page-label`) | `/document/markdownDocument/*/pages/*` | 5 / 4   | Page span + chunk_id per text chunk |
| 6 | `analyze-diagram` (`/api/analyze-diagram`) | `/document/enriched_figures/*`         | 1 / 4   | Per-figure vision JSON (cache-aware) |
| 7 | `shape-table` (`/api/shape-table`)     | `/document/enriched_tables/*`             | 5 / 4   | Per-table record |
| 8 | `build-semantic-string-text` (`/api/build-semantic-string`) | `/document/markdownDocument/*/pages/*` | 10 / 4 | `chunk_for_semantic` for text |
| 9 | `build-semantic-string-diagram` (`/api/build-semantic-string`) | `/document/enriched_figures/*` | 10 / 4 | `chunk_for_semantic` for diagrams |
| 10 | `build-doc-summary` (`/api/build-doc-summary`) | `/document`                          | 1 / 2   | One summary record per PDF |

Every custom skill returns `processing_status` and `skill_version` so
broken records can be filtered at query time.

### Index projections

Four selectors flatten nested outputs into one index per record type:

| Selector (parentKeyFieldName) | Source context                           | Produces |
|-------------------------------|------------------------------------------|----------|
| `text_parent_id`              | `/document/markdownDocument/*/pages/*`   | 1 record per text chunk |
| `dgm_parent_id`               | `/document/enriched_figures/*`           | 1 record per figure |
| `tbl_parent_id`               | `/document/enriched_tables/*`            | 1 record per table |
| `sum_parent_id`               | `/document`                              | 1 record per PDF |

---

## 13. Index schema — field reference

### Identity

| Field                                                      | Type   | Notes |
|------------------------------------------------------------|--------|-------|
| `id`                                                       | string | Key. Keyword analyzer. Auto-generated. |
| `chunk_id`                                                 | string | Stable, human-readable (`txt_…`, `dgm_…`, `tbl_…`, `sum_…`). |
| `parent_id`                                                | string | Hash of source PDF URL; groups records from one PDF. |
| `text_parent_id` / `dgm_parent_id` / `tbl_parent_id` / `sum_parent_id` | string | Only the one matching `record_type` is populated. |
| `record_type`                                              | string | `text` / `diagram` / `table` / `summary`. |

### Content

| Field                | Type | Notes |
|----------------------|------|-------|
| `chunk`              | string, searchable | Raw content (markdown / vision description / table markdown / summary). |
| `chunk_for_semantic` | string, searchable | Chunk + source + headers + page info, tuned for the reranker. |
| `text_vector`        | `Collection(Edm.Single)`, 1,536, `stored=false`, `retrievable=false` | ada-002 embedding of `chunk_for_semantic`. |

### Page + location

| Field                    | Type | Notes |
|--------------------------|------|-------|
| `physical_pdf_page`      | Int32, filterable, sortable | First physical page (1-indexed). |
| `physical_pdf_page_end`  | Int32, filterable | Last physical page. |
| `physical_pdf_pages`     | `Collection(Edm.Int32)`, filterable, facetable | Every page touched. Use `physical_pdf_pages/any(p: p eq 42)`. |
| `printed_page_label`     | string, searchable, filterable | Label as printed (`"iv"`, `"18-33"`). |
| `printed_page_label_end` | string | End label for multi-page chunks. |
| `layout_ordinal`         | Int32, filterable, sortable | DI section ordinal. |

### Header chain

| Field                                | Type | Notes |
|--------------------------------------|------|-------|
| `header_1` / `header_2` / `header_3` | string, searchable | h1/h2/h3 chain the chunk sits under. |

### Diagram-only

| Field                 | Type | Notes |
|-----------------------|------|-------|
| `figure_id`           | string | DI-assigned id (`"134.3"`). |
| `figure_ref`          | string, searchable, filterable | Human ref (`"Figure 18.117"`). |
| `figure_bbox`         | string (JSON) | `{page, x_in, y_in, w_in, h_in}` for UI highlight. |
| `diagram_description` | string, searchable | GPT-4.1 description + OCR labels. |
| `diagram_category`    | string, filterable, facetable, keyword | `circuit_diagram`, `wiring_diagram`, `schematic`, `line_diagram`, `block_diagram`, `pid_diagram`, `flow_diagram`, `control_logic`, `exploded_view`, `parts_list_diagram`, `nameplate`, `equipment_photo`, `decorative`, `unknown`. |
| `has_diagram`         | bool, filterable, facetable | True only for useful diagrams. |
| `image_hash`          | string | SHA-256 of cropped PNG; dedupes repeated logos. |

### Table-only

| Field             | Type | Notes |
|-------------------|------|-------|
| `table_row_count` | Int32 | After continuation-merge + split. |
| `table_col_count` | Int32 |  |
| `table_caption`   | string, searchable, filterable | Caption above the table. |

### Source reference

| Field         | Type | Notes |
|---------------|------|-------|
| `source_file` | string, searchable/filterable/sortable/facetable | Filename. |
| `source_url`  | string, retrievable | Full blob URL (UI link). |
| `source_path` | string, filterable | Same as `source_url`, used in filters. |

### Provenance + health

| Field                 | Type | Notes |
|-----------------------|------|-------|
| `surrounding_context` | string, searchable | Sentences around the figure/table from body text. |
| `processing_status`   | string, filterable, facetable | `"ok"`, `"no_image"`, `"content_filter"`, … |
| `skill_version`       | string, filterable, facetable | `SKILL_VERSION` stamp. |

### Admin classification (reserved / null today)

| Field            | Type | Notes |
|------------------|------|-------|
| `operationalarea`| string, searchable | Populated out-of-band (not by this pipeline). |
| `functionalarea` | string, searchable |  |
| `doctype`        | string, searchable |  |

### Vector + semantic configs

```json
"vectorSearch": {
  "algorithms": [{ "name": "mm-hnsw-algo", "kind": "hnsw",
    "hnswParameters": { "m": 8, "efConstruction": 400, "efSearch": 500, "metric": "cosine" }}],
  "profiles": [{ "name": "mm-hnsw-profile", "algorithm": "mm-hnsw-algo", "vectorizer": "aoai-vectorizer" }],
  "vectorizers": [{ "name": "aoai-vectorizer", "kind": "azureOpenAI",
    "azureOpenAIParameters": { "resourceUri": "...", "deploymentId": "...", "modelName": "text-embedding-ada-002" }}]
}

"semantic": {
  "defaultConfiguration": "mm-semantic-config",
  "configurations": [{
    "name": "mm-semantic-config",
    "prioritizedFields": {
      "titleField":            { "fieldName": "source_file" },
      "prioritizedContentFields": [
        { "fieldName": "chunk_for_semantic" }, { "fieldName": "chunk" },
        { "fieldName": "diagram_description" }, { "fieldName": "surrounding_context" }],
      "prioritizedKeywordsFields": [
        { "fieldName": "header_1" }, { "fieldName": "header_2" }, { "fieldName": "header_3" },
        { "fieldName": "figure_ref" }, { "fieldName": "table_caption" },
        { "fieldName": "printed_page_label" }, { "fieldName": "diagram_category" }]
    }
  }]
}
```

---

## 14. Querying the index

### Hybrid (recommended)

```http
POST /indexes/<prefix>-index/docs/search?api-version=2024-11-01-preview
Authorization: Bearer <aad-token>

{
  "search": "buried underground distribution",
  "queryType": "semantic",
  "semanticConfiguration": "mm-semantic-config",
  "captions": "extractive",
  "answers": "extractive|count-3",
  "vectorQueries": [{ "kind": "text", "text": "buried underground distribution", "fields": "text_vector" }],
  "top": 10
}
```

### Diagrams only

```json
{ "search": "fault indicator",
  "filter": "record_type eq 'diagram' and has_diagram eq true",
  "top": 20 }
```

### Page range inside one PDF

```json
{ "search": "fusing",
  "filter": "source_file eq 'ED-ED-UGC.pdf' and physical_pdf_page ge 1000 and physical_pdf_page le 1100" }
```

### Records that touch a specific page

```json
{ "search": "*",
  "filter": "physical_pdf_pages/any(p: p eq 1337)" }
```

### Citation projection

```json
{ "search": "...",
  "select": "chunk_id, source_file, physical_pdf_page, printed_page_label, header_1, header_2, header_3, chunk, figure_bbox, record_type",
  "top": 5 }
```

---

## 15. Steady-state operations

### Re-index a single file

Indexer change detection is high-water-mark on
`metadata_storage_last_modified`. Rewrite the blob (same content, new
LMT) to force re-pickup.

### Full re-index

```bash
az rest --method post \
  --url "https://<search>.search.windows.net/indexers/<prefix>-indexer/reset?api-version=2024-05-01-preview"
python scripts/deploy_search.py --config deploy.config.json --run-indexer
```

### Rotate the function key

```bash
az functionapp keys set -g <rg> -n <func> --key-type functionKeys --key-name default
python scripts/deploy_search.py --config deploy.config.json
```

### Bump `skillVersion`

Edit `deploy.config.json`, re-run `scripts/deploy_function.sh`.
Records re-processed from that point carry the new version; older
records keep the old one until touched.

### Manual incremental catch-up

```bash
python scripts/preanalyze.py --config deploy.config.json --incremental
python scripts/preanalyze.py --config deploy.config.json --cleanup
az rest --method post --url "<search>/indexers/<prefix>-indexer/run?api-version=2024-11-01-preview"
```

### Clear the index (keep schema)

Azure AI Search has no native truncate.

1. `DELETE /indexes/<prefix>-index?api-version=…`
2. `python scripts/deploy_search.py --config deploy.config.json`
3. Reset + run the indexer.

### Delete one PDF's records

```json
{ "search": "*", "filter": "source_file eq 'OLD.pdf'", "select": "id", "top": 10000 }
```

```http
POST /indexes/<prefix>-index/docs/index?api-version=2024-11-01-preview
{ "value": [ { "@search.action": "delete", "id": "<record-id>" } ] }
```

### Index health

```bash
python scripts/check_index.py --config deploy.config.json
```

### Indexer status

```bash
az rest --method get \
  --url "https://<search>.search.windows.net/indexers/<prefix>-indexer/status?api-version=2024-11-01-preview" \
  -o json
```

---

## 16. Production automation — add / update / delete

In production the operator does not run preanalyze or trigger the
indexer manually. SharePoint→blob is automated upstream; everything
from the blob container onwards must also be automated.

### 16.1 The correctness problem with naïve cron

| Event | Blob emits | What we need to do | Naïve cron behaviour |
|---|---|---|---|
| **Add** (new PDF) | `BlobCreated` | preanalyze → index | ✅ `--incremental` handles it |
| **Update** (overwrite, same name) | `BlobCreated` (new LMT) | invalidate cache → re-preanalyze → re-index | ❌ `--incremental` skips because `_dicache/<pdf>.output.json` already exists. Indexer reads stale cache. |
| **Delete** | `BlobDeleted` | drop records + drop `_dicache/<pdf>.*` | ✅ if soft-delete on + deletion policy in place |

The update case is the killer. The recommended architecture solves
it by reacting to blob events directly.

### 16.2 Recommended architecture

```
Storage Account  (blob soft-delete ON)
   │
   ├── Event Grid System Topic
   │      ├── Subscription: BlobCreated   (subject endsWith ".pdf",
   │      │                                 NOT under "_dicache/")
   │      └── Subscription: BlobDeleted   (same filter)
   │                  │
   │                  ▼
   │         Storage Queue  (visibility 5 min, DLQ after 5 dequeues)
   │                  │
   │                  ▼
   │   Container App Job — event-triggered (KEDA queue-length scaler)
   │     replicas: min 0, max 2-3
   │     Created  -> invalidate cache for that PDF
   │                 -> preanalyze --only <pdf> --force
   │                 -> POST /indexers/<prefix>-indexer/run
   │     Deleted  -> remove _dicache/<pdf>.* blobs
   │
   └── Container App Job — cron 0 2 * * *   (nightly reconciliation)
          preanalyze --incremental
          preanalyze --cleanup
          POST /indexers/<prefix>-indexer/run

   Indexer schedule: PT1H   (third independent safety net)
```

### 16.3 Why these specific choices

- **Container Apps Jobs over Functions:** unbounded execution time;
  pay-per-run; KEDA scales on queue length.
- **Event-driven over pure cron:** `BlobCreated` fires on overwrite,
  so updates are correct. Latency drops from 30–60 min to 5–15 min.
- **Nightly reconciliation kept anyway:** Event Grid is
  at-least-once but not guaranteed-once. ~$0.05/day buys a safety
  net that catches missed events.
- **Indexer schedule kept at PT1H:** third layer. If both the event
  path and the nightly job fail, lag is one hour, not "until someone
  notices".

### 16.4 Pros and cons

**Pros**

- Correct for add / update / delete, no manual intervention.
- 5–15 min upload-to-searchable.
- Three independent safety nets (events → nightly sweep → PT1H).
- Bounded AOAI cost via replica cap.
- Works for the 2,000-page edge-case PDF.
- Burst absorption via queue.

**Cons**

- More infra than a single cron: ~1–2 days to stand up cleanly.
- Two small `preanalyze.py` changes required first (§16.6).
- DLQ requires monitoring for poison PDFs.
- One 2,000-page PDF ties up a worker for ~3 h; bursts queue.
- Extra MI grants on the Container App Job principal.

### 16.5 Two-phase rollout (recommended)

**Phase 1 — make it correct on a cron:** add LMT-aware invalidation
to `preanalyze.py`. Deploy one Container App Job on cron every
30 min running `preanalyze --incremental && preanalyze --cleanup &&
POST /indexers/run`. Correct for add / update / delete with ≤30 min
worst-case lag.

**Phase 2 — add the event path:** stand up Event Grid + Storage
Queue + an event-triggered Container App Job in front of Phase 1.
The Phase 1 cron stays as the nightly reconciliation. Additive — no
rework.

Ship correctness first. Only add the event path if business actually
needs sub-30-min lag.

### 16.6 Code gaps to close in `preanalyze.py`

Localized changes (~100 LOC):

1. **`--only <blob-name>`** — process a single PDF (event worker uses
   it).
2. **LMT-aware invalidation** — if `pdf.lastModified >
   output.json.lastModified`, re-run even under `--incremental`.
   Required for both Phase 1 and Phase 2 update correctness.
3. **Per-PDF cleanup helper** — delete `_dicache/<pdf>.*` for one
   name (used by event worker on `BlobDeleted`).

### 16.7 Required prerequisites checklist

Before turning automation on:

- [ ] Blob soft-delete enabled on the storage account
- [ ] `dataDeletionDetectionPolicy` present in `search/indexer.json`
- [ ] LMT-aware invalidation implemented in `preanalyze.py` (Phase 1)
- [ ] `--only <pdf>` and per-PDF cleanup helpers exist (Phase 2)
- [ ] Container App Job MI has the role grants in §5
- [ ] Event Grid filters: `subject endsWith ".pdf"` AND `subject does
      not contain "/_dicache/"`
- [ ] DLQ has an alert wired to on-call
- [ ] Job stdout shipped to Log Analytics; non-zero exit alerts

---

## 17. Anticipated failure modes and runbooks

A senior engineer's library of "this will break, here's what to do".

### 17.1 AOAI: 429 throttling mid-vision

**Symptom:** preanalyze logs `429 Too Many Requests` from AOAI; some
figures end up with `_error.transient` cache entries.

**Cause:** vision deployment hit TPM ceiling. `--vision-parallel` too
high for the deployment's quota.

**Resolution:**
1. Reduce `--vision-parallel` to 30 or 20.
2. If quota is genuinely insufficient, file a quota increase
   (Azure portal → AOAI resource → Quotas).
3. Re-run `preanalyze --incremental` — failed figures retry
   automatically up to 3 times; permanent failures are cached and
   skipped.

### 17.2 AOAI: vision content filter on a legitimate figure

**Symptom:** figure has `processing_status="content_filter"`,
`diagram_description=""`. Often happens on nameplates or
electrical-schematic warning labels.

**Cause:** safety system false-positive (e.g. "warning" + small
red triangle interpreted as violence).

**Resolution:** none in code — false-positives are cached as
permanent failures. The figure is still indexed with page, headers,
and bbox; retrieval via surrounding text still works. Manual review
only if a critical figure is missed for retrieval.

### 17.3 AOAI: model deployment not found / region mismatch

**Symptom:** `404 Resource not found` from AOAI for the chat or
vision deployment.

**Cause:** `azureOpenAI.endpoint` in config points at a different
region than the one with the deployment, or `chatDeployment`/
`visionDeployment` name is wrong.

**Resolution:** verify with
`az cognitiveservices account deployment list -n <aoai> -g <rg>`
and update config.

### 17.4 Document Intelligence: timeout on huge PDF

**Symptom:** preanalyze fails with `OperationCancelled` or
`AnalyzerTimeout` on a 1,500+ page PDF.

**Cause:** DI service-side analyze timeout (~30 min) or POST body
limit (~50 MB).

**Resolution:** preanalyze already uses the `urlSource` path with a
SAS URL for big PDFs (no body limit). If timeout still hits,
re-trigger that one PDF — DI succeeds on second try ~90% of the
time. If consistently failing, the PDF may exceed the 2,000-page
DI limit; need to split it manually.

### 17.5 Custom skill: `230s timeout` exceeded

**Symptom:** indexer execution history shows
`"Skill execution timed out"` on `process-document` or
`analyze-diagram`.

**Cause:** preanalyze cache for that PDF is missing or incomplete;
the skill is falling back to live DI / vision.

**Resolution:**
1. `python scripts/preanalyze.py --status` — verify
   `<pdf>.output.json` exists.
2. If not, run `preanalyze --only <pdf>` (Phase 2 capability) or full
   `preanalyze --incremental`.
3. Reset + run the indexer.

### 17.6 Indexer: `Execution time quota of 120/1440 minutes reached`

**Symptom:** indexer stops mid-run with quota message.

**Cause:** normal on initial loads. Basic = 120 min cap, S1+ =
1,440 min cap.

**Resolution:** the schedule auto-resumes on the next tick
(default 15 min). No action needed. To accelerate: trigger
`POST /indexers/run` immediately — Azure honours it after a brief
cool-off.

### 17.7 Indexer: 0 documents after 30 minutes

**Symptom:** indexer status `running` but `itemsProcessed=0`.

**Likely causes & checks:**
1. **`indexedFileNameExtensions` not `.pdf`** — verify in
   `indexer.json`. Cache `.json` blobs would be picked up otherwise
   and fail the skillset.
2. **Datasource pointing at wrong container or path prefix.** Check
   `datasource.json`.
3. **Search MI missing `Storage Blob Data Reader`.** Add and wait
   5 min for propagation.
4. **First big PDF still being processed.** Check execution history
   → "current" tab.

### 17.8 Vector search returns nothing

**Symptom:** `vectorQueries` returns empty even when text search
finds matches.

**Cause:** dimension mismatch — index says 1,536, deployment is
3-large (3,072), or vice versa.

**Resolution:**
```bash
# Verify deployment model
az cognitiveservices account deployment show \
  -n <aoai> -g <rg> --deployment-name <embed-deployment>
```
If wrong, re-deploy the right model OR update
`text_vector.dimensions` in `index.json` and reset the index.

### 17.9 Skillset PUT fails with 403 on AI Services

**Symptom:** `deploy_search.py` errors on the skillset PUT with
`Access denied to Azure AI Services`.

**Cause:** Search service MI is missing `Cognitive Services User`
role on the AI Services account (referenced via
`AIServicesByIdentity`).

**Resolution:** add the role; wait 5–10 min for propagation; re-run
`deploy_search.py`.

### 17.10 Function skill returns 401 / 403 from AOAI or DI

**Symptom:** indexer execution history shows skill call returned
401 or 403.

**Cause:** Function App MI missing the right role on AOAI or DI.

**Resolution:** assign roles per §5; wait 5–10 min; re-run.

### 17.11 Updated PDF still serves old content

**Symptom:** user re-uploaded a PDF; indexer ran; content still
shows old text.

**Cause:** preanalyze `--incremental` skipped because
`_dicache/<pdf>.output.json` exists from the old version. Custom
skills read stale cache.

**Resolution (Phase 1):** add LMT-aware invalidation per §16.6.
Until then, **manually**:
1. Delete `_dicache/<pdf>.*` for the affected file.
2. `preanalyze --incremental` — re-runs that PDF.
3. Reset + run the indexer for that one document.

### 17.12 Function key rotated; skillset starts failing

**Symptom:** all custom-skill calls return 401 after a key rotation.

**Cause:** the skillset's URIs embed the old key; the new key is
live but the skillset wasn't updated.

**Resolution:** `python scripts/deploy_search.py --config
deploy.config.json` — re-renders and PUTs the skillset with the
fresh key.

### 17.13 Storage 503 mid-upload during preanalyze

**Symptom:** transient `ServerBusy` on cache writes.

**Cause:** Azure Storage backpressure.

**Resolution:** preanalyze has 3-retry-with-backoff on every blob
op; usually self-heals. If a phase repeatedly fails, re-run that
phase only (`--phase di` etc.).

### 17.14 Event Grid delivery missed an event

**Symptom:** PDF uploaded; user can't find it; no event in worker
logs.

**Cause:** Event Grid retry budget exhausted or subscription
misconfigured.

**Resolution:** the **2 a.m. nightly reconciliation** picks it up.
If you can't wait, manually `preanalyze --only <pdf> --force` and
trigger the indexer.

### 17.15 Container App Job stuck on a poison PDF

**Symptom:** queue depth not draining; one PDF keeps reappearing in
DLQ.

**Cause:** corrupt PDF, password-protected, or DI consistently
failing.

**Resolution:**
1. Inspect the DLQ message; identify the PDF.
2. `preanalyze --only <pdf> --force` locally with verbose logs to
   see the underlying error.
3. If genuinely unprocessable: move the PDF to a `_quarantine/`
   prefix in the container and remove the DLQ message. Notify the
   uploader.

### 17.16 Indexer reset wipes documents unexpectedly

**Symptom:** after `indexers/<name>/reset`, the index empties.

**Cause:** `reset` clears the high-water-mark; the next run
re-processes everything. **Existing documents are not deleted by
`reset`**, but a *bug* in the skillset that emits delete operations
during re-projection can clear them.

**Resolution:** check `indexProjections.parameters.projectionMode`
is `skipIndexingParentDocuments` (already set); never use
`overwrite` mode unless intentional. To recover: run the indexer
to repopulate.

### 17.17 Search service quota: too many indexers / indexes

**Symptom:** `403 Quota exceeded` on PUT.

**Cause:** Basic tier caps at 5 indexers / 5 indexes. Dev
environment hit it.

**Resolution:** move dev to a separate Search service or upgrade to
Standard.

### 17.18 PyMuPDF crash on encrypted PDF

**Symptom:** preanalyze logs `MuPDF: cannot open password-protected
document`.

**Cause:** input PDF has owner/user password.

**Resolution:** preanalyze records `processing_status="encrypted"`
and skips the file. Indexer still indexes the metadata. Notify the
uploader.

---

## 18. Monitoring & observability

### 18.1 Sources of truth

| Layer | Where | What to watch |
|---|---|---|
| Function App | App Insights / `az webapp log tail` | Per-skill latency, error rate, exceptions |
| Indexer | Search → Indexers → Execution history | Items processed, failed items, error message |
| Container App Job | Log Analytics (linked workspace) | Job exit code, stdout, replica count |
| Storage Queue | Portal / `az storage queue stats` | Approx message count (= backlog) |
| Event Grid | Subscription metrics | Delivered, failed, DLQ count |
| AOAI | Azure portal → AOAI resource → Metrics | Token usage, 429 rate, latency |
| Cost | Cost Management | Daily spend per service |

### 18.2 Alerts to wire

- Indexer execution status `transientFailure` or `error` for
  3 consecutive runs.
- Function App 5xx rate > 1% for 10 min.
- Container App Job non-zero exit (any).
- Storage Queue DLQ message count > 0.
- AOAI 429 rate > 5% for 15 min.
- Storage `BlobCount` for `_dicache/` growing without bound (orphan
  cleanup not running).

### 18.3 Dashboards worth building

Two dashboards, kept simple:

**Pipeline health** — single page with: indexer last-run status,
queue depth, DLQ count, recent App Insights exceptions, last
nightly-reconciliation exit.

**Cost** — daily spend split by AOAI / DI / Search / Storage /
Container Apps, with month-to-date forecast.

---

## 19. Cost considerations

Order-of-magnitude per-PDF for a typical 500-page / 1,500-figure
manual:

| Service | Cost driver | Per-PDF approx |
|---|---|---|
| AOAI gpt-4.1 vision | 1,500 figures × ~3.5K tokens | ~$5–8 (cached, paid once) |
| AOAI ada-002 embeddings | ~5K chunks × 200 tokens | ~$0.05 (per re-embed) |
| Document Intelligence | Per-page billing | ~$5–7 |
| AI Services (Layout skill in indexer) | Per-page | ~$1–2 |
| Search storage | ~25 MB / PDF | negligible (~$0.02/mo) |
| Storage (PDFs + cache) | 50 MB PDF + 100 MB cache | negligible |
| Function App Consumption | Skill calls | negligible |
| Container Apps Jobs | Worker minutes | <$1 / PDF |

**Total ~$11–18 per fresh PDF.** Re-indexing after a schema change
costs only the embedding + Search re-projection (~$0.10), thanks to
the cache.

Cost levers, in priority order:
1. **Don't re-run vision unnecessarily.** The cache already does
   this; preserve it.
2. **Bump quota, don't add deployments.** Two deployments doesn't
   give 2× TPM if quota is per-region.
3. **Use Standard storage redundancy (LRS).** GRS doubles cost for
   no gain on this workload.
4. **Drop the indexer schedule to PT1H once events are live.**
   Eliminates ~95% of empty indexer runs.

---

## 20. Disaster recovery & rollback

### 20.1 What's stateful and how to recover

| State | Where | Recovery |
|---|---|---|
| Source PDFs | Storage account | Re-sync from SharePoint (already automated upstream) |
| Cache (`_dicache/`) | Storage account | **Regenerable** via preanalyze; not strictly DR-critical |
| Search index | Search service | Rebuild via reset + indexer run; ~30 min/PDF, no data loss because PDFs are the source of truth |
| Function App code | The repo + ACR (for jobs) | `scripts/deploy_function.sh` redeploys |
| Search artifacts | The repo | `scripts/deploy_search.py` redeploys |
| `deploy.config.json` | Local / KeyVault / ops repo | **Back up separately** — it has all endpoints/IDs |

There is no irrecoverable state in this system. Worst case, you can
rebuild the entire pipeline from the repo + a fresh resource group
in ~2 hours plus preanalyze time.

### 20.2 Rollback procedure for a bad deploy

If a deploy of either function code or search artifacts breaks
production:

1. **Function App:** redeploy the previous commit:
   ```bash
   git checkout <last-good-sha> -- function_app/
   scripts/deploy_function.sh deploy.config.json
   git checkout HEAD -- function_app/
   ```
2. **Search artifacts:** the JSON files are in git; check them out
   and re-run `deploy_search.py`.
3. **Index schema change that's not backwards-compatible:** delete
   the index, redeploy, reset + run indexer. ~30 min/PDF to
   repopulate, no source data loss.

### 20.3 Cross-region failover

We do not run active-active. The pipeline is a single-region
deployment by design (cost, simplicity, and PDFs are not
latency-sensitive). If the primary region fails:

1. Provision a new resource group in the secondary region using §4
   commands.
2. Re-sync PDFs to the new storage account (separate runbook owned
   by the SharePoint→blob automation).
3. Run §10 bootstrap end-to-end. Steady-state in ~half a day per
   1,000 PDFs (limited by AOAI TPM).

---

## 21. CI / Testing

`.github/workflows/ci.yml` runs on every PR and push to `main`:

- `python tests/test_unit.py`
- `python tests/test_e2e_simulator.py`
- `ruff check function_app tests scripts`

Tests require no Azure credentials. Make these required checks in
branch protection.

Local dev against real Azure:

```bash
cp function_app/local.settings.json.example function_app/local.settings.json
# Fill in endpoints; AUTH_MODE=mi uses your az login credential chain.
cd function_app && func start
```

---

## 22. Licensing

`PyMuPDF` (used in `shared/pdf_crop.py`) is **AGPL-3.0**. For a
closed-source internal Azure Function App this is generally fine —
the AGPL network clause is triggered by distributing modified
source, not by running the library behind a function endpoint. If
this pipeline ships as part of a public SaaS, review PyMuPDF's
terms or swap to `pypdfium2`.

---

## 23. Quick reference — commands

```bash
# Bootstrap
cp deploy.config.example.json deploy.config.json
az login
python scripts/preflight.py --config deploy.config.json

# Deploy function + search artifacts
scripts/deploy_function.sh deploy.config.json
python scripts/deploy_search.py --config deploy.config.json

# Preanalyze (offline DI + vision caching)
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output

# Run + validate
python scripts/deploy_search.py --config deploy.config.json --run-indexer
python scripts/smoke_test.py    --config deploy.config.json
python scripts/check_index.py   --config deploy.config.json

# Reset + re-run indexer
.\scripts\reset_indexer.ps1

# Rotate function key
az functionapp keys set -g <rg> -n <func> --key-type functionKeys --key-name default
python scripts/deploy_search.py --config deploy.config.json
```

---

## 24. Related docs

- [README.md](../README.md) — top-level project readme
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — design rationale (deeper
  on preanalyze trade-offs)
- [docs/SEARCH_INDEX_GUIDE.md](SEARCH_INDEX_GUIDE.md) — index
  concepts & schema reference for non-search engineers
- [docs/validation.md](validation.md) — manual validation checklist
- [scripts/PREANALYZE_README.md](../scripts/PREANALYZE_README.md) —
  team-facing preanalyze runbook
- [search/index.json](../search/index.json) /
  [skillset.json](../search/skillset.json) /
  [indexer.json](../search/indexer.json) /
  [datasource.json](../search/datasource.json) — actual search
  artifact bodies
