# RUN THE INDEXING PIPELINE ON YOUR LAPTOP — step by step

Follow these in order, top to bottom. Commands are for Windows PowerShell. Do not skip a step.

============================================================================
>>> JENKINS PIPELINE FAILED IN "PREFLIGHT" WITH "not found" / "AuthorizationFailed"? DO THIS <<<
============================================================================
SYMPTOM (what you saw in the Jenkins log):
  - Blob soft-delete check:  Storage account 'psegtmstacdevv01' not found
  - Cosmos check:            AuthorizationFailed for SP object
                             d21336b2-a818-4e2c-a8b7-278aa5113fd7 on Microsoft.DocumentDB
  - Preflight is a GATING stage, so every later stage was skipped and the build failed.

ROOT CAUSE (both errors are the SAME problem):
  The Jenkins service principal (SP) d21336b2-a818-4e2c-a8b7-278aa5113fd7 has NO "Reader"
  role, so it cannot even READ resource metadata. "not found" and "AuthorizationFailed" are
  both just the permission being denied. The fix is to grant the SP its least-privilege roles.

  NOTE: `scripts/assign_roles.py` alone does NOT fix this -- its --jenkins-principal-id path
  does not grant "Reader", and "Reader" is exactly what the two failing preflight checks need.
  You must self-grant the roles below.

------------------------------------------------------------
PART A — GRANT THE ROLES (run in VS Code terminal, ONE COMMAND AT A TIME)
------------------------------------------------------------
Run these as YOURSELF (your admin account that is allowed to create role assignments) --
NOT as the Jenkins SP. The SP cannot grant itself roles. Run them one by one, top to
bottom. If any command errors, copy the error to Copilot and ask it to fix that one line.

A1. Set the Azure Government cloud:
      az cloud set --name AzureUSGovernment

A2. Log in as yourself (a browser opens -- sign in with your PSEG admin account):
      az login

A3. Point at the DEV subscription (replace <DEV_SUBSCRIPTION_ID> with the real id):
      az account set --subscription "<DEV_SUBSCRIPTION_ID>"

A4. Save the subscription id, the Jenkins SP id, and the scope into variables:
      $sub   = az account show --query id -o tsv
      $sp    = "d21336b2-a818-4e2c-a8b7-278aa5113fd7"
      $scope = "/subscriptions/$sub"
    # (optional) confirm they are set -- both lines should print a value:
      echo $sub
      echo $sp

A5. Grant Reader  <-- THIS is the one that fixes your preflight error:
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Reader" --scope $scope

A6. Grant Website Contributor (deploy the function app code):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Website Contributor" --scope $scope

A7. Grant Search Service Contributor (create/update index, skillset, indexer):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Search Service Contributor" --scope $scope

A8. Grant Search Index Data Contributor (write/query index documents):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Search Index Data Contributor" --scope $scope

A9. Grant Storage Blob Data Contributor (read/write cache blobs):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope $scope

A10. Grant Cognitive Services OpenAI User (embeddings/vision):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $scope

A11. Grant Cognitive Services User (Document Intelligence):
      az role assignment create --assignee-object-id $sp --assignee-principal-type ServicePrincipal --role "Cognitive Services User" --scope $scope

A12. Grant the Cosmos data role (SEPARATE command -- different API, do not skip):
      az cosmosdb sql role assignment create --account-name psegtmcosmdevv01 --resource-group psegtmrgdevv01 --role-definition-name "Cosmos DB Built-in Data Contributor" --principal-id $sp --scope "/"

A13. Wire up the managed identities (function app + search service). ONE TIME per
     environment. Needs deploy.config.json in the repo root (see STEP 4) and the venv
     activated (see STEP 2). If Srikanth already did this, skip it:
      python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal

A14. Wait ~2 minutes for the roles to take effect, then go to Jenkins and run ACTION=check
     (see PART B below). If storage STILL says "not found" after this, the account name or
     subscription in the config is wrong -- ask Copilot/Srikanth.

