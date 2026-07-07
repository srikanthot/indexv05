Hey Copilot,

I worked with an AI architect to diagnose why our Azure AI Search indexer was failing (Web API 404, and figures/tables getting dropped from the index). Root causes found:

1. Our chat/vision model is **GPT-5.1, a reasoning model** — it returns **HTTP 400 for any `temperature != 1`**. Our code hardcoded `temperature=0.0/0.1`, so every vision and summary call was failing.
2. `scripts/preanalyze.py` had an **IndentationError** (a 5-space docstring in `_do_crops`) that could stop the file from running at all — which by itself drops every figure/table.
3. The **Web API 404** most likely means the function app published but registered **zero functions** (empty remote build), so the indexer's skill calls 404.

Below is what I want you to implement. Apply the edits exactly — do not refactor anything else. If any "Find" block does not match the current file, **stop and tell me which one** instead of guessing. Otherwise apply all edits, run the commands, and print the REPORT block at the end. We are on Azure US-Gov and `az` is already logged in.

Files to change: `function_app/shared/config.py`, `function_app/shared/diagram.py`, `function_app/shared/summary.py`, `scripts/preanalyze.py`.

---

### EDIT 1 — `function_app/shared/config.py`
Append this function to the END of the file:

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

### EDIT 2 — `function_app/shared/diagram.py`
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

### EDIT 3 — `function_app/shared/summary.py`
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

### EDIT 4 — `scripts/preanalyze.py`
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

### EDIT 5 — `scripts/preanalyze.py`
In the function `def _do_crops(...)`, the docstring line right after `... elapsed: float) -> str:` is `"""Shared crop + parallel-upload body...`. Make sure it has exactly **4** leading spaces. If it has 5, change it to 4. If it is already 4, skip.

---

### Now run these and capture ALL output

```bash
# 1. Verify the edits compile
python -m py_compile function_app/shared/config.py function_app/shared/diagram.py function_app/shared/summary.py scripts/preanalyze.py && echo "COMPILE_OK"

# 2. Read resource names from deploy.config.json
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
print("FUNC_APP=", c["functionApp"]["name"])
print("RG=", c["functionApp"]["resourceGroup"])
print("MODEL_PROVIDER=", c.get("modelProvider"))
print("FOUNDRY=", c.get("foundry"))
print("AOAI_ENDPOINT=", c.get("azureOpenAI", {}).get("endpoint"))
PY
```

Using FUNC_APP and RG from step 2, substitute them below:

```bash
# 3. Set reasoning-effort low (cuts latency so calls don't time out) + show model settings
az functionapp config appsettings set -g <RG> -n <FUNC_APP> --settings AOAI_REASONING_EFFORT=low --output none
az functionapp config appsettings list -g <RG> -n <FUNC_APP> --query "[?contains(name,'FOUNDRY')||name=='MODEL_PROVIDER'||contains(name,'AOAI')].{name:name,value:value}" -o table

# 4. Grant the Function App managed identity access to the Foundry resource (GPT-5.1).
#    Get the principal, list Cognitive Services accounts, then assign the role on the FOUNDRY one.
az functionapp identity show -g <RG> -n <FUNC_APP> --query principalId -o tsv
az resource list --resource-type Microsoft.CognitiveServices/accounts --query "[].{name:name, id:id}" -o table
#    -> pick the Foundry resource id from that list and run:
# az role assignment create --assignee <FUNC_PRINCIPAL_ID> --role "Cognitive Services OpenAI User" --scope <FOUNDRY_RESOURCE_ID>

# 5. Redeploy the function app WITH a real remote build (this also fixes the 404 if functions were not registered)
az functionapp config appsettings set -g <RG> -n <FUNC_APP> --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true --output none
cd function_app && func azure functionapp publish <FUNC_APP> --python --build remote; cd ..

# 6. THE 404 CHECK — must list 7 functions
az functionapp function list -g <RG> -n <FUNC_APP> --query "[].name" -o tsv
```

### Print this REPORT block (fill in the values)

```
COMPILE: <COMPILE_OK or the error>
EDITS_APPLIED: <yes/no; name any Find block that did not match>
MODEL_PROVIDER: <value>   FOUNDRY set?: <yes/no>   AOAI_ENDPOINT: <value>
FUNC_PRINCIPAL: <principalId>   ROLE_ASSIGNED_ON_FOUNDRY: <yes/no>
PUBLISH: <success / the error tail>
FUNCTION_LIST (count + names): <paste the tsv>
```

If anything errors, stop and tell me the error. Otherwise implement everything above and give me the REPORT block output.
