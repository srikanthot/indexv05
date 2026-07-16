# Indexing Component — Runbook

## 1. Purpose

This runbook is a complete, standalone guide to the **indexing component** of the Technical
Manual assistant platform. It explains what the component is, the architecture, the services
it uses, why it is built this way, the permissions it needs, and the exact steps to deploy and
operate it through the Jenkins pipeline. A reader needs only this document to understand and
run the indexing pipeline.

The wider platform has three independently owned parts:

| Part | Responsibility |
|---|---|
| Infrastructure (Bicep) | Creates the Azure resources and their role assignments for each environment. |
| **Indexing (this document)** | **Processes the PDF manuals and builds/maintains the search index.** |
| Front-end / back-end | The chat interface and retrieval API that query the index. |

---

## 2. What the component does

Technical manuals are large PDFs (hundreds to a few thousand pages) that mix body text,
tables, and engineering diagrams. Users ask precise questions — a value in a table, the wiring
in a specific figure, a safety warning on a procedure — so plain text search is not enough.

The indexing component reads each PDF from blob storage, extracts its text, tables, and
figures, uses AI to describe the diagrams and summarise the document, and writes everything
into a single Azure AI Search index. Each manual is split into small **records** that the
chatbot retrieves and cites with the manual name, page number, and section headers.

The output is a retrieval-ready knowledge base that supports keyword search, vector (semantic)
search, and a semantic re-ranker, so the chatbot can answer precise technical questions and
point the user to the exact page and figure.

---

## 3. Architecture

```
        Upload PDFs
             │
             ▼
   ┌─────────────────────┐
   │  Blob Storage       │   <container>/           source PDFs
   │                     │   <container>/_dicache/  generated cache
   └─────────┬───────────┘
             │  STAGE 1  Pre-analyze (offline, slow: minutes per PDF)
             │           Document Intelligence + figure crops + AI vision
             ▼
   ┌─────────────────────┐
   │  _dicache/*.json    │   cached extraction results
   └─────────┬───────────┘
             │  STAGE 2  Indexer (fast: reads the cache)
             ▼
   ┌─────────────────────┐         ┌─────────────────────┐
   │ Azure AI Search     │ ──────▶ │ Azure Function App  │  custom skills:
   │ Indexer + Skillset  │ ◀────── │ (Python, Linux)     │  read cache, build records
   └─────────┬───────────┘         └─────────────────────┘
             ▼
   ┌─────────────────────┐         ┌─────────────────────┐
   │ Search Index        │         │ Cosmos DB           │  run history +
   │ (5 record types)    │         │  (database indexing)│  per-PDF state
   └─────────┬───────────┘         └─────────────────────┘
             │  search queries
             ▼
     Chatbot back-end / users
```

**Why two stages.** Document Intelligence and per-figure AI vision take several minutes on a
large PDF. Azure AI Search custom skills have a fixed 230-second timeout, so calling those
services live inside the indexer would time out. Instead, the heavy work runs **offline** in a
pre-analyze step that writes its results to blob storage (`_dicache/`); the indexer's skills
then read the cache in milliseconds. This keeps indexing fast and reliable, and means AI
vision is paid for once even if the index is rebuilt.

---

## 4. Services and components

### 4.1 Azure resources (per environment)

| # | Resource | Role in the pipeline |
|---|---|---|
| 1 | Resource group | Container for all resources in the environment. |
| 2 | Storage account | Holds the source PDFs and the `_dicache/` cache. Blob soft-delete must be ON. |
| 3 | Blob container | Where the manuals are uploaded. |
| 4 | Azure AI Search | Hosts the index, indexer, skillset, and data source. |
| 5 | Azure AI Foundry (chat/vision model) | Generates diagram descriptions and document summaries. |
| 6 | Embedding model (text-embedding-ada-002, 1536 dims) | Produces the vectors for semantic search. |
| 7 | Document Intelligence | Extracts layout, text, tables, and figure regions from PDFs. |
| 8 | AI Services (multi-service Cognitive account) | Identity/billing surface the built-in layout skill uses. |
| 9 | Function App (Linux, Python 3.12) | Hosts the custom skills that assemble the records. |
| 10 | Application Insights | Function App telemetry and logs. |
| 11 | Cosmos DB (database `indexing`) | Run history and per-PDF state for the dashboard. |
| 12 | CI/CD service principal | The identity the Jenkins pipeline uses to deploy and run. |

Resources 7 and 8 are frequently the same multi-service Cognitive account.

### 4.2 Function App custom skills

The indexer calls these skills (in the Function App) to build the records. Each reads the
pre-analyze cache and returns structured output:

| Skill | Produces |
|---|---|
| `extract-page-label` | Page number/label and a stable id for each text chunk. |
| `process-document` | The list of figures and tables to enrich for a PDF. |
| `analyze-diagram` | The AI vision description + OCR for each figure. |
| `shape-table` | A cleaned table record, plus per-row records for lookup tables. |
| `build-semantic-string` | A tuned string used for embedding and re-ranking. |
| `build-doc-summary` | One whole-document summary record per PDF. |

