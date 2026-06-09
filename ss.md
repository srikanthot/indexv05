I need you to act like a senior DevOps + Azure AI Search engineer and help me convert this repo into a working Jenkins pipeline.

Context:

* Repo name/folder: `psegtmindex`
* Branch currently: `dev`
* This repo is for Azure AI Search indexing automation for technical manuals/PDFs.
* This is not a normal frontend/backend build.
* The goal is to run the Azure AI Search indexing pipeline through Jenkins.
* Jenkins multibranch job already exists:

  * Jenkins folder/job: `TechManual / psegtechmanualindex`
  * Branch: `dev`
* Current Jenkinsfile was manually created by someone. It is only a skeleton right now.
* Current Jenkinsfile has:

  * `agent { label 'linux' }`
  * Azure credentials:

    * `azure-client-id`
    * `azure-client-secret`
    * `azure-tenant-id`
    * `DEV_AZURE_SUBSCRIPTION_ID`
    * `QA_AZURE_SUBSCRIPTION_ID`
    * `PROD_AZURE_SUBSCRIPTION_ID`
  * Branch mapping:

    * `dev` → DEV subscription
    * `qa` → QA subscription
    * `main` → PROD subscription
  * Azure login using service principal
  * Build stage only echoes “This is the BUILD stage”
  * Deploy stage only echoes “This is the DEPLOY stage”
  * Post stage runs `az logout`

Problem:

* Jenkins currently succeeds quickly, but it is not doing real work.
* Build and Deploy stages are placeholders.
* Need to inspect the repo and create a real Jenkins pipeline that can:

  1. Set up Python
  2. Install repo dependencies
  3. Log into Azure
  4. Select correct Azure subscription based on branch
  5. Validate config
  6. Run safe check first
  7. Then run indexing pipeline
  8. Optionally run full deployment only when requested
  9. Archive useful logs/results
  10. Fail clearly if credentials/config/Azure permissions are missing

Important safety requirement:

* Do not make the default action full deploy.
* Full deploy can modify Azure Search resources, indexers, skillsets, function app settings, etc.
* The safest default should be `check`.
* Then we can run `run`.
* Only later we should run `deploy`.

Please inspect these files carefully:

* `Jenkinsfile`
* `Jenkinsfile.deploy`
* `Jenkinsfile.run`
* `deploy.config.json`
* `deploy.config.example.json`
* `requirements.txt`
* `README.md`
* `scripts/check_index.py`
* `scripts/run_pipeline.py`
* `scripts/deploy.py`
* `scripts/bootstrap.py`
* `scripts/preanalyze.py`
* `scripts/heal_until_done.py`
* `scripts/deploy_search.py`
* Any other scripts used by these files

First, do not change code blindly. I want you to analyze and tell me:

1. What each Jenkinsfile currently does.
2. Which Jenkinsfile should be used by the Jenkins multibranch job.
3. Whether the current `Jenkinsfile` is enough or needs replacement.
4. Whether `deploy.config.json` contains secrets and should be moved to Jenkins Secret File credentials.
5. Which scripts are safe to run first.
6. Which scripts modify Azure resources.
7. Which command should be used for:

   * safe index check only
   * normal indexing run
   * full deployment/setup
8. What Jenkins credentials are required.
9. What Azure RBAC permissions are required.
10. What can be completed only by code changes and what requires Jenkins/Azure admin access.

Expected Jenkins design:

* Add Jenkins parameters:

  * `ACTION`: `check`, `run`, `deploy`
  * Optional `SKIP_TESTS`: true/false
  * Optional `CONFIG_FILE_MODE`: repo config vs Jenkins secret file, depending on what you find
* Keep branch-to-subscription mapping:

  * `dev` → DEV subscription
  * `qa` → QA subscription
  * `main` → PROD subscription
* Add stages:

  1. Checkout / print repo info
  2. Setup Python
  3. Install dependencies
  4. Azure login
  5. Select subscription
  6. Validate required files
  7. Validate config without printing secrets
  8. Run tests/lint if available
  9. Run selected action:

     * `check`: run `check_index.py`
     * `run`: run `run_pipeline.py`, then `heal_until_done.py`, then `check_index.py`
     * `deploy`: run full `deploy.py --auto-fix`
  10. Archive logs/results
  11. Azure logout

Use these command ideas, but verify exact arguments from `--help` before finalizing:

Safe check:

```bash
python scripts/check_index.py --config deploy.config.json --coverage
```

Normal indexing run:

```bash
python scripts/run_pipeline.py --config deploy.config.json
python scripts/heal_until_done.py --config deploy.config.json
python scripts/check_index.py --config deploy.config.json --coverage
```

Full deployment/setup:

```bash
python scripts/deploy.py --config deploy.config.json --auto-fix
```

Please run or suggest these local checks:

```bash
git branch
git status
python --version
python3 --version
python scripts/check_index.py --help
python scripts/run_pipeline.py --help
python scripts/deploy.py --help
```

For Jenkins Linux agent, use Linux venv syntax:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Current Jenkinsfile has service principal login like this:

```bash
az login --service-principal \
  -u ${AZURE_CLIENT_ID} \
  -p ${AZURE_CLIENT_SECRET} \
  --tenant ${AZURE_TENANT_ID}
```

Keep this style unless you find the Jenkins agent uses managed identity.

Please produce:

1. A short explanation of what is wrong with the current Jenkinsfile.
2. A list of missing Jenkins credentials or Azure permissions.
3. A safe first version of the Jenkinsfile that only runs setup + Azure login + check.
4. A final Jenkinsfile with `ACTION=check/run/deploy`.
5. Any changes needed in the repo.
6. Any steps I must ask Jenkins/Azure admin to do because I do not have full access.

Important:

* Do not print secrets in logs.
* Do not echo full `deploy.config.json` if it contains keys/secrets.
* Do not hardcode secrets in Jenkinsfile.
* Do not remove existing branch mapping unless there is a better reason.
* Default action should be `check`, not `deploy`.
* Make the pipeline fail fast with clear error messages.
* Use `set -euo pipefail` where appropriate.
* Archive logs/artifacts if the repo creates any.
* Increase timeout if indexing takes more than 1 hour.
* If a script may take long time, explain where timeout should be increased.

After your analysis, give me the exact final Jenkinsfile content to replace the current one.



Now review the Jenkinsfile you generated as if this will run in a corporate Jenkins environment.

Check specifically:

1. Will this work on a Linux Jenkins agent?
2. Are secrets protected?
3. Is `deploy.config.json` handled safely?
4. Does it fail clearly if Jenkins credentials are missing?
5. Does it fail clearly if Azure login fails?
6. Does it avoid running full deploy by default?
7. Does it support `dev`, `qa`, and `main` branches correctly?
8. Does it run the correct Python commands for this repo?
9. Are the Python virtual environment commands correct for Linux?
10. Are logs/artifacts archived properly?
11. Is the timeout enough for PDF pre-analysis/indexing?
12. What exact Jenkins admin/Azure admin access is still needed?

Then give me the final Jenkinsfile only, plus a short list of required Jenkins credentials.

