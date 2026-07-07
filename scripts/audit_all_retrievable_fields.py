# placeholder — paste real content from office laptop
"""
Full-corpus field quality audit for ALL retrievable index fields.
 
This script scans all records in the live index (partitioned by source_file
so it can pass Azure Search skip limits) and produces:
- full-field null/empty rates by record_type
- cross-field consistency checks (page coordinate sanity)
- content-quality checks for chatbot-critical text fields
- prioritized findings (critical/high/medium)
 
Outputs:
- reports/all_fields_audit.json
- reports/all_fields_audit.md
 
Usage:
  python scripts/audit_all_retrievable_fields.py --config deploy.config.json
"""
 
from __future__ import annotations
 
import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
CRITICAL_REQUIRED_BY_TYPE: dict[str, list[str]] = {
    "text": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "physical_pdf_pages", "page_resolution_method",
        "processing_status", "skill_version", "retrieval_eligible",
    ],
    "diagram": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "has_diagram", "diagram_category", "retrieval_eligible",
    ],
    "table": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_row_count", "table_col_count", "retrieval_eligible",
    ],
    "table_row": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_parent_chunk_id", "table_row_index", "retrieval_eligible",
        "table_row_quality", "table_row_search_text",
    ],
    "summary": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "processing_status", "skill_version", "retrieval_eligible",
    ],
}
 
RETRIEVAL_REQUIRED_FIELDS = [
    "chunk_id",
    "source_file",
    "record_type",
    "header_1",
    "chunk",
    "content_class",
    "retrieval_eligible_reason",
]
 
VALID_CONTENT_CLASSES = {
    "operational_content",
    "table_content",
    "figure_content",
    "procedure_step",
    "locator_artifact",
    "summary_content",
    "other",
}
 