Built-in Azure Search skills handle layout extraction, text splitting, and embeddings.

### 4.3 Operational scripts

The pipeline orchestrates a set of Python scripts. The main ones:

| Script | Purpose |
|---|---|
| `deploy.py` | One command that chains the whole end-to-end deploy. |
| `bootstrap.py` | Deploys the Function App code and configures its settings. |
| `preanalyze.py` | Stage 1 — runs Document Intelligence + AI vision, writes the cache. |
| `deploy_search.py` | Creates/updates the index, skillset, indexer, and data source. |
| `reconcile.py` | Detects added / changed / deleted PDFs and purges stale records. |
| `run_pipeline.py` | The routine (incremental) operations orchestrator. |
| `heal_until_done.py` | Re-runs the indexer until every PDF is fully indexed. |
| `check_index.py` | Reports index coverage and diagnostics. |
| `preflight.py` | Pre-run validation of the environment and access. |

---

## 5. Why this approach

| Decision | Reason |
|---|---|
| Offline pre-analyze + cache | Document Intelligence and per-figure vision exceed the 230-second custom-skill timeout on large PDFs. Doing it offline and caching keeps the indexer fast and avoids re-paying vision costs on every rebuild. |
| Custom skills, not only built-ins | Built-in skills cannot do per-figure vision descriptions with OCR, table-to-markdown with page spans, per-row table records, or one-summary-per-document. These are essential for technical-manual questions. |
| Five record types in one index | Text, diagrams, tables, table rows, and summaries are retrieved together and distinguished by a `record_type` field, so the chatbot can route each question to the right kind of content. |
| Hybrid retrieval (keyword + vector + re-ranker) | Keyword catches exact part numbers and codes; vector catches paraphrases and concepts; the semantic re-ranker lifts the best result to the top. Together they cover all query styles. |
| Jenkins pipeline for automation | Pre-analyzing a large corpus can run for hours — longer than serverless time limits — and is bursty. Jenkins matches that workload and reuses existing CI infrastructure. |
| Managed identity everywhere | No secrets or connection strings to store or rotate. All service-to-service auth is Microsoft Entra (AAD) with least-privilege roles. |

---

## 6. The search index

Each PDF is fanned into **five record types**, all in one flat index, distinguished by
`record_type`:

| `record_type` | One record per… | Used for |
|---|---|---|
| `text` | Page-sized chunk of body text | General questions, context |
| `diagram` | One figure (AI description + OCR) | "Show/describe the diagram for…" |
| `table` | One whole table (markdown) | Questions about a table |
| `table_row` | One row of a lookup table | Precise value lookups |
| `summary` | One per document | Document-level questions, routing |

Every record carries citation metadata: source file, physical PDF page(s), printed page
label, and the section header chain. Diagram records add a bounding box for UI highlighting;
table rows add structured key/value fields; text can carry safety-callout flags. Records also
carry a document revision and an `is_current_revision` flag so the chatbot can restrict answers
to current manuals.

The index name follows the pattern `<prefix>-index`, where `<prefix>` is the environment's
configured artifact prefix; the indexer, skillset, and data source follow the same pattern.

---

## 7. Permissions

All service-to-service authentication is managed identity (AAD), least-privilege — **no Owner,
Contributor, or User Access Administrator**. The Bicep template assigns these roles per
environment to three identities: the pipeline service principal, the Search service identity,
and the Function App identity.

**Pipeline service principal** (the identity Jenkins uses):

| Role | On |
|---|---|
| Reader | Resource group |
| Website Contributor | Function App |
| Search Service Contributor | Search service |
| Search Index Data Contributor | Search service |
| Storage Blob Data Contributor | Storage account |
| Cognitive Services OpenAI User | Foundry / AOAI resource |
| Cognitive Services User | Document Intelligence |
| Cosmos DB Built-in Data Contributor | Cosmos account (data-plane SQL role) |

**Search service identity:**

| Role | On |
|---|---|
| Storage Blob Data Reader | Storage account |
| Cognitive Services OpenAI User | Foundry / AOAI resource |
| Cognitive Services User | AI Services account |

**Function App identity:**

| Role | On |
|---|---|
| Storage Blob Data Reader | Storage account |
| Cognitive Services OpenAI User | Foundry / AOAI resource |
| Cognitive Services User | Document Intelligence |
| Search Index Data Reader | Search service |
| Cosmos DB Built-in Data Contributor | Cosmos account (data-plane SQL role) |

The Cosmos assignments are Cosmos SQL data-plane role assignments, not standard Azure role
assignments. Role changes take a few minutes to take effect.

---

## 8. Configuration

