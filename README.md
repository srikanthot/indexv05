# Multimodal Manuals Indexing Pipeline

Production indexing pipeline that turns technical PDF manuals into a
multimodal Azure AI Search index queryable by an AI assistant. Each
manual produces five record types as peers in a single index:

| `record_type` | Content |
|---|---|
| `text`      | A chunk of body text with section headers + page numbers |
| `table`     | A full table rendered as markdown |
| `table_row` | One row of a shaped table (5–80 row tables only) |
| `diagram`   | One figure with a GPT-4 Vision description + OCR |
| `summary`   | One per-document summary |

All record types are embedded with `text-embedding-ada-002` (1536 dim)
and returned with citation metadata (manual, page, headers, bbox).

This repository contains the **application layer** only: function code,
search artifacts, deploy + operational scripts, tests, docs. Azure
infrastructure (Storage, Search, AOAI, DI, Function App, Cosmos DB,
Application Insights) is provisioned out-of-band.

> **Cloud:** Azure Government (`*.azure.us`). The codebase hardcodes
> Government cloud scopes;

> **Building a chatbot on top of this index?**
> Read **[CHATBOT_INTEGRATION.md](CHATBOT_INTEGRATION.md)** — the full
> hand-off spec for the engineer or LLM agent wiring chatbot retrieval.
> It explains how to use every record type (text, diagram, table,
> table_row, summary) and every field so the chatbot extracts the full
> value of the index.

---

## Architecture

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
    │ (5 record types)     │      │  - run history           │
    │                      │      │  - per-PDF state         │
    └──────────────────────┘      └──────────────────────────┘
             ▲
             │ search queries
        AI assistant / users
```

Two stages on purpose: pre-analyze can take 5–60 minutes per large
PDF. The Azure Search WebApi-skill timeout is 230 seconds. Doing slow
work offline and caching the result keeps the indexer fast and reliable.

---

## Prerequisites

Provisioned by the infrastructure team (Bicep / Terraform / portal):

- **Azure AI Search** — Standard tier or higher. System-assigned MI on.
- **Azure Storage** account with the source-PDF container.
  Blob soft-delete must be enabled (Data protection blade) so the
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
  auto-created on first run; optional).

Required role assignments are listed in [docs/RUNBOOK.md §5](docs/RUNBOOK.md#5-incident-response) and granted automatically by `python scripts/assign_roles.py --config deploy.config.json`.

Local prerequisites for running scripts manually:

- Python 3.11+
- Azure CLI (`az`) authenticated to the target subscription
- `pip install -r requirements.txt`

---

## Repository layout

```
.
├── function_app/              Azure Function App (custom skills)
│   ├── function_app.py        HTTP routing + auto-heal timer
│   ├── host.json
│   ├── requirements.txt
│   └── shared/                Skill implementations + utilities
│
├── scripts/                   Operational tooling (Jenkins or laptop)
│   ├── deploy.py              ★ ONE-COMMAND end-to-end deploy
│   ├── bootstrap.py           Provision + RBAC + app settings + function code
│   ├── heal_until_done.py     Loop indexer + force-reindex until 100%
│   ├── preanalyze.py          Stage 1: DI + Vision, populates _dicache/
│   ├── reconcile.py           Detect added / edited / deleted PDFs
│   ├── run_pipeline.py        Daily operations orchestrator
│   ├── check_index.py         Coverage + diagnostics
│   ├── cosmos_writer.py       Cosmos DB persistence helper
│   ├── deploy_search.py       Deploy index/skillset/datasource/indexer
│   ├── deploy_function.sh     Deploy function app code (Linux)
│   ├── deploy_function.ps1    Deploy function app code (Windows)
│   ├── smoke_test.py          Post-deploy validation gate
│   ├── reset_indexer.{sh,ps1} Reset + run indexer
│   ├── diagnose.py            Health probe (function app + indexer)
│   ├── inspect_pdf.py         Per-PDF cache + index inspection
│   ├── preflight.py           Pre-deploy environment validation
│   ├── assign_roles.py        One-time RBAC bootstrapper
│   ├── reap_stale_rows.py     Cleanup stale index records
│   ├── force_reindex_blobs.ps1  Force reindex of specific PDFs
│   ├── rerun_failed_docs.ps1  Surgical retry of failed PDFs
│   └── pipeline_lock.py       Cross-script pipeline lock
│
├── search/                    Azure AI Search artifact templates
│   ├── datasource.json
│   ├── index.json
│   ├── skillset.json
│   └── indexer.json
│
├── tests/                     Pure-Python tests
│   ├── test_unit.py
│   ├── test_e2e_simulator.py
│   ├── test_filename_spaces.py
│   └── test_techmanual_capture.py
│
├── docs/
│   └── RUNBOOK.md             Everything operational: setup, ops, RBAC, Jenkins,
│                              dashboard spec, incident response, troubleshooting
│
├── CHATBOT_INTEGRATION.md     Hand-off spec for the chatbot dev team
├── Jenkinsfile.deploy         CI pipeline (push to main)
├── Jenkinsfile.run            CI pipeline (nightly cron + manual)
├── .github/workflows/ci.yml   PR gate: pytest + ruff
├── deploy.config.example.json Template; copy to deploy.config.json
├── requirements.txt
├── ruff.toml
└── README.md
```

---

## First-time deployment — ONE command

```bash
# 1. Configure
cp deploy.config.example.json deploy.config.json
# Fill in identifiers for your environment. Never commit this file.

