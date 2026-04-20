# Pre-analyze — team runbook

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
