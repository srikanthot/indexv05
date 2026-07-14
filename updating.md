# RUN THE INDEXING PIPELINE ON YOUR LAPTOP — step by step

Follow these in order, top to bottom. Commands are for Windows PowerShell. Do not skip a step.

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
