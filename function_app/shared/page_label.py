"""
extract-page-label

Two responsibilities for each text chunk:

  1. Extract the printed page label (the human-visible label, e.g. "iv",
     "3-12", "TOC-2") from the chunk's leading or trailing lines.
  2. Compute the chunk's accurate physical PDF page span by parsing the
     `<!-- PageNumber="N" -->` and `<!-- PageBreak -->` markers that
     DocumentIntelligenceLayoutSkill emits into markdownDocument content
     when outputContentFormat=markdown.

The chunk arrives from SplitSkill operating over a single markdown
section, so we also receive the full section content and the section's
first page number. We locate the chunk inside the section content and
walk the marker timeline to figure out which page it starts on and which
page it ends on.
"""

import re
from typing import Any

from .ids import (
    SKILL_VERSION,
    parent_id_for,
    safe_int,
    safe_str,
    text_chunk_id,
)

# ---------- printed-label heuristics ----------

ROMAN_RE = re.compile(r"\b([ivxlcdm]{1,6})\b", re.IGNORECASE)
PAGE_PREFIX_RE = re.compile(r"\bpage\s+([A-Za-z0-9\-\.]{1,8})\b", re.IGNORECASE)
DASH_NUM_RE = re.compile(
    r"^[\-\u2013\u2014\s]*("
    r"[A-Za-z]{1,3}[\-\.]\d{1,4}"   # A-7, B.4
    r"|\d{1,4}[\-\.]\d{1,4}"        # 3-4, 3.4
    r"|\d{1,4}"                     # 12
    r")[\-\u2013\u2014\s]*$"
)
SECTION_DASH_RE = re.compile(r"\b([A-Z]{1,3}[\-\.]\d{1,4})\b")
TOC_LIKE_RE = re.compile(r"\b(TOC|Index|Form|Fig|Table|App)[\-\s]?(\d{1,4})\b", re.IGNORECASE)


# ---------- DI markdown page markers ----------
# DI emits both forms: <!-- PageNumber="3" --> and <!-- PageBreak -->
PAGE_NUMBER_MARKER_RE = re.compile(r'<!--\s*PageNumber\s*=\s*"?(\d+)"?\s*-->', re.IGNORECASE)
PAGE_BREAK_MARKER_RE = re.compile(r'<!--\s*PageBreak\s*-->', re.IGNORECASE)


def _is_roman(s: str) -> bool:
    return bool(re.fullmatch(r"[ivxlcdm]+", s, re.IGNORECASE))


def _candidate_lines(text: str):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = lines[:3]
    tail = lines[-3:]
    return head + tail


def _strip_di_markers(text: str) -> str:
    """Remove DI page markers before printed-label scanning so they don't
    pollute the heuristics."""
    if not text:
        return ""
    text = PAGE_NUMBER_MARKER_RE.sub("", text)
    text = PAGE_BREAK_MARKER_RE.sub("", text)
    return text


def _extract_label(text: str) -> str | None:
    if not text:
        return None
    text = _strip_di_markers(text)

    for line in _candidate_lines(text):
        m = PAGE_PREFIX_RE.search(line)
        if m:
            return m.group(1)

    for line in _candidate_lines(text):
        m = TOC_LIKE_RE.search(line)
        if m:
            return f"{m.group(1).upper()}-{m.group(2)}"

    for line in _candidate_lines(text):
        m = SECTION_DASH_RE.search(line)
        if m:
            return m.group(1)

    for line in _candidate_lines(text):
        m = DASH_NUM_RE.match(line)
        if m:
            return m.group(1)

    for line in _candidate_lines(text):
        m = ROMAN_RE.fullmatch(line)
        if m and _is_roman(m.group(1)):
            return m.group(1).lower()

    return None


# ---------- page-span computation ----------


def _marker_timeline(section_content: str, section_start_page: int) -> list[tuple[int, int]]:
    """
    Walk the section content and return a sorted list of (offset, page_number).
    The first entry is always (0, section_start_page) so any chunk that
    sits before the first explicit marker still resolves to a real page.
    """
    timeline: list[tuple[int, int]] = [(0, section_start_page)]
    current_page = section_start_page

    # Combine both marker types in document order.
    events: list[tuple[int, str, int | None]] = []
    for m in PAGE_NUMBER_MARKER_RE.finditer(section_content or ""):
        events.append((m.end(), "num", int(m.group(1))))
    for m in PAGE_BREAK_MARKER_RE.finditer(section_content or ""):
        events.append((m.end(), "break", None))
    events.sort(key=lambda e: e[0])

    for off, kind, val in events:
        if kind == "num" and val is not None:
            current_page = val
        elif kind == "break":
            current_page = current_page + 1
        timeline.append((off, current_page))

    return timeline


def _page_at_offset(timeline: list[tuple[int, int]], offset: int) -> int:
    """
    Binary-walk: return the page number active at `offset` (the page of the
    most recent marker whose end <= offset).
    """
    page = timeline[0][1]
    for off, pn in timeline:
        if off <= offset:
            page = pn
        else:
            break
    return page


