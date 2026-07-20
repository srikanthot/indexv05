"""
Sample-based FIELD ACCURACY audit — "how good is the data, really?"

Unlike audit_index_production.py (which scans ALL ~210k records for coverage),
this reads a STRATIFIED SAMPLE (default 2,000 chunks, spread across every
source_file and record_type) and scores each field on two axes:

  fill%   — how often the field is non-empty
  valid%  — of the filled ones, how often the value passes a consistency/
            validity check (no human ground truth needed — the checks catch
            values that are internally contradictory or malformed, e.g. a
            physical_pdf_page greater than the PDF's page count, an effective_date
            that isn't a real date, a printed_page_label that disagrees with the
            physical page, a bbox whose coordinates fall outside the page).

It is READ-ONLY and fast. It never preanalyzes, indexes, or deploys. Run it as
often as you like; re-run after a fix to watch valid% climb.

Optional --llm-judge takes a small sub-sample and asks a chat model whether the
extracted fields plausibly match the chunk text (catches semantically-wrong-but-
well-formed values a rule can't). Requires --chat-deployment; otherwise the
sub-sample is written to a file you can score with your own tooling.

Usage:
  python scripts/audit_index_accuracy.py --config deploy.config.json
  python scripts/audit_index_accuracy.py --config deploy.config.json --n 3000 --seed 7
  python scripts/audit_index_accuracy.py --config deploy.config.json --llm-judge --chat-deployment gpt51psegtmuatv01

Outputs:
  reports/accuracy_audit.json
  reports/accuracy_audit.md     (per-field scorecard, worst-first)
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-07-01"
SEARCH_SCOPE = "https://search.azure.us/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.us/.default"
SKIP_LIMIT = 100_000

RECORD_TYPES = ["text", "diagram", "table", "table_row", "summary"]

VALID_CONTENT_CLASSES = {
    "operational_content", "table_content", "figure_content",
    "procedure_step", "locator_artifact", "summary_content", "other",
}
VALID_CRITICALITY = {"low", "medium", "high", "critical", "info", "normal"}
VALID_LOCATOR_TYPES = {"none", "page", "section", "figure", "table", "step", "header"}
REVISION_STOPWORDS = {
    "history", "control", "reviewed", "iewed", "date", "page", "number",
    "table", "figure", "version", "record", "log", "list", "the", "and",
}

PLACEHOLDER_RE = re.compile(
    r"^\s*(?:n/?a|na|none|null|nil|unknown|tbd|tba|undefined|--+|\.{3,}|_+|\-+)\s*$",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
LABEL_RE = re.compile(r"^(?:[ivxlcdm]+|[A-Za-z]{0,4}[-.\s]?\d{1,4}[A-Za-z]?)$", re.IGNORECASE)
DIGIT_RE = re.compile(r"\d")


def _token(scope: str = SEARCH_SCOPE) -> str:
    return DefaultAzureCredential().get_token(scope).token


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


def _post(url, headers, body, timeout=120.0):
    r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_bbox(v):
    """Return a bbox dict/list of dicts, or None if not parseable."""
    if isinstance(v, (dict | list)):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Validator registry. Each returns (is_valid: bool, reason: str). Validators run
# only over FILLED values. A field not present here is scored on fill% only.
# ---------------------------------------------------------------------------
def _v_in(allowed: set[str]) -> Callable:
    def f(v, rec):
        ok = str(v).strip().lower() in allowed
        return ok, "" if ok else f"'{_sample(v)}' not in allowed set"
    return f


def _v_score_0_1(v, rec):
    f = _to_float(v)
    if f is None:
        return False, f"'{_sample(v)}' not numeric"
    return (0.0 <= f <= 1.0), "" if 0.0 <= f <= 1.0 else f"{f} outside 0..1"


def _v_record_type(v, rec):
    ok = v in RECORD_TYPES
    return ok, "" if ok else f"unknown record_type '{v}'"


def _v_physical_page(v, rec):
    p = _to_int(v)
    if p is None:
        return False, f"'{_sample(v)}' not an int"
    if p < 1:
        return False, f"page {p} < 1"
    total = _to_int(rec.get("pdf_total_pages"))
    if total and p > total:
        return False, f"page {p} > pdf_total_pages {total}"
    return True, ""


def _v_page_end(v, rec):
    pe = _to_int(v)
    p = _to_int(rec.get("physical_pdf_page"))
    if pe is None:
        return False, "not an int"
    if p is not None and pe < p:
        return False, f"page_end {pe} < physical_pdf_page {p}"
    total = _to_int(rec.get("pdf_total_pages"))
    if total and pe > total:
        return False, f"page_end {pe} > pdf_total_pages {total}"
    return True, ""


def _v_printed_label(v, rec):
    s = str(v).strip()
    if not LABEL_RE.match(s):
        return False, f"'{s}' not a well-formed page label"
    # If it looks purely numeric and is NOT flagged synthetic, it should agree
    # with the physical page within a small tolerance.
    if s.isdigit() and rec.get("printed_page_label_is_synthetic") is False:
        p = _to_int(rec.get("physical_pdf_page"))
        if p is not None and abs(int(s) - p) > 2:
            return False, f"label {s} disagrees with physical_pdf_page {p} (>2)"
    return True, ""


def _v_effective_date(v, rec):
    s = str(v).strip()[:10]
    if len(s) == 7:  # YYYY-MM
        s = s + "-01"
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return False, f"'{_sample(v)}' not a real ISO date"
    yr = datetime.now(UTC).year
    if d.year < 1950 or d.year > yr + 1:
        return False, f"implausible year {d.year}"
    return True, ""


def _v_revision(v, rec):
    s = str(v).strip()
    low = s.lower()
    if low in REVISION_STOPWORDS or any(w == low for w in REVISION_STOPWORDS):
        return False, f"'{s}' looks like boilerplate, not a revision"
    if " " in s or len(s) > 8:
        return False, f"'{s}' too long / has spaces for a revision id"
    return True, ""


def _v_has_digit(v, rec):
    ok = bool(DIGIT_RE.search(str(v)))
    return ok, "" if ok else f"'{_sample(v)}' has no number"


def _v_chunk(v, rec):
    s = str(v).strip()
    if len(s) < 20:
        return False, f"very short ({len(s)} chars)"
    if CONTROL_RE.search(s):
        return False, "contains control chars"
    if _is_placeholder(s):
        return False, "placeholder-like body"
    return True, ""


def _v_ocr_text(v, rec):
    s = str(v)
    if not s.strip():
        return True, ""
    bad = s.count("�") + len(CONTROL_RE.findall(s))
    if bad and bad / max(1, len(s)) > 0.05:
        return False, "high replacement/control-char ratio (mojibake)"
    return True, ""


def _v_bbox(v, rec):
    b = _parse_bbox(v)
    if b is None:
        return False, "not valid JSON bbox"
    w = _to_float(rec.get("page_width_in"))
    h = _to_float(rec.get("page_height_in"))
    boxes = b if isinstance(b, list) else [b]
    for box in boxes:
        if not isinstance(box, dict):
            continue
        x = _to_float(box.get("x_in", box.get("x")))
        y = _to_float(box.get("y_in", box.get("y")))
        bw = _to_float(box.get("w_in", box.get("w")))
        bh = _to_float(box.get("h_in", box.get("h")))
        if None in (x, y, bw, bh):
            continue
        if x < -0.1 or y < -0.1 or bw <= 0 or bh <= 0:
            return False, f"degenerate box {box}"
        if w and (x + bw) > w * 1.05:
            return False, "box extends past page width"
        if h and (y + bh) > h * 1.05:
            return False, "box extends past page height"
    return True, ""


VALIDATORS: dict[str, Callable] = {
    "record_type": _v_record_type,
    "content_class": _v_in(VALID_CONTENT_CLASSES),
    "criticality": _v_in(VALID_CRITICALITY),
    "locator_type": _v_in(VALID_LOCATOR_TYPES),
    "physical_pdf_page": _v_physical_page,
    "physical_pdf_page_end": _v_page_end,
    "printed_page_label": _v_printed_label,
    "effective_date": _v_effective_date,
    "document_revision": _v_revision,
    "document_number": _v_has_digit,
    "figure_number": _v_has_digit,
    "figure_ref": _v_has_digit,
    "table_number": _v_has_digit,
    "table_integrity_score": _v_score_0_1,
    "chunk_quality_score": _v_score_0_1,
    "ocr_min_confidence": _v_score_0_1,
    "table_row_min_confidence": _v_score_0_1,
    "chunk": _v_chunk,
    "diagram_ocr_text": _v_ocr_text,
    "text_bbox": _v_bbox,
    "chunk_bboxes": _v_bbox,
    "figure_bbox": _v_bbox,
    "table_bbox": _v_bbox,
}


# ---------------------------------------------------------------------------
def build_strata(search_url, headers, source_files):
    """One count query per source_file (facet record_type) -> {(sf,rt): size}."""
    strata: dict[tuple, int] = {}
    for sf in source_files:
        safe = str(sf).replace("'", "''")
        r = _post(search_url, headers, {
            "search": "*", "filter": f"source_file eq '{safe}'",
            "top": 0, "count": True, "facets": ["record_type,count:10"],
        })
        for x in (r.get("@search.facets", {}).get("record_type") or []):
            if x.get("value") and x.get("count"):
                strata[(sf, x["value"])] = int(x["count"])
    return strata


def allocate(strata, n_target, floor):
    total = sum(strata.values()) or 1
    alloc = {}
    for key, size in strata.items():
        want = max(floor, round(n_target * size / total))
        alloc[key] = min(size, want)
    return alloc


def sample_stratum(search_url, headers, sf, rt, size, k, select_str, rng, batch=3):
    """Draw ~k records spread across a stratum via random $skip offsets over a
    stable sort. O(k/batch) requests, not O(size)."""
    safe = str(sf).replace("'", "''")
    flt = f"source_file eq '{safe}' and record_type eq '{rt}'"
    out = []
    if size <= k:
        skip = 0
        while skip < size and skip < SKIP_LIMIT:
            vals = _post(search_url, headers, {
                "search": "*", "filter": flt, "select": select_str,
                "top": min(1000, size - skip), "skip": skip, "orderby": "chunk_id asc",
            }).get("value", [])
            if not vals:
                break
            out.extend(vals)
            skip += len(vals)
        return out
    n_offsets = max(1, (k + batch - 1) // batch)
    hi = min(size, SKIP_LIMIT) - 1
    offsets = sorted({rng.randint(0, max(0, hi)) for _ in range(n_offsets * 2)})[:n_offsets]
    for off in offsets:
        vals = _post(search_url, headers, {
            "search": "*", "filter": flt, "select": select_str,
            "top": batch, "skip": off, "orderby": "chunk_id asc",
        }).get("value", [])
        out.extend(vals)
        if len(out) >= k:
            break
    return out[:k]


# ---------------------------------------------------------------------------
def llm_judge(cfg, records, chat_deployment, out_path):
    """Ask a chat model whether extracted fields plausibly match the chunk text.
    Best-effort: writes the sub-sample to a file, and if a deployment is given,
    calls the Azure-OpenAI chat endpoint and returns aggregate verdicts."""
    sub = [r for r in records if (r.get("record_type") in {"text", "table", "table_row"})][:100]
    payload = [{
        "chunk_id": r.get("chunk_id"),
        "record_type": r.get("record_type"),
        "source_file": r.get("source_file"),
        "physical_pdf_page": r.get("physical_pdf_page"),
        "header_1": r.get("header_1"), "header_2": r.get("header_2"),
        "printed_page_label": r.get("printed_page_label"),
        "criticality": r.get("criticality"), "hazard_class": r.get("hazard_class"),
        "chunk": (r.get("chunk") or "")[:1200],
    } for r in sub]
    Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not chat_deployment:
        print(f"  --llm-judge: wrote {len(payload)} records to {out_path} "
              "(no --chat-deployment given; score them with your own tooling)")
        return {"judged": 0, "note": "sub-sample written to file only"}

    endpoint = (cfg.get("azureOpenAI") or {}).get("endpoint", "").rstrip("/")
    api_ver = (cfg.get("azureOpenAI") or {}).get("apiVersion") or "2024-12-01-preview"
    url = f"{endpoint}/openai/deployments/{chat_deployment}/chat/completions?api-version={api_ver}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token(COGNITIVE_SCOPE)}"}
    verdicts = []
    for rec in payload:
        prompt = (
            "You are auditing a search index for an electrical safety manual RAG bot. "
            "Given one chunk's text and its extracted metadata fields, judge whether the "
            "fields plausibly MATCH the text. Reply ONLY compact JSON: "
            '{\"fields_match\": true|false, \"issues\": [\"...\"]}\n\n'
            + json.dumps(rec, ensure_ascii=False)
        )
        try:
            resp = _post(url, headers, {
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 300,
            }, timeout=60.0)
            txt = resp["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", txt, re.DOTALL)
            v = json.loads(m.group(0)) if m else {"fields_match": None}
            v["chunk_id"] = rec["chunk_id"]
            verdicts.append(v)
        except Exception as e:  # noqa: BLE001 - never let the judge crash the audit
            verdicts.append({"chunk_id": rec["chunk_id"], "fields_match": None, "error": str(e)[:200]})
    bad = [v for v in verdicts if v.get("fields_match") is False]
    return {"judged": len(verdicts), "mismatches": len(bad), "verdicts": verdicts}


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Sample-based field accuracy audit")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--n", type=int, default=2000, help="target sample size")
    ap.add_argument("--floor", type=int, default=3, help="min records per (source_file x type) stratum")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--llm-judge", action="store_true")
    ap.add_argument("--chat-deployment", default=None, help="chat deployment name for --llm-judge")
    ap.add_argument("--out-json", default="reports/accuracy_audit.json")
    ap.add_argument("--out-md", default="reports/accuracy_audit.md")
    args = ap.parse_args()

    rng = random.Random(args.seed)
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
    select_str = ",".join(retrievable)

    meta = _post(search_url, headers, {
        "search": "*", "top": 0, "count": True, "facets": ["source_file,count:1000"],
    })
    service_total = int(meta.get("@odata.count") or 0)
    source_files = [x["value"] for x in (meta.get("@search.facets", {}).get("source_file") or [])
                    if x.get("value")]
    print(f"  {service_total} records across {len(source_files)} source_file(s); building strata...")

    strata = build_strata(search_url, headers, source_files)
    alloc = allocate(strata, args.n, args.floor)
    print(f"  sampling ~{sum(alloc.values())} records from {len(alloc)} strata (target {args.n})")

    # accumulators
    fill: Counter = Counter()
    valid: Counter = Counter()
    filled_total: Counter = Counter()
    bad_examples: dict[str, list] = defaultdict(list)
    sampled_by_type: Counter = Counter()
    records: list[dict] = []
    scanned = 0

    for (sf, rt), k in alloc.items():
        recs = sample_stratum(search_url, headers, sf, rt, strata[(sf, rt)], k, select_str, rng)
        for rec in recs:
            scanned += 1
            sampled_by_type[rec.get("record_type") or "NULL"] += 1
            records.append(rec)
            for fld in retrievable:
                v = rec.get(fld)
                if _is_missing(v):
                    continue
                fill[fld] += 1
                if fld in VALIDATORS:
                    filled_total[fld] += 1
                    ok, reason = VALIDATORS[fld](v, rec)
                    if ok:
                        valid[fld] += 1
                    elif len(bad_examples[fld]) < 8:
                        bad_examples[fld].append({"chunk_id": rec.get("chunk_id"),
                                                  "value": _sample(v), "why": reason})

    # per-field scorecard
    rows = []
    for fld in retrievable:
        filled = fill[fld]
        fill_pct = round(100.0 * filled / scanned, 1) if scanned else 0.0
        if fld in VALIDATORS and filled_total[fld] > 0:
            valid_pct = round(100.0 * valid[fld] / filled_total[fld], 1)
        else:
            valid_pct = None
        rows.append({
            "field": fld, "fill_pct": fill_pct, "filled": filled,
            "valid_pct": valid_pct, "checked": filled_total[fld],
            "bad_examples": bad_examples.get(fld, []),
        })

    # worst-first: fields with a real validity signal and low valid%, then low fill%
    def sort_key(r):
        vp = r["valid_pct"] if r["valid_pct"] is not None else 999
        return (vp, r["fill_pct"])
    rows.sort(key=sort_key)

    llm = None
    if args.llm_judge:
        print("  running LLM judge on a sub-sample...")
        llm = llm_judge(cfg, records, args.chat_deployment, "reports/accuracy_llm_sample.json")

    out = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index_name": index_name, "seed": args.seed,
        "service_total": service_total,
        "sampled": scanned, "sampled_by_record_type": dict(sampled_by_type),
        "target_n": args.n, "strata_count": len(alloc),
        "scorecard": rows, "llm_judge": llm,
    }
    out_json, out_md = Path(args.out_json), Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    L = ["# Field Accuracy Audit (sampled)", "",
         f"- Generated: {out['generated_at']}",
         f"- Index: `{index_name}`  (seed {args.seed})",
         f"- Sampled: **{scanned}** of {service_total} records — {dict(sampled_by_type)}",
         "",
         "fill% = how often non-empty. valid% = of filled, how often the value "
         "passes its consistency check (— = fill-only, no rule).",
         "",
         "## Fields with accuracy problems (worst first)", "",
         "| field | fill% | valid% | checked | example bad value |",
         "|---|---:|---:|---:|---|"]
    for r in rows:
        if r["valid_pct"] is not None and r["valid_pct"] < 100.0:
            ex = r["bad_examples"][0] if r["bad_examples"] else {}
            exs = f"{ex.get('value','')} — {ex.get('why','')}" if ex else ""
            L.append(f"| {r['field']} | {r['fill_pct']} | **{r['valid_pct']}** | {r['checked']} | {exs} |")
    L += ["", "## All fields — fill rate", "", "| field | fill% | valid% |", "|---|---:|---:|"]
    for r in sorted(rows, key=lambda x: x["fill_pct"]):
        vp = r["valid_pct"] if r["valid_pct"] is not None else "—"
        L.append(f"| {r['field']} | {r['fill_pct']} | {vp} |")
    if llm:
        L += ["", "## LLM judge",
              f"- judged: {llm.get('judged')}  mismatches: {llm.get('mismatches','n/a')}"]
    out_md.write_text("\n".join(L), encoding="utf-8")

    problems = [r for r in rows if r["valid_pct"] is not None and r["valid_pct"] < 95.0]
    print(f"\nSampled {scanned} records. Fields below 95% valid: {len(problems)}")
    for r in problems[:15]:
        print(f"  {r['field']:<28} fill {r['fill_pct']:>5}%  valid {r['valid_pct']:>5}%")
    print(f"\nWrote {out_json}\nWrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
