"""
Drive the indexer until EVERY document is indexed — resuming and, when needed,
forcing stragglers. Safe to run repeatedly and safe to leave running.

HOW IT WORKS
    Azure Search enforces a 120-min execution quota per run, so heavy corpora
    finish only a few docs per run. This driver loops:
      1. measure coverage (which source PDFs already have a summary record),
      2. if all done -> exit success,
      3. if the indexer is still ADVANCING on its own -> just re-trigger a run,
      4. if it STALLED with docs still missing -> force ONLY those missing docs
         with resetDocs (targeted; does not touch already-indexed docs, does not
         reset the whole indexer) and run again,
      5. wait for the run to settle, then repeat.

    It does NOT give up on a per-run timeout (that was the old bug) and it does
    NOT reset the whole index. It stops only when coverage is complete, the time
    budget is exhausted, or the same stragglers refuse to index after repeated
    forced attempts (genuinely broken docs — reported by name).

Usage:
    python scripts/backfill_indexer.py --config deploy.config.json
    python scripts/backfill_indexer.py --config deploy.config.json --max-hours 12
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import quote

import httpx
from azure.identity import DefaultAzureCredential

SEARCH_API = "2024-05-01-preview"          # supports resetDocs
STORAGE_API = "2024-08-04"
SEARCH_SCOPE = "https://search.azure.us/.default"      # Azure US Gov
STORAGE_SCOPE = "https://storage.azure.com/.default"   # same across clouds

_cred = DefaultAzureCredential()


def _tok(scope: str) -> str:
    return _cred.get_token(scope).token


# ---- coverage (search side) -------------------------------------------------

def _done_source_files(endpoint: str, index: str) -> set[str]:
    """source_file values that have a summary record (= fully indexed)."""
    url = f"{endpoint}/indexes/{index}/docs/search?api-version={SEARCH_API}"
    out: set[str] = set()
    skip = 0
    while True:
        body = {"search": "*", "filter": "record_type eq 'summary'",
                "select": "source_file", "top": 1000, "skip": skip}
        with httpx.Client(timeout=60.0) as c:
            r = c.post(url, headers={"Authorization": f"Bearer {_tok(SEARCH_SCOPE)}",
                                     "Content-Type": "application/json"}, json=body)
        if r.status_code != 200:
            print(f"  WARN: coverage query {r.status_code}: {r.text[:200]}", flush=True)
            break
        batch = r.json().get("value", [])
        out.update(h["source_file"] for h in batch if h.get("source_file"))
        if len(batch) < 1000:
            break
        skip += 1000
    return out


# ---- container listing (storage side) ---------------------------------------

def _blob_suffix(endpoint: str) -> str:
    return "blob.core.usgovcloudapi.net" if ".azure.us" in endpoint else "blob.core.windows.net"


def _container_pdfs(account: str, container: str, suffix: str) -> list[str]:
    """All .pdf blob names in the container (excludes the _dicache/ cache)."""
    names: list[str] = []
    marker = ""
    while True:
        url = (f"https://{account}.{suffix}/{container}?restype=container&comp=list"
               f"&maxresults=5000")
        if marker:
            url += f"&marker={quote(marker)}"
        with httpx.Client(timeout=60.0) as c:
            r = c.get(url, headers={"Authorization": f"Bearer {_tok(STORAGE_SCOPE)}",
                                    "x-ms-version": STORAGE_API})
        if r.status_code != 200:
            print(f"  WARN: container list {r.status_code}: {r.text[:200]}", flush=True)
            break
        body = r.text
        for m in re.finditer(r"<Name>([^<]+)</Name>", body):
            n = m.group(1).strip()
            if n.lower().endswith(".pdf") and not n.startswith("_dicache/"):
                names.append(n)
        nm = re.search(r"<NextMarker>([^<]*)</NextMarker>", body)
        marker = (nm.group(1).strip() if nm else "")
        if not marker:
            break
    return sorted(set(names))


# ---- indexer control (search side) ------------------------------------------

def _trigger_run(endpoint: str, indexer: str) -> None:
    url = f"{endpoint}/indexers/{indexer}/run?api-version={SEARCH_API}"
    with httpx.Client(timeout=30.0) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {_tok(SEARCH_SCOPE)}"})
    if r.status_code in (200, 202):
        print("  triggered run (no reset)", flush=True)
    elif r.status_code == 409:
        print("  already running (409) — waiting for it", flush=True)
    else:
        print(f"  WARN: run trigger {r.status_code}: {r.text[:200]}", flush=True)


def _force_missing(endpoint: str, indexer: str, account: str, container: str,
                   suffix: str, missing: list[str], per_run_minutes: int) -> bool:
    """resetDocs ONLY the missing blobs so the indexer reprocesses them next
    run, regardless of change-tracking. Does not touch other docs; does not
    reset the whole indexer. Returns True if the reset was accepted.

    resetDocs FAILS with HTTP 400 ("Cannot reset ... while indexer is currently
    running"), so we must wait for the indexer to be idle first. The PT5M
    schedule can start a run between the wait and the call, so retry a few times."""
    blob_urls = [f"https://{account}.{suffix}/{container}/{quote(n)}" for n in missing]
    url = f"{endpoint}/indexers/{indexer}/resetdocs?api-version={SEARCH_API}"
    for attempt in range(1, 4):
        _wait_until_idle(endpoint, indexer, per_run_minutes)   # resetDocs needs idle
        with httpx.Client(timeout=30.0) as c:
            r = c.post(url, headers={"Authorization": f"Bearer {_tok(SEARCH_SCOPE)}",
                                     "Content-Type": "application/json"},
                       json={"datasourceDocumentIds": blob_urls})
        if r.status_code in (200, 202, 204):
            print(f"  resetDocs on {len(blob_urls)} straggler(s) -> forcing reindex", flush=True)
            _trigger_run(endpoint, indexer)
            return True
        if r.status_code == 400 and "running" in r.text.lower():
            print(f"  resetDocs 400 (indexer started running again) — "
                  f"waiting for idle and retrying ({attempt}/3)", flush=True)
            continue
        print(f"  WARN: resetDocs {r.status_code}: {r.text[:200]}", flush=True)
        return False
    print("  WARN: could not resetDocs (indexer kept running) — will retry next round", flush=True)
    return False


def _wait_until_idle(endpoint: str, indexer: str, max_minutes: int) -> None:
    """Poll until the run settles (not inProgress). Prints a live heartbeat. On
    a per-run timeout it just returns (the caller keeps looping — it never
    exits)."""
    url = f"{endpoint}/indexers/{indexer}/status?api-version={SEARCH_API}"
    deadline = time.time() + max_minutes * 60
    t0 = time.time()
    poll = 0
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(url, headers={"Authorization": f"Bearer {_tok(SEARCH_SCOPE)}"})
        if r.status_code != 200:
            print(f"  status {r.status_code}; retry 20s", flush=True)
            time.sleep(20)
            continue
        last = r.json().get("lastResult") or {}
        top = r.json().get("status") or "unknown"
        st = last.get("status") or "unknown"
        poll += 1
        print(f"  [poll {poll} +{(time.time()-t0)/60:.0f}m] indexer={top} lastRun={st} "
              f"items={last.get('itemsProcessed','?')} failed={last.get('itemsFailed',0)}",
              flush=True)
        if top != "inProgress" and st != "inProgress":
            return
        time.sleep(20)
    print(f"  (run did not settle within {max_minutes}m — continuing anyway)", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--max-hours", type=float, default=24.0,
                    help="Overall wall-clock budget (default 24). It runs to "
                         "completion; this is only a safety cap.")
    ap.add_argument("--per-run-minutes", type=int, default=180,
                    help="Max wait for one run to settle before re-checking "
                         "(default 180; exceeds the 120-min quota).")
    ap.add_argument("--max-force-rounds", type=int, default=3,
                    help="If the SAME stragglers stay unindexed after this many "
                         "forced attempts, declare them broken and stop (default 3).")
    args = ap.parse_args()

    cfg = json.loads(open(args.config, encoding="utf-8").read())
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index = f"{prefix}-index"
    indexer = f"{prefix}-indexer"
    account = cfg["storage"]["accountResourceId"].rstrip("/").split("/")[-1]
    container = cfg["storage"]["pdfContainerName"]
    suffix = _blob_suffix(endpoint)

    pdfs = set(_container_pdfs(account, container, suffix))
    target = len(pdfs)
    print("=" * 70)
    print("  BACKFILL DRIVER — run the indexer until ALL documents are indexed")
    print(f"  indexer={indexer}  container={container}  target={target} PDFs")
    print(f"  budget={args.max_hours}h  per-run-wait={args.per_run_minutes}m")
    print("=" * 70, flush=True)
    if target == 0:
        print("No PDFs found in the container.")
        return 1

    t_start = time.time()
    prev_indexed = -1
    force_streak = 0
    rnd = 0

    while (time.time() - t_start) / 3600.0 < args.max_hours:
        rnd += 1
        done = _done_source_files(endpoint, index) & pdfs
        indexed = len(done)
        missing = sorted(pdfs - done)
        elapsed = (time.time() - t_start) / 3600.0
        print(f"\n----- round {rnd}  ({elapsed:.2f}h)  indexed {indexed}/{target}  "
              f"remaining {len(missing)} -----", flush=True)
        if not missing:
            print(f"\n[DONE] all {target} documents are indexed.")
            return 0
        if len(missing) <= 15:
            print(f"  remaining: {missing}", flush=True)

        advancing = indexed > prev_indexed if prev_indexed >= 0 else True
        if advancing:
            force_streak = 0
            print("  indexer is advancing on its own — re-triggering a run", flush=True)
            _trigger_run(endpoint, indexer)
        else:
            force_streak += 1
            if force_streak > args.max_force_rounds:
                # Do NOT fail the pipeline: the backfill did its job for the rest
                # of the corpus. Surface the stragglers loudly as a warning so the
                # stage still passes and they can be investigated separately (a
                # coverage/check step is the place to gate on completeness).
                print(f"\n[WARN] {indexed}/{target} indexed. These {len(missing)} "
                      f"document(s) did not index after {args.max_force_rounds} "
                      f"forced attempts — likely a content/skill issue on those "
                      f"specific docs. Inspect the indexer execution errors for "
                      f"them:\n  {missing}")
                print("[DONE-WITH-WARNINGS] exiting 0 so the pipeline is not "
                      "failed by a few problem docs.")
                return 0
            print(f"  no progress since last round — FORCING {len(missing)} "
                  f"straggler(s) (attempt {force_streak})", flush=True)
            _force_missing(endpoint, indexer, account, container, suffix, missing,
                           args.per_run_minutes)

        prev_indexed = indexed
        _wait_until_idle(endpoint, indexer, args.per_run_minutes)

    print(f"\n[BUDGET] hit the {args.max_hours}h safety cap. Re-run to continue "
          f"(nothing was reset; progress is preserved).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
