"""
Verify the NEW safety/quality fields are actually populated in the live index —
per record type, with fill-rate % and real example values so you can eyeball
accuracy before scaling to the full corpus.

Usage:
  python scripts/verify_new_fields.py --config deploy.config.json
  python scripts/verify_new_fields.py --config deploy.config.json --source-file CO-CC-GEN.pdf
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

# New fields to verify, grouped by the record_type they should appear on.
FIELDS_BY_TYPE: dict[str, list[str]] = {
    "text": [
        "applies_to_voltage", "applies_to_equipment", "applies_to_domain",
        "applies_to_phase", "hazard_class", "criticality", "is_prohibition",
        "prohibitions", "governing_callouts", "safety_callout",
        "procedure_id", "procedure_title", "procedure_step_order",
        "procedure_step_text", "procedure_step_count", "low_confidence_ocr",
    ],
    "table": [
        "table_number", "table_title", "applies_to_voltage",
        "applies_to_equipment", "applies_to_domain", "applies_to_phase",
        "hazard_class", "criticality", "governing_callouts",
    ],
    "table_row": [
        "table_row_key", "applies_to_domain", "hazard_class", "criticality",
        "safety_callout",
    ],
    "diagram": [
        "figure_number", "figure_title", "figure_callouts", "figure_step_linked",
        "figure_linkage_confidence", "applies_to_domain", "hazard_class",
    ],
    "summary": [
        "applies_to_domain", "applies_to_equipment", "hazard_class", "criticality",
    ],
}

ALL_FIELDS = sorted({f for fs in FIELDS_BY_TYPE.values() for f in fs} |
                    {"chunk_id", "record_type", "source_file"})


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, bool):
        return v is False   # for booleans, treat False as "not flagged" for fill-rate
    return False


def _sample_val(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v[:3]) + "]"
    s = str(v)
    return s[:70] + ("…" if len(s) > 70 else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify new fields are populated")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--source-file", default=None, help="limit to one PDF")
    ap.add_argument("--per-type", type=int, default=400, help="records to scan per type")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}

    print(f"Index: {index_name}" + (f"  (source_file={args.source_file})" if args.source_file else ""))

    for rt, fields in FIELDS_BY_TYPE.items():
        flt = f"record_type eq '{rt}'"
        if args.source_file:
            sf = args.source_file.replace("'", "''")
            flt += f" and source_file eq '{sf}'"
        body = {
            "search": "*", "filter": flt,
            "select": ",".join(sorted(set(fields) | {"chunk_id"})),
            "top": args.per_type,
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=60.0)
        resp.raise_for_status()
        recs = resp.json().get("value", [])
        print(f"\n=== record_type = {rt}   (scanned {len(recs)}) ===")
        if not recs:
            print("  (no records)")
            continue
        counts = defaultdict(int)
        examples: dict[str, str] = {}
        for r in recs:
            for f in fields:
                v = r.get(f)
                if not _empty(v):
                    counts[f] += 1
                    if f not in examples:
                        examples[f] = _sample_val(v)
        n = len(recs)
        for f in fields:
            pct = 100.0 * counts[f] / n if n else 0.0
            ex = examples.get(f, "")
            bar = "OK " if pct > 0 else "-- "
            print(f"  {bar} {f:<26} {pct:5.0f}%   e.g. {ex}")

    print("\nNote: booleans (safety_callout, is_prohibition, figure_step_linked, "
          "low_confidence_ocr) count only True as 'populated' — a low % is normal "
          "(most chunks aren't hazardous / aren't figures-in-procedures).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
