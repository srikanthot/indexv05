"""
Validate that indexed records have required fields populated.
 
Queries the live index and checks per-record-type field contracts.
Reports violations as warnings (default) or hard-fails the pipeline
when --strict is passed.
 
This is a non-destructive read-only check. Safe to run any time.
 
Usage:
    python scripts/validate_index.py --config deploy.config.json
    python scripts/validate_index.py --config deploy.config.json --strict
    python scripts/validate_index.py --config deploy.config.json --sample 5
"""
 
from __future__ import annotations
 
import argparse
import json
import sys
from pathlib import Path
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
# ─── Required field contracts per record_type ───────────────────────
# Fields listed here MUST be non-null and non-empty on every record
# of that type. A violation means the record is incomplete for
# production chatbot use.
 
REQUIRED_FIELDS = {
    "text": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "physical_pdf_pages", "page_resolution_method",
        "processing_status", "skill_version", "retrieval_eligible", "content_class",
    ],
    "diagram": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "has_diagram", "diagram_category", "retrieval_eligible", "content_class",
    ],
    "table": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_row_count", "table_col_count", "retrieval_eligible", "content_class",
    ],
    "table_row": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_parent_chunk_id", "table_row_index", "retrieval_eligible",
        "table_row_quality", "table_row_search_text", "content_class",
    ],
    "summary": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "processing_status", "skill_version", "retrieval_eligible",
    ],
}
 
# Fields that SHOULD be populated (warn but don't fail)
RECOMMENDED_FIELDS = {
    "text": ["header_1", "highlight_text", "applies_to_equipment", "applies_to_system", "applies_to_voltage"],
    "diagram": ["diagram_description", "figure_ref", "diagram_ocr_text", "figure_linkage_confidence", "figure_step_linked"],
    "table": [
        "table_caption", "highlight_text", "table_cluster_id", "table_columns",
        "table_integrity_score", "table_variant_id", "table_scope_tags",
    ],
    "table_row": [
        "table_caption", "highlight_text", "table_cluster_id",
        "table_row_quality_reason_codes", "table_row_semantic_key",
        "table_row_semantic_value", "table_context_path", "table_row_cells",
        "suggested_for_eval_question",
    ],
    "summary": ["highlight_text"],
}
 
 
VALID_CONTENT_CLASSES = {
    "operational_content",
    "table_content",
    "figure_content",
    "procedure_step",
    "locator_artifact",
    "summary_content",
    "other",
}
 
VALID_LOCATOR_TYPES = {
    "",
    "none",
    "page",
    "section",
    "figure",
    "table",
    "step",
    "header",
}
 
 
def _extra_policy_checks(record_type: str, rec: dict) -> list[str]:
    errs: list[str] = []
    retrieval = bool(rec.get("retrieval_eligible"))
    content_class = str(rec.get("content_class") or "").strip().lower()
 
    # Global retrieval contract: retrieval_eligible rows must carry
    # core grounding fields so citations and filters remain deterministic.
    if retrieval:
        for field in ("chunk_id", "source_file", "record_type", "chunk"):
            if not _is_populated(rec.get(field)):
                errs.append(f"retrieval_eligible=true but missing {field}")
        if not _is_populated(rec.get("retrieval_eligible_reason")):
            errs.append("retrieval_eligible=true but retrieval_eligible_reason empty")
        if record_type in {"text", "diagram", "table", "table_row"} and not _is_populated(rec.get("header_1")):
            errs.append("retrieval_eligible=true but header_1 empty")
 
    # Page contract for record types that should always map to a physical page.
    if retrieval and record_type in {"text", "diagram", "table", "table_row"}:
        if not _is_populated(rec.get("physical_pdf_page")):
            errs.append("retrieval_eligible=true but physical_pdf_page empty")
 
    # Table-row linkage contract.
    if retrieval and record_type == "table_row" and not _is_populated(rec.get("table_cluster_id")):
        errs.append("retrieval_eligible=true but table_cluster_id empty")
 
    if content_class and content_class not in VALID_CONTENT_CLASSES:
        errs.append(f"content_class has unsupported value '{content_class}'")
 
    if retrieval and content_class == "locator_artifact":
        errs.append("locator_artifact must not be retrieval_eligible")
 
    locator_type = str(rec.get("locator_type") or "").strip().lower()
    if locator_type and locator_type not in VALID_LOCATOR_TYPES:
        errs.append(f"locator_type has unsupported value '{locator_type}'")
    if bool(rec.get("is_locator_artifact")) and retrieval:
        errs.append("is_locator_artifact=true must not be retrieval_eligible")
 
    if record_type == "table":
        tscore = rec.get("table_integrity_score")
        if tscore is not None:
            try:
                score = float(tscore)
                if score < 0.0 or score > 1.0:
                    errs.append("table_integrity_score must be within [0,1]")
            except Exception:
                errs.append("table_integrity_score must be numeric")
 
    if record_type == "diagram":
        link_conf = rec.get("figure_linkage_confidence")
        if link_conf is not None:
            try:
                lscore = float(link_conf)
                if lscore < 0.0 or lscore > 1.0:
                    errs.append("figure_linkage_confidence must be within [0,1]")
            except Exception:
                errs.append("figure_linkage_confidence must be numeric")
 
    if record_type in {"text", "table_row"} and _is_populated(rec.get("procedure_step_id")):
        if not _is_populated(rec.get("procedure_step_order")):
            errs.append("procedure_step_id present but procedure_step_order empty")
 
    if record_type != "table_row":
        return errs
 
    search_text = (rec.get("table_row_search_text") or "").strip()
    quality = (rec.get("table_row_quality") or "").strip().lower()
    is_index_like = bool(rec.get("table_row_is_index_like"))
    is_placeholder_like = bool(rec.get("table_row_is_placeholder_like"))
 
    if retrieval and not search_text:
        errs.append("retrieval_eligible=true but table_row_search_text empty")
    if is_index_like and retrieval:
        errs.append("index_like row must not be retrieval_eligible")
    if is_placeholder_like and retrieval:
        errs.append("placeholder_like row must not be retrieval_eligible")
    if quality == "noise" and retrieval:
        errs.append("noise row must not be retrieval_eligible")
    return errs
 
 
