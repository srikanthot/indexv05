"""
Local unit tests for pure-Python helpers.

These do not require Azure / DI / OpenAI / Search at runtime; they exercise
the deterministic code paths against synthetic inputs that match the shapes
the real services emit. Run with:

    python tests/test_unit.py

Exits non-zero on any failure.
"""

import json
import os
import sys
import traceback

# Make the function_app package importable as a flat module path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

from shared.config import ConfigError, feature_enabled, optional_env, required_env
from shared.ids import (
    chunk_content_hash,
    diagram_chunk_id,
    summary_chunk_id,
    table_chunk_id,
    text_chunk_id,
)
from shared.page_label import (
    _extract_label,
    _marker_timeline,
    compute_page_span,
    process_page_label,
)
from shared.process_table import process_table
from shared.search_cache import _odata_escape, _safe_token, lookup_existing_by_hash
from shared.sections import (
    build_section_index,
    extract_surrounding_text,
    find_section_for_page,
)
from shared.semantic import process_semantic_string
from shared.tables import extract_table_records

# ---------- harness ----------

failures = []
passed = 0


def check(name, condition, detail=""):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failures.append((name, detail))
        print(f"  FAIL  {name}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


# ---------- 1. page-span parser ----------
section("1. compute_page_span")

# Synthetic section spanning pages 5..7 with DI markers in markdown.
SECTION_5_7 = (
    "<!-- PageNumber=\"5\" -->\n"
    "First paragraph on page 5. " + ("alpha " * 50) + "\n"
    "Second paragraph still on page 5. " + ("beta " * 50) + "\n"
    "<!-- PageBreak -->\n"
    "<!-- PageNumber=\"6\" -->\n"
    "Page 6 content. " + ("gamma " * 80) + "\n"
    "<!-- PageBreak -->\n"
    "<!-- PageNumber=\"7\" -->\n"
    "Final page content. " + ("delta " * 60) + "\n"
)

# Chunk that lives entirely on page 5
chunk_p5 = "First paragraph on page 5. " + ("alpha " * 30)
start, end, pages = compute_page_span(chunk_p5, SECTION_5_7, section_start_page=5)
check("chunk entirely on page 5 -> (5,5)", (start, end) == (5, 5), f"got ({start},{end})")
check("chunk p5 pages=[5]", pages == [5], str(pages))

# Chunk that crosses page 5 -> 6
chunk_5_6 = (
    "Second paragraph still on page 5. " + ("beta " * 50) + "\n"
    "<!-- PageBreak -->\n"
    "<!-- PageNumber=\"6\" -->\n"
    "Page 6 content. " + ("gamma " * 20)
)
start, end, pages = compute_page_span(chunk_5_6, SECTION_5_7, section_start_page=5)
check("chunk crosses 5->6", (start, end) == (5, 6), f"got ({start},{end})")
check("chunk 5->6 pages=[5,6]", pages == [5, 6], str(pages))

# Chunk that crosses 6 -> 7
chunk_6_7 = (
    "Page 6 content. " + ("gamma " * 60) + "\n"
    "<!-- PageBreak -->\n"
    "<!-- PageNumber=\"7\" -->\n"
    "Final page content. " + ("delta " * 20)
)
start, end, pages = compute_page_span(chunk_6_7, SECTION_5_7, section_start_page=5)
check("chunk crosses 6->7", (start, end) == (6, 7), f"got ({start},{end})")
check("chunk 6->7 pages=[6,7]", pages == [6, 7], str(pages))

# Chunk spanning all three pages 5..7. In production SplitSkill emits
# chunks that are exact substrings of the section markdown, so we build
# the test chunk the same way: slice from the start of page 5 body.
_offset_page5 = SECTION_5_7.index("First paragraph")
chunk_5_7 = SECTION_5_7[_offset_page5:]
start, end, pages = compute_page_span(chunk_5_7, SECTION_5_7, section_start_page=5)
check("chunk spans 5..7", (start, end) == (5, 7), f"got ({start},{end})")
check("chunk 5..7 pages=[5,6,7]", pages == [5, 6, 7], str(pages))

# Chunk entirely on page 7 (after both breaks)
chunk_p7 = "Final page content. " + ("delta " * 40)
start, end, pages = compute_page_span(chunk_p7, SECTION_5_7, section_start_page=5)
check("chunk entirely on page 7 -> (7,7)", (start, end) == (7, 7), f"got ({start},{end})")
check("chunk p7 pages=[7]", pages == [7], str(pages))

# Chunk whose visible content is on page 5 but which happens to end with
# a PageBreak marker must NOT be attributed to page 6.
chunk_trailing_break = (
    "First paragraph on page 5. " + ("alpha " * 20) + "\n<!-- PageBreak -->\n"
)
start, end, pages = compute_page_span(chunk_trailing_break, SECTION_5_7, section_start_page=5)
check(
    "trailing PageBreak stays on page 5",
    (start, end) == (5, 5),
    f"got ({start},{end})",
)
check("trailing-break pages=[5]", pages == [5], str(pages))

# Section with no markers — single-page section
SECTION_FLAT = "Just one page of text. " * 20
start, end, pages = compute_page_span("Just one page of text.", SECTION_FLAT, section_start_page=12)
check("flat single-page section -> (12,12)", (start, end) == (12, 12), f"got ({start},{end})")
check("flat single-page pages=[12]", pages == [12], str(pages))

# Empty section_content fallback path
chunk_with_marker = "lead text <!-- PageNumber=\"9\" --> trailing text"
start, end, pages = compute_page_span(chunk_with_marker, "", section_start_page=8)
check("no section_content but marker in chunk", (start, end) == (8, 9), f"got ({start},{end})")
check("no section_content pages=[8,9]", pages == [8, 9], str(pages))


# ---------- 2. process_page_label end-to-end ----------
section("2. process_page_label")

result = process_page_label({
    "page_text": chunk_5_6,
    "section_content": SECTION_5_7,
    "source_file": "manual.pdf",
    "source_path": "https://blob/container/manual.pdf",
    "layout_ordinal": 3,
    "physical_pdf_page": 5,
})
check("record_type=text", result["record_type"] == "text")
check("chunk_id has txt_ prefix", result["chunk_id"].startswith("txt_"))
check("physical_pdf_page=5", result["physical_pdf_page"] == 5)
check("physical_pdf_page_end=6", result["physical_pdf_page_end"] == 6, str(result))
check("physical_pdf_pages=[5,6]", result["physical_pdf_pages"] == [5, 6], str(result))
check("processing_status=ok", result["processing_status"] == "ok")
# This chunk crosses page 5 -> 6 and its text contains the
# <!-- PageNumber="6" --> marker (the page-5 marker was BEFORE this
# chunk in the section). The label extractor takes the first non-
# empty marker found inside the chunk, so we expect "6", not "5".
check("printed_page_label populated from DI marker (=6)",
      result["printed_page_label"] == "6")
check("printed_page_label_is_synthetic = False (real DI marker)",
      result.get("printed_page_label_is_synthetic") is False)

# Synthetic-fallback case: chunk text has no DI marker AND heuristic
# can't find a label. Fall back to str(physical_pdf_page) so the field
# is never blank for the citation UI.
synthetic_result = process_page_label({
    "page_text": "Just a paragraph with no markers at all. Just words.",
    "section_content": "Just a paragraph with no markers at all. Just words.",
    "source_file": "manual.pdf",
    "source_path": "https://blob/container/manual.pdf",
    "layout_ordinal": 0,
    "physical_pdf_page": 42,
})
check("synthetic: physical_pdf_page=42",
      synthetic_result["physical_pdf_page"] == 42)
check("synthetic: printed_page_label = str(physical_pdf_page)",
      synthetic_result["printed_page_label"] == "42")
check("synthetic: printed_page_label_is_synthetic = True",
      synthetic_result.get("printed_page_label_is_synthetic") is True)

# ---------- 3. printed-label heuristic ----------
section("3. _extract_label")

check("page-prefix", _extract_label("Page 12\nbody body body\nfooter") == "12")
check("section-dash", _extract_label("3-4\nbody\nbody") == "3-4")
check("roman lowercase", _extract_label("body\nbody\nbody\niv") == "iv")
check("toc-like", _extract_label("TOC-3\nbody body\nfooter").upper().startswith("TOC"))
# Markers should not pollute label extraction
check(
    "label ignores DI markers",
    _extract_label("<!-- PageNumber=\"42\" -->\nPage A-7\nbody") == "A-7",
)


# ---------- 4. section index ----------
section("4. build_section_index + find_section_for_page")

# Build a synthetic DI analyzeResult with two nested sections.
ANALYZE = {
    "paragraphs": [
        {"role": "title", "content": "Manual Title", "boundingRegions": [{"pageNumber": 1}]},
        {"role": "sectionHeading", "content": "1 Overview", "boundingRegions": [{"pageNumber": 2}]},
        {"content": "Overview body text on page 2", "boundingRegions": [{"pageNumber": 2}]},
        {"content": "Overview body text on page 3", "boundingRegions": [{"pageNumber": 3}]},
        {"role": "sectionHeading", "content": "2 Procedures", "boundingRegions": [{"pageNumber": 4}]},
        {"role": "sectionHeading", "content": "2.1 Startup", "boundingRegions": [{"pageNumber": 4}]},
        {"content": "Startup procedure step 1", "boundingRegions": [{"pageNumber": 4}]},
        {"content": "Startup procedure step 2", "boundingRegions": [{"pageNumber": 5}]},
    ],
    "sections": [
        # Root section: contains title + two top-level subsections
        {"elements": ["/paragraphs/0", "/sections/1", "/sections/2"]},
        # Section 1: Overview (paragraphs 1,2,3)
        {"elements": ["/paragraphs/1", "/paragraphs/2", "/paragraphs/3"]},
        # Section 2: Procedures (heading + nested 2.1)
        {"elements": ["/paragraphs/4", "/sections/3"]},
        # Section 3: 2.1 Startup
        {"elements": ["/paragraphs/5", "/paragraphs/6", "/paragraphs/7"]},
    ],
}

idx = build_section_index(ANALYZE)
check("section index non-empty", len(idx) > 0)

s_p2 = find_section_for_page(idx, 2)
check("page 2 -> Overview", s_p2 is not None and "Overview" in s_p2["header_1"], str(s_p2))

s_p5 = find_section_for_page(idx, 5)
check(
    "page 5 -> Procedures + Startup",
    s_p5 is not None and "Procedures" in s_p5["header_1"] and "Startup" in s_p5["header_2"],
    str(s_p5),
)


# ---------- 5. surrounding context ----------
section("5. extract_surrounding_text")

body = (
    "Lots of intro text before the figure. " * 5
    + "Figure 4-2: Schematic of relay. "
    + "Lots of trailing description after the figure. " * 5
)
ctx = extract_surrounding_text(body, "Figure 4-2: Schematic of relay.", chars=80)
check("surrounding has [...] separator", "[...]" in ctx, ctx)
check("surrounding contains body words", "trailing description" in ctx, ctx)

# When anchor not found, fall back to head of section
ctx2 = extract_surrounding_text("alpha beta gamma " * 30, "no-such-anchor", chars=50)
check("fallback head non-empty", len(ctx2) > 0)


# ---------- 6. table extraction with multi-page merge ----------
section("6. extract_table_records")

TABLE_RESULT = {
    "tables": [
        {
            "rowCount": 2,
            "columnCount": 2,
            "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "Header A"},
                {"rowIndex": 0, "columnIndex": 1, "content": "Header B"},
                {"rowIndex": 1, "columnIndex": 0, "content": "row1 a"},
                {"rowIndex": 1, "columnIndex": 1, "content": "row1 b"},
            ],
            "boundingRegions": [{"pageNumber": 10}],
            "caption": {"content": "Table 1: Demo"},
        },
        # Continuation on the very next page, no caption, same column count
        {
            "rowCount": 1,
            "columnCount": 2,
            "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "row2 a"},
                {"rowIndex": 0, "columnIndex": 1, "content": "row2 b"},
            ],
            "boundingRegions": [{"pageNumber": 11}],
        },
        # Unrelated table on page 20
        {
            "rowCount": 2,
            "columnCount": 1,
            "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "single"},
                {"rowIndex": 1, "columnIndex": 0, "content": "value"},
            ],
            "boundingRegions": [{"pageNumber": 20}],
        },
    ]
}

