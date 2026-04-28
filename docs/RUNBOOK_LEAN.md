# 2.3 Indexing Pipeline — Engineering Runbook

Companion to the deep reference in [RUNBOOK.md](RUNBOOK.md). This
document is structured the way a senior engineer thinks about a new
system: what it is, what it's made of, why it looks like this, how it
runs, what you need before you can deploy it, what depends on what,
what permissions are involved, and finally how to actually stand it
up and operate it.

If a section needs a specific command, schema field, or failure
walkthrough you don't find here, it lives in `RUNBOOK.md`.

---

## 2.3.1 What it is

A multimodal RAG indexing pipeline for technical PDF manuals. It
takes PDFs from a blob container and produces an Azure AI Search
index where every searchable unit — a body-text chunk, a figure, a
table, a per-document summary — is one record, queryable by keyword,
vector similarity, or both.

The output is consumed by a chat / search front-end that answers
questions like *"what is wired to terminal X7 on the overcurrent
relay?"* with a citation to the exact figure and page in the source
PDF.

This repository owns the **application layer only** — function code,
search artifacts, deploy scripts, the offline pre-analyzer. It does
not provision Azure resources and it does not move PDFs from
SharePoint into the blob (a separate automation handles that).

---

## 2.3.2 Components

The system is a small set of cooperating Azure services. Each one is
deliberately chosen and has a single job; there is no overlap.

**Blob container.** The system of record for PDFs. Everything
downstream is derived from this. The same container also holds a
`_dicache/` prefix that the pre-analyzer writes its cache into, so
PDFs and their cached intermediate results stay co-located.

**Pre-analyzer (`scripts/preanalyze.py`).** A Python script that
runs offline, reads each PDF, calls Document Intelligence for layout
extraction, crops every figure with PyMuPDF, and asks GPT-4.1 Vision
to describe each figure. It stores the entire result back into the
blob container under `_dicache/<pdf>.*`. This is the heaviest part
of the pipeline and the reason we cannot do everything inside the
indexer skillset (see §2.3.3).

**Azure Function App.** A Linux Python 3.11 Function App that hosts
six custom skills exposed as HTTP endpoints. The Search indexer
calls these skills as part of the skillset. They do *not* re-run DI
or vision — they read from the pre-analyzer's cache, transform it
into the shape the index needs, and return in milliseconds.

**Azure AI Search.** The destination. Holds four objects:

- `datasource` — points at the blob container.
- `index` — the schema (around 40 fields) and vector / semantic
  configuration.
- `skillset` — the ordered pipeline of built-in and custom skills
  that turns each PDF into records.
- `indexer` — the scheduler / executor that runs the skillset over
  every blob.

**Azure OpenAI.** Two deployments: `text-embedding-ada-002` (1,536
dims) for embeddings used by both the index and query-time
vectorization, and `gpt-4.1` for vision (per-figure description) and
per-document summaries.

**Document Intelligence.** Used by the pre-analyzer for prebuilt-
layout extraction. The built-in `DocumentIntelligenceLayoutSkill`
inside the indexer also calls it (billed against the AI Services
multi-service account).

**AI Services multi-service account.** The billing target for the
built-in Layout skill — referenced by the skillset via
`AIServicesByIdentity`. It does not host any code; it is purely a
billing endpoint.

**Application Insights.** Telemetry sink for the Function App.

**Container Apps environment + two jobs.** The production automation
layer. One job is event-triggered (drains a queue of blob events);
the other is a nightly cron reconciliation. Container Apps Jobs are
chosen because they have no execution time limit — a 2,000-page PDF
takes hours to pre-analyze, and Functions would time out.

**Event Grid + Storage Queue.** The plumbing that connects blob
events to the Container App Job. Event Grid subscribes to
`BlobCreated` and `BlobDeleted` on the storage account, filters to
`.pdf`, and writes onto the queue. The queue exists because Event
Grid is at-least-once and bursty — the queue absorbs the burst,
deduplicates retries, and provides a dead-letter destination.

---

## 2.3.3 Why we chose this approach

Three Azure constraints make the obvious "build it inside the Search
indexer" approach impossible at our document size, and several less
obvious ones explain the rest of the choices.

