"""Deterministic table-row quality classification and enrichment.

This module assigns table row quality labels and reason codes, plus
retrieval/eval eligibility flags, without mutating the original row text.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_NOISE = "noise"

REASON_EMPTY_OR_PUNCT_ONLY = "EMPTY_OR_PUNCT_ONLY"
REASON_TOKEN_COUNT_TOO_LOW = "TOKEN_COUNT_TOO_LOW"
REASON_CHAR_COUNT_TOO_LOW = "CHAR_COUNT_TOO_LOW"
REASON_PLACEHOLDER_LITERAL = "PLACEHOLDER_LITERAL"
REASON_INDEX_LIKE_ROW = "INDEX_LIKE_ROW"
REASON_PAGE_REF_ONLY = "PAGE_REF_ONLY"
REASON_HEADER_ONLY = "HEADER_ONLY"
REASON_WEAK_SEMANTIC_SIGNAL = "WEAK_SEMANTIC_SIGNAL"
REASON_VALID_SEMANTIC_KEY_VALUE = "VALID_SEMANTIC_KEY_VALUE"

_MIN_TOKEN_NOISE = 2
_MIN_CHAR_NOISE = 8
_MIN_TOKEN_LOW = 4
_MIN_CHAR_LOW = 16

_PUNCT_ONLY_RE = re.compile(r"^[\W_]+$", re.ASCII)
_MULTI_WS_RE = re.compile(r"\s+")
_DASHES_RE = re.compile(r"[‐‑‒–—―−]")

_PLACEHOLDER_PATTERNS = [
    re.compile(r"^\s*(n/?a|na|none|null|nil|unknown|tbd|tba|--+|\.+)\s*$", re.IGNORECASE),
    re.compile(r"^\s*[xX]\s*$"),
    re.compile(r"^\s*[-_]{2,}\s*$"),
]

_INDEX_LIKE_PATTERNS = [
    re.compile(r"^(figure|fig\.?|table|section|sec\.?|appendix)\s+\d", re.IGNORECASE),
    re.compile(r"^\s*\d+(\.\d+){1,4}\s*[-:]?\s+"),
    re.compile(r"\b(list\s+of\s+figures|list\s+of\s+tables|contents?)\b", re.IGNORECASE),
]

_PAGE_REF_ONLY_PATTERNS = [
    re.compile(r"^\s*(p|pg|page|pages)\s*\.?\s*\d+(\s*[-,]\s*\d+)*\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),
]

_HEADER_ONLY_PATTERNS = [
    re.compile(r"^(header|column|field|parameter|description|value|unit|remarks?)\b", re.IGNORECASE),
]

_OCR_FIXUPS: list[tuple[str, str]] = [
    (" 0f ", " of "),
    (" l ", " 1 "),
    (" | ", " / "),
]

_UNIT_FIXUPS: list[tuple[str, str]] = [
    ("° c", " degc"),
    ("° f", " degf"),
    ("k v", " kv"),
    ("m a", " ma"),
]



def normalize_row_text(text: str) -> str:
    """Deterministically normalize text for semantic parsing/search text."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = _DASHES_RE.sub("-", s)
    s = s.replace(" ", " ")
    s = _MULTI_WS_RE.sub(" ", s).strip()

    lowered = f" {s.lower()} "
    for old, new in _OCR_FIXUPS:
        lowered = lowered.replace(old, new)
    for old, new in _UNIT_FIXUPS:
        lowered = lowered.replace(old, new)
    s = _MULTI_WS_RE.sub(" ", lowered).strip()
    return s



def _token_count(text: str) -> int:
    if not text:
        return 0
    return len([t for t in re.split(r"\s+", text.strip()) if t])



def _is_placeholder_like(text: str) -> bool:
    return any(p.search(text) for p in _PLACEHOLDER_PATTERNS)



def _is_index_like(text: str) -> bool:
    return any(p.search(text) for p in _INDEX_LIKE_PATTERNS)



def _is_page_ref_only(text: str) -> bool:
    return any(p.search(text) for p in _PAGE_REF_ONLY_PATTERNS)



def _is_header_only(text: str) -> bool:
    return any(p.search(text) for p in _HEADER_ONLY_PATTERNS)



def _split_semantic_key_value(normalized_text: str) -> tuple[str, str]:
    """Extract a semantic key/value using deterministic separators."""
    if not normalized_text:
        return "", ""

    parts = [p.strip() for p in re.split(r";", normalized_text) if p.strip()]
    if parts:
        best = parts[0]
    else:
        best = normalized_text

    for sep_re in (r"\s*:\s*", r"\s*=\s*", r"\s+-\s+"):
        m = re.split(sep_re, best, maxsplit=1)
        if len(m) == 2:
            key, value = m[0].strip(), m[1].strip()
            return key, value

    # Fallback: use first token as key for sparse rows.
    tokens = best.split(" ")
    if len(tokens) >= 3:
        key = " ".join(tokens[:2]).strip()
        value = " ".join(tokens[2:]).strip()
        return key, value
    return "", best.strip()



