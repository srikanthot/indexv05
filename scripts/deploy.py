"""
deploy.py — ONE command, end-to-end pipeline.

For the team. This is the only command they need to know:

    python scripts/deploy.py --config deploy.config.json --auto-fix

What it runs, in order:

  STEP 1.  bootstrap.py --skip-search-artifacts
           Provision RBAC, fix search auth, create Cosmos DB, deploy
           function code, configure Function App app settings (incl.
           AUTO_HEAL_ENABLED=true). Stops BEFORE deploying search
           artifacts so the indexer doesn't fire on an empty preanalyze
           cache.

  STEP 2.  preanalyze.py --incremental
           Run DI + GPT-5.1 Vision against every PDF in the container
           that doesn't yet have a complete cache. Writes _dicache/
           output.json files so the indexer can read them instantly.

  STEP 3.  deploy_search.py
           Deploy the index, skillset, indexer, datasource to Azure
           Search. Indexer's first run will hit the populated cache.

  STEP 4.  reset_indexer (resetdocs + run)
           Force a fresh full pass so the indexer reprocesses every
           blob with the just-deployed skillset + preanalyzed cache.

  STEP 5.  heal_until_done.py
           Loop: check coverage, bump stuck blobs, retrigger indexer,
           repeat until every PDF has a `summary` record OR the same
           stuck set repeats twice (deterministic failure → exit 1
           and operator must investigate).

  STEP 6.  check_index.py --coverage
           Final report: which PDFs have records, how many chunks per
           PDF, anything still missing.

Exit codes:
  0  every PDF in the container has a summary record in the index
  1  one of the steps failed (or heal_until_done detected a
     deterministic failure — see its output for the stuck PDF list)
  2  config file not found

Re-runnable: every step is idempotent. Re-running picks up wherever
the previous run failed. Use this as the nightly Jenkins job or the
one-shot command after a fresh provision.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def section(title: str) -> None:
    print()
    print("#" * 72)
    print(f"#  {title}")
    print("#" * 72)


def run_script(script: str, args: list[str]) -> int:
    py = sys.executable or "python"
    cmd = [py, str(REPO_ROOT / script)] + args
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def run_reset_indexer() -> int:
    """Cross-platform reset_indexer invocation."""
    if os.name == "nt":
        script = str(REPO_ROOT / "scripts" / "reset_indexer.ps1")
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script]
    else:
        script = str(REPO_ROOT / "scripts" / "reset_indexer.sh")
        cmd = ["bash", script]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ONE command end-to-end: bootstrap + preanalyze + indexer + heal.",
    )
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--auto-fix", action="store_true",
                    help="Pass-through to bootstrap.py. Allows auto-fixing "
                         "search service authOptions + publicNetworkAccess.")
    ap.add_argument("--skip-bootstrap", action="store_true",
                    help="Skip step 1 (bootstrap). Use if infrastructure + "
                         "RBAC + function code are already in place and you "
                         "only want to re-run preanalyze + indexer + heal.")
    ap.add_argument("--skip-preanalyze", action="store_true",
                    help="Skip step 2 (preanalyze). Use if all PDFs already "
                         "have a complete _dicache/ entry.")
    ap.add_argument("--force-preanalyze", action="store_true",
                    help="Re-run preanalyze for ALL PDFs even if a cache exists "
                         "(passes --force instead of --incremental). REQUIRED when "
                         "the cache was built with older code -- e.g. new table "
                         "structured-cells or a changed vision endpoint -- so the "
                         "cache is rebuilt with the current code. Slow (full DI + "
                         "vision), but it's the correct one-command path for that.")
    ap.add_argument("--skip-roles", action="store_true",
                    help="Pass --skip-roles to bootstrap so the pipeline does NOT "
                         "assign RBAC roles. Use in Jenkins where the SP lacks User "
                         "Access Administrator -- the managed-identity + SP roles are "
                         "provisioned ONCE, manually (assign_roles.py + self-granted "
                         "data roles). See docs/RBAC_LEAST_PRIVILEGE.md.")
    ap.add_argument("--skip-currency", action="store_true",
                    help="Skip the post-index mark_current_revisions pass. Not "
                         "recommended -- without it the chatbot's is_current_revision "
                         "filter excludes the whole corpus.")
    ap.add_argument("--skip-heal-loop", action="store_true",
                    help="Skip step 5 (heal_until_done). Use to deploy + "
                         "trigger indexer once, then accept whatever lands "
                         "without iterating.")
    ap.add_argument("--preanalyze-concurrency", type=int, default=3,
                    help="Parallel PDFs during preanalyze (default 3).")
    ap.add_argument("--preanalyze-vision-parallel", type=int, default=50,
                    help="Parallel vision calls per PDF during preanalyze "
                         "(default 50). Lower to 10-20 if you see AOAI 429s.")
    ap.add_argument("--heal-max-iterations", type=int, default=8,
                    help="Max heal iterations (default 8).")
    ap.add_argument("--heal-wait-minutes", type=int, default=30,
                    help="Per-iteration max wait for indexer to drain (default 30).")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 2

    print(f"Using config: {cfg_path}")
    print(f"Auto-fix:     {args.auto_fix}")
    print(f"Steps:        "
          f"{'bootstrap, ' if not args.skip_bootstrap else ''}"
          f"{'preanalyze, ' if not args.skip_preanalyze else ''}"
          f"deploy_search, reset_indexer"
          f"{', heal_loop' if not args.skip_heal_loop else ''}"
          f", check_index")

    # ============================================================
    section("STEP 1 / 6 — Bootstrap (infrastructure + RBAC + app settings + code)")
    # ============================================================
    if args.skip_bootstrap:
        print("  --skip-bootstrap set; skipping")
    else:
        boot_args = ["--config", args.config, "--skip-search-artifacts", "--skip-smoke-test"]
        if args.auto_fix:
            boot_args.append("--auto-fix")
        if args.skip_roles:
            boot_args.append("--skip-roles")
        rc = run_script("scripts/bootstrap.py", boot_args)
        if rc != 0:
            print(f"\n✗ bootstrap failed (rc={rc}). Fix the issue above and re-run.")
            return 1

    # ============================================================
    section("STEP 2 / 6 — Preanalyze (build DI + vision cache)")
    # ============================================================
    if args.skip_preanalyze:
        print("  --skip-preanalyze set; skipping")
    else:
        _pre_mode = "--force" if args.force_preanalyze else "--incremental"
        print(f"  preanalyze mode: {_pre_mode}")
        rc = run_script("scripts/preanalyze.py", [
            "--config", args.config,
            _pre_mode,
            "--concurrency", str(args.preanalyze_concurrency),
            "--vision-parallel", str(args.preanalyze_vision_parallel),
        ])
        if rc != 0:
            print(f"\n✗ preanalyze failed (rc={rc}). "
                  "Check the output above; fix and re-run deploy.py.")
            return 1

    # ============================================================
    section("STEP 3 / 6 — Deploy search artifacts (index/skillset/indexer/datasource)")
    # ============================================================
    rc = run_script("scripts/deploy_search.py", ["--config", args.config])
    if rc != 0:
        print(f"\n✗ deploy_search failed (rc={rc}). "
              "If 403, RBAC may need more propagation — wait 5 min and re-run.")
        return 1

    # ============================================================
    section("STEP 4 / 6 — Reset + run indexer (fresh pass over cached blobs)")
    # ============================================================
    rc = run_reset_indexer()
    if rc != 0:
        # reset_indexer is best-effort — if it fails (e.g. indexer already
        # running), the next step's heal loop will trigger it again.
        print(f"  reset_indexer exited rc={rc} (continuing; heal loop will retrigger)")

    # ============================================================
    section("STEP 5 / 6 — Heal until done (loop until coverage = 100%)")
    # ============================================================
    if args.skip_heal_loop:
        print("  --skip-heal-loop set; skipping")
    else:
        rc = run_script("scripts/heal_until_done.py", [
            "--config", args.config,
            "--max-iterations", str(args.heal_max_iterations),
            "--wait-minutes", str(args.heal_wait_minutes),
        ])
        if rc == 0:
            print("\n  heal_until_done: every PDF has a summary record ✓")
        elif rc == 1:
            print("\n  heal_until_done exited 1 — see its output above:")
            print("    • If 'GAVE UP after N iterations' → some PDFs still need time; "
                  "re-run deploy.py later.")
            print("    • If 'FAIL: same N PDF(s) stuck across 2 consecutive iterations' "
                  "→ those PDFs are deterministically failing. Investigate with:")
            print(f"      python scripts/check_index.py --config {args.config} --bad")
            print("    • Common cause on big PDFs: Function App OOM (exit code 137). "
                  "Bump the App Service Plan to a tier with more memory.")
            # Don't return immediately — still want to print final coverage below.

    # ============================================================
    section("STEP 5.5 — Mark current revisions (currency filter)")
    # ============================================================
    # After the indexer populates records, set is_current_revision so the
    # chatbot's `is_current_revision eq true` filter returns the current manuals.
    # Without this pass the filter matches NOTHING and the whole corpus looks
    # empty. Idempotent + best-effort: a failure here does not fail the deploy.
    if args.skip_currency:
        print("  --skip-currency set; skipping (chatbot currency filter may exclude all docs)")
    else:
        cur_rc = run_script("scripts/mark_current_revisions.py",
                            ["--config", args.config, "--apply"])
        if cur_rc == 0:
            print("  is_current_revision set for current revisions ✓")
        else:
            print(f"  mark_current_revisions exited rc={cur_rc} (continuing; "
                  "re-run it if the currency filter looks empty)")

    # ============================================================
    section("STEP 6 / 6 — Coverage report")
    # ============================================================
    cov_rc = run_script("scripts/check_index.py", [
        "--config", args.config,
        "--coverage",
    ])

    print()
    print("=" * 72)
    if not args.skip_heal_loop and rc == 0 and cov_rc == 0:
        print("  ✓ DEPLOY COMPLETE — every PDF is indexed.")
        print("=" * 72)
        return 0
    if not args.skip_heal_loop and rc == 1:
        print("  ✗ DEPLOY INCOMPLETE — some PDFs are still not indexed.")
        print("=" * 72)
        print("  Re-running deploy.py may pick up transient failures.")
        print("  For deterministic failures, see the heal_until_done output above.")
        return 1
    print("  Pipeline completed; review coverage report above for any gaps.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
