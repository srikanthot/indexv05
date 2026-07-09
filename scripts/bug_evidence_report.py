"""
bug_evidence_report.py — THE PROOF, not the promise.

The chatbot team asked us to "add" ~28 fields. Every one of them ALREADY EXISTS
in the index schema. So the real question is never "does the field exist" — it's
"is the field POPULATED with real data the chatbot can use, for the chunks that
matter?" A schema is not evidence. THIS script is.

It connects to the LIVE index and produces, in one run, exactly what the chatbot
asked us to share:

  PART 1  FIELD COMPLETENESS   — for every requested field, per record_type:
          fill-rate %  +  a REAL example value. (Proves populated vs empty.)

  PART 2  MANUAL CATALOG       — lists every manual with its revision / effective
          date / is_current_revision. (Proves "list all manuals" already works
          off summary records — no new catalog source needed.)

  PART 3  PER-BUG EVIDENCE     — runs the actual retrieval for each of the 6
          remaining bugs, shows the top chunks + the key fields those bugs need,
          and prints a VERDICT:
             FOUND & POPULATED   -> index has it; the chatbot query must USE the
                                     fields (join by procedure_id, read table_row_*).
             FOUND, FIELDS EMPTY -> a real coverage/enrichment gap on our side.
             NO CHUNK RETRIEVED  -> likely a SOURCE-CONTENT gap (the manual truly
                                     doesn't contain it) or retrieval tuning.

  PART 4  RUN HEADER           — index name, api-version, index_run_id, doc count,
          so the report is reproducible and stamped.

Usage (office laptop, where Azure creds live):
  python scripts/bug_evidence_report.py --config deploy.config.json
  python scripts/bug_evidence_report.py --config deploy.config.json --source-file 61020.pdf
  python scripts/bug_evidence_report.py --config deploy.config.json > bug_evidence.txt

Paste the whole output back — that IS our answer to the chatbot's work request.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

SEARCH_SCOPE = "https://search.azure.us/.default"
SCAN_API = "2024-05-01-preview"   # for filter/scan (matches verify_new_fields.py)
QUERY_API = "2024-07-01"          # for semantic + integrated query vectorization


# ── which fields each record_type is EXPECTED to carry ───────────────────────
# (only the fields the chatbot listed in the work request, so the report maps
#  1:1 to what they asked for)
FIELDS_BY_TYPE: dict[str, list[str]] = {
    "text": [
        # procedure model (only on procedure chunks — see PROCEDURE_FIELDS note)
        "procedure_id", "procedure_step_id", "procedure_step_order",
        "procedure_step_count", "procedure_title",
        # applicability + safety
        "applies_to_domain", "applies_to_equipment", "applies_to_system",
        "applies_to_phase", "hazard_class", "criticality", "prohibitions",
        "governing_callouts",
        # eligibility hygiene
        "processing_status", "retrieval_eligible", "retrieval_eligible_reason",
        "is_current_revision",
    ],
    "table": [
        "table_number", "table_title", "table_caption", "table_columns",
        "table_cluster_id", "applies_to_domain", "applies_to_equipment",
    ],
    "table_row": [
        "table_cluster_id", "table_row_index", "table_row_key", "table_columns",
        "table_row_semantic_key", "table_row_semantic_value", "table_row_cells",
        "equipment_ids", "applies_to_domain",
    ],
    "diagram": [
        "figure_number", "figure_title", "figure_callouts", "applies_to_domain",
        "hazard_class",
    ],
    "summary": [
        "document_number", "document_revision", "effective_date",
        "is_current_revision", "applies_to_domain",
    ],
}

# Procedure fields only make sense on procedure chunks. We report them a SECOND
# way: fill-rate among chunks that actually have a procedure_id (conditional),
# because their fill-rate across ALL text is naturally low and misleading.
PROCEDURE_FIELDS = {
    "procedure_step_id", "procedure_step_order", "procedure_step_count",
    "procedure_title",
}

# Booleans where BOTH true and false are meaningful (null = truly unset).
HYGIENE_BOOLS = {"retrieval_eligible", "is_current_revision"}


# ── the 6 remaining bugs, as real retrieval probes ───────────────────────────
BUGS = [
    {
        "id": "60014",
        "what": "bypass requirements at regulating stations",
        "query": "bypass requirements regulating station pressure",
        "record_type": None,
        "key_fields": ["chunk", "procedure_id", "applies_to_equipment",
                       "hazard_class", "prohibitions", "governing_callouts",
                       "source_file", "physical_pdf_page"],
        "need": "a requirement/prohibition chunk scoped to regulating stations",
        "grounding": ["prohibitions", "governing_callouts", "procedure_id"],
    },
    {
        "id": "61020a",
        "what": "mark out 4-inch plastic into 6-inch cast iron main",
        "query": "mark out 4 inch plastic 6 inch cast iron main procedure",
        "record_type": "text",
        "key_fields": ["procedure_id", "procedure_title", "procedure_step_order",
                       "procedure_step_count", "procedure_step_text",
                       "source_file", "physical_pdf_page"],
        "need": "ordered mark-out procedure steps (not just nearby prose)",
        "grounding": ["procedure_id", "procedure_step_text"],
    },
    {
        "id": "61020b",
        "what": "remove water from regulator pit",
        "query": "remove water from regulator pit procedure pump",
        "record_type": "text",
        "key_fields": ["procedure_id", "procedure_title", "procedure_step_order",
                       "procedure_step_text", "source_file", "physical_pdf_page"],
        "need": "actionable regulator-pit water-removal steps (IF the manual has them)",
        "grounding": ["procedure_id", "procedure_step_text"],
    },
    {
        "id": "61021",
        "what": "steel vs cast iron configuration comparison",
        "query": "steel versus cast iron main configuration comparison",
        "record_type": None,
        "key_fields": ["chunk", "applies_to_equipment", "table_cluster_id",
                       "table_columns", "source_file", "physical_pdf_page"],
        "need": "comparison-ready content covering BOTH steel and cast iron",
        "grounding": ["chunk"],
    },
    {
        "id": "66009",
        "what": "hazardous classification around fired gas equipment",
        "query": "hazardous area classification fired gas equipment location class division",
        "record_type": None,
        "key_fields": ["chunk", "hazard_class", "criticality",
                       "applies_to_equipment", "governing_callouts",
                       "source_file", "physical_pdf_page"],
        "need": "fired-equipment hazardous-area records with classification rules",
        "grounding": ["hazard_class", "governing_callouts", "chunk"],
    },
    {
        "id": "67009",
        "what": "max amperage of 397 aluminum primary open wire",
        "query": "ampacity 397 aluminum primary open wire maximum amperage",
        "record_type": "table_row",
        "key_fields": ["table_row_key", "table_columns", "table_row_cells",
                       "table_row_semantic_key", "table_row_semantic_value",
                       "table_cluster_id", "low_confidence_ocr",
                       "source_file", "physical_pdf_page"],
        "need": "a clean ampacity table ROW keyed to 397 AAC with a numeric value",
        "grounding": ["table_row_key", "table_row_cells"],
    },
]


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    return False


def _sample(v: Any, width: int = 90) -> str:
    if isinstance(v, list):
        s = "[" + ", ".join(str(x) for x in v[:4]) + ("…" if len(v) > 4 else "") + "]"
    else:
        s = str(v)
    s = s.replace("\n", " ⏎ ")
    return s[:width] + ("…" if len(s) > width else "")


def _post(url: str, body: dict, headers: dict) -> dict:
    r = httpx.post(url, json=body, headers=headers, timeout=90.0)
    r.raise_for_status()
    return r.json()


def _search(base: str, headers: dict, query: str, record_type: str | None,
            select: list[str], top: int) -> list[dict]:
    """Hybrid semantic + integrated-vector query, degrading gracefully."""
    url = f"{base}/docs/search?api-version={QUERY_API}"
    flt = f"record_type eq '{record_type}'" if record_type else None
    base_body = {
        "search": query,
        "top": top,
        "select": ",".join(sorted(set(select) | {"chunk_id", "record_type"})),
    }
    if flt:
        base_body["filter"] = flt
    # 1) full: semantic + vector
    attempts = [
        {**base_body, "queryType": "semantic",
         "semanticConfiguration": "mm-semantic-config",
         "vectorQueries": [{"kind": "text", "text": query,
                            "fields": "text_vector", "k": max(top, 30)}]},
        {**base_body, "vectorQueries": [{"kind": "text", "text": query,
                                         "fields": "text_vector", "k": max(top, 30)}]},
        base_body,  # 3) plain keyword
    ]
    last = None
    for body in attempts:
        try:
            return _post(url, body, headers).get("value", [])
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    print(f"    ! search failed: {last}")
    return []


def part1_field_completeness(base: str, headers: dict, source_file: str | None,
                             per_type: int) -> None:
    print("\n" + "=" * 78)
    print("PART 1 — FIELD COMPLETENESS  (proves populated vs empty, per record_type)")
    print("=" * 78)
    url = f"{base}/docs/search?api-version={SCAN_API}"
    for rt, fields in FIELDS_BY_TYPE.items():
        flt = f"record_type eq '{rt}'"
        if source_file:
            flt += f" and source_file eq '{source_file.replace(chr(39), chr(39)*2)}'"
        body = {"search": "*", "filter": flt, "top": per_type,
                "select": ",".join(sorted(set(fields) | {"chunk_id", "procedure_id"}
                                          if rt == "text" else set(fields) | {"chunk_id"}))}
        try:
            recs = _post(url, body, headers).get("value", [])
        except Exception as e:  # noqa: BLE001
            print(f"\n  record_type={rt}: scan failed ({e})")
            continue
        n = len(recs)
        print(f"\n  record_type = {rt}   (scanned {n})")
        if not n:
            print("    (no records of this type)")
            continue
        n_proc = sum(1 for r in recs if not _empty(r.get("procedure_id")))
        counts: dict[str, int] = defaultdict(int)
        true_counts: dict[str, int] = defaultdict(int)
        examples: dict[str, str] = {}
        for r in recs:
            for f in fields:
                v = r.get(f)
                if f in HYGIENE_BOOLS:
                    if v is not None:                 # set at all
                        counts[f] += 1
                        if v is True:
                            true_counts[f] += 1
                        examples.setdefault(f, str(v))
                elif not _empty(v):
                    counts[f] += 1
                    examples.setdefault(f, _sample(v))
        for f in fields:
            denom = n_proc if (f in PROCEDURE_FIELDS and rt == "text") else n
            denom = denom or 1
            pct = 100.0 * counts[f] / denom
            tag = "  "
            if f in PROCEDURE_FIELDS and rt == "text":
                tag = "§"  # measured among procedure chunks
            extra = ""
            if f in HYGIENE_BOOLS:
                extra = f"  (true={100.0*true_counts[f]/n:4.0f}% of all)"
            mark = "OK" if pct > 0 else "--"
            print(f"    {mark}{tag}{f:<27}{pct:5.0f}%   e.g. {examples.get(f,'')}{extra}")
        if n_proc and rt == "text":
            print(f"    §  = measured among the {n_proc} chunks that have a procedure_id "
                  f"(not all text is a procedure)")


def part2_manual_catalog(base: str, headers: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 2 — MANUAL CATALOG  ('list all manuals' already works off summaries)")
    print("=" * 78)
    url = f"{base}/docs/search?api-version={SCAN_API}"
    body = {
        "search": "*", "filter": "record_type eq 'summary'", "top": 200,
        "select": "source_file,document_number,document_revision,effective_date,"
                  "is_current_revision,document_family_id",
        "orderby": "source_file asc",
    }
    try:
        recs = _post(url, body, headers).get("value", [])
    except Exception as e:  # noqa: BLE001
        print(f"  catalog query failed: {e}")
        return
    print(f"  {len(recs)} manual(s) (one summary record each):\n")
    print(f"    {'source_file':<34}{'doc#':<14}{'rev':<8}{'effective':<12}cur")
    for r in recs:
        print(f"    {str(r.get('source_file','')):<34}"
              f"{str(r.get('document_number') or '-'):<14}"
              f"{str(r.get('document_revision') or '-'):<8}"
              f"{str(r.get('effective_date') or '-'):<12}"
              f"{r.get('is_current_revision')}")


def part3_bug_evidence(base: str, headers: dict, top: int) -> None:
    print("\n" + "=" * 78)
    print("PART 3 — PER-BUG EVIDENCE  (real retrieval + verdict for each remaining bug)")
    print("=" * 78)
    for bug in BUGS:
        print(f"\n  ── BUG {bug['id']} — {bug['what']}")
        print(f"     query : \"{bug['query']}\"" +
              (f"   [record_type={bug['record_type']}]" if bug["record_type"] else ""))
        print(f"     need  : {bug['need']}")
        hits = _search(base, headers, bug["query"], bug["record_type"],
                       bug["key_fields"], top)
        if not hits:
            print("     VERDICT: NO CHUNK RETRIEVED  ->  likely SOURCE-CONTENT GAP "
                  "(manual may not contain it) or retrieval tuning. Confirm the "
                  "manual actually has this before treating as an index bug.")
            continue
        top_hit = hits[0]
        grounded = all(not _empty(top_hit.get(f)) for f in bug["grounding"])
        any_key = any(not _empty(top_hit.get(f)) for f in bug["key_fields"])
        if grounded:
            verdict = ("FOUND & POPULATED  ->  index HAS it. The chatbot must USE "
                       "these fields (expand by procedure_id / read table_row_* / "
                       "show governing_callouts). Not an index gap.")
        elif any_key:
            verdict = ("FOUND but GROUNDING FIELDS EMPTY  ->  real COVERAGE gap: the "
                       "chunk is there but " + ", ".join(bug["grounding"]) +
                       " not populated. Enrichment fix on our side.")
        else:
            verdict = ("WEAK  ->  a chunk came back but none of the key fields are "
                       "populated. Inspect: coverage gap or wrong chunk retrieved.")
        print(f"     hits  : {len(hits)}   top chunk_id={top_hit.get('chunk_id')}  "
              f"({top_hit.get('record_type')})  {top_hit.get('source_file','')}")
        for f in bug["key_fields"]:
            v = top_hit.get(f)
            flag = " " if not _empty(v) else "∅"
            print(f"        {flag} {f:<26} {_sample(v)}")
        print(f"     VERDICT: {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-index evidence report for the chatbot bugs")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--source-file", default=None, help="limit PART 1 to one PDF")
    ap.add_argument("--per-type", type=int, default=500, help="records scanned per type in PART 1")
    ap.add_argument("--top", type=int, default=3, help="top hits per bug in PART 3")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    base = f"{endpoint}/indexes/{index_name}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}

    print("=" * 78)
    print("PART 4 — RUN HEADER")
    print("=" * 78)
    print(f"  index      : {index_name}")
    print(f"  endpoint   : {endpoint}")
    print(f"  scan api   : {SCAN_API}   query api: {QUERY_API}")
    # doc count + a run id sample
    try:
        cnt = httpx.post(f"{base}/docs/search?api-version={SCAN_API}",
                         json={"search": "*", "count": True, "top": 0,
                               "select": "chunk_id"}, headers=headers,
                         timeout=60.0).json()
        print(f"  total docs : {cnt.get('@odata.count')}")
    except Exception as e:  # noqa: BLE001
        print(f"  total docs : (count failed: {e})")
    try:
        ri = httpx.post(f"{base}/docs/search?api-version={SCAN_API}",
                        json={"search": "*", "top": 1, "select": "index_run_id,last_indexed_at"},
                        headers=headers, timeout=60.0).json().get("value", [{}])[0]
        print(f"  index_run  : {ri.get('index_run_id')}   last_indexed_at: {ri.get('last_indexed_at')}")
    except Exception:  # noqa: BLE001
        pass

    part1_field_completeness(base, headers, args.source_file, args.per_type)
    part2_manual_catalog(base, headers)
    part3_bug_evidence(base, headers, args.top)

    print("\n" + "=" * 78)
    print("HOW TO READ THIS")
    print("=" * 78)
    print("""  • PART 1: a field at 0% on a type it should populate = a real coverage gap.
    A field with a % and an example = POPULATED and usable RIGHT NOW.
  • '§' fields (procedure_*) are measured among procedure chunks only — a low
    overall count is expected (most text isn't a numbered procedure).
  • PART 3 verdicts:
      FOUND & POPULATED   -> NOT an index bug. The chatbot query must join by
                             procedure_id / read table_row_cells / show callouts.
      FOUND, FIELDS EMPTY -> our enrichment gap; we fix it in the pipeline.
      NO CHUNK RETRIEVED  -> confirm the manual even contains it (source gap) or
                             tune retrieval; adding fields won't create content.
  • This whole output is the reply to the chatbot's work request: run id + field
    completeness + per-bug evidence + honest source gaps.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
