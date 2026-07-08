"""
Reconcile blob storage state with the search index.
 
Detects three cases per PDF:
  - ADDED   : in blob, not in index           -> no action (preanalyze + indexer pick it up next run)
  - DELETED : in index, not in blob           -> purge index records + cache blobs + Cosmos state
  - EDITED  : blob's last_modified > last_indexed_at  (per Cosmos pdf_state)
                                              -> purge index records + cache blobs (forces full reanalyse)
  - STABLE  : nothing changed                 -> skip
 
Safety:
  - --dry-run prints what would happen without acting.
  - --max-purges (default 2) caps the number of PDFs we'll purge in one
    run. If more candidates are found, the script aborts with a clear
    message. The operator raises the cap intentionally once they trust
    the script.
 
Usage:
    python scripts/reconcile.py --config deploy.config.json --dry-run
    python scripts/reconcile.py --config deploy.config.json --max-purges 5
"""
 
from __future__ import annotations
 
import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
 
import httpx
from azure.identity import DefaultAzureCredential
 
# Make function_app/shared importable (parent_id derivation).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "function_app"))
# Local script imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cosmos_writer  # noqa: E402
from preanalyze import (  # noqa: E402
    _init_storage,
    blob_exists,
    delete_blob,
    fetch_blob,
    list_cache_blobs,
)
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"

# Document types the pipeline can ingest (PDF natively; office docs via the
# LibreOffice conversion in preanalyze). The blob listing MUST cover all of
# these — if it only listed .pdf, a .docx already in the index would look
# "not in blob" and get wrongly purged as DELETED.
SUPPORTED_DOC_EXTS = (".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls")


def _odata_escape(value: str) -> str:
    """Escape a string literal for an OData filter (single-quote doubling)."""
    return (value or "").replace("'", "''")
 
 
@dataclass
class ReconcilePlan:
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    stable: list[str] = field(default_factory=list)
    blob_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    index_chunks_per_pdf: dict[str, int] = field(default_factory=dict)
 
 
# ---------- helpers ----------
 
def _aad_token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
 
def _list_blob_metadata(cfg: dict) -> dict[str, dict[str, Any]]:
    """Returns {filename: {last_modified: iso, content_length: int}} for
    every PDF blob at the container root. Uses the same az CLI path as
    preanalyze.list_pdfs, but with --query that captures last_modified."""
    import os
    import subprocess
 
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    from preanalyze import _get_connection_string  # local import — same module
    conn_str = _get_connection_string(cfg)
    az_bin = "az.cmd" if os.name == "nt" else "az"
    raw = subprocess.run(
        [
            az_bin, "storage", "blob", "list",
            "--container-name", container,
            "--connection-string", conn_str,
            "--num-results", "*",
            "--query", "[].{name:name, last_modified:properties.lastModified, length:properties.contentLength}",
            "-o", "json",
        ],
        capture_output=True, text=True, check=True,
    )
    items = json.loads(raw.stdout)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item.get("name") or ""
        # Include every supported document type, not just .pdf — otherwise an
        # office doc already in the index is mis-classified DELETED and purged.
        if not name.lower().endswith(SUPPORTED_DOC_EXTS):
            continue
        out[name] = {
            "last_modified": item.get("last_modified"),
            "content_length": item.get("length") or 0,
        }
    return out
 
 