Each environment has one configuration file (`deploy.config.json`) holding that environment's
resource names and endpoints: storage account, blob container, search endpoint, Foundry
endpoint, Document Intelligence endpoint, AI Services endpoint, Cosmos endpoint, and the
artifact prefix. It contains no secrets and is delivered to Jenkins as a per-environment
secret-file credential.

The Bicep template must also set these non-role configuration items: blob **soft-delete ON**
(7 days) on storage; **system-assigned identity ON** for the Search service and Function App;
the Function App on **Linux, Python 3.12** with the server-side build enabled and without the
run-from-package setting; and a Cosmos database named `indexing`.

---

## 9. Running the pipeline (Jenkins)

The pipeline is a multibranch job; the branch selects the environment:

| Branch | Environment |
|---|---|
| `dev` | Development |
| `qa` | QA |
| `main` | Production |

### 9.1 The ACTION parameter

Open the job for the target branch, choose **Build with Parameters**, and set **ACTION**:

| ACTION | What it does | When to use |
|---|---|---|
| `check` | Read-only. Runs preflight and reports index coverage. Changes nothing. | To verify access/config or see current state. Safe default. |
| `bootstrap` | One-time setup: deploys the Function App code and creates the search index, skillset, indexer, and data source. | First-time environment setup or a code/schema upgrade. |
| `deploy` | Full build: bootstrap + pre-analyze every PDF + run the indexer + heal until complete. Long-running. | First full load of an environment, or a full rebuild. |
| `run` | Routine operations: detect added/changed/deleted PDFs, pre-analyze the new ones, run the indexer, report coverage. | Nightly or on-demand incremental updates. |

Two checkboxes:

- **SKIP_TESTS** — leave unchecked (skips unit tests and lint; emergencies only).
- **DRY_RUN** — leave unchecked.

### 9.2 Recommended sequence for a new environment

1. **`check`** — confirms the pipeline can reach the resources and the config is valid.
2. **`deploy`** — the first full build (creates the index and indexes all PDFs). This runs for
   a while; pre-analyze is the slow stage.
3. **`check`** — confirms coverage (every PDF has records).
4. **`run`** — thereafter, for day-to-day incremental updates (schedule nightly).

### 9.3 What a build does

Each build checks out the code, sets up Python, logs in to Azure with the service principal,
selects the subscription for the branch, loads the environment configuration, runs preflight
validation and an offline schema check, performs the chosen ACTION, and archives the logs.

---

## 10. Running from a workstation (optional)

The same flow can be run manually for troubleshooting, from a machine with the Azure CLI:

```
az cloud set --name AzureUSGovernment
az login
az account set --subscription "<subscription-id>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# place the environment's deploy.config.json in the repo root, then:
python scripts/deploy.py --config deploy.config.json --skip-roles
```

`deploy.py` chains the same stages the pipeline runs (bootstrap → pre-analyze → deploy search
artifacts → run indexer → heal → coverage report). Every stage is idempotent and re-runnable.

---

## 11. Day-to-day operations

| Action | What to do | Result |
|---|---|---|
| Add a manual | Upload the PDF to the blob container | The next `run` pre-analyzes and indexes it. |
| Update a manual | Re-upload (overwrite) the PDF | `run` detects the change, purges old records and cache, and reindexes. |
| Delete a manual | Delete the PDF from the container | `run` purges its records and cache. |

Only PDFs are processed; other file types are skipped. Filenames may contain spaces — they are
handled automatically. For steady state, schedule **ACTION = `run`** (for example nightly), and
use **`check`** any time to see coverage.

---

## 12. Monitoring

Three sources of truth, in increasing reliability:

1. **Azure portal → Search service → Indexers → execution history** — best for inspecting the
   errors and warnings of a single run. The "Docs succeeded" count resets each run and is not a
   coverage measure.
2. **`ACTION = check`** — reports per-PDF coverage against the live index; the command-line
   source of truth.
3. **Dashboard** — reads the Cosmos DB run history and per-PDF state; the canonical view for
   operators and managers.

---

## 13. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Preflight fails: storage/Cosmos "not found" or "AuthorizationFailed" | Pipeline identity missing **Reader** (or another role) | Confirm the role assignments in Section 7 are applied to the pipeline identity. |
| `check` fails: index 404 / "index not found" | The environment was never deployed | Run `ACTION = deploy` first. |
| Indexer runs but 0 records indexed | Function App deployed without its dependencies, or an identity missing a role on Storage / Foundry / Document Intelligence | Open the indexer run's error text; confirm the Function App deployed with a server-side build and that the Section 7 roles are assigned. |
| Some figures have empty descriptions | AI vision content filter false-positive on a figure | Non-fatal; the figure is still indexed with page and headers and stays retrievable via surrounding text. |
| A PDF stays "not started" across runs | Deterministic failure, often a very large PDF exceeding Function App memory | Check the indexer/heal output for the named PDF; increase the Function App plan size if it is a memory limit. |

For any run, start from the **indexer execution history** error text — it names the failing
document and skill.
