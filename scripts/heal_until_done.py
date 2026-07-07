"""
heal_until_done.py — foreground heal loop that runs the indexer to 100%.
 
Background: the function-app auto_heal_timer bumps stuck blobs every 30
min. It works, but it has no completion signal — operators have to
manually poll coverage and decide when to stop. This script is the
foreground CI loop:
 
    while stuck PDFs exist:
        bump metadata on each stuck blob
        clear indexer failed-items state for those blobs
        trigger an indexer run
        wait for the indexer to go idle
        sleep a grace period for skills to settle
        re-check coverage
 
Exit codes:
    0  every container PDF has a `summary` record in the index
    1  same stuck set repeated 2 iterations in a row (deterministic
       failure — manual investigation needed), OR max iterations
       elapsed with stuck PDFs remaining
 
Usage:
    python scripts/heal_until_done.py --config deploy.config.json
    python scripts/heal_until_done.py --config deploy.config.json \\
        --max-iterations 8 --wait-minutes 30 --grace-minutes 5
 
Auth:
    DefaultAzureCredential for Azure AI Search (REST PUTs / GETs).
    az CLI in `--auth-mode login` for storage blob operations
    (matches force_reindex_blobs.ps1 — keeps the same operator-identity
    requirements).
"""
 
from __future__ import annotations
 
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
 
def _az_bin() -> str:
    """`az.cmd` on Windows, `az` elsewhere. Python's subprocess can't
    locate the .cmd shim without the explicit suffix on Windows."""
    return "az.cmd" if os.name == "nt" else "az"
 
 
def _az(args: list[str], *, check: bool = True) -> str:
    cmd = [_az_bin()] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:5])} failed: {r.stderr[:400]}")
    return r.stdout.strip()
 
 
def _storage_endpoint_suffix(search_endpoint: str) -> str:
    """Mirror force_reindex_blobs.ps1: Gov vs. Public Azure detection."""
    if ".azure.us" in search_endpoint:
        return "blob.core.usgovcloudapi.net"
    return "blob.core.windows.net"
 
 
def _summary_pdfs(client: httpx.Client, endpoint: str, index_name: str,
                  token: str) -> set[str]:
    """Return source_file values that already have a `summary` record."""
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    body = {
        "search": "*",
        "filter": "record_type eq 'summary'",
        "select": "source_file",
        "top": 1000,
    }
    resp = client.post(url, json=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    resp.raise_for_status()
    out: set[str] = set()
    for row in resp.json().get("value") or []:
        sf = row.get("source_file")
        if sf:
            out.add(sf)
    return out
 
 
def _pdf_blobs(account: str, container: str) -> list[str]:
    """List PDF blob names in the container. Uses az CLI login auth so
    operator/CI identity is honored (same as force_reindex_blobs.ps1)."""
    raw = _az([
        "storage", "blob", "list",
        "--account-name", account,
        "--container-name", container,
        "--auth-mode", "login",
        "--query", "[?ends_with(name, '.pdf')].name",
        "-o", "json",
    ])
    return json.loads(raw) if raw else []
 
 
def _bump_blob_metadata(account: str, container: str, blob_name: str,
                       stamp: str) -> bool:
    """Set blob metadata `force_reindex=<stamp>`. Bumping metadata
    advances lastModified, which the Search indexer treats as a fresh
    blob and re-processes.
 
    PRESERVES existing user-metadata on the blob (operationalarea,
    functionalarea, doctype, etc.). The `az storage blob metadata
    update --metadata` command REPLACES the entire metadata dict, so
    we must read existing keys first and merge force_reindex into
    them before writing back. Otherwise every heal iteration wipes
    classification tags the operator set for retrieval filtering.
    """
    try:
        # 1. Read existing metadata.
        existing_raw = _az([
            "storage", "blob", "metadata", "show",
            "--account-name", account,
            "--container-name", container,
            "--name", blob_name,
            "--auth-mode", "login",
            "-o", "json",
        ], check=True)
        try:
            existing = json.loads(existing_raw) if existing_raw else {}
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}
 
        # 2. Merge force_reindex without dropping any existing keys.
        existing["force_reindex"] = stamp
 
        # 3. Write back the full dict (one key=value arg per entry).
        meta_args = [f"{k}={v}" for k, v in existing.items()]
        _az([
            "storage", "blob", "metadata", "update",
            "--account-name", account,
            "--container-name", container,
            "--name", blob_name,
            "--metadata", *meta_args,
            "--auth-mode", "login",
        ], check=True)
        return True
    except RuntimeError as exc:
        print(f"    metadata update failed for {blob_name}: {exc}", flush=True)
        return False
 
 
