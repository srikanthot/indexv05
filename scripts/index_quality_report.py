# placeholder — paste real content from office laptop
"""Generate index quality reports and samples for table-row cleanup acceptance.
 
Outputs:
- index_quality_report.json
- index_quality_report.md
- reindex_run_summary.md
- golden_set_generation_report.md
- samples/noise_rows_sample.jsonl (up to 200)
- samples/high_quality_rows_sample.jsonl (up to 200)
"""
 
from __future__ import annotations
 
import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SEARCH_SCOPE = "https://search.azure.us/.default"
 
REQUIRED_REASON_CODES = {
    "EMPTY_OR_PUNCT_ONLY",
    "TOKEN_COUNT_TOO_LOW",
    "CHAR_COUNT_TOO_LOW",
    "PLACEHOLDER_LITERAL",
    "INDEX_LIKE_ROW",
    "PAGE_REF_ONLY",
    "HEADER_ONLY",
    "WEAK_SEMANTIC_SIGNAL",
    "VALID_SEMANTIC_KEY_VALUE",
}
 
 
def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
 
def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token
 
 
 
def _search(endpoint: str, index_name: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    r = httpx.post(url, json=body, headers=headers, timeout=120.0)
    if r.status_code != 200:
        raise RuntimeError(f"search failed ({r.status_code}): {r.text[:400]}")
    return r.json()
 
 
 
def _fetch_all_table_rows(endpoint: str, index_name: str, token: str) -> list[dict[str, Any]]:
    select = ",".join([
        "chunk_id",
        "source_file",
        "table_row_quality",
        "table_row_quality_reason_codes",
        "table_row_is_index_like",
        "table_row_is_placeholder_like",
        "table_row_semantic_key",
        "table_row_semantic_value",
        "table_row_search_text",
        "retrieval_eligible",
        "suggested_for_eval_question",
        "table_context_path",
        "chunk",
        "record_type",
    ])
 
    out: list[dict[str, Any]] = []
    skip = 0
    page_size = 1000
    while True:
        body = {
            "search": "*",
            "filter": "record_type eq 'table_row'",
            "select": select,
            "top": page_size,
            "skip": skip,
        }
        data = _search(endpoint, index_name, token, body)
        batch = data.get("value", [])
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page_size:
            break
        skip += page_size
    return out
 
 
 
def _facet_counts(endpoint: str, index_name: str, token: str) -> dict[str, Any]:
    body = {
        "search": "*",
        "count": True,
        "top": 0,
        "facets": [
            "record_type,count:20",
            "retrieval_eligible,count:5",
            "table_row_quality,count:10",
            "source_file,count:100",
        ],
    }
    data = _search(endpoint, index_name, token, body)
    return {
        "total_docs": data.get("@odata.count", 0),
        "facets": data.get("@search.facets", {}),
    }
 
 
 
def _sample_rows(rows: list[dict[str, Any]], predicate, n: int) -> list[dict[str, Any]]:
    filtered = [r for r in rows if predicate(r)]
    if len(filtered) <= n:
        return filtered
    random.seed(42)
    return random.sample(filtered, n)
 
 
 
def _pct(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(100.0 * num / den, 2)
 
 
 
def build_report(rows: list[dict[str, Any]], facets: dict[str, Any]) -> dict[str, Any]:
    by_quality = Counter((r.get("table_row_quality") or "") for r in rows)
    reason_counts = Counter()
    source_quality = defaultdict(Counter)
 
    for r in rows:
        sf = r.get("source_file") or ""
        q = r.get("table_row_quality") or ""
        source_quality[sf][q] += 1
        for code in r.get("table_row_quality_reason_codes") or []:
            reason_counts[code] += 1
 
    retrieval_true_rows = [r for r in rows if bool(r.get("retrieval_eligible"))]
    retrieval_true_search_text_nonempty = [
        r for r in retrieval_true_rows if (r.get("table_row_search_text") or "").strip()
    ]
 
    semantic_populated = [
        r for r in rows
        if (r.get("table_row_semantic_key") or "").strip() and (r.get("table_row_semantic_value") or "").strip()
    ]
 
    index_like_rows = [r for r in rows if bool(r.get("table_row_is_index_like"))]
    placeholder_rows = [r for r in rows if bool(r.get("table_row_is_placeholder_like"))]
 
    report = {
        "generated_at": _now_iso(),
        "total_counts_by_record_type": {
            x.get("value"): x.get("count")
            for x in (facets.get("facets", {}).get("record_type") or [])
        },
        "table_row_quality_distribution": dict(by_quality),
        "reason_code_counts": dict(reason_counts),
        "retrieval_eligible_distribution": {
            str(x.get("value")): x.get("count")
            for x in (facets.get("facets", {}).get("retrieval_eligible") or [])
        },
        "source_wise_table_quality_distribution": {
            sf: dict(counter) for sf, counter in source_quality.items()
        },
        "index_like_rows": {
            "count": len(index_like_rows),
            "sample": index_like_rows[:20],
        },
        "placeholder_like_rows": {
            "count": len(placeholder_rows),
            "sample": placeholder_rows[:20],
        },
        "retrieval_eligible_table_rows_with_nonempty_search_text_pct": _pct(
            len(retrieval_true_search_text_nonempty), len(retrieval_true_rows)
        ),
        "table_rows_with_semantic_key_value_populated_pct": _pct(
            len(semantic_populated), len(rows)
        ),
        "table_rows_total": len(rows),
        "table_rows_retrieval_eligible_total": len(retrieval_true_rows),
        "required_reason_codes_present": sorted(list(REQUIRED_REASON_CODES & set(reason_counts.keys()))),
        "required_reason_codes_missing": sorted(list(REQUIRED_REASON_CODES - set(reason_counts.keys()))),
    }
    return report
 
 
 
def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Index Quality Report")
    lines.append("")
    lines.append(f"Generated at: {report['generated_at']}")
    lines.append("")
    lines.append("## Total Counts By Record Type")
    for k, v in sorted(report["total_counts_by_record_type"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
 
    lines.append("## Table Row Quality Distribution")
    for k, v in sorted(report["table_row_quality_distribution"].items()):
        lines.append(f"- {k or '(empty)'}: {v}")
    lines.append("")
 
    lines.append("## Reason Code Counts")
    for k, v in sorted(report["reason_code_counts"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
 
    lines.append("## Retrieval Eligible Distribution")
    for k, v in sorted(report["retrieval_eligible_distribution"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
 
    lines.append("## Source-wise Table Quality Distribution")
    for sf, counts in sorted(report["source_wise_table_quality_distribution"].items()):
        lines.append(f"- {sf}")
        for q, v in sorted(counts.items()):
            lines.append(f"  - {q}: {v}")
    lines.append("")
 
    lines.append("## Index-like Rows")
    lines.append(f"- Count: {report['index_like_rows']['count']}")
    lines.append("")
 
    lines.append("## Placeholder-like Rows")
    lines.append(f"- Count: {report['placeholder_like_rows']['count']}")
    lines.append("")
 
    lines.append("## Coverage Checks")
    lines.append(
        "- Retrieval-eligible table rows with non-empty table_row_search_text: "
        f"{report['retrieval_eligible_table_rows_with_nonempty_search_text_pct']}%"
    )
    lines.append(
        "- Table rows with semantic key/value populated: "
        f"{report['table_rows_with_semantic_key_value_populated_pct']}%"
    )
    lines.append("")
 
    lines.append("## Required Reason Codes")
    lines.append(f"- Present: {', '.join(report['required_reason_codes_present']) or '(none)'}")
    lines.append(f"- Missing: {', '.join(report['required_reason_codes_missing']) or '(none)'}")
    lines.append("")
 
    return "\n".join(lines)
 
 
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Generate index quality and golden-set hardening reports")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--output-dir", default="reports")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    out_dir = Path(args.output_dir)
    samples_dir = out_dir / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_dir.mkdir(parents=True, exist_ok=True)
 
    token = _token()
    rows = _fetch_all_table_rows(endpoint, index_name, token)
    facet_data = _facet_counts(endpoint, index_name, token)
    report = build_report(rows, facet_data)
 
    (out_dir / "index_quality_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "index_quality_report.md").write_text(_render_md(report), encoding="utf-8")
 
    noise_sample = _sample_rows(rows, lambda r: (r.get("table_row_quality") == "noise"), 200)
    high_sample = _sample_rows(rows, lambda r: (r.get("table_row_quality") == "high"), 200)
 
    with (samples_dir / "noise_rows_sample.jsonl").open("w", encoding="utf-8") as f:
        for row in noise_sample:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
 
    with (samples_dir / "high_quality_rows_sample.jsonl").open("w", encoding="utf-8") as f:
        for row in high_sample:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
 
    reindex_summary = {
        "generated_at": _now_iso(),
        "index_name": index_name,
        "table_rows_total": report["table_rows_total"],
        "table_row_quality_distribution": report["table_row_quality_distribution"],
        "retrieval_eligible_distribution": report["retrieval_eligible_distribution"],
        "samples": {
            "noise_rows_sample_count": len(noise_sample),
            "high_quality_rows_sample_count": len(high_sample),
        },
    }
    (out_dir / "reindex_run_summary.md").write_text(
        "# Reindex Run Summary\n\n" + json.dumps(reindex_summary, indent=2), encoding="utf-8"
    )
 
    # Golden set report here is a hardening compliance report for index-side gating.
    golden_report = {
        "generated_at": _now_iso(),
        "rules": {
            "exclude_retrieval_eligible_false": True,
            "exclude_noise_quality": True,
            "exclude_degenerate_labels": True,
            "auto_enrich_short_labels_with_source_and_section": True,
            "populate_suggested_for_eval_question": True,
        },
        "counts": {
            "table_rows_total": len(rows),
            "eligible_rows": len([r for r in rows if bool(r.get("retrieval_eligible"))]),
            "noise_rows": len([r for r in rows if (r.get("table_row_quality") == "noise")]),
            "suggested_for_eval_question_true": len([
                r for r in rows if bool(r.get("suggested_for_eval_question"))
            ]),
        },
        "rejections": {
            "retrieval_eligible_false": len([r for r in rows if not bool(r.get("retrieval_eligible"))]),
            "table_row_quality_noise": len([r for r in rows if (r.get("table_row_quality") == "noise")]),
            "degenerate_label_key": len([
                r
                for r in rows
                if not (r.get("table_row_semantic_key") or "").strip()
                or len((r.get("table_row_semantic_key") or "").strip()) <= 1
                or (r.get("table_row_semantic_key") or "").strip().lower().startswith("col")
            ]),
        },
    }
    (out_dir / "golden_set_generation_report.md").write_text(
        "# Golden Set Generation Hardening Report\n\n" + json.dumps(golden_report, indent=2),
        encoding="utf-8",
    )
 
    print(f"Wrote reports to: {out_dir}")
    return 0
 
 
 
if __name__ == "__main__":
    raise SystemExit(main())
 