# Bootstrap — copy-paste recipe for a fresh environment

End-to-end commands to take an environment from "Bicep just finished
provisioning" to "indexer is running and serving search results".
Every command is copy-paste ready. Where you have to fill in a value,
look for the inline `# fill in:` comment.

Total time: ~10 minutes of typing + 10 minutes of waiting + however
long preanalyze takes for your PDFs.

> **Prerequisites already done before you start this:**
> - Bicep template deployed; resources exist
> - Function App and Search service have system-assigned MI **enabled**
> - You ran `git clone` of this repo and `cd`-ed into it
> - `az login` already done
> - `deploy.config.json` already exists (copied from
>   `deploy.config.example.json` and filled in)

---

## Step 1 — Sync the repo to latest

```bash
git pull origin main
```

After this, `scripts/assign_roles.sh` and `scripts/assign_roles.ps1`
should be present.

---

## Step 2 — Confirm Azure context

```bash
az account show --query "{cloud:environmentName, sub:name}" -o table
```

Check the output:
- `cloud` should be `AzureCloud` (commercial) or `AzureUSGovernment` (gov)
- `sub` should be the subscription that holds your resources

If the subscription is wrong:

```bash
az account set --subscription "<your-subscription>"   # fill in: subscription name or id
```

---

## Step 3 — List your resources to find their names

```bash
az resource list -g <your-rg> --query "[].{name:name, type:type}" -o table   # fill in: <your-rg>
```

You're looking for up to 7 names. Five are obvious from the type column;
the Cognitive Services accounts need one more query to tell them apart
by `kind`:

```bash
az cognitiveservices account list -g <your-rg> --query "[].{name:name, kind:kind}" -o table   # fill in: <your-rg>
```

Map the output:

| `kind` value | Means this is your |
|---|---|
| `OpenAI` | **AOAI** (must be a separate resource — AOAI deployments do not live in multi-service accounts) |
| `FormRecognizer` | Standalone **Document Intelligence** |
| `CognitiveServices` | **Azure AI multi-service** account |

Three valid layouts you might see in your environment:

| Layout | What you have | What to use for DI / AISVC |
|---|---|---|
| **Two separate accounts** | one `FormRecognizer` + one `CognitiveServices` | `DI` = the FormRecognizer name, `AISVC` = the CognitiveServices name |
| **One multi-service account** (common in **GCC High**, Azure Gov, and many enterprise environments) | one `CognitiveServices` only — DI is bundled inside it | `DI` and `AISVC` are **the same name** — the multi-service account |
| **Standalone DI only** (rare) | one `FormRecognizer` only | Provision a multi-service `CognitiveServices` account too — the built-in Layout skill needs it for billing |

Write down all the names you'll need (5 to 7 depending on layout). You'll paste them in the next step.

### GCC High / Gov Cloud — verify your AOAI model availability

GCC High lags Azure Commercial by 12–18 months on new models.
**Before you continue, confirm `gpt-4.1` is actually available in your
AOAI resource:**

```bash
az cognitiveservices account list-models \
  -n <your-aoai> -g <your-rg> \
  --query "[?contains(name, 'gpt-4') || contains(name, 'embedding')].{name:name, version:version}" \
  -o table   # fill in: <your-aoai>, <your-rg>
```

If `gpt-4.1` doesn't appear, use whatever vision-capable model is
available (typically `gpt-4o` or `gpt-4-turbo` in Gov), and update
`deploy.config.json` so `azureOpenAI.chatDeployment` and
`azureOpenAI.visionDeployment` reference the deployment name you
actually created.

---

## Step 4 — Edit `scripts/assign_roles.sh`

Open the file in your editor. The top has this block:

```bash
# ---------- FILL THESE IN ----------
RG="<your-rg>"                            # fill in: resource group name
SEARCH="<search-service-name>"            # fill in: Microsoft.Search/searchServices
STORAGE="<storage-account-name>"          # fill in: Microsoft.Storage/storageAccounts
AOAI="<aoai-resource-name>"               # fill in: cognitiveservices kind=OpenAI

# DI + AISVC: if you have a standalone FormRecognizer AND a separate
# multi-service CognitiveServices account, fill in those two names.
# If you have only ONE multi-service CognitiveServices account (common
# in GCC High / Gov Cloud), set DI and AISVC to the SAME name.
DI="<di-or-multi-service-account-name>"
AISVC="<ai-services-multi-service-account-name>"

FUNC="<function-app-name>"                # fill in: Microsoft.Web/sites
# -----------------------------------
```

