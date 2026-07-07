# placeholder — paste real content from office laptop
"""
Index Field Reference & Query Examples for Frontend/Backend Teams.
 
This script demonstrates how to query the psegtechmanuals index
using all available fields. Run it to see live examples.
 
Usage:
    python scripts/index_query_guide.py --config deploy.config.json
    python scripts/index_query_guide.py --config deploy.config.json --demo
"""
 
from __future__ import annotations
 
import argparse
import json
import sys
from pathlib import Path
 
# ─── INDEX CONFIGURATION ───────────────────────────────────────────────
INDEX_CONFIG = {
    "api_version": "2024-05-01-preview",
    "semantic_config": "mm-semantic-config",
    "scoring_profiles": ["safety-boost", "freshness-boost"],
    "vector_field": "text_vector",
    "vector_dimensions": 1536,
    "record_types": ["text", "table", "table_row", "diagram", "summary"],
}
 
# ─── FIELD CATALOG ─────────────────────────────────────────────────────
# Every field in the index, grouped by purpose.
 
# Fields to always SELECT in queries (safe for all record types)
SELECT_COMMON = [
    "chunk_id", "record_type", "record_subtype", "parent_id",
    "source_file", "source_url", "source_path", "source_hash",
    "physical_pdf_page", "physical_pdf_page_end", "physical_pdf_pages",
    "printed_page_label", "printed_page_label_end", "printed_page_label_is_synthetic",
    "page_resolution_method", "pdf_total_pages",
    "header_1", "header_2", "header_3", "layout_ordinal",
    "chunk", "highlight_text", "chunk_token_count",
    "processing_status", "skill_version", "index_run_id",
    "document_revision", "effective_date", "document_number",
    "operationalarea", "functionalarea", "doctype", "filetype",
    "language", "last_indexed_at",
]
 
# Diagram-specific fields
SELECT_DIAGRAM = [
    "figure_id", "figure_ref", "figure_bbox",
    "diagram_description", "diagram_ocr_text", "diagram_category",
    "has_diagram", "multi_page_figure",
    "image_hash", "image_phash",
]
 
# Table-specific fields
SELECT_TABLE = [
    "table_caption", "table_bbox", "table_row_count", "table_col_count",
    "table_cluster_id", "table_split_index", "table_split_count",
    "chunk_content_hash",
]
 
# Table row-specific fields
SELECT_TABLE_ROW = [
    "table_caption", "table_bbox", "table_row_index",
    "table_parent_chunk_id", "table_cluster_id",
]
 
# Text-specific fields
SELECT_TEXT = [
    "text_bbox", "figure_ref", "table_ref",
    "figures_referenced", "figures_referenced_normalized",
    "tables_referenced", "sections_referenced", "pages_referenced",
    "safety_callout", "callouts", "footnotes",
    "equipment_ids", "ocr_min_confidence", "chunk_quality_score",
]
 
# Fields that are NOT retrievable (never $select them)
DO_NOT_SELECT = [
    "chunk_for_semantic",   # used only for embedding + reranker
    "surrounding_context",  # used only for embedding
    "text_vector",          # vector field, not retrievable
    "text_parent_id", "dgm_parent_id", "tbl_parent_id",
    "tbl_row_parent_id", "sum_parent_id",  # internal projection keys
]
 
 
# ─── QUERY TEMPLATES ───────────────────────────────────────────────────
 
def build_default_query(question: str, top: int = 20) -> dict:
    """Default hybrid + semantic query. Best for general questions."""
    return {
        "search": question,
        "queryType": "semantic",
        "semanticConfiguration": "mm-semantic-config",
        "captions": "extractive",
        "answers": "extractive|count-3",
        "top": top,
        "vectorQueries": [{
            "kind": "text",
            "text": question,
            "fields": "text_vector",
            "k": 50,
        }],
        "select": ",".join(SELECT_COMMON + SELECT_TEXT + SELECT_DIAGRAM + SELECT_TABLE + SELECT_TABLE_ROW),
        "filter": "processing_status eq 'ok'",
    }
 
 
def build_table_row_query(question: str, top: int = 10) -> dict:
    """Exact value lookup — hits individual table rows."""
    return {
        "search": question,
        "queryType": "semantic",
        "semanticConfiguration": "mm-semantic-config",
        "top": top,
        "vectorQueries": [{
            "kind": "text",
            "text": question,
            "fields": "text_vector",
            "k": 30,
        }],
        "select": ",".join(SELECT_COMMON + SELECT_TABLE_ROW),
        "filter": "record_type eq 'table_row' and processing_status eq 'ok'",
    }
 
 
