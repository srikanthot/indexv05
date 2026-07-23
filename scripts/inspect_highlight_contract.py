"""
Show EXACTLY what the index provides for citation page numbers + highlighting,
and the contract for how a frontend/backend must consume it.

This is a read-only diagnostic. Hand its output to the UI/API team so they can
check their code against the real data:
  - which field is the page number to DISPLAY vs. the page to RENDER on,
  - the bounding-box arrays and their units,
  - how to convert boxes to screen pixels.

It does NOT touch the frontend/backend code (that lives in another repo); it
reports the index's side of the contract so mismatches can be pinned down.

Usage:
    # a few sample records:
    python scripts/inspect_highlight_contract.py --config deploy.config.json --top 5

    # inspect a specific reported mismatch:
    python scripts/inspect_highlight_contract.py --config deploy.config.json \
        --source-file "ED-ED-UGC.pdf" --page 42
    python scripts/inspect_highlight_contract.py --config deploy.config.json \
        --chunk-id "<the chunk_id the UI showed>"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-07-01"
SEARCH_SCOPE = "https://search.azure.us/.default"  # Azure US Gov search scope

# The fields that together define the citation page + highlight contract.
SELECT = ",".join([
    "chunk_id", "source_file", "record_type", "content",
    "physical_pdf_page", "physical_pdf_page_end",
    "printed_page_label", "printed_page_label_end", "printed_page_label_is_synthetic",
    "page_width_in", "page_height_in",
    "text_bbox", "line_bboxes", "chunk_bboxes", "chunk_span_bboxes",
])

CONTRACT = """\
======================================================================
  CITATION PAGE + HIGHLIGHT CONTRACT  (how the UI/API must use these)
======================================================================
Two different "page numbers" exist, for two different jobs:

  * physical_pdf_page (+ _end)   -> the page to RENDER + draw the box on.
        Sequential position in the PDF file (what a viewer's scrollbar shows).
        ALWAYS present. Highlights are anchored to THIS page.

  * printed_page_label (+ _end)  -> the page number to DISPLAY to the user.
        The label printed on the page ("iv", "3-7", "18-25"). This is what a
        reader recognises. Use it for the citation text. If
        printed_page_label_is_synthetic == true, we could not read a real
        label (cover/figure/bad scan) and fell back to the physical page --
        show it as "approximate".

  DO NOT display physical_pdf_page as the citation number, and DO NOT try to
  draw a highlight using printed_page_label. Those are the two most common
  integration mistakes.

BOUNDING BOXES (all four are JSON strings -> parse to an array):
  Each entry = {"page": <physical_pdf_page>, "x_in", "y_in", "w_in", "h_in"}.
  Units are INCHES, origin TOP-LEFT of the physical page.
    - chunk_span_bboxes : one union rect per physical page across the chunk's
                          true page span. PREFERRED render target.
    - chunk_bboxes      : union box(es) for the chunk.
    - line_bboxes       : per-line boxes (finer, but can have gaps).
    - text_bbox         : per-page union (coarsest).
  Draw on the entry's own "page" (a chunk can span pages -> multiple entries
  on different physical pages).

CONVERTING INCHES -> SCREEN PIXELS (the usual source of "box is off"):
  Scale by the page's REAL dimensions, not a hardcoded 8.5x11:
    px_x = x_in * (rendered_page_width_px  / page_width_in)
    px_y = y_in * (rendered_page_height_px / page_height_in)
    px_w = w_in * (rendered_page_width_px  / page_width_in)
    px_h = h_in * (rendered_page_height_px / page_height_in)
  If the UI assumes Letter size or a fixed DPI, boxes drift on any non-Letter
  or landscape page. page_width_in / page_height_in are provided per record.
======================================================================
"""


def _headers() -> dict:
    token = DefaultAzureCredential().get_token(SEARCH_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _odata_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pp_bbox(name: str, raw) -> None:
    if raw in (None, ""):
        print(f"    {name}: (empty)")
        return
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        print(f"    {name}: (unparseable) {str(raw)[:120]}")
        return
    if not arr:
        print(f"    {name}: []  (no boxes)")
        return
    print(f"    {name}: {len(arr)} box(es)")
    for b in arr[:8]:
        if isinstance(b, dict):
            pg = b.get("page")
            print(f"        page={pg}  x_in={b.get('x_in')} y_in={b.get('y_in')} "
                  f"w_in={b.get('w_in')} h_in={b.get('h_in')}")
        else:
            print(f"        {b}")
    if len(arr) > 8:
        print(f"        ... (+{len(arr) - 8} more)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--source-file", help="Filter to one source PDF (exact source_file).")
    ap.add_argument("--page", type=int, help="Filter to one physical_pdf_page.")
    ap.add_argument("--chunk-id", help="Inspect one exact chunk_id.")
    ap.add_argument("--record-type", help="Filter to a record_type (text/table/diagram/...).")
    ap.add_argument("--top", type=int, default=5, help="How many records to show (default 5).")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"

    filters = []
    if args.chunk_id:
        filters.append(f"chunk_id eq {_odata_str(args.chunk_id)}")
    if args.source_file:
        filters.append(f"source_file eq {_odata_str(args.source_file)}")
    if args.page is not None:
        filters.append(f"physical_pdf_page eq {args.page}")
    if args.record_type:
        filters.append(f"record_type eq {_odata_str(args.record_type)}")

    body = {
        "search": "*",
        "select": SELECT,
        "top": args.top,
    }
    if filters:
        body["filter"] = " and ".join(filters)

    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    print(CONTRACT)
    print(f"Index: {index_name}")
    print(f"Filter: {body.get('filter', '(none)')}   Top: {args.top}\n")

    with httpx.Client(timeout=60.0) as c:
        resp = c.post(url, headers=_headers(), json=body)
    if resp.status_code != 200:
        print(f"ERROR: search returned {resp.status_code}: {resp.text[:300]}")
        return 1

    docs = resp.json().get("value") or []
    if not docs:
        print("No records matched. (Is the index populated / filters too narrow?)")
        return 0

    for i, d in enumerate(docs, 1):
        content = (d.get("content") or "").replace("\n", " ")
        print("-" * 70)
        print(f"[{i}] chunk_id={d.get('chunk_id')}")
        print(f"    source_file={d.get('source_file')}   record_type={d.get('record_type')}")
        print(f"    content: {content[:120]}{'...' if len(content) > 120 else ''}")
        print(f"    RENDER on physical page: {d.get('physical_pdf_page')}"
              f" .. {d.get('physical_pdf_page_end')}")
        print(f"    DISPLAY page number    : {d.get('printed_page_label')!r}"
              f" .. {d.get('printed_page_label_end')!r}"
              f"   synthetic={d.get('printed_page_label_is_synthetic')}")
        print(f"    page size (inches)     : {d.get('page_width_in')} x {d.get('page_height_in')}")
        _pp_bbox("chunk_span_bboxes", d.get("chunk_span_bboxes"))
        _pp_bbox("chunk_bboxes", d.get("chunk_bboxes"))
        _pp_bbox("line_bboxes", d.get("line_bboxes"))
        _pp_bbox("text_bbox", d.get("text_bbox"))
    print("-" * 70)
    print(f"\nShown {len(docs)} record(s). Compare these values against what the "
          f"UI/API actually reads and draws.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
