# placeholder — paste real content from office laptop
"""
Diagnostic: fetch a sample record from the index and dump the exact
highlight/bbox/page data the frontend receives.
 
Run this, copy the output, and paste it to the frontend/backend team
to verify they are rendering it correctly.
 
Usage:
    python scripts/diagnose_highlight.py --config deploy.config.json
    python scripts/diagnose_highlight.py --config deploy.config.json --record-type diagram
    python scripts/diagnose_highlight.py --config deploy.config.json --chunk-id "txt_abc123_4_def456"
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
 
# Fields the frontend needs for citation highlighting
HIGHLIGHT_FIELDS = [
    "chunk_id", "record_type", "source_file", "source_url",
    "physical_pdf_page", "physical_pdf_page_end", "physical_pdf_pages",
    "printed_page_label", "printed_page_label_is_synthetic",
    "highlight_text", "text_bbox", "figure_bbox", "table_bbox",
    "chunk",
]
 
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose citation highlight data")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--record-type", default="text",
                    choices=["text", "diagram", "table", "table_row", "summary"])
    ap.add_argument("--chunk-id", default="",
                    help="Fetch a specific chunk_id instead of random sample")
    ap.add_argument("--query", default="",
                    help="Search query to find a specific record")
    args = ap.parse_args()
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
 
    token = DefaultAzureCredential().get_token(SEARCH_SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
 
    # Build query
    if args.chunk_id:
        body = {
            "search": "*",
            "filter": f"chunk_id eq '{args.chunk_id}'",
            "select": ",".join(HIGHLIGHT_FIELDS),
            "top": 1,
        }
    elif args.query:
        body = {
            "search": args.query,
            "filter": f"record_type eq '{args.record_type}' and processing_status eq 'ok'",
            "select": ",".join(HIGHLIGHT_FIELDS),
            "top": 1,
        }
    else:
        body = {
            "search": "*",
            "filter": f"record_type eq '{args.record_type}' and processing_status eq 'ok'",
            "select": ",".join(HIGHLIGHT_FIELDS),
            "top": 1,
        }
 
    resp = httpx.post(url, json=body, headers=headers, timeout=30.0)
    if resp.status_code != 200:
        print(f"ERROR: {resp.status_code} {resp.text[:300]}")
        return 1
 
    hits = resp.json().get("value", [])
    if not hits:
        print("No records found matching criteria.")
        return 1
 
    record = hits[0]
 
    print("=" * 70)
    print("  CITATION HIGHLIGHT DIAGNOSTIC")
    print("  Record from live index — this is what the frontend receives")
    print("=" * 70)
    print()
    print(f"chunk_id:            {record.get('chunk_id')}")
    print(f"record_type:         {record.get('record_type')}")
    print(f"source_file:         {record.get('source_file')}")
    print(f"source_url:          {record.get('source_url', '')[:80]}...")
    print(f"physical_pdf_page:   {record.get('physical_pdf_page')}")
    print(f"physical_pdf_page_end: {record.get('physical_pdf_page_end')}")
    print(f"physical_pdf_pages:  {record.get('physical_pdf_pages')}")
    print(f"printed_page_label:  {record.get('printed_page_label')}")
    print(f"printed_page_label_is_synthetic: {record.get('printed_page_label_is_synthetic')}")
    print()
 
    # Bbox analysis
    text_bbox = record.get("text_bbox") or ""
    figure_bbox = record.get("figure_bbox") or ""
    table_bbox = record.get("table_bbox") or ""
    active_bbox = text_bbox or figure_bbox or table_bbox
 
    if active_bbox:
        try:
            bbox_data = json.loads(active_bbox)
            bbox_field = "text_bbox" if text_bbox else ("figure_bbox" if figure_bbox else "table_bbox")
            print(f"BBOX FIELD: {bbox_field}")
            print(f"BBOX DATA ({len(bbox_data)} rectangles):")
            for i, b in enumerate(bbox_data):
                print(f"  rect[{i}]: page={b.get('page')} "
                      f"x={b.get('x_in','?')}in y={b.get('y_in','?')}in "
                      f"w={b.get('w_in','?')}in h={b.get('h_in','?')}in")
                # Convert to points for the frontend
                x_pt = b.get('x_in', 0) * 72
                y_pt = b.get('y_in', 0) * 72
                w_pt = b.get('w_in', 0) * 72
                h_pt = b.get('h_in', 0) * 72
                print(f"           -> {x_pt:.1f}pt, {y_pt:.1f}pt, {w_pt:.1f}pt x {h_pt:.1f}pt")
        except json.JSONDecodeError:
            print(f"BBOX FIELD: MALFORMED JSON: {active_bbox[:100]}")
    else:
        print("BBOX: EMPTY (no highlight rectangle available)")
 
    print()
    highlight = record.get("highlight_text") or ""
    print(f"highlight_text length: {len(highlight)} chars")
    print(f"highlight_text (first 200): {highlight[:200]}")
 
    print()
    chunk = record.get("chunk") or ""
    print(f"chunk length: {len(chunk)} chars")
    print(f"chunk (first 200): {chunk[:200]}")
 
    print()
    print("=" * 70)
    print("  EXPECTED FRONTEND BEHAVIOR")
    print("=" * 70)
    print("""