The single biggest constraint is the **230-second timeout on Azure
AI Search custom WebApi skills**. This is enforced at the service
layer and cannot be raised. A 500-page technical manual takes
Document Intelligence three to fifteen minutes to lay out, and
GPT-4.1 Vision needs ten to seventy-five minutes to describe its
1,500 figures. Neither fits in 230 seconds. So if we tried to call
DI or vision live from a custom skill, every realistic PDF would
time out and never make it into the index.

The way out is to do the heavy work offline, before the indexer
runs. `preanalyze.py` does exactly that. It calls DI with a 30-min
timeout, runs forty parallel vision calls per PDF, and caches every
result to blob. At index time the custom skills only read from this
cache, which takes milliseconds. The 230-second budget is more than
enough.

The cache also has a second value: re-indexing after a schema change
or a bug fix doesn't re-run vision. We pay GPT-4.1 once per figure,
ever, until that figure changes.

For the parts the built-in skills *can* handle, we use them.
`DocumentIntelligenceLayoutSkill` produces markdown with header
structure preserved. `SplitSkill` chunks it. `AzureOpenAIEmbeddingSkill`
calls ada-002. We did not reinvent any of these. Custom skills exist
only where there is no equivalent: per-figure GPT-4 Vision with OCR
(generic image tags from the wizard are not enough for technical
diagrams), page-label mapping, table-to-markdown with caption and
page-span tracking, the tuned `chunk_for_semantic` string, and the
per-document summary.

For automation we picked Azure Container Apps Jobs over Azure
Functions. Functions on the Consumption plan time out at 10 minutes;
on Premium at 30 minutes. A 2,000-page manual can take 2–4 hours to
pre-analyze. Functions will fail mid-vision, leave half-written
cache, and the next run will not know whether to trust it.
Container Apps Jobs have no execution limit, scale to zero when
idle, and cost only while a job is running.

For embeddings we still use `text-embedding-ada-002` (1,536
dimensions) rather than the newer 3-large family. Ada-002 is in
both Commercial and Government cloud, the cheapest per token of the
embedding family, and our hybrid retrieval (BM25 + vector + semantic
reranker) covers any precision gap a newer model would close. If we
ever need to migrate, the path is straightforward — change
`text_vector.dimensions` in the index, redeploy, reset the indexer.

For vision we use GPT-4.1 because smaller vision models do not
reliably read tiny labels, rotated nameplates, or low-contrast
schematic text — all of which appear constantly in our manuals.
Vision cost is dominated by the number of calls, not the per-call
token price, and we cache every result, so per-PDF vision cost is
paid exactly once.

For authentication we use managed identity end to end. The Function
App, the Search service, and the Container App Job each have their
own system-assigned identity, and every outbound call goes through
AAD. The only secret in the system is the Function App's default
function key, embedded in the skillset URIs at deploy time and
rotated by re-running the deploy script.

---

## 2.3.4 How it works

The first time a PDF appears in the blob container, the following
sequence runs.

Event Grid receives a `BlobCreated` event for the new blob. The
filter on the subscription accepts only `.pdf` files outside the
`_dicache/` prefix, so cache writes by the pre-analyzer don't loop
back into the system. Event Grid drops a small message — really just
the blob name — onto the Storage Queue.

The event-triggered Container App Job is configured with a queue-
length scaler (KEDA), so it spins up a replica when the queue has
messages. The replica picks up the message, looks at the blob, and
checks whether `_dicache/<pdf>.output.json` already exists with a
last-modified timestamp newer than the PDF. If yes, the cache is
fresh and the job skips straight to triggering the indexer. If no
(either the file is new or the cache is stale because the PDF was
overwritten), the job runs `preanalyze.py --only <pdf> --force`.

The pre-analyzer is itself a multi-phase process. It first calls
Document Intelligence on the PDF and writes `<pdf>.di.json`. Then
it walks the figures DI identified, crops each one with PyMuPDF,
hashes the resulting PNG, and writes `<pdf>.crop.<fig>.json` for
each. Then it submits the crops to GPT-4.1 Vision with a structured
prompt that returns category, description, OCR labels, and figure
reference, and writes `<pdf>.vision.<fig>.json` for each. Finally
it assembles all the per-figure and per-table outputs into one
`<pdf>.output.json` — the file whose presence marks the PDF as
fully pre-analyzed.

When the pre-analyzer finishes, the job POSTs to
`/indexers/<name>/run` on the Search service. The indexer wakes up,
sees the new (or updated) blob via the high-water-mark on
`metadata_storage_last_modified`, and sends it through the skillset.

