# Indexing Component — Runbook

## 1. Purpose and scope

This runbook covers the **indexing component** of the Technical Manual assistant
platform: the pipeline that turns technical PDF manuals into a searchable, multimodal
Azure AI Search index that the chatbot back-end queries.

The overall platform is delivered in three parts, owned separately:

| Part | Responsibility |
|---|---|
| Infrastructure (Bicep) | Provisions the Azure resources and their role assignments per environment. |
| **Indexing (this document)** | **Processes the PDFs and builds/maintains the search index.** |
| Front-end / back-end | The chat UI and retrieval API that consume the index. |

This document explains what the indexing component is, how it is built, why it is built
that way, and the exact steps to deploy and operate it through the Jenkins pipeline. For
how other components connect to the index, see the **Integration Guide**.

---

## 2. Overview — what this component does

Technical manuals are large PDFs (hundreds to a few thousand pages) that mix body text,
tables, and engineering diagrams. A plain text search over them is not good enough: users
ask about specific figures, table values, and safety callouts.

This component reads each PDF from blob storage, extracts its text, tables, and figures,
generates AI descriptions of the diagrams and a document summary, and writes everything
into a single Azure AI Search index. Each manual is broken into small **records** that the
chatbot retrieves and cites — with the manual name, page number, and section headers.

The result is a retrieval-ready knowledge base that supports keyword search, vector
(semantic) search, and a semantic re-ranker, so the chatbot can answer precise technical
questions and point the user to the exact page and figure.

---

## 3. Architecture

```
        Upload PDFs
             │
             ▼
   ┌─────────────────────┐
   │  Blob Storage       │   <container>/         source PDFs
   │                     │   <container>/_dicache/ generated cache
   └─────────┬───────────┘
             │  (1) Pre-analyze  — offline, slow (minutes/PDF)
             │      Document Intelligence + figure crops + AI vision
             ▼
   ┌─────────────────────┐
   │  _dicache/*.json    │   cached extraction results
   └─────────┬───────────┘
             │  (2) Indexer  — fast, reads the cache
             ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ Azure AI Search     │ ─────▶ │ Azure Function App  │  custom skills
   │ Indexer + Skillset  │ ◀───── │ (Python, Linux)     │  (read cache, build records)
   └─────────┬───────────┘        └─────────────────────┘
             ▼
   ┌─────────────────────┐        ┌─────────────────────┐
   │ Search Index        │        │ Cosmos DB           │  run history +
   │ (5 record types)    │        │                     │  per-PDF state
   └─────────┬───────────┘        └─────────────────────┘
             │  search queries
             ▼
     Chatbot back-end / users
```

**Two stages on purpose.** Document Intelligence and per-figure AI vision can take several
minutes per large PDF. Azure AI Search custom skills have a hard 230-second timeout, so the
heavy work is done **offline** (pre-analyze) and cached to blob storage; the indexer's skills
then read the cache in milliseconds. This keeps indexing fast and reliable, and avoids paying
for AI vision twice when the index is rebuilt.

---

## 4. Components and services

### 4.1 Azure resources (per environment)

| # | Resource | Role in the pipeline |
|---|---|---|
| 1 | Resource group | Container for all resources in the environment. |
| 2 | Storage account | Holds the source PDFs and the `_dicache/` extraction cache. Blob soft-delete must be ON. |
| 3 | Blob container | The container the manuals are uploaded to. |
| 4 | Azure AI Search | Hosts the index, indexer, skillset, and data source. |
| 5 | Azure AI Foundry (chat/vision model) | Generates diagram descriptions and document summaries. |
| 6 | Embedding model (text-embedding-ada-002, 1536-dim) | Produces the vectors for semantic search. |
| 7 | Document Intelligence | Extracts layout, text, tables, and figure regions from PDFs. |
| 8 | AI Services (multi-service Cognitive account) | Billing/identity surface the built-in layout skill attaches to. |
| 9 | Function App (Linux, Python 3.12) | Hosts the custom skills that assemble the index records. |
| 10 | Application Insights | Function App telemetry and logs. |
| 11 | Cosmos DB (database `indexing`) | Stores run history and per-PDF state for the dashboard. |
| 12 | CI/CD service principal | The identity the Jenkins pipeline uses to deploy and run. |

> Resources 7 and 8 are often the **same** multi-service Cognitive account.