Replace each `<...>` with the actual name from Step 3. Don't change
anything below the dashed line. Save the file.

(PowerShell users: edit `scripts/assign_roles.ps1` instead. Same 7
variables, same placeholders.)

---

## Step 5 — Run the role assignments

```bash
bash scripts/assign_roles.sh
```

Or PowerShell:

```powershell
.\scripts\assign_roles.ps1
```

Expected output:

```
Looking up principal IDs and resource IDs...

A. Granting your user (deploying principal) roles...
  -> Search Service Contributor
  -> Search Index Data Contributor
  -> Storage Blob Data Contributor
  -> Cognitive Services OpenAI User
  -> Cognitive Services User

B. Granting Search service MI roles...
  -> Storage Blob Data Reader
  -> Cognitive Services OpenAI User
  -> Cognitive Services User

C. Granting Function App MI roles...
  -> Storage Blob Data Reader
  -> Cognitive Services OpenAI User
  -> Cognitive Services User
  -> Search Index Data Reader

All 12 role assignments submitted.
Wait 10 minutes for RBAC propagation before running deploy_search.py.
```

If you see `is still the placeholder` — you forgot to edit the names
at the top. Fix and re-run; the script is idempotent.

If you see `missing a system-assigned identity` — run the two `az`
commands the script prints, then re-run.

---

## Step 6 — Wait 10 minutes

Set a timer. RBAC propagation is 5–10 minutes; running early gives
you 403 even though the assignments are correct.

---

## Step 7 — Deploy the Function App code

If you haven't already done this earlier:

```bash
bash scripts/deploy_function.sh deploy.config.json
```

Or PowerShell:

```powershell
.\scripts\deploy_function.ps1 -Config .\deploy.config.json
```

This publishes the Python package and applies App Settings on the
Function App. Skip this step if you already ran it.

---

## Step 8 — Deploy search artifacts (datasource, index, skillset, indexer)

```bash
python scripts/deploy_search.py --config deploy.config.json
```

This is the step that previously gave you 403. With the role grants
from Step 5 in place and the 10-minute wait done, it should now print
four `ok` lines for `datasources/...`, `indexes/...`,
`skillsets/...`, `indexers/...`.

---

## Step 9 — Pre-analyze every PDF in the container

```bash
python scripts/preanalyze.py --config deploy.config.json
```

This is the long-running step. For a typical 500-page manual it takes
30–60 minutes. For an initial load of many PDFs, run phased:

```bash
python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 40
python scripts/preanalyze.py --config deploy.config.json --phase output
```

Cache lands in the same blob container under `_dicache/`.

---

## Step 10 — Trigger the indexer

```bash
python scripts/deploy_search.py --config deploy.config.json --run-indexer
```

The indexer will pick up every PDF, run the skillset over it, and
write records into the index. For a single 500-page manual this
takes ~10 minutes after preanalyze (because the cache makes the
custom skills fast).

---

## Step 11 — Validate

```bash
python scripts/smoke_test.py --config deploy.config.json
```

This waits for `status=success` on the indexer, then asserts record
counts, required fields, and that `physical_pdf_pages` covers the
declared start+end on text/table records. Non-zero exit on any
failure.

Optional sanity check on what's actually in the index:

```bash
python scripts/check_index.py --config deploy.config.json
```

Shows total docs, per-`record_type` breakdown, and flags any field
that's null on 100% of records (a schema/skillset drift signal).

---

## Done

If Steps 1–11 all succeeded, the index is live and queryable. The
indexer's schedule (`PT15M` by default) will keep picking up new
PDFs from now on.

For the production automation that handles add / update / delete
without manual intervention, see [RUNBOOK.md §16](RUNBOOK.md#16-production-automation--add--update--delete).

For failure-mode walkthroughs if any step misbehaved, see
[RUNBOOK.md §17](RUNBOOK.md#17-anticipated-failure-modes-and-runbooks).