records = extract_table_records(TABLE_RESULT)
check("table records non-empty", len(records) >= 2, str(records))
# First cluster should span 10..11
cluster_a = records[0]
check("merged table page_start=10", cluster_a["page_start"] == 10)
check("merged table page_end=11", cluster_a["page_end"] == 11, str(cluster_a))
check("merged table contains row1 + row2", "row1 a" in cluster_a["markdown"] and "row2 a" in cluster_a["markdown"], cluster_a["markdown"])
check("merged table caption preserved", cluster_a["caption"] == "Table 1: Demo")
# Unrelated table is its own record
unrelated = [r for r in records if r["page_start"] == 20]
check("unrelated table separated", len(unrelated) == 1)


# ---------- 7. process_table shape ----------
section("7. process_table")

shaped = process_table({
    "table_index": "0_0",
    "page_start": 10,
    "page_end": 11,
    "markdown": cluster_a["markdown"],
    "row_count": cluster_a["row_count"],
    "col_count": cluster_a["col_count"],
    "caption": cluster_a["caption"],
    "header_1": "Chapter 4",
    "header_2": "Specifications",
    "header_3": "",
    "source_file": "manual.pdf",
    "source_path": "https://blob/container/manual.pdf",
    "parent_id": "abc123",
})
check("table chunk_id has tbl_ prefix", shaped["chunk_id"].startswith("tbl_"), shaped["chunk_id"])
check("table record_type=table", shaped["record_type"] == "table")
check("table no figure_ref field", "figure_ref" not in shaped, str(shaped.keys()))
check("table chunk_for_semantic has Section line", "Section: Chapter 4 > Specifications" in shaped["chunk_for_semantic"])
check("table chunk_for_semantic includes markdown grid", "| Header A |" in shaped["chunk_for_semantic"])


