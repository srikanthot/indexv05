# Access Request & Explanation — Tech Manual Indexing pipeline (dev/qa/prod)

**Forward this as-is.** It explains the one permission we need, why, and why our other pipelines don't
need it.

---

## THE BLOCKER (one line)
The **Tech Manual Indexing** Jenkins pipeline's service principal needs **User Access Administrator**
at the **subscription scope**. Without it, the pipeline fails at the role‑assignment step
(`AuthorizationFailed on Microsoft.Authorization/roleAssignments/write`).

- Service principal: **"PSEG Tech Manual - Jenkins"**  (appId `6be27496-7668-454b-ac68-1a8bcffac97e`)
- Scope: **subscription level** — dev (`b41d2ec9-3c69-41f3-8dc7-b1500baeedf1`), and later qa + prod
- Role: **User Access Administrator** (one only — NOT Owner, NOT standing Contributor)

---

## WHY THIS PERMISSION IS NEEDED
The indexing pipeline doesn't just deploy code — it **sets up a whole AI system** where several Azure
services must securely talk to each other (Search ↔ AOAI ↔ Storage ↔ Document Intelligence ↔ Function
App ↔ Cosmos), using **managed identities** instead of keys.

To wire those identities together, the pipeline **creates role assignments** for them. And Azure has one
hard rule: **to grant a permission, you must have permission to grant permissions** — that role is
**User Access Administrator**. So the pipeline that assigns roles must itself hold UAA.

It is a **one‑time, permanent** grant. At subscription scope it covers **every resource group** in that
subscription — so we never have to come back for new resource groups or new resources.

---

## WHY THE CHATBOT (front‑end / back‑end) PIPELINE DOES *NOT* NEED THIS
Fair question — the chatbot pipeline deploys fine without it. The difference:

| | What it does | Does it assign roles? | Needs UAA? |
|---|---|---|---|
| **Chatbot pipeline** | copies code onto an **existing web app** | No | **No** — just "deploy to web app" |
| **Indexing pipeline** | wires Search/AOAI/Storage/DI/Function/Cosmos identities together, then deploys + indexes | **Yes** | **Yes** |

The chatbot only pushes code to an app that already exists — it never touches security wiring, so it
needs no role‑assignment rights. The indexing pipeline has to **provision the identity permissions** for
the AI services, which is the privileged action.

---

## BICEP vs INDEXING — same permission either way (this is key)
Assigning roles requires **User Access Administrator no matter which pipeline does it.** It's the same
permission — only the *holder* changes:

- **If the Bicep (infra) pipeline assigns the roles** → the **Bicep deployment identity** needs UAA.
- **If the Indexing pipeline assigns the roles** (our choice) → the **indexing SP** needs UAA.

We chose to keep role‑assignment **inside the indexing pipeline**, so the grant goes to the indexing SP
above. (Bicep stays as the resource‑creation pipeline only — it is the standby option; if we ever move
role‑assignment there, the exact same UAA simply moves to the Bicep identity.)

So there is **no way to avoid this one grant** — it's inherent to "a pipeline that assigns roles." The
only question was *where* it lives, and we've decided: the indexing pipeline.

---

## WHAT'S ALREADY DONE (so you know this is the only gap)
- The pipeline's **data/service roles** (Storage Blob Data, Search Index Data, Cognitive Services, etc.)
  are **already granted** — I did those myself; they succeeded.
- Only **User Access Administrator failed** — because my own account is ABAC‑restricted from granting
  privileged roles. That's the single remaining piece, and it needs an admin.

---

## THE ASK (one line for the admin)
> Grant **User Access Administrator** to service principal **`6be27496-7668-454b-ac68-1a8bcffac97e`**
> ("PSEG Tech Manual - Jenkins") at the **subscription scope** for **dev** (and qa, prod when we
> promote). One‑time; the CI pipeline uses it to assign managed‑identity roles across all resource
> groups. No Owner / no standing Contributor required.

CLI the admin can run (dev example):
```powershell
az role assignment create `
  --assignee "6be27496-7668-454b-ac68-1a8bcffac97e" `
  --role "User Access Administrator" `
  --scope "/subscriptions/b41d2ec9-3c69-41f3-8dc7-b1500baeedf1"
```
Repeat with the qa and prod subscription IDs when promoting.

---

## AFTER THE GRANT (no more admin needed)
1. I self‑grant the SP's remaining data/service roles (Reader, Search Service Contributor, Cognitive
   Services OpenAI User, Cosmos data) — I can do these.
2. Re‑run the indexing pipeline → it assigns the identity roles + deploys + creates the search index +
   runs the indexer, automatically, and nightly thereafter.
3. This holds for every future resource group in the subscription — **no repeat requests.**
