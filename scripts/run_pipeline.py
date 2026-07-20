"""
End-to-end pipeline orchestrator.

One command runs the full operational loop:
  1. reconcile.py        --- detect added/edited/deleted PDFs, purge stale data
  2. preanalyze.py --incremental   --- run DI + Vision for any new/edited PDF
  3. wait for indexer    --- poll status until idle (success or known error)
  4. check_index.py --coverage --write-status   --- report state, persist to Cosmos

This is what Jenkins runs nightly and what an operator runs after
uploading new PDFs. It is safe to run repeatedly; every step is
idempotent.

Usage:
    python scripts/run_pipeline.py --config deploy.config.json
    python scripts/run_pipeline.py --config deploy.config.json --skip-reconcile
    python scripts/run_pipeline.py --config deploy.config.json --max-wait-minutes 60
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cosmos_writer  # noqa: E402

API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=REPO_ROOT,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def run_step(label: str, cmd: list[str], allow_fail: bool = False) -> tuple[int, str]:
    """Run a subprocess step and stream its output. Returns (returncode, output)."""
    print()
    print("=" * 70)
    print(f"  STEP: {label}")
    print(f"  CMD:  {' '.join(cmd)}")
    print("=" * 70)
    proc = subprocess.run(cmd, capture_output=False, text=True)
    rc = proc.returncode
    if rc != 0 and not allow_fail:
        print(f"\nSTEP FAILED: {label} (exit {rc})")
    return rc, ""


def _parse_iso(s: str):
    """Parse an ISO-8601 timestamp (tolerant of trailing 'Z') -> datetime or None."""
    from datetime import datetime
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def trigger_indexer(endpoint: str, indexer_name: str) -> bool:
    """Kick an on-demand indexer run. Returns True if a run is now in flight.
    A 409 means the indexer is already running, which is fine — the wait step
    below will pick it up. Any other non-2xx is a warning, not fatal."""
    url = f"{endpoint}/indexers/{indexer_name}/run?api-version={API_VERSION}"
    token = DefaultAzureCredential().get_token(SEARCH_SCOPE).token
    print()
    print("=" * 70)
    print(f"  STEP: trigger indexer {indexer_name} (on-demand run)")
    print("=" * 70)
    with httpx.Client(timeout=30.0) as c:
        resp = c.post(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code in (200, 202):
        print(f"  triggered (HTTP {resp.status_code})")
        return True
    if resp.status_code == 409:
        print("  already running (HTTP 409) — will wait for the in-flight run")
        return True
    print(f"  WARNING: trigger returned HTTP {resp.status_code}: {resp.text[:300]}")
    return False


def wait_for_indexer_idle(endpoint: str, indexer_name: str, max_minutes: int,
                          fresh_after_iso: str | None = None) -> dict:
    """Poll indexer status until it's not 'inProgress'. Returns the
    final lastResult dict (may indicate success, transientFailure, or
    persistentFailure)."""
    url = f"{endpoint}/indexers/{indexer_name}/status?api-version={API_VERSION}"
    cred = DefaultAzureCredential()
    deadline = time.time() + max_minutes * 60
    backoff = 15.0
    last_status = None
    fresh_dt = _parse_iso(fresh_after_iso) if fresh_after_iso else None

    print()
    print("=" * 70)
    print(f"  STEP: wait for indexer {indexer_name} to settle")
    print(f"  Polling every ~{int(backoff)}s, max {max_minutes} min")
    print("=" * 70)

    while time.time() < deadline:
        token = cred.get_token(SEARCH_SCOPE).token
        with httpx.Client(timeout=30.0) as c:
            resp = c.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            print(f"  status fetch returned {resp.status_code}; retrying")
            time.sleep(backoff)
            continue
        body = resp.json()
        last_status = body.get("status") or "unknown"
        last_result = body.get("lastResult") or {}
        last_run_status = last_result.get("status") or "unknown"
        items = last_result.get("itemsProcessed", "?")
        run_start = last_result.get("startTime") or ""
        is_fresh = True
        if fresh_dt is not None:
            rs = _parse_iso(run_start)
            is_fresh = bool(rs and rs >= fresh_dt)
        print(f"  indexer={last_status}  lastResult={last_run_status}  items={items}")
        if last_status != "inProgress" and last_run_status != "inProgress" and is_fresh:
            return last_result
        time.sleep(backoff)

    print(f"  timeout: indexer still {last_status} after {max_minutes} min")
    return {"status": "timeout", "itemsProcessed": 0}


def _run_heal_passes(cfg: dict, config_path: str, max_passes: int,
                     wait_minutes: int, endpoint: str, indexer_name: str,
                     py: str) -> dict:
    """Audit + repair loop. Each pass:
       1. Read current per-PDF state from Cosmos pdf_state.
       2. Identify PDFs with status in {partial, not_started, failed}.
       3. For each, delete its cache blobs (forces fresh DI on next preanalyze).
       4. Run preanalyze --incremental (which will pick up the now-empty cache).
       5. Wait for the indexer to drain.
       6. Re-run check_index --coverage --write-status to refresh state.
       Stop when a pass finds nothing left to heal, or max_passes reached.

       Returns a dict of per-pass stats."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cosmos_writer  # noqa: WPS433
    from preanalyze import delete_blob, list_cache_blobs

    out = {"passes": [], "error": None}
    if not cosmos_writer._is_configured(cfg):
        out["error"] = "auto-heal requires Cosmos DB configuration"
        return out

    for pass_idx in range(max_passes):
        try:
            db = cosmos_writer._get_database_client(cfg)
            container = cosmos_writer._ensure_container(
                db, cosmos_writer.PDF_STATE_CONTAINER,
            )
            incomplete = []
            for item in container.read_all_items():
                status = (item.get("status") or "").lower()
                sf = item.get("source_file") or item.get("id")
                if status in ("partial", "not_started", "failed") and sf:
                    incomplete.append(sf)
        except Exception as exc:
            out["error"] = f"cosmos read failed: {exc}"
            return out

        print()
        print("=" * 70)
        print(f"  HEAL PASS {pass_idx + 1}/{max_passes}: {len(incomplete)} incomplete PDFs")
        print("=" * 70)

        if not incomplete:
            out["passes"].append({"pass": pass_idx + 1, "incomplete": 0, "healed": 0})
            print("  Nothing to heal. Done.")
            return out

        # Purge stale cache for each incomplete PDF so preanalyze re-runs
        # fully (rather than thinking it's already cached).
        all_cache = list_cache_blobs(cfg)
        purged = 0
        for pdf in incomplete:
            prefix = f"_dicache/{pdf}."
            for b in all_cache:
                if b.startswith(prefix):
                    try:
                        if delete_blob(cfg, b):
                            purged += 1
                    except Exception as exc:
                        print(f"  warn: could not delete {b}: {exc}", flush=True)
        print(f"  Purged {purged} cache blobs for {len(incomplete)} PDFs")

        # Re-run preanalyze (will re-process just the purged ones).
        rc, _ = run_step(
            f"heal pass {pass_idx + 1}: preanalyze --incremental",
            [py, str(REPO_ROOT / "scripts" / "preanalyze.py"),
             "--config", config_path, "--incremental"],
            allow_fail=True,
        )

        # Wait for the indexer to pick up the new cache.
        wait_for_indexer_idle(endpoint, indexer_name, max(wait_minutes // 2, 10))

        # Refresh coverage / pdf_state.
        run_step(
            f"heal pass {pass_idx + 1}: check_index --coverage --write-status",
            [py, str(REPO_ROOT / "scripts" / "check_index.py"),
             "--config", config_path, "--coverage", "--write-status",
             "--triggered-by", f"auto-heal-pass-{pass_idx + 1}"],
            allow_fail=True,
        )

        out["passes"].append({
            "pass": pass_idx + 1,
            "incomplete": len(incomplete),
            "purged_blobs": purged,
            "preanalyze_exit": rc,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Run full indexing pipeline")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--skip-reconcile", action="store_true")
    ap.add_argument("--skip-preanalyze", action="store_true")
    ap.add_argument("--skip-wait", action="store_true",
                    help="Don't wait for the indexer; just report current state")
    ap.add_argument("--max-wait-minutes", type=int, default=60)
    ap.add_argument("--max-purges", type=int, default=2)
    ap.add_argument("--triggered-by", default="manual",
                    help="Free-text label for the run record (jenkins-cron, jenkins-manual, manual, ...)")
    ap.add_argument("--trigger-indexer", action="store_true",
                    help="Explicitly kick an on-demand indexer run after preanalyze "
                         "instead of waiting up to ~15 min for the indexer's own "
                         "schedule. Jenkins passes this.")
    ap.add_argument("--auto-heal", action="store_true",
                    help="After the main pipeline, audit coverage. If any PDF is "
                         "in PARTIAL or NOT_STARTED state, automatically purge its "
                         "stale cache and re-run preanalyze for it. Repeats up to "
                         "--heal-passes times.")
    ap.add_argument("--heal-passes", type=int, default=2,
                    help="Max number of heal passes (default 2). Each pass: "
                         "identify incomplete PDFs, purge their cache, re-run "
                         "preanalyze on them, wait for indexer.")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    indexer_name = f"{prefix}-indexer"

    pipeline_started_at = _now_iso()
    pipeline_t0 = time.time()
    git_sha = _git_sha()
    py = sys.executable or "python"

    overall_rc = 0
    step_results: dict[str, dict] = {}

    # 1. Reconcile.
    if args.skip_reconcile:
        step_results["reconcile"] = {"skipped": True}
    else:
        rc, _ = run_step(
            "reconcile",
            [py, str(REPO_ROOT / "scripts" / "reconcile.py"),
             "--config", args.config,
             "--max-purges", str(args.max_purges)],
            allow_fail=True,
        )
        step_results["reconcile"] = {"exit_code": rc}
        # Reconcile rc=2 means "would-purge exceeds cap" — that's a soft
        # gate, not a failure. Operator must decide. We continue but
        # mark the pipeline as needing attention.
        if rc not in (0, 2):
            overall_rc = max(overall_rc, rc)

    # 2. Preanalyze (incremental).
    if args.skip_preanalyze:
        step_results["preanalyze"] = {"skipped": True}
    else:
        rc, _ = run_step(
            "preanalyze --incremental",
            [py, str(REPO_ROOT / "scripts" / "preanalyze.py"),
             "--config", args.config,
             "--incremental"],
            allow_fail=True,
        )
        step_results["preanalyze"] = {"exit_code": rc}
        if rc != 0:
            overall_rc = max(overall_rc, rc)

    # 2b. Optionally kick the indexer now so we get a FRESH run (that starts
    # after pipeline_started_at) instead of waiting up to ~15 min for the
    # indexer's own schedule. The wait step below requires a fresh run.
    if args.trigger_indexer:
        triggered = trigger_indexer(endpoint, indexer_name)
        step_results["trigger_indexer"] = {"triggered": triggered}
    else:
        step_results["trigger_indexer"] = {"skipped": True}

    # 3. Wait for the indexer to drain. If we didn't trigger above, the
    # indexer's own 15-minute schedule should pick up new caches
    # automatically; we just watch for it to finish whatever batch it's on.
    if args.skip_wait:
        step_results["wait_indexer"] = {"skipped": True}
    else:
        # Require a run that STARTED after this pipeline began, so we don't
        # accept a stale "success" from before our preanalyze wrote new caches.
        last = wait_for_indexer_idle(endpoint, indexer_name, args.max_wait_minutes,
                                     fresh_after_iso=pipeline_started_at)
        step_results["wait_indexer"] = {
            "last_status": last.get("status"),
            "items_processed": last.get("itemsProcessed", 0),
            "errors": len(last.get("errors") or []),
            "warnings": len(last.get("warnings") or []),
        }

    # 4. Coverage + status persistence. check_index.py --write-status
    # both prints coverage AND writes a run_history + pdf_state batch
    # to Cosmos.
    rc, _ = run_step(
        "check_index --coverage --write-status",
        [py, str(REPO_ROOT / "scripts" / "check_index.py"),
         "--config", args.config,
         "--coverage", "--write-status",
         "--triggered-by", args.triggered_by],
        allow_fail=True,
    )
    step_results["check_index"] = {"exit_code": rc}
    if rc != 0:
        overall_rc = max(overall_rc, rc)

    # 4.2. Currency pass: set is_current_revision on the newest revision of each
    # manual. Nightly ingests can add/replace docs, so re-run every night —
    # otherwise the chatbot's `is_current_revision eq true` filter goes stale or
    # empty. Best-effort: a failure here does not fail the run.
    rc_cur, _ = run_step(
        "mark_current_revisions --apply",
        [py, str(REPO_ROOT / "scripts" / "mark_current_revisions.py"),
         "--config", args.config, "--apply"],
        allow_fail=True,
    )
    step_results["mark_current_revisions"] = {"exit_code": rc_cur}

    # 4.5. Optional auto-heal: detect any PDF the indexer didn't fully
    # process (partial chunks, no summary, missing DI cache) and force a
    # clean re-process for just those files. Bounded by --heal-passes
    # so a permanently-broken PDF can't loop forever.
    if args.auto_heal:
        heal_results = _run_heal_passes(
            cfg, args.config, args.heal_passes, args.max_wait_minutes,
            endpoint, indexer_name, py,
        )
        step_results["auto_heal"] = heal_results
        if heal_results.get("error"):
            overall_rc = max(overall_rc, 1)

    # 5. Validation gate: check that required fields are populated
    # across all record types. This catches silent partial indexing.
    rc, _ = run_step(
        "validate_index --strict",
        [py, str(REPO_ROOT / "scripts" / "validate_index.py"),
         "--config", args.config, "--strict", "--sample", "10"],
        allow_fail=True,
    )
    step_results["validate_index"] = {"exit_code": rc}
    if rc != 0:
        overall_rc = max(overall_rc, rc)

    # 6. Page-coordinate integrity gate: required for citation accuracy.
    # This blocks go-live if chunk page mappings are inconsistent
    # (non-contiguous page lists, start/end page mismatch against list).
    rc, _ = run_step(
        "validate_page_coordinates --strict",
        [py, str(REPO_ROOT / "scripts" / "validate_page_coordinates.py"),
         "--config", args.config, "--strict"],
        allow_fail=True,
    )
    step_results["validate_page_coordinates"] = {"exit_code": rc}
    if rc != 0:
        overall_rc = max(overall_rc, rc)

    # 7. Full retrievable-field quality audit (report + trend visibility).
    rc, _ = run_step(
        "audit_all_retrievable_fields",
        [py, str(REPO_ROOT / "scripts" / "audit_all_retrievable_fields.py"),
         "--config", args.config],
        allow_fail=True,
    )
    step_results["audit_all_retrievable_fields"] = {"exit_code": rc}
    if rc != 0:
        overall_rc = max(overall_rc, rc)

    # 8. Pipeline-level run record.
    cosmos_writer.write_run_record(cfg, {
        "run_type": "full_pipeline",
        "triggered_by": args.triggered_by,
        "git_sha": git_sha,
        "started_at": pipeline_started_at,
        "duration_seconds": int(time.time() - pipeline_t0),
        "steps": step_results,
        "exit_code": overall_rc,
    })

    print()
    print("=" * 70)
    print(f"  PIPELINE DONE (exit {overall_rc}, "
          f"{int(time.time() - pipeline_t0)}s, "
          f"{sum(1 for s in step_results.values() if s.get('skipped'))} skipped)")
    print("=" * 70)
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())