Each successful "az role assignment create" prints a JSON block describing the assignment.
If a role already exists you may see "already exists" -- that is fine, it means it is done.
If you see "AuthorizationFailed" on these commands, YOUR account is not allowed to grant
roles -- an Azure admin must run Part A for you.

------------------------------------------------------------
PART B — WHAT TO PICK IN THE JENKINS "ACTION" DROPDOWN
------------------------------------------------------------
  check      READ-ONLY, ~5 min, changes nothing. RUN THIS FIRST after granting the roles --
             it re-runs preflight + coverage and confirms the permission fix worked. Safe default.
  bootstrap  One-time setup (function app + search index/skillset/indexer). Skips preflight.
             Use only if this environment was never set up.
  deploy     FULL + DESTRUCTIVE + long (hours: preanalyze/vision over every PDF + heal). It
             already includes bootstrap. Use for the first full build of the environment.
  run        Routine nightly ops (reconcile -> preanalyze changed docs -> indexer -> heal).

  RECOMMENDED ORDER:  grant roles (Part A)  ->  ACTION=check (verify)  ->  ACTION=deploy
                      (first full setup)     ->  ACTION=run (day-to-day thereafter).

  CHECKBOXES:
    SKIP_TESTS  -> leave UNCHECKED (emergencies only).
    DRY_RUN     -> leave UNCHECKED. Heads-up: it is declared in the Jenkinsfile but NOT wired
                   into any stage, so toggling it currently does nothing. Do not rely on it.

----------------------------------------------------------------------------

============================================================================
>>> ALREADY STARTED AND GOT "'func' is not recognized"? DO EXACTLY THIS <<<
============================================================================
You got far (preflight + preanalyze passed). It only stopped because Azure
Functions Core Tools (the `func` command) is not installed. Fix it and continue:

1. Install func (Azure Functions Core Tools v4):
      winget install Microsoft.Azure.FunctionsCoreTools
   # (or the v4 x64 MSI: https://github.com/Azure/azure-functions-core-tools/releases)
   # (or, if you have Node.js:  npm install -g azure-functions-core-tools@4 --unsafe-perm true)

2. CLOSE the terminal completely, open a NEW one, and confirm func is found:
      func --version
   # must print a 4.x number. If it says "not recognized", the install did not
   # land on PATH -- reopen the terminal again, or use the MSI installer.

3. Go back into the repo folder and re-activate the environment:
      cd <REPO-FOLDER>
      .\.venv\Scripts\Activate.ps1

4. Make sure Azure is still logged in (re-login if it says not):
      az account show -o table
      # if that errors:  az login   then   az account set --subscription "<DEV_SUBSCRIPTION_ID>"

5. Re-run the SAME command -- it is idempotent, it resumes where it stopped
   (preanalyze is already cached, it will jump to deploying the function + indexing):
      python scripts/deploy.py --config deploy.config.json --skip-roles

That's it. If it stops again, read the error and check the troubleshooting section
at the bottom. Everything below is the full setup from scratch (for a fresh laptop).

----------------------------------------------------------------------------

============================================================================
STEP 0 — INSTALL THESE ONCE (skip any you already have)
============================================================================
- Python 3.12 (or 3.11):  https://www.python.org/downloads/     check:  python --version
- Azure CLI (az):          https://aka.ms/installazurecliwindows  check:  az --version
- Git:                     https://git-scm.com/download/win        check:  git --version
- Azure Functions Core Tools v4 (the `func` command -- REQUIRED to deploy the
  function app code):
      winget install Microsoft.Azure.FunctionsCoreTools
      # (or the v4 x64 MSI from https://github.com/Azure/azure-functions-core-tools/releases)
      # (or, if you have Node.js:  npm install -g azure-functions-core-tools@4 --unsafe-perm true)
      check:  func --version     (must print a 4.x version)
(Close and reopen your terminal after installing, so the commands are found.)

NOTE: LibreOffice is NOT required. If preflight prints "[WARN] LibreOffice (optional)"
that is safe to ignore for PDF manuals -- it only affects figure extraction from
.docx/.pptx/.xlsx files, not PDFs.