def _last_page_segment(chunk: str) -> str:
    """Return the slice of `chunk` that follows the last DI page marker
    (PageNumber or PageBreak). Used for end-label extraction on
    multi-page chunks so we scan only the final physical page's text."""
    if not chunk:
        return ""
    last_end = -1
    for m in PAGE_NUMBER_MARKER_RE.finditer(chunk):
        last_end = max(last_end, m.end())
    for m in PAGE_BREAK_MARKER_RE.finditer(chunk):
        last_end = max(last_end, m.end())
    if last_end < 0:
        # No markers — fall back to the tail half so we still bias toward
        # the end of the chunk rather than scanning the whole thing.
        return chunk[len(chunk) // 2 :]
    return chunk[last_end:]


# Matches any run of trailing DI page markers (+ whitespace) at the end
# of a chunk. Stripped before offset computation so a chunk whose final
# content sits on page N but whose tail happens to include a PageBreak
# marker is not mis-attributed to page N+1.
TRAILING_MARKERS_RE = re.compile(
    r"(?:\s*<!--\s*(?:PageNumber\s*=\s*\"?\d+\"?|PageBreak)\s*-->\s*)+\Z",
    re.IGNORECASE,
)


def _trim_trailing_markers(chunk: str) -> str:
    """Return chunk with any trailing DI page markers + whitespace removed."""
    if not chunk:
        return chunk
    return TRAILING_MARKERS_RE.sub("", chunk)


def _locate_chunk_in_section(chunk: str, section_content: str) -> int:
    """
    Find chunk start offset in section_content.

    In production SplitSkill emits chunks that are exact substrings of
    the section markdown, so `find(chunk)` is the fast path. The probe
    fallback handles edge cases where whitespace or marker rendering
    normalized slightly.
    """
    if not chunk or not section_content:
        return -1
    idx = section_content.find(chunk)
    if idx >= 0:
        return idx
    probe = _strip_di_markers(chunk)[:200].strip()
    if probe:
        idx = section_content.find(probe)
    return idx


def _pages_in_range(
    timeline: list[tuple[int, int]],
    start_off: int,
    end_off: int,
) -> list[int]:
    """All distinct pages active anywhere in [start_off, end_off]."""
    if end_off < start_off:
        end_off = start_off
    pages = {_page_at_offset(timeline, start_off)}
    for off, pn in timeline:
        if start_off <= off <= end_off:
            pages.add(pn)
        elif off > end_off:
            break
    return sorted(pages)


def compute_page_span(
    chunk: str,
    section_content: str,
    section_start_page: int | None,
) -> tuple[int | None, int | None, list[int]]:
    """
    Returns (start_page, end_page, pages_covered).

    pages_covered is the full sorted list of physical PDF pages the
    chunk touches — critical for citation / highlight UIs that need to
    resolve every page the chunk grounds.

    Falls back to (section_start_page, section_start_page, [section_start_page])
    if we cannot locate the chunk inside the section.
    """
    if section_start_page is None:
        return None, None, []
    if not chunk:
        return section_start_page, section_start_page, [section_start_page]

    # Explicit markers inside the chunk: covers the no-section-context fallback.
    nums_in_chunk = [int(m.group(1)) for m in PAGE_NUMBER_MARKER_RE.finditer(chunk)]
    breaks_in_chunk = list(PAGE_BREAK_MARKER_RE.finditer(chunk))

    if not section_content:
        if nums_in_chunk:
            lo = min([section_start_page] + nums_in_chunk)
            hi = max([section_start_page] + nums_in_chunk)
            return lo, hi, list(range(lo, hi + 1))
        if breaks_in_chunk:
            hi = section_start_page + len(breaks_in_chunk)
            return section_start_page, hi, list(range(section_start_page, hi + 1))
        return section_start_page, section_start_page, [section_start_page]

    timeline = _marker_timeline(section_content, section_start_page)
    chunk_start = _locate_chunk_in_section(chunk, section_content)
    if chunk_start < 0:
        if nums_in_chunk:
            lo = min(nums_in_chunk)
            hi = max(nums_in_chunk)
            return lo, hi, list(range(lo, hi + 1))
        return section_start_page, section_start_page, [section_start_page]

    # Use the *trimmed* chunk length so a trailing PageBreak marker
    # doesn't push chunk_end into the next page.
    effective_len = len(_trim_trailing_markers(chunk))
    chunk_end = chunk_start + effective_len
    start_page = _page_at_offset(timeline, chunk_start)
    end_page = _page_at_offset(timeline, chunk_end)
    if end_page < start_page:
        end_page = start_page
    pages = _pages_in_range(timeline, chunk_start, chunk_end)
    # Ensure start/end are present in the list.
    pages_set = set(pages)
    pages_set.add(start_page)
    pages_set.add(end_page)
    return start_page, end_page, sorted(pages_set)


# ---------- skill entry point ----------


def process_page_label(data: dict[str, Any]) -> dict[str, Any]:
    page_text = safe_str(data.get("page_text"))
    section_content = safe_str(data.get("section_content"))
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    layout_ordinal = safe_int(data.get("layout_ordinal"), default=0)
    section_start_page = safe_int(data.get("physical_pdf_page"), default=None)

    start_page, end_page, pages_covered = compute_page_span(
        page_text, section_content, section_start_page
    )

    label = _extract_label(page_text)
    if not label:
        label = str(start_page) if start_page is not None else ""

    # End-label extraction: for multi-page chunks, slice the chunk text
    # at the last DI page marker and scan only that final segment. This
    # is marker-aware and far more accurate than "scan the second half".
    end_label = ""
    if page_text and end_page is not None and start_page is not None and end_page > start_page:
        end_label = _extract_label(_last_page_segment(page_text)) or ""
    if not end_label:
        # Single-page chunk or no marker found: reuse the start label for
        # human readability when start==end; otherwise fall back to the
        # numeric page so the field is never empty.
        end_label = label if start_page == end_page else (
            str(end_page) if end_page is not None else label
        )

    return {
        "chunk_id": text_chunk_id(source_path, source_file, layout_ordinal, page_text),
        "parent_id": parent_id_for(source_path, source_file),
        "record_type": "text",
        "printed_page_label": label,
        "printed_page_label_end": end_label,
        "physical_pdf_page": start_page,
        "physical_pdf_page_end": end_page,
        "physical_pdf_pages": pages_covered,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