1. Open source_url (with SAS token if needed)
2. Navigate to physical_pdf_page (1-based page number)
3. Parse the bbox JSON field (text_bbox OR figure_bbox OR table_bbox)
4. For EACH rectangle in the array:
   - Convert inches to PDF points: x_pt = x_in * 72, y_pt = y_in * 72, etc.
   - NOTE: DI coordinates are TOP-LEFT origin in inches.
   - If your PDF viewer uses BOTTOM-LEFT origin (standard PDF):
     y_pt_corrected = (page_height_in - y_in - h_in) * 72
   - Draw a semi-transparent highlight rectangle at those coordinates
5. OPTIONALLY use highlight_text as a PDF.js findController search
   (secondary enhancement, not primary mechanism)
 
COORDINATE SYSTEM:
  DI (what index stores):  origin = top-left, units = inches
  PDF standard:            origin = bottom-left, units = points (1/72 inch)
  PDF.js viewport:         origin = top-left, units = CSS pixels at current zoom
 
  Transform for PDF.js:
    x_css = x_in * 72 * viewport.scale
    y_css = y_in * 72 * viewport.scale  (PDF.js uses top-left like DI, no flip needed)
    w_css = w_in * 72 * viewport.scale
    h_css = h_in * 72 * viewport.scale
 
  Transform for raw PDF annotations (bottom-left origin):
    x_pdf = x_in * 72
    y_pdf = (page_height_in - y_in - h_in) * 72
    w_pdf = w_in * 72
    h_pdf = h_in * 72
""")
 
    print("=" * 70)
    print("  QUESTIONS FOR FRONTEND TEAM")
    print("=" * 70)
    print("""
1. Are you reading text_bbox / figure_bbox / table_bbox from the search result?
2. Are you parsing it as JSON (it's a string containing a JSON array)?
3. What coordinate transform are you applying?
   - Are you multiplying by 72 to convert inches to points?
   - Are you Y-flipping for bottom-left origin, or NOT flipping for PDF.js?
   - Are you multiplying by viewport.scale for zoom level?
4. Are you handling page rotation (0/90/180/270)?
5. What is your page_height source? (must match the SAME page dimensions DI used)
6. If bbox is empty, are you falling back to highlight_text text-search?
7. If both are empty, what do you show?
8. For table_row records, are you drawing the table_bbox (whole table) or
   do you have logic to narrow to the specific row?
9. For diagram records, are you drawing figure_bbox (the image region)?
 
REPRODUCE THE BUG:
- Take the bbox data above
- Open the same PDF at the same page
- Manually measure: does the rectangle land on the expected content?
- If it DOES land correctly manually but NOT in the viewer: frontend transform bug
- If it does NOT land correctly even manually: indexing bbox bug
""")
 
    # Output as JSON for programmatic use
    print()
    print("=" * 70)
    print("  RAW JSON (paste this to frontend for testing)")
    print("=" * 70)
    output = {
        "chunk_id": record.get("chunk_id"),
        "record_type": record.get("record_type"),
        "source_file": record.get("source_file"),
        "physical_pdf_page": record.get("physical_pdf_page"),
        "bbox_field": "text_bbox" if text_bbox else ("figure_bbox" if figure_bbox else ("table_bbox" if table_bbox else "none")),
        "bbox_json": active_bbox,
        "highlight_text_first_100": highlight[:100],
        "coordinate_system": "inches, top-left origin",
        "to_pdf_points": "multiply by 72",
        "to_pdfjs_css_pixels": "multiply by 72 * viewport.scale",
    }
    print(json.dumps(output, indent=2))
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 