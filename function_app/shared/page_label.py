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

import logging
import re
from typing import Any

from .ids import (
    SKILL_VERSION,
    parent_id_for,
    safe_int,
    safe_str,
    text_chunk_id,
)
from .di_client import fetch_cached_analysis
from .sections import build_section_index

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

# Matches figure references like "Figure 4-2", "Fig. 12", "FIGURE A-1".
# The reference ID must contain at least one digit to avoid false positives
# from DI word-splitting (e.g. "Fig\nure" → "Fig" + "ure").
FIGURE_REF_RE = re.compile(
    r"\b(Figure|Fig\.?)\s*[\-:]?\s*([A-Z]{0,3}[\-\.]?\d[\w\-\.]{0,8})",
    re.IGNORECASE,
)


# ---------- DI markdown page markers ----------
# DI emits both forms: <!-- PageNumber="3" --> and <!-- PageBreak -->
# PageNumber content is the *printed* label (may be "3", "iv", "18-33"),
# so we capture everything between the quotes as a string.
PAGE_NUMBER_MARKER_RE = re.compile(r'<!--\s*PageNumber\s*=\s*"([^"]*)"\s*-->', re.IGNORECASE)
PAGE_BREAK_MARKER_RE = re.compile(r'<!--\s*PageBreak\s*-->', re.IGNORECASE)


# ---------- DI-cache fallback for section_start_page ----------
#
# The skillset currently wires `/document/markdownDocument/*/pageNumber` as
# the section's starting physical page, but DocumentIntelligenceLayoutSkill
# does not reliably expose that field. When the input arrives as None,
# we look up the DI cache blob (written by preanalyze.py), build a section
# index from the raw analyzeResult, and match the chunk's section_content
# to its source section to recover page_start.
#
# Cached at module scope so a batch of chunks from the same PDF triggers
# at most one blob fetch.

_SECTION_INDEX_CACHE: dict[str, list[dict[str, Any]]] = {}


def _sections_for(source_path: str) -> list[dict[str, Any]]:
    if not source_path:
        return []
    cached = _SECTION_INDEX_CACHE.get(source_path)
    if cached is not None:
        return cached
    try:
        analyze = fetch_cached_analysis(source_path)
        if not analyze:
            _SECTION_INDEX_CACHE[source_path] = []
            return []
        result = analyze.get("analyzeResult") if isinstance(analyze, dict) else None
        result = result or analyze
        sections = build_section_index(result)
    except Exception as exc:
        logging.warning("page_label: failed to fetch DI cache for %s: %s",
                        source_path, exc)
        sections = []
    _SECTION_INDEX_CACHE[source_path] = sections
    return sections


