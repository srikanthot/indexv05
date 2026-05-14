"""
Per-PDF inspection — show what got indexed for a single document.

Outputs:
  - Total chunks in the index for that PDF
  - Breakdown by record_type (text / diagram / table / table_row / summary)
  - Number of distinct pages with content
  - Estimated total skill-call time (so you can explain why it took N minutes)

Usage:
    python scripts/inspect_pdf.py --config deploy.config.json --pdf ED-EM-SSM.pdf
    python scripts/inspect_pdf.py --config deploy.config.json --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"

# App Insights observed averages (ms per call)
SKILL_AVG_MS = {
    "extract_page_label":   102,
    "analyze_diagram":      145,
    "shape_table":           62,
    "build_semantic_text":  120,
    "build_semantic_diag":  120,
    "build_doc_summary":  17300,
    "process_document":     763,
}

RECORD_TYPES = ["text", "diagram", "table", "table_row", "summary"]


def query_count(base: str, headers: dict, filter_clause: str) -> int:
    body = {"search": "*", "filter": filter_clause, "top": 0, "count": True}
    r = httpx.post(base, json=body, headers=headers, timeout=30)
    if r.status_code != 200:
        print(f"  query failed: HTTP {r.status_code}", file=sys.stderr)
        return 0
    return int(r.json().get("@odata.count", 0))


def query_distinct_pages(base: str, headers: dict, pdf: str) -> int:
    body = {
        "search": "*",
        "filter": f"source_file eq '{pdf}' and record_type eq 'text'",
        "facets": ["printed_page_label,count:500"],
        "top": 0,
        "count": True,
    }
    r = httpx.post(base, json=body, headers=headers, timeout=30)
    if r.status_code != 200:
        return 0
    facets = r.json().get("@search.facets", {}).get("printed_page_label", [])
    return len(facets)


def estimate_time(counts: dict[str, int]) -> dict[str, float]:
    """Estimate total time spent in skill calls based on observed averages.
    Returns dict of {skill_name: total_seconds}."""
    text   = counts.get("text", 0)
    diag   = counts.get("diagram", 0)
    table  = counts.get("table", 0)
    row    = counts.get("table_row", 0)
    summary = counts.get("summary", 0)

    # Page-label extraction runs on every text + diagram + table record
    page_label_calls = text + diag + table
    # Diagram analysis runs on every diagram record
    diag_calls = diag
    # Shape-table runs on every table record
    table_calls = table
    # Semantic strings run on text + diagram records
    sem_text_calls = text
    sem_diag_calls = diag
    # Doc summary runs once per PDF
    doc_summary_calls = summary
    # Process-document runs once per PDF
    process_doc_calls = summary  # 1 per PDF — summary count is a proxy

    return {
        "extract_page_label": page_label_calls * SKILL_AVG_MS["extract_page_label"] / 1000,
        "analyze_diagram":    diag_calls       * SKILL_AVG_MS["analyze_diagram"]    / 1000,
        "shape_table":        table_calls      * SKILL_AVG_MS["shape_table"]        / 1000,
        "build_semantic_text":sem_text_calls   * SKILL_AVG_MS["build_semantic_text"]/ 1000,
        "build_semantic_diag":sem_diag_calls   * SKILL_AVG_MS["build_semantic_diag"]/ 1000,
        "build_doc_summary":  doc_summary_calls* SKILL_AVG_MS["build_doc_summary"]  / 1000,
        "process_document":   process_doc_calls* SKILL_AVG_MS["process_document"]   / 1000,
    }


def inspect(base: str, headers: dict, pdf: str) -> dict[str, int]:
    print(f"\n{'=' * 70}")
    print(f"  {pdf}")
    print('=' * 70)

    # Total
    total = query_count(base, headers, f"source_file eq '{pdf}'")
    print(f"\n  Total chunks in index:  {total:,}")

    if total == 0:
        print("\n  (no records — PDF has not been indexed yet)")
        return {}

    # Breakdown by record_type
    print("\n  Breakdown by record_type:")
    counts: dict[str, int] = {}
    for rt in RECORD_TYPES:
        c = query_count(base, headers, f"source_file eq '{pdf}' and record_type eq '{rt}'")
        counts[rt] = c
        if c > 0:
            label = {
                "text":      "Text chunks (paragraphs / sections)",
                "diagram":   "Diagrams / figures",
                "table":     "Tables",
                "table_row": "Per-row table records",
                "summary":   "Document summaries",
            }[rt]
            print(f"    {rt:12s}: {c:>7,}    {label}")

    # Pages with content
    pages = query_distinct_pages(base, headers, pdf)
    if pages:
        print(f"\n  Distinct pages with text content:  {pages}")

    # Time estimate
    times = estimate_time(counts)
    total_serial = sum(times.values())
    print("\n  Estimated time spent in AI skill calls (cumulative):")
    for skill, secs in sorted(times.items(), key=lambda kv: -kv[1]):
        if secs < 0.5:
            continue
        mins = secs / 60
        if mins >= 1:
            print(f"    {skill:22s}: {secs:>7.0f} sec  ({mins:>5.1f} min)")
        else:
            print(f"    {skill:22s}: {secs:>7.1f} sec")
    print(f"    {'-' * 50}")
    print(f"    {'TOTAL (serial)':22s}: {total_serial:>7.0f} sec  ({total_serial/60:>5.1f} min)")
    print(f"    {'TOTAL @ dop=2':22s}: {total_serial/2:>7.0f} sec  ({total_serial/120:>5.1f} min)")

    return counts


def list_indexed_pdfs(base: str, headers: dict) -> list[str]:
    body = {
        "search": "*",
        "facets": ["source_file,count:200"],
        "top": 0,
    }
    r = httpx.post(base, json=body, headers=headers, timeout=30)
    facets = r.json().get("@search.facets", {}).get("source_file", [])
    return sorted(f["value"] for f in facets)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--pdf", help="PDF filename to inspect (e.g. ED-EM-SSM.pdf)")
    ap.add_argument("--all", action="store_true", help="Inspect every indexed PDF")
    args = ap.parse_args()

    if not args.pdf and not args.all:
        print("Pass --pdf <filename> or --all", file=sys.stderr)
        sys.exit(2)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"

    token = DefaultAzureCredential().get_token("https://search.azure.us/.default").token
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    base = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"

    print(f"Index: {index_name}")
    print(f"Endpoint: {endpoint}")

    if args.all:
        pdfs = list_indexed_pdfs(base, headers)
        print(f"\nInspecting {len(pdfs)} indexed PDFs...")
        for pdf in pdfs:
            inspect(base, headers, pdf)
    else:
        inspect(base, headers, args.pdf)


if __name__ == "__main__":
    main()