def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
 
def _sample_records(endpoint: str, index_name: str, token: str,
                    record_type: str, sample_size: int) -> list[dict]:
    """Fetch a sample of records for the given record_type."""
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    # Select all fields we need to check
    all_fields = set(
        REQUIRED_FIELDS.get(record_type, [])
        + RECOMMENDED_FIELDS.get(record_type, [])
        + ["header_1", "physical_pdf_page", "table_cluster_id", "table_row_search_text",
            "table_row_quality", "table_row_is_index_like", "table_row_is_placeholder_like",
            "retrieval_eligible_reason", "content_class", "table_integrity_score",
            "table_variant_id", "table_scope_tags", "table_columns", "table_row_cells",
            "applies_to_equipment", "applies_to_system", "applies_to_voltage",
            "procedure_id", "procedure_step_id", "procedure_step_order", "procedure_branch_label",
            "figure_step_linked", "figure_linkage_confidence",
            "locator_type", "locator_value", "is_locator_artifact", "artifact_reason_codes"]
    )
    select = ",".join(sorted(all_fields | {"chunk_id", "source_file", "record_type"}))
 
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    out: list[dict] = []
    skip = 0
    page_size = 1000 if sample_size <= 0 else min(sample_size, 1000)
    while True:
        body = {
            "search": "*",
            "filter": f"record_type eq '{record_type}'",
            "select": select,
            "top": page_size,
            "skip": skip,
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=60.0)
        if resp.status_code != 200:
            print(f"  WARNING: query for {record_type} returned {resp.status_code}")
            return out
        batch = resp.json().get("value", [])
        if not batch:
            break
        out.extend(batch)
        if sample_size > 0 and len(out) >= sample_size:
            return out[:sample_size]
        if len(batch) < page_size:
            break
        skip += page_size
    return out
 
 
def _is_populated(value) -> bool:
    """True if field value is meaningfully populated (not null/empty)."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    return True
 
 
def validate(endpoint: str, index_name: str, token: str,
             sample_size: int) -> dict:
    """Run validation across all record types. Returns stats dict."""
    results = {
        "passed": True,
        "record_types": {},
        "total_violations": 0,
        "total_warnings": 0,
    }
 
    for rtype in REQUIRED_FIELDS:
        print(f"\n  Checking record_type={rtype}...")
        records = _sample_records(endpoint, index_name, token, rtype, sample_size)
        if not records:
            print(f"    No records found for {rtype}")
            results["record_types"][rtype] = {
                "sampled": 0, "violations": 0, "warnings": 0,
            }
            continue
 
        violations = []
        warnings = []
 
        for rec in records:
            cid = rec.get("chunk_id", "?")
            sf = rec.get("source_file", "?")
            # Check required fields
            for field in REQUIRED_FIELDS[rtype]:
                if not _is_populated(rec.get(field)):
                    violations.append(f"{cid} ({sf}): missing required '{field}'")
            for extra in _extra_policy_checks(rtype, rec):
                violations.append(f"{cid} ({sf}): {extra}")
            # Check recommended fields
            for field in RECOMMENDED_FIELDS.get(rtype, []):
                if not _is_populated(rec.get(field)):
                    warnings.append(f"{cid} ({sf}): missing recommended '{field}'")
 
        # Print first few violations
        for v in violations[:5]:
            print(f"    VIOLATION: {v}")
        if len(violations) > 5:
            print(f"    ... and {len(violations) - 5} more violations")
 
        for w in warnings[:3]:
            print(f"    WARNING: {w}")
        if len(warnings) > 3:
            print(f"    ... and {len(warnings) - 3} more warnings")
 
        results["record_types"][rtype] = {
            "sampled": len(records),
            "violations": len(violations),
            "warnings": len(warnings),
        }
        results["total_violations"] += len(violations)
        results["total_warnings"] += len(warnings)
        if violations:
            results["passed"] = False
 
    return results
 
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Validate index field completeness")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on any violation (use as pipeline gate)")
    ap.add_argument("--sample", type=int, default=10,
                    help="Number of records to sample per record_type (default 10, use 0 for full corpus)")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    print(f"Validating index: {index_name}")
    print(f"  Sample size: {args.sample} per record_type")
    print(f"  Mode: {'STRICT (fail on violations)' if args.strict else 'report only'}")
 
    token = _token()
    results = validate(endpoint, index_name, token, args.sample)
 
    # Summary
    print("\n" + "=" * 60)
    print("  VALIDATION SUMMARY")
    print("=" * 60)
    for rtype, stats in results["record_types"].items():
        status = "PASS" if stats["violations"] == 0 else "FAIL"
        print(f"  {rtype:12s}  sampled={stats['sampled']:3d}  "
              f"violations={stats['violations']:3d}  "
              f"warnings={stats['warnings']:3d}  [{status}]")
    print()
    print(f"  Total violations: {results['total_violations']}")
    print(f"  Total warnings:   {results['total_warnings']}")
    print(f"  Overall: {'PASS' if results['passed'] else 'FAIL'}")
 
    if args.strict and not results["passed"]:
        print("\n  STRICT MODE: Exiting with error due to violations.")
        return 1
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 