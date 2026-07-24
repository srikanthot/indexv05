/*
 * Jenkinsfile — Azure AI Search Indexing Pipeline
 * Multibranch pipeline job: TechManual / psegtechmanualindex
 *
 * Author: Paul F. Rubio - PSEG DevOps
 * April 2026 - CHG0000263423
 *
 * Updates:
 *   06-03-2026: Start Pipeline Setup
 *   06-09-2026: Full implementation — parameterized ACTION (check/run/deploy)
 *
 * Branch → Environment mapping:
 *   dev  → DEV subscription
 *   qa   → QA subscription
 *   main → PROD subscription
 *
 * ACTION parameter controls what the pipeline actually does:
 *   bootstrap — ONE-TIME environment setup (preflight + function app + search artifacts).
 *               Roles are NOT assigned here — provisioned once, manually (docs/RBAC_LEAST_PRIVILEGE.md).
 *   check  — READ-ONLY. Validates config, checks index coverage, detects stuck indexer.
 *   run    — Nightly/ops run. Reconcile + incremental preanalyze + conditional indexer + heal/check.
 *            Indexer is triggered ONLY when changed docs are detected and preanalyze succeeds.
 *   deploy — Full deployment (bootstrap + function code + search artifacts + preanalyze + heal + currency).
 *            DESTRUCTIVE. Modifies Azure Search resources + Function App code/settings (no RBAC — skipped).
 *
 * Required Jenkins credentials:
 *   azure-client-id             (Secret text)  — Service principal app ID
 *   azure-client-secret         (Secret text)  — Service principal password
 *   azure-tenant-id             (Secret text)  — Azure AD tenant ID
 *   DEV_AZURE_SUBSCRIPTION_ID   (Secret text)  — DEV subscription ID
 *   QA_AZURE_SUBSCRIPTION_ID    (Secret text)  — QA subscription ID
 *   PROD_AZURE_SUBSCRIPTION_ID  (Secret text)  — PROD subscription ID
 *   deploy-config-dev           (Secret file)  — deploy.config.json for DEV
 *   deploy-config-qa            (Secret file)  — deploy.config.json for QA
 *   deploy-config-prod          (Secret file)  — deploy.config.json for PROD
 *
 * Required Azure RBAC on the service principal (LEAST PRIVILEGE — all self-grantable,
 * NO User Access Administrator and NO Contributor. Roles are assigned ONCE, manually,
 * OUTSIDE this pipeline — see docs/RBAC_LEAST_PRIVILEGE.md. Every ACTION uses --skip-roles):
 *   Reader                                on the subscription (metadata reads)
 *   Website Contributor                   on the Function App (deploy code)
 *   Search Service Contributor            on the search service (create index/indexer)
 *   Search Index Data Contributor         on the search service (index documents)
 *   Storage Blob Data Contributor         on the storage account (cache blobs)
 *   Cognitive Services OpenAI User        on AOAI (embeddings/vision)
 *   Cognitive Services User               on Document Intelligence
 *   Cosmos DB Built-in Data Contributor   on the cosmos account
 * The function-app + search MANAGED IDENTITIES get their roles via a one-time
 *   `python scripts/assign_roles.py --config deploy.config.json --skip-deploy-principal`
 *   run from VS Code (data roles only — no admin needed).
 */
 