def build_diagram_query(question: str, top: int = 10) -> dict:
    """Find diagrams by description or OCR labels."""
    return {
        "search": question,
        "queryType": "semantic",
        "semanticConfiguration": "mm-semantic-config",
        "top": top,
        "vectorQueries": [{
            "kind": "text",
            "text": question,
            "fields": "text_vector",
            "k": 30,
        }],
        "select": ",".join(SELECT_COMMON + SELECT_DIAGRAM),
        "filter": "record_type eq 'diagram' and has_diagram eq true and processing_status eq 'ok'",
    }
 
 
def build_safety_query(question: str, top: int = 10) -> dict:
    """Find safety/warning content with boost."""
    return {
        "search": question,
        "queryType": "semantic",
        "semanticConfiguration": "mm-semantic-config",
        "scoringProfile": "safety-boost",
        "scoringParameters": ["safetytags-WARNING,DANGER,CAUTION"],
        "top": top,
        "vectorQueries": [{
            "kind": "text",
            "text": question,
            "fields": "text_vector",
            "k": 30,
        }],
        "select": ",".join(SELECT_COMMON + SELECT_TEXT),
        "filter": "safety_callout eq true and processing_status eq 'ok'",
    }
 
 
def build_table_cluster_query(cluster_id: str) -> dict:
    """Fetch all splits of one logical table."""
    return {
        "search": "*",
        "filter": f"table_cluster_id eq '{cluster_id}' and record_type eq 'table'",
        "select": ",".join(SELECT_COMMON + SELECT_TABLE),
        "orderby": "table_split_index asc",
        "top": 20,
    }
 
 
def build_table_rows_query(cluster_id: str) -> dict:
    """Fetch all rows of one table, ordered."""
    return {
        "search": "*",
        "filter": f"table_cluster_id eq '{cluster_id}' and record_type eq 'table_row'",
        "select": ",".join(SELECT_COMMON + SELECT_TABLE_ROW),
        "orderby": "table_row_index asc",
        "top": 1000,
    }
 
 
def build_parent_table_query(table_parent_chunk_id: str) -> dict:
    """Fetch the parent table record for a table_row citation."""
    return {
        "search": "*",
        "filter": f"chunk_id eq '{table_parent_chunk_id}'",
        "select": ",".join(SELECT_COMMON + SELECT_TABLE),
        "top": 1,
    }
 
 
def build_cross_ref_diagram_query(parent_id: str, figure_ref_normalized: str) -> dict:
    """When text mentions a figure, fetch the diagram record."""
    return {
        "search": "*",
        "filter": (
            f"parent_id eq '{parent_id}' and record_type eq 'diagram' "
            f"and has_diagram eq true "
            f"and figures_referenced_normalized/any(f: f eq '{figure_ref_normalized}')"
        ),
        "select": ",".join(SELECT_COMMON + SELECT_DIAGRAM),
        "top": 5,
    }
 
 
def build_document_summary_query(source_file: str) -> dict:
    """Get the document summary for a specific PDF."""
    return {
        "search": "*",
        "filter": f"source_file eq '{source_file}' and record_type eq 'summary'",
        "select": ",".join(SELECT_COMMON),
        "top": 1,
    }
 
 
# ─── CITATION RENDERING SPEC ──────────────────────────────────────────
 
CITATION_SPEC = """
CITATION RENDERING (for frontend):
 
1. PAGE NAVIGATION:
   url = source_url + "#page=" + physical_pdf_page
   display_label = printed_page_label (if not synthetic) else str(physical_pdf_page)
 
2. HIGHLIGHT RECTANGLE (primary):
   bbox_field = "figure_bbox" if record_type == "diagram"
                else "table_bbox" if record_type in ("table", "table_row")
                else "text_bbox"
   rects = JSON.parse(result[bbox_field])  # IT'S A STRING, MUST PARSE
   
   For each rect in rects where rect.page == currentPage:
     x_css = rect.x_in * 72 * viewport.scale
     y_css = rect.y_in * 72 * viewport.scale  # NO Y-FLIP for PDF.js
     w_css = rect.w_in * 72 * viewport.scale
     h_css = rect.h_in * 72 * viewport.scale
     Draw yellow semi-transparent rectangle at (x_css, y_css, w_css, h_css)
 
3. TEXT SEARCH FALLBACK (if bbox is empty):
   pdfViewer.findController.executeCommand('find', {
     query: result.highlight_text.substring(0, 150)
   })
 
4. BREADCRUMB:
   [header_1, header_2, header_3].filter(Boolean).join(' ▸ ')
 
5. CITATION CHIP:
   "{source_file} — p. {printed_page_label || physical_pdf_page}"
 
6. SAFETY BADGE:
   if (safety_callout) show "⚠ " + callouts.join(", ")
 
7. TABLE ROW context:
   if record_type == "table_row":
     fetch parent via table_parent_chunk_id for header/column context
 
8. DIAGRAM:
   chunk = vision description (not physically on PDF)
   diagram_ocr_text = labels/tags from the image
   Highlight figure_bbox region, show description in panel
"""
 
