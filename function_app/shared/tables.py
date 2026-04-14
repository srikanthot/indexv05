"""
Convert DI's tables[] into structured markdown table records.

Features:
- Builds a markdown grid honoring rowIndex/columnIndex (no spans collapsed,
  spanned cells repeat their content for readability).
- Detects multi-page table continuations: a follow-on table on the next
  page with the same column count and no caption is merged.
- Splits oversized tables (>3000 chars) at row boundaries, repeating the
  header row in each split.
"""

from typing import Any

MAX_TABLE_CHARS = 3000


def _cell_text(cell: dict[str, Any]) -> str:
    return (cell.get("content") or "").replace("|", "\\|").replace("\n", " ").strip()


def _table_pages(table: dict[str, Any]) -> list[int]:
    pages = set()
    for br in table.get("boundingRegions", []) or []:
        pn = br.get("pageNumber")
        if isinstance(pn, int):
            pages.add(pn)
    return sorted(pages)


def _table_caption(table: dict[str, Any]) -> str:
    cap = table.get("caption") or {}
    return (cap.get("content") or "").strip()


def _table_to_grid(table: dict[str, Any]) -> list[list[str]]:
    rows = table.get("rowCount") or 0
    cols = table.get("columnCount") or 0
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells", []) or []:
        r = cell.get("rowIndex", 0)
        c = cell.get("columnIndex", 0)
        rs = cell.get("rowSpan", 1) or 1
        cs = cell.get("columnSpan", 1) or 1
        text = _cell_text(cell)
        for dr in range(rs):
            for dc in range(cs):
                rr, cc = r + dr, c + dc
                if 0 <= rr < rows and 0 <= cc < cols:
                    grid[rr][cc] = text
    return grid


def _grid_to_markdown(grid: list[list[str]]) -> str:
    if not grid or not grid[0]:
        return ""
    header = grid[0]
    sep = ["---"] * len(header)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _try_merge_continuation(prev: dict[str, Any], curr: dict[str, Any]) -> bool:
    """
    True if `curr` looks like a continuation of `prev`:
      - immediately following page
      - same column count
      - curr has no caption
    """
    prev_pages = _table_pages(prev)
    curr_pages = _table_pages(curr)
    if not prev_pages or not curr_pages:
        return False
    if curr_pages[0] != prev_pages[-1] + 1:
        return False
    if (prev.get("columnCount") or 0) != (curr.get("columnCount") or 0):
        return False
    if _table_caption(curr):
        return False
    return True


def _first_row_matches(prev_grid: list[list[str]], curr_grid: list[list[str]]) -> bool:
    """True if curr_grid's first row equals prev_grid's first row (header
    repeated on continuation page)."""
    if not prev_grid or not curr_grid:
        return False
    return prev_grid[0] == curr_grid[0]


def _split_oversized(markdown: str) -> list[str]:
    if len(markdown) <= MAX_TABLE_CHARS:
        return [markdown]
    lines = markdown.splitlines()
    if len(lines) < 3:
        return [markdown]
    header = lines[0]
    sep = lines[1]
    body = lines[2:]

    out: list[str] = []
    cur: list[str] = []
    cur_len = len(header) + len(sep) + 2
    for row in body:
        if cur_len + len(row) + 1 > MAX_TABLE_CHARS and cur:
            out.append("\n".join([header, sep, *cur]))
            cur = []
            cur_len = len(header) + len(sep) + 2
        cur.append(row)
        cur_len += len(row) + 1
    if cur:
        out.append("\n".join([header, sep, *cur]))
    return out


def extract_table_records(analyze_result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Returns a list of table record dicts:
      {
        index: int,                # table index (post-merge, post-split)
        page_start, page_end: int,
        markdown: str,
        row_count, col_count: int,
        caption: str,
      }
    """
    raw_tables = analyze_result.get("tables", []) or []
    if not raw_tables:
        return []

    sorted_tables = sorted(
        raw_tables,
        key=lambda t: (_table_pages(t)[0] if _table_pages(t) else 0),
    )

    merged: list[tuple[list[dict[str, Any]], list[list[str]]]] = []
    for tbl in sorted_tables:
        if merged and _try_merge_continuation(merged[-1][0][-1], tbl):
            prev_grid = merged[-1][1]
            new_grid = _table_to_grid(tbl)
            # DI often repeats the header row on continuation pages; drop it
            # so it does not appear as a data row in the merged markdown.
            if _first_row_matches(prev_grid, new_grid):
                new_grid = new_grid[1:]
            prev_grid.extend(new_grid)
            merged[-1][0].append(tbl)
        else:
            merged.append(([tbl], _table_to_grid(tbl)))

    out: list[dict[str, Any]] = []
    for cluster_idx, (cluster, grid) in enumerate(merged):
        first = cluster[0]
        last = cluster[-1]
        pages_first = _table_pages(first)
        pages_last = _table_pages(last)
        page_start = pages_first[0] if pages_first else 0
        page_end = pages_last[-1] if pages_last else page_start
        caption = _table_caption(first)
        md = _grid_to_markdown(grid)

        for split_idx, chunk in enumerate(_split_oversized(md)):
            # Count data rows in this split chunk (total lines minus header
            # + separator), so callers see the real shape of the split.
            chunk_rows = max(0, len(chunk.splitlines()) - 2)
            out.append({
                "index": f"{cluster_idx}_{split_idx}",
                "page_start": page_start,
                "page_end": page_end,
                "markdown": chunk,
                "row_count": chunk_rows,
                "col_count": len(grid[0]) if grid else 0,
                "caption": caption,
            })

    return out
