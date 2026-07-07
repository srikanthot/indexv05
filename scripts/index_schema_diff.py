# placeholder — paste real content from office laptop
"""Generate index schema diff report for required table-row quality fields."""
 
from __future__ import annotations
 
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
 
REQUIRED_FIELDS = [
    "table_row_quality",
    "table_row_quality_reason_codes",
    "table_row_is_header_like",
    "table_row_is_index_like",
    "table_row_is_placeholder_like",
    "table_row_token_count",
    "table_row_char_count",
    "table_row_semantic_key",
    "table_row_semantic_value",
    "table_context_path",
    "table_row_search_text",
    "retrieval_eligible",
    "suggested_for_eval_question",
]
 
 
 
def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
 
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-json", default="search/index.json")
    ap.add_argument("--output", default="reports/index_schema_diff.md")
    args = ap.parse_args()
 
    index_path = Path(args.index_json)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
 
    index_body = json.loads(index_path.read_text(encoding="utf-8"))
    fields = {f.get("name"): f for f in index_body.get("fields", [])}
 
    present = [f for f in REQUIRED_FIELDS if f in fields]
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
 
    lines = []
    lines.append("# Index Schema Diff")
    lines.append("")
    lines.append(f"Generated at: {_now_iso()}")
    lines.append("")
    lines.append("## Required Fields Present")
    for name in present:
        lines.append(f"- {name}: {fields[name].get('type')}")
    lines.append("")
    lines.append("## Required Fields Missing")
    if missing:
        for name in missing:
            lines.append(f"- {name}")
    else:
        lines.append("- (none)")
 
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0
 
 
 
if __name__ == "__main__":
    raise SystemExit(main())
 
 