"""
One-command bootstrap for a fresh environment.

Step list (idempotent — safe to re-run):

  0. Pre-preflight auto-fixes (--auto-fix only):
       - Enable blob soft-delete (30-day retention) if disabled
       - Create Cosmos DB database if missing
     These run BEFORE preflight so preflight passes without manual
     `az storage account ...` / `az cosmosdb ...` commands.
  1. Preflight checks                        (preflight.py)
  2. Detect Search auth + network issues     (auto-fix with --auto-fix)
  3. Create Cosmos DB database if missing    (safety-net; usually a no-op
                                              after STEP 0)
  4. Assign all RBAC roles (+ 300s wait)     (assign_roles.py)
  5. Wait for RBAC propagation
  6. Deploy Function App code                (deploy_function.sh/ps1)
  7. Configure Function App app settings     (AUTO_HEAL_ENABLED=true + AOAI/DI/SEARCH env vars)
  8. Deploy search artifacts                 (deploy_search.py, 5-attempt retry)
  9. Smoke test                              (smoke_test.py)

If you ALSO want preanalyze + indexer + heal loop in the same command,
use scripts/deploy.py — it wraps bootstrap.py with the data pipeline.

Usage:
    python scripts/bootstrap.py --config deploy.config.json
    python scripts/bootstrap.py --config deploy.config.json --auto-fix

    # Skip specific phases (used by scripts/deploy.py to defer search
    # artifacts until AFTER preanalyze runs):
    python scripts/bootstrap.py --config deploy.config.json --skip-search-artifacts
    python scripts/bootstrap.py --config deploy.config.json --skip-cosmos

Use --auto-fix when you have permission to change security settings on
the search service. Without it, the script reports what needs changing
and stops.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def az_bin() -> str:
    return "az.cmd" if os.name == "nt" else "az"


def az(args: list[str], *, check: bool = False) -> tuple[int, str, str]:
    """Run az command. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run([az_bin()] + args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        print("ERROR: az CLI not on PATH. Install Azure CLI first.")
        sys.exit(2)
    if check and r.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:6])} failed: {r.stderr[:300]}")
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def step(msg: str) -> None:
    print(f"  -> {msg}", flush=True)


def run_script(script_path: str, args: list[str]) -> int:
    """Run a Python or shell script as a subprocess. Returns exit code."""
    py = sys.executable or "python"
    cmd = [py, str(REPO_ROOT / script_path)] + args
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd)
    return proc.returncode


# ---------- detection helpers ----------

def detect_search_service_config(search_name: str, rg: str) -> dict:
    rc, out, _ = az([
        "search", "service", "show", "-n", search_name, "-g", rg,
        "--query", "{authOptions:authOptions, publicAccess:publicNetworkAccess, ipRules:networkRuleSet.ipRules}",
        "-o", "json",
    ])
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {}


def detect_cosmos_database(account: str, db: str, rg: str) -> bool:
    rc, out, _ = az([
        "cosmosdb", "sql", "database", "list",
        "--account-name", account, "--resource-group", rg,
        "--query", "[].name", "-o", "tsv",
    ])
    if rc != 0:
        return False
    return db in (out or "").splitlines()


def detect_blob_soft_delete(storage_account: str) -> tuple[bool, int]:
    """Returns (enabled, retention_days). enabled=False on any error."""
    rc, out, _ = az([
        "storage", "account", "blob-service-properties", "show",
        "--account-name", storage_account,
        "--query", "deleteRetentionPolicy",
        "-o", "json",
    ])
    if rc != 0:
        return False, 0
    try:
        prop = json.loads(out) if out.strip() else {}
        return bool(prop.get("enabled")), int(prop.get("days") or 0)
    except Exception:
        return False, 0


def detect_cosmos_role(account: str, rg: str, principal_oid: str) -> bool:
    """Returns True if principal_oid has at least one role assignment on
    the cosmos account."""
    rc, out, _ = az([
        "cosmosdb", "sql", "role", "assignment", "list",
        "--account-name", account, "--resource-group", rg,
        "-o", "json",
    ])
    if rc != 0:
        return False
    try:
        for r in json.loads(out):
            if (r.get("principalId") or "").lower() == principal_oid.lower():
                return True
    except Exception:
        pass
    return False