def classify_table_row(
    *,
    source_file: str,
    header_1: str,
    header_2: str,
    header_3: str,
    table_caption: str,
    row_text: str,
) -> dict[str, Any]:
    raw = row_text or ""
    normalized = normalize_row_text(raw)
    char_count = len(normalized)
    token_count = _token_count(normalized)

    reason_codes: list[str] = []

    is_empty_or_punct = (not normalized) or bool(_PUNCT_ONLY_RE.match(normalized))
    is_placeholder_like = _is_placeholder_like(normalized)
    is_index_like = _is_index_like(normalized)
    is_page_ref_only = _is_page_ref_only(normalized)
    is_header_like = _is_header_only(normalized)

    if is_empty_or_punct:
        reason_codes.append(REASON_EMPTY_OR_PUNCT_ONLY)
    if token_count < _MIN_TOKEN_NOISE:
        reason_codes.append(REASON_TOKEN_COUNT_TOO_LOW)
    if char_count < _MIN_CHAR_NOISE:
        reason_codes.append(REASON_CHAR_COUNT_TOO_LOW)
    if is_placeholder_like:
        reason_codes.append(REASON_PLACEHOLDER_LITERAL)
    if is_index_like:
        reason_codes.append(REASON_INDEX_LIKE_ROW)
    if is_page_ref_only:
        reason_codes.append(REASON_PAGE_REF_ONLY)
    if is_header_like:
        reason_codes.append(REASON_HEADER_ONLY)

    semantic_key, semantic_value = _split_semantic_key_value(normalized)
    has_semantic_key_value = bool(semantic_key and semantic_value)

    if has_semantic_key_value:
        reason_codes.append(REASON_VALID_SEMANTIC_KEY_VALUE)
    else:
        reason_codes.append(REASON_WEAK_SEMANTIC_SIGNAL)

    context_parts = [p.strip() for p in [header_1, header_2, header_3, table_caption] if (p or "").strip()]
    table_context_path = " > ".join(context_parts)

    hard_noise = (
        is_empty_or_punct
        or is_placeholder_like
        or is_index_like
        or (is_page_ref_only and not has_semantic_key_value)
        or (token_count < _MIN_TOKEN_NOISE and char_count < _MIN_CHAR_NOISE)
    )

    if hard_noise:
        quality = QUALITY_NOISE
    elif has_semantic_key_value and token_count >= 8 and char_count >= 30:
        quality = QUALITY_HIGH
    elif has_semantic_key_value and token_count >= _MIN_TOKEN_LOW and char_count >= _MIN_CHAR_LOW:
        quality = QUALITY_MEDIUM
    else:
        quality = QUALITY_LOW

    retrieval_eligible = False
    if quality in (QUALITY_HIGH, QUALITY_MEDIUM):
        retrieval_eligible = True
    elif quality == QUALITY_LOW and has_semantic_key_value and not is_index_like and not is_placeholder_like:
        retrieval_eligible = True

    # Search text must be populated for retrieval-eligible rows.
    search_bits = [
        (source_file or "").strip(),
        table_context_path,
        semantic_key,
        semantic_value,
        normalized,
    ]
    table_row_search_text = " | ".join([b for b in search_bits if b])
    if retrieval_eligible and not table_row_search_text:
        table_row_search_text = normalized or raw.strip()

    suggested_for_eval_question = (
        retrieval_eligible
        and quality != QUALITY_NOISE
        and not is_index_like
        and not is_placeholder_like
        and len((semantic_key or "").strip()) >= 2
        and not re.match(r"^col\d+$", (semantic_key or "").strip(), flags=re.IGNORECASE)
    )

    return {
        "table_row_quality": quality,
        "table_row_quality_reason_codes": sorted(set(reason_codes)),
        "table_row_is_header_like": bool(is_header_like),
        "table_row_is_index_like": bool(is_index_like),
        "table_row_is_placeholder_like": bool(is_placeholder_like),
        "table_row_token_count": token_count,
        "table_row_char_count": char_count,
        "table_row_semantic_key": semantic_key,
        "table_row_semantic_value": semantic_value,
        "table_context_path": table_context_path,
        "table_row_search_text": table_row_search_text,
        "retrieval_eligible": bool(retrieval_eligible),
        "suggested_for_eval_question": bool(suggested_for_eval_question),
    }
