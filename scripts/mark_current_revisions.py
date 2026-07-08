"""
Post-index pass: populate document_family_id + is_current_revision + supersedes_revision.

Why a separate pass (not the emitters): deciding which revision of a manual is
CURRENT requires comparing ACROSS documents (all revisions of one manual), which
a per-document indexing skill structurally cannot do. This runs after indexing,
groups every record into a "family" (all revisions of the same manual, keyed by a
normalized document_number), marks the newest revision current, and merges the
three fields back onto every record via mergeOrUpload.

This is the stale-answer defense: the chatbot filters `is_current_revision eq true`
so a superseded torque value / clearance can't be returned as current.

Safety: DRY-RUN by default. It prints the family grouping and what it WOULD write.
Pass --apply to actually merge the fields into the index.

Usage:
  python scripts/mark_current_revisions.py --config deploy.config.json            # dry run
  python scripts/mark_current_revisions.py --config deploy.config.json --apply    # write
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _family_key(document_number: str) -> str:
    """Normalize a document_number into a stable family key shared by every
    revision of the same manual. Revision/date live in separate fields, so we
    only collapse case + punctuation + whitespace. Empty when no doc number."""
    if not document_number:
        return ""
    key = re.sub(r"[^a-z0-9]+", "", document_number.lower())
    # Defensive: strip a trailing rev token if it leaked into the number.
    key = re.sub(r"rev[a-z0-9]*$", "", key)
    return key


def _revision_sort_key(rec: dict[str, Any]) -> tuple:
    """Newest-first ordering within a family: effective_date desc, then a
    natural-ish document_revision desc as tie-breaker."""
    eff = (rec.get("effective_date") or "").strip()
    rev = (rec.get("document_revision") or "").strip().lower()
    # zero-pad digit runs in revision so 'rev 10' > 'rev 9'
    rev_norm = re.sub(r"\d+", lambda m: m.group(0).zfill(6), rev)
    return (eff, rev_norm)


def _iter_all_records(search_url: str, headers: dict) -> list[dict[str, Any]]:
    """Page through every record, selecting only the fields we need. Partition
    by nothing fancy — top/skip up to Azure's 100k window is plenty here since
    we select a handful of fields."""
    out: list[dict[str, Any]] = []
    skip = 0
    select = "id,source_file,document_number,document_revision,effective_date"
    while True:
        body = {"search": "*", "select": select, "top": 1000, "skip": skip}
        resp = httpx.post(search_url, json=body, headers=headers, timeout=60.0)
        resp.raise_for_status()
        batch = resp.json().get("value", [])
        out.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
        if skip >= 100000:
            print("WARNING: hit 100k skip window; not all records scanned.")
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Mark current vs superseded revisions")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    index_url = f"{endpoint}/indexes/{index_name}/docs/index?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}

    records = _iter_all_records(search_url, headers)
    print(f"scanned {len(records)} records")

    # Group into families; within a family, group by source_file (one revision
    # == one PDF). Pick the newest source_file as current.
    families: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in records:
        fam = _family_key(r.get("document_number") or "")
        if not fam:
            continue
        sf = r.get("source_file") or ""
        # keep one representative rev-meta per source_file
        families[fam].setdefault(sf, r)

    # Decide current source_file per family + supersedes chain.
    current_by_family: dict[str, str] = {}
    supersedes_by_family: dict[str, str] = {}
    for fam, by_sf in families.items():
        reps = sorted(by_sf.values(), key=_revision_sort_key, reverse=True)
        current_by_family[fam] = reps[0].get("source_file") or ""
        if len(reps) > 1:
            supersedes_by_family[fam] = (reps[1].get("document_revision") or "").strip()

    multi = {f: v for f, v in families.items() if len(v) > 1}
    print(f"{len(families)} families ({len(multi)} with multiple revisions)")
    for fam, by_sf in list(multi.items())[:20]:
        cur = current_by_family[fam]
        print(f"  family {fam}: current={cur!r}  revisions={sorted(by_sf)}")

    # Build merge actions for every record.
    actions: list[dict[str, Any]] = []
    for r in records:
        fam = _family_key(r.get("document_number") or "")
        if not fam:
            continue
        is_current = (r.get("source_file") or "") == current_by_family.get(fam)
        actions.append({
            "@search.action": "mergeOrUpload",
            "id": r["id"],
            "document_family_id": fam,
            "is_current_revision": bool(is_current),
            "supersedes_revision": supersedes_by_family.get(fam, "") if is_current else "",
        })

    print(f"prepared {len(actions)} merge actions")
    if not args.apply:
        print("DRY RUN — pass --apply to write. Sample action:")
        if actions:
            print("  " + json.dumps(actions[0]))
        return 0

    # Write in batches of 1000 (Azure Search limit).
    written = 0
    for i in range(0, len(actions), 1000):
        batch = actions[i:i + 1000]
        resp = httpx.post(index_url, json={"value": batch}, headers=headers, timeout=120.0)
        if resp.status_code not in (200, 207):
            print(f"batch {i} failed: {resp.status_code} {resp.text[:300]}")
            return 1
        written += len(batch)
        print(f"  merged {written}/{len(actions)}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