def _reset_failed_items(client: httpx.Client, endpoint: str, indexer_name: str,
                        blob_urls: list[str], token: str) -> None:
    """Clear failed-items state for the given documents so the indexer
    retries them. Non-fatal: metadata bump alone usually triggers
    reprocessing even if resetdocs fails."""
    if not blob_urls:
        return
    url = f"{endpoint}/indexers/{indexer_name}/resetdocs?api-version={API_VERSION}"
    try:
        r = client.post(url, json={"datasourceDocumentIds": blob_urls}, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        r.raise_for_status()
        print("  resetdocs: accepted", flush=True)
    except Exception as exc:
        print(f"  resetdocs: failed ({exc}); continuing — bump alone usually retriggers",
              flush=True)
 
 
def _trigger_indexer_run(client: httpx.Client, endpoint: str, indexer_name: str,
                          token: str) -> None:
    """POST /indexers/<name>/run. Tolerate 409 = already running."""
    url = f"{endpoint}/indexers/{indexer_name}/run?api-version={API_VERSION}"
    r = client.post(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Length": "0",
    })
    if r.status_code == 409:
        print("  run: indexer already running; bumped blobs will be picked up",
              flush=True)
        return
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"indexer run failed: {r.status_code} {r.text[:300]}")
    print("  run: triggered", flush=True)
 
 
def _reset_indexer(client: httpx.Client, endpoint: str, indexer_name: str,
                   token: str) -> None:
    """POST /indexers/<name>/reset to clear a stuck run's change-tracking state."""
    url = f"{endpoint}/indexers/{indexer_name}/reset?api-version={API_VERSION}"
    r = client.post(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Length": "0",
    })
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"indexer reset failed: {r.status_code} {r.text[:300]}")
    print("  reset: accepted", flush=True)
 
 
def _wait_for_idle(client: httpx.Client, endpoint: str, indexer_name: str,
                    cred: DefaultAzureCredential, *, max_minutes: int,
                    reset_after_zero_minutes: int) -> dict[str, bool]:
    """Block until the indexer is no longer reporting inProgress, or the
    deadline elapses. Mirrors run_pipeline.wait_for_indexer_idle."""
    url = f"{endpoint}/indexers/{indexer_name}/status?api-version={API_VERSION}"
    deadline = time.time() + max_minutes * 60
    backoff = 30
    zero_started_at: float | None = None
    while time.time() < deadline:
        # Re-acquire token every poll so long waits don't hit 401 expiry loops.
        token = cred.get_token(SEARCH_SCOPE).token
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            print(f"  status fetch returned {r.status_code}; retrying", flush=True)
            time.sleep(backoff)
            continue
        body = r.json()
        top_status = (body.get("status") or "unknown").lower()
        last = body.get("lastResult") or {}
        last_status = (last.get("status") or "unknown").lower()
        items = int(last.get("itemsProcessed") or 0)
        print(f"  indexer={top_status}  lastResult={last_status}  items={items}",
              flush=True)
 
        if top_status == "running" and last_status == "inprogress" and items == 0:
            if zero_started_at is None:
                zero_started_at = time.time()
            stagnant_secs = time.time() - zero_started_at
            if reset_after_zero_minutes > 0 and stagnant_secs >= reset_after_zero_minutes * 60:
                print("  detected stagnant in-progress run with 0 items; "
                      "requesting indexer reset/restart", flush=True)
                return {"idle": False, "stagnant": True}
        else:
            zero_started_at = None
 
        if top_status != "inprogress" and last_status != "inprogress":
            return {"idle": True, "stagnant": False}
        time.sleep(backoff)
    print(
        f"  timeout: indexer still in-progress after {max_minutes} min "
        "(continuing to next iteration)",
        flush=True,
    )
    return {"idle": False, "stagnant": False, "timed_out_running": True}
 
 
