"""
Pure enrichment helpers shared across the record emitters.

Everything here is a PURE function — no DI, no network, no I/O — so it is unit-
testable in isolation (tests/test_enrichment.py). The emitters (page_label,
process_table, diagram, summary) call these so the new enrichment fields are
populated identically across all record types.

New fields these support:
  topic_id                      cross-type "one topic" group key
  section_path                  "H1 > H2 > H3" breadcrumb
  chapter_label / chapter_number  chapter identifier for citations
  document_title (coalesced)    never-empty title
  tables_referenced_normalized  working text<->table join key
And accuracy guards used at emit time:
  is_valid_page_label, clamp_page, is_boilerplate_revision
"""

from __future__ import annotations

import re

from .ids import _short_hash, safe_str


# ---------------------------------------------------------------------------
# Topic / section grouping
# ---------------------------------------------------------------------------
def topic_id(parent_id: str, section_index) -> str:
    """Section-level group key shared by EVERY record type of one topic, so a
    procedure text chunk, its figure record, and its checklist table record all
    share one id and can be fetched together with `$filter topic_id eq '...'`.
    Same seed idea as procedure_id, but for all record types and all topics."""
    if not parent_id or section_index is None or str(section_index) == "":
        return ""
    return "topic_" + _short_hash(f"{parent_id}|{section_index}|topic", 12)


def section_path(h1, h2, h3) -> str:
    """Human-readable topic breadcrumb for display + cross-type dedup."""
    parts = [safe_str(h).strip() for h in (h1, h2, h3)]
    return " > ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Chapter extraction
# ---------------------------------------------------------------------------
CHAPTER_MARKER_RE = re.compile(
    r"\bchapter\s+(\d{1,3}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*(\d{1,3}|[ivxlcdm]{1,7}))?\b",
    re.IGNORECASE,
)


def _roman_to_int(s: str):
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    s = s.lower()
    total = 0
    prev = 0
    for ch in reversed(s):
        if ch not in vals:
            return None
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def extract_chapter(*candidates) -> tuple[str, str]:
    """Return (chapter_label, chapter_number) from the FIRST candidate that
    contains a chapter marker. Callers MUST pass sources in priority order —
    heading chain first, then start-page furniture — and MUST NOT pass free body
    text (so "see Chapter 3 for details" in prose does not become the chapter).

    "Chapter 1"      -> ("Chapter 1", "1")
    "Chapter 1/2"    -> ("Chapter 1/2", "1")
    "CHAPTER IV — X" -> ("Chapter IV", "4")
    """
    for c in candidates:
        s = safe_str(c)
        if not s:
            continue
        m = CHAPTER_MARKER_RE.search(s)
        if not m:
            continue
        primary = m.group(1)
        label = "Chapter " + primary.upper() if not primary.isdigit() else "Chapter " + primary
        if m.group(2):
            label += "/" + m.group(2)
        if primary.isdigit():
            number = primary
        else:
            n = _roman_to_int(primary)
            number = str(n) if n else primary
        return (label, number)
    return ("", "")


# ---------------------------------------------------------------------------
# Never-empty document title (coalesce)
# ---------------------------------------------------------------------------
_JUNK_TITLE_RE = re.compile(
    r"^(?:untitled|microsoft\s+word\s*-|document\d*$|final$|draft$|\.?pdf$)", re.IGNORECASE
)


def _looks_like_junk_title(s: str) -> bool:
    return bool(_JUNK_TITLE_RE.match(s.strip()))


def coalesce_title(metadata_title, cover_heading, document_number, source_file) -> str:
    """First non-empty, non-junk of: blob metadata_title, cover-page heading,
    document_number; else the filename stem with the extension stripped and
    separators normalized. Guarantees a non-empty title for citations."""
    for cand in (metadata_title, cover_heading, document_number):
        s = safe_str(cand).strip()
        if s and not _looks_like_junk_title(s):
            return s
    stem = safe_str(source_file).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem or safe_str(source_file)


# ---------------------------------------------------------------------------
# Reference normalization (text<->table join, mirrors figure normalization)
# ---------------------------------------------------------------------------
_REF_PREFIX_RE = re.compile(r"^(?:table|tbl|figure|fig)\.?\s*", re.IGNORECASE)
_REF_SEP_RE = re.compile(r"[\s.\-_ –—]+")


def normalize_ref(s) -> str:
    """"Table 5-2" -> "52", "Figure 18.117" -> "18117". Strips the Table/Figure
    prefix and every separator, lowercases. Use to join text-record
    tables_referenced_normalized to table-record table_number-normalized."""
    s = safe_str(s).strip()
    if not s:
        return ""
    s = _REF_PREFIX_RE.sub("", s)
    return _REF_SEP_RE.sub("", s).lower()


def tables_referenced_normalized(refs) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in refs or []:
        n = normalize_ref(r)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Accuracy guards (used at emit time to prevent plausible-but-wrong values)
# ---------------------------------------------------------------------------
_ROMAN_LABEL_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_ALNUM_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-\s]*$")


def is_valid_page_label(label) -> bool:
    """A printed page label is roman ('iv') or a short digit-bearing code
    ('12', 'A-3', '5-7', 'B-12a'). Gate raw DI PageNumber markers through this
    before trusting them, so mis-tagged junk ('Attachment', a full date) is not
    emitted as a page label."""
    s = safe_str(label).strip()
    if not s:
        return False
    if _ROMAN_LABEL_RE.match(s):
        return True
    return len(s) <= 8 and bool(re.search(r"\d", s)) and bool(_ALNUM_LABEL_RE.match(s))


def clamp_page(page, total):
    """Clamp a 1-based physical page to [1, total]. Returns (page, was_clamped).
    Prevents a citation pointing at a page beyond the PDF's length."""
    if not isinstance(page, int):
        return page, False
    if page < 1:
        return 1, True
    if isinstance(total, int) and total > 0 and page > total:
        return total, True
    return page, False


_REV_STOPWORDS = {
    "history", "control", "reviewed", "iewed", "date", "page", "number",
    "table", "figure", "version", "record", "log", "list",
}


def is_boilerplate_revision(rev) -> bool:
    """True if a captured document_revision is actually cover boilerplate
    ('Revision History' -> 'History') rather than a real revision id."""
    s = safe_str(rev).strip()
    if not s:
        return False
    if s.lower() in _REV_STOPWORDS:
        return True
    return " " in s or len(s) > 8