def _query_index_pdfs(endpoint: str, index_name: str, headers: dict) -> dict[str, int]:
    """Returns {source_file: chunk_count} via facet query on the index."""
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    body = {"search": "*", "facets": ["source_file,count:0"], "top": 0}
    with httpx.Client(timeout=60.0) as c:
        resp = c.post(url, json=body, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    facets_root = payload.get("@search.facets") or payload.get("@odata.facets") or {}
    facets = facets_root.get("source_file", [])
    return {f["value"]: int(f.get("count", 0)) for f in facets if f.get("value")}
 
 
def _collect_ids_for_source_file(
    endpoint: str, index_name: str, headers: dict, source_file: str,
) -> list[str]:
    """Return every index key (`id`) whose source_file matches this document.

    Deleting by the exact `source_file` (the blob name, stored on every record)
    is bulletproof — it needs no parent_id reconstruction (which previously
    hardcoded the Gov storage suffix and silently deleted nothing on a mismatch)
    and it covers ALL record types (text/table/table_row/diagram/summary).

    We collect all keys first (paginate by skip) THEN delete, so we never loop
    on eventual-consistency lag from deleting mid-scan.
    """
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    sf = _odata_escape(source_file)
    ids: list[str] = []
    skip = 0
    while True:
        body = {
            "search": "*",
            "filter": f"source_file eq '{sf}'",
            "select": "id",
            "top": 1000,
            "skip": skip,
        }
        with httpx.Client(timeout=60.0) as c:
            resp = c.post(search_url, json=body, headers=headers)
        resp.raise_for_status()
        hits = resp.json().get("value", [])
        ids.extend(h["id"] for h in hits if h.get("id"))
        if len(hits) < 1000:
            break
        skip += 1000
        if skip >= 100000:
            logging.warning("source_file %s has >100k records; truncating scan", source_file)
            break
    return ids


def _delete_index_records_for_source_file(
    endpoint: str, index_name: str, headers: dict, source_file: str,
    max_batch: int = 1000,
) -> int:
    """Delete every index record for a document, keyed by exact source_file.
    Returns the number of records deleted."""
    index_url = f"{endpoint}/indexes/{index_name}/docs/index?api-version={API_VERSION}"
    ids = _collect_ids_for_source_file(endpoint, index_name, headers, source_file)
    deleted_total = 0
    for i in range(0, len(ids), max_batch):
        batch = ids[i:i + max_batch]
        actions = [{"@search.action": "delete", "id": _id} for _id in batch]
        with httpx.Client(timeout=60.0) as c:
            del_resp = c.post(index_url, json={"value": actions}, headers=headers)
        del_resp.raise_for_status()
        results = del_resp.json().get("value", [])
        deleted_total += sum(1 for r in results if r.get("status"))
    return deleted_total


def _delete_cache_blobs_for_pdf(cfg: dict, pdf_name: str) -> int:
    """Delete every _dicache/<pdf_name>.* blob. Returns deleted count."""
    cache_blobs = list_cache_blobs(cfg)
    prefix = f"_dicache/{pdf_name}."
    targets = [b for b in cache_blobs if b.startswith(prefix)]
    n = 0
    for b in targets:
        try:
            if delete_blob(cfg, b):
                n += 1
        except Exception as exc:
            logging.warning("delete cache blob %s failed: %s", b, exc)
    return n
 
 
# ---------- planning ----------
 
def build_plan(cfg: dict, endpoint: str, index_name: str, headers: dict) -> ReconcilePlan:
    """Compare blob, index, and Cosmos pdf_state to produce a plan."""
    plan = ReconcilePlan()
 
    print("Listing PDFs in blob container...")
    plan.blob_meta = _list_blob_metadata(cfg)
    blob_pdfs = set(plan.blob_meta.keys())
    print(f"  {len(blob_pdfs)} PDFs in blob")
 
    print("Querying index for source_file facet...")
    plan.index_chunks_per_pdf = _query_index_pdfs(endpoint, index_name, headers)
    indexed_pdfs = set(plan.index_chunks_per_pdf.keys())
    print(f"  {len(indexed_pdfs)} PDFs have records in the index")
 
    # Read existing pdf_state from Cosmos so we know last_indexed_at per PDF.
    last_indexed_at: dict[str, str] = {}
    if cosmos_writer._is_configured(cfg):
        try:
            db = cosmos_writer._get_database_client(cfg)
            container = cosmos_writer._ensure_container(db, cosmos_writer.PDF_STATE_CONTAINER)
            for item in container.read_all_items():
                sf = item.get("source_file") or item.get("id")
                ts = item.get("last_indexed_at")
                if sf and ts:
                    last_indexed_at[sf] = ts
            print(f"  {len(last_indexed_at)} PDFs have Cosmos state rows")
        except Exception as exc:
            logging.warning("Cosmos pdf_state read failed: %s", exc)
 
    # Classify each PDF.
    # Also load cached source hashes for content-change detection.
    cached_sizes: dict[str, int] = {}
    for pdf in sorted(blob_pdfs):
        size_blob = f"_dicache/{pdf}.source_size"
        try:
            if blob_exists(cfg, size_blob):
                cached_sizes[pdf] = int(fetch_blob(cfg, size_blob).decode("utf-8").strip() or "0")
        except Exception:
            pass
    if cached_sizes:
        print(f"  {len(cached_sizes)} PDFs have cached source_size")
 
    for pdf in sorted(blob_pdfs | indexed_pdfs):
        in_blob = pdf in blob_pdfs
        in_index = pdf in indexed_pdfs
        if in_blob and not in_index:
            plan.added.append(pdf)
            continue
        if in_index and not in_blob:
            plan.deleted.append(pdf)
            continue
        # In both. Edit detection: blob.last_modified > last_indexed_at
        # OR cached source_hash differs from live blob hash (content changed
        # even if timestamp comparison is ambiguous due to clock drift).
        # EDIT detection. Two independent signals (OR):
        #   1. blob.last_modified > last_indexed_at (the "different timestamp"
        #      signal — needs Cosmos pdf_state).
        #   2. blob content size != the size cached when we indexed it
        #      (Cosmos-INDEPENDENT — works even if pdf_state is missing).
        # Either firing means the same-named blob changed -> purge ALL its old
        # chunks, then preanalyze + indexer rebuild it fresh. A same-size edit
        # with no Cosmos state can't be caught cheaply; use --purge-files to
        # force it.
        blob_lm = plan.blob_meta.get(pdf, {}).get("last_modified") or ""
        cosmos_ts = last_indexed_at.get(pdf, "")
        live_len = int(plan.blob_meta.get(pdf, {}).get("content_length") or 0)
        cached_len = cached_sizes.get(pdf)
        is_edited = False
        if blob_lm and cosmos_ts and blob_lm > cosmos_ts:
            is_edited = True
        elif cached_len is not None and live_len and live_len != cached_len:
            is_edited = True
        if is_edited:
            plan.edited.append(pdf)
        elif blob_lm and not cosmos_ts:
            # No Cosmos record but the PDF is already in the index.
            # Treat as stable on this run; the next preanalyze/indexer
            # cycle will populate the Cosmos state row, after which edit
            # detection works.
            plan.stable.append(pdf)
        else:
            plan.stable.append(pdf)
    return plan
 
 
# ---------- execution ----------
 
def execute_plan(cfg: dict, plan: ReconcilePlan, endpoint: str,
                 index_name: str, headers: dict, dry_run: bool) -> dict[str, Any]:
    """Carry out the deletes and edit-purges in the plan. Returns a stats
    dict suitable for the run record."""
    stats = {
        "deleted_pdfs": 0, "edited_pdfs": 0,
        "chunks_purged": 0, "cache_blobs_purged": 0,
        "errors": [],
    }
    targets = [(pdf, "deleted") for pdf in plan.deleted] + [(pdf, "edited") for pdf in plan.edited]
    for pdf, kind in targets:
        if dry_run:
            print(f"  [dry-run] would purge {pdf} ({kind}, "
                  f"chunks={plan.index_chunks_per_pdf.get(pdf, 0)})")
            continue

        # 1. Delete index records by EXACT source_file (covers every record
        #    type; no fragile parent_id reconstruction). This is the primitive
        #    both DELETED and EDITED need: for EDITED we purge ALL old chunks
        #    first, then preanalyze rebuilds the cache and the indexer
        #    re-projects fresh chunks — so pages removed in an edit don't leave
        #    orphaned rows behind.
        try:
            n = _delete_index_records_for_source_file(endpoint, index_name, headers, pdf)
            stats["chunks_purged"] += n
            print(f"  purged {n} index records for {pdf} ({kind})")
        except Exception as exc:
            err = f"index purge failed for {pdf}: {exc}"
            logging.warning(err)
            stats["errors"].append(err)
            continue
 
        # 2. Delete cache blobs.
        try:
            blobs = _delete_cache_blobs_for_pdf(cfg, pdf)
            stats["cache_blobs_purged"] += blobs
            print(f"  purged {blobs} cache blobs for {pdf}")
        except Exception as exc:
            err = f"cache purge failed for {pdf}: {exc}"
            logging.warning(err)
            stats["errors"].append(err)
 
        # 3. For deleted PDFs, also clear Cosmos state.
        if kind == "deleted":
            cosmos_writer.delete_pdf_state(cfg, pdf)
            stats["deleted_pdfs"] += 1
        else:
            # For edited, leave the Cosmos row; preanalyze/check_index
            # will overwrite it on the next run with the new state.
            stats["edited_pdfs"] += 1
 
    return stats
 
 
# ---------- main ----------
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile blob storage with search index")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print plan without making any changes")
    ap.add_argument("--max-purges", type=int, default=2,
                    help="Refuse to purge more than N PDFs in one run (default 2)")
    ap.add_argument("--no-lock", action="store_true",
                    help="Skip the pipeline lock. Use only for read/diagnostic mode.")
    ap.add_argument("--skip-edits", action="store_true",
                    help="Only act on DELETED PDFs (purge index records + cache "
                         "+ Cosmos state for them). Skip EDITED PDFs entirely — "
                         "their cache and chunks stay intact, indexer re-projects "
                         "on next run via blob LMT bump. Use when a blob batch was "
                         "re-uploaded only to set metadata, and you want the "
                         "existing _dicache/ to be re-used instead of rebuilt.")
    ap.add_argument("--purge-files", nargs="+", metavar="SOURCE_FILE", default=None,
                    help="Explicit mode for CI/Jenkins: purge ALL index chunks (and "
                         "cache blobs) for the named source file(s), skipping "
                         "auto-detection. Use when your pipeline already KNOWS a PDF "
                         "was deleted or edited (same name, new timestamp) and wants "
                         "its old chunks removed before re-indexing. Respects "
                         "--dry-run. Add --clear-cosmos to also drop pdf_state.")
    ap.add_argument("--clear-cosmos", action="store_true",
                    help="With --purge-files, also delete the Cosmos pdf_state row "
                         "(use for true DELETES; omit for EDITS that will re-index).")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_aad_token()}",
    }

    # ---- Explicit purge mode (CI/Jenkins) -------------------------------
    # Jenkins detects a deleted blob, or an edited blob (same name, new
    # timestamp), and calls this to remove that PDF's stale chunks BEFORE
    # re-indexing. Bypasses auto-detection entirely.
    if args.purge_files:
        print(f"Explicit purge of {len(args.purge_files)} file(s) "
              f"(dry_run={args.dry_run}, clear_cosmos={args.clear_cosmos}):")
        total_chunks = 0
        for sf in args.purge_files:
            if args.dry_run:
                ids = _collect_ids_for_source_file(endpoint, index_name, headers, sf)
                print(f"  [dry-run] would purge {len(ids)} index records + cache for {sf}")
                continue
            n = _delete_index_records_for_source_file(endpoint, index_name, headers, sf)
            total_chunks += n
            cache_n = _delete_cache_blobs_for_pdf(cfg, sf)
            print(f"  purged {n} index records + {cache_n} cache blobs for {sf}")
            if args.clear_cosmos:
                try:
                    cosmos_writer.delete_pdf_state(cfg, sf)
                    print(f"    cleared Cosmos pdf_state for {sf}")
                except Exception as exc:
                    print(f"    warn: Cosmos clear failed for {sf}: {exc}")
        print(f"Explicit purge done ({total_chunks} records removed). "
              f"Re-run preanalyze --incremental + the indexer to rebuild edited files.")
        return 0

    # Acquire the same pipeline lock preanalyze uses, so the two can't
    # collide (e.g. reconcile purges a cache blob preanalyze just wrote).
    # Dry-run mode doesn't take the lock — it's read-only.
    lock_id = None
    if not args.dry_run and not args.no_lock:
        try:
            from pipeline_lock import LockHeldError, acquire_lock
            lock_id = acquire_lock(cfg, "preanalyze")
            print(f"  acquired pipeline lock (id={lock_id[:8]}...)")
        except LockHeldError as exc:
            print(f"\nABORT: {exc}")
            return 2
        except Exception as exc:
            print(f"  warning: lock acquire failed: {exc}; proceeding")
 
    print(f"Reconcile starting (dry_run={args.dry_run}, max_purges={args.max_purges}, "
          f"skip_edits={args.skip_edits})")
    plan = build_plan(cfg, endpoint, index_name, headers)
 
    # If --skip-edits, clear the edits list so they're treated as STABLE.
    # We keep them in the printed summary as "EDITED (skipped)" so the
    # operator can see what was bypassed.
    edits_skipped: list[str] = []
    if args.skip_edits and plan.edited:
        edits_skipped = list(plan.edited)
        plan.edited = []
 
    print()
    print(f"  ADDED   : {len(plan.added)}  (no action — preanalyze + indexer will pick them up)")
    if edits_skipped:
        print(f"  EDITED  : {len(plan.edited)}  (purge), "
              f"{len(edits_skipped)} skipped (--skip-edits)")
    else:
        print(f"  EDITED  : {len(plan.edited)}  (will purge index + cache, then preanalyze re-runs)")
    print(f"  DELETED : {len(plan.deleted)} (will purge index + cache + Cosmos state)")
    print(f"  STABLE  : {len(plan.stable)}")
 
    purge_count = len(plan.deleted) + len(plan.edited)
    if purge_count > args.max_purges and not args.dry_run:
        print()
        print(f"ABORT: {purge_count} PDFs would be purged but --max-purges is {args.max_purges}.")
        print("       Re-run with a higher --max-purges (or --dry-run to inspect first).")
        return 2
 
    if plan.deleted or plan.edited:
        print()
        print("Executing purges:")
        stats = execute_plan(cfg, plan, endpoint, index_name, headers, args.dry_run)
    else:
        stats = {"deleted_pdfs": 0, "edited_pdfs": 0,
                 "chunks_purged": 0, "cache_blobs_purged": 0, "errors": []}
 
    # Persist a run record (best-effort).
    if not args.dry_run:
        cosmos_writer.write_run_record(cfg, {
            "run_type": "reconcile",
            "triggered_by": "manual",
            "added": plan.added,
            "edited": plan.edited,
            "deleted": plan.deleted,
            "stable_count": len(plan.stable),
            "chunks_purged": stats["chunks_purged"],
            "cache_blobs_purged": stats["cache_blobs_purged"],
            "errors": stats["errors"],
        })
 
    # Release lock before exit.
    if lock_id is not None:
        try:
            from pipeline_lock import release_lock
            release_lock(cfg, "preanalyze", lock_id)
            print("  released pipeline lock")
        except Exception as exc:
            print(f"  warning: lock release failed: {exc}")
 
    print()
    print("Reconcile done.")
    if stats["errors"]:
        print(f"  WARNING: {len(stats['errors'])} errors during execution; see logs")
        return 1
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())