def _now_stamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
 
 
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Loop until every container PDF has a summary record in the index.",
    )
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--max-iterations", type=int, default=8,
                    help="Hard cap on iterations (default 8). Worst-case wall "
                         "time = max-iterations * (wait-minutes + grace-minutes).")
    ap.add_argument("--wait-minutes", type=int, default=90,
                    help="Max minutes to wait for the indexer to go idle each "
                         "iteration (default 90). Big PDFs need 5-15 min each "
                         "through the full skill pipeline.")
    ap.add_argument("--grace-minutes", type=int, default=5,
                    help="Extra sleep after the indexer goes idle, so downstream "
                         "skill records have time to project into the index "
                         "(default 5). Set 0 to skip.")
    ap.add_argument("--max-per-iteration", type=int, default=20,
                    help="Cap the number of stuck blobs bumped per iteration "
                         "(default 20). Mirrors force_reindex_blobs.ps1.")
    ap.add_argument("--reset-after-zero-minutes", type=int, default=0,
                    help="If indexer stays running/inProgress with 0 items "
                         "for this many minutes, force indexer reset + rerun "
                         "(default 0 = disabled).")
    ap.add_argument("--wait-only-sleep-seconds", type=int, default=180,
                    help="When indexer is still running after wait timeout, "
                         "sleep this long and continue waiting WITHOUT rebumping "
                         "metadata (default 180).")
    ap.add_argument("--repeat-stuck-fail-streak", type=int, default=4,
                    help="Fail only after this many unchanged stuck-set "
                         "iterations (default 4).")
    args = ap.parse_args()
 
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
 
    search_ep = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    indexer_name = f"{prefix}-indexer"
    index_name = f"{prefix}-index"
    storage_acct = cfg["storage"]["accountResourceId"].rstrip("/").split("/")[-1]
    container = cfg["storage"]["pdfContainerName"]
    storage_suffix = _storage_endpoint_suffix(search_ep)
 
    print(f"Search:   {search_ep}")
    print(f"Index:    {index_name}")
    print(f"Indexer:  {indexer_name}")
    print(f"Storage:  {storage_acct}/{container}")
    print(f"Plan:     up to {args.max_iterations} iterations, "
          f"{args.wait_minutes}m idle wait + {args.grace_minutes}m grace each")
    print()
 
    cred = DefaultAzureCredential()
    previous_stuck: set[str] | None = None
    repeat_streak = 0
    wait_only_mode = False
 
    with httpx.Client(timeout=60.0) as client:
        for it in range(1, args.max_iterations + 1):
            print("=" * 70)
            print(f"  ITERATION {it}/{args.max_iterations}")
            print("=" * 70)
 
            token = cred.get_token(SEARCH_SCOPE).token
 
            done = _summary_pdfs(client, search_ep, index_name, token)
            blob_names = set(_pdf_blobs(storage_acct, container))
            stuck = blob_names - done
 
            print(f"  Container PDFs : {len(blob_names)}")
            print(f"  Indexed (has summary): {len(done)}")
            print(f"  Stuck: {len(stuck)}")
 
            if not stuck:
                print()
                print("DONE: every container PDF has a summary record.")
                return 0
 
            if previous_stuck is not None and stuck == previous_stuck:
                repeat_streak += 1
                print(f"  Stuck set unchanged from previous iteration "
                      f"(streak={repeat_streak}).")
                if repeat_streak >= args.repeat_stuck_fail_streak:
                    print()
                    print(f"FAIL: same {len(stuck)} PDF(s) stuck across "
                          f"{args.repeat_stuck_fail_streak} consecutive iterations.")
                    print("These PDFs are failing deterministically. Investigate with:")
                    print(f"  python scripts/check_index.py --config "
                          f"{args.config} --bad")
                    print("Stuck list:")
                    for sf in sorted(stuck):
                        print(f"  - {sf}")
                    return 1
            else:
                repeat_streak = 0
            previous_stuck = set(stuck)
 
            if wait_only_mode:
                print("  Wait-only mode: indexer is still running; skipping metadata bump/resetdocs.")
            else:
                to_bump = sorted(stuck)[: args.max_per_iteration]
                print(f"  Bumping metadata on {len(to_bump)} blob(s):")
                stamp = _now_stamp()
                bumped: list[str] = []
                for name in to_bump:
                    ok = _bump_blob_metadata(storage_acct, container, name, stamp)
                    print(f"    {'ok ' if ok else 'FAIL'}  {name}")
                    if ok:
                        bumped.append(name)
 
                if not bumped:
                    print("  no blobs could be updated (storage failures); aborting")
                    return 1
 
                blob_urls = [
                    f"https://{storage_acct}.{storage_suffix}/{container}/{n}"
                    for n in bumped
                ]
                _reset_failed_items(client, search_ep, indexer_name, blob_urls, token)
                _trigger_indexer_run(client, search_ep, indexer_name, token)
 
            print(f"  Waiting up to {args.wait_minutes} min for indexer to drain...")
            wait_result = _wait_for_idle(
                client,
                search_ep,
                indexer_name,
                cred,
                max_minutes=args.wait_minutes,
                reset_after_zero_minutes=args.reset_after_zero_minutes,
            )
 
            if wait_result.get("stagnant"):
                wait_only_mode = False
                token = cred.get_token(SEARCH_SCOPE).token
                try:
                    _reset_indexer(client, search_ep, indexer_name, token)
                    _trigger_indexer_run(client, search_ep, indexer_name, token)
                except Exception as exc:
                    print(f"  reset/rerun failed: {exc}", flush=True)
                # Let the next iteration perform a full recheck and retry cycle.
                print()
                continue
 
            if wait_result.get("timed_out_running"):
                wait_only_mode = True
                # Don't treat unchanged stuck-set as deterministic failure while
                # a long-running run is still active.
                previous_stuck = None
                repeat_streak = 0
                print(f"  Indexer still running. Sleeping {args.wait_only_sleep_seconds}s "
                      "and continuing wait-only mode.")
                time.sleep(max(0, args.wait_only_sleep_seconds))
                print()
                continue
 
            wait_only_mode = False
 
            if args.grace_minutes > 0:
                print(f"  Grace sleep {args.grace_minutes} min for skill "
                      "records to project...")
                time.sleep(args.grace_minutes * 60)
 
            print()
 
    print()
    print(f"GAVE UP after {args.max_iterations} iterations.")
    if previous_stuck:
        print(f"Remaining stuck: {len(previous_stuck)} PDF(s)")
        for sf in sorted(previous_stuck):
            print(f"  - {sf}")
    return 1
 
 
if __name__ == "__main__":
    sys.exit(main())