Inside the skillset, built-in `DocumentIntelligenceLayoutSkill` and
`SplitSkill` produce a markdown view of the text and chunk it into
1,200-character pages with 200-character overlap. The custom
`process-document` skill reads `<pdf>.output.json` and emits two
arrays the indexer can iterate over: `enriched_figures` and
`enriched_tables`. Per-figure custom skills (`analyze-diagram`,
`build-semantic-string`) and per-table custom skills (`shape-table`)
shape each into a record. A per-document summary skill produces one
more. Built-in `AzureOpenAIEmbeddingSkill` is invoked four times to
embed each record type into a 1,536-dim vector.

Index projections then flatten the nested skill output. Each text
chunk becomes a record with `record_type="text"`, each figure with
`"diagram"`, each table with `"table"`, and the summary with
`"summary"`. They share one schema and one vector field — at query
time you filter on `record_type` if you want only one kind.

If a PDF is overwritten in blob storage, the same path runs but with
cache invalidation: `preanalyze --only --force` rewrites every cache
file, and the indexer's high-water-mark detection picks up the new
LMT and re-projects every record.

If a PDF is deleted, Event Grid sends `BlobDeleted` and the job
deletes `_dicache/<pdf>.*`. The next indexer run sees the soft-
deleted blob (assuming blob soft-delete is enabled on the storage
account) via `NativeBlobSoftDeleteDeletionDetectionPolicy` and emits
delete operations to the index.

A nightly cron job runs `preanalyze --incremental` and
`preanalyze --cleanup` as a safety net for events Event Grid lost
or PDFs that arrived when the queue was offline. The indexer's own
schedule (`PT1H`) is a third independent backstop.

---

## 2.3.5 Prerequisites

Before any deploy can succeed, the following must be true.

The deploying user is signed into the Azure CLI (`az login`) against
the right tenant and subscription, and they have at least
Contributor on the resource group that holds the Function App,
Search Service Contributor and Search Index Data Contributor on the
Search service, and Owner-equivalent rights to assign managed-
identity roles on the resources the Function App and Search service
will reach.

A region has been chosen with **GPT-4.1 model availability**. This
is the binding constraint on region choice. Embedding deployments
and Document Intelligence are widely available; gpt-4.1 is not.
Verify with `az cognitiveservices account list-models` before you
provision anything.

Quota has been reviewed. Default Standard-S0 deployments cap around
240,000 TPM for ada-002 and 80,000 TPM for gpt-4.1 vision. For the
typical steady-state workload (a handful of new PDFs per day) this
is fine. For a fresh load of 100+ PDFs at once, file a quota
increase one to two weeks in advance — Microsoft will not approve a
large bump on the day you need it.

The storage account that will receive PDFs has, or can have,
**blob soft-delete enabled** with a retention of at least 30 days.
The indexer's deletion-detection policy depends on this. Without
it, deleted PDFs leave orphan records in the index forever.

The upstream SharePoint→blob automation already exists and is
trusted. This document does not own that pipe, but everything
downstream assumes a PDF that lands in the container is genuine,
not corrupted, and not flapping (re-uploaded repeatedly).

A Python 3.11 environment is available for running deploy scripts
and the pre-analyzer, with `pip install -r requirements.txt` already
done.

`jq` is available on the deploying machine (used by the bash deploy
script) and Azure Functions Core Tools v4 (`func`) is installed.

---

## 2.3.6 Dependencies

The system depends on the following Azure services. Their job and
the way the pipeline talks to each is described below.

**Azure Storage** is the source of truth for PDFs and the durable
home for the pre-analyzer cache. The pipeline writes to it via the
pre-analyzer (cache) and reads from it via the indexer (PDFs) and
the Function App (cache). Authentication is managed identity:
`Storage Blob Data Reader` for read paths, `Storage Blob Data
Contributor` for write paths.

**Azure AI Search** hosts the indexer that drives the whole
pipeline, the index that holds the records, the skillset that
defines the per-PDF transform, and the datasource that points at
the storage account. The Search service has a system-assigned
identity that authenticates outbound to AOAI (for the embedding
skill and the query-time vectorizer), to the AI Services multi-
service account (for built-in DI), and to Storage (to read PDFs).

