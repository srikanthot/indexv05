# Runbook — everything operational

Single document covering setup, day-to-day operations, validation,
content capture, incident response, Jenkins pipeline configuration,
the Power BI dashboard spec, and first-time-setup troubleshooting.

For the high-level architecture overview, see [../README.md](../README.md).
For the chatbot integration spec, see
[../CHATBOT_INTEGRATION.md](../CHATBOT_INTEGRATION.md).

## Table of contents

1. [Daily operations](#1-daily-operations) — running the pipeline, monitoring
2. [Preanalyze runbook](#2-preanalyze-runbook) — team-facing operational guide for the offline script
3. [Validation](#3-validation) — local + cloud checks
4. [Content capture](#4-content-capture) — what gets extracted from each file type
5. [Incident response](#5-incident-response) — top failure modes + recovery
6. [Jenkins pipeline setup](#6-jenkins-pipeline-setup) — agent, credentials, Jenkinsfiles
7. [Power BI dashboard spec](#7-power-bi-dashboard-spec) — Cosmos schema + recommended tiles
8. [First-time setup troubleshooting](#8-first-time-setup-troubleshooting) — SSL, proxy, firewall, 403 diagnostics

---


# 1. Daily operations


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
Upload PDFs ──► Blob container (PDFs)
                    │
                    ▼
       ┌─────────────────────────────┐
       │  Jenkins agent OR laptop    │   runs:
       │  python scripts/deploy.py   │     bootstrap → preanalyze
       │  (one command)              │     → deploy_search → reset_indexer
       │                             │     → heal_until_done → check_index
       └─────────────────────────────┘
                    │
                    │  writes _dicache/* alongside the PDFs
                    ▼
       ┌─────────────────────────────┐
       │  Azure AI Search            │
       │  Indexer ─► Skillset        │   schedule: PT15M
       │  ─► Index                   │
       └─────────────────────────────┘
                    │
                    │  custom skills hit:
                    ▼
       ┌─────────────────────────────┐
       │  Azure Function App         │
       │  (Python 3.11, Linux)       │   6 WebApi skills:
       │  reads _dicache/ + AOAI     │   page label, process-document,
       │                             │   analyze-diagram, process-table,
       │                             │   build-semantic-string, summary
       └─────────────────────────────┘
                    │
                    │  status persistence
                    ▼
       ┌─────────────────────────────┐
       │  Cosmos DB (optional)       │   run history + per-PDF state
       │  → Power BI dashboard       │   for operators / managers
       └─────────────────────────────┘
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

### 2.3 Why a Jenkins agent (and not Functions or Container Apps) for automation

Production automation has to handle a 2,000-page PDF that takes 2–4
hours to preanalyze. Hosting options considered:

| Host                          | Execution time limit | Verdict for preanalyze |
|-------------------------------|----------------------|------------------------|
| Azure Functions (Consumption) | 10 min               | ❌ fails mid-vision |
| Azure Functions (Premium)     | 30 min               | ❌ fails on large PDFs |
| Azure App Service WebJob      | indefinite, but always-warm cost | ⚠ wasteful at idle |
| Azure Container Apps Job      | no limit             | ⚠ extra infra to provision and monitor |
| **Jenkins agent VM**          | **no limit**         | ✅ already provisioned for CI; same identity model |
| Azure Batch                   | indefinite           | ⚠ heavyweight for this scale |

The Jenkins agent matches the workload shape (long-running, bursty,
idle most of the day) and reuses CI infra the team already operates.
`Jenkinsfile.deploy` runs on push-to-main; `Jenkinsfile.run` runs the
operational pipeline nightly on a cron. Both call the same scripts a
developer runs from their laptop — no Container Apps, Event Grid, or
Storage Queue infrastructure required.

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

### 3.5 Storage

| Limitation                                   | Impact |
|----------------------------------------------|--------|
| Blob soft-delete retention: 7–365 days       | Set to 30 days for our case |
| Per-blob max size: 5 TB                      | Far above any realistic PDF |
| Blob HEAD/GET: needs `Storage Blob Data Reader` for AAD | RBAC granted to Function App MI + Search service MI |

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
| 2 | Storage account                            | PDFs + `_dicache/` cache | Standard_LRS; **soft-delete ON** |
| 3 | Blob container                             | Source PDFs | name = `manuals` (or per `deploy.config.json`) |
| 4 | Azure AI Search                            | Index + indexer + skillset | Standard (S1) for prod; system-assigned MI |
| 5 | Azure OpenAI                               | Embeddings + vision + summary | Standard; deployments below |
| 6 | AOAI deployment: `text-embedding-ada-002`  | Embeddings | 1,536 dims; ≥240K TPM |
| 7 | AOAI deployment: `gpt-4.1`                 | Vision + summary | ≥80K TPM |
| 8 | Document Intelligence                      | Layout extraction | Standard (S0); region with prebuilt-layout |
| 9 | AI Services multi-service                  | Bills the built-in Layout skill | Standard (S0) |
| 10 | Function App (Linux, Python 3.11, v4)     | Hosts custom skills | Consumption (Y1); system-assigned MI; **AUTO_HEAL_ENABLED=true** |
| 11 | Application Insights                      | Function App telemetry | Workspace-based |
| 12 | Cosmos DB (optional)                      | Run history + per-PDF state for the Power BI dashboard | Serverless or low RU; SQL API; database `indexing` |

> **Note on rows 8 + 9.** These can be **the same resource**. A multi-service `kind=CognitiveServices` account bundles Document Intelligence with the AI Services billing surface, so a single account fills both roles. This is the common layout in **GCC High** and Azure Gov, where teams typically provision one multi-service account rather than two separate resources. In `deploy.config.json`, point `documentIntelligence.endpoint` and `aiServices.subdomainUrl` at the same URL; in `scripts/assign_roles.py`, the script handles same-name or distinct-name cases automatically. AOAI (row 5) is always a separate `kind=OpenAI` resource — it cannot live inside a multi-service account.

> **What the repo does NOT need.** No Container Apps, no Container App Jobs, no Event Grid, no Storage Queue, no Service Bus, no ACR. The pipeline runs from a Jenkins agent (or a developer laptop) calling `python scripts/deploy.py`. Earlier designs explored an event-driven Container Apps Jobs architecture; production simplified to the Jenkins cron model because it reuses CI infra and has no extra moving parts.

### 4.2 Region choice

Use one region for all of: storage, search, function, AOAI, DI, Cosmos.
Cross-region traffic costs money and adds latency.

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

# 1. Storage account + container + soft-delete
az storage account create \
  -n ${PREFIX}stg -g "$RG" -l "$LOC" \
  --sku Standard_LRS --kind StorageV2 --allow-blob-public-access false
az storage account blob-service-properties update \
  --account-name ${PREFIX}stg -g "$RG" \
  --enable-delete-retention true --delete-retention-days 30
az storage container create \
  --account-name ${PREFIX}stg -n manuals --auth-mode login

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

# 4. Document Intelligence (skip if using a single multi-service account)
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

# 8. Cosmos DB (optional, for run history + Power BI dashboard)
az cosmosdb create -n ${PREFIX}-cosmos -g "$RG" \
  --kind GlobalDocumentDB --default-consistency-level Session \
  --locations regionName="$LOC" failoverPriority=0
az cosmosdb sql database create -a ${PREFIX}-cosmos -g "$RG" -n indexing
# Containers (indexing_run_history, indexing_pdf_state) auto-create on first write
```

After these commands run, fill `deploy.config.json` with the resulting
endpoints and resource IDs, then proceed to §10. The `python
scripts/deploy.py` path (§10.0) handles app settings, role
assignments, function code, search artifacts, preanalyze, and indexer
in one call.

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
| Jenkins agent MI (or laptop user) | Storage account     | Storage Blob Data Contributor                               | preanalyze writes `_dicache/`; reconcile purges blobs |
| Jenkins agent MI (or laptop user) | Azure OpenAI        | Cognitive Services OpenAI User                              | Vision calls during preanalyze |
| Jenkins agent MI (or laptop user) | Document Intelligence | Cognitive Services User                                   | DI submissions during preanalyze |
| Jenkins agent MI (or laptop user) | Search service      | Search Service Contributor + Search Index Data Contributor  | PUT search artifacts; delete chunks during reconcile |
| Jenkins agent MI (or laptop user) | Cosmos DB account   | Cosmos DB Built-in Data Contributor                         | Write run history + per-PDF state |
| Deploying principal               | Function App's RG   | Contributor                                                 | Set App Settings, fetch function key |

All of these are granted in one shot by
`python scripts/assign_roles.py --config deploy.config.json`. The
script reads identities from the config, looks up principal IDs
automatically, and skips assignments that already exist.

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
| `preanalyze.py --vision-parallel`                  | 40    | Empirically saturates 80K TPM without 429 storms |
| `preanalyze.py --concurrency` (parallel PDFs)      | 3     | Default in `deploy.py`; raise carefully on bigger AOAI quotas |
| `heal_until_done.py --max-iterations`              | 8     | Caps healing loop duration; raise for very large initial loads |

---

## 7. Local prerequisites

Tools needed on the machine that runs deploy / preanalyze (Jenkins
agent or developer laptop — same list):

- Azure CLI (`az`) — logged in via `az login`
- Azure Functions Core Tools v4 (`func`) — used by `deploy_function.{sh,ps1}`
- Python 3.11+
- `jq` (used by `deploy_function.sh` on Linux/Mac)
- PowerShell 5.1+ or bash
- LibreOffice (optional) — enables figure extraction from DOCX/PPTX/XLSX
  by auto-converting to PDF first. Pipeline works without it; non-PDF
  formats just won't have figures extracted.

Python deps (`pip install -r requirements.txt`):

- `azure-identity`, `httpx`, `azure-storage-blob`, `pymupdf`,
  `azure-functions`, `azure-cosmos`, `ruff` (dev)

---

## 8. Repository layout

```
function_app/                 Python Azure Functions app (custom skills)
  function_app.py             HTTP routes + auto-heal timer
  host.json
  requirements.txt
  local.settings.json.example
  shared/
    auto_heal.py              Timer-triggered self-recovery
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
  deploy.py                   ★ ONE-COMMAND end-to-end deploy
  bootstrap.py                Provision + RBAC + app settings + function code
  heal_until_done.py          Loop indexer + force-reindex until 100%
  preanalyze.py               Stage 1: DI + Vision, populates _dicache/
  reconcile.py                Detect added / edited / deleted PDFs
  run_pipeline.py             Daily operations orchestrator (used by Jenkinsfile.run)
  check_index.py              Coverage + diagnostics
  cosmos_writer.py            Cosmos DB persistence helper
  deploy_search.py            Render + PUT search artifacts via AAD
  deploy_function.sh / .ps1   Publish function code + apply App Settings
  smoke_test.py               Post-deploy validation gate
  reset_indexer.sh / .ps1     Reset + run indexer
  diagnose.py                 Health probe (function app + indexer)
  diagnose_403.py             Targeted 403 diagnosis for deploy_search
  inspect_pdf.py              Per-PDF cache + index inspection
  preflight.py                Pre-deploy environment validation
  assign_roles.py             One-shot RBAC bootstrapper (reads config)
  reap_stale_rows.py          Cleanup stale index records
  cleanup_environment.py      Fresh-start cleanup of search artifacts + cache
  convert.py                  DOCX/PPTX/XLSX -> PDF via LibreOffice
  force_reindex_blobs.ps1     Force reindex of specific PDFs
  rerun_failed_docs.ps1       Surgical retry of failed PDFs
  pipeline_lock.py            Cross-script pipeline lock

tests/
  test_unit.py
  test_e2e_simulator.py
  test_filename_spaces.py
  test_techmanual_capture.py

docs/
  RUNBOOK.md                  This file (everything operational)

Jenkinsfile.deploy            CI pipeline (push to main)
Jenkinsfile.run               CI pipeline (nightly cron + manual)
.github/workflows/ci.yml      PR gate: pytest + ruff
CHATBOT_INTEGRATION.md        Hand-off spec for the chatbot dev team
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

Two paths — pick one. **10.0** is the production path (one command,
chains every step). **10.1 onwards** is the manual line-by-line
sequence for when you want to run a single stage at a time.

### 10.0 ONE command (recommended)

```bash
# 1. Configure (one-time)
cp deploy.config.example.json deploy.config.json
# Fill in identifiers for your environment. Never commit this file.

# 2. Authenticate
az cloud set --name AzureUSGovernment    # skip if commercial cloud
az login
az account set --subscription "<your-sub-id>"

# 3. One-shot RBAC (only the first time, on a fresh env)
python scripts/assign_roles.py --config deploy.config.json
# wait 10 minutes for RBAC propagation

# 4. ONE command does everything else
python scripts/deploy.py --config deploy.config.json --auto-fix
```

`deploy.py` runs in order:
1. **bootstrap** — RBAC sanity check, Cosmos DB database, Function App
   app settings (`AUTO_HEAL_ENABLED=true`), function code deploy
2. **preanalyze** — DI + GPT-4 Vision, populates `_dicache/`
3. **deploy_search** — index, skillset, datasource, indexer
4. **reset_indexer** — fresh full indexer pass against the populated cache
5. **heal_until_done** — loop until every PDF has a `summary` record
6. **check_index** — final coverage report

Exit code 0 = every PDF in the container is indexed. Exit code 1 = a
deterministic failure on specific PDFs (output names them); usually a
Function App memory hit on the biggest files — bump the App Service
Plan SKU and re-run.

Skip flags for partial re-runs:

```bash
python scripts/deploy.py --config deploy.config.json --skip-bootstrap        # infra + code already deployed
python scripts/deploy.py --config deploy.config.json --skip-preanalyze       # cache already complete
python scripts/deploy.py --config deploy.config.json --skip-heal-loop        # deploy + trigger once, no looping
```

For everything else, jump to §11. The rest of §10 is the manual stage-by-stage walkthrough.

### 10.1 Sign in

```bash
az login
# For Gov Cloud:
# az cloud set --name AzureUSGovernment && az login
az account set --subscription "<your-sub-id>"
```

### 10.2 Grant RBAC (one-time per environment)

```bash
python scripts/assign_roles.py --config deploy.config.json
```

Reads `deploy.config.json`, discovers principal IDs of every managed
identity, and assigns the roles from §5. Idempotent. After this,
**wait 10 minutes** for propagation before the next step.

### 10.3 Preflight check

```bash
python scripts/preflight.py --config deploy.config.json
```

Verifies the resources from §4 exist and the role assignments from §5
are in place. Exits non-zero with a clear message if anything is off.

### 10.4 Deploy the Function App code

```bash
# Linux/Mac:
bash scripts/deploy_function.sh deploy.config.json

# Windows:
.\scripts\deploy_function.ps1 -Config .\deploy.config.json
```

Publishes the Python package and applies App Settings:

```
AUTH_MODE=mi
AOAI_ENDPOINT, AOAI_API_VERSION, AOAI_CHAT_DEPLOYMENT, AOAI_VISION_DEPLOYMENT
DI_ENDPOINT, DI_API_VERSION
SEARCH_ENDPOINT, SEARCH_INDEX_NAME
SKILL_VERSION
AUTO_HEAL_ENABLED=true
APPLICATIONINSIGHTS_CONNECTION_STRING
```

### 10.5 Pre-analyze the PDFs

```bash
python scripts/preanalyze.py --config deploy.config.json --incremental
```

Runs DI + GPT-4 Vision on every PDF that doesn't yet have a complete
cache. `--incremental` skips fully-cached PDFs, so re-running picks up
where it left off.

Cache artifacts land under `<container>/_dicache/`:

| Blob                                      | Contents |
|-------------------------------------------|----------|
| `_dicache/<pdf>.di.json`                  | Full DI layout result |
| `_dicache/<pdf>.crop.<fig>.json`          | Per-figure base64 PNG + bbox |
| `_dicache/<pdf>.vision.<fig>.json`        | Per-figure GPT-4V JSON |
| `_dicache/<pdf>.output.json`              | Final assembled output (= "done" marker) |

For phased runs (rare; useful only when debugging one stage):

```bash
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output
```

### 10.6 Deploy the Search artifacts

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

### 10.7 Reset + run the indexer

```bash
# Linux/Mac:
bash scripts/reset_indexer.sh

# Windows:
.\scripts\reset_indexer.ps1
```

Forces a fresh full pass over every blob with the new skillset +
populated cache.

### 10.8 Heal until done

```bash
python scripts/heal_until_done.py --config deploy.config.json
```

Polls coverage, bumps any stuck blobs, retriggers the indexer, repeats
until every PDF has a `summary` record (or two consecutive identical
stuck-set iterations, which signals a deterministic failure to
investigate).

### 10.9 Validate

```bash
python scripts/smoke_test.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
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

## 16. Production automation — Jenkins pipelines

In production the operator does not run preanalyze or trigger the
indexer manually. Jenkins runs everything on a nightly cron, with
manual "Build Now" available for catch-up. See §6 for one-time
Jenkins agent setup.

### 16.1 Correctness for add / update / delete

| Event | What needs to happen | How the pipeline handles it |
|---|---|---|
| **Add** (new PDF) | preanalyze → index | `reconcile.py` detects the new blob; `preanalyze.py --incremental` builds the cache; indexer's 15-min schedule picks it up. |
| **Update** (overwrite, same name) | invalidate cache → re-preanalyze → re-index | `reconcile.py` compares `metadata_storage_last_modified` to its tracked LMT and treats it as an edit. It purges the stale `_dicache/<pdf>.*` blobs AND the stale chunks from the index. Then `preanalyze.py --incremental` rebuilds the cache (now uncached). Indexer reprocesses. |
| **Delete** | drop records + drop `_dicache/<pdf>.*` | `reconcile.py` detects the absent blob, purges chunks from the index AND `_dicache/` blobs. Blob soft-delete + `NativeBlobSoftDeleteDeletionDetectionPolicy` is also wired so the indexer would handle deletion even without reconcile. |

The update-case correctness comes from `reconcile.py` — not from event
triggers. The earlier Container Apps + Event Grid design was
considered but production simplified to a single nightly Jenkins job
that runs `reconcile → preanalyze → indexer → coverage` in sequence.

### 16.2 The two Jenkins pipelines

```
Jenkinsfile.deploy (push to main + manual approval before prod)
  └── tests → lint → load config → approve prod → bootstrap → smoke test

Jenkinsfile.run (cron 0 2 * * * UTC + manual "Build Now")
  └── checkout → bootstrap (venv, az login) → load config
      └── python scripts/run_pipeline.py
            ├── reconcile.py                   # detect add/edit/delete; purge stale
            ├── preanalyze.py --incremental    # build cache for new/changed PDFs
            ├── wait for indexer to settle     # poll status until idle
            ├── check_index.py --coverage      # report
            └── write run record + per-PDF state to Cosmos DB
```

Both pipelines call the same `scripts/` a developer runs from a
laptop. No extra Azure infra is required beyond the resources in §4.

### 16.3 Why this design (and not event-driven Container Apps Jobs)

| Design                                | Pros                                              | Cons                                              | Verdict |
|---------------------------------------|---------------------------------------------------|---------------------------------------------------|---------|
| **Jenkins nightly + reconcile**       | Reuses CI infra; one cron; correct for add/edit/delete; identical to laptop runs | Worst-case 24-hour lag for new uploads (operator can "Build Now" to bypass) | ✅ **Chosen for production** |
| Event Grid + Storage Queue + Container Apps Job | Sub-15-min lag; bursty workloads scale on queue | Extra Azure resources (Event Grid Topic, Queue, Container Apps env, ACR); KEDA scaler; DLQ monitoring; more MI grants; harder to debug locally | Considered, not adopted — extra infra didn't justify the latency improvement for this workload |
| Naïve cron without reconcile          | Simplest                                          | **Incorrect on edits** — stale `_dicache/<pdf>.output.json` causes the indexer to read stale content forever | Rejected |

### 16.4 Required prerequisites checklist

Before turning the Jenkins cron on:

- [ ] Blob soft-delete enabled on the storage account (Data protection blade)
- [ ] `dataDeletionDetectionPolicy: NativeBlobSoftDeleteDeletionDetectionPolicy` present in `search/indexer.json`
- [ ] Jenkins agent identity holds the roles listed in §5
- [ ] `deploy.config.json` uploaded to Jenkins as a secret-file credential per env (`deploy-config-dev`, `deploy-config-qa`, `deploy-config-prod`) — see §6
- [ ] `Jenkinsfile.run` cron uncommented (set to whatever cadence matches your manuals' update frequency)
- [ ] Optional: Cosmos DB provisioned + Power BI dashboard reading `indexing_run_history` / `indexing_pdf_state` (see §7)

### 16.5 On-demand catch-up

When a user uploads a PDF and wants it indexed immediately (instead of
waiting for tonight's cron):

- **From Jenkins:** open `Jenkinsfile.run` → "Build Now" → pick the env. Same code path as the nightly run.
- **From a laptop with auth:** `python scripts/deploy.py --config deploy.config.json --skip-bootstrap --auto-fix` runs preanalyze → indexer → heal-until-done.

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

### 17.14 Jenkins nightly run missed an upload window

**Symptom:** PDF uploaded today; user can't find it; nothing in the
indexer.

**Cause:** Upload happened after the nightly Jenkins cron fired.

**Resolution:** in Jenkins, open `Jenkinsfile.run` → "Build Now" with
the right `TARGET_ENV`. Same code path as the nightly. Or from a
laptop:
```bash
python scripts/deploy.py --config deploy.config.json --skip-bootstrap --auto-fix
```

### 17.15 Pipeline stuck on a poison PDF

**Symptom:** `heal_until_done.py` exits 1 with "same N PDF(s) stuck
across 2 consecutive iterations". Same PDFs failing every run.

**Cause:** corrupt PDF, password-protected, DI consistently failing,
or Function App OOM on a very large PDF (exit code 137).

**Resolution:**
1. Identify the failing PDFs from `heal_until_done` output.
2. Run `python scripts/inspect_pdf.py --config deploy.config.json --name <pdf>` to see what's in the cache vs the index.
3. If OOM: bump the Function App App Service Plan SKU (more memory).
4. If genuinely unprocessable: move the PDF to a `_quarantine/`
   prefix in the container and notify the uploader.

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
| Jenkins pipeline | Jenkins console output | Pipeline exit code, stage failures, stdout |
| Cosmos DB | `indexing_run_history` / `indexing_pdf_state` | Coverage, run history, per-PDF status |
| AOAI | Azure portal → AOAI resource → Metrics | Token usage, 429 rate, latency |
| Cost | Cost Management | Daily spend per service |

### 18.2 Alerts to wire

- Indexer execution status `transientFailure` or `error` for
  3 consecutive runs.
- Function App 5xx rate > 1% for 10 min.
- Jenkins pipeline failure (any non-success on `Jenkinsfile.run`).
- Coverage drop in Cosmos `indexing_run_history` (e.g. `fully_chunked / blob_pdfs_total < 0.9`).
- AOAI 429 rate > 5% for 15 min.
- Storage `BlobCount` for `_dicache/` growing without bound (orphan
  cleanup not running — `reconcile.py` should be removing these).

### 18.3 Dashboards worth building

Two dashboards, kept simple:

**Pipeline health** — Power BI on top of `indexing_run_history`:
last nightly-pipeline exit code, coverage %, items processed,
recent App Insights exceptions for the Function App.

**Cost** — Azure Cost Management: daily spend split by AOAI / DI /
Search / Storage / Function App, with month-to-date forecast.

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
| Cosmos DB (optional) | Run history writes | negligible (serverless) |

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
4. **Tune the indexer schedule.** `PT15M` is the default; bump to
   `PT1H` if uploads are infrequent — eliminates ~95% of empty runs.

---

## 20. Disaster recovery & rollback

### 20.1 What's stateful and how to recover

| State | Where | Recovery |
|---|---|---|
| Source PDFs | Storage account | Re-sync from SharePoint (already automated upstream) |
| Cache (`_dicache/`) | Storage account | **Regenerable** via preanalyze; not strictly DR-critical |
| Search index | Search service | Rebuild via reset + indexer run; ~30 min/PDF, no data loss because PDFs are the source of truth |
| Function App code | The repo | `scripts/deploy_function.sh` redeploys |
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

- [../README.md](../README.md) — top-level project readme + architecture
- [§3 above](#3-validation) — manual validation checklist
- [§2 above](#2-preanalyze-runbook) — team-facing preanalyze runbook
- [../CHATBOT_INTEGRATION.md](../CHATBOT_INTEGRATION.md) — chatbot dev hand-off spec
- [search/index.json](../search/index.json) /
  [skillset.json](../search/skillset.json) /
  [indexer.json](../search/indexer.json) /
  [datasource.json](../search/datasource.json) — actual search
  artifact bodies

---

# 2. Preanalyze runbook


Processes every PDF in the configured blob container through Document
Intelligence and GPT-4 Vision, caches all results in blob storage, and
assembles a per-PDF `output.json` the indexer consumes. Safe to re-run.

## Setup (once per machine)

```powershell
# From the repo root, with the Python venv active (the one that has httpx etc. installed)
az login                                # must be signed in; the scripts use az CLI for keys
```

Your `deploy.config.json` must be present in the repo root with the usual
keys (`storage`, `azureOpenAI`, `documentIntelligence`, `functionApp`).

## Daily use

### Run everything (one command)

```powershell
./scripts/run_preanalyze.ps1
```

Defaults: 40 parallel vision calls per PDF, 2 PDFs at a time, 3 sweep
passes (for error retry). Tune with flags:

```powershell
./scripts/run_preanalyze.ps1 -VisionParallel 48 -Concurrency 3
```

### Check status (no work done)

```powershell
python scripts/preanalyze.py --config deploy.config.json --status
```

Prints a table:

```
PDF                   DI   Output   Vision (ok/err)
ED-ED-OTC.pdf         OK   OK       644/12
ED-ED-UGC.pdf         OK   OK       1454/20
ED-EM-SSM.pdf         OK   OK       478/23
new-manual.pdf        --   --       --
partial-manual.pdf    OK   --       320/5

Summary: 3/5 PDFs fully done, 2 remaining, 60 errored figures across all PDFs
```

- `OK` under Output = fully done, will be skipped on the next run.
- `--` = not cached yet.
- Vision `ok/err` = successful calls / cached errors (permanent or out-of-retries).

### Remove cache for deleted PDFs

```powershell
python scripts/preanalyze.py --config deploy.config.json --cleanup
```

### Force re-analyze everything (rare)

```powershell
python scripts/preanalyze.py --config deploy.config.json --force --vision-parallel 40
```

## How it works (cache layout)

For each PDF `foo.pdf`, three types of blobs live under `_dicache/`:

| Blob | Purpose | When written |
|---|---|---|
| `_dicache/foo.pdf.di.json` | Document Intelligence output | After DI analyze succeeds |
| `_dicache/foo.pdf.crop.<fig>.json` | Cropped figure image (base64) | After cropping each figure |
| `_dicache/foo.pdf.vision.<fig>.json` | Vision API result per figure | After each vision call |
| `_dicache/foo.pdf.output.json` | Final assembled output for indexer | After all phases succeed |

`output.json` is the **done marker**. If it's present, the PDF is fully
processed. `--incremental` (used by the wrapper) filters on this.

## Resumability

If you `Ctrl+C` or the script dies mid-run:

- Completed DI analyses are safe (cached before any crop work starts).
- Completed figure crops are safe.
- Completed per-figure vision calls are safe.
- Just re-run `run_preanalyze.ps1`. It skips every figure that already
  has a cached result. No duplicate vision-API calls, no wasted tokens.

## Error handling

- **Vision JSON parse errors** — usually caused by model output being cut
  off. `max_tokens` is set to 1500 which covers almost all diagrams. The
  remaining ones retry up to 3 times across runs, then stop.
- **Content-filter blocks** (`ResponsibleAIPolicyViolation`) — marked
  permanent immediately. Never retried. Figure is recorded with no
  vision description but isn't considered a failure.
- **Transient blob/network errors** — every blob HEAD/GET/PUT retries 3
  times with backoff before failing.
- **PDF-level failure** — printed at the end under "Failed PDFs". Just
  re-run to retry that PDF.

## Performance

Rough throughput with defaults (`-VisionParallel 40 -Concurrency 2`):

- **First run of a new PDF**: dominated by vision calls. Roughly one
  minute per 300 figures on average. A 1500-figure PDF takes ~5 minutes.
- **Re-run of an already-done PDF**: instant (seconds). The vision phase
  short-circuits when `output.json` exists.
- **10 PDFs, ~500 figures each, fresh**: expect ~30-60 minutes with
  defaults; faster if AOAI quota allows higher `-VisionParallel`.

Bottlenecks, in order:

1. AOAI throughput (TPM quota on the vision deployment).
2. Document Intelligence submission time for very large PDFs.
3. Blob storage round trips (minor).

If vision calls throttle (429s), reduce `-VisionParallel`. If they're
bored, raise it.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Nothing to process." but PDFs exist in blob | PDFs don't end in `.pdf`/`.PDF` | Rename the blobs (filter matches any case) |
| Same PDF keeps failing | See the FAIL line; usually DI timeout on huge PDFs | Re-run; DI has server-side retry + long polling |
| `vision error (... permanent)` lines | Content filter blocks | Expected; ignore |
| `vision error (... attempt N/3)` lines | Transient; will stop after 3 sweeps | Normal |
| `Found N PDFs` where N is smaller than expected | Some blobs are in a subfolder, or have an unexpected extension | Check with `az storage blob list` |

## Files to copy when handing off

1. `scripts/preanalyze.py` — the main script
2. `scripts/run_preanalyze.ps1` — the one-command wrapper
3. `scripts/PREANALYZE_README.md` — this document
4. `function_app/shared/` — the script imports helpers from here
5. `deploy.config.json` — your environment's config (do NOT commit secrets)

---

# 3. Validation


Two layers: **local** (no Azure required) and **cloud** (against a
deployed environment).

## Local

```bash
python tests/test_unit.py             # deterministic unit checks
python tests/test_e2e_simulator.py    # full handler simulation
ruff check function_app tests scripts
```

- `test_unit.py`: page-span parsing, section index walking, table
  extraction with multi-page merge, semantic-string assembly,
  chunk-id uniqueness, OData escaping, config error handling.
- `test_e2e_simulator.py`: drives every handler through the exact JSON
  envelope Azure AI Search sends and validates each record type
  against the index schema.

## Cloud — automated

```bash
python scripts/smoke_test.py --config deploy.config.json
```

Runs the indexer, waits for completion, then asserts:

1. Indexer `status == success`, `itemsProcessed > 0`.
2. Every `record_type` (text, diagram, table, summary) has ≥ 1 record.
3. Required fields are populated on a sample of each record type.
4. `physical_pdf_pages` on text/table records covers both the declared
   start and end.

Non-zero exit on any failure.

## Cloud — manual spot-checks

Worth eyeballing the first time you bring up an environment or after
changing the skillset.

### 1. Multi-figure page → multiple diagram records
Pick a PDF page with 2+ figures:

```
$filter=record_type eq 'diagram' and physical_pdf_page eq <page>
```

Should return one record per figure, not one collapsed record.

### 2. Diagram → section linking
For 5 random diagram records, confirm `header_1/2/3` match the
chapter/section the figure visually belongs to.

### 3. `surrounding_context` populated
For 5 random diagram records, confirm it contains real body prose —
not just headers, not empty.

### 4. Table records are structured
For a known spec table:

```
$filter=record_type eq 'table' and contains(table_caption, '<caption>')
```

`chunk` should be a real markdown grid (`|` separators, `---` row), not
a vision description.

### 5. Multi-page table merge
For a multi-page table, one record should cover both pages:

```
physical_pdf_page lt physical_pdf_page_end
```

`chunk` contains data rows from all covered pages, with the
continuation-page header deduplicated.

### 6. Multi-page text chunks
For text records crossing a page boundary:

```
$filter=record_type eq 'text' and physical_pdf_page lt physical_pdf_page_end
```

- `physical_pdf_pages` is the full sorted list of every page covered
  (citation UIs use this to highlight every grounded page).
- `printed_page_label_end` matches the printed label on the final
  physical page the chunk covers.

### 7. Hash-cache hits on re-index
Reset + re-run the indexer:

```
$filter=record_type eq 'diagram' and processing_status eq 'cache_hit'
```

Second run should produce cache_hit records — no new vision calls.

### 8. Vectorizer query (no client embedding)

```
POST /indexes/<INDEX_NAME>/docs/search?api-version=2024-05-01-preview
{
  "vectorQueries": [{
    "kind": "text",
    "text": "wiring diagram for control relay",
    "fields": "text_vector",
    "k": 5
  }]
}
```

Returns results without the caller embedding the query.

### 9. `chunk_id` uniqueness
No collisions. Prefixes: `txt_`, `dgm_`, `tbl_`, `sum_`.

---

# 4. Content capture


Plain-language reference for content owners and operators: what the
pipeline extracts, what it doesn't, and what it would take to add the
missing pieces. Read this before asking why a query "didn't find"
something.

---

## Captured today (out of the box)

| Content | Captured | How |
|---|---|---|
| Body text | ✅ | DI markdown → SplitSkill → text records |
| Section headers (h1/h2/h3) | ✅ | DI section walk → stamped on every chunk |
| Tables (with cells, captions, multi-page merge) | ✅ | DI tables → markdown → table records |
| Figures + diagram descriptions | ✅ (PDF only) | DI figures → PyMuPDF crop → GPT-4 Vision → diagram records |
| Figure references in body text (`Figure 4-2`) | ✅ | Regex on body → `figure_ref` field |
| Table references in body text (`Table 18-3`) | ✅ | Regex on body → `table_ref` field |
| Equation references (`Equation 4-2`, `Eq. 18.3`) | ✅ | Head-loaded into `chunk_for_semantic` |
| Section references (`Section 4.2`, `§ 4.2.1`) | ✅ | Head-loaded into `chunk_for_semantic` |
| Safety callouts (WARNING / DANGER / CAUTION / NOTE) | ✅ | Head-loaded into `chunk_for_semantic` |
| Page labels (printed, e.g. "4-12") + physical PDF pages | ✅ | DI markdown markers + section walk |
| Document summary | ✅ | GPT-4.1 over the full markdown |
| Per-document taxonomy (operationalarea / functionalarea / doctype) | ✅ if blob metadata is set | See below |
| File type (`.pdf`/`.pptx`/`.docx`/`.xlsx`) | ✅ | `filetype` field, auto-populated |

## Captured for non-PDF formats

The indexer accepts `.pdf`, `.docx`, `.pptx`, and `.xlsx`. For the three
non-PDF formats, **preanalyze auto-converts them to PDF using LibreOffice
headless** (if installed on the agent), then runs the full pipeline.
This means slide diagrams in PPTX, embedded images in DOCX, and embedded
charts in XLSX all get cropped + Vision-analyzed just like PDF figures.

| Aspect | With LibreOffice on agent | Without LibreOffice |
|---|---|---|
| Text content + section structure | ✅ Same as PDF | ✅ Same |
| Tables | ✅ Same as PDF | ✅ Same |
| **Figure / diagram extraction** | ✅ via LibreOffice → PDF → PyMuPDF crop → Vision | ❌ Not captured; records carry empty `enriched_figures` |
| Document summary | ✅ Same as PDF | ✅ Same |
| Page numbers | ✅ Slides → PDF pages 1:1 for PPTX; sheets paginate for XLSX | ✅ from DI markers |

To enable conversion (recommended): install LibreOffice on the Jenkins
agent. `preflight.py` reports whether it's present.

```bash
# Linux (Ubuntu/Debian)
sudo apt-get install -y libreoffice

# Linux (RHEL/Fedora)
sudo dnf install -y libreoffice

# macOS
brew install --cask libreoffice

# Windows
# download from libreoffice.org; ensure soffice.exe is on PATH
```

If LibreOffice is missing, preanalyze prints a one-line warning and
falls back to text+tables-only for non-PDF files. PDFs are unaffected.

**Conversion fidelity:** good but not pixel-perfect.
- PowerPoint animations are flattened (irrelevant for retrieval).
- Embedded fonts may be substituted.
- Slide-to-page mapping is 1:1 for PPTX.
- XLSX may produce multi-page output for wide sheets — page numbers
  reflect the PDF, not Excel sheet/cell coordinates.

**Conversion cost:** 5-30 seconds per file, run once and cached.
Negligible vs DI which is minutes per file.

---

## How to populate the taxonomy fields

The three classification fields are read from blob user-metadata. They
can be set with the Azure CLI when you upload (or on existing blobs):

```bash
# Set on an existing blob
az storage blob metadata update \
    --account-name <your-storage> \
    --container-name <your-container> \
    --name "GAS_Procedure.pdf" \
    --metadata operationalarea="Gas Distribution" \
                functionalarea="Operations" \
                doctype="Procedure"

# Or set during upload
az storage blob upload \
    --account-name <your-storage> \
    --container-name <your-container> \
    --name "GAS_Procedure.pdf" \
    --file ./GAS_Procedure.pdf \
    --metadata operationalarea="Gas Distribution" \
                functionalarea="Operations" \
                doctype="Procedure"
```

The indexer auto-extracts blob user metadata as `metadata_<key>` fields.
Field mappings in [indexer.json](../search/indexer.json) route these to
the document-level fields, and the skillset projects them onto every
record (text, table, diagram, summary) so a query can filter by any of
them:

```
$filter=operationalarea eq 'Gas Distribution' and doctype eq 'Procedure'
```

If the metadata isn't set on a blob, the fields are simply empty — no
error, no indexer failure. Existing records can be retro-tagged by
setting metadata then re-running the pipeline (`reconcile` will detect
the metadata change as an edit and re-index).

> Tip: keep a CSV of filename → taxonomy and a small script that sets
> metadata in bulk. A structured filename prefix (e.g. `GD-AS-ATM`)
> can be parsed mechanically; the script writes `operationalarea=Gas
> Distribution` for everything starting with `GD-`, etc.

---

## Not captured — and what it would take

### PDF annotations (highlights, sticky notes, comments)

**What they are:** When a reviewer opens a PDF in Acrobat and adds a
yellow highlight, a sticky-note comment, or a strikethrough, those
become *annotation objects* attached to the PDF — they're stored
separately from the page content stream. Document Intelligence does
**not** read them. PyMuPDF does (via `page.annots()`), but our pipeline
ignores them.

**Why ignore by default:** Annotations are typically internal review
markup ("LGTM", "fix this paragraph"). They're not authoritative
manual content and putting them in search results would confuse end
users.

**To capture them:** Add an annotation-extraction step in
`preanalyze.py` that uses `page.annots()` to read each annotation's
content + location and writes them as a separate record type
(`record_type: "annotation"`). About 50 lines + a new index field.
**Don't do this unless you have a specific reason** — most teams find
annotations are noise.

### Hyperlinks (clickable links inside the PDF)

**What they are:** A link from "see Section 4.2 on page 78" to the
actual page 78 of the same PDF, or a `mailto:` / `https://` link to an
external resource. PDFs store hyperlinks as link annotations
(`/Link` annotations) with a target URI or page reference.

**What we capture today:** Nothing as a dedicated field. The link's
*display text* is captured because it's part of the body text, but the
target URL is not.

**To capture them:** Two options:

1. **Cross-reference targets** (link points to another page in the
   same PDF) — usually redundant; we already extract `figure_ref` and
   `Section X.Y` references mechanically, so a search for "Section
   4.2" finds the right chunk regardless of clickability.
2. **External URLs** (link to another document) — could be useful for
   navigation. Add a `links_referenced` `Collection(Edm.String)` field
   to the schema, populate via PyMuPDF `page.get_links()` in
   preanalyze. About 30 lines.

**Recommendation:** Skip unless you have an explicit need to navigate
between manuals via a UI feature. The vector search + `figure_ref` /
`table_ref` / `Section X.Y` cross-refs we already extract cover the
common in-document navigation case.

### Equations as math objects

**What they are:** Equations like:

> *V* = *I* · *R* + Σ *Vᵢ*

PDFs render these in three different ways:

1. **Inline text with special characters** — DI captures it as text but
   subscripts/superscripts may be lost. Result: searchable as "V = I R"
   but the relationships and units may be muddled.
2. **A bitmap image** of a LaTeX-rendered formula — DI sees this as a
   figure. Vision describes it ("equation showing voltage equals
   current times resistance") but the formal expression is not
   recovered.
3. **MathML embedded** in the PDF — extremely rare in technical manuals;
   DI doesn't parse it.

**What we capture today:** Equations of type 1 land in body text, of
type 2 in `diagram_description` (vision narrates them). Neither is a
structured equation object.

**To capture them as math:** State of the art uses dedicated equation-OCR
tools (commercial APIs or open-source LaTeX-recognition libraries) which
recognise equation regions and emit LaTeX. Both classes are external
dependencies (paid API or 1+GB model). Integrating either would mean
adding a `latex` or `equation_text` field and a new
equation-recognition skill in preanalyze.

**Recommendation:** Defer. The equations users actually search for
("Equation 4-2") are referenced by number in body text — and our
`Equation X-Y` extraction catches those. The equation *content* itself
is rarely the search target.

### Other things technical manuals might have

| Content | Captured? | Notes |
|---|---|---|
| Figure / table cross-references | ✅ | Already extracted |
| Step-numbered procedures ("Step 3:") | ⚠️ in body text | Sequence preserved; no per-step record |
| Acronyms + glossary entries | ⚠️ in body text | No dedicated extraction |
| Part numbers / model numbers | ⚠️ in body text + vision OCR | Vision pulls them from nameplates; body text retains them inline |
| Cross-document references ("see GD-2024-01") | ⚠️ in body text | No automatic linking |
| Footnotes | ⚠️ may merge with body | DI's `role: "footnote"` not specially handled |
| Index / TOC entries | ⚠️ marked `processing_status="toc_like"` | Filterable so you can exclude from results |
| Multiple languages in same manual | ⚠️ no language detection field | All chunks indexed identically |
| Tables of contents | ⚠️ filtered as `toc_like` | UIs should `$filter=processing_status eq 'ok'` |

---

## When to ask "should we capture X?"

Three questions:

1. **Will users search for it?** If a user is going to type the part
   number into the assistant, we need to make it findable. If it's
   internal review markup nobody asks about, skip.
2. **Is it already covered by existing fields?** Vector search on
   `chunk_for_semantic` plus the head-loaded `References:` and
   `Callouts:` lines covers most retrieval needs without dedicated
   fields.
3. **Does adding a field require a reset?** Yes — schema changes mean
   re-deploying the index, which means re-paying embeddings for every
   chunk. For an incremental cost, maybe — for a "would be nice" cost,
   no.

The fields in this document are the ones we judged worth the cost.
Re-evaluate periodically as the AI assistant evolves.

---

# 5. Incident response


Top failure modes for the indexing pipeline and the recovery steps for
each. Read this before opening a war room.

For deeper architecture context, see [../README.md](../README.md).
For steady-state procedures, see [§1 above](#1-daily-operations).

---

## 1. Indexer is timing out / 0 docs succeeded

**Symptom:** Azure portal shows indexer runs with status `Timed out`,
`itemsProcessed: 0` or `1`, durations of 1–4 hours. Index document
count stops growing.

**Cause:** Pre-analyze cache is missing for one or more PDFs. The
`process-document` skill falls back to live Document Intelligence,
which exceeds the 230-second WebApi-skill timeout for any non-trivial
PDF.

**Diagnose:**

```bash
python scripts/preanalyze.py --config deploy.config.json --status
```

Any PDF showing `todo`, `di-only`, or `PARTIAL` is a candidate.

**Fix:**

```bash
python scripts/preanalyze.py --config deploy.config.json --incremental
```

This populates the missing cache. The indexer will succeed on the next
15-min tick automatically — **do not press Reset**.

---

## 2. PDF was edited, but search returns old content

**Symptom:** A user re-uploaded a manual; search results still show
text from the previous version.

**Cause:** The indexer reprocessed the PDF and produced new chunks
(with new chunk_id hashes), but the old chunks weren't deleted — they
linger in the index forever.

**Fix:**

```bash
python scripts/run_pipeline.py --config deploy.config.json
```

`reconcile.py` (the first step) detects the edit by comparing blob
`last_modified` against Cosmos `pdf_state.last_indexed_at`, purges
stale chunks + cache, then preanalyze regenerates everything from the
new content.

If `--max-purges` aborts the run, raise the cap:

```bash
python scripts/run_pipeline.py --config deploy.config.json --max-purges 10
```

---

## 3. PDF was deleted, but still appears in search

**Symptom:** Manual was deleted from the blob container; search still
returns chunks attributed to it.

**Cause:** Same class as #2, but for the delete case. Azure's native
soft-delete-detection only fires when the blob path matches an index
document key, which is content-derived in our schema and won't match
in many cases.

**Fix:** Same — run `run_pipeline.py`. Reconcile detects deletions and
purges. If the storage account has soft-delete disabled, enable it
first (Storage → Data protection → Blob soft delete → 7+ days). Soft
delete also protects against accidental deletion in the future.

---

## 4. Pre-analyze fails with `blob HEAD unexpected 403`

**Symptom:** preanalyze.py logs `RuntimeError: blob HEAD unexpected 403`
for some PDFs but not others.

**Cause (historical):** Filename URL-encoding bug in the SharedKey
signing path. Fixed in the codebase; this should no longer occur.

**If you still see it:**

1. Check that the failing filenames don't contain unusual characters
   not in the test coverage. Run
   `python tests/test_filename_spaces.py` to confirm the fix is
   present.
2. Verify the agent's storage role assignment
   (`Storage Blob Data Contributor`).
3. Check the storage account firewall — if it's IP-restricted, the
   Jenkins agent's IP must be allow-listed.

---

## 5. Vision call returning content-filter errors

**Symptom:** preanalyze.py logs `vision API content_filter` repeatedly
on a particular PDF.

**Cause:** Azure OpenAI's responsible-AI policy flagged a figure or
its surrounding context as harmful.

**Fix:** The vision retry logic caches permanent (content_filter)
errors and stops retrying. The figure is dropped from the index with
`processing_status="vision_error:..."`. To recover:

1. Inspect the offending figure manually — confirm it's a false
   positive.
2. If valid: open a support ticket with Azure to request a content
   filter review for your subscription, or reduce the surrounding
   context window in the prompt.
3. If invalid (the figure really shouldn't be in the index): leave
   it — the rest of the manual indexes fine.

---

## 6. AOAI / Search returning 429 throttling

**Symptom:** `vision API rate limited`, `search_cache POST 429`, or
`hash cache lookup error: 429`.

**Cause:** Tenant-level quota exceeded; usually from running multiple
preanalyze jobs in parallel or a giant batch.

**Fix:** The retry logic in `preanalyze.py`, `di_client.py`, and
`search_cache.py` honors `Retry-After` (capped at 30s for the
function app, 120s for vision). If 429s persist:

1. Lower `--vision-parallel` from 20 (default) to 5.
2. Raise the AOAI tokens-per-minute quota with Microsoft.
3. Check that no other team is hammering the same AOAI deployment.

---

## 7. Cosmos DB writes failing

**Symptom:** Pipeline runs succeed, but the dashboard shows stale
data. Logs include
`cosmos run_history upsert failed: ...` or
`cosmos pdf_state bulk: container open failed: ...`.

**Cause:** Either the agent identity is missing the
`Cosmos DB Built-in Data Contributor` role, or the configured
endpoint/database are wrong, or the Cosmos account is in a different
network than the agent.

**Fix:**

```bash
# Verify the role is assigned
az role assignment list --assignee <agent-identity-id> \
    --scope <cosmos-account-resource-id>

# Verify the endpoint resolves from the agent
curl -I https://<your-cosmos>.documents.azure.us:443/
```

Cosmos write failures **do not** fail the pipeline run — the underlying
indexing succeeded; only the dashboard is stale. The next run will
overwrite the missing rows.

---

## 8. Search index out of sync with reality

**Nuclear option, last resort.** Use only when reconcile + run_pipeline
have not recovered the state.

**Symptom:** `--coverage` reports counts that disagree with what's in
blob storage, even after several pipeline runs. Orphaned PDFs persist.

**Fix:**

```bash
# 1. Drop everything
az search index delete --service-name <search> --name <prefix>-index --yes

# 2. Re-deploy the index schema
python scripts/deploy_search.py --config deploy.config.json

# 3. Reset the indexer so it reprocesses every blob
./scripts/reset_indexer.sh --config deploy.config.json

# 4. Watch progress
python scripts/check_index.py --config deploy.config.json --coverage
```

**Cost impact:** This re-pays embeddings for every chunk
(~$5 for ~100K chunks). DI + Vision are NOT re-paid because the
preanalyze cache survives. Only do this if absolutely necessary.

---

## 9. Function App stops responding

**Symptom:** All skill calls in the indexer fail with
`Could not execute skill ... TimeoutException`. App Insights shows no
function invocations.

**Diagnose:**

```bash
python scripts/diagnose.py --config deploy.config.json
```

**Common fixes:**

- Function App is stopped — `az functionapp start -g <rg> -n <func>`
- App Settings missing — re-run `scripts/deploy_function.sh`
- App Service Plan is out of memory / CPU — scale up the plan
- Recent code change broke startup — `az functionapp log tail` to see
  the import error, then revert + redeploy

---

## 10. PDF is corrupted / unreadable

**Symptom:** preanalyze logs `FAIL-di <name>: PDF is corrupted /
unreadable. Inspect the source file.`

**Cause:** PyMuPDF could not open the file. Either the upload was
truncated, or the source file itself is malformed.

**Fix:**

1. Download the blob: `az storage blob download --container-name <c> --name <name> --file /tmp/check.pdf`
2. Try opening it in Acrobat / a browser. If it doesn't open there
   either, the source is bad → ask the content owner for a fresh copy.
3. If it opens fine elsewhere but PyMuPDF rejects it: try re-saving
   from Acrobat (File → Save As) which sometimes rewrites the PDF
   structure.
4. Re-upload, then re-run the pipeline. `reconcile` will detect the
   edit and re-process.

The pipeline does NOT silently produce an empty record for a corrupt
PDF — it fails loud so this gets noticed.

---

## 11. PDF is password-protected

**Symptom:** preanalyze logs `FAIL-di <name>: PDF is password-protected.`

**Cause:** The PDF has a user password (preventing open) or owner
password (preventing extraction). DI may also reject it.

**Fix:** We deliberately do not store passwords in the pipeline.
Remove protection upstream:

```bash
# Using qpdf (fast, lossless)
qpdf --decrypt --password=<password> input.pdf output.pdf

# Or open in Acrobat → Tools → Protect → Encrypt → Remove Security
```

Re-upload the unprotected version. Re-run the pipeline.

---

## 12. PPTX/DOCX/XLSX file processed but no figures show up in the index

**Symptom:** A PowerPoint file shows in `--coverage` as `done` but the
diagram count for it is zero. Users can't find the slide images.

**Cause:** **By design.** PyMuPDF only renders PDFs. For PPTX/DOCX/XLSX
we extract text + tables but skip figure cropping. The `diagram_count`
will be 0 for any non-PDF.

**Fix (if you need diagram extraction for a PowerPoint):** Convert the
PPTX to PDF before upload. Each slide becomes a PDF page; figures on
slides become extractable figures. PowerPoint's File → Save As → PDF
handles this in one step.

---

## 13. Cosmos DB writes time out during heavy preanalyze runs

**Symptom:** Pipeline run completes but the dashboard is missing rows;
logs show `cosmos pdf_state upsert for ... failed: ServiceRequestTimeoutError`.

**Cause:** Cosmos throughput too low for the burst of writes during
auto-heal or a full re-process.

**Fix:** Increase shared throughput on the database:

```bash
az cosmosdb sql database throughput update \
    --account-name <cosmos> \
    --resource-group <rg> \
    --name indexing \
    --throughput 800
```

Cosmos writes are best-effort — pipeline doesn't fail when they do.
Next pipeline run will refresh missing rows.

---

## 14. Indexer says "0 documents succeeded" but coverage shows new chunks

**Symptom:** Azure portal indexer page reports zero items processed in
the last run, but `check_index.py --coverage` shows the chunk count is
higher than yesterday.

**Cause:** This is normal. The "Docs succeeded" column reflects the
*current run only*. If the last run was a no-op (nothing changed,
schedule fired anyway), it'll show 0 even though the index already has
historical content.

**Fix:** None. Trust `--coverage`, ignore the indexer page's per-run
counter for cumulative state.

---

## 15. Auto-heal looping on the same PDF

**Symptom:** `--auto-heal` runs but the same PDF keeps showing up as
"partial" pass after pass. Eventually the pipeline times out.

**Cause:** The PDF has a structural problem the pipeline can't recover
from automatically. Common cases:

- DI returns 0 figures despite the PDF clearly having figures
  (DI scanning issue)
- The vision model rejects the figure due to content filter
- The crop step picks an empty region

**Fix:** Auto-heal is bounded by `--heal-passes` (default 2). After
that, the PDF stays in `partial` state. Investigate manually:

```bash
python scripts/preanalyze.py --config deploy.config.json --status
# inspect the specific PDF's _dicache/<name>.* blobs
# check di_client.py debug logs for that PDF
```

---

## 16. Indexer hits 24-hour run limit

**Symptom:** Indexer status shows `transientFailure` after exactly 24h,
some items processed, some skipped.

**Cause:** Azure Search caps a single indexer run at 24 hours on
standard tiers. With our 15-min schedule the indexer simply restarts
on the next interval and continues from the high-water mark.

**Fix:** None — this is by design. Watch coverage growth over a few
runs to confirm it's making progress. If it stalls (no chunk-count
growth across multiple runs), see #1 and #2 above.

---

## When to escalate

Open a support case with Microsoft when:

- AOAI returns 5xx errors consistently for >30 minutes (likely a
  regional outage)
- Document Intelligence returns 5xx errors consistently
- Azure Search service status page shows incidents in your region
- A Cosmos DB account becomes unavailable

Otherwise, the failure is in our code or config and the steps above
should resolve it.

---

## Sanity check

After any incident, run:

```bash
python scripts/check_index.py --config deploy.config.json --coverage
```

If the numbers match the blob container, you're back in a known good
state.

---


# 6. Jenkins pipeline setup


Two pipelines live in this repo:

| File | Trigger | What it does |
|------|---------|--------------|
| [`Jenkinsfile.deploy`](../Jenkinsfile.deploy) | Push to `main` (with manual approval before prod) | Deploy function app code → deploy search artifacts → smoke test |
| [`Jenkinsfile.run`](../Jenkinsfile.run) | Cron `0 2 * * *` UTC + manual button | Reconcile → preanalyze → wait for indexer → coverage → Cosmos status |

## One-time agent setup

You need a Linux Jenkins agent (Ubuntu 22.04 LTS recommended) with:

```bash
# Python 3.11+
sudo apt-get install -y python3.11 python3.11-venv python3-pip

# Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Tell az to use Government cloud
az cloud set --name AzureUSGovernment

# LibreOffice (OPTIONAL but recommended) — enables figure extraction
# from DOCX/PPTX/XLSX via auto-conversion to PDF. Without it those
# formats index for text + tables only.
sudo apt-get install -y libreoffice
```

`Jenkinsfile.run` attempts to auto-install LibreOffice on first run if
it's missing. The pipeline still works without it; figures from non-PDF
formats just won't be extracted.

Identity options (in order of preference):

1. **System-assigned MI on the agent VM** (production default).
   - Enable on the VM in the Azure portal.
   - Assign the roles in [§5 above](#5-incident-response) (RBAC matrix
     in main runbook) to the VM's MI.
   - In Jenkins jobs, `az login --identity` with no further config.
2. **Service principal** (for cross-cloud agents or Jenkins running
   outside Azure).
   - Create the SP and assign the same roles.
   - Bind credentials as a Jenkins **Username + Password** credential
     where `username = AZURE_CLIENT_ID` and `password = client_secret`.
   - Add `AZURE_TENANT_ID` as a string credential.
   - In the pipeline, `withCredentials([...]) { az login --service-principal ... }`.

## Required Jenkins credentials

Both pipelines load `deploy.config.json` from a Jenkins **secret file**
credential so the per-environment identifiers never appear in code.

| Credential ID | Type | Contents |
|---------------|------|----------|
| `deploy-config-dev`  | Secret file | dev-environment `deploy.config.json` |
| `deploy-config-qa`   | Secret file | qa-environment `deploy.config.json` |
| `deploy-config-prod` | Secret file | prod-environment `deploy.config.json` |

Create them:

> Manage Jenkins → Credentials → System → Global → Add Credentials → Kind: Secret file → ID: `deploy-config-prod` → upload the file.

If using service-principal auth, also create:

| Credential ID | Type | Contents |
|---------------|------|----------|
| `azure-sp` | Username with password | `AZURE_CLIENT_ID` / `client_secret` |
| `azure-tenant` | Secret text | `AZURE_TENANT_ID` |

And add an early stage to both Jenkinsfiles:

```groovy
withCredentials([
    usernamePassword(credentialsId: 'azure-sp',
                     usernameVariable: 'AZ_CLIENT', passwordVariable: 'AZ_SECRET'),
    string(credentialsId: 'azure-tenant', variable: 'AZ_TENANT')
]) {
    sh '''
        az login --service-principal -u "$AZ_CLIENT" -p "$AZ_SECRET" --tenant "$AZ_TENANT"
    '''
}
```

## Configuring the deploy pipeline

`Jenkinsfile.deploy` is a multi-branch / parameterised job:

> New Item → Multibranch Pipeline → Branch source: GitHub → Pipeline:
> by Jenkinsfile → Path: `Jenkinsfile.deploy`.

Manual parameters:

- `TARGET_ENV` — `dev`, `qa`, or `prod`. Picks the secret-file credential.
- `SKIP_SMOKE` — emergency only.

Production stage gates on a manual approval (`input` step). Set the
approver list to the operations team.

## Configuring the run pipeline

`Jenkinsfile.run` is a separate parameterised job (single branch, main):

> New Item → Pipeline → Pipeline script from SCM → Script Path:
> `Jenkinsfile.run`.

Cron is `0 2 * * *` UTC; tweak in the Jenkinsfile if your manuals
update on a different cadence.

Parameters:

- `TARGET_ENV` — same.
- `SKIP_RECONCILE` — useful if reconcile is misbehaving and you just
  want to run preanalyze + coverage.
- `SKIP_WAIT` — short-circuit the indexer wait; useful for quick
  status checks.
- `MAX_PURGES` — caps how many PDFs reconcile is allowed to delete in
  one run. Default 2. Raise intentionally.
- `MAX_WAIT_MINUTES` — how long to poll the indexer before giving up
  and reporting current state. Default 60.

`disableConcurrentBuilds()` is on — only one run at a time per
environment, to prevent two concurrent pipelines fighting over the
same indexer.

## What runs vs what's parallel

```
Jenkinsfile.deploy (push to main)         Jenkinsfile.run (nightly)
─────────────────────────────────         ─────────────────────────
1. Checkout                               1. Checkout
2. Bootstrap (venv, az login)             2. Bootstrap
3. Tests + lint (gate)                    3. Load config
4. Load config                            4. python scripts/run_pipeline.py
5. Approve prod                              ├── reconcile.py
6. Deploy function app                       ├── preanalyze.py --incremental
7. Deploy search artifacts                   ├── wait for indexer
8. Smoke test (gate)                         ├── check_index.py --coverage --write-status
                                             └── write run record to Cosmos
```

## Failure modes + Jenkins behavior

| Failure | Pipeline behavior | Operator action |
|---------|-------------------|-----------------|
| Tests fail | Deploy aborts before any Azure call | Fix the test or revert the commit |
| `deploy_function.sh` fails | Deploy aborts; function app may be partially updated | Re-run; the script is idempotent |
| `deploy_search.py` fails on placeholder | Deploy aborts with the missing key name | Add the missing field to `deploy.config.json` |
| Smoke test fails | Deploy marked failed; function + search artifacts already updated | Investigate via `python scripts/diagnose.py`; can roll back by reverting commit + redeploying |
| Reconcile finds > MAX_PURGES | Run pipeline finishes step with exit 2; pipeline continues but skips purges | Re-run with higher `MAX_PURGES` if intentional |
| Preanalyze partial failure (some PDFs FAIL) | Run pipeline continues; failures listed in stdout + Cosmos | Inspect Cosmos run record; re-run preanalyze for failed PDFs |
| Indexer wait timeout | Run pipeline reports current state and exits non-zero | Indexer is still running on its own schedule; next pipeline run will pick up where this one left off |

## Notifications

Both pipelines emit a final `post` block. Wire to your team's channels:

```groovy
post {
    failure {
        emailext to: 'ops-team@example.com',
                 subject: "FAIL: ${currentBuild.fullDisplayName}",
                 body: "${env.BUILD_URL}"
        // or
        slackSend channel: '#indexing-pipeline',
                  color: 'danger',
                  message: "Indexing pipeline failed: ${env.BUILD_URL}"
    }
}
```

(Not added by default; depends on which Jenkins plugins your team has.)

---


# 7. Power BI dashboard spec


What the Power BI analyst needs to wire indexing-pipeline tiles onto
the existing Cosmos-DB-backed dashboard.

## Data sources

Two new containers in the existing Cosmos DB account
(auto-created on first run; partition key shown):

### `indexing_run_history`

Partition key: `/partitionKey` (set to date `YYYY-MM-DD`).

One document per pipeline / preanalyze / reconcile / coverage run.

```json
{
  "id": "2026-05-04T02:30:00Z-a1b2c3d4",
  "partitionKey": "2026-05-04",
  "started_at": "2026-05-04T02:30:00Z",
  "ended_at":   "2026-05-04T03:14:00Z",
  "duration_seconds": 2640,
  "run_type": "full_pipeline | preanalyze | reconcile | coverage",
  "triggered_by": "jenkins-cron | jenkins-manual | manual",
  "git_sha": "b6f8473",
  "exit_code": 0,
  "steps": {
    "reconcile":   { "exit_code": 0 },
    "preanalyze":  { "exit_code": 0 },
    "wait_indexer":{ "last_status": "success", "items_processed": 56, "errors": 0, "warnings": 2 },
    "check_index": { "exit_code": 0 }
  },

  "blob_pdfs_total":   56,
  "fully_chunked":     54,
  "partial":            1,
  "not_started":        1,
  "orphaned":           0,
  "total_chunks":   92847,

  "pdfs_processed":     2,
  "pdfs_skipped":      54,
  "pdfs_failed":        0,

  "added":   ["new_manual.pdf"],
  "edited":  ["GAS_Procedure.pdf"],
  "deleted": [],
  "chunks_purged":     412,
  "cache_blobs_purged": 18,

  "errors": []
}
```

Not every field is populated for every run type — `run_type` tells you
which subset to expect:

- `full_pipeline` → `steps`, `blob_pdfs_total`, `fully_chunked`, `partial`, `not_started`, `total_chunks`, `exit_code`, `duration_seconds`
- `preanalyze` → `pdfs_processed`, `pdfs_skipped`, `pdfs_failed`, `errors`, `phase`, `incremental`
- `reconcile` → `added`, `edited`, `deleted`, `chunks_purged`, `cache_blobs_purged`, `errors`
- `coverage` → `blob_pdfs_total`, `fully_chunked`, `partial`, `not_started`, `orphaned`, `total_chunks`

### `indexing_pdf_state`

Partition key: `/partitionKey` (set to `source_file`, so each PDF lives
in its own logical partition).

One document per PDF, replaced on each pipeline run.

```json
{
  "id": "GAS_Procedure.pdf",
  "partitionKey": "GAS_Procedure.pdf",
  "source_file": "GAS_Procedure.pdf",
  "status": "done",
  "chunks_in_index": 1842,
  "last_indexed_at": "2026-05-04T03:14:00Z",
  "last_blob_modified": "2026-05-04T01:18:23Z",
  "last_error": null,
  "updated_at": "2026-05-04T03:14:00Z"
}
```

`status` enum: `done | partial | not_started | failed`.

## Recommended tiles

### Tile 1 — Coverage headline (KPI card × 4)

Read most-recent `coverage` or `full_pipeline` doc:

| KPI            | Source                         |
|----------------|--------------------------------|
| Manuals total  | `blob_pdfs_total`              |
| Fully indexed  | `fully_chunked` (% of total)   |
| Partial        | `partial`                      |
| Not started    | `not_started`                  |

Color rule: green if `fully_chunked / blob_pdfs_total >= 0.95`, yellow
between 0.8 and 0.95, red below.

### Tile 2 — Last run summary (table)

Read most-recent `full_pipeline` doc:

| Column          | Source                                  |
|-----------------|-----------------------------------------|
| Run started     | `started_at`                            |
| Triggered by    | `triggered_by`                          |
| Duration        | `duration_seconds`                      |
| Items processed | `steps.wait_indexer.items_processed`    |
| Errors          | `steps.wait_indexer.errors`             |
| Warnings        | `steps.wait_indexer.warnings`           |
| Git sha         | `git_sha`                               |

### Tile 3 — Run history trend (line chart)

X axis: `ended_at` (last 30 days)
Y axis: `fully_chunked`
Filter: `run_type == "full_pipeline" or run_type == "coverage"`

Visualises whether coverage is growing or stalling.

### Tile 4 — Per-PDF status (table)

Source: entire `indexing_pdf_state` container.

| Column           | Source            |
|------------------|-------------------|
| Manual           | `source_file`     |
| Status           | `status`          |
| Chunks in index  | `chunks_in_index` |
| Last indexed     | `last_indexed_at` |
| Last error       | `last_error`      |

Sort: status descending (so partial / failed bubble up), then
`last_indexed_at` ascending.

### Tile 5 — Recent errors (table)

Source: `indexing_run_history` where `len(errors) > 0`, last 7 days.

| Column     | Source           |
|------------|------------------|
| Run type   | `run_type`       |
| When       | `ended_at`       |
| Triggered by | `triggered_by` |
| Error      | First entry of `errors[]` |

### Tile 6 — Reconcile activity (last 7 days)

Source: `indexing_run_history` where `run_type == "reconcile"`.

Bar chart, X = day, three series:
- Added (new PDFs)
- Edited (re-indexed PDFs)
- Deleted (purged PDFs)

Optional: a stat on chunks_purged to show how much "cleanup" the
pipeline does over time.

## SQL examples

The Power BI Cosmos DB connector uses Cosmos SQL. Two starter queries:

**Most recent coverage snapshot:**

```sql
SELECT TOP 1 *
FROM c
WHERE c.run_type IN ("full_pipeline", "coverage")
ORDER BY c.ended_at DESC
```

**Per-PDF state with error sort:**

```sql
SELECT
  c.source_file,
  c.status,
  c.chunks_in_index,
  c.last_indexed_at,
  c.last_error
FROM c
ORDER BY c.status DESC, c.last_indexed_at ASC
```

**Run-failure rate over last 30 days:**

```sql
SELECT
  COUNT(1) AS runs,
  SUM(c.exit_code != 0 ? 1 : 0) AS failures
FROM c
WHERE c.run_type = "full_pipeline"
  AND c.ended_at > "2026-04-04T00:00:00Z"
```

## Identity / connection

Power BI to Cosmos requires the gateway to authenticate. Either:

- Cosmos DB **read-only key** stored in the analyst's Power BI
  workspace credential (simpler, but a key in flight).
- **Microsoft Entra ID** auth via a service principal granted
  `Cosmos DB Built-in Data Reader` on the database.

The latter is preferred — same pattern as the rest of the pipeline.

## Refresh cadence

Set Power BI dataset refresh to 30 minutes (or whatever cadence
matches the pipeline frequency). The pipeline runs nightly at 02:00,
so a single refresh at 03:30 suffices for the morning view; more
frequent refreshes give responsiveness on manual runs.

---


# 8. First-time setup troubleshooting

When `deploy.py` / `deploy_search.py` / `preanalyze.py` / `run_pipeline.py`
fails on a fresh laptop or environment, walk down this section and run
the matching checks. Every command is copy-paste ready for **PowerShell
on Windows**; equivalent bash for Linux/Mac included where it differs.

> **First: always set your SSL cert env vars** if your environment uses
> a corporate proxy / TLS inspection (Forcepoint, Zscaler, etc.):
>
> ```powershell
> $env:SSL_CERT_FILE = "C:\Users\<you>\Downloads\combined-ca.crt"
> $env:REQUESTS_CA_BUNDLE = "C:\Users\<you>\Downloads\combined-ca.crt"
> ```
>
> If you don't have a combined-ca.crt, your IT/security team can give
> you one. Without it, every Python HTTPS call fails with
> `SSL: CERTIFICATE_VERIFY_FAILED`.

## 8.0 Quick decision tree

| Symptom | Jump to |
|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED` | [§8.1](#81-ssl-cert-error) |
| `PUT datasources/... failed: 403` | [§8.2](#82-deploy_searchpy-403) |
| `Forbidden by IP firewall` in 403 body | [§8.3](#83-search-service-firewall) |
| HTML body in 403 mentioning Forcepoint / corp proxy | [§8.4](#84-corporate-proxy-blocking) |
| `not authorized to perform action` in 403 body | [§8.5](#85-rbac--identity-issues) |
| `assign_roles.py` says "already assigned" but deploy still 403 | [§8.5](#85-rbac--identity-issues) |
| Works in one resource group but not another | [§8.6](#86-subscription--tenant-mismatch) |
| `LibreOffice not found` warning | [§8.7](#87-libreoffice-missing) |
| `blob HEAD unexpected 403` on PDFs with spaces | already fixed in code; pull latest |
| Indexer stuck "in progress" for hours | [§8.8](#88-indexer-stuck) |
| `cosmos run_history upsert failed` | [§8.9](#89-cosmos-db-issues) |

## 8.1 SSL cert error

**Symptom:**
```
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self signed certificate in certificate chain
```

**Cause:** corporate TLS inspection rewriting cert chain.

**Fix (PowerShell):**
```powershell
$env:SSL_CERT_FILE = "C:\Users\<you>\Downloads\combined-ca.crt"
$env:REQUESTS_CA_BUNDLE = "C:\Users\<you>\Downloads\combined-ca.crt"
```

**Fix (bash):**
```bash
export SSL_CERT_FILE="$HOME/Downloads/combined-ca.crt"
export REQUESTS_CA_BUNDLE="$HOME/Downloads/combined-ca.crt"
```

These env vars only live in your current shell session. Re-set them
every time you open a new terminal, or add them to your PowerShell
profile / `~/.bashrc`.

## 8.2 deploy_search.py 403

When you see:

```
PUTting artifacts to https://<search>.search.azure.us
PUT datasources/<name>-ds failed: 403
```

The script truncates the response body. Get the **actual 403 message** —
this is the single most useful diagnostic:

```powershell
python -c @"
import httpx, json, base64
from azure.identity import DefaultAzureCredential

cfg = json.loads(open('deploy.config.json').read())
endpoint = cfg['search']['endpoint'].rstrip('/')

cred = DefaultAzureCredential()
token_obj = cred.get_token('https://search.azure.us/.default')

# Decode the JWT to confirm which identity Python is actually using
parts = token_obj.token.split('.')
pad = '=' * ((4 - len(parts[1]) % 4) % 4)
claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
print('Token issued for:')
print('  upn:', claims.get('upn') or claims.get('appid'))
print('  oid:', claims.get('oid'))
print('  tenant:', claims.get('tid'))
print('  audience:', claims.get('aud'))
print()

r = httpx.get(f'{endpoint}/datasources?api-version=2024-11-01-preview',
              headers={'Authorization': 'Bearer ' + token_obj.token}, timeout=30)
print(f'GET status: {r.status_code}')
print(f'BODY (first 1500 chars):')
print(r.text[:1500])
"@
```

Read the body and match against:

| Body says | Means | Fix |
|---|---|---|
| `Forbidden by IP firewall` | Search service blocks your IP | [§8.3](#83-search-service-firewall) |
| HTML page with `Forcepoint` or company proxy branding | Corporate proxy blocking outbound | [§8.4](#84-corporate-proxy-blocking) |
| `not authorized to perform action` / `AuthorizationFailed` | RBAC role missing or wrong identity | [§8.5](#85-rbac--identity-issues) |
| `PrincipalNotFound` | RBAC propagation incomplete | wait 10–30 min, retry |
| `apiKeyOnly` / `requires admin key` | Search service rejects AAD | [§8.5.4](#854-search-service-in-apikeyonly-mode) |

## 8.3 Search service firewall

Verify network rules:

```powershell
$cfg = Get-Content deploy.config.json | ConvertFrom-Json
$searchName = ($cfg.search.endpoint -replace 'https://','').Split('.')[0]
$rg = $cfg.functionApp.resourceGroup

az search service show -n $searchName -g $rg `
  --query "{publicAccess:publicNetworkAccess, ipRules:networkRuleSet.ipRules}" -o json
```

If `publicAccess` is `"disabled"` or `ipRules` is restrictive:

**Add your IP to the allowlist:**
```powershell
$myIp = (Invoke-RestMethod -Uri "https://api.ipify.org")
Write-Host "Your public IP: $myIp"

# If api.ipify.org returns HTML (proxy intercepting), use this instead:
$myIp = (curl.exe -s https://ifconfig.me/ip).Trim()

az search service update -n $searchName -g $rg --ip-rules $myIp
```

**Or enable public access entirely:**
```powershell
az search service update -n $searchName -g $rg --public-network-access enabled
```

## 8.4 Corporate proxy blocking

**Symptom:** the 403 body contains HTML like:
```html
<title>Access to this site is blocked</title>
Copyright (c) 2022 Forcepoint
... credential prompt form ...
```

**Cause:** your corporate web filter (Forcepoint, Zscaler, Bluecoat, etc.)
is intercepting outbound HTTPS to `*.azure.us` and returning its own
block page. The traffic never reaches Azure.

**Verify:** open the search URL in your browser:
```
https://<search>.search.azure.us
```
- Real Azure: TLS cert subject is `*.search.azure.us`, issuer is
  Microsoft-something. Page returns Azure's own 403 (no UI).
- Proxy intercept: TLS cert subject is your company / Forcepoint.
  Page is the proxy's branded "blocked" page.

**Fixes:**
1. **Get Azure Gov endpoints allowlisted** — submit IT ticket asking for:
   ```
   *.search.azure.us
   *.openai.azure.us
   *.cognitiveservices.azure.us
   *.documents.azure.us
   *.blob.core.usgovcloudapi.net
   *.azurewebsites.us
   login.microsoftonline.us
   ```
2. **Use Forcepoint credential override** if your team has elevated
   credentials for proxy bypass.
3. **Run from Jenkins** — the CI agent is inside the corporate network
   with proper allowlisting. Push to main, let `Jenkinsfile.deploy`
   run there instead of from your laptop.

## 8.5 RBAC / identity issues

### 8.5.1 Verify which identity Python is using

The diagnostic in [§8.2](#82-deploy_searchpy-403) prints the JWT claims.
Confirm:
- `upn` matches your login email
- `tid` (tenant) is your expected tenant
- `aud` (audience) is `https://search.azure.us`

If the identity is wrong, force a refresh:

```powershell
az logout
az login
az account set --subscription "<the-sub-where-resources-live>"
```

### 8.5.2 Verify the role IS on THIS search service

```powershell
$cfg = Get-Content deploy.config.json | ConvertFrom-Json
$searchName = ($cfg.search.endpoint -replace 'https://','').Split('.')[0]
$rg = $cfg.functionApp.resourceGroup

$me = az ad signed-in-user show --query id -o tsv
$searchId = az search service show -n $searchName -g $rg --query id -o tsv

az role assignment list --assignee $me --scope $searchId `
  --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

You should see at least:
```
Search Service Contributor
Search Index Data Contributor
```

If empty → re-run `assign_roles.py` after confirming the correct
subscription is selected (see [§8.6](#86-subscription--tenant-mismatch)).

### 8.5.3 Verify Function App MI has its roles

```powershell
$funcMi = az functionapp identity show -n $cfg.functionApp.name -g $rg --query principalId -o tsv
az role assignment list --assignee $funcMi --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

Function App MI should have:
- Storage Blob Data Reader on storage account
- Cognitive Services OpenAI User on AOAI
- Cognitive Services User on Document Intelligence
- Search Index Data Reader on Search service

### 8.5.4 Search service in apiKeyOnly mode

```powershell
az search service show -n $searchName -g $rg `
  --query "{authOptions:authOptions, disableLocalAuth:disableLocalAuth}" -o json
```

If `authOptions` is `apiKeyOnly` or AAD is disabled, AAD tokens get 403.

```powershell
az search service update -n $searchName -g $rg `
  --aad-auth-failure-mode http403 --auth-options aadOrApiKey
```

Wait 5 minutes after this change.

## 8.6 Subscription / tenant mismatch

Most common when "it works in another RG but not this one".

```powershell
# What sub am I using right now?
az account show --query "{name:name, id:id, tenant:tenantId}" -o table

# What sub does the resource live in?
$cfg = Get-Content deploy.config.json | ConvertFrom-Json
$searchName = ($cfg.search.endpoint -replace 'https://','').Split('.')[0]
az resource list --resource-type "Microsoft.Search/searchServices" --name $searchName `
  --query "[].{name:name, id:id}" -o table
```

If the sub IDs don't match → switch and re-grant:

```powershell
az account set --subscription "<sub-id-from-the-resource>"
python scripts/assign_roles.py --config deploy.config.json --wait-for-propagation 300
az logout
az login
az account set --subscription "<sub-id-from-the-resource>"
python scripts/deploy_search.py --config deploy.config.json
```

## 8.7 LibreOffice missing

**Symptom:** preanalyze logs:
```
warn: skipping conversion for X.pptx -- LibreOffice not installed.
Indexing text + tables only.
```

This is **not an error**, just a warning. PDFs are unaffected. PPTX/DOCX/XLSX
get text + tables but no figure extraction.

```powershell
# Windows: download from libreoffice.org, install with default options
soffice --version
```

```bash
# Linux
sudo apt-get install -y libreoffice  # Ubuntu/Debian
sudo dnf install -y libreoffice      # RHEL/Fedora

# macOS
brew install --cask libreoffice
```

After install, restart your terminal and re-run preflight:
```powershell
python scripts/preflight.py --config deploy.config.json
```

## 8.8 Indexer stuck

If the Azure portal shows the indexer "In progress" for >24h:

```powershell
python scripts/check_index.py --config deploy.config.json --check-stuck-indexer
```

Returns:
- exit 0: healthy
- exit 2: stuck (in_progress >24h, OR last 5 runs all failed)
- exit 3: cannot fetch status

If stuck:
```powershell
.\scripts\reset_indexer.ps1   # Windows
./scripts/reset_indexer.sh    # Linux/Mac
```

If indexer hits 230s timeout repeatedly → preanalyze cache is missing for
some PDF. Run:
```powershell
python scripts/preanalyze.py --config deploy.config.json --status
python scripts/preanalyze.py --config deploy.config.json --incremental
```

## 8.9 Cosmos DB issues

Cosmos is **optional** — pipeline works without it. If `cosmos.endpoint`
or `cosmos.database` is blank in deploy.config.json, all Cosmos writes
silently no-op.

```powershell
# 1. Provision Cosmos account
az cosmosdb create -n <cosmos-name> -g <rg> --kind GlobalDocumentDB `
  --default-consistency-level Session

# 2. Create the database
az cosmosdb sql database create -a <cosmos-name> -g <rg> -n indexing

# 3. Edit deploy.config.json
#    "cosmos": {
#      "endpoint": "https://<cosmos-name>.documents.azure.us:443/",
#      "database": "indexing"
#    }

# 4. Re-run RBAC (idempotent — only grants missing Cosmos roles)
python scripts/assign_roles.py --config deploy.config.json

# 5. Containers auto-create on first write
python scripts/run_pipeline.py --config deploy.config.json
```

**Cosmos write failures don't fail the pipeline.** They log a warning and
move on.

## 8.10 First-time setup, full sequence

If you're starting fresh on a new resource group / new laptop:

```powershell
# 1. Set SSL env vars (every new shell)
$env:SSL_CERT_FILE = "C:\Users\<you>\Downloads\combined-ca.crt"
$env:REQUESTS_CA_BUNDLE = "C:\Users\<you>\Downloads\combined-ca.crt"

# 2. Set Azure cloud + login
az cloud set --name AzureUSGovernment
az login
az account set --subscription "<your-sub-id>"

# 3. Verify environment
python scripts/preflight.py --config deploy.config.json

# 4. Grant RBAC roles
python scripts/assign_roles.py --config deploy.config.json --wait-for-propagation 300

# 5. Force fresh token after RBAC change
az logout
az login
az account set --subscription "<your-sub-id>"

# 6. Run the one-command deploy
python scripts/deploy.py --config deploy.config.json --auto-fix
```

If step 6 fails on `deploy_search`, run [§8.2](#82-deploy_searchpy-403)
and follow the matching sub-section.

## 8.11 When all else fails — run from Jenkins

Your Jenkins agent runs **inside the corporate network** with proper
allowlists, in the **correct subscription** (configured by infra), with
**managed identity** that already has the right roles.

If your laptop hits any of: corporate proxy block, IP firewall block,
weird tenant issue → push to main and let Jenkins do it:

1. Commit + push your changes (never commit `deploy.config.json` — it's
   uploaded into Jenkins as a secret-file credential, one per env)
2. Open Jenkins → trigger `Jenkinsfile.deploy` for the right environment
3. Watch the console output

## 8.12 Useful one-liners

```powershell
# What's my current Azure context?
az account show --query "{user:user.name, sub:name, tenant:tenantId}" -o table

# What roles do I have right now (across all scopes)?
$me = az ad signed-in-user show --query id -o tsv
az role assignment list --assignee $me --query "[].{role:roleDefinitionName, scope:scope}" -o table

# Coverage check on the index
python scripts/check_index.py --config deploy.config.json --coverage

# What's stuck in preanalyze?
python scripts/preanalyze.py --config deploy.config.json --status

# Storage container PDF count
az storage blob list --account-name <storage> --container-name <container> `
  --auth-mode login --query "length([?ends_with(name, '.pdf')])" -o tsv

# Trigger the indexer manually
.\scripts\reset_indexer.ps1
```

## 8.13 Pasting an error into chat for help

When asking for help with a 403 or other failure, include:

1. **The exact error message** (run the diagnostic in §8.2 to get the body)
2. **The JWT claims** (from the same diagnostic) — `upn`, `oid`, `tid`, `aud`
3. **Your Azure context**: `az account show -o table`
4. **The role list**: `az role assignment list --assignee $me --scope $searchId -o table`
5. **The Search service config**: `az search service show -n <name> -g <rg> -o json`

With those five things, the cause is usually obvious within minutes.
