# Multimodal Manuals Indexing Pipeline

Production indexing pipeline that turns technical PDF manuals into a
multimodal Azure AI Search index queryable by an AI assistant. Each
manual produces four record types as peers in a single index:

| record_type | content                                              |
|-------------|------------------------------------------------------|
| `text`      | A chunk of body text with section headers and pages  |
| `table`     | One table as markdown                                |
| `diagram`   | One figure with a GPT-4 Vision description + OCR     |
| `summary`   | One per-document summary                             |

All four record types are embedded with `text-embedding-ada-002` and
returned with citation metadata (manual, page, headers, bbox).

This repository contains the **application layer** only: function code,
search artifacts, deploy + operational scripts, tests, docs. Azure
infrastructure (Storage, Search, AOAI, DI, Function App, Cosmos DB,
Application Insights) is provisioned out-of-band.

> **Cloud:** Azure Government (`*.azure.us`). The codebase hardcodes
> Government cloud scopes; do not deploy to commercial Azure without
> auditing every script's authentication scope.

---

## Architecture (one screen)

```
                                                ┌──────────────────┐
              upload PDFs                       │  Power BI        │
                  ▼                             │  dashboard       │
    ┌──────────────────────┐                    └────────▲─────────┘
    │ Azure Blob Storage   │                             │
    │   <container>/       │                             │
    │   <container>/_dicache/                            │
    └────────┬─────────────┘                             │
             │                                           │ reads
             │ 1) preanalyze (offline, slow)             │
             │    DI + GPT-4 Vision + crops              │
             ▼                                           │
    ┌──────────────────────┐                             │
    │  _dicache/*.json     │                             │
    │  (cache for indexer) │                             │
    └────────┬─────────────┘                             │
             │ 2) indexer reads cache (fast)             │
             ▼                                           │
    ┌──────────────────────┐                             │
    │ Azure AI Search      │  ──── status ──┐            │
    │ Indexer + Skillset   │                │            │
    └────────┬─────────────┘                │            │
             │                              │            │
             ▼                              ▼            │
    ┌──────────────────────┐      ┌──────────────────────┴───┐
    │ Search Index         │      │ Cosmos DB                │
    │ (4 record types)     │      │  - run history           │
    │                      │      │  - per-PDF state         │
    └──────────────────────┘      └──────────────────────────┘
             ▲
             │ search queries
        AI assistant / users
```

Two stages on purpose: pre-analyze can take 5–60 minutes per large PDF.
The Azure Search WebApi-skill timeout is 230 seconds. Doing slow work
offline and caching the result keeps the indexer fast and reliable.

---

## Prerequisites

Provisioned by the infrastructure team (Bicep / Terraform / portal):

- **Azure AI Search** — Standard tier or higher. System-assigned MI on.
- **Azure Storage** account with the source-PDF container.
  **Blob soft-delete must be enabled** (Data protection blade) so the
  indexer's deletion-detection policy works and accidental deletes are
  recoverable.
- **Azure OpenAI** with deployments named per `deploy.config.json`:
  - `text-embedding-ada-002` (1536 dims)
  - `gpt-4.1` for chat + vision
- **Azure Document Intelligence** (prebuilt-layout).
- **Azure AI Services** multi-service account (billing for the Layout skill).
- **Azure Function App** — Linux, Python 3.11, Functions v4. MI on.
- **Application Insights** — connection string written to function app settings.
- **Cosmos DB** account with database `indexing` (containers are
  auto-created on first run).

