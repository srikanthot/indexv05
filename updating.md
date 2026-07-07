Hi Copilot — the 404 is fixed and all 8 functions are registered, and the GPT-5.1
temperature fix is deployed. Now let's PROVE the temperature fix works end-to-end
by running the precompute step (`scripts/preanalyze.py`) on a SMALL test set of
**3 PDFs only** — do NOT run the whole corpus.

The key thing we're checking: with the temperature fix, the GPT-5.1 vision calls
should now return **zero HTTP 400 errors**, and `output.json` should build with
figures/tables. (Before the fix, every vision call was 400-ing.)

Do NOT edit any code. Run the steps below and print the REPORT block. We are on
Azure US-Gov; set the SSL cert env you normally use before running. If a command
errors, stop and tell me the exact error.

---

### Steps

```bash
# 0. Environment (use your usual cert path)
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
export AOAI_REASONING_EFFORT=low   # keeps GPT-5.1 latency low so calls don't time out

# 1. Show preanalyze's flags so we target only specific PDFs (look for a per-PDF / limit flag)
python scripts/preanalyze.py --help

# 2. List PDFs in the source container with sizes; we'll pick 3 (ideally 1 small + 2 larger ~30-40MB)
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
acct=c["storage"]["accountResourceId"].rstrip("/").split("/")[-1]
cont=c["storage"]["pdfContainerName"]
print("STORAGE_ACCOUNT=",acct)
print("CONTAINER=",cont)
PY
# then (substitute the account/container printed above):
az storage blob list --account-name <STORAGE_ACCOUNT> --container-name <CONTAINER> --auth-mode login --query "[?ends_with(name,'.pdf')].{name:name, mb: to_number(properties.contentLength)}" -o tsv | awk -F'\t' '{printf "%.1fMB\t%s\n", $2/1048576, $1}' | sort -n | head -20
```

Pick **3** PDFs from that list (one small, and if available two around 30-40MB).
Then run preanalyze on **only those 3**, all phases (di → vision → output), using
the per-PDF targeting flag you found in step 1's `--help` (it is likely `--pdf`,
`--only`, or similar — use whatever the help shows). For example, per PDF:

```bash
python scripts/preanalyze.py --config deploy.config.json --phase all <PER_PDF_FLAG> "<pdf_name_1>"
python scripts/preanalyze.py --config deploy.config.json --phase all <PER_PDF_FLAG> "<pdf_name_2>"
python scripts/preanalyze.py --config deploy.config.json --phase all <PER_PDF_FLAG> "<pdf_name_3>"
```

If `--help` shows **no** per-PDF flag, STOP and paste me the `--help` output so I
give you the exact command (do not run preanalyze on the whole container).

After each run, inspect what it printed (it reports vision coverage:
total / present / errored / missing per PDF) and whether it wrote `output.json`.

### Print this REPORT block

```
PREANALYZE_FLAGS: <paste the per-PDF/limit-related flags from --help>
TEST_PDFS: <the 3 names + sizes you used>
PDF_1: DI=<ok/fail>  VISION total/present/errored/missing=<n/n/n/n>  OUTPUT_JSON=<written/no>
PDF_2: DI=<ok/fail>  VISION total/present/errored/missing=<n/n/n/n>  OUTPUT_JSON=<written/no>
PDF_3: DI=<ok/fail>  VISION total/present/errored/missing=<n/n/n/n>  OUTPUT_JSON=<written/no>
ANY_HTTP_400_OR_TEMPERATURE_ERROR: <yes/no>   <-- this is the key result; should be NO now
OTHER_ERRORS: <paste any errors, or none>
```

If anything errors, stop and give me the error. Otherwise run everything and give me the REPORT block.