# ─── INTENT DETECTION (simple regex patterns for backend) ──────────────
 
INTENT_PATTERNS = {
    "diagram": r"diagram|figure|schematic|wiring|drawing|illustration|nameplate|show me",
    "table_row": r"value for|rating of|spec for|what is the.*for|checklist|lookup|\d+\s*(A|V|kV|MW)",
    "table": r"show.*table|table of|full table|all rows",
    "safety": r"warning|caution|danger|hazard|safe|lockout|tagout|loto|de-?energize|grounding",
    "glossary": r"what (does|is).*mean|define|definition|glossary|acronym",
    "summary": r"what is this manual|what does.*cover|overview|summary of",
}
 
 
# ─── MAIN ──────────────────────────────────────────────────────────────
 
def main() -> int:
    ap = argparse.ArgumentParser(description="Index query guide and demo")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--demo", action="store_true", help="Run live demo queries")
    ap.add_argument("--question", default="", help="Run a specific question")
    args = ap.parse_args()
 
    print("=" * 70)
    print("  PSEG TECHNICAL MANUAL INDEX — QUERY REFERENCE")
    print("=" * 70)
    print()
    print(f"Fields: 78 total")
    print(f"Record types: {INDEX_CONFIG['record_types']}")
    print(f"Semantic config: {INDEX_CONFIG['semantic_config']}")
    print(f"Scoring profiles: {INDEX_CONFIG['scoring_profiles']}")
    print(f"Vector: {INDEX_CONFIG['vector_field']} ({INDEX_CONFIG['vector_dimensions']} dims)")
    print()
    print("DO NOT SELECT these fields (non-retrievable):")
    for f in DO_NOT_SELECT:
        print(f"  - {f}")
    print()
    print(CITATION_SPEC)
 
    if not args.demo and not args.question:
        print("\nRun with --demo to execute live queries, or --question 'your question'")
        return 0
 
    # Live demo
    try:
        import httpx
        from azure.identity import DefaultAzureCredential
    except ImportError:
        print("Install httpx and azure-identity for live demo")
        return 1
 
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoint = cfg["search"]["endpoint"].rstrip("/")
    prefix = cfg["search"].get("artifactPrefix") or "mm-manuals"
    index_name = f"{prefix}-index"
    token = DefaultAzureCredential().get_token("https://search.azure.us/.default").token
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version=2024-05-01-preview"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
 
    import os
    cert = os.environ.get("SSL_CERT_FILE") or True
 
    questions = [
        ("Default hybrid", build_default_query(args.question or "24-hour checklist UEOC", top=3)),
        ("Table row lookup", build_table_row_query(args.question or "24-hour checklist", top=3)),
        ("Diagram search", build_diagram_query(args.question or "wiring diagram relay", top=3)),
    ] if not args.question else [
        ("Your question", build_default_query(args.question, top=5)),
    ]
 
    for label, query in questions:
        print(f"\n{'─' * 70}")
        print(f"  QUERY: {label}")
        print(f"{'─' * 70}")
        resp = httpx.post(url, json=query, headers=headers, timeout=30, verify=cert)
        if resp.status_code != 200:
            print(f"  ERROR: {resp.status_code} {resp.text[:200]}")
            continue
        hits = resp.json().get("value", [])
        print(f"  Results: {len(hits)}")
        for i, hit in enumerate(hits[:5]):
            rt = hit.get("record_type", "?")
            sf = hit.get("source_file", "?")
            page = hit.get("physical_pdf_page", "?")
            chunk = (hit.get("chunk") or "")[:100].replace("\n", " ")
            caption = hit.get("table_caption") or ""
            cluster = hit.get("table_cluster_id") or ""
            ocr = (hit.get("diagram_ocr_text") or "")[:60]
            print(f"  {i+1}. [{rt:10s}] {sf} p.{page}")
            if caption:
                print(f"     caption: {caption[:60]}")
            if cluster:
                print(f"     cluster: {cluster}")
            if ocr:
                print(f"     ocr: {ocr}")
            print(f"     chunk: {chunk}...")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 