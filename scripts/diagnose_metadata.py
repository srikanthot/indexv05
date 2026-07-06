"""
Diagnose blob user-metadata vs index taxonomy fields, per PDF.
 
For every PDF in the blob container:
  - reads its blob user-metadata
  - queries the index for the operationalarea / functionalarea / doctype
    on a sample chunk
  - prints a per-PDF row comparing the two
 
Helps answer:
  - Which PDFs have metadata set on the blob (and which don't)?
  - Which PDFs have the index fields populated (and which don't)?
  - Are there key-name mismatches (case or naming)?
 
Usage:
    python scripts/diagnose_metadata.py --config deploy.config.json
"""
 
from __future__ import annotations
 
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
EXPECTED_KEYS = ("operationalarea", "functionalarea", "doctype")
 
 
def _az_bin() -> str:
    return "az.cmd" if os.name == "nt" else "az"
 
 
def _az(args: list[str]) -> str:
    r = subprocess.run([_az_bin()] + args, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()
 
 
def list_pdf_blobs(storage_account: str, container: str) -> list[str]:
    raw = _az([
        "storage", "blob", "list",
        "--account-name", storage_account,
        "--container-name", container,
        "--auth-mode", "login",
        "--query", "[?ends_with(name, '.pdf')].name",
        "-o", "json",
    ])
    return json.loads(raw) if raw else []
 
 
def get_blob_metadata(storage_account: str, container: str, blob: str) -> dict:
    raw = _az([
        "storage", "blob", "metadata", "show",
        "--account-name", storage_account,
        "--container-name", container,
        "--name", blob,
        "--auth-mode", "login",
        "-o", "json",
    ])
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}
 
 
def get_index_fields(client: httpx.Client, endpoint: str, index: str,
                    token: str, source_file: str) -> dict:
    """Query for one sample chunk for this PDF; return the taxonomy fields."""
    body = {
        "search": "*",
        "filter": f"source_file eq '{source_file}'",
        "select": "operationalarea,functionalarea,doctype,filetype",
        "top": 1,
    }
    r = client.post(
        f"{endpoint}/indexes/{index}/docs/search?api-version={API_VERSION}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )
    if r.status_code != 200:
        return {}
    rows = r.json().get("value") or []
    return rows[0] if rows else {}
 
 
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="deploy.config.json")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    storage = cfg["storage"]["accountResourceId"].rstrip("/").split("/")[-1]
    container = cfg["storage"]["pdfContainerName"]
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    index = f"{cfg['search']['artifactPrefix']}-index"
 
    token = DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
    pdfs = list_pdf_blobs(storage, container)
    pdfs = [p for p in pdfs if not p.startswith("_dicache/")]
    pdfs.sort()
 
    if not pdfs:
        print("No PDFs found in container.")
        return 0
 
    print(f"Container: {storage}/{container}")
    print(f"Index:     {index}")
    print(f"Found {len(pdfs)} PDFs.\n")
 
    # Header
    print(f"{'PDF':45} {'blob keys':50} {'idx oa':>10} {'idx fa':>10} {'idx dt':>10}")
    print("-" * 130)
 
    issues: list[str] = []
    with httpx.Client() as client:
        for pdf in pdfs:
            meta = get_blob_metadata(storage, container, pdf)
            idx = get_index_fields(client, endpoint, index, token, pdf)
 
            # Build a compact view of which expected keys are set
            present = []
            unexpected = []
            for k in meta:
                kl = k.lower()
                if kl in EXPECTED_KEYS:
                    present.append(kl)
                elif kl not in ("force_reindex", "auto_heal_at"):
                    unexpected.append(k)
 
            keys_view = ",".join(present) or "(none)"
            if unexpected:
                keys_view += f"  +unexpected:{','.join(unexpected)}"
 
            oa = (idx.get("operationalarea") or "NULL")[:10]
            fa = (idx.get("functionalarea") or "NULL")[:10]
            dt = (idx.get("doctype") or "NULL")[:10]
 
            short_pdf = pdf if len(pdf) <= 44 else pdf[:41] + "..."
            print(f"{short_pdf:45} {keys_view[:50]:50} {oa:>10} {fa:>10} {dt:>10}")
 
            # Flag specific issues for the summary
            for expected in EXPECTED_KEYS:
                if expected in [k.lower() for k in meta] and not idx.get(expected):
                    issues.append(
                        f"  {pdf}: blob has '{expected}' but index field is NULL "
                        "(indexer hasn't reprocessed since metadata was set OR mapping miss)"
                    )
            for k in unexpected:
                issues.append(
                    f"  {pdf}: blob has unexpected key '{k}' — won't be picked up by indexer. "
                    f"Rename to {EXPECTED_KEYS}."
                )
 
    print()
    if issues:
        print("ISSUES FOUND:")
        for i in issues[:50]:
            print(i)
    else:
        print("No mismatches detected between blob metadata and index fields.")
 
    # Top-level summary
    n_blob_tagged = sum(
        1 for pdf in pdfs
        if any(k.lower() in EXPECTED_KEYS for k in get_blob_metadata(storage, container, pdf))
    )
    print(f"\nSummary: {n_blob_tagged}/{len(pdfs)} PDFs have at least one of "
          f"{EXPECTED_KEYS} set as blob metadata.")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())