# ---------- 8. semantic string builder ----------
section("8. process_semantic_string")

text_sem = process_semantic_string({
    "mode": "text",
    "chunk": "body of the chunk",
    "header_1": "Ch1",
    "header_2": "Sec1.2",
    "header_3": "",
    "source_file": "manual.pdf",
    "printed_page_label": "1-3",
})
s_text = text_sem["chunk_for_semantic"]
check("text semantic includes Source", "Source: manual.pdf" in s_text)
check("text semantic includes Section", "Section: Ch1 > Sec1.2" in s_text)
check("text semantic includes Page", "Page: 1-3" in s_text)
check("text semantic includes chunk body", "body of the chunk" in s_text)

dgm_sem = process_semantic_string({
    "mode": "diagram",
    "diagram_description": "Schematic showing relay K1 and contactor C2",
    "diagram_category": "circuit_diagram",
    "figure_ref": "Figure 4-2",
    "context_text": "The relay is described in section 4.1 above.",
    "source_file": "manual.pdf",
    "physical_pdf_page": "12",
})
s_dgm = dgm_sem["chunk_for_semantic"]
check("diagram semantic includes figure ref", "Figure 4-2" in s_dgm)
check("diagram semantic includes category", "circuit_diagram" in s_dgm)
check("diagram semantic includes description", "Schematic showing relay" in s_dgm)
check("diagram semantic includes Context (not Visible text)", "Context:" in s_dgm and "Visible text:" not in s_dgm)