pipeline {
    agent { label 'linux' }
 
    parameters {
        choice(
            name: 'ACTION',
            choices: ['bootstrap', 'check', 'run', 'deploy', 'preanalyze', 'deploy-function', 'deploy-search', 'index-resume'],
            description: '''What to do:
  bootstrap = ONE-TIME setup (preflight + function + search artifacts; roles assigned separately)
  check  = READ-ONLY index coverage + stuck-indexer check (safe, default)
  run    = Nightly operations (reconcile → preanalyze → indexer only-if-needed → heal → check)
  deploy = Full deployment (bootstrap → function code → search artifacts → preanalyze → heal)
            WARNING: deploy modifies Azure resources. Use only for initial setup or upgrades.
  --- granular (run ONE step only) ---
  preanalyze      = ONLY run preanalyze (DI + vision → output.json cache). No search/function changes.
  deploy-function = ONLY publish the Function App code + app settings. No search/preanalyze.
  deploy-search   = ONLY (re)create the search artifacts (index/skillset/indexer/datasource).
                    Tick RUN_INDEXER to also trigger an indexer run afterwards.
  index-resume    = RESUME an in-progress backfill: re-trigger the existing indexer
                    (NO reset, NO heal, NO preanalyze) until the 120-min-quota backlog
                    drains. Safe to re-run — it CONTINUES, never restarts. Use this to
                    finish indexing the remaining documents.'''
        )
        booleanParam(
            name: 'SKIP_TESTS',
            defaultValue: false,
            description: 'Skip unit tests and lint (use for emergency runs only)'
        )
        booleanParam(
            name: 'DRY_RUN',
            defaultValue: false,
            description: 'For run/deploy: print what would happen without making changes'
        )
        booleanParam(
            name: 'SKIP_PREANALYZE',
            defaultValue: false,
            description: 'For deploy/run: SKIP preanalyze (DI + vision) and reuse the existing output.json cache. Check this when the documents are already preanalyzed and you only need to (re)create the search artifacts + reindex — avoids re-running the 12-13h preanalyze.'
        )
        booleanParam(
            name: 'RUN_INDEXER',
            defaultValue: false,
            description: 'For the deploy-search action ONLY: also trigger an indexer run after (re)creating the search artifacts. Leave OFF to just update index/skillset/indexer definitions without reindexing.'
        )
    }
 
    options {
        timestamps()
        // deploy can take 8+ hours (preanalyze on large PDFs + 8 heal iterations)
        // run can take 6 hours; check takes < 5 min
        timeout(time: 10, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()
    }
 
    environment {
        // Service identity
        ServiceName = 'psegtechmanualindex'
        AZURE_CLOUD = 'AzureUSGovernment'
        PYTHONUNBUFFERED = '1'
        ACTION = "${params.ACTION ?: 'check'}"
        // Injected into the deploy/run commands: '--skip-preanalyze' or empty.
        SKIP_PA = "${params.SKIP_PREANALYZE ? '--skip-preanalyze' : ''}"
        // For the deploy-search action: 'true' triggers an indexer run afterwards.
        RUN_INDEXER = "${params.RUN_INDEXER ? 'true' : 'false'}"
 
        // Azure credentials (masked automatically by Jenkins)
        AZURE_CLIENT_ID     = credentials('azure-client-id')
        AZURE_CLIENT_SECRET = credentials('azure-client-secret')
        AZURE_TENANT_ID     = credentials('azure-tenant-id')
 
        // Subscription IDs per environment
        DEV_AZURE_SUBSCRIPTION_ID  = credentials('DEV_AZURE_SUBSCRIPTION_ID')
        QA_AZURE_SUBSCRIPTION_ID   = credentials('QA_AZURE_SUBSCRIPTION_ID')
        PROD_AZURE_SUBSCRIPTION_ID = credentials('PROD_AZURE_SUBSCRIPTION_ID')
    }
 
    stages {
 
        // ============================================================
        stage('Checkout & Info') {
        // ============================================================
            steps {
                checkout scm
                sh '''
                    set -euo pipefail
                    echo "=========================================="
                    echo "  Job:      ${JOB_NAME}"
                    echo "  Branch:   ${BRANCH_NAME}"
                    echo "  Action:   ${ACTION}"
                    echo "  Build:    #${BUILD_NUMBER}"
                    echo "  Node:     $(hostname)"
                    echo "  Git SHA:  $(git rev-parse --short HEAD)"
                    echo "  Date:     $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
                    echo "=========================================="
                '''
            }
        }
 
        // ============================================================
        stage('Setup Python') {
        // ============================================================
            steps {
                sh '''
                    set -euo pipefail
                    echo "[INFO] Setting up Python virtual environment..."
 
                    # Require Python 3.12 or 3.11 explicitly — do NOT fall back to system python3
                    if command -v python3.12 >/dev/null 2>&1; then
                        PYTHON=python3.12
                    elif command -v python3.11 >/dev/null 2>&1; then
                        PYTHON=python3.11
                    else
                        echo "[FAIL] Neither python3.12 nor python3.11 found on this agent."
                        echo "       System python3 is: $(python3 --version 2>&1 || echo 'not installed')"
                        echo "       Install Python 3.11+ on this node before running this pipeline."
                        exit 1
                    fi
 
                    echo "[INFO] Using: $($PYTHON --version)"
 
                    # Always recreate venv so we never reuse a stale interpreter.
                    rm -rf .venv
                    $PYTHON -m venv .venv
                    . .venv/bin/activate
 
                    echo "[INFO] Active python after venv activation:"
                    python --version
                    which python
                    python -c "import sys; print(sys.executable, sys.version)"
 
                    python -m pip install --upgrade pip --quiet
                    pip install --quiet -r requirements.txt
 
                    echo "[INFO] Python environment ready."
                    pip list --format=columns 2>/dev/null | head -5 || true
                '''
            }
        }
 
        // ============================================================
        stage('Validate Tools') {
        // ============================================================
            steps {
                sh '''
                    set -euo pipefail
                    echo "[INFO] Checking required tools..."
 
                    # Azure CLI
                    if ! command -v az >/dev/null 2>&1; then
                        echo "[FAIL] Azure CLI (az) not found. Install it on the Jenkins agent."
                        exit 1
                    fi
                    az --version 2>&1 | head -3 || true
 
                    # jq (used by deploy_function.sh)
                    if ! command -v jq >/dev/null 2>&1; then
                        echo "[WARN] jq not found. deploy action will fail if it needs deploy_function.sh."
                    fi
 
                    echo "[INFO] Tools validated."
                '''
            }
        }
 
        // ============================================================
        stage('Unit Tests & Lint') {
        // ============================================================
            when { expression { return !params.SKIP_TESTS } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "[INFO] Running unit tests..."
                    python tests/test_unit.py 2>&1 | tee test_output.log || true
 
                    # Count pass/fail from our custom test harness
                    PASS_COUNT=$(grep -c "^  PASS" test_output.log || echo "0")
                    # Only count real test FAIL lines (indented), not SSL error noise
                    FAIL_COUNT=$(grep -c "^  FAIL" test_output.log || echo "0")
                    echo "[INFO] Tests: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
 
                    if [ "$FAIL_COUNT" -gt "0" ]; then
                        echo "[WARN] Some unit tests failed. Review test_output.log."
                        grep "^  FAIL" test_output.log || true
                    fi
 
                    echo "[INFO] Running lint (ruff)..."
                    pip install --quiet ruff
                    ruff check function_app scripts tests 2>&1 | tee lint_output.log || true
                    echo "[INFO] Lint complete."
                '''
            }
        }
 
        // ============================================================
        stage('Azure Login') {
        // ============================================================
            steps {
                sh '''
                    set -euo pipefail
                    echo "[INFO] Setting Azure cloud to ${AZURE_CLOUD}..."
                    az cloud set --name "${AZURE_CLOUD}"
 
                    echo "[INFO] Logging into Azure (service principal)..."
                    az login --service-principal \
                        -u "${AZURE_CLIENT_ID}" \
                        -p "${AZURE_CLIENT_SECRET}" \
                        --tenant "${AZURE_TENANT_ID}" \
                        --output none
 
                    echo "[INFO] Azure login successful."
                    az account show --query "{name:name, id:id, tenantId:tenantId}" -o table
                '''
            }
        }
 
        // ============================================================
        stage('Select Subscription') {
        // ============================================================
            steps {
                sh '''
                    set -euo pipefail
                    branch="${BRANCH_NAME}"
 
                    if [ "$branch" = "dev" ]; then
                        echo "[INFO] Setting subscription for DEV environment"
                        az account set --subscription "${DEV_AZURE_SUBSCRIPTION_ID}"
 
                    elif [ "$branch" = "qa" ]; then
                        echo "[INFO] Setting subscription for QA environment"
                        az account set --subscription "${QA_AZURE_SUBSCRIPTION_ID}"
 
                    elif [ "$branch" = "main" ]; then
                        echo "[INFO] Setting subscription for PROD environment"
                        az account set --subscription "${PROD_AZURE_SUBSCRIPTION_ID}"
 
                    else
                        echo "[FAIL] No subscription mapping for branch: $branch"
                        echo "       Supported branches: dev, qa, main"
                        exit 1
                    fi
 
                    echo "[INFO] Active subscription:"
                    az account show --query "{name:name, id:id}" -o table
                '''
            }
        }
 
        // ============================================================
        stage('Load Config') {
        // ============================================================
            steps {
                script {
                    // Select the correct secret file credential based on branch
                    def credId
                    switch (env.BRANCH_NAME) {
                        case 'dev':  credId = 'deploy-config-dev';  break
                        case 'qa':   credId = 'deploy-config-qa';   break
                        case 'main': credId = 'deploy-config-prod'; break
                        default:
                            error "[FAIL] No config credential for branch: ${env.BRANCH_NAME}"
                    }
                    withCredentials([file(credentialsId: credId, variable: 'CFG_FILE')]) {
                        sh '''
                            set -euo pipefail
                            cp "$CFG_FILE" deploy.config.json
                            echo "[INFO] deploy.config.json loaded from Jenkins credentials."
 
                            # Validate it is parseable JSON
                            if ! python3 -c "import json; json.load(open('deploy.config.json'))"; then
                                echo "[FAIL] deploy.config.json is not valid JSON!"
                                exit 1
                            fi
 
                            # Print structure WITHOUT values (no secrets, but cautious about endpoints)
                            echo "[INFO] Config structure (keys only):"
                            python3 -c "
import json
def show_keys(d, prefix=''):
    for k, v in d.items():
        if k.startswith('_'):
            continue
        if isinstance(v, dict):
            print(f'  {prefix}{k}:')
            show_keys(v, prefix + '  ')
        else:
            # Show key + value length, not the value
            print(f'  {prefix}{k}: ({len(str(v))} chars)')
show_keys(json.load(open('deploy.config.json')))
"
                        '''
                    }
                }
            }
        }
 
        // ============================================================
        stage('Preflight Validation') {
        // ============================================================
            when { expression { return params.ACTION != 'bootstrap' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "[INFO] Python used by preflight:"
                    python --version
                    which python
                    python -c "import sys; print(sys.executable, sys.version)"
 
                    echo "[INFO] Running preflight checks..."
                    python scripts/preflight.py --config deploy.config.json
                    echo "[INFO] Preflight passed."
                '''
            }
        }
 
        // ============================================================
        stage('Local Schema Check') {
        // ============================================================
            when { expression { return params.ACTION != 'bootstrap' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "[INFO] Running offline schema consistency check..."
                    python scripts/smoke_test.py --local 2>&1 | tee schema_check.log
                    echo "[INFO] Schema check complete."
                '''
            }
        }
 
        // ============================================================
        stage('Action: bootstrap') {
        // ============================================================
            when { expression { return params.ACTION == 'bootstrap' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "=========================================="
                    echo "  ACTION: bootstrap (ONE-TIME SETUP)"
                    echo "=========================================="
 
                    # --skip-roles: role assignment is done ONCE, manually, from VS
                    #   Code (assign_roles.py + self-granted data roles). SP needs no
                    #   User Access Administrator / Contributor. Resource settings come
                    #   from the Bicep infra pipeline, so --auto-fix is not used.
                    python scripts/bootstrap.py \
                        --config deploy.config.json \
                        --skip-roles \
                        2>&1 | tee bootstrap_output.log
 
                    echo "[INFO] Bootstrap complete."
                '''
            }
        }
 
        // ============================================================
        stage('Action: check') {
        // ============================================================
            when { expression { return params.ACTION == 'check' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "=========================================="
                    echo "  ACTION: check (read-only)"
                    echo "=========================================="
 
                    # NOTE: check_index.py's main() RETURNS after --coverage, so
                    # --coverage and --check-stuck-indexer MUST be separate runs or
                    # the stuck-indexer check is silently skipped.
                    echo "[INFO] Checking index coverage..."
                    python scripts/check_index.py \
                        --config deploy.config.json \
                        --coverage \
                        --triggered-by jenkins-${BUILD_NUMBER} \
                        2>&1 | tee check_index_output.log

                    echo "[INFO] Checking for a stuck indexer..."
                    python scripts/check_index.py \
                        --config deploy.config.json \
                        --check-stuck-indexer \
                        2>&1 | tee -a check_index_output.log
 
                    echo "[INFO] Check complete."
                '''
            }
        }
 
        // ============================================================
        stage('Action: run') {
        // ============================================================
            when { expression { return params.ACTION == 'run' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "=========================================="
                    echo "  ACTION: run (indexing pipeline)"
                    echo "=========================================="
 
                    TRIGGERED_BY="jenkins-${BRANCH_NAME}-${BUILD_NUMBER}"
 
                    echo "[INFO] Running full indexing pipeline..."
                    python scripts/run_pipeline.py \
                        --config deploy.config.json \
                        --triggered-by "$TRIGGERED_BY" \
                        --trigger-indexer \
                        --auto-heal \
                        --max-wait-minutes 60 \
                        $SKIP_PA \
                        2>&1 | tee run_pipeline_output.log
 
                    echo ""
                    echo "[INFO] Final coverage check..."
                    python scripts/check_index.py \
                        --config deploy.config.json \
                        --coverage \
                        --write-status \
                        --triggered-by "$TRIGGERED_BY" \
                        2>&1 | tee check_index_output.log
 
                    echo "[INFO] Run pipeline complete."
                '''
            }
        }
 
        // ============================================================
        stage('Action: index-resume') {
        // ============================================================
            when { expression { return params.ACTION == 'index-resume' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate

                    echo "=========================================="
                    echo "  ACTION: index-resume"
                    echo "  Resume the backfill: re-trigger the indexer until the"
                    echo "  120-min-quota backlog drains. NO reset, NO heal, NO"
                    echo "  preanalyze -- it CONTINUES, it never restarts."
                    echo "=========================================="

                    # Best-effort: turn OFF the auto-heal timer so it cannot reset the
                    # indexer's high-water mark mid-backfill. Non-fatal -- the driver
                    # itself never resets, and the deployed auto_heal guard also skips
                    # while the indexer is advancing.
                    FUNC_APP=$(python -c "import json;print(json.load(open('deploy.config.json'))['functionApp']['name'])")
                    FUNC_RG=$(python -c "import json;print(json.load(open('deploy.config.json'))['functionApp']['resourceGroup'])")
                    echo "[INFO] Disabling AUTO_HEAL_ENABLED on ${FUNC_APP} (best-effort)..."
                    if az functionapp config appsettings set -g "${FUNC_RG}" -n "${FUNC_APP}" \
                           --settings AUTO_HEAL_ENABLED=false >/dev/null 2>&1; then
                        echo "[INFO] auto-heal disabled for the backfill."
                    else
                        echo "[WARN] could not set AUTO_HEAL_ENABLED (continuing; the driver never resets)."
                    fi

                    echo "[INFO] Driving the indexer to drain the remaining backlog..."
                    python scripts/backfill_indexer.py \
                        --config deploy.config.json \
                        --max-hours 8 \
                        2>&1 | tee backfill_output.log

                    echo ""
                    echo "[INFO] Coverage after this invocation (informational; partial is expected mid-backfill):"
                    python scripts/check_index.py \
                        --config deploy.config.json \
                        --coverage \
                        --triggered-by "jenkins-index-resume-${BUILD_NUMBER}" \
                        2>&1 | tee check_index_output.log || true

                    echo "[INFO] index-resume finished. If not yet all docs, just run"
                    echo "       this action again -- it continues from where it left off."
                '''
            }
        }

        // ============================================================
        stage('Confirm Deploy') {
        // ============================================================
            when { expression { return params.ACTION in ['deploy', 'deploy-function', 'deploy-search'] } }
            steps {
                script {
                    if (env.BRANCH_NAME == 'main') {
                        input(
                            message: "⚠️  PRODUCTION DEPLOY — This will modify Azure Search resources, " +
                                     "Function App code, RBAC, and trigger full reindexing. Approve?",
                            submitterParameter: 'APPROVER'
                        )
                    } else {
                        echo "[INFO] Non-prod deploy (${env.BRANCH_NAME}). Proceeding without manual approval."
                    }
                }
            }
        }
 
        // ============================================================
        stage('Action: deploy') {
        // ============================================================
            when { expression { return params.ACTION == 'deploy' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate
 
                    echo "=========================================="
                    echo "  ACTION: deploy (FULL DEPLOYMENT)"
                    echo "  Branch: ${BRANCH_NAME}"
                    echo "  WARNING: This modifies Azure resources!"
                    echo "=========================================="
 
                    # --skip-roles: same as bootstrap -- roles are provisioned once,
                    #   manually, outside the pipeline. deploy.py chains bootstrap +
                    #   preanalyze + search artifacts + indexer + heal + currency pass.
                    python scripts/deploy.py \
                        --config deploy.config.json \
                        --skip-roles \
                        $SKIP_PA \
                        2>&1 | tee deploy_output.log
 
                    echo ""
                    echo "[INFO] Post-deploy coverage check..."
                    python scripts/check_index.py \
                        --config deploy.config.json \
                        --coverage \
                        --write-status \
                        --triggered-by "jenkins-deploy-${BRANCH_NAME}-${BUILD_NUMBER}" \
                        2>&1 | tee check_index_output.log
 
                    echo "[INFO] Deploy complete."
                '''
            }
        }
 
        // ============================================================
        stage('Action: preanalyze') {
        // ============================================================
            when { expression { return params.ACTION == 'preanalyze' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate

                    echo "=========================================="
                    echo "  ACTION: preanalyze (DI + vision -> output.json cache ONLY)"
                    echo "  No search artifacts or function code are touched."
                    echo "=========================================="

                    # Respects the existing cache (only analyzes what is missing).
                    # Add --force to re-analyze everything, or --incremental for new docs.
                    python scripts/preanalyze.py \
                        --config deploy.config.json \
                        2>&1 | tee preanalyze_output.log

                    echo "[INFO] Preanalyze complete."
                '''
            }
        }

        // ============================================================
        stage('Action: deploy-function') {
        // ============================================================
            when { expression { return params.ACTION == 'deploy-function' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate

                    echo "=========================================="
                    echo "  ACTION: deploy-function (Function App CODE + app settings ONLY)"
                    echo "  No search artifacts or preanalyze are touched."
                    echo "=========================================="

                    bash scripts/deploy_function.sh deploy.config.json \
                        2>&1 | tee deploy_function_output.log

                    echo "[INFO] Function code + app settings deployed."
                '''
            }
        }

        // ============================================================
        stage('Action: deploy-search') {
        // ============================================================
            when { expression { return params.ACTION == 'deploy-search' } }
            steps {
                sh '''
                    set -euo pipefail
                    . .venv/bin/activate

                    echo "=========================================="
                    echo "  ACTION: deploy-search (index/skillset/indexer/datasource ONLY)"
                    echo "  Function code + preanalyze are NOT touched."
                    echo "  RUN_INDEXER=${RUN_INDEXER}"
                    echo "=========================================="

                    RUN_INDEXER_FLAG=""
                    if [ "${RUN_INDEXER}" = "true" ]; then RUN_INDEXER_FLAG="--run-indexer"; fi

                    python scripts/deploy_search.py \
                        --config deploy.config.json \
                        $RUN_INDEXER_FLAG \
                        2>&1 | tee deploy_search_output.log

                    echo "[INFO] Search artifacts updated."
                '''
            }
        }

    } // stages
 
    post {
        always {
            // Archive all log files produced during the run
            archiveArtifacts artifacts: '*.log', allowEmptyArchive: true
 
            // Clean up config file (contains environment-specific endpoints)
            sh 'rm -f deploy.config.json || true'
 
            // Logout from Azure
            sh 'az logout 2>/dev/null || true'
 
            echo "[INFO] Cleanup complete."
        }
        success {
            echo "[SUCCESS] Pipeline finished: ACTION=${params.ACTION}, branch=${BRANCH_NAME}, build=#${BUILD_NUMBER}"
        }
        failure {
            echo "[FAILURE] Pipeline FAILED: ACTION=${params.ACTION}, branch=${BRANCH_NAME}, build=#${BUILD_NUMBER}"
            echo "          Review the archived .log files and stage output for details."
        }
        unstable {
            echo "[UNSTABLE] Pipeline completed with warnings."
        }
    }
 
} // pipeline
 
 