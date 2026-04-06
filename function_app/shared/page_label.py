"""
extract-page-label

Deterministic-first printed page label extractor. Looks for typical
manual page label patterns near the top/bottom of the page text:

  - arabic numerals: "12", "Page 12", "- 12 -"
  - roman numerals:  "iv", "Page xii"
  - chapter style:   "3-12", "A-7", "B.4"
  - TOC/index/forms: "TOC-3", "Index-2", "Form 4-1"

Falls back to the physical PDF page number (as a string) if nothing
is found, so we always emit a usable label.
"""

import re
from typing import Dict, Any, Optional

from .ids import (
    SKILL_VERSION,
    text_chunk_id,
    parent_id_for,
    safe_int,
    safe_str,
)

ROMAN_RE = re.compile(r"\b([ivxlcdm]{1,6})\b", re.IGNORECASE)
PAGE_PREFIX_RE = re.compile(r"\bpage\s+([A-Za-z0-9\-\.]{1,8})\b", re.IGNORECASE)
DASH_NUM_RE = re.compile(r"^[\-\u2013\u2014\s]*([A-Za-z]{1,3}[\-\.]?\d{1,4}|\d{1,4})[\-\u2013\u2014\s]*$")
SECTION_DASH_RE = re.compile(r"\b([A-Z]{1,3}[\-\.]\d{1,4})\b")
TOC_LIKE_RE = re.compile(r"\b(TOC|Index|Form|Fig|Table|App)[\-\s]?(\d{1,4})\b", re.IGNORECASE)


def _is_roman(s: str) -> bool:
    return bool(re.fullmatch(r"[ivxlcdm]+", s, re.IGNORECASE))


def _candidate_lines(text: str):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = lines[:3]
    tail = lines[-3:]
    return head + tail


def _extract_label(text: str) -> Optional[str]:
    if not text:
        return None

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


def process_page_label(data: Dict[str, Any]) -> Dict[str, Any]:
    page_text = safe_str(data.get("page_text"))
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    layout_ordinal = safe_int(data.get("layout_ordinal"), default=0)
    physical_pdf_page = safe_int(data.get("physical_pdf_page"), default=None)

    label = _extract_label(page_text)
    if not label:
        label = str(physical_pdf_page) if physical_pdf_page is not None else ""

    return {
        "chunk_id": text_chunk_id(source_path, source_file, layout_ordinal, 0),
        "parent_id": parent_id_for(source_path, source_file),
        "record_type": "text",
        "printed_page_label": label,
        "printed_page_label_end": label,
        "physical_pdf_page": physical_pdf_page,
        "physical_pdf_page_end": physical_pdf_page,
        "processing_status": "ok",
        "skill_version": SKILL_VERSION,
    }
