Hi Copilot — deploy_search failed because the index's vectorizer `aoai-vectorizer`
needs a classic Azure OpenAI endpoint (`*.openai.azure.us`), but our config points
elsewhere. Before I fix it, I need to see the actual endpoints and how embeddings
are wired (we added a custom `build_embedding` function, so the setup may have
changed). This round is READ-ONLY — do NOT edit any code or deploy anything. Just
run these and print the REPORT block. Set your usual SSL env; we're on US Gov.

---

### Run these (read-only)

```bash
export SSL_CERT_FILE='C:/Users/C90255306/Downloads/combined-ca.crt'
export REQUESTS_CA_BUNDLE='C:/Users/C90255306/Downloads/combined-ca.crt'
az cloud set --name AzureUSGovernment

# 1. What the config currently uses
python - <<'PY'
import json
c=json.load(open("deploy.config.json"))
ao=c.get("azureOpenAI",{})
print("azureOpenAI.endpoint     =", ao.get("endpoint"))
print("azureOpenAI.embedDeployment =", ao.get("embedDeployment"))
print("azureOpenAI.chatDeployment  =", ao.get("chatDeployment"))
print("azureOpenAI.visionDeployment=", ao.get("visionDeployment"))
print("modelProvider =", c.get("modelProvider"))
print("foundry =", c.get("foundry"))
print("aiServices =", c.get("aiServices"))
PY

# 2. All Cognitive Services / OpenAI / Foundry accounts (name, kind, endpoint)
az cognitiveservices account list --query "[].{name:name, kind:kind, endpoint:properties.endpoint, rg:resourceGroup}" -o table

# 3. Full endpoint map for the Foundry resource — look for an 'openai.azure.us' URL in here
az cognitiveservices account show -n psegtmfdryuatv01 -g psegtmrguatv01 --query "properties.endpoints" -o json

# 4. What models are deployed on the Foundry resource (is an embedding model / ada-002 here?)
az cognitiveservices account deployment list -n psegtmfdryuatv01 -g psegtmrguatv01 --query "[].{name:name, model:properties.model.name, version:properties.model.version}" -o table

# 5. If there is a SEPARATE Azure OpenAI (kind=OpenAI) resource, list its deployments too.
#    (Use the name from the step-2 table where kind is 'OpenAI'; skip if none.)
# az cognitiveservices account deployment list -n <OPENAI_RESOURCE> -g <RG> --query "[].{name:name, model:properties.model.name}" -o table

# 6. How does the skillset embed — built-in AzureOpenAIEmbeddingSkill, or the custom build_embedding skill?
grep -nE "AzureOpenAIEmbedding|build-embedding|build_embedding|resourceUri|deploymentId" search/skillset.json | head -40

# 7. The index vectorizer block (what resourceUri/deploymentId it expects)
grep -nE "vectorizer|resourceUri|deploymentId|modelName|\"kind\"" search/index.json | head -20
```

### Print this REPORT block

```
CONFIG:
  azureOpenAI.endpoint = <value>
  embedDeployment = <value>   chatDeployment = <value>   visionDeployment = <value>
  modelProvider = <value>
  foundry = <value>
ACCOUNTS (name/kind/endpoint): <paste the table>
FOUNDRY_ENDPOINTS_MAP: <paste step 3 json — especially any *.openai.azure.us URL>
FOUNDRY_DEPLOYMENTS: <paste step 4 table — note any embedding model like text-embedding-ada-002>
OPENAI_RESOURCE_DEPLOYMENTS: <paste step 5 if a kind=OpenAI resource exists, else 'none'>
SKILLSET_EMBED: <does it use AzureOpenAIEmbeddingSkill or a custom build_embedding webapi skill? paste the matching lines>
INDEX_VECTORIZER: <paste step 7 lines>
```

Run everything and give me the REPORT block. Do not deploy or edit anything.