# ---------- 9. id helpers ----------
section("9. id helpers")

t1 = text_chunk_id("p", "f", 0, "first chunk text")
t2 = text_chunk_id("p", "f", 0, "second chunk text")
check("text ids unique by chunk content", t1 != t2)
check("text id prefix txt_", t1.startswith("txt_"))
# Same content -> same id (stable across reindex)
t1_again = text_chunk_id("p", "f", 0, "first chunk text")
check("text id stable for same content", t1 == t1_again)
check("diagram id prefix dgm_", diagram_chunk_id("p", "f", "deadbeef" * 4).startswith("dgm_"))
check("table id prefix tbl_", table_chunk_id("p", "f", "0_0").startswith("tbl_"))
check("summary id prefix sum_", summary_chunk_id("p", "f").startswith("sum_"))


# ---------- 10. chunk_id collision regression ----------
section("10. chunk_id collision regression")

# Same source + same layout_ordinal but DIFFERENT chunk text — these
# represent SplitSkill producing two pages from one section. v2 had a
# bug here: hardcoded page_index=0 made both ids identical and the
# second projection silently overwrote the first in the index.

multi_page_section = (
    "<!-- PageNumber=\"1\" -->\n"
    "Alpha content on page 1. " + ("alpha " * 60)
    + "\n<!-- PageBreak -->\n<!-- PageNumber=\"2\" -->\n"
    + "Beta content on page 2. " + ("beta " * 60)
)
chunk_a = "Alpha content on page 1. " + ("alpha " * 60)
chunk_b = "Beta content on page 2. " + ("beta " * 60)