# 2. Authenticate
az cloud set --name AzureUSGovernment
az login

# 3. ONE command does the whole end-to-end deploy
python scripts/deploy.py --config deploy.config.json --auto-fix
```

`deploy.py` runs the full pipeline in order: bootstrap (RBAC, Cosmos
DB, Function App app settings incl. `AUTO_HEAL_ENABLED=true`, function
code deploy) → preanalyze (DI + Vision cache) → deploy search artifacts
→ reset + run indexer → heal-until-done loop → final coverage report.

Exit code `0` = every PDF in the container is indexed. Exit code `1`
means the heal loop detected a deterministic failure on specific PDFs
(its output names them) — usually a Function App memory limit hit by
the largest files; bump the App Service Plan SKU and re-run.

For a partial deploy or to skip phases, see:

```bash
python scripts/deploy.py --help
# --skip-bootstrap, --skip-preanalyze, --skip-heal-loop, etc.
```

The lower-level scripts (`bootstrap.py`, `deploy_function.{ps1,sh}`,
`deploy_search.py`, `preanalyze.py`, `heal_until_done.py`,
`check_index.py`) are still individually invokable. `scripts/deploy.py`
just chains them in the right order with the right flags.

In production, `Jenkinsfile.deploy` (one-time provision) and
`Jenkinsfile.run` (nightly ops) both wrap these scripts. See
[docs/RUNBOOK.md §6](docs/RUNBOOK.md#6-jenkins-pipeline-setup).

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

Only PDFs are processed. PowerPoint, Word, and Excel files in the
container are silently skipped. Convert to PDF before upload.

Filenames may contain spaces and most punctuation — they are
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
   See [docs/RUNBOOK.md §7](docs/RUNBOOK.md#7-power-bi-dashboard-spec).

---

## When something breaks

See [docs/RUNBOOK.md §5](docs/RUNBOOK.md#5-incident-response) for
incident playbooks and [docs/RUNBOOK.md §8](docs/RUNBOOK.md#8-first-time-setup-troubleshooting)
for first-time-setup diagnostics (SSL, proxy, firewall, 403). Quick links:

- Indexer timing out / 0 docs succeeded → preanalyze cache missing for
  some PDF. Run `preanalyze.py --status` then `--incremental`.
- PDFs stuck in PARTIAL → re-run `reconcile.py --dry-run` to diagnose,
  then `run_pipeline.py`.
- Edits not reflecting in search → reconcile.py finds and purges stale
  chunks; run the pipeline.

---

## Tests + CI

```bash
python tests/test_unit.py
python tests/test_e2e_simulator.py
python tests/test_filename_spaces.py
ruff check function_app tests scripts
```

GitHub Actions runs all four on every PR + push to `main` via
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). Required checks
should be enforced in branch protection.

The Jenkins deploy pipeline runs the same gate plus `smoke_test.py`
post-deploy.

---

## Security model

- All inter-service auth is **AAD / managed identity** (`AUTH_MODE=mi`,
  the default).
- The single embedded credential is the Function App key, present in
  the deployed skillset only. Rotated by re-running `deploy_search.py`
  after `az functionapp keys set`.
- API keys (`AUTH_MODE=key`) remain supported for local development only.
- Per-environment `deploy.config.json` is excluded from git; deliver
  it to Jenkins via secret file credentials.
- See [docs/RUNBOOK.md](docs/RUNBOOK.md) for the full role matrix and operational details.

---

## Licensing note

PyMuPDF (used for figure cropping in `function_app/shared/pdf_crop.py`
and `scripts/preanalyze.py`) is licensed under **AGPL-3.0**. For an
internal Azure Function App this is normally fine; the AGPL network
clause is triggered by distribution of modified source, not by running
the library behind a function endpoint. Review with legal before
shipping any externally-distributable build.
