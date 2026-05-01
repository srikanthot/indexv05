"""
shape-table custom skill.

Runs once per enriched_table item produced by process-document. Builds
the index-projection-ready record (chunk_id, chunk, chunk_for_semantic,
headers, page span, table_caption, etc.).
"""

import json
from typing import Any

from .ids import (
    SKILL_VERSION,
    parent_id_for,
    safe_int,
    safe_str,
    table_chunk_id,
)
from .text_utils import build_highlight_text


def _build_semantic(record: dict[str, Any]) -> str:
    source_file = safe_str(record.get("source_file"))
    h1 = safe_str(record.get("header_1"))
    h2 = safe_str(record.get("header_2"))
    h3 = safe_str(record.get("header_3"))
    header_path = " > ".join([h for h in [h1, h2, h3] if h])
    caption = safe_str(record.get("caption"))
    page_start = safe_str(record.get("page_start"))
    markdown = safe_str(record.get("markdown"))

    parts = []
    if source_file:
        parts.append(f"Source: {source_file}")
    if header_path:
        parts.append(f"Section: {header_path}")
    if page_start:
        parts.append(f"Page: {page_start}")
    if caption:
        parts.append(f"Table caption: {caption}")
    parts.append("Table:")
    parts.append(markdown)
    return "\n".join(parts)


def process_table(data: dict[str, Any]) -> dict[str, Any]:
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    parent_id = safe_str(data.get("parent_id")) or parent_id_for(source_path, source_file)

    table_index = safe_str(data.get("table_index"), "0")
    page_start = safe_int(data.get("page_start"), default=None)
    page_end = safe_int(data.get("page_end"), default=page_start)
    markdown = safe_str(data.get("markdown"))
    row_count = safe_int(data.get("row_count"), default=0)
    col_count = safe_int(data.get("col_count"), default=0)
    caption = safe_str(data.get("caption"))
    h1 = safe_str(data.get("header_1"))
    h2 = safe_str(data.get("header_2"))
    h3 = safe_str(data.get("header_3"))
    pdf_total_pages = safe_int(data.get("pdf_total_pages"), default=None)

    # Per-page bboxes for the full table (cluster-level union — every
    # split of one logical table shares the same list). Front-end uses
    # this to draw highlight rectangles on each page the table spans.
    bboxes_in = data.get("bboxes")
    bboxes_list: list[dict[str, Any]] = []
    if isinstance(bboxes_in, list):
        bboxes_list = [b for b in bboxes_in if isinstance(b, dict)]
    table_bbox_json = json.dumps(bboxes_list, separators=(",", ":")) if bboxes_list else ""

    chunk_id = table_chunk_id(source_path, source_file, table_index)
    chunk_for_semantic = _build_semantic({
        "source_file": source_file,
        "header_1": h1, "header_2": h2, "header_3": h3,
        "caption": caption, "page_start": page_start, "markdown": markdown,
    })

    # Full list of physical pages this (possibly multi-page-merged) table
    # spans. Parity with text records for consistent citation UI.
    pages_covered: list[int] = []
    if page_start is not None:
        hi = page_end if page_end is not None else page_start
        if hi < page_start:
            hi = page_start
        pages_covered = list(range(page_start, hi + 1))

    # Highlight text: sanitized markdown so PDF.js / Acrobat search can
    # match against the rendered PDF text layer. Tables are markdown
    # pipe-tables; build_highlight_text strips markdown syntax and
    # collapses whitespace so cell contents survive in a searchable form.
    highlight = build_highlight_text(markdown)

    # Printed page label: tables don't go through extract-page-label,
    # so we don't have a real DI-extracted label. Synthesize from the
    # physical page number so the UI never has a blank page indicator
    # on a table citation. The is_synthetic flag is True so consumers
    # who care can distinguish from real-extracted labels.
    printed_label = str(page_start) if page_start is not None else ""
    printed_label_end = str(page_end) if page_end is not None else printed_label

    return {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "record_type": "table",
        "chunk": markdown,
        "chunk_for_semantic": chunk_for_semantic,
        "highlight_text": highlight,
        "table_bbox": table_bbox_json,
        "header_1": h1,
        "header_2": h2,
        "header_3": h3,
        "physical_pdf_page": page_start,
        "physical_pdf_page_end": page_end,
        "physical_pdf_pages": pages_covered,
        "printed_page_label": printed_label,
        "printed_page_label_end": printed_label_end,
        "printed_page_label_is_synthetic": bool(printed_label),
        "pdf_total_pages": pdf_total_pages,
        # DI gave us the page_start/end via boundingRegions, so this is
        # always direct-from-DI for tables. Mirrors the same field on
        # diagram and text records for a uniform UI signal.
        "page_resolution_method": "di_input" if page_start is not None else "missing",
        "table_row_count": row_count,
        "table_col_count": col_count,
        "table_caption": caption,
        "source_file": source_file,
        "source_path": source_path,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
