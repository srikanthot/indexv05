"""
Quick diagnostic: query the index to see what record types exist,
check counts, and sample records. Uses AAD auth (same as deploy_search.py).

Usage:
    python scripts/check_index.py --config deploy.config.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="deploy.config.json")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"

    token = DefaultAzureCredential().get_token("https://search.azure.us/.default").token
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    base = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"

    print(f"Index: {index_name}")
    print(f"Endpoint: {endpoint}\n")

    # 1. Total document count
    resp = httpx.post(base, json={"search": "*", "top": 0, "count": True}, headers=headers, timeout=30)
    total = resp.json().get("@odata.count", "?")
    print(f"Total documents in index: {total}\n")

    # 2. Count by record_type
    print("--- Record type breakdown ---")
    for rt in ["text", "diagram", "table", "summary"]:
        body = {
            "search": "*",
            "filter": f"record_type eq '{rt}'",
            "top": 0,
            "count": True,
        }
        resp = httpx.post(base, json=body, headers=headers, timeout=30)
        count = resp.json().get("@odata.count", 0)
        print(f"  {rt:10s}: {count}")

    # 3. Sample a text record
    print("\n--- Sample text record ---")
    body = {
        "search": "*",
        "filter": "record_type eq 'text'",
        "top": 1,
        "select": "chunk_id,record_type,source_file,header_1,header_2,printed_page_label,figure_ref,physical_pdf_page,chunk,processing_status",
    }
    resp = httpx.post(base, json=body, headers=headers, timeout=30)
    hits = resp.json().get("value", [])
    if hits:
        print(json.dumps(hits[0], indent=2, ensure_ascii=False)[:1500])
    else:
        print("  (no text records found)")

    # 4. Sample a diagram record
    print("\n--- Sample diagram record ---")
    body = {
        "search": "*",
        "filter": "record_type eq 'diagram'",
        "top": 1,
        "select": "chunk_id,record_type,source_file,diagram_description,diagram_category,figure_ref,has_diagram,image_hash,processing_status",
    }
    resp = httpx.post(base, json=body, headers=headers, timeout=30)
    hits = resp.json().get("value", [])
    if hits:
        print(json.dumps(hits[0], indent=2, ensure_ascii=False)[:1500])
    else:
        print("  (no diagram records found)")

    # 5. Sample a table record
    print("\n--- Sample table record ---")
    body = {
        "search": "*",
        "filter": "record_type eq 'table'",
        "top": 1,
        "select": "chunk_id,record_type,source_file,header_1,table_caption,table_row_count,table_col_count,processing_status",
    }
    resp = httpx.post(base, json=body, headers=headers, timeout=30)
    hits = resp.json().get("value", [])
    if hits:
        print(json.dumps(hits[0], indent=2, ensure_ascii=False)[:1500])
    else:
        print("  (no table records found)")

    # 6. Sample a summary record
    print("\n--- Sample summary record ---")
    body = {
        "search": "*",
        "filter": "record_type eq 'summary'",
        "top": 1,
        "select": "chunk_id,record_type,source_file,chunk,processing_status",
    }
    resp = httpx.post(base, json=body, headers=headers, timeout=30)
    hits = resp.json().get("value", [])
    if hits:
        print(json.dumps(hits[0], indent=2, ensure_ascii=False)[:1500])
    else:
        print("  (no summary records found)")

    # 7. Check for figure_ref cross-references (text chunks with figure refs)
    print("\n--- Text chunks with figure_ref ---")
    body = {
        "search": "*",
        "filter": "record_type eq 'text' and figure_ref ne null and figure_ref ne ''",
        "top": 3,
        "select": "chunk_id,source_file,figure_ref,printed_page_label",
        "count": True,
    }
    resp = httpx.post(base, json=body, headers=headers, timeout=30)
    data = resp.json()
    count = data.get("@odata.count", 0)
    print(f"  Text chunks with figure_ref: {count}")
    for hit in data.get("value", [])[:3]:
        print(f"    {hit.get('figure_ref')} — {hit.get('source_file')} pg {hit.get('printed_page_label')}")

    # 8. Check processing_status distribution
    print("\n--- Processing status check ---")
    for status in ["ok", "cache_hit", "skipped_decorative", "no_image", "no_content", "no_source_path"]:
        body = {
            "search": "*",
            "filter": f"processing_status eq '{status}'",
            "top": 0,
            "count": True,
        }
        resp = httpx.post(base, json=body, headers=headers, timeout=30)
        count = resp.json().get("@odata.count", 0)
        if count:
            print(f"  {status:25s}: {count}")

    print("\nDone.")


if __name__ == "__main__":
    main()