Required role assignments are listed in [docs/SETUP.md §3](docs/SETUP.md#3-rbac).

Local prerequisites for running scripts manually:

- Python 3.11+
- Azure CLI (`az`) authenticated to the target subscription
- `pip install -r requirements.txt`

---

## Repository layout

```
.
├── function_app/              Azure Function App (custom skills)
│   ├── function_app.py        Entry point + HTTP routing
│   ├── host.json
│   ├── requirements.txt
│   └── shared/                Skill implementations + utilities
│
├── scripts/                   Operational tooling (runs from Jenkins or laptop)
│   ├── preanalyze.py          Stage 1: DI + Vision, populates _dicache/
│   ├── reconcile.py           Detect added / edited / deleted PDFs
│   ├── run_pipeline.py        End-to-end orchestrator (the one Jenkins runs)
│   ├── check_index.py         Coverage + diagnostics, --write-status to Cosmos
│   ├── cosmos_writer.py       Helper used by other scripts
│   ├── deploy_search.py       Deploy index/skillset/datasource/indexer
│   ├── deploy_function.sh     Deploy function app code (Linux)
│   ├── deploy_function.ps1    Deploy function app code (Windows)
│   ├── smoke_test.py          Post-deploy validation gate
│   ├── reset_indexer.sh       Reset + run indexer (bash)
│   ├── reset_indexer.ps1      Reset + run indexer (PowerShell)
│   ├── diagnose.py            One-shot health probe (function app + indexer)
│   ├── preflight.py           Pre-deploy environment validation
│   └── assign_roles.ps1       One-time RBAC bootstrapper
│
├── search/                    Azure AI Search artifact templates
│   ├── datasource.json
│   ├── index.json
│   ├── skillset.json
│   └── indexer.json
│
├── tests/                     Pure-Python (no Azure deps)
│   ├── test_unit.py
│   ├── test_e2e_simulator.py
│   └── test_filename_spaces.py
│
├── docs/                      All documentation, in 3 mega-docs:
│   ├── SETUP.md                  ARCHITECTURE + BOOTSTRAP + RBAC + JENKINS
│   │                             + DASHBOARD_SPEC + SEARCH_INDEX_GUIDE
│   ├── RUNBOOK.md                Daily operations + preanalyze guide +
│   │                             validation + content capture + incidents
│   └── SCENARIOS.md              511 scenarios (general + preanalyze + indexer)
│
├── Jenkinsfile.deploy         CI pipeline (push to main)
├── Jenkinsfile.run            CI pipeline (nightly cron + manual)
├── .github/workflows/ci.yml   PR gate: pytest + ruff
├── deploy.config.example.json Template; copy to deploy.config.json
├── requirements.txt
├── ruff.toml
└── README.md
```

---

## First-time deployment

```bash
# 1. Configure
cp deploy.config.example.json deploy.config.json
# Fill in identifiers for your environment. Never commit this file.

# 2. Authenticate
az cloud set --name AzureUSGovernment
az login

# 3. Deploy function app code (Linux)
./scripts/deploy_function.sh deploy.config.json

# 4. Deploy search artifacts (index, skillset, datasource, indexer)
python scripts/deploy_search.py --config deploy.config.json

# 5. Validate
python scripts/smoke_test.py --config deploy.config.json
```

In production, steps 3–5 are run by `Jenkinsfile.deploy` on every push
to `main`. See [docs/SETUP.md §4](docs/SETUP.md#4-jenkins).

---

## Steady-state operations

One command runs the full operational loop:

```bash
python scripts/run_pipeline.py --config deploy.config.json
```

It:

1. **Reconciles** — detects added/edited/deleted PDFs, purges stale
   index records and cache for any deleted/edited PDF, leaves the rest
   alone.
2. **Pre-analyzes** any PDF without a complete cache (DI + Vision).
3. **Waits** for the indexer to settle (the indexer runs on a 15-min
   schedule and picks up new caches automatically; `run_pipeline.py`
   just polls until it's idle).
4. **Reports coverage** — counts of done / partial / not-started PDFs.
5. **Persists** a run record + per-PDF state to Cosmos DB so the
   dashboard reflects current state.

Run nightly via `Jenkinsfile.run`; can also be triggered on-demand
from the Jenkins UI.

---

## Adding, editing, and deleting PDFs

| Action | What to do | What happens |
|--------|------------|--------------|
| **Add** a PDF | Upload to the blob container | Next pipeline run picks it up automatically |
| **Edit** a PDF | Re-upload (overwrite) | Reconcile purges old chunks; preanalyze regenerates cache; indexer reindexes the new content |
| **Delete** a PDF | Delete from the blob container | Reconcile purges chunks + cache + Cosmos state |

**Only PDFs are processed.** PowerPoint, Word, and Excel files in the
container are silently skipped. Convert to PDF before upload.

**Filenames may contain spaces and most punctuation** — they are
URL-encoded automatically. Do not pre-encode.

---

## Operational status

Three sources of truth, in increasing reliability:

1. **Azure portal indexer page** — useful for inspecting individual run
   errors. The "Docs succeeded" column resets every run and is **not**
   a coverage measure. Don't use it to answer "how many manuals are
   indexed?".

2. **`scripts/check_index.py --coverage`** — queries the live index,
   prints per-PDF state. Source of truth on the command line.

3. **Power BI dashboard** — reads the Cosmos DB containers
   `indexing_run_history` and `indexing_pdf_state`. Single canonical
   view for managers and operators.
   See [docs/SETUP.md §5](docs/SETUP.md#5-dashboard-spec).

---

## When something breaks

See [docs/RUNBOOK.md §5](docs/RUNBOOK.md#5-incident-response) for the top
failure modes and recovery steps. Quick links:

- Indexer timing out / 0 docs succeeded → preanalyze cache missing for
  some PDF. Run `preanalyze.py --status` then `--incremental`.
- PDFs stuck in PARTIAL → re-run `reconcile.py --dry-run` to diagnose,
  then `run_pipeline.py`.
- Edits not reflecting in search → reconcile.py finds and purges stale
  chunks; run the pipeline.

For deeper architecture and configuration reference, see
[docs/SETUP.md §1](docs/SETUP.md#1-architecture) and
[docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Tests + CI

```bash
python tests/test_unit.py
python tests/test_e2e_simulator.py
python tests/test_filename_spaces.py
ruff check function_app tests scripts
```

GitHub Actions runs all four on every PR + push to `main`. Required
checks should be enforced in branch protection.

The Jenkins deploy pipeline runs the same gate plus `smoke_test.py`
post-deploy.

---

## Security model

- All inter-service auth is **AAD / managed identity** (`AUTH_MODE=mi`,
  the default).
- The single embedded credential is the Function App key, present in
  the deployed skillset only. Rotated by re-running `deploy_search.py`
  after `az functionapp keys set`.
- API keys (`AUTH_MODE=key`) remain supported for local development
  only.
- Per-environment `deploy.config.json` is excluded from git; it is
  delivered to Jenkins via secret file credentials.
- See [docs/SETUP.md §3](docs/SETUP.md#3-rbac) for the full role matrix.

---

## Licensing note

PyMuPDF (used for figure cropping in `function_app/shared/pdf_crop.py`
and `scripts/preanalyze.py`) is licensed under **AGPL-3.0**. For an
internal Azure Function App this is normally fine; the AGPL network
clause is triggered by distribution of modified source, not by running
the library behind a function endpoint. Review with legal before
shipping any externally-distributable build.