============================================================================
STEP 1 — GET THE CODE
============================================================================
# if you do NOT have the code yet:
git clone <REPO-URL>
cd <REPO-FOLDER>

# if you ALREADY have the code, just update it:
cd <REPO-FOLDER>
git pull

============================================================================
STEP 2 — PYTHON ENVIRONMENT + DEPENDENCIES
============================================================================
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# (Mac/Linux instead:  source .venv/bin/activate)
# You should now see (.venv) at the start of your prompt.
python -m pip install --upgrade pip
pip install -r requirements.txt

# NOTE: every NEW terminal window, re-run:  .\.venv\Scripts\Activate.ps1

============================================================================
STEP 3 — LOG INTO AZURE (US Government cloud)
============================================================================
az cloud set --name AzureUSGovernment
az login
# ^ a browser opens; sign in with your PSEG work account.
az account set --subscription "<DEV_SUBSCRIPTION_ID>"
az account show -o table
# ^ confirm the Name/Id shown is the DEV subscription.

============================================================================
STEP 4 — PUT THE CONFIG FILE IN PLACE
============================================================================
# Copy deploy.config.json into the ROOT of the repo folder (get the file from Srikanth).
# Confirm it is there:
Test-Path deploy.config.json
# ^ must print: True

============================================================================
STEP 5 — ONE TIME PER ENVIRONMENT: wire the identity roles  (skip if already done)
============================================================================
# This grants the function-app + search managed identities their data roles.
# Only needs to be done ONCE per environment, by an account allowed to grant
# data roles. If Srikanth already did it, SKIP this step.
python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal

============================================================================
STEP 6 — THE SUPER COMMAND  (does EVERYTHING in one go)
============================================================================
python scripts/deploy.py --config deploy.config.json --skip-roles

# What it does, in order:
#   1. deploys the function app code
#   2. preanalyzes every PDF in the blob container (slow on big PDFs -- be patient)
#   3. creates the search index if it does not exist (never deletes an existing one)
#   4. runs the indexer over all documents and waits until they are all done
#   5. sets is_current_revision so the chatbot's currency filter works
#   6. prints a coverage report
# Leave it running to the end. It can take a while the first time.

============================================================================
STEP 7 — CHECK IT WORKED
============================================================================
python scripts/check_index.py --config deploy.config.json --coverage
# ^ shows which PDFs are indexed and how many chunks each has.

============================================================================
LATER — DAILY / INCREMENTAL RUN (only new or deleted PDFs)
============================================================================
python scripts/run_pipeline.py --config deploy.config.json --triggered-by manual
# reconcile new/deleted PDFs -> preanalyze only the new ones -> index the changes
# -> set currency. Safe to run anytime; it never re-does what is already indexed.

============================================================================
IF SOMETHING FAILS — quick fixes
============================================================================
- "unrecognized arguments: --skip-roles"
      -> your scripts are OLD. Re-run STEP 1 (git pull) to get the latest code.
- "AuthorizationFailed" / 403
      -> your Azure account is missing a role on that resource. Tell Srikanth the
         resource name from the error; it is a one-time role grant.
- "config not found: deploy.config.json"
      -> the file is not in the repo root. Redo STEP 4.
- "(.venv) not showing" or "module not found"
      -> you did not activate the venv in this terminal. Run:  .\.venv\Scripts\Activate.ps1
         then  pip install -r requirements.txt  again if needed.
- az command not found
      -> Azure CLI not installed / terminal not reopened. Redo STEP 0.

============================================================================
THE WHOLE THING AS A COPY-PASTE BLOCK (after Step 0 is done once)
============================================================================
cd <REPO-FOLDER>
git pull
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
az cloud set --name AzureUSGovernment
az login
az account set --subscription "<DEV_SUBSCRIPTION_ID>"
# make sure deploy.config.json is in this folder, then:
python scripts/deploy.py --config deploy.config.json --skip-roles
python scripts/check_index.py --config deploy.config.json --coverage
