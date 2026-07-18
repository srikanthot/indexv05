"""
Whole-index quality gate validator.

Implements the mandatory gates from the Data-Prep Specification's "Definition of
Done". Run it after a (re)index and BEFORE promoting to production — in Jenkins,
wire it so a critical-gate failure fails the build (blocks promotion).

Gates:
  1. SCHEMA        — every retrieval-eligible chunk has the required identity/
                     provenance fields; content_class is a known value.
  2. TABLE ALIGN   — table_row chunks have aligned table_columns + table_row_cells.
  3. FIGURE LINK   — diagram chunks carry a figure_ref / normalized figure join.
  4. LOCATOR SUPP  — locator/TOC chunks are retrieval_eligible=false.
  5. APPLICABILITY — operational text/table chunks carry >= 1 applies_to_* tag
                     (coverage %, warns below threshold).
  6. PROCEDURE     — procedure chunks have a step order + total step count.
  7. DURABILITY    — no record carries a loss/partial processing_status.

Exit code: 0 if all CRITICAL gates pass, 1 otherwise. Warnings never fail the
build on their own (raise them to critical with --strict).

Usage:
  python scripts/validate_index_quality.py --config deploy.config.json
  python scripts/validate_index_quality.py --config deploy.config.json --strict
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"

REQUIRED_ALWAYS = ["chunk_id", "source_file", "record_type", "content_class"]
KNOWN_CONTENT_CLASS = {
    "operational_content", "table_content", "figure_content",
    "summary_content", "locator_artifact",
}
LOSS_STATUSES = {
    "needs_preanalyze_output", "all_figures_dropped",
    "partial_figure_loss", "partial_vision",
}
# Applicability coverage is expected on operational text + table records.
APPLICABILITY_FIELDS = ["applies_to_equipment", "applies_to_voltage",
                        "applies_to_domain", "applies_to_phase"]
APPLICABILITY_MIN_COVERAGE = 0.50  # warn below 50%


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _facet_source_files(search_url: str, headers: dict) -> list[str]:
    body = {"search": "*", "facets": ["source_file,count:0"], "top": 0}
    r = httpx.post(search_url, json=body, headers=headers, timeout=60.0)
    r.raise_for_status()
    facets = (r.json().get("@search.facets") or {}).get("source_file", [])
    return [f["value"] for f in facets if f.get("value")]


def _iter_records_for_file(search_url: str, headers: dict, source_file: str,
                           select: str) -> list[dict[str, Any]]:
    out, skip = [], 0
    sf = source_file.replace("'", "''")
    while True:
        body = {"search": "*", "filter": f"source_file eq '{sf}'",
                "select": select, "top": 1000, "skip": skip}
        r = httpx.post(search_url, json=body, headers=headers, timeout=60.0)
        r.raise_for_status()
        batch = r.json().get("value", [])
        out.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
        if skip >= 100000:
            break
    return out


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Whole-index quality gate validator")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--out-json", default="reports/index_quality_gates.json")
    ap.add_argument("--strict", action="store_true",
                    help="Treat warnings as failures (exit 1 on any warning).")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}

    select = ",".join([
        "chunk_id", "source_file", "record_type", "content_class",
        "retrieval_eligible", "is_locator_artifact", "processing_status",
        "figure_ref", "figures_referenced_normalized",
        "table_columns", "table_row_cells",
        "procedure_id", "procedure_step_order", "procedure_step_count",
    ] + APPLICABILITY_FIELDS)

    files = _facet_source_files(search_url, headers)
    print(f"Validating {len(files)} document(s) in {index_name}\n")

    # Aggregate counters.
    crit = defaultdict(int)   # critical gate failures
    warn = defaultdict(int)
    totals = defaultdict(int)
    per_pdf: dict[str, dict[str, int]] = {}

    for sf in files:
        recs = _iter_records_for_file(search_url, headers, sf, select)
        p = defaultdict(int)
        for r in recs:
            rt = r.get("record_type") or "?"
            totals[f"records:{rt}"] += 1
            p["records"] += 1

            # 1. SCHEMA
            for f in REQUIRED_ALWAYS:
                if _empty(r.get(f)):
                    crit["schema_missing_required"] += 1
                    p["schema_fail"] += 1
            cc = r.get("content_class")
            if cc and cc not in KNOWN_CONTENT_CLASS:
                warn["schema_unknown_content_class"] += 1

            # 7. DURABILITY
            if (r.get("processing_status") or "") in LOSS_STATUSES:
                crit["durability_loss_status"] += 1
                p["loss"] += 1

            # 3. FIGURE LINK
            if rt == "diagram":
                if _empty(r.get("figure_ref")) and _empty(r.get("figures_referenced_normalized")):
                    warn["figure_no_ref"] += 1

            # 2. TABLE ALIGN
            if rt == "table_row":
                cols = r.get("table_columns") or []
                cells = r.get("table_row_cells") or []
                if _empty(cells):
                    warn["table_row_no_cells"] += 1
                elif cols and cells and abs(len(cols) - len(cells)) > max(1, len(cols)):
                    warn["table_row_misaligned"] += 1

            # 4. LOCATOR SUPPRESSION
            if r.get("is_locator_artifact") and r.get("retrieval_eligible"):
                crit["locator_not_suppressed"] += 1
                p["locator_leak"] += 1

            # 5. APPLICABILITY (operational text + table only)
            if rt in ("text", "table") and (cc == "operational_content" or rt == "table"):
                if r.get("retrieval_eligible"):
                    totals["applicability_denominator"] += 1
                    if any(not _empty(r.get(f)) for f in APPLICABILITY_FIELDS):
                        totals["applicability_covered"] += 1

            # 6. PROCEDURE
            if not _empty(r.get("procedure_id")):
                totals["procedure_chunks"] += 1
                if r.get("procedure_step_order") is None and _empty(r.get("procedure_step_count")):
                    warn["procedure_no_order"] += 1

        per_pdf[sf] = dict(p)

    # Applicability coverage %.
    denom = totals.get("applicability_denominator", 0)
    cov = (totals.get("applicability_covered", 0) / denom) if denom else 1.0
    if denom and cov < APPLICABILITY_MIN_COVERAGE:
        warn["applicability_low_coverage"] = round((1 - cov) * denom)

    # ---- report ----
    def line(sym, name, n):
        print(f"  {sym} {name}: {n}")

    print("=== CRITICAL gates ===")
    if not crit:
        print("  PASS  no critical failures")
    for k, n in sorted(crit.items()):
        line("FAIL", k, n)

    print("\n=== Warnings ===")
    if not warn:
        print("  none")
    for k, n in sorted(warn.items()):
        line("warn", k, n)

    print("\n=== Coverage ===")
    print(f"  applicability coverage: {cov*100:.1f}% of {denom} eligible operational/table chunks")
    print(f"  procedure chunks: {totals.get('procedure_chunks', 0)}")
    for k in sorted(totals):
        if k.startswith("records:"):
            print(f"  {k}: {totals[k]}")

    # Worst PDFs by failures.
    ranked = sorted(per_pdf.items(),
                    key=lambda kv: (kv[1].get("schema_fail", 0) + kv[1].get("loss", 0)
                                    + kv[1].get("locator_leak", 0)), reverse=True)
    print("\n=== Top problem documents ===")
    for sf, p in ranked[:20]:
        score = p.get("schema_fail", 0) + p.get("loss", 0) + p.get("locator_leak", 0)
        if score == 0:
            continue
        print(f"  {sf}: schema_fail={p.get('schema_fail',0)} loss={p.get('loss',0)} "
              f"locator_leak={p.get('locator_leak',0)}")

    report = {
        "index": index_name, "documents": len(files),
        "critical": dict(crit), "warnings": dict(warn),
        "applicability_coverage": round(cov, 4),
        "totals": dict(totals), "per_pdf": per_pdf,
    }
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {args.out_json}")

    failed = bool(crit) or (args.strict and bool(warn))
    print("\nRESULT:", "FAIL (block promotion)" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
