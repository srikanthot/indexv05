# >>> MESSAGE TO FORWARD TO ADAM (copy from here) <<<

Hey Adam, good afternoon! 🙂

I'm setting up the **Tech Manual Indexing** pipeline in Jenkins (the one that builds the Azure Search
index behind the tech‑manual chatbot). I've got it almost fully working, but I'm blocked on **one Azure
permission** that only someone with your access can grant.

Could you please grant **User Access Administrator** to our Jenkins service principal, at the
**subscription** level?

- **Service principal:** PSEG Tech Manual - Jenkins  (appId `6be27496-7668-454b-ac68-1a8bcffac97e`)
- **Role:** User Access Administrator  (just this one — not Owner / not Contributor)
- **Scope:** subscription level — **dev** (`b41d2ec9-3c69-41f3-8dc7-b1500baeedf1`) now, and **qa + prod**
  when we promote

**Why I need it:** the indexing pipeline wires several Azure services (Search, OpenAI, Storage, Document
Intelligence, Function App, Cosmos) to securely talk to each other via managed identities. To do that it
**creates role assignments**, and Azure only lets an identity assign roles if it has **User Access
Administrator**. It's a **one‑time** grant — at subscription scope it covers every resource group, so I
won't have to come back each time we add a resource.

I've already set up all the other (data) roles myself — this is the only piece I can't do, because my own
account is restricted from granting privileged roles.

Happy to jump on a quick call if you'd like more context. Thanks so much for the help! 🙏

CLI if that's easier (dev — repeat with qa/prod subscription IDs):
```
az role assignment create --assignee "6be27496-7668-454b-ac68-1a8bcffac97e" --role "User Access Administrator" --scope "/subscriptions/b41d2ec9-3c69-41f3-8dc7-b1500baeedf1"
```

# >>> END OF MESSAGE TO ADAM <<<

---
---

# Background / justification (for my own reference or if Adam asks for detail)

## The blocker (one line)
The Tech Manual Indexing pipeline's service principal needs **User Access Administrator** at the
**subscription scope**. Without it, the pipeline fails at the role‑assignment step
(`AuthorizationFailed on Microsoft.Authorization/roleAssignments/write`).

## Why this permission is needed
The indexing pipeline doesn't just deploy code — it **sets up a whole AI system** where several Azure
services must securely talk to each other (Search ↔ AOAI ↔ Storage ↔ Document Intelligence ↔ Function
App ↔ Cosmos) using **managed identities** instead of keys. To wire those identities together, the
pipeline **creates role assignments** for them. Azure's rule: to grant a permission you must have
permission to grant permissions — that role is **User Access Administrator**. So the pipeline that
assigns roles must itself hold UAA. One‑time; subscription scope covers all resource groups.

## Why the chatbot (front‑end / back‑end) pipeline does NOT need this
| | What it does | Assigns roles? | Needs UAA? |
|---|---|---|---|
| **Chatbot pipeline** | copies code onto an **existing web app** | No | **No** |
| **Indexing pipeline** | wires the AI services' identities together, then deploys + indexes | **Yes** | **Yes** |

The chatbot only pushes code to an app that already exists — it never touches security wiring. The
indexing pipeline has to provision the identity permissions for the AI services, which is the privileged
action.

## Bicep vs Indexing — same permission either way
Assigning roles needs **User Access Administrator no matter which pipeline does it** — only the holder
changes. If Bicep assigns the roles, the Bicep identity needs UAA; if the indexing pipeline assigns them
(our choice), the indexing SP needs UAA. There is no way to avoid this one grant; we only chose where it
lives (the indexing pipeline). Bicep stays as the resource‑creation pipeline only.

## What's already done (so this is the only gap)
- Data/service roles (Storage Blob Data, Search Index Data, Cognitive Services, etc.) — already granted
  by me; succeeded.
- **User Access Administrator** — failed when I tried (my account is ABAC‑restricted from privileged
  roles). This is the single remaining piece and needs Adam.

## After the grant (no more admin needed)
1. I self‑grant the SP's remaining data/service roles (Reader, Search Service Contributor, Cognitive
   Services OpenAI User, Cosmos data).
2. Re‑run the indexing pipeline → it assigns the identity roles + deploys + creates the search index +
   runs the indexer, automatically, and nightly thereafter.
3. Holds for every future resource group in the subscription — no repeat requests.
