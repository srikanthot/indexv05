"""
Production go/no-go audit for the whole search index — ONE command.

This is the single command to run before promoting an index to production. It
scans EVERY record in the live index (not a sample), checks every main field for
empty / placeholder / garbage / constant-stub values per record_type, verifies
that the retrieval-critical text_vector embeddings actually landed (the one thing
no retrievable-field scan can see), and EXITS NON-ZERO if anything critical is
wrong. A green run means: full coverage, required fields populated with real
values, safety fields sane, and vectors present.

Why a new script (vs the older audit_all_retrievable_fields.py):
  - the old audit ALWAYS exit(0) — it could print "Critical findings: 5000" and
    still pass the build.
  - it computed full_coverage but never enforced it, and never scanned records
    whose source_file is null/empty (exactly the broken ones).
  - many anomalies it counted were never promoted to findings.
  - NOTHING in the suite ever checked text_vector — a 100%-null-vector index
    passed as "full coverage, clean".
This script fixes all of the above and fails loudly.

Usage:
  # Full whole-index audit (the real thing — run this before go-live):
  python scripts/audit_index_production.py --config deploy.config.json

  # Fail on high findings too, not just critical (stricter gate):
  python scripts/audit_index_production.py --config deploy.config.json --strict

  # Quick scoped dry-run against one PDF while iterating:
  python scripts/audit_index_production.py --config deploy.config.json --source-file CO-CC-GEN.pdf

  # Skip the vector probe (e.g. offline / no query vectorizer configured):
  python scripts/audit_index_production.py --config deploy.config.json --no-vector-check

Exit codes:
  0 = clean (no critical; no high when --strict)
  1 = critical findings, incomplete coverage, or missing vectors  -> DO NOT PROMOTE
  2 = high findings and --strict

Outputs:
  reports/production_audit.json   (full machine-readable detail)
  reports/production_audit.md     (human report you can hand to the team)
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

# 2024-07-01 is the first STABLE api-version with integrated-vectorization query
# support (vectorQueries kind:"text"), which we need to probe text_vector.
API_VERSION = "2024-07-01"
SEARCH_SCOPE = "https://search.azure.us/.default"

# Azure Search hard limit on $skip. If any single partition exceeds this we must
# stop and warn rather than silently truncate.
SKIP_LIMIT = 100_000

# ---------------------------------------------------------------------------
# Field contracts. These encode "what MUST be populated for this record type to
# be a usable, citable, retrievable chunk". Missing any of these on a record is
# a critical defect for a safety RAG index.
# ---------------------------------------------------------------------------
REQUIRED_BY_TYPE: dict[str, list[str]] = {
    "text": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "page_resolution_method",
        "processing_status", "skill_version", "record_type",
    ],
    "diagram": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "diagram_category", "record_type",
    ],
    "table": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_row_count", "table_col_count", "record_type",
    ],
    "table_row": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "physical_pdf_page", "processing_status", "skill_version",
        "table_parent_chunk_id", "table_row_index", "record_type",
        "table_row_search_text",
    ],
    "summary": [
        "chunk_id", "parent_id", "chunk", "source_file", "source_path",
        "processing_status", "skill_version", "record_type",
    ],
}

# Fields that every retrieval-eligible record MUST carry (grounding / citation).
RETRIEVAL_REQUIRED = [
    "chunk_id", "source_file", "record_type", "chunk",
    "content_class", "retrieval_eligible_reason",
]

# Safety-relevant fields we sanity-check for value plausibility, not just presence.
SAFETY_FIELDS = [
    "safety_callout", "callouts", "governing_callouts", "hazard_class",
    "criticality", "is_prohibition", "prohibitions",
    "low_confidence_ocr", "ocr_min_confidence",
]

VALID_CONTENT_CLASSES = {
    "operational_content", "table_content", "figure_content",
    "procedure_step", "locator_artifact", "summary_content", "other",
}
VALID_CRITICALITY = {"", "low", "medium", "high", "critical", "info", "normal"}
VALID_LOCATOR_TYPES = {"none", "page", "section", "figure", "table", "step", "header"}

# A value that looks populated but is actually a placeholder / garbage token.
PLACEHOLDER_RE = re.compile(
    r"^\s*(?:n/?a|na|none|null|nil|unknown|tbd|tba|undefined|--+|\.{3,}|_+|\-+)\s*$",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Cap the number of distinct values we retain per field (for the constant/stub
# detector) so memory stays bounded on a huge corpus.
DISTINCT_CAP = 60


def _token() -> str:
    return DefaultAzureCredential().get_token(SEARCH_SCOPE).token


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list | dict)):
        return len(v) == 0
    return False


def _is_placeholder(v: Any) -> bool:
    return isinstance(v, str) and bool(PLACEHOLDER_RE.match(v))


def _sample(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v[:3]) + "]"
    s = str(v)
    return s[:80] + ("…" if len(s) > 80 else "")


class Finding:
    __slots__ = ("severity", "category", "message", "count", "examples")

    def __init__(self, severity, category, message, count=0, examples=None):
        self.severity = severity
        self.category = category
        self.message = message
        self.count = count
        self.examples = examples or []

    def as_dict(self):
        return {
            "severity": self.severity, "category": self.category,
            "message": self.message, "count": self.count,
            "examples": self.examples[:5],
        }


def _post(url, headers, body, timeout=180.0):
    r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
def enumerate_source_files(search_url, headers) -> tuple[int, list[str], dict]:
    """Return (service_total, distinct source_file values, per-type counts).

    Uses a large facet count and then VERIFIES via @odata.count that we saw them
    all; the caller hard-fails on any coverage gap.
    """
    meta = _post(search_url, headers, {
        "search": "*", "top": 0, "count": True,
        "facets": ["source_file,count:1000", "record_type,count:50"],
    })
    total = int(meta.get("@odata.count") or 0)
    facets = meta.get("@search.facets", {})
    sfs = [x.get("value") for x in (facets.get("source_file") or []) if x.get("value")]
    type_counts = {x.get("value"): x.get("count") for x in (facets.get("record_type") or [])}
    return total, sfs, type_counts


def scan_partition(search_url, headers, flt, select_str, page=1000):
    """Yield every record matching `flt`, paging with $skip. Raises if a single
    partition exceeds the Azure $skip limit (so we never silently truncate)."""
    skip = 0
    while True:
        if skip >= SKIP_LIMIT:
            raise RuntimeError(
                f"partition exceeded $skip limit ({SKIP_LIMIT}) for filter {flt!r}; "
                "split the partition further to guarantee full coverage"
            )
        body = {"search": "*", "filter": flt, "select": select_str,
                "top": page, "skip": skip, "orderby": "chunk_id asc"}
        vals = _post(search_url, headers, body).get("value", [])
        if not vals:
            return
        yield from vals
        if len(vals) < page:
            return
        skip += page


# ---------------------------------------------------------------------------
def vector_coverage(search_url, headers, source_files, type_counts) -> list[Finding]:
    """Verify text_vector actually landed. text_vector is retrievable:false, so
    it can't be $select'd — instead we run an integrated-vectorization query
    (kind:"text") which (a) fails if the query vectorizer/endpoint is broken and
    (b) returns zero hits for any partition whose vectors are null."""
    findings: list[Finding] = []
    probe = "safety clearance grounding voltage procedure"

    def vquery(extra_filter=None, top=1):
        body = {
            "count": True, "top": top, "select": "chunk_id,source_file",
            "vectorQueries": [{
                "kind": "text", "text": probe,
                "fields": "text_vector", "k": top, "exhaustive": True,
            }],
        }
        if extra_filter:
            body["filter"] = extra_filter
        return _post(search_url, headers, body)

    # 1) Global probe: does query-time vectorization work at all + any vectors present?
    try:
        g = vquery(top=3)
    except httpx.HTTPStatusError as e:
        findings.append(Finding(
            "critical", "vector_query_failed",
            "Integrated-vectorization query FAILED — query-time embedding is "
            f"broken (bad AOAI endpoint suffix?). HTTP {e.response.status_code}: "
            f"{e.response.text[:300]}",
        ))
        return findings

    if not g.get("value"):
        findings.append(Finding(
            "critical", "vectors_all_null",
            "A whole-index vector query returned ZERO results — text_vector is "
            "null across the entire index. Vector/hybrid/semantic retrieval is "
            "dead; the bot is running BM25-only. Almost always a wrong embedding "
            "endpoint suffix (must be *.openai.azure.us, not *.services.ai.azure.us).",
        ))
        return findings  # nothing else to check; global vectors are gone

    # 2) Per-source_file probe: any document with text/table content but zero
    #    vector hits has lost its vectors.
    missing = []
    for sf in source_files:
        safe = str(sf).replace("'", "''")
        try:
            r = vquery(extra_filter=f"source_file eq '{safe}'", top=1)
        except httpx.HTTPStatusError:
            missing.append(sf)
            continue
        if not r.get("value"):
            missing.append(sf)

    if missing:
        findings.append(Finding(
            "critical", "vectors_missing_for_documents",
            f"{len(missing)} source_file(s) have records but ZERO vector hits — "
            "their embeddings did not land. Those PDFs are invisible to vector "
            "search.", count=len(missing), examples=missing,
        ))
    return findings


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Whole-index production audit")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--source-file", default=None,
                    help="limit scan to one PDF (for quick dry-runs only)")
    ap.add_argument("--strict", action="store_true",
                    help="also exit non-zero (2) on HIGH findings")
    ap.add_argument("--no-vector-check", action="store_true",
                    help="skip the text_vector coverage probe")
    ap.add_argument("--out-json", default="reports/production_audit.json")
    ap.add_argument("--out-md", default="reports/production_audit.md")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}

    print(f"Index: {index_name}")
    schema_fields = httpx.get(index_url, headers=headers, timeout=60).json().get("fields", [])
    retrievable = [f["name"] for f in schema_fields
                   if f.get("retrievable") is True and f.get("name")]
    retr_set = set(retrievable)
    select_str = ",".join(retrievable)

    # --- enumerate partitions + establish the ground-truth total to reconcile against
    service_total, source_files, type_counts = enumerate_source_files(search_url, headers)
    if args.source_file:
        source_files = [args.source_file]
        print(f"  (scoped to source_file={args.source_file} — NOT a full audit)")
    print(f"  service reports {service_total} total records across "
          f"{len(source_files)} source_file(s)")

    # --- accumulators
    counts_by_type: Counter = Counter()
    status_counts: Counter = Counter()
    field_missing: dict[str, Counter] = defaultdict(Counter)
    field_placeholder: dict[str, Counter] = defaultdict(Counter)
    field_present: dict[str, Counter] = defaultdict(Counter)
    field_distinct: dict[str, set] = defaultdict(set)          # (rt,field) -> values
    field_example: dict[str, str] = {}
    anomalies: Counter = Counter()
    anomaly_examples: dict[str, list] = defaultdict(list)
    safety_flag_true: Counter = Counter()                      # for over-flag ratios
    scanned = 0

    def note(key, chunk_id=None):
        anomalies[key] += 1
        if chunk_id and len(anomaly_examples[key]) < 5:
            anomaly_examples[key].append(chunk_id)

    # --- the full scan. Partition by source_file (+ a null/empty bucket) so no
    #     record is ever excluded, then reconcile the total at the end.
    partitions = [f"source_file eq '{str(sf).replace(chr(39), chr(39)*2)}'" for sf in source_files]
    if not args.source_file:
        partitions.append("source_file eq null or source_file eq ''")

    for flt in partitions:
        try:
            for rec in scan_partition(search_url, headers, flt, select_str):
                scanned += 1
                rt = rec.get("record_type") or "NULL"
                cid = rec.get("chunk_id")
                counts_by_type[rt] += 1
                status_counts[rec.get("processing_status") or "NULL"] += 1

                # per-field: missing / placeholder / present + distinct tracking
                for f in retrievable:
                    v = rec.get(f)
                    if _is_missing(v):
                        field_missing[rt][f] += 1
                    else:
                        field_present[rt][f] += 1
                        if _is_placeholder(v):
                            field_placeholder[rt][f] += 1
                        ds = field_distinct[f"{rt}::{f}"]
                        if len(ds) < DISTINCT_CAP:
                            ds.add(_sample(v))
                        field_example.setdefault(f"{rt}::{f}", _sample(v))

                # required fields (empty OR placeholder both count as a defect)
                for f in REQUIRED_BY_TYPE.get(rt, []):
                    if f in retr_set and (_is_missing(rec.get(f)) or _is_placeholder(rec.get(f))):
                        note(f"required_missing::{rt}::{f}", cid)

                # retrieval contract
                eligible = bool(rec.get("retrieval_eligible")) if "retrieval_eligible" in retr_set else False
                if eligible:
                    for f in RETRIEVAL_REQUIRED:
                        if f in retr_set and (_is_missing(rec.get(f)) or _is_placeholder(rec.get(f))):
                            note(f"retrieval_missing::{f}", cid)
                    if rt in {"text", "diagram", "table", "table_row"} and \
                            "physical_pdf_page" in retr_set and _is_missing(rec.get("physical_pdf_page")):
                        note("retrieval_missing::physical_pdf_page", cid)
                    if rt == "text" and "header_1" in retr_set and _is_missing(rec.get("header_1")):
                        note("retrieval_missing::header_1", cid)

                # content quality of the chunk body
                chunk = rec.get("chunk")
                if isinstance(chunk, str):
                    s = chunk.strip()
                    if not s:
                        note("empty_chunk", cid)
                    else:
                        if len(s) < 20:
                            note("very_short_chunk_lt20", cid)
                        if CONTROL_RE.search(s):
                            note("control_chars_in_chunk", cid)
                        if _is_placeholder(s):
                            note("placeholder_chunk", cid)

                # enum validity
                cc = str(rec.get("content_class") or "").strip().lower()
                if cc and cc not in VALID_CONTENT_CLASSES:
                    note("invalid_content_class", cid)
                crit = str(rec.get("criticality") or "").strip().lower()
                if crit and crit not in VALID_CRITICALITY:
                    note("invalid_criticality", cid)
                lt = str(rec.get("locator_type") or "").strip().lower()
                if lt and lt not in VALID_LOCATOR_TYPES:
                    note("invalid_locator_type", cid)

                # locator-artifact leak: artifacts must not be retrieval-eligible
                if eligible and (cc == "locator_artifact" or bool(rec.get("is_locator_artifact"))):
                    note("locator_artifact_retrieval_eligible", cid)

                # table integrity
                if rt in {"table", "table_row"}:
                    ts = rec.get("table_integrity_score")
                    if ts is not None:
                        try:
                            if not (0.0 <= float(ts) <= 1.0):
                                note("table_integrity_out_of_range", cid)
                        except (TypeError, ValueError):
                            note("table_integrity_not_numeric", cid)
                if rt == "table_row" and eligible:
                    trq = str(rec.get("table_row_quality") or "").strip().lower()
                    if trq == "noise":
                        note("noise_row_retrieval_eligible", cid)
                    if _is_missing(rec.get("table_row_search_text")):
                        note("retrieval_row_missing_search_text", cid)

                # safety-field sanity (value plausibility)
                if bool(rec.get("safety_callout")):
                    safety_flag_true["safety_callout"] += 1
                if bool(rec.get("is_prohibition")):
                    safety_flag_true["is_prohibition"] += 1
                if bool(rec.get("low_confidence_ocr")):
                    safety_flag_true["low_confidence_ocr"] += 1
                # a prohibition record with no prohibition text is suspect
                if bool(rec.get("is_prohibition")) and _is_missing(rec.get("prohibitions")):
                    note("is_prohibition_without_text", cid)

                # applies_to_system that merely echoes the header is not a real tag
                sys_tags = rec.get("applies_to_system") or []
                hdrs = [x for x in [rec.get("header_1"), rec.get("header_2")] if x]
                if sys_tags and list(sys_tags) == [h.strip() for h in hdrs if isinstance(h, str)]:
                    note("applies_to_system_is_header_echo", cid)

                # page-coordinate integrity
                p, pe = rec.get("physical_pdf_page"), rec.get("physical_pdf_page_end")
                plist = rec.get("physical_pdf_pages")
                plist = plist if isinstance(plist, list) else []
                if plist:
                    pmin, pmax = min(plist), max(plist)
                    if isinstance(p, int) and p != pmin:
                        note("physical_page_not_min_of_list", cid)
                    if isinstance(pe, int) and pe != pmax:
                        note("physical_page_end_not_max_of_list", cid)
                    if sorted(set(plist)) != list(range(pmin, pmax + 1)):
                        note("physical_pdf_pages_non_contiguous", cid)
        except RuntimeError as e:
            anomalies["partition_scan_incomplete"] += 1
            anomaly_examples["partition_scan_incomplete"].append(str(e))

    # -------------------------------------------------------------------------
    # Build findings
    findings: list[Finding] = []

    # (0) COVERAGE — the load-bearing gate. If we didn't see every record, we
    #     cannot certify anything. This is why the old audit was unsafe.
    coverage_ok = (scanned == service_total) or bool(args.source_file)
    if not coverage_ok:
        findings.append(Finding(
            "critical", "incomplete_coverage",
            f"Scanned {scanned} records but the service reports {service_total}. "
            f"{service_total - scanned} record(s) were NOT audited (source_file "
            "beyond facet cap, or an over-limit partition). The audit cannot "
            "certify this index until coverage is 100%.",
            count=service_total - scanned,
        ))

    # (1) required-field & contract anomalies -> critical
    for key, c in anomalies.items():
        if c <= 0:
            continue
        cat = key.split("::")[0]
        ex = anomaly_examples.get(key, [])
        if cat in {"required_missing", "retrieval_missing"}:
            sev = "critical"
        elif key in {"empty_chunk", "control_chars_in_chunk", "placeholder_chunk",
                     "locator_artifact_retrieval_eligible", "invalid_content_class",
                     "noise_row_retrieval_eligible", "retrieval_row_missing_search_text",
                     "table_integrity_out_of_range", "table_integrity_not_numeric",
                     "is_prohibition_without_text", "partition_scan_incomplete"}:
            sev = "critical"
        elif key in {"physical_page_not_min_of_list", "physical_page_end_not_max_of_list",
                     "physical_pdf_pages_non_contiguous", "invalid_locator_type",
                     "invalid_criticality", "very_short_chunk_lt20"}:
            sev = "high"
        else:
            sev = "medium"
        findings.append(Finding(sev, cat, f"{key} = {c}", count=c, examples=ex))

    # (2) constant / stub field detector: a field present on many records but
    #     with <=1 distinct value is almost certainly a hardcoded stub.
    for rt, total in counts_by_type.items():
        for f in retrievable:
            present = field_present[rt][f]
            distinct = field_distinct.get(f"{rt}::{f}", set())
            if present >= max(20, 0.5 * total) and len(distinct) <= 1:
                findings.append(Finding(
                    "medium", "constant_stub_field",
                    f"{rt}.{f}: present on {present} records but only "
                    f"{len(distinct)} distinct value(s) "
                    f"(e.g. {field_example.get(f'{rt}::{f}', '')}) — likely a "
                    "hardcoded stub, not real data.",
                    count=present,
                ))

    # (3) placeholder-but-present in any field -> high (garbage that looks OK)
    for rt in counts_by_type:
        for f, c in field_placeholder[rt].items():
            if c > 0:
                findings.append(Finding(
                    "high", "placeholder_value",
                    f"{rt}.{f}: {c} record(s) hold a placeholder value "
                    f"('unknown'/'N/A'/…) that passes an emptiness check but is "
                    "not real data.", count=c,
                ))

    # (4) safety over-flag / under-flag ratios (informational review signal)
    text_total = counts_by_type.get("text", 0)
    if text_total:
        sc_ratio = 100.0 * safety_flag_true["safety_callout"] / text_total
        if sc_ratio >= 40.0:
            findings.append(Finding(
                "high", "safety_callout_overflag",
                f"safety_callout is TRUE on {sc_ratio:.1f}% of text records — "
                "implausibly high; the callout regex likely treats NOTE/NOTICE as "
                "safety. Over-flagging dilutes the safety-boost ranking.",
                count=safety_flag_true["safety_callout"],
            ))
        if safety_flag_true["safety_callout"] == 0:
            findings.append(Finding(
                "high", "safety_callout_never_set",
                "safety_callout is FALSE on every text record — either these "
                "manuals truly have no WARNING/DANGER callouts (verify!) or the "
                "callout extractor is dead.",
            ))

    # (5) vector coverage — the field no other check can see
    vec_findings: list[Finding] = []
    if not args.no_vector_check:
        print("  probing text_vector coverage (integrated vectorization query)...")
        try:
            vec_findings = vector_coverage(search_url, headers, source_files, type_counts)
        except Exception as e:  # noqa: BLE001 - never let the probe crash the audit
            vec_findings = [Finding("high", "vector_probe_error",
                                    f"vector probe raised: {e}")]
        findings.extend(vec_findings)

    # -------------------------------------------------------------------------
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order.get(f.severity, 9), -f.count))
    n_crit = sum(1 for f in findings if f.severity == "critical")
    n_high = sum(1 for f in findings if f.severity == "high")
    n_med = sum(1 for f in findings if f.severity == "medium")

    # per-field rate table
    field_rates: dict[str, dict] = {}
    for rt, total in counts_by_type.items():
        field_rates[rt] = {
            f: {
                "present": field_present[rt][f],
                "missing": field_missing[rt][f],
                "placeholder": field_placeholder[rt][f],
                "present_pct": round(100.0 * field_present[rt][f] / total, 2) if total else 0.0,
                "distinct_seen": len(field_distinct.get(f"{rt}::{f}", set())),
            } for f in retrievable
        }

    out = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index_name": index_name,
        "scoped_source_file": args.source_file,
        "scanned": scanned,
        "service_total": service_total,
        "coverage_ok": coverage_ok,
        "counts_by_record_type": dict(counts_by_type),
        "facet_counts_by_record_type": type_counts,
        "processing_status_counts": dict(status_counts),
        "safety_flag_true_counts": dict(safety_flag_true),
        "n_critical": n_crit, "n_high": n_high, "n_medium": n_med,
        "findings": [f.as_dict() for f in findings],
        "field_rates_by_record_type": field_rates,
    }

    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # human report
    L: list[str] = []
    L.append("# Production Index Audit")
    L.append("")
    verdict = "PASS ✅" if n_crit == 0 and coverage_ok and not (args.strict and n_high) else "FAIL ❌"
    L.append(f"**Verdict: {verdict}**")
    L.append("")
    L.append(f"- Generated: {out['generated_at']}")
    L.append(f"- Index: `{index_name}`")
    L.append(f"- Coverage: scanned **{scanned}** / service **{service_total}** "
             f"→ {'FULL ✅' if coverage_ok else 'INCOMPLETE ❌'}")
    L.append(f"- Findings: **{n_crit} critical**, {n_high} high, {n_med} medium")
    L.append(f"- Records by type: {dict(counts_by_type)}")
    L.append(f"- processing_status: {dict(status_counts)}")
    L.append("")
    for sev, label in [("critical", "Critical (blocks promotion)"),
                       ("high", "High"), ("medium", "Medium")]:
        rows = [f for f in findings if f.severity == sev]
        L.append(f"## {label} — {len(rows)}")
        if not rows:
            L.append("- none")
        for f in rows[:200]:
            ex = f" e.g. {f.examples[:3]}" if f.examples else ""
            L.append(f"- **[{f.category}]** {f.message}{ex}")
        L.append("")
    out_md.write_text("\n".join(L), encoding="utf-8")

    # console summary
    print(f"\nCoverage: {scanned}/{service_total} full={coverage_ok}")
    print(f"Findings: {n_crit} critical, {n_high} high, {n_med} medium")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(f"\nVERDICT: {verdict}")

    if n_crit > 0 or not coverage_ok:
        return 1
    if args.strict and n_high > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