### 4.2 Function App custom skills

The indexer calls these skills (hosted in the Function App) to build the records. Each reads
the pre-analyze cache and returns structured output:

| Skill | Produces |
|---|---|
| `extract-page-label` | Page number/label and a stable id for each text chunk. |
| `process-document` | The list of figures and tables to enrich for a PDF. |
| `analyze-diagram` | The AI vision description + OCR for each figure. |
| `shape-table` | A cleaned table record (and per-row records for lookup tables). |
| `build-semantic-string` | A tuned string used for embedding and re-ranking. |
| `build-doc-summary` | One whole-document summary record per PDF. |

Built-in Azure Search skills handle layout extraction, text splitting, and embeddings.

---

## 5. Why this approach

| Decision | Reason |
|---|---|
| Offline pre-analyze + cache | Document Intelligence and per-figure vision exceed the 230-second custom-skill timeout on large PDFs. Doing it offline and caching keeps the indexer fast and avoids re-paying vision costs on every rebuild. |
| Custom skills (not only built-ins) | Built-in skills cannot do per-figure vision descriptions with OCR, table-to-markdown with page spans, or one-summary-per-document. These are essential for technical-manual questions. |
| Five record types in one index | Text, diagrams, tables, table rows, and summaries are retrieved together and distinguished by a `record_type` field, so the chatbot can route a question to the right kind of content. |
| Hybrid retrieval (keyword + vector + re-ranker) | Keyword catches exact part numbers; vector catches paraphrases and concepts; the semantic re-ranker lifts the best result to the top. Together they cover all query styles. |
| Jenkins pipeline for automation | Pre-analyzing a large corpus can run for hours — longer than serverless time limits. Jenkins matches the long-running, bursty workload and reuses existing CI infrastructure. |
| Managed identity everywhere | No secrets or connection strings to store or rotate. All service-to-service auth is Microsoft Entra (AAD) with least-privilege roles. |

---

## 6. The search index

Each PDF is fanned into **five record types**, all in one flat index, distinguished by
`record_type`:

| `record_type` | One record per… | Used for |
|---|---|---|
| `text` | Page-sized chunk of body text | General questions, context |
| `diagram` | One figure (AI vision description + OCR) | "Show me the wiring diagram for…" |
| `table` | One whole table (as markdown) | Questions about a table |
| `table_row` | One row of a lookup table | Precise value lookups ("value for 200A at 277V") |
| `summary` | One per document | Document-level questions, routing |

Every record carries citation metadata: source file, physical PDF page(s), printed page
label, and the section header chain (`header_1/2/3`). Diagram records add a bounding box for
UI highlighting; table rows add structured key/value fields. The full field catalog is in the
Integration Guide and in `search/index.json`.

