"""
Strict page-coordinate validator for production readiness.
 
Checks full corpus and fails on any critical page-coordinate anomalies:
- physical_pdf_page not matching min(physical_pdf_pages)
- physical_pdf_page_end not matching max(physical_pdf_pages)
- non-contiguous physical_pdf_pages
 
Also reports suspicious long spans (>=4, >=5) for operational review.
 
Usage:
  python scripts/validate_page_coordinates.py --config deploy.config.json --strict
"""
 
from __future__ import annotations
 
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
 
def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
 
def _search(url: str, headers: dict, body: dict) -> dict:
    r = httpx.post(url, json=body, headers=headers, timeout=120.0)
    r.raise_for_status()
    return r.json()
 
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Validate page-coordinate consistency")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--strict", action="store_true", help="Fail on any critical anomaly")
    ap.add_argument("--output", default="reports/page_coordinate_gate_report.json")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
 
    token = _token()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
 
    # Discover retrievable fields so select cannot fail.
    idx = httpx.get(index_url, headers=headers, timeout=60.0)
    idx.raise_for_status()
    fields = idx.json().get("fields", [])
    retrievable = {f.get("name") for f in fields if f.get("retrievable") is True}
 
    select_candidates = [
        "chunk_id",
        "record_type",
        "source_file",
        "physical_pdf_page",
        "physical_pdf_page_end",
        "physical_pdf_pages",
        "processing_status",
    ]
    select_fields = [f for f in select_candidates if f in retrievable]
 
    meta = _search(
        search_url,
        headers,
        {"search": "*", "top": 0, "count": True, "facets": ["source_file,count:500"]},
    )
    total = int(meta.get("@odata.count") or 0)
    source_files = [
        x.get("value")
        for x in (meta.get("@search.facets", {}).get("source_file") or [])
        if x.get("value")
    ]
 
    counts = Counter()
    by_type = defaultdict(Counter)
    examples = defaultdict(list)
 
    scanned = 0
    for sf in source_files:
        safe_sf = str(sf).replace("'", "''")
        skip = 0
        top = 1000
        while True:
            body = {
                "search": "*",
                "filter": f"source_file eq '{safe_sf}'",
                "select": ",".join(select_fields),
                "top": top,
                "skip": skip,
            }
            data = _search(search_url, headers, body)
            vals = data.get("value", [])
            if not vals:
                break
 
            for rec in vals:
                scanned += 1
                rt = rec.get("record_type") or "NULL"
                p = rec.get("physical_pdf_page")
                pe = rec.get("physical_pdf_page_end")
                plist = rec.get("physical_pdf_pages")
                plist = plist if isinstance(plist, list) else []
 
                if plist:
                    pmin = min(plist)
                    pmax = max(plist)
                    uniq = sorted(set(plist))
                    contiguous = uniq == list(range(pmin, pmax + 1))
 
                    if isinstance(p, int) and p != pmin:
                        counts["physical_page_not_min_of_list"] += 1
                        by_type[rt]["physical_page_not_min_of_list"] += 1
                        if len(examples["physical_page_not_min_of_list"]) < 10:
                            examples["physical_page_not_min_of_list"].append(rec.get("chunk_id"))
 
                    if isinstance(pe, int) and pe != pmax:
                        counts["physical_page_end_not_max_of_list"] += 1
                        by_type[rt]["physical_page_end_not_max_of_list"] += 1
                        if len(examples["physical_page_end_not_max_of_list"]) < 10:
                            examples["physical_page_end_not_max_of_list"].append(rec.get("chunk_id"))
 
                    if not contiguous:
                        counts["physical_pdf_pages_non_contiguous"] += 1
                        by_type[rt]["physical_pdf_pages_non_contiguous"] += 1
                        if len(examples["physical_pdf_pages_non_contiguous"]) < 10:
                            examples["physical_pdf_pages_non_contiguous"].append(rec.get("chunk_id"))
 
                l = len(plist)
                if l >= 4:
                    counts["pages_list_len_ge_4"] += 1
                    by_type[rt]["pages_list_len_ge_4"] += 1
                if l >= 5:
                    counts["pages_list_len_ge_5"] += 1
                    by_type[rt]["pages_list_len_ge_5"] += 1
 
            if len(vals) < top:
                break
            skip += top
 
    critical = (
        counts["physical_page_not_min_of_list"]
        + counts["physical_page_end_not_max_of_list"]
        + counts["physical_pdf_pages_non_contiguous"]
    )
 
    report = {
        "index_name": index_name,
        "total_records_reported_by_service": total,
        "total_records_scanned": scanned,
        "full_coverage": scanned == total,
        "critical_anomaly_counts": {
            "physical_page_not_min_of_list": counts["physical_page_not_min_of_list"],
            "physical_page_end_not_max_of_list": counts["physical_page_end_not_max_of_list"],
            "physical_pdf_pages_non_contiguous": counts["physical_pdf_pages_non_contiguous"],
        },
        "suspicious_span_counts": {
            "pages_list_len_ge_4": counts["pages_list_len_ge_4"],
            "pages_list_len_ge_5": counts["pages_list_len_ge_5"],
        },
        "anomaly_counts_by_record_type": {k: dict(v) for k, v in by_type.items()},
        "example_chunk_ids": dict(examples),
        "strict_pass": critical == 0 and scanned == total,
    }
 
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
 
    print(f"Index: {index_name}")
    print(f"Scanned: {scanned}/{total} (full={scanned == total})")
    print("Critical anomalies:")
    print(f"  physical_page_not_min_of_list: {counts['physical_page_not_min_of_list']}")
    print(f"  physical_page_end_not_max_of_list: {counts['physical_page_end_not_max_of_list']}")
    print(f"  physical_pdf_pages_non_contiguous: {counts['physical_pdf_pages_non_contiguous']}")
    print("Suspicious spans:")
    print(f"  pages_list_len_ge_4: {counts['pages_list_len_ge_4']}")
    print(f"  pages_list_len_ge_5: {counts['pages_list_len_ge_5']}")
    print(f"Wrote {out}")
 
    if args.strict and not report["strict_pass"]:
        print("STRICT FAIL: critical page-coordinate anomalies found.")
        return 1
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 