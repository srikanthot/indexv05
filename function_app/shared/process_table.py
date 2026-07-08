"""
shape-table custom skill.
 
Runs once per enriched_table item produced by process-document. Builds
the index-projection-ready record (chunk_id, chunk, chunk_for_semantic,
headers, page span, table_caption, etc.).
 
Field parity: emits the same cover-metadata + ops fields that text
records emit (document_revision, effective_date, document_number,
embedding_version, last_indexed_at) so frontend filters work
uniformly across all record types.
"""
 
import datetime
import json
import re
from typing import Any
 
from .config import index_run_id as _index_run_id, optional_env
from .ids import (
    SKILL_VERSION,
    chunk_content_hash,
    parent_id_for,
    safe_int,
    safe_str,
    table_chunk_id,
    table_row_chunk_id,
)
from .table_row_quality import classify_table_row
from .text_utils import build_highlight_text
 
 
def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
def _embedding_version() -> str:
    return optional_env("EMBEDDING_MODEL_VERSION", "text-embedding-ada-002")
 
 
def _approx_token_count(text: str) -> int:
    if not text:
        return 0
    return max(0, (len(text) + 3) // 4)
 
 
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
    # Table coherence: cluster_id groups all splits of one logical table;
    # split_index and split_count locate this record within that group.
    table_cluster_id_raw = safe_str(data.get("cluster_id"), "")
    table_split_index = safe_int(data.get("split_index"), default=0)
    table_split_count = safe_int(data.get("split_count"), default=1)
    # Derive cluster_id as parent_id + cluster_idx for global uniqueness.
    # Falls back to parsing table_index ("0_1" -> cluster "0") for compat.
    if not table_cluster_id_raw:
        parts = table_index.split("_", 1)
        table_cluster_id_raw = parts[0] if parts else "0"
    page_start = safe_int(data.get("page_start"), default=None)
    page_end = safe_int(data.get("page_end"), default=page_start)
    markdown = safe_str(data.get("markdown"))
    row_count = safe_int(data.get("row_count"), default=0)
    col_count = safe_int(data.get("col_count"), default=0)
    caption = safe_str(data.get("caption"))
    # Canonical table number ("Table 12-5") + title (caption minus the number).
    _tnum_m = re.search(r"Table\s+([A-Z]?\d+(?:[.\-]\d+){0,3})", caption, re.IGNORECASE)
    table_number = f"Table {_tnum_m.group(1)}" if _tnum_m else ""
    table_title = re.sub(r"^\s*Table\s+[A-Z]?\d+(?:[.\-]\d+){0,3}\s*[:.\-]*\s*", "", caption, flags=re.IGNORECASE).strip() if caption else ""
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
    chunk_bboxes_list = [
        {
            "page": b.get("page"),
            "x_in": b.get("x_in"),
            "y_in": b.get("y_in"),
            "w_in": b.get("w_in"),
            "h_in": b.get("h_in"),
            "source": "table_region",
            "confidence": 0.95,
        }
        for b in bboxes_list
        if isinstance(b, dict)
    ]
    line_bboxes_list = [
        {
            "page": b.get("page"),
            "x_in": b.get("x_in"),
            "y_in": b.get("y_in"),
            "w_in": b.get("w_in"),
            "h_in": b.get("h_in"),
            "confidence": 0.9,
            "reading_order": i + 1,
        }
        for i, b in enumerate(bboxes_list)
        if isinstance(b, dict)
    ]
    chunk_bboxes_json = json.dumps(chunk_bboxes_list, separators=(",", ":")) if chunk_bboxes_list else ""
    line_bboxes_json = json.dumps(line_bboxes_list, separators=(",", ":")) if line_bboxes_list else ""
    bbox_mode_available = [m for m, ok in (("chunk", bool(chunk_bboxes_list)), ("line", bool(line_bboxes_list))) if ok]
    page_width_in = 8.5
    page_height_in = 11.0
 
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
 
    # Per-row records (record_type="table_row"). Built upstream by
    # tables.extract_table_records; we just shape them into ready-to-
    # project index documents here. Each row carries:
    #   - the parent table's caption + section path + table_index
    #   - the row content rendered as "Header: value; Header: value"
    #   - its own chunk_id, page, bbox (inherited from parent for now)
    # so a query like "200A 4-wire 277/480V conductor" hits the row
    # directly via BM25 + vector search, without the LLM having to
    # traverse the full markdown grid.
 
    # Cover metadata. Read from input data only -- process-document /
    # preanalyze always supply these fields (empty string when the PDF
    # has no cover metadata to extract). Previously had a fallback to
    # cover_metadata_for_pdf when all three were empty, but that fired
    # on every chunk of every PDF without cover metadata and burned
    # 14-22 min per call doing a redundant 23 MB DI cache fetch.
    cover_meta = {
        "document_revision": safe_str(data.get("document_revision")),
        "effective_date": safe_str(data.get("effective_date")),
        "document_number": safe_str(data.get("document_number")),
    }
    embedding_ver = _embedding_version()
    indexed_at = _now_iso()
 
    raw_rows = data.get("table_rows") or []
    row_records: list[dict[str, Any]] = []
    table_scope_tags = [x for x in [h1.strip(), h2.strip(), caption.strip()] if x]
    table_columns = [f"column_{i + 1}" for i in range(col_count)] if col_count > 0 else []
    table_integrity_score = 0.95 if markdown and row_count > 0 and col_count > 0 else 0.0
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        row_index = safe_int(raw_row.get("row_index"), default=0)
        row_text = safe_str(raw_row.get("row_text"))
        row_page = safe_int(raw_row.get("page"), default=page_start)
        if not row_text:
            continue
        row_chunk_id = table_row_chunk_id(source_path, source_file, table_index, row_index)
        # Semantic form: lead with the parent table's identity so the
        # ranker treats the row as a member of that table, not as
        # standalone content. Important for queries that name the table
        # ("Table 18-3 row for 200A").
        row_semantic_parts: list[str] = []
        if source_file:
            row_semantic_parts.append(f"Source: {source_file}")
        header_path = " > ".join([h for h in [h1, h2, h3] if h])
        if header_path:
            row_semantic_parts.append(f"Section: {header_path}")
        if row_page is not None:
            row_semantic_parts.append(f"Page: {row_page}")
        if caption:
            row_semantic_parts.append(f"Table: {caption}")
        row_semantic_parts.append(f"Row: {row_text}")
        row_semantic = "\n".join(row_semantic_parts)
 
        row_printed = str(row_page) if row_page is not None else ""
        row_quality = classify_table_row(
            source_file=source_file,
            header_1=h1,
            header_2=h2,
            header_3=h3,
            table_caption=caption,
            row_text=row_text,
        )
        row_retrieval = bool(
            row_quality.get("retrieval_eligible")
            and h1.strip()
            and isinstance(row_page, int)
            and bool(f"{parent_id}_{table_cluster_id_raw}".strip())
        )
        row_quality["retrieval_eligible"] = row_retrieval
        row_quality["suggested_for_eval_question"] = bool(
            row_retrieval and row_quality.get("suggested_for_eval_question")
        )
        # Structured, collision-free cell binding. The row builder now passes
        # grid-bound header/value lists, so we must NOT re-parse row_text on
        # ':'/';' — a value like a "3:1" ratio or "1:00" time would otherwise
        # be mis-split and re-bound to the wrong column.
        cell_headers = raw_row.get("cell_headers") or []
        cell_values = raw_row.get("cell_values") or []
        all_headers = [h for h in (raw_row.get("all_headers") or []) if h]
        if cell_headers and cell_values:
            row_cells = [
                (f"{h}: {v}" if h else v) for h, v in zip(cell_headers, cell_values)
            ]
            if all_headers:
                # Prefer real column names over the generic "column_N" set.
                table_columns = all_headers
        else:
            # Backward-compat for older cached output.json without structured
            # cells: best-effort parse (may mis-split ':'/';' values).
            row_cells = [p.strip() for p in row_text.split(";") if p.strip()]
            if row_cells and not table_columns:
                table_columns = [p.split(":", 1)[0].strip() for p in row_cells if ":" in p]
        # Per-row applicability + hazard tags (safety numbers live in rows).
        from .content_classifiers import enrich as _enrich_tags
        from .semantic import _extract_callouts as _row_callouts_fn
        _row_headers = [x for x in (h1, h2, h3, caption) if x and x.strip()]
        _row_callouts = _row_callouts_fn(row_text)
        _row_tags = _enrich_tags(row_text, headers=_row_headers, callouts=_row_callouts)
        row_records.append({
            "chunk_id": row_chunk_id,
            "parent_id": parent_id,
            "record_type": "table_row",
            "chunk": row_text,
            "chunk_for_semantic": row_semantic,
            "highlight_text": build_highlight_text(row_text),
            # Inherit the parent table's bbox (we don't compute per-row
            # bboxes — frontend can dim the parent table and accent the
            # row by index if it wants). Same JSON shape as the parent.
            "table_bbox": table_bbox_json,
            "line_bboxes": line_bboxes_json,
            "chunk_bboxes": chunk_bboxes_json,
            "bbox_mode_available": bbox_mode_available,
            "page_width_in": page_width_in,
            "page_height_in": page_height_in,
            "bbox_padding_hint_in": 0.05,
            "bbox_version": "2.0.0",
            "header_1": h1,
            "header_2": h2,
            "header_3": h3,
            "physical_pdf_page": row_page,
            "physical_pdf_page_end": row_page,
            "physical_pdf_pages": [row_page] if row_page is not None else [],
            "printed_page_label": row_printed,
            "printed_page_label_end": row_printed,
            "printed_page_label_is_synthetic": bool(row_printed),
            "pdf_total_pages": pdf_total_pages,
            "page_resolution_method": "di_input" if row_page is not None else "missing",
            # Parent linkage for the citation UI: clicking a row
            # citation can fetch the parent table_chunk_id to render
            # the surrounding context.
            "table_caption": caption,
            "table_parent_chunk_id": chunk_id,
            "table_cluster_id": f"{parent_id}_{table_cluster_id_raw}",
            "table_variant_id": f"{parent_id}_{table_cluster_id_raw}",
            "table_scope_tags": table_scope_tags,
            "table_columns": table_columns,
            "table_row_cells": row_cells,
            "table_row_key": (cell_values[0] if cell_values else ""),
            "table_header_rows_count": 1,
            "table_integrity_score": table_integrity_score,
            "content_role": "actual_content",
            "content_class": "table_content",
            "retrieval_eligible_reason": (
                "eligible_table_row_content"
                if row_retrieval
                else "ineligible_row_quality_or_missing_header_or_page"
            ),
            "applies_to_equipment": _row_tags["applies_to_equipment"],
            "applies_to_system": [x for x in [h1.strip(), h2.strip()] if x],
            "applies_to_voltage": _row_tags["applies_to_voltage"],
            "applies_to_domain": _row_tags["applies_to_domain"],
            "applies_to_phase": _row_tags["applies_to_phase"],
            "hazard_class": _row_tags["hazard_class"],
            "criticality": _row_tags["criticality"],
            "is_prohibition": _row_tags["is_prohibition"],
            "prohibitions": _row_tags["prohibitions"],
            "governing_callouts": _row_callouts,
            "safety_callout": bool(_row_callouts),
            "procedure_id": "",
            "procedure_step_id": "",
            "procedure_step_order": None,
            "procedure_branch_label": "",
            "figure_step_linked": False,
            "figure_linkage_confidence": 0.0,
            "locator_type": "none",
            "locator_value": "",
            "is_locator_artifact": False,
            "artifact_reason_codes": [],
            "table_row_index": row_index,
            "source_file": source_file,
            "source_path": source_path,
            # Cover metadata + ops fields -- parity with text records.
            "document_revision": cover_meta["document_revision"],
            "effective_date": cover_meta["effective_date"],
            "document_number": cover_meta["document_number"],
            "embedding_version": embedding_ver,
            "last_indexed_at": indexed_at,
            "chunk_token_count": _approx_token_count(row_text),
            "language": "en",
            "processing_status": "ok",
            "skill_version": SKILL_VERSION,
            "index_run_id": _index_run_id(),
            **row_quality,
        })
 
    # Content hash for re-embedding gate. When the markdown content of
    # this logical table is unchanged across indexer runs, the embedding
    # skill can short-circuit (search_cache.lookup_existing_by_content_hash
    # returns the prior text_vector). Saves the AOAI call on every table
    # whose content didn't change between runs.
    content_hash = chunk_content_hash(markdown, length=16)
    # cover_meta, embedding_ver, indexed_at already computed above
    # (before the row-records loop). Reused here for the parent record.

    # Applicability + hazard tags for the parent table (was 0% / hardcoded []).
    from .content_classifiers import enrich as _enrich_tags
    from .semantic import _extract_callouts, extract_callout_keywords
    _tbl_headers = [x for x in (h1, h2, h3) if x and x.strip()]
    _tbl_text = " ".join([caption or "", markdown or ""])
    _tbl_callouts = _extract_callouts(markdown)
    _tbl_tags = _enrich_tags(_tbl_text, headers=_tbl_headers, callouts=_tbl_callouts)
    _tbl_callout_keywords = extract_callout_keywords(markdown)
 
    return {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "record_type": "table",
        "chunk": markdown,
        "chunk_for_semantic": chunk_for_semantic,
        "chunk_content_hash": content_hash,
        "highlight_text": highlight,
        "table_bbox": table_bbox_json,
        "line_bboxes": line_bboxes_json,
        "chunk_bboxes": chunk_bboxes_json,
        "bbox_mode_available": bbox_mode_available,
        "page_width_in": page_width_in,
        "page_height_in": page_height_in,
        "bbox_padding_hint_in": 0.05,
        "bbox_version": "2.0.0",
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
        "table_number": table_number,
        "table_title": table_title,
        "table_cluster_id": f"{parent_id}_{table_cluster_id_raw}",
        "table_variant_id": f"{parent_id}_{table_cluster_id_raw}",
        "table_scope_tags": table_scope_tags,
        "table_columns": table_columns,
        "table_row_cells": [],
        "table_header_rows_count": 1,
        "table_integrity_score": table_integrity_score,
        "table_rows_truncated": bool(data.get("rows_truncated")),
        "table_rows_suppressed_count": safe_int(data.get("rows_suppressed_count"), default=0),
        "content_role": "actual_content",
        "content_class": "table_content",
        "retrieval_eligible_reason": (
            "eligible_table_content"
            if bool(
                h1.strip()
                and isinstance(page_start, int)
                and bool(f"{parent_id}_{table_cluster_id_raw}".strip())
            )
            else "ineligible_missing_header_or_page_or_cluster"
        ),
        "applies_to_equipment": _tbl_tags["applies_to_equipment"],
        "applies_to_system": [x for x in [h1.strip(), h2.strip()] if x],
        "applies_to_voltage": _tbl_tags["applies_to_voltage"],
        "applies_to_domain": _tbl_tags["applies_to_domain"],
        "applies_to_phase": _tbl_tags["applies_to_phase"],
        "hazard_class": _tbl_tags["hazard_class"],
        "criticality": _tbl_tags["criticality"],
        "is_prohibition": _tbl_tags["is_prohibition"],
        "prohibitions": _tbl_tags["prohibitions"],
        "governing_callouts": _tbl_callouts,
        "callouts": _tbl_callout_keywords,
        "safety_callout": bool(_tbl_callout_keywords),
        "procedure_id": "",
        "procedure_step_id": "",
        "procedure_step_order": None,
        "procedure_branch_label": "",
        "figure_step_linked": False,
        "figure_linkage_confidence": 0.0,
        "locator_type": "none",
        "locator_value": "",
        "is_locator_artifact": False,
        "artifact_reason_codes": [],
        "table_split_index": table_split_index,
        "table_split_count": table_split_count,
        "table_row_quality": "",
        "table_row_quality_reason_codes": [],
        "table_row_is_header_like": False,
        "table_row_is_index_like": False,
        "table_row_is_placeholder_like": False,
        "table_row_token_count": 0,
        "table_row_char_count": 0,
        "table_row_semantic_key": "",
        "table_row_semantic_value": "",
        "table_context_path": " > ".join([x for x in [h1, h2, h3, caption] if x]),
        "table_row_search_text": "",
        "retrieval_eligible": bool(
            h1.strip()
            and isinstance(page_start, int)
            and bool(f"{parent_id}_{table_cluster_id_raw}".strip())
        ),
        "suggested_for_eval_question": False,
        "source_file": source_file,
        "source_path": source_path,
        # Cover metadata + ops fields -- parity with text records.
        "document_revision": cover_meta["document_revision"],
        "effective_date": cover_meta["effective_date"],
        "document_number": cover_meta["document_number"],
        "embedding_version": embedding_ver,
        "last_indexed_at": indexed_at,
        "chunk_token_count": _approx_token_count(markdown),
        "language": "en",
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
        "index_run_id": _index_run_id(),
        # List of pre-built per-row records ready for indexProjection.
        # Empty list when the table has fewer than 5 or more than 80 body
        # rows (cf. ROW_RECORD_MIN/MAX_ROWS in tables.py).
        "table_rows": row_records,
    }
 