The index name follows the pattern `<artifactPrefix>-index` (the prefix comes from the
environment's config file); the indexer, skillset, and data source follow the same pattern.

---

## 7. Prerequisites

### 7.1 Permissions

All service-to-service authentication is managed identity (AAD), least-privilege — **no
Owner, Contributor, or User Access Administrator**. The roles are assigned to three
identities (the pipeline service principal, the Search service identity, and the Function App
identity) and are provisioned by the Bicep template per environment. The full per-resource
role matrix is in **`docs/BICEP_RBAC_CHECKLIST.md`**.

### 7.2 Configuration

Each environment has one config file (`deploy.config.json`) holding that environment's
resource names and endpoints (storage account, container, search endpoint, Foundry endpoint,
Document Intelligence endpoint, Cosmos endpoint, artifact prefix). It contains no secrets and
is delivered to Jenkins as a per-environment secret-file credential. The template is
`deploy.config.example.json`.

### 7.3 Resource configuration flags

The Bicep template must also set: blob **soft-delete ON** (7 days) on storage;
**system-assigned identity ON** for the Search service and Function App; Function App on
**Linux, Python 3.12** with server-side build enabled; and a Cosmos database named `indexing`.

---

## 8. Running the pipeline (Jenkins)

The pipeline is a multibranch job. The branch selects the target environment:

| Branch | Environment |
|---|---|
| `dev` | Development |
| `qa` | QA |
| `main` | Production |

### 8.1 The ACTION parameter

Open the job for the target branch, choose **Build with Parameters**, and set **ACTION**:

| ACTION | What it does | When to use |
|---|---|---|
| `check` | Read-only. Runs preflight + reports index coverage. Changes nothing. | To verify access/config, or check current state. Safe default. |
| `bootstrap` | One-time setup: deploys the Function App code and creates the search index, skillset, indexer, and data source. | First-time environment setup (or code/schema upgrade). |
| `deploy` | Full build: bootstrap + pre-analyze every PDF + run the indexer + heal until complete. Long-running. | First full load of an environment, or a full rebuild. |
| `run` | Routine operations: detect added/changed/deleted PDFs, pre-analyze the new ones, run the indexer, report coverage. | Nightly / on-demand incremental updates. |

Two checkboxes:

- **SKIP_TESTS** — leave unchecked (skips unit tests/lint; emergencies only).
- **DRY_RUN** — leave unchecked.

### 8.2 Recommended sequence for a new environment

1. **`check`** — confirms the pipeline can reach the resources and the config is valid.
2. **`deploy`** — first full build (creates the index and indexes all PDFs). Runs for a while;
   the pre-analyze stage is the slow part.
3. **`check`** — confirms coverage (every PDF has records).
4. **`run`** — thereafter, for day-to-day incremental updates (can be scheduled nightly).

### 8.3 What the pipeline stages do

On each build the pipeline: checks out the code, sets up Python, logs in to Azure with the
service principal, selects the subscription for the branch, loads the environment config,
runs preflight validation and a schema check, then performs the chosen ACTION and archives
the logs.

---

## 9. Running locally (optional)

The same steps can be run from a workstation with the Azure CLI, for troubleshooting:

```
az cloud set --name AzureUSGovernment
az login
az account set --subscription "<subscription-id>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows
pip install -r requirements.txt
# place the environment's deploy.config.json in the repo root, then:
python scripts/deploy.py --config deploy.config.json --skip-roles
```

`deploy.py` chains the same stages the pipeline runs (bootstrap → pre-analyze → deploy search
artifacts → run indexer → heal → coverage report). Every stage is idempotent and re-runnable.

---

## 10. Day-to-day operations

| Action | What to do | Result |
|---|---|---|
| Add a manual | Upload the PDF to the blob container | The next `run` picks it up, pre-analyzes it, and indexes it. |
| Update a manual | Re-upload (overwrite) the PDF | `run` detects the change, purges the old records and cache, and reindexes. |
| Delete a manual | Delete the PDF from the container | `run` purges its records and cache. |

Only PDFs are processed; other file types are skipped. Filenames may contain spaces — they are
handled automatically.

For steady state, schedule **ACTION = `run`** (e.g. nightly). Use **`check`** any time to see
coverage.

---

## 11. Monitoring

Three sources of truth, in increasing reliability:

1. **Azure portal → Search service → Indexers → execution history.** Best for inspecting the
   errors/warnings of a single run. The "Docs succeeded" count resets every run and is **not**
   a coverage measure.
2. **`ACTION = check`** (or `python scripts/check_index.py --config deploy.config.json
   --coverage`) — reports per-PDF coverage against the live index. Command-line source of truth.
3. **Dashboard** — reads the Cosmos DB run history and per-PDF state; the canonical view for
   operators and managers.

---

## 12. Troubleshooting quick reference

| Symptom | Likely cause | Action |
|---|---|---|
| Preflight fails: storage/Cosmos "not found" or "AuthorizationFailed" | Pipeline identity missing **Reader** (or other roles) on the resource group | Ensure the Bicep role assignments applied; see `docs/BICEP_RBAC_CHECKLIST.md`. |
| `check` fails: index 404 / "index not found" | The environment was never deployed | Run `ACTION = deploy` first. |
| Indexer runs but 0 records indexed | Function App skills failing (e.g. deployed without dependencies), or an identity missing a role on Storage/Foundry/DI | Check the indexer run's error text; confirm the Function App deployed with a server-side build and the managed-identity roles are assigned. |
| Some figures have empty descriptions | AI vision content filter false-positive on a figure | Non-fatal; the figure is still indexed with page/headers and is retrievable via surrounding text. |
| A PDF stays "not started" across runs | Deterministic failure (often a very large PDF exceeding Function App memory) | Inspect the indexer/heal output for the named PDF; increase the Function App plan size if it is a memory limit. |

For a specific run, always start from the **indexer execution history** error text — it names
the failing document and skill.