rec_a = process_page_label({
    "page_text": chunk_a,
    "section_content": multi_page_section,
    "source_file": "manual.pdf",
    "source_path": "https://blob/c/manual.pdf",
    "layout_ordinal": 7,
    "physical_pdf_page": 1,
})
rec_b = process_page_label({
    "page_text": chunk_b,
    "section_content": multi_page_section,
    "source_file": "manual.pdf",
    "source_path": "https://blob/c/manual.pdf",
    "layout_ordinal": 7,
    "physical_pdf_page": 1,
})
check("two split pages get different chunk_ids", rec_a["chunk_id"] != rec_b["chunk_id"], f"{rec_a['chunk_id']} == {rec_b['chunk_id']}")
check("chunk A page=1", rec_a["physical_pdf_page"] == 1)
check("chunk B page=2", rec_b["physical_pdf_page"] == 2, str(rec_b))

# Determinism: same input twice -> same id (so reindex doesn't churn)
rec_a2 = process_page_label({
    "page_text": chunk_a,
    "section_content": multi_page_section,
    "source_file": "manual.pdf",
    "source_path": "https://blob/c/manual.pdf",
    "layout_ordinal": 7,
    "physical_pdf_page": 1,
})
check("chunk_id deterministic across runs", rec_a["chunk_id"] == rec_a2["chunk_id"])


# ---------- 11. table_caption flow ----------
section("11. table_caption first-class")

shaped_with_caption = process_table({
    "table_index": "0_0",
    "page_start": 14,
    "page_end": 14,
    "markdown": "| A | B |\n| --- | --- |\n| 1 | 2 |",
    "row_count": 2,
    "col_count": 2,
    "caption": "Table 5: Transformer ratings",
    "header_1": "Specs",
    "header_2": "Electrical",
    "header_3": "",
    "source_file": "manual.pdf",
    "source_path": "https://blob/c/manual.pdf",
    "parent_id": "abc",
})
check("table_caption populated", shaped_with_caption.get("table_caption") == "Table 5: Transformer ratings")
check("no figure_ref overload on tables", "figure_ref" not in shaped_with_caption)
check(
    "table_caption appears in chunk_for_semantic",
    "Table 5: Transformer ratings" in shaped_with_caption["chunk_for_semantic"],
)

shaped_no_caption = process_table({
    "table_index": "1_0",
    "page_start": 20, "page_end": 20,
    "markdown": "| X |\n| --- |\n| y |",
    "row_count": 2, "col_count": 1,
    "caption": "",
    "header_1": "", "header_2": "", "header_3": "",
    "source_file": "manual.pdf",
    "source_path": "https://blob/c/manual.pdf",
    "parent_id": "abc",
})
check("missing caption -> empty string, not crash", shaped_no_caption.get("table_caption") == "")


# ---------- 12. OData escaping in search_cache ----------
section("12. OData escaping + token whitelist")

check("escape doubles single quotes", _odata_escape("o'malley") == "o''malley")
check("escape on empty string ok", _odata_escape("") == "")
check("hex token accepted", _safe_token("abcdef0123456789") == "abcdef0123456789")
check("dash token accepted", _safe_token("txt_abc-123") == "txt_abc-123")
check("apostrophe rejected", _safe_token("o'malley") is None)
check("space rejected", _safe_token("ab cd") is None)
check("none rejected", _safe_token("") is None)

# Lookup function: when env vars are not set, must return None and not raise.
import os

for k in ("SEARCH_ENDPOINT", "SEARCH_ADMIN_KEY"):
    os.environ.pop(k, None)
result = lookup_existing_by_hash("parent123", "deadbeef")
check("lookup returns None when feature disabled", result is None)


# ---------- 13. config error handling ----------
section("13. config helpers")

import os