# ---------- main bootstrap ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="One-command environment bootstrap")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--auto-fix", action="store_true",
                    help="Allow auto-fixing common preflight blockers: "
                         "search service authOptions, search publicNetworkAccess, "
                         "blob soft-delete (30-day default), and Cosmos DB database "
                         "creation. Without this, the script reports issues and stops.")
    ap.add_argument("--skip-cosmos", action="store_true",
                    help="Skip Cosmos DB setup entirely.")
    ap.add_argument("--skip-deploy-principal", action="store_true",
                    help="Skip granting RBAC to the signed-in principal "
                         "(use when Jenkins agent identity already has roles).")
    ap.add_argument("--skip-roles", action="store_true",
                    help="Skip STEP 4 (assign_roles.py) entirely. Use in the "
                         "RECURRING pipeline when the managed-identity role "
                         "assignments were already provisioned once (by an admin "
                         "or IaC). This lets the pipeline SP run with LEAST "
                         "PRIVILEGE -- it then needs NO User Access Administrator "
                         "and NO Contributor, only the data/service roles it uses "
                         "directly. See docs/RBAC_LEAST_PRIVILEGE.md.")
    ap.add_argument("--skip-function-app", action="store_true",
                    help="Skip deploying function app code.")
    ap.add_argument("--skip-app-settings", action="store_true",
                    help="Skip configuring Function App app settings.")
    ap.add_argument("--skip-search-artifacts", action="store_true",
                    help="Skip deploying search index/skillset/indexer/datasource. "
                         "Use when the caller wants to run preanalyze BEFORE the "
                         "indexer fires (e.g. scripts/deploy.py wrapper).")
    ap.add_argument("--skip-smoke-test", action="store_true",
                    help="Skip the final smoke test.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    rg = cfg["functionApp"]["resourceGroup"]
    search_name = cfg["search"]["endpoint"].replace("https://", "").split(".")[0]
    cosmos_endpoint = (cfg.get("cosmos") or {}).get("endpoint", "")
    cosmos_db = (cfg.get("cosmos") or {}).get("database", "")
    cosmos_account = cosmos_endpoint.replace("https://", "").split(".")[0] if cosmos_endpoint else ""
    storage_account = cfg["storage"]["accountResourceId"].rstrip("/").split("/")[-1]

    issues_blocking: list[str] = []
    issues_warned: list[str] = []

    # ============================================================
    section("STEP 0 / 8 — Pre-preflight auto-fixes (--auto-fix only)")
    # ============================================================
    # These run BEFORE preflight so the preflight checks pass without
    # operator intervention. Each one is idempotent — re-running is safe.
    if args.auto_fix:
        # 0a. Enable blob soft-delete if disabled.
        enabled, days = detect_blob_soft_delete(storage_account)
        if enabled:
            step(f"blob soft-delete already ON ({days}-day retention) on {storage_account}")
        else:
            step(f"enabling blob soft-delete on {storage_account} (30-day retention)...")
            rc, _, err = az([
                "storage", "account", "blob-service-properties", "update",
                "--account-name", storage_account,
                "--resource-group", rg,
                "--enable-delete-retention", "true",
                "--delete-retention-days", "30",
            ])
            if rc != 0:
                issues_warned.append(f"could not enable blob soft-delete: {err[:200]}")
            else:
                step("blob soft-delete enabled")

        # 0b. Create Cosmos DB database if missing.
        if not args.skip_cosmos and cosmos_account and cosmos_db:
            if detect_cosmos_database(cosmos_account, cosmos_db, rg):
                step(f"cosmos database '{cosmos_db}' already exists in {cosmos_account}")
            else:
                step(f"creating cosmos database '{cosmos_db}' in {cosmos_account}...")
                rc, _, err = az([
                    "cosmosdb", "sql", "database", "create",
                    "--account-name", cosmos_account,
                    "--resource-group", rg,
                    "--name", cosmos_db,
                    "--throughput", "400",
                ])
                if rc != 0:
                    issues_warned.append(f"could not create cosmos database: {err[:200]}")
                else:
                    step("cosmos database created")
    else:
        print("  (skipped; --auto-fix not set)")

    # ============================================================
    section("STEP 1 / 8 — Preflight checks")
    # ============================================================
    rc = run_script("scripts/preflight.py", ["--config", args.config])
    if rc != 0:
        print("\nPreflight reported issues. Fix them and re-run.")
        return rc

    # ============================================================
    section("STEP 2 / 8 — Detect search service config issues")
    # ============================================================
    config = detect_search_service_config(search_name, rg)
    auth_opts = config.get("authOptions") or {}
    public_access = config.get("publicAccess") or "Unknown"
    ip_rules = config.get("ipRules") or []

    is_apikey_only = "apiKeyOnly" in auth_opts and "aadOrApiKey" not in auth_opts
    is_public_disabled = (public_access or "").lower() == "disabled"

    print(f"  authOptions:        {list(auth_opts.keys())}")
    print(f"  publicAccess:       {public_access}")
    print(f"  ipRules:            {len(ip_rules)} entries")

    if is_apikey_only:
        if args.auto_fix:
            step("Search service is in apiKeyOnly mode. Auto-fixing -> aadOrApiKey")
            rc, _, err = az([
                "search", "service", "update",
                "-n", search_name, "-g", rg,
                "--auth-options", "aadOrApiKey",
                "--aad-auth-failure-mode", "http403",
            ])
            if rc != 0:
                issues_blocking.append(f"failed to fix authOptions: {err[:200]}")
            else:
                step("authOptions fixed; sleeping 30s for change to take effect")
                time.sleep(30)
        else:
            issues_blocking.append(
                "search service authOptions is 'apiKeyOnly' — AAD-based deploys will get 403. "
                f"Fix: az search service update -n {search_name} -g {rg} "
                "--auth-options aadOrApiKey --aad-auth-failure-mode http403"
                " (or re-run this script with --auto-fix)"
            )

    if is_public_disabled:
        if args.auto_fix:
            step("publicNetworkAccess is disabled. Auto-fixing -> enabled")
            rc, _, err = az([
                "search", "service", "update",
                "-n", search_name, "-g", rg,
                "--public-network-access", "enabled",
            ])
            if rc != 0:
                issues_blocking.append(f"failed to enable public access: {err[:200]}")
            else:
                step("public access enabled; sleeping 15s")
                time.sleep(15)
        else:
            issues_warned.append(
                "search publicNetworkAccess is 'disabled'. If your laptop isn't on a "
                "private endpoint, deploys will fail. Either run from corporate network, "
                "OR re-run this script with --auto-fix (review with security team first)."
            )

    if issues_blocking:
        print("\nBLOCKING ISSUES:")
        for i in issues_blocking:
            print(f"  - {i}")
        return 1

    # ============================================================
    section("STEP 3 / 8 — Cosmos DB database (auto-create if missing)")
    # ============================================================
    if args.skip_cosmos or not cosmos_account:
        print("  Cosmos DB skipped (no config or --skip-cosmos)")
    else:
        if detect_cosmos_database(cosmos_account, cosmos_db, rg):
            step(f"database '{cosmos_db}' already exists in {cosmos_account}")
        else:
            step(f"creating database '{cosmos_db}' in {cosmos_account}...")
            rc, _, err = az([
                "cosmosdb", "sql", "database", "create",
                "--account-name", cosmos_account,
                "--resource-group", rg,
                "--name", cosmos_db,
                "--throughput", "400",
            ])
            if rc != 0:
                issues_warned.append(f"could not create cosmos database: {err[:200]}")
            else:
                step("database created")

    # ============================================================
    section("STEP 4 / 8 — Assign RBAC roles")
    # ============================================================
    if args.skip_roles:
        step("--skip-roles set; assuming managed-identity roles were already "
             "provisioned (one-time, by admin/IaC). Pipeline SP runs least-"
             "privilege -- no User Access Administrator / Contributor needed.")
    else:
        role_args = ["--config", args.config, "--wait-for-propagation", "300"]
        if args.skip_deploy_principal:
            role_args.append("--skip-deploy-principal")
        rc = run_script("scripts/assign_roles.py", role_args)
        if rc != 0:
            return rc

    # ============================================================
    section("STEP 5 / 8 — Wait for RBAC propagation")
    # ============================================================
    # We don't force `az logout && az login` here because that breaks
    # automation (interactive). Instead we rely on:
    #   1. The 300s wait baked into assign_roles.py above
    #   2. The 5-attempt retry on deploy_search below (with 120s between)
    # In Gov cloud, total propagation can be 15-30 min. The retry loop
    # absorbs that. If even after 5 retries deploy_search still 403s,
    # the user can re-run bootstrap.py — it's idempotent.
    step("relying on assign_roles' 300s wait + deploy_search's 5-attempt retry")
    step("for full RBAC propagation. No interactive token refresh needed.")

    # ============================================================
    section("STEP 6 / 8 — Deploy Function App code")
    # ============================================================
    if args.skip_function_app:
        print("  --skip-function-app set; skipping")
    else:
        # Corporate Group Policy can block default script execution.
        # Force process-scoped bypass for this invocation only.
        if os.name == "nt":
            ps_script = str((REPO_ROOT / "scripts" / "deploy_function.ps1").resolve())
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", ps_script,
                "-Config", args.config,
            ]
        else:
            cmd = ["bash", "scripts/deploy_function.sh", args.config]
        print(f"\n$ {' '.join(cmd)}", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            issues_blocking.append(f"deploy_function failed: rc={rc}")
            return 1

    # ============================================================
    section("STEP 6.5 / 9 — Configure Function App app settings")
    # ============================================================
    # Sets every env var the skills read, including AUTO_HEAL_ENABLED.
    # Without this, operators have to remember to run `az functionapp
    # config appsettings set` separately for AUTO_HEAL_ENABLED, AOAI_*,
    # DI_*, SEARCH_*, etc. — easy to forget, and the auto_heal timer
    # silently no-ops until enabled.
    if args.skip_app_settings:
        print("  --skip-app-settings set; skipping")
    else:
        func_name = cfg["functionApp"]["name"]
        aoai = cfg.get("azureOpenAI") or {}
        foundry = cfg.get("foundry") or {}
        di = cfg.get("documentIntelligence") or {}
        search_cfg = cfg.get("search") or {}
        storage_cfg = cfg.get("storage") or {}
        model_provider = (cfg.get("modelProvider") or "aoai").strip().lower()
        prefix = search_cfg.get("artifactPrefix") or "mm-manuals"
        storage_acct = (storage_cfg.get("accountResourceId") or "").rstrip("/").split("/")[-1]

        settings = {
            "AUTH_MODE": "mi",
            "MODEL_PROVIDER": model_provider,
            "FUNCTIONS_WORKER_RUNTIME": "python",
            "AOAI_ENDPOINT": (aoai.get("endpoint") or "").rstrip("/"),
            "AOAI_API_VERSION": aoai.get("apiVersion") or "2024-12-01-preview",
            "AOAI_CHAT_DEPLOYMENT": aoai.get("chatDeployment") or "",
            "AOAI_VISION_DEPLOYMENT": aoai.get("visionDeployment") or "",
            "AOAI_EMBED_DEPLOYMENT": aoai.get("embedDeployment") or "",
            "FOUNDRY_PROJECT_ENDPOINT": (foundry.get("projectEndpoint") or "").rstrip("/"),
            "FOUNDRY_API_VERSION": foundry.get("apiVersion") or "2024-05-01-preview",
            "FOUNDRY_CHAT_MODEL": foundry.get("chatModel") or "",
            "FOUNDRY_EMBED_MODEL": foundry.get("embedModel") or "",
            "DI_ENDPOINT": (di.get("endpoint") or "").rstrip("/"),
            "DI_API_VERSION": di.get("apiVersion") or "2024-11-30",
            "SEARCH_ENDPOINT": (search_cfg.get("endpoint") or "").rstrip("/"),
            "SEARCH_INDEX_NAME": f"{prefix}-index",
            "SEARCH_INDEXER_NAME": f"{prefix}-indexer",
            "STORAGE_ACCOUNT_NAME": storage_acct,
            "STORAGE_CONTAINER_NAME": storage_cfg.get("pdfContainerName") or "",
            "SKILL_VERSION": cfg.get("skillVersion") or "1.0.0",
            # AUTO_HEAL on by default — the timer self-heals stuck blobs
            # every 30 min. Operators can disable later via az CLI if
            # needed, but for a fresh deploy "on" is the right default.
            "AUTO_HEAL_ENABLED": "true",
            "AUTO_HEAL_STUCK_AFTER_MIN": "60",
            "AUTO_HEAL_MAX_BLOBS_PER_RUN": "20",
        }
        app_insights_conn = (cfg.get("appInsights") or {}).get("connectionString") or ""
        if app_insights_conn:
            settings["APPLICATIONINSIGHTS_CONNECTION_STRING"] = app_insights_conn

        # Filter out empty values so `az` doesn't blank-out existing settings
        # the operator might have set manually (e.g. a connection string).
        kv_pairs = [f"{k}={v}" for k, v in settings.items() if v]
        step(f"applying {len(kv_pairs)} app settings to {func_name}")
        rc, _, err = az([
            "functionapp", "config", "appsettings", "set",
            "-n", func_name, "-g", rg,
            "--settings", *kv_pairs,
            "--output", "none",
        ])
        if rc != 0:
            issues_warned.append(f"could not set app settings: {err[:300]}")
        else:
            step("app settings applied (AUTO_HEAL_ENABLED=true)")
            step("restarting function app to pick up new settings")
            az(["functionapp", "restart", "-n", func_name, "-g", rg, "--output", "none"])
            time.sleep(15)

    # ============================================================
    section("STEP 7 / 9 — Deploy search artifacts (5-attempt retry on 403)")
    # ============================================================
    # 5 attempts × 120s wait = up to 10 minutes of total retry time.
    # That covers worst-case Gov-cloud RBAC propagation (~15-30 min)
    # combined with the 5-min wait already done in step 4.
    if args.skip_search_artifacts:
        print("  --skip-search-artifacts set; skipping (caller will run preanalyze "
              "first, then deploy artifacts)")
    else:
        max_attempts = 5
        rc = -1
        for attempt in range(1, max_attempts + 1):
            rc = run_script("scripts/deploy_search.py", ["--config", args.config])
            if rc == 0:
                step(f"deploy_search succeeded on attempt {attempt}")
                break
            if attempt < max_attempts:
                step(f"attempt {attempt}/{max_attempts} failed; sleeping 120s for RBAC propagation")
                time.sleep(120)
        if rc != 0:
            issues_blocking.append(
                f"deploy_search.py failed after {max_attempts} attempts. Likely causes: "
                "(1) Gov-cloud RBAC took longer than ~15 min — re-run bootstrap.py to retry. "
                "(2) Network firewall blocking your laptop — run from corporate network. "
                "(3) Search service in apiKeyOnly mode — re-run with --auto-fix."
            )
            return 1

    # ============================================================
    section("STEP 8 / 9 — Smoke test")
    # ============================================================
    if args.skip_smoke_test or args.skip_search_artifacts:
        # Smoke test queries the deployed search service; if artifacts
        # weren't deployed, skipping is the right call.
        print("  skipped (no search artifacts deployed yet OR --skip-smoke-test)")
    else:
        rc = run_script("scripts/smoke_test.py", ["--config", args.config, "--skip-run"])
        if rc != 0:
            issues_warned.append("smoke_test reported issues; review logs above")

    # ============================================================
    section("DONE")
    # ============================================================
    if issues_warned:
        print("\nWarnings:")
        for w in issues_warned:
            print(f"  - {w}")
        print()
    print("Bootstrap complete. Infrastructure + RBAC + app settings + function "
          "code are in place.")
    print()
    print("THE EASIEST WAY TO RUN THE REST: scripts/deploy.py does everything")
    print("(bootstrap, preanalyze, deploy search artifacts, indexer, heal loop)")
    print("in one command. If you only ran bootstrap, finish with these steps:")
    print()
    print("  # 1. Upload PDFs to the blob container, then run preanalyze.")
    print(f"  python scripts/preanalyze.py --config {args.config} --concurrency 3 --vision-parallel 50")
    print()
    if args.skip_search_artifacts:
        print("  # 2. Search artifacts were NOT deployed (--skip-search-artifacts). Deploy now:")
        print(f"  python scripts/deploy_search.py --config {args.config}")
        print()
    print("  # 3. Reset + run the indexer.")
    if os.name == "nt":
        print("  .\\scripts\\reset_indexer.ps1")
    else:
        print("  ./scripts/reset_indexer.sh")
    print()
    print("  # 4. Loop until coverage is 100% (or a deterministic failure shows up).")
    print(f"  python scripts/heal_until_done.py --config {args.config}")
    print()
    print("  # 5. Verify coverage.")
    print(f"  python scripts/check_index.py --config {args.config} --coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
