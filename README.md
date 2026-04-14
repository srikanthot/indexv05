# Azure AI Search — Multimodal Manual Indexing (v3.0)

Azure-native pipeline for diagram-heavy technical manuals. One
multimodal index holds **text**, **diagram**, **table**, and
**summary** records as peers — all embedded with Ada-002 and
re-queryable via a built-in Azure OpenAI vectorizer.

Custom logic runs in an Azure Function App exposed as Custom WebApi
skills. Diagram analysis and document summaries use **gpt-4.1**.

## What's in v3.0

Production-readiness upgrade over v2.2:

- **Managed-identity-first** auth: Function App and Search service
  reach every dependency (AOAI, DI, Storage, Search) via AAD tokens.
- **Infrastructure-as-code**: `infra/main.bicep` provisions every
  resource, every RBAC assignment, App Insights, Log Analytics.
- **One-shot deploy**: `scripts/deploy.sh <env>` runs infra → function
  publish → search-artifact rendering.
- **CI**: tests + Bicep build + lint on every PR; dispatchable deploy
  workflow with per-environment approval gates.
- **Observability**: App Insights connection string wired via Bicep;
  `azure-monitor-opentelemetry` included.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list, and
[`docs/deployment.md`](docs/deployment.md) for the deploy flow.

## Architecture

```
Blob (PDFs)
   |
   v
Data Source -> Indexer -> Skillset -> mm-manuals-index
                            |
                            +-- DocumentIntelligenceLayoutSkill (markdown text path)
                            +-- SplitSkill (1200 / 200)
                            +-- WebApi: process-document   --> Function -> DI direct -> figures + tables
                            +-- WebApi: extract-page-label --> Function
                            +-- WebApi: analyze-diagram    --> Function (per figure, hash-cached, gpt-4.1 vision)
                            +-- WebApi: shape-table        --> Function (per table)
                            +-- WebApi: build-semantic-string --> Function (text + diagram modes)
                            +-- WebApi: build-doc-summary  --> Function (gpt-4.1)
                            +-- AOAI Embedding x4 (text / figures / tables / summary)
```

Index projections write four peer record types:

| record_type | sourceContext                          | chunk_id prefix |
|-------------|----------------------------------------|-----------------|
| text        | /document/markdownDocument/*/pages/*   | `txt_`          |
| diagram     | /document/enriched_figures/*           | `dgm_`          |
| table       | /document/enriched_tables/*            | `tbl_`          |
| summary     | /document                              | `sum_`          |

## Repository layout

```
infra/                    Bicep IaC
  main.bicep              Subscription-scoped entry
  modules/resources.bicep All RG-scoped resources + RBAC
  parameters/*.bicepparam Per-env values

scripts/
  deploy.sh / deploy.ps1  One-shot deploy (infra + function + search)
  deploy_search.py        Render & PUT search artifacts via AAD

search/                   Search REST bodies (templated)
function_app/             Python v2 Functions app
  shared/
    credentials.py        Central MI/credential helper
    config.py             Typed env-var access
    aoai.py               Azure OpenAI client (MI first)
    di_client.py          DI REST client + blob fetch (MI first)
    search_cache.py       Hash-cache lookup (MI first)
    skill_io.py           WebApi envelope + error translation
    ids.py / page_label.py / pdf_crop.py / sections.py / tables.py /
    process_document.py / process_table.py / semantic.py /
    diagram.py / summary.py

tests/
  test_unit.py            68 unit assertions
  test_e2e_simulator.py   Full end-to-end handler simulation

docs/
  setup.md
  deployment.md
  security.md
  runbook.md
  validation.md

.github/workflows/
  ci.yml                  Tests + Bicep build + ruff on PR
  deploy.yml              Dispatchable per-env deploy

CHANGELOG.md
```

## Quick start

```bash
# 1. Authenticate to the target subscription
az login
az account set --subscription <sub-id>

# 2. Deploy dev
scripts/deploy.sh dev --run-indexer
```

That's it. No secrets to paste, no portal clicks. See
[`docs/deployment.md`](docs/deployment.md) for details.

## Local development

Tests run with no Azure credentials:

```bash
python tests/test_unit.py
python tests/test_e2e_simulator.py
```

Running the function locally with `AUTH_MODE=key` + populated
`local.settings.json` lets you hit real AOAI / DI / Storage without
the Azure CLI login chain.

## Validation

See [`docs/validation.md`](docs/validation.md) for post-deploy checks.

## Operations

See [`docs/runbook.md`](docs/runbook.md) for dashboards, alerts,
incident responses, and re-indexing procedures.

## Security

See [`docs/security.md`](docs/security.md) for the auth model, secret
surface, and production hardening recommendations (private endpoints,
network isolation).
