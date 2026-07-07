Hey Copilot,

The code fixes for this round are **already applied** in this repo — 4 files were
updated by me: `function_app/shared/config.py`, `function_app/shared/diagram.py`,
`function_app/shared/summary.py`, `scripts/preanalyze.py`. They remove a
GPT-5.1 blocker (the model is a reasoning model and returns HTTP 400 for any
`temperature != 1`, so the hardcoded temperatures were making every vision/summary
call fail) and fix a compile error in `preanalyze.py`.

**Do NOT edit any code.** The files are already correct. Just run the commands
below and print the REPORT block at the end. If a command errors, stop and tell
me the error. We are on Azure US-Gov and `az` is already logged in.

---

### Run these and capture ALL output

```bash
# 1. Confirm the already-applied edits compile
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
MODEL_PROVIDER: <value>   FOUNDRY set?: <yes/no>   AOAI_ENDPOINT: <value>
FUNC_PRINCIPAL: <principalId>   ROLE_ASSIGNED_ON_FOUNDRY: <yes/no>
PUBLISH: <success / the error tail>
FUNCTION_LIST (count + names): <paste the tsv>
```

If anything errors, stop and tell me the error. Otherwise run everything above and give me the REPORT block output.
