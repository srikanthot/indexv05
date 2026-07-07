# Indexer recovery — work log & Copilot scripts

Copy the **SCRIPT** block below into your office-laptop Copilot. It applies the
code changes and runs the verification. After it runs, paste the **REPORT
block** output back to the assistant.

---

## Round 1 — Phase 0: GPT-5.1 temperature fix + preanalyze compile fix

### Files changed this round (4)
- `function_app/shared/config.py` — **added** function `model_gen_kwargs(...)` (reasoning-model-safe generation params; omits `temperature` by default).
- `function_app/shared/diagram.py` — `_call_vision`: removed `temperature=0.0` + `max_completion_tokens=1500`, now uses `**model_gen_kwargs(4000)`; added import.
- `function_app/shared/summary.py` — chat call: removed `temperature=0.1` + `max_completion_tokens=900`, now uses `**model_gen_kwargs(2000)`; added import.
- `scripts/preanalyze.py` — `_call_vision_api`: removed hardcoded temperature, added env-driven params; **fixed an IndentationError** (5-space docstring in `_do_crops` → 4 spaces) that stopped the file from compiling.

Why: GPT-5.1 is a reasoning model and returns **HTTP 400 for any `temperature != 1`**, so every vision/summary call was failing. The preanalyze IndentationError meant that file may not have run at all (which alone would drop all figures/tables).

---

## SCRIPT 1 — paste this whole block to Copilot

Hi Copilot. Apply the code edits below, then run the commands, then print the
REPORT block at the end. We're on Azure US-Gov; `az` is logged in. Make exactly
these changes — do not refactor anything else.

**EDIT 1 — `function_app/shared/config.py`** — append this function to the END of the file:

```python


def model_gen_kwargs(default_max_tokens: int) -> dict:
    """Generation kwargs SAFE for GPT-5.1 / reasoning models.

    GPT-5.x reasoning models REJECT any temperature != 1 (HTTP 400). So we
    omit temperature by default. Operators on a classic model can set
    AOAI_TEMPERATURE. max_completion_tokens gets headroom because reasoning
    tokens draw from it. reasoning_effort is opt-in via env.
    """
    kwargs: dict = {}
    raw_max = optional_env("AOAI_MAX_COMPLETION_TOKENS", "")
    try:
        kwargs["max_completion_tokens"] = int(raw_max) if raw_max else default_max_tokens
    except ValueError:
        kwargs["max_completion_tokens"] = default_max_tokens
    temp = optional_env("AOAI_TEMPERATURE", "")
    if temp != "":
        try:
            kwargs["temperature"] = float(temp)
        except ValueError:
            pass
    effort = optional_env("AOAI_REASONING_EFFORT", "")
    if effort:
        kwargs["reasoning_effort"] = effort
    return kwargs
```

**EDIT 2 — `function_app/shared/diagram.py`** — in `_call_vision`, add the import and replace the temperature lines.

Find:
```python
def _call_vision(image_b64: str, user_text: str) -> dict[str, Any]:
    client = get_client()
```
Replace with:
```python
def _call_vision(image_b64: str, user_text: str) -> dict[str, Any]:
    from .config import model_gen_kwargs
    client = get_client()
```
Then find:
```python
        temperature=0.0,
        max_completion_tokens=1500,
        response_format={"type": "json_object"},
```
Replace with:
```python
        response_format={"type": "json_object"},
        **model_gen_kwargs(4000),
```

**EDIT 3 — `function_app/shared/summary.py`** — add the import and replace the temperature lines.

Find:
```python
    try:
        client = get_client()
```
Replace with:
```python
    try:
        from .config import model_gen_kwargs
        client = get_client()
```
Then find:
```python
            temperature=0.1,
            max_completion_tokens=900,
```
Replace with:
```python
            **model_gen_kwargs(2000),
```

**EDIT 4 — `scripts/preanalyze.py`** — in `_call_vision_api`, replace the body temperature block.

Find:
```python
        "temperature": 0.0,
        "max_completion_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    if provider == "foundry":
        body["model"] = deployment
```
Replace with:
```python
        "response_format": {"type": "json_object"},
    }
    _raw_max = os.environ.get("AOAI_MAX_COMPLETION_TOKENS", "").strip()
    body["max_completion_tokens"] = int(_raw_max) if _raw_max.isdigit() else 4000
    _temp = os.environ.get("AOAI_TEMPERATURE", "").strip()
    if _temp:
        try:
            body["temperature"] = float(_temp)
        except ValueError:
            pass
    _effort = os.environ.get("AOAI_REASONING_EFFORT", "").strip()
    if _effort:
        body["reasoning_effort"] = _effort
    if provider == "foundry":
        body["model"] = deployment
```

**EDIT 5 — `scripts/preanalyze.py`** — fix a docstring indentation bug in `_do_crops`.
Find the function `def _do_crops(`. The line right after `... elapsed: float) -> str:` is the docstring `"""Shared crop + parallel-upload body...`. Make sure it has exactly **4** leading spaces. If it currently has 5, change it to 4. (If already 4, skip.)

**Now run these and capture ALL output:**

```bash
# 1. Verify the edits compile
python -m py_compile function_app/shared/config.py function_app/shared/diagram.py function_app/shared/summary.py scripts/preanalyze.py && echo "COMPILE_OK"

# 2. Read resource names from deploy.config.json
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
print("FUNC_APP=",c["functionApp"]["name"])
print("RG=",c["functionApp"]["resourceGroup"])
print("MODEL_PROVIDER=",c.get("modelProvider"))
print("FOUNDRY=",c.get("foundry"))
print("AOAI_ENDPOINT=",c.get("azureOpenAI",{}).get("endpoint"))
PY
```

Then, using FUNC_APP and RG from step 2:

```bash
# 3. Set reasoning-effort + show current model settings on the app
az functionapp config appsettings set -g <RG> -n <FUNC_APP> --settings AOAI_REASONING_EFFORT=low --output none
az functionapp config appsettings list -g <RG> -n <FUNC_APP> --query "[?contains(name,'FOUNDRY')||name=='MODEL_PROVIDER'||contains(name,'AOAI')].{name:name,value:value}" -o table

# 4. Redeploy the function app WITH a real remote build (also fixes the 404 if functions were not registered)
az functionapp config appsettings set -g <RG> -n <FUNC_APP> --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true --output none
cd function_app && func azure functionapp publish <FUNC_APP> --python --build remote; cd ..

# 5. THE 404 CHECK — must list 7 functions
az functionapp function list -g <RG> -n <FUNC_APP> --query "[].name" -o tsv
```

**Print this REPORT block (fill in the values):**

```
COMPILE: <COMPILE_OK or the error>
EDITS_APPLIED: <yes/no; note any find-string that was not found>
MODEL_PROVIDER: <value>   FOUNDRY set?: <yes/no>   AOAI_ENDPOINT: <value>
PUBLISH: <success / the error tail>
FUNCTION_LIST (count + names): <paste the tsv>
```

---

## Also do manually (portal/RBAC — one click, not a script)
Grant the **Function App's managed identity** the role **`Cognitive Services OpenAI User`** on the **Foundry** resource. Without it, GPT-5.1 calls will 401/403 even after the 404 clears.

## Next
After you paste the REPORT block back, you'll get **SCRIPT 2** (run preanalyze on
3 docs → run the indexer → verify figures/tables/vectors landed).
