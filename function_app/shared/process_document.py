"""
process-document custom skill.

One call per source PDF. Fetches the PDF from blob, calls Azure Document
Intelligence directly, extracts:

  - per-figure cropped images + bbox + caption + headers + surrounding context
  - per-table markdown (with multi-page merging + oversized splits)

Returns two arrays the skillset projects as peer records:
  - enriched_figures
  - enriched_tables

The vision call still runs separately (per figure) in the analyze-diagram
skill so we can keep batching/parallelism tunable from the skillset.
"""

import logging
from typing import Dict, Any, List

from .ids import (
    SKILL_VERSION,
    parent_id_for,
    safe_str,
)
from .di_client import analyze_layout, fetch_blob_bytes
from .pdf_crop import crop_figure_png_b64
from .sections import (
    build_section_index,
    find_section_for_page,
    extract_surrounding_text,
)
from .tables import extract_table_records


def _figure_first_page(figure: Dict[str, Any]) -> int:
    for br in figure.get("boundingRegions", []) or []:
        pn = br.get("pageNumber")
        if isinstance(pn, int):
            return pn
    return 0


def _figure_polygon(figure: Dict[str, Any]) -> List[float]:
    for br in figure.get("boundingRegions", []) or []:
        poly = br.get("polygon")
        if poly:
            return poly
    return []


def _figure_caption(figure: Dict[str, Any]) -> str:
    cap = figure.get("caption") or {}
    return (cap.get("content") or "").strip()


def process_document(data: Dict[str, Any]) -> Dict[str, Any]:
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    parent_id = parent_id_for(source_path, source_file)

    if not source_path:
        return {
            "enriched_figures": [],
            "enriched_tables": [],
            "processing_status": "no_source_path",
            "skill_version": SKILL_VERSION,
        }

    try:
        pdf_bytes = fetch_blob_bytes(source_path)
    except Exception as exc:
        logging.exception("blob fetch failed for %s", source_path)
        return {
            "enriched_figures": [],
            "enriched_tables": [],
            "processing_status": f"blob_fetch_error:{type(exc).__name__}",
            "skill_version": SKILL_VERSION,
        }

    try:
        analyze = analyze_layout(pdf_bytes)
    except Exception as exc:
        logging.exception("DI analyze failed for %s", source_path)
        return {
            "enriched_figures": [],
            "enriched_tables": [],
            "processing_status": f"di_error:{type(exc).__name__}",
            "skill_version": SKILL_VERSION,
        }

    sections_index = build_section_index(analyze)

    enriched_figures: List[Dict[str, Any]] = []
    for fig_idx, figure in enumerate(analyze.get("figures", []) or []):
        page = _figure_first_page(figure)
        polygon = _figure_polygon(figure)
        if not page or not polygon:
            continue
        caption = _figure_caption(figure)
        figure_id = figure.get("id") or f"fig_{fig_idx}"

        try:
            image_b64, bbox = crop_figure_png_b64(pdf_bytes, page, polygon)
        except Exception as exc:
            logging.warning("crop failed (fig %s pg %s): %s", figure_id, page, exc)
            continue

        section = find_section_for_page(sections_index, page)
        h1 = section["header_1"] if section else ""
        h2 = section["header_2"] if section else ""
        h3 = section["header_3"] if section else ""
        surrounding = (
            extract_surrounding_text(section["content"], caption, chars=200)
            if section else ""
        )

        enriched_figures.append({
            "figure_id": figure_id,
            "page_number": page,
            "caption": caption,
            "image_b64": image_b64,
            "bbox": bbox,
            "header_1": h1,
            "header_2": h2,
            "header_3": h3,
            "surrounding_context": surrounding,
            "source_file": source_file,
            "source_path": source_path,
            "parent_id": parent_id,
        })

    enriched_tables: List[Dict[str, Any]] = []
    for tbl in extract_table_records(analyze):
        section = find_section_for_page(sections_index, tbl["page_start"])
        h1 = section["header_1"] if section else ""
        h2 = section["header_2"] if section else ""
        h3 = section["header_3"] if section else ""
        enriched_tables.append({
            "table_index": tbl["index"],
            "page_start": tbl["page_start"],
            "page_end": tbl["page_end"],
            "markdown": tbl["markdown"],
            "row_count": tbl["row_count"],
            "col_count": tbl["col_count"],
            "caption": tbl["caption"],
            "header_1": h1,
            "header_2": h2,
            "header_3": h3,
            "source_file": source_file,
            "source_path": source_path,
            "parent_id": parent_id,
        })

    return {
        "enriched_figures": enriched_figures,
        "enriched_tables": enriched_tables,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