PLACEHOLDER_RE = re.compile(r"^\s*(?:n/?a|na|none|null|nil|unknown|tbd|tba|--+|\.{3,}|_+|\-+)?\s*$", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
 
 
def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
 
def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    return False
 
 
def _is_placeholder(v: str) -> bool:
    return bool(PLACEHOLDER_RE.match(v or ""))
 
 
def _severity_sort_key(s: str) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return order.get(s, 99)
 
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Audit all retrievable fields for quality")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--out-json", default="reports/all_fields_audit.json")
    ap.add_argument("--out-md", default="reports/all_fields_audit.md")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
 
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_token()}",
    }
 
    idx = httpx.get(index_url, headers=headers, timeout=60.0)
    idx.raise_for_status()
    schema_fields = idx.json().get("fields", [])
 
    retrievable_fields = [
        f.get("name") for f in schema_fields
        if f.get("retrievable") is True and f.get("name")
    ]
    retrievable_set = set(retrievable_fields)
 
    meta = httpx.post(
        search_url,
        headers=headers,
        json={"search": "*", "top": 0, "count": True, "facets": ["source_file,count:500"]},
        timeout=120.0,
    )
    meta.raise_for_status()
    meta_json = meta.json()
    service_total = int(meta_json.get("@odata.count") or 0)
    source_files = [
        x.get("value")
        for x in (meta_json.get("@search.facets", {}).get("source_file") or [])
        if x.get("value")
    ]
 
    counts_by_type: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
 
    # field quality stats
    field_missing: dict[str, Counter[str]] = defaultdict(Counter)
    field_nonmissing: dict[str, Counter[str]] = defaultdict(Counter)
 
    # critical checks
    required_missing: dict[str, Counter[str]] = defaultdict(Counter)
    anomalies: Counter[str] = Counter()
    anomaly_by_type: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[Any]] = defaultdict(list)
 
    scanned = 0
    page = 1000
    select_str = ",".join(retrievable_fields)
 
    for sf in source_files:
        safe_sf = str(sf).replace("'", "''")
        skip = 0
 
        while True:
            body = {
                "search": "*",
                "filter": f"source_file eq '{safe_sf}'",
                "select": select_str,
                "top": page,
                "skip": skip,
            }
            r = httpx.post(search_url, headers=headers, json=body, timeout=120.0)
            r.raise_for_status()
            vals = r.json().get("value", [])
            if not vals:
                break
 
            for rec in vals:
                scanned += 1
                rt = rec.get("record_type") or "NULL"
                counts_by_type[rt] += 1
                status_counts[rec.get("processing_status") or "NULL"] += 1
 
                # Field stats for every retrievable field
                for f in retrievable_fields:
                    v = rec.get(f)
                    if _is_missing(v):
                        field_missing[rt][f] += 1
                    else:
                        field_nonmissing[rt][f] += 1
 
                # Required field checks by record type
                for f in CRITICAL_REQUIRED_BY_TYPE.get(rt, []):
                    if f in retrievable_set and _is_missing(rec.get(f)):
                        required_missing[rt][f] += 1
                        k = f"missing_required:{rt}:{f}"
                        if len(examples[k]) < 5:
                            examples[k].append(rec.get("chunk_id"))
 
                # Retrieval contract checks: retrieval_eligible rows must
                # carry mandatory grounding fields and linkage keys.
                retrieval = bool(rec.get("retrieval_eligible")) if "retrieval_eligible" in retrievable_set else False
                if retrieval:
                    for f in RETRIEVAL_REQUIRED_FIELDS:
                        if f in retrievable_set and _is_missing(rec.get(f)):
                            anomalies[f"retrieval_missing_{f}"] += 1
                            anomaly_by_type[rt][f"retrieval_missing_{f}"] += 1
                    if rt in {"text", "diagram", "table", "table_row"} and "physical_pdf_page" in retrievable_set and _is_missing(rec.get("physical_pdf_page")):
                        anomalies["retrieval_missing_physical_pdf_page"] += 1
                        anomaly_by_type[rt]["retrieval_missing_physical_pdf_page"] += 1
                    if rt == "table_row" and "table_cluster_id" in retrievable_set and _is_missing(rec.get("table_cluster_id")):
                        anomalies["retrieval_table_row_missing_cluster_id"] += 1
                        anomaly_by_type[rt]["retrieval_table_row_missing_cluster_id"] += 1
 
                # content_class and artifact policy checks
                content_class = str(rec.get("content_class") or "").strip().lower() if "content_class" in retrievable_set else ""
                if content_class and content_class not in VALID_CONTENT_CLASSES:
                    anomalies["invalid_content_class"] += 1
                    anomaly_by_type[rt]["invalid_content_class"] += 1
 
                if content_class == "locator_artifact" and retrieval:
                    anomalies["locator_artifact_retrieval_true"] += 1
                    anomaly_by_type[rt]["locator_artifact_retrieval_true"] += 1
 
                if "is_locator_artifact" in retrievable_set and bool(rec.get("is_locator_artifact")) and retrieval:
                    anomalies["is_locator_artifact_retrieval_true"] += 1
                    anomaly_by_type[rt]["is_locator_artifact_retrieval_true"] += 1
 
                if "locator_type" in retrievable_set and not _is_missing(rec.get("locator_type")):
                    lt = str(rec.get("locator_type") or "").strip().lower()
                    if lt not in {"none", "page", "section", "figure", "table", "step", "header"}:
                        anomalies["invalid_locator_type"] += 1
                        anomaly_by_type[rt]["invalid_locator_type"] += 1
 
                # table integrity checks for table + table_row records
                if rt in {"table", "table_row"}:
                    if "table_integrity_score" in retrievable_set and not _is_missing(rec.get("table_integrity_score")):
                        try:
                            tscore = float(rec.get("table_integrity_score"))
                            if tscore < 0.0 or tscore > 1.0:
                                anomalies["table_integrity_score_out_of_range"] += 1
                                anomaly_by_type[rt]["table_integrity_score_out_of_range"] += 1
                        except Exception:
                            anomalies["table_integrity_score_not_numeric"] += 1
                            anomaly_by_type[rt]["table_integrity_score_not_numeric"] += 1
                    if retrieval and "table_columns" in retrievable_set and _is_missing(rec.get("table_columns")):
                        anomalies["retrieval_table_missing_columns"] += 1
                        anomaly_by_type[rt]["retrieval_table_missing_columns"] += 1
                    if rt == "table_row" and retrieval and "table_row_cells" in retrievable_set and _is_missing(rec.get("table_row_cells")):
                        anomalies["retrieval_table_row_missing_cells"] += 1
                        anomaly_by_type[rt]["retrieval_table_row_missing_cells"] += 1
 
                # applicability checks for retrieval-eligible operational content
                if retrieval and rt in {"text", "table", "table_row", "diagram"}:
                    has_equipment = bool(rec.get("applies_to_equipment")) if "applies_to_equipment" in retrievable_set else False
                    has_system = bool(rec.get("applies_to_system")) if "applies_to_system" in retrievable_set else False
                    has_voltage = bool(rec.get("applies_to_voltage")) if "applies_to_voltage" in retrievable_set else False
                    if not (has_equipment or has_system or has_voltage):
                        anomalies["retrieval_missing_applicability_tags"] += 1
                        anomaly_by_type[rt]["retrieval_missing_applicability_tags"] += 1
 
                # procedure linkage checks
                if rt in {"text", "table_row"} and "procedure_step_id" in retrievable_set and _is_missing(rec.get("procedure_step_id")) is False:
                    if "procedure_step_order" in retrievable_set and _is_missing(rec.get("procedure_step_order")):
                        anomalies["procedure_step_missing_order"] += 1
                        anomaly_by_type[rt]["procedure_step_missing_order"] += 1
 
                if rt == "diagram" and "figure_step_linked" in retrievable_set and bool(rec.get("figure_step_linked")):
                    if "figure_linkage_confidence" in retrievable_set and _is_missing(rec.get("figure_linkage_confidence")):
                        anomalies["figure_step_linked_missing_confidence"] += 1
                        anomaly_by_type[rt]["figure_step_linked_missing_confidence"] += 1
 
                # Page-coordinate consistency
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
                        anomalies["physical_page_not_min_of_list"] += 1
                        anomaly_by_type[rt]["physical_page_not_min_of_list"] += 1
                    if isinstance(pe, int) and pe != pmax:
                        anomalies["physical_page_end_not_max_of_list"] += 1
                        anomaly_by_type[rt]["physical_page_end_not_max_of_list"] += 1
                    if not contiguous:
                        anomalies["physical_pdf_pages_non_contiguous"] += 1
                        anomaly_by_type[rt]["physical_pdf_pages_non_contiguous"] += 1
 
                    if len(plist) >= 4:
                        anomalies["pages_list_len_ge_4"] += 1
                        anomaly_by_type[rt]["pages_list_len_ge_4"] += 1
                    if len(plist) >= 5:
                        anomalies["pages_list_len_ge_5"] += 1
                        anomaly_by_type[rt]["pages_list_len_ge_5"] += 1
 
                # Chatbot content quality checks
                chunk = rec.get("chunk")
                if isinstance(chunk, str):
                    s = chunk.strip()
                    if not s:
                        anomalies["empty_chunk"] += 1
                        anomaly_by_type[rt]["empty_chunk"] += 1
                    else:
                        if len(s) < 20:
                            anomalies["very_short_chunk_lt20"] += 1
                            anomaly_by_type[rt]["very_short_chunk_lt20"] += 1
                        if CONTROL_RE.search(s):
                            anomalies["control_chars_in_chunk"] += 1
                            anomaly_by_type[rt]["control_chars_in_chunk"] += 1
                        if _is_placeholder(s):
                            anomalies["placeholder_like_chunk"] += 1
                            anomaly_by_type[rt]["placeholder_like_chunk"] += 1
 
                # Retrieval policy consistency
                trq = (rec.get("table_row_quality") or "").strip().lower() if "table_row_quality" in retrievable_set else ""
                trst = (rec.get("table_row_search_text") or "") if "table_row_search_text" in retrievable_set else ""
                if rt == "table_row" and retrieval and trq == "noise":
                    anomalies["noise_but_retrieval_eligible"] += 1
                    anomaly_by_type[rt]["noise_but_retrieval_eligible"] += 1
                if rt == "table_row" and retrieval and not trst.strip():
                    anomalies["retrieval_true_missing_table_row_search_text"] += 1
                    anomaly_by_type[rt]["retrieval_true_missing_table_row_search_text"] += 1
 
            if len(vals) < page:
                break
            skip += page
 
    # Build field rates
    field_rates_by_type: dict[str, dict[str, dict[str, float | int]]] = {}
    findings: list[dict[str, Any]] = []
 
    for rt, total in counts_by_type.items():
        rt_map: dict[str, dict[str, float | int]] = {}
        for f in retrievable_fields:
            miss = field_missing[rt][f]
            present = field_nonmissing[rt][f]
            rt_map[f] = {
                "missing": miss,
                "present": present,
                "missing_pct": round(100.0 * miss / total, 3) if total else 0.0,
                "present_pct": round(100.0 * present / total, 3) if total else 0.0,
            }
        field_rates_by_type[rt] = rt_map
 
    # Severity-based findings
    for rt, misses in required_missing.items():
        for f, c in misses.items():
            if c > 0:
                findings.append({
                    "severity": "critical",
                    "category": "required_field_missing",
                    "record_type": rt,
                    "field": f,
                    "count": c,
                    "message": f"Required field missing: {rt}.{f} has {c} missing records",
                })
 
    for k in [
        "physical_page_not_min_of_list",
        "physical_page_end_not_max_of_list",
        "physical_pdf_pages_non_contiguous",
        "invalid_content_class",
        "locator_artifact_retrieval_true",
        "is_locator_artifact_retrieval_true",
        "invalid_locator_type",
        "table_integrity_score_out_of_range",
        "table_integrity_score_not_numeric",
        "retrieval_table_missing_columns",
        "retrieval_table_row_missing_cells",
        "retrieval_missing_applicability_tags",
        "procedure_step_missing_order",
        "figure_step_linked_missing_confidence",
    ]:
        c = anomalies[k]
        if c > 0:
            findings.append({
                "severity": "critical",
                "category": "page_coordinate_integrity",
                "record_type": "*",
                "field": "physical_pdf_page/physical_pdf_page_end/physical_pdf_pages",
                "count": c,
                "message": f"Page-coordinate integrity issue: {k}={c}",
            })
 
    for k in ["pages_list_len_ge_5", "very_short_chunk_lt20", "placeholder_like_chunk", "control_chars_in_chunk"]:
        c = anomalies[k]
        if c > 0:
            sev = "high" if k in {"pages_list_len_ge_5", "control_chars_in_chunk"} else "medium"
            findings.append({
                "severity": sev,
                "category": "content_quality",
                "record_type": "*",
                "field": "chunk",
                "count": c,
                "message": f"Content quality issue: {k}={c}",
            })
 
    # Optional: flag likely-expected fields with high missing rates
    for rt, total in counts_by_type.items():
        for f in retrievable_fields:
            miss = field_missing[rt][f]
            pct = (100.0 * miss / total) if total else 0.0
            # Candidate for quality concern when field is mostly present in this type
            # but still has non-trivial gaps.
            present = field_nonmissing[rt][f]
            present_pct = (100.0 * present / total) if total else 0.0
            if present_pct >= 70.0 and miss > 0 and pct >= 1.0:
                findings.append({
                    "severity": "high",
                    "category": "field_inconsistency",
                    "record_type": rt,
                    "field": f,
                    "count": miss,
                    "message": f"Field mostly expected for {rt} but missing in {miss} records ({pct:.2f}%)",
                })
 
    findings.sort(key=lambda x: (_severity_sort_key(str(x.get("severity"))), -int(x.get("count", 0))))
 
    out = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index_name": index_name,
        "total_records_scanned": scanned,
        "total_records_reported_by_service": service_total,
        "full_coverage": scanned == service_total,
        "retrievable_fields_count": len(retrievable_fields),
        "retrievable_fields": retrievable_fields,
        "counts_by_record_type": dict(counts_by_type),
        "processing_status_counts": dict(status_counts),
        "required_field_missing_counts": {k: dict(v) for k, v in required_missing.items()},
        "anomaly_counts": dict(anomalies),
        "anomaly_counts_by_record_type": {k: dict(v) for k, v in anomaly_by_type.items()},
        "field_rates_by_record_type": field_rates_by_type,
        "findings": findings,
        "examples": dict(examples),
    }
 
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
 
    lines: list[str] = []
    lines.append("# All Retrievable Fields Audit")
    lines.append("")
    lines.append(f"Generated at: {out['generated_at']}")
    lines.append(f"Index: {index_name}")
    lines.append(f"Coverage: {scanned}/{service_total} full={scanned == service_total}")
    lines.append(f"Retrievable fields audited: {len(retrievable_fields)}")
    lines.append("")
    lines.append("## Critical Findings")
    critical = [f for f in findings if f.get("severity") == "critical"]
    if critical:
        for f in critical[:100]:
            lines.append(f"- {f['message']}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## High Findings")
    high = [f for f in findings if f.get("severity") == "high"]
    if high:
        for f in high[:100]:
            lines.append(f"- {f['message']}")
    else:
        lines.append("- none")
 
    out_md.write_text("\n".join(lines), encoding="utf-8")
 
    print(f"Index: {index_name}")
    print(f"Coverage: {scanned}/{service_total} full={scanned == service_total}")
    print(f"Audited retrievable fields: {len(retrievable_fields)}")
    print(f"Critical findings: {len(critical)}")
    print(f"High findings: {len(high)}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
 