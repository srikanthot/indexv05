"""
Resume an in-progress indexer backfill to completion — WITHOUT resetting it.

WHY THIS EXISTS
    Azure Search enforces a hard 120-minute execution quota PER indexer run.
    With heavy documents only a handful finish per run, then the run stops with
    lastResult.status = "transientFailure" and the message
        "... execution time quota of 120 minutes has been reached ..."
    The indexer resumes from its change-tracking high-water mark on the NEXT
    run, so the backfill just needs to be re-triggered until the backlog drains.

    This driver does exactly that and NOTHING else. It never calls resetdocs,
    never bumps blob metadata, never heals, never re-runs preanalyze — so it can
    never throw away the docs already indexed. It is safe to run repeatedly; each
    invocation CONTINUES the backfill (it does not restart it).

TERMINATION
    - DONE      : a run completes with status "success" -> the backlog drained.
    - BUDGET    : --max-rounds or --max-hours reached while still advancing ->
                  exits 0; just run the job again to continue.
    - STUCK     : two consecutive runs process 0 documents -> exits 1 (a single
                  document the indexer cannot get past; inspect it separately).

Usage:
    python scripts/backfill_indexer.py --config deploy.config.json
    python scripts/backfill_indexer.py --config deploy.config.json --max-rounds 4 --max-hours 8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"  # Azure US Gov search scope


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _trigger_run(endpoint: str, indexer: str) -> bool:
    """Kick an on-demand run (NO resetdocs). 409 = already running, which is
    fine — we just wait for the in-flight run. Returns True if a run is (or will
    be) in flight, False on an unexpected error."""
    url = f"{endpoint}/indexers/{indexer}/run?api-version={API_VERSION}"
    with httpx.Client(timeout=30.0) as c:
        resp = c.post(url, headers={"Authorization": f"Bearer {_token()}"})
    if resp.status_code in (200, 202):
        print("  triggered on-demand run (no reset)")
        return True
    if resp.status_code == 409:
        print("  indexer already running (409) — will wait for it")
        return True
    print(f"  WARNING: run trigger returned {resp.status_code}: {resp.text[:200]}")
    return False


def _wait_until_idle(endpoint: str, indexer: str, max_minutes: int) -> dict:
    """Poll status until the indexer is not running. Returns lastResult
    ({status, itemsProcessed, itemsFailed, errorMessage, startTime, endTime})."""
    url = f"{endpoint}/indexers/{indexer}/status?api-version={API_VERSION}"
    cred = DefaultAzureCredential()
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as c:
            resp = c.get(url, headers={"Authorization": f"Bearer {cred.get_token(SEARCH_SCOPE).token}"})
        if resp.status_code != 200:
            print(f"  status fetch {resp.status_code}; retrying in 20s")
            time.sleep(20)
            continue
        body = resp.json()
        top = body.get("status") or "unknown"
        last = body.get("lastResult") or {}
        last_status = last.get("status") or "unknown"
        items = last.get("itemsProcessed", "?")
        print(f"  indexer={top}  lastRun={last_status}  itemsProcessed={items}")
        if top != "inProgress" and last_status != "inProgress":
            return last
        time.sleep(20)
    return {"status": "timeout", "itemsProcessed": 0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--max-rounds", type=int, default=6,
                    help="Max indexer runs to drive this invocation (default 6).")
    ap.add_argument("--max-hours", type=float, default=8.0,
                    help="Wall-clock budget for this invocation, hours "
                         "(default 8; keep under the Jenkins job timeout).")
    ap.add_argument("--per-run-minutes", type=int, default=150,
                    help="Max minutes to wait for a single run to settle "
                         "(default 150; must exceed the 120-min quota).")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    indexer = f"{prefix}-indexer"

    print("=" * 70)
    print("  BACKFILL DRIVER — resume the indexer without resetting it")
    print(f"  endpoint={endpoint}")
    print(f"  indexer ={indexer}")
    print(f"  budget  = {args.max_rounds} rounds / {args.max_hours} h")
    print("=" * 70)

    t0 = time.time()
    total_processed = 0
    zero_streak = 0

    for rnd in range(1, args.max_rounds + 1):
        elapsed_h = (time.time() - t0) / 3600.0
        if elapsed_h >= args.max_hours:
            print(f"\n[BUDGET] time budget ({args.max_hours} h) reached before round {rnd}.")
            break

        print(f"\n----- round {rnd}/{args.max_rounds}  (elapsed {elapsed_h:.2f} h) -----")
        if not _trigger_run(endpoint, indexer):
            print("  could not trigger a run; aborting this invocation.")
            return 1

        last = _wait_until_idle(endpoint, indexer, args.per_run_minutes)
        status = last.get("status") or "unknown"
        items = last.get("itemsProcessed") or 0
        failed = last.get("itemsFailed") or 0
        err = (last.get("errorMessage") or "").strip()
        total_processed += items if isinstance(items, int) else 0

        print(f"  round result: status={status}  itemsProcessed={items}  "
              f"itemsFailed={failed}  cumulative={total_processed}")
        if err:
            print(f"  lastRun error: {err[:200]}")

        if status == "success":
            print(f"\n[DONE] indexer run completed with status=success after "
                  f"{rnd} round(s) — the backlog is drained. "
                  f"Total docs processed this invocation: {total_processed}.")
            return 0

        if status == "timeout":
            print("\n[BUDGET] a run did not settle within --per-run-minutes; "
                  "exiting. Re-run the job to continue (nothing was reset).")
            return 0

        if isinstance(items, int) and items == 0:
            zero_streak += 1
            if zero_streak >= 2:
                print("\n[STUCK] two consecutive runs processed 0 documents. The "
                      "indexer cannot get past the next document. Nothing was "
                      "reset; inspect that document / the indexer errors above.")
                return 1
        else:
            zero_streak = 0

    print(f"\n[BUDGET] drove {args.max_rounds} round(s) / "
          f"{(time.time() - t0) / 3600.0:.2f} h; still advancing "
          f"({total_processed} docs processed this invocation). NOT complete — "
          f"run this Jenkins job again to continue (the high-water mark is "
          f"preserved; no reset occurs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
