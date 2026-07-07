Hi Copilot — the pipeline is validated end-to-end on 3 docs. Now we're scaling to
the 12 documents needed for performance testing (these are bigger: ~40-90MB). This
round: confirm all 12 are in the container, report the model TPM quotas (we saw a
429 earlier, so we may need to bump ada-002), and run preanalyze on all of them.

No code edits. Run the steps and print the REPORT block. SSL env as usual; US Gov.
NOTE: preanalyze on 9 big new docs (the 3 small ones are already done and will be
skipped by --incremental) may take 1-3 hours — let it run to completion; it is
resumable if interrupted. If you must, run it in the background and report progress
via `python scripts/preanalyze.py --config deploy.config.json --status`.

---

### Steps

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low
az cloud set --name AzureUSGovernment
az account set --subscription 5c58d830-b35f-458a-ab5d-65ad9d0b9815

# 1. Confirm all 12 PDFs are in the container (upload the missing ones first if fewer than 12)
SA=$(python -c "import json;print(json.load(open('deploy.config.json'))['storage']['accountResourceId'].rstrip('/').split('/')[-1])")
CONT=$(python -c "import json;print(json.load(open('deploy.config.json'))['storage']['pdfContainerName'])")
echo "STORAGE=$SA CONTAINER=$CONT"
az storage blob list --account-name "$SA" --container-name "$CONT" --auth-mode login --num-results 5000 \
  --query "[?ends_with(name,'.pdf')].{mb: to_number(properties.contentLength), name:name}" -o tsv \
  | awk -F'\t' '{tot+=$1;n++; printf "%.1fMB\t%s\n",$1/1048576,$2} END{printf "TOTAL_PDFS=%d TOTAL_MB=%.0f\n",n,tot/1048576}' | sort -rn

# 2. Report the model deployment TPM/capacity (so we know if ada-002 needs a bump)
az cognitiveservices account deployment list -n psegtmfdryuatv01 -g psegtmrguatv01 \
  --query "[].{name:name, model:properties.model.name, sku:sku.name, capacity_k_tpm:sku.capacity}" -o table

# 3. Run preanalyze on all container docs (all phases; skips the 3 already done).
#    This is the long step. Let it finish. It prints per-PDF vision coverage.
python scripts/preanalyze.py --config deploy.config.json --phase all --incremental --concurrency 3 --vision-parallel 20

# 4. After it finishes, show the status summary
python scripts/preanalyze.py --config deploy.config.json --status
```

### Print this REPORT block

```
CONTAINER: TOTAL_PDFS=<n> TOTAL_MB=<n>   (list the 12 names + sizes)
TPM_QUOTAS: ada-002 capacity=<k TPM>   gpt-5.1 capacity=<k TPM>
PREANALYZE per-PDF (name: DI ok?, vision total/present/errored/missing, output.json written?):
  <one line per PDF>
ANY_HTTP_400_OR_TEMPERATURE_ERROR: <yes/no>
RATE_LIMIT_429_COUNT: <how many times did you see "rate limited" / 429 during preanalyze>
OTHER_ERRORS: <paste any, or none>
PREANALYZE_STATUS_SUMMARY: <paste the --status output>
```

If fewer than 12 PDFs are in the container, tell me and upload the missing ones
before running step 3. If preanalyze is heavily rate-limited (many 429s), report
that — we'll bump the ada-002 TPM before continuing. Otherwise let it finish and
give me the REPORT.