for k in ("TEST_REQUIRED_VAR",):
    os.environ.pop(k, None)
raised = False
try:
    required_env("TEST_REQUIRED_VAR")
except ConfigError as e:
    raised = True
    msg = str(e)
check("required_env raises ConfigError when missing", raised)
check("ConfigError message names the variable", "TEST_REQUIRED_VAR" in msg)

os.environ["TEST_REQUIRED_VAR"] = "value"
check("required_env returns value when set", required_env("TEST_REQUIRED_VAR") == "value")
del os.environ["TEST_REQUIRED_VAR"]

check("optional_env returns default", optional_env("UNSET_VAR_X", "fallback") == "fallback")
check("feature_enabled false when missing", feature_enabled("UNSET_VAR_X", "UNSET_VAR_Y") is False)


# ---------- 14. text_utils.build_highlight_text ----------
section("14. build_highlight_text")
from shared.text_utils import build_highlight_text

# Empty / None input
check("None -> ''", build_highlight_text(None) == "")
check("empty -> ''", build_highlight_text("") == "")
check("whitespace -> ''", build_highlight_text("   \n\t  ") == "")

# DI markers stripped
md_with_markers = (
    '<!-- PageNumber="iv" -->\n'
    "# Heading\n"
    "Paragraph text.\n"
    "<!-- PageBreak -->\n"
    "More text."
)
out = build_highlight_text(md_with_markers)
check("DI PageNumber marker stripped", "PageNumber" not in out)
check("DI PageBreak marker stripped", "PageBreak" not in out)
check("markdown header marker '#' stripped", "#" not in out)
check("paragraph content survives", "Paragraph text." in out)

# Smart quotes -> ASCII
smart = "He said “hello” and ‘goodbye’ — see page 5"
out = build_highlight_text(smart)
check("smart double quotes -> ASCII", '"hello"' in out)
check("smart single quotes -> ASCII", "'goodbye'" in out)
check("em dash -> ASCII hyphen", " - " in out)

# End-of-line hyphen joining (lowercase next line only)
hyph = "This is a sen-\ntence and ano-\nther one."
out = build_highlight_text(hyph)
check("eol hyphen joins lowercase continuation", "sentence" in out and "another" in out)

# Real hyphens preserved (next char uppercase / digit)
keep = "Use 32-bit mode and Class-A wiring per Figure 3-2."
out = build_highlight_text(keep)
check("32-bit hyphen preserved", "32-bit" in out)
check("Class-A hyphen preserved", "Class-A" in out)
check("Figure 3-2 hyphen preserved", "3-2" in out)

# Soft hyphen + NBSP + zero-width chars dropped
weird = "soft­hyphen non breaking zero​width"
out = build_highlight_text(weird)
check("soft hyphen dropped", "softhyphen" in out)
check("NBSP -> space", "non breaking" in out)
check("zero-width space dropped", "zerowidth" in out)

# Unicode NFC normalization (NFD 'é' should become NFC 'é')
nfd = "café latte"   # 'cafe' + combining acute
out = build_highlight_text(nfd)
check("NFD -> NFC normalizes combining marks", "café" in out)

# Idempotence
once = build_highlight_text(md_with_markers)
twice = build_highlight_text(once)
check("build_highlight_text is idempotent", once == twice)

# Length cap
big = "a" * 5000
out = build_highlight_text(big)
check("length cap at 2000", len(out) <= 2000)


# ---------- 15. record-type field parity ----------
# The citation UI wants the same field set on every record_type so it
# does not need per-type special cases. Lock that contract here.
section("15. record-type field parity")

# Diagram record (process_diagram entry point — no_image fast path is
# enough to exercise field surface without needing a real image).
from shared.diagram import process_diagram
dgm = process_diagram({
    "image_b64": "",
    "figure_id": "fig_1",
    "page_number": 12,
    "caption": "",
    "header_1": "Section A",
    "source_file": "test.pdf",
    "source_path": "https://example/test.pdf",
    "parent_id": "p1",
    "pdf_total_pages": 100,
})
check("dgm: physical_pdf_pages is a list",
      isinstance(dgm.get("physical_pdf_pages"), list))
check("dgm: physical_pdf_pages = [page]",
      dgm.get("physical_pdf_pages") == [12])
