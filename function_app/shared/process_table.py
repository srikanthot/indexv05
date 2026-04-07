"""
shape-table custom skill.

Runs once per enriched_table item produced by process-document. Builds
the index-projection-ready record (chunk_id, chunk, chunk_for_semantic,
headers, page span, table_caption, etc.).
"""

from typing import Dict, Any

from .ids import (
    SKILL_VERSION,
    parent_id_for,
    table_chunk_id,
    safe_int,
    safe_str,
)


def _build_semantic(record: Dict[str, Any]) -> str:
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


def process_table(data: Dict[str, Any]) -> Dict[str, Any]:
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

    chunk_id = table_chunk_id(source_path, source_file, table_index)
    chunk_for_semantic = _build_semantic({
        "source_file": source_file,
        "header_1": h1, "header_2": h2, "header_3": h3,
        "caption": caption, "page_start": page_start, "markdown": markdown,
    })

    return {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "record_type": "table",
        "chunk": markdown,
        "chunk_for_semantic": chunk_for_semantic,
        "header_1": h1,
        "header_2": h2,
        "header_3": h3,
        "physical_pdf_page": page_start,
        "physical_pdf_page_end": page_end,
        "table_row_count": row_count,
        "table_col_count": col_count,
        "table_caption": caption,
        "source_file": source_file,
        "source_path": source_path,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