def _normalize_text(s: str) -> str:
    """Aggressive normalization for fuzzy content matching across the DI
    markdown output vs DI raw paragraph text. Strips markdown headers,
    list markers, HTML comments, and non-alphanumerics, then lowercases.
    This maximises the chance two logically-equal contents compare equal
    even when one is markdown-rendered and the other is paragraph-concat.
    """
    if not s:
        return ""
    # Drop HTML comments (PageBreak, PageNumber, PageFooter, etc.)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.DOTALL)
    # Drop markdown header/list markers at line starts
    s = re.sub(r"^\s*#{1,6}\s*", " ", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*[\-\*\+]\s+", " ", s, flags=re.MULTILINE)
    # Collapse to alphanumerics + single spaces, lowercased
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _find_section_start_page(
    source_path: str,
    section_content: str,
    header_1: str = "",
    header_2: str = "",
    header_3: str = "",
) -> int | None:
    """Look up this chunk's section in the DI cache and return its
    page_start. Match strategy, in order of reliability:

      1. Exact match on (header_1, header_2, header_3) tuple. Headers
         are the same tokens the skillset uses for text projection, so
         if they are populated they are the most reliable link between
         a chunk and a DI section.
      2. Exact match on (header_1, header_2) when h3 is missing.
      3. Exact match on header_1 alone.
      4. Fuzzy content substring match after aggressive normalization.

    Returns None if no strategy matches (e.g. chunk has no headers AND
    DI section paragraph concat diverges heavily from markdown output).
    """
    sections = _sections_for(source_path)
    if not sections:
        return None

    def _first_page(matches: list[dict[str, Any]]) -> int | None:
        for s in matches:
            ps = s.get("page_start")
            if isinstance(ps, int):
                return ps
        return None

    # Normalize headers for comparison: the skillset's
    # /document/markdownDocument/*/sections/h1..h3 and build_section_index's
    # header_1..3 both trace back to DI paragraph content but may differ in
    # whitespace, so we compare on normalized forms.
    def _nh(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    h1 = _nh(header_1)
    h2 = _nh(header_2)
    h3 = _nh(header_3)

    if h1 or h2 or h3:
        # Tier 1: full chain
        tier1 = [
            s for s in sections
            if _nh(s.get("header_1") or "") == h1
            and _nh(s.get("header_2") or "") == h2
            and _nh(s.get("header_3") or "") == h3
        ]
        p = _first_page(tier1)
        if p is not None:
            return p
        # Tier 2: h1+h2 (chunks sometimes lose h3 at the split boundary)
        if h1 or h2:
            tier2 = [
                s for s in sections
                if _nh(s.get("header_1") or "") == h1
                and _nh(s.get("header_2") or "") == h2
            ]
            p = _first_page(tier2)
            if p is not None:
                return p
        # Tier 3: h1 alone
        if h1:
            tier3 = [s for s in sections if _nh(s.get("header_1") or "") == h1]
            p = _first_page(tier3)
            if p is not None:
                return p

    # Tier 4: fuzzy content match.
    probe = _normalize_text(section_content)[:400]
    if not probe:
        return None
    best: tuple[int, int] | None = None  # (overlap_len, page_start)
    for s in sections:
        content = _normalize_text(s.get("content") or "")
        if not content:
            continue
        if probe[:200] and probe[:200] in content:
            ps = s.get("page_start")
            if isinstance(ps, int):
                overlap = min(len(content), len(probe))
                if best is None or overlap > best[0]:
                    best = (overlap, ps)
        elif content[:200] and content[:200] in probe:
            ps = s.get("page_start")
            if isinstance(ps, int):
                overlap = min(len(content), len(probe))
                if best is None or overlap > best[0]:
                    best = (overlap, ps)
    if best:
        return best[1]

    logging.info(
        "page_label: no DI-cache match for headers=(%r, %r, %r) in %s (have %d sections)",
        h1, h2, h3, source_path, len(sections),
    )
    return None


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

    # Combine both marker types in document order. PageNumber markers
    # carry the *printed* label (e.g. "18-33") which is not a physical
    # page number, so we only use them to double-check integer-style
    # labels (matches "3"). PageBreak is the reliable page advancer.
    events: list[tuple[int, str, int | None]] = []
    for m in PAGE_NUMBER_MARKER_RE.finditer(section_content or ""):
        label = (m.group(1) or "").strip()
        if label.isdigit():
            events.append((m.end(), "num", int(label)))
        # Non-numeric labels (e.g. "18-33") are informational only.
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
    r"(?:\s*<!--\s*(?:PageNumber\s*=\s*\"[^\"]*\"|PageBreak)\s*-->\s*)+\Z",
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
        # DI section pageNumber is unknown and we have no caller-supplied
        # cache lookup at this point. Caller (process_page_label) should
        # have already tried the DI-cache fallback; if we still got None,
        # there is nothing reliable to return.
        return None, None, []
    if not chunk:
        return section_start_page, section_start_page, [section_start_page]

    # Integer-style PageNumber markers inside the chunk (e.g. "3") are
    # used as a fallback when we cannot locate the chunk in section_content.
    # Printed labels like "18-33" are ignored here (they are surfaced as
    # printed_page_label instead).
    nums_in_chunk: list[int] = []
    for m in PAGE_NUMBER_MARKER_RE.finditer(chunk):
        lbl = (m.group(1) or "").strip()
        if lbl.isdigit():
            nums_in_chunk.append(int(lbl))
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
    h1_in = safe_str(data.get("header_1"))
    h2_in = safe_str(data.get("header_2"))
    h3_in = safe_str(data.get("header_3"))

    # Azure Search's DocumentIntelligenceLayoutSkill does not reliably
    # expose a per-section pageNumber in its markdown_document output, so
    # the `physical_pdf_page` input arrives as None for every chunk. When
    # that happens, recover the starting physical page by fetching the
    # DI cache blob (written by preanalyze.py) and matching this chunk's
    # section by header chain (primary) or content fuzzy match (fallback).
    if section_start_page is None:
        section_start_page = _find_section_start_page(
            source_path, section_content, h1_in, h2_in, h3_in,
        )

    start_page, end_page, pages_covered = compute_page_span(
        page_text, section_content, section_start_page
    )

    # Prefer the explicit `<!-- PageNumber="..." -->` marker from DI when
    # present in the chunk — for technical manuals it holds the printed
    # label the reader would recognise (e.g. "18-33", "iv", "A-12").
    label = ""
    for m in PAGE_NUMBER_MARKER_RE.finditer(page_text or ""):
        candidate = (m.group(1) or "").strip()
        if candidate:
            label = candidate
            break
    if not label:
        label = _extract_label(page_text) or ""
    if not label:
        label = str(start_page) if start_page is not None else ""

    # End-label extraction: for multi-page chunks, first try the last DI
    # PageNumber marker in the chunk (which carries the printed label).
    # Fall back to heuristic parsing of the final segment if there is no
    # explicit marker.
    end_label = ""
    if page_text and end_page is not None and start_page is not None and end_page > start_page:
        last_marker = ""
        for m in PAGE_NUMBER_MARKER_RE.finditer(page_text):
            cand = (m.group(1) or "").strip()
            if cand:
                last_marker = cand
        end_label = last_marker or (_extract_label(_last_page_segment(page_text)) or "")
    if not end_label:
        # Single-page chunk or no marker found: reuse the start label for
        # human readability when start==end; otherwise fall back to the
        # numeric page so the field is never empty.
        end_label = label if start_page == end_page else (
            str(end_page) if end_page is not None else label
        )

    # Extract figure references from the text chunk so text records can be
    # cross-referenced with their companion diagram records via figure_ref.
    fig_refs = sorted(
        set(
            f"{m.group(1).title()} {m.group(2)}"
            for m in FIGURE_REF_RE.finditer(page_text)
        )
    )
    figure_ref = ", ".join(fig_refs) if fig_refs else ""

    return {
        "chunk_id": text_chunk_id(source_path, source_file, layout_ordinal, page_text),
        "parent_id": parent_id_for(source_path, source_file),
        "record_type": "text",
        "printed_page_label": label,
        "printed_page_label_end": end_label,
        "physical_pdf_page": start_page,
        "physical_pdf_page_end": end_page,
        "physical_pdf_pages": pages_covered,
        "figure_ref": figure_ref,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