check("dgm: pdf_total_pages plumbed",
      dgm.get("pdf_total_pages") == 100)
check("dgm: page_resolution_method = 'di_input'",
      dgm.get("page_resolution_method") == "di_input")
check("dgm: highlight_text present",
      "highlight_text" in dgm)
check("dgm: highlight_text = '' for no-image path",
      dgm.get("highlight_text") == "")
# Diagram printed_page_label is always synthesised from the physical
# page (diagrams don't run through extract-page-label).
check("dgm: printed_page_label populated from physical page",
      dgm.get("printed_page_label") == "12")
check("dgm: printed_page_label_end same as start (single page)",
      dgm.get("printed_page_label_end") == "12")
check("dgm: printed_page_label_is_synthetic is True",
      dgm.get("printed_page_label_is_synthetic") is True)

# Table record
tbl = process_table({
    "source_file": "test.pdf",
    "source_path": "https://example/test.pdf",
    "parent_id": "p1",
    "table_index": "0_0",
    "page_start": 5,
    "page_end": 6,
    "markdown": "| A | B |\n| --- | --- |\n| 1 | 2 |",
    "row_count": 1,
    "col_count": 2,
    "caption": "Test table",
    "header_1": "Section A",
    "pdf_total_pages": 100,
    "bboxes": [
        {"page": 5, "x_in": 1.0, "y_in": 2.0, "w_in": 3.0, "h_in": 4.0},
        {"page": 6, "x_in": 1.0, "y_in": 2.0, "w_in": 3.0, "h_in": 1.0},
    ],
})
check("tbl: physical_pdf_pages=[5,6]",
      tbl.get("physical_pdf_pages") == [5, 6])
check("tbl: pdf_total_pages plumbed",
      tbl.get("pdf_total_pages") == 100)
check("tbl: page_resolution_method = 'di_input'",
      tbl.get("page_resolution_method") == "di_input")
check("tbl: highlight_text present and non-empty",
      tbl.get("highlight_text", "") != "")
check("tbl: table_bbox is JSON string with 2 entries",
      isinstance(tbl.get("table_bbox"), str)
      and len(json.loads(tbl["table_bbox"])) == 2)
check("tbl: table_bbox empty string when no bboxes input",
      process_table({
          "source_file": "x.pdf", "source_path": "x", "parent_id": "p",
          "table_index": "0_0", "page_start": 1, "page_end": 1,
          "markdown": "| a |\n|---|\n| b |",
          "row_count": 1, "col_count": 1, "caption": "",
          "header_1": "", "pdf_total_pages": 10, "bboxes": [],
      }).get("table_bbox") == "")
# Table printed_page_label is synthesised from page_start (tables don't
# go through extract-page-label).
check("tbl: printed_page_label = str(page_start)",
      tbl.get("printed_page_label") == "5")
check("tbl: printed_page_label_end = str(page_end)",
      tbl.get("printed_page_label_end") == "6")
check("tbl: printed_page_label_is_synthetic is True",
      tbl.get("printed_page_label_is_synthetic") is True)

# Summary record
from shared.summary import process_doc_summary
sum_rec = process_doc_summary({
    "source_file": "test.pdf",
    "source_path": "https://example/test.pdf",
    "markdown_text": "",          # no_content fast path
    "section_titles": [],
    "pdf_total_pages": 100,
})
check("sum: pdf_total_pages plumbed",
      sum_rec.get("pdf_total_pages") == 100)
check("sum: highlight_text present (empty for no_content path)",
      sum_rec.get("highlight_text") == "")

# Unified contract: every record-type carries pdf_total_pages and highlight_text
for rec_name, rec in [("dgm", dgm), ("tbl", tbl), ("sum", sum_rec)]:
    check(f"{rec_name}: has 'pdf_total_pages' field",
          "pdf_total_pages" in rec)
    check(f"{rec_name}: has 'highlight_text' field",
          "highlight_text" in rec)


# ---------- summary ----------
print()
total = passed + len(failures)
print(f"Results: {passed}/{total} passed")
if failures:
    print()
    print("FAILURES:")
    for name, detail in failures:
        print(f"  - {name}: {detail}")
    sys.exit(1)
print("ALL TESTS PASSED")
sys.exit(0)