**Azure OpenAI** provides two model deployments that the pipeline
calls: `text-embedding-ada-002` for vector embeddings (called by the
Search service from the embedding skill, and by the pre-analyzer for
post-processing if needed) and `gpt-4.1` for vision (called by the
pre-analyzer for figure descriptions and by the Function App's
summary skill).

**Azure Document Intelligence** does the layout extraction inside
the pre-analyzer. The built-in Layout skill in the indexer also
calls a DI back-end, but billed through the AI Services multi-
service account, not directly.

**Azure AI Services (multi-service)** is the billing endpoint for
the built-in `DocumentIntelligenceLayoutSkill`. It does not host
code or data; it exists so the Search service has something to bill
against when it calls DI on our behalf.

**Azure Function App** hosts the six custom skills as HTTP
endpoints. The Search indexer calls them; they read from the cache
and from AOAI. The Function App's identity has `Cognitive Services
OpenAI User` on AOAI, `Cognitive Services User` on DI (for the rare
fallback path), `Storage Blob Data Reader` on the storage account,
and `Search Index Data Reader` on Search (for the image-hash cache
lookup).

**Azure Container Apps environment** hosts two jobs that automate
the pre-analyzer in production. They are independent of the Function
App and have their own identity with broader rights — Storage Blob
Data Contributor (cache writes), Storage Queue Data Message
Processor (queue drain), Cognitive Services OpenAI User and
Cognitive Services User (the pre-analyzer calls AOAI and DI), and
Search Service Contributor (to POST `/indexers/run`).

**Event Grid + Storage Queue** are plumbing for the event-driven
job. Event Grid is the publisher; the queue is the buffer the job
reads from. KEDA scales the job based on queue depth.

**Application Insights** is the telemetry sink for the Function App.
It is optional in the strictest sense — the pipeline runs without
it — but in practice you cannot operate this in production without
some kind of telemetry, so treat it as required.

The repository itself depends on a small set of Python libraries
(`azure-identity`, `httpx`, `azure-storage-blob`, `pymupdf`,
`azure-functions`) listed in `requirements.txt`. PyMuPDF in
particular is AGPL-3.0; for an internal Function App this is fine,
but if you ever ship this pipeline as part of a public SaaS, swap
to `pypdfium2` or review PyMuPDF's terms.

---

## 2.3.7 Permissions

Permissions in this system come in two layers — what the deploying
human needs, and what each managed identity needs. The runbook
should be read in that order.

**The deploying principal** (the human or service principal running
`scripts/deploy_*.sh`) needs Search Service Contributor and Search
Index Data Contributor on the Search service so they can PUT the
datasource, index, skillset, and indexer; Contributor on the
Function App's resource group so they can apply App Settings and
fetch the function key; and the standard rights to assign roles to
the managed identities listed below.

**The Function App's system-assigned identity** needs Storage Blob
Data Reader on the storage account (to read cache blobs), Cognitive
Services OpenAI User on Azure OpenAI (to call the embedding and
vision endpoints), Cognitive Services User on Document Intelligence
(for the rare live-DI fallback path), and Search Index Data Reader
on the Search service (used by the diagram-skill's image-hash
deduplication, which queries the index).

**The Search service's system-assigned identity** needs Storage Blob
Data Reader on the storage account so the indexer can read PDFs,
Cognitive Services OpenAI User on Azure OpenAI so the embedding
skill and the vectorizer can call ada-002, and Cognitive Services
User on the AI Services multi-service account so the built-in
Layout skill can run.

**The Container App Job's identity** needs Storage Blob Data
Contributor on the storage account (the pre-analyzer writes cache
blobs and may delete cache for deleted PDFs), Storage Queue Data
Message Processor on the queue so the event-triggered job can
dequeue and complete messages, Cognitive Services OpenAI User and
Cognitive Services User for the pre-analyzer's vision and DI calls,
and Search Service Contributor on the Search service so the job can
POST `/indexers/run` after each pre-analysis completes.

The single embedded secret in the system is the Function App's
`default` function key. It is fetched live by the deploy script and
embedded in the skillset's WebApi URIs. Rotate it by running
`az functionapp keys set` followed by `python scripts/deploy_search.py`
— the latter re-PUTs the skillset with the new key, no other code
change required.

A note on role propagation. Azure RBAC takes five to ten minutes to
propagate after a role assignment. The single most common
"why doesn't auth work" cause in this pipeline is a freshly
assigned role that hasn't propagated yet. If something is failing
401 or 403 right after a deploy, wait ten minutes and try again
before debugging anything else.

---

## 2.3.8 The creation process

This is the path from an empty subscription to a working pipeline.
It assumes the prerequisites in §2.3.5 are satisfied.

The first step is to provision the Azure resources from §2.3.2. The
order matters only loosely — the storage account before the search
service (which needs to point at it), AOAI before the search service
(so the vectorizer's deployment ID exists), and the Function App
last because it needs the storage backing account to already be
there. The exact `az` commands are in [RUNBOOK.md §4.3](RUNBOOK.md#43-creation-commands-commercial-cloud).
For an enterprise environment this should be Bicep or Terraform
rather than ad-hoc CLI; treat the `az` commands as a reference for
what each resource looks like, not as the recommended provisioning
mechanism.

The second step is to grant the roles from §2.3.7. Do this once
and let propagation finish before moving on.

The third step is to copy `deploy.config.example.json` to
`deploy.config.json` and fill in every endpoint, deployment name,
storage resource ID, and AI Services subdomain URL. This file is
the single source of truth for every deploy script in the
repository, and it is the only file that changes between dev,
staging, and prod environments. Back it up to KeyVault or an ops
repo — it is the only state in this system that isn't in git.

The fourth step is `python scripts/preflight.py --config
deploy.config.json`. The preflight verifies that every resource in
the config exists, that the deploying principal has the right
roles, and that the role grants from §2.3.7 are in place. Fix
anything it complains about before continuing.

The fifth step is to deploy the Function App code:
`scripts/deploy_function.sh deploy.config.json`. This publishes the
Python package and applies App Settings (`AUTH_MODE=mi`, the AOAI
and DI endpoints, the Search endpoint and index name, the skill
version stamp, and the App Insights connection string).

The sixth step is to deploy the Search artifacts:
`python scripts/deploy_search.py --config deploy.config.json`. This
renders every `<PLACEHOLDER>` in `search/datasource.json`,
`index.json`, `skillset.json`, and `indexer.json` from the config
plus a live-fetched function key, and PUTs them via AAD. The script
is idempotent and fails loud if any placeholder is unrendered.

The seventh step, before the indexer can succeed, is to pre-analyze
every PDF already in the container:
`python scripts/preanalyze.py --config deploy.config.json` runs all
phases sequentially. For a large initial load split into phases —
DI first with `--phase di --concurrency 3`, then vision with
`--phase vision --vision-parallel 40`, then `--phase output` — so
each phase is restartable on its own.

The eighth step is to run the indexer and validate the result:
`python scripts/deploy_search.py --config deploy.config.json
--run-indexer` triggers the run, then
`python scripts/smoke_test.py --config deploy.config.json` waits
for `status=success` and asserts record counts, required fields,
and that `physical_pdf_pages` covers the declared start and end on
text and table records.

The ninth step is to stand up production automation: the two
Container App Jobs, the Event Grid system topic and subscriptions,
and the Storage Queue. This is described in §2.3.9.

---

## 2.3.9 Operations

Once the pipeline is running, day-2 operations split into three
concerns: keeping the automation correct, responding when things
break, and evolving the system.

**Production automation.** The target shape is the one in §2.3.4 —
events drive the fast path, a nightly cron is the safety net, and
the indexer's own schedule is a third backstop. There is one
correctness gap to close before automation can be trusted: the
pre-analyzer's `--incremental` mode skips a re-uploaded PDF because
its `output.json` cache file still exists, so the indexer reads
stale cache and serves stale content. The fix is roughly 100 lines
in `preanalyze.py`: add an `--only <blob-name>` flag, make
`--incremental` compare the PDF's last-modified timestamp against
the cache's and re-run if the PDF is newer, and add a per-PDF
cleanup helper that the event worker can call on `BlobDeleted`.
Deploy this first; deploy automation on top of it.

A two-phase rollout works well in practice. Phase 1 is the cron
half only — one Container App Job on a 30-minute cron running
`preanalyze --incremental && preanalyze --cleanup && POST
/indexers/run`. With LMT-aware invalidation in place, this is
correct for add, update, and delete with up to 30 minutes of lag,
and most teams stop here. Phase 2 adds the event-driven path in
front for sub-15-minute lag; the cron job stays as the nightly
reconciliation. Phase 2 is additive — there is no rework of Phase 1.

**Steady-state tasks.** Re-indexing one file is a matter of
rewriting the blob (its new last-modified timestamp triggers the
indexer's high-water-mark). A full re-index needs a `POST
/indexers/<name>/reset` followed by a run trigger. Rotating the
function key means `az functionapp keys set` followed by
re-running `deploy_search.py` so the skillset gets the new key.
Bumping `skillVersion` in the config and re-running the function
deploy stamps every newly processed record with the new version,
which is useful when the image-hash cache needs invalidating.
Clearing the index without losing the schema means deleting the
index, redeploying via the deploy script, and resetting plus
running the indexer.

**When something breaks.** The most common production failures and
their resolutions live in [RUNBOOK.md §17](RUNBOOK.md#17-anticipated-failure-modes-and-runbooks).
The shortest summary is this: most issues are either a propagation
delay on a freshly assigned role (wait ten minutes), a missing
pre-analyzer cache (the skill is timing out at 230 seconds because
it's falling back to live DI — re-run pre-analyze for that PDF),
or a stale skillset after a function-key rotation (re-run the
search deploy script). AOAI 429s during initial loads mean the
vision parallelism is set higher than the deployment's TPM ceiling
and want a quota increase or a lower `--vision-parallel`.
Content-filter false-positives on figures (e.g. a small red
warning triangle on a nameplate triggering a violence filter) are
cached as permanent failures; the figure still indexes with its
page, headers, and bounding box, just without the description, and
manual review is only justified if a critical figure is missed.

**Observability.** Three signals matter: indexer execution status
(in the portal under Search → Indexers → Execution history),
Function App 5xx rate and per-skill latency (in App Insights), and
queue depth on `pdf-events` (a non-zero depth that doesn't drain is
the early warning that the event worker is wedged or scaled to
zero). Wire alerts on the indexer failing three runs in a row, on
DLQ messages appearing, and on AOAI 429 rate above five percent.

**Cost.** Per fresh PDF (~500 pages, ~1,500 figures), expect
roughly $11–18, dominated by Document Intelligence and GPT-4.1
vision. Re-indexing the same PDF after a schema change costs about
$0.10 because the cache absorbs both the DI and vision cost. The
cost levers in priority order are: don't break the cache; bump TPM
quota rather than spinning a second deployment; use Standard_LRS
storage rather than GRS; once events are live, drop the indexer
schedule from `PT15M` to `PT1H` to eliminate empty runs.

**Disaster recovery.** Nothing in this system is irrecoverable. The
PDFs are the source of truth; if everything else burns down, you
provision a fresh resource group, restore the PDFs (via the
upstream SharePoint→blob automation), re-run §2.3.8 from step one,
and you are back. The only file that lives outside git and outside
the cloud is `deploy.config.json` — back it up to KeyVault or an
ops repo. There is no active-active configuration; if the primary
region fails, provision in the secondary region per §2.3.8 and
accept the half-day rebuild.

---

## 2.3.10 When manual operation is justified

The automation is the contract — it is what the team trusts to keep
the index in sync with the blob. Manual operation should be rare
and well-scoped. Three legitimate cases:

The first is a large initial load. Fifty or more PDFs arriving at
once will work through the event-driven job, but a workstation
running `preanalyze.py` directly with a high `--vision-parallel`
will finish faster because it can briefly exceed the queue worker's
replica cap. Run the workstation, then enable the indexer.

The second is a controlled `skillVersion` rollout. Bumping the
version forces every record to get re-stamped on next touch, which
is useful when the cache logic itself has changed. Run
`preanalyze.py --force` on a small subset, validate retrieval
quality, and only then enable the bump for the rest.

The third is suspected upstream contamination — e.g. the
SharePoint→blob automation re-uploaded a batch of files with the
wrong content. Run `preanalyze.py --force` on the affected PDFs,
trigger the indexer, and verify before re-enabling the event path.

Outside these cases, route everything through automation.

---

## 2.3.11 Pointers

For the deep reference — every command, every schema field, every
failure walkthrough — see:

- [RUNBOOK.md](RUNBOOK.md) — full operator + infrastructure runbook
- [ARCHITECTURE.md](ARCHITECTURE.md) — design rationale deep-dive
- [SEARCH_INDEX_GUIDE.md](SEARCH_INDEX_GUIDE.md) — index concepts
  for non-search engineers
- `search/*.json` — actual artifact bodies
- `scripts/preanalyze.py` — pre-analyzer reference
