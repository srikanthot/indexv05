"""
Unit tests for function_app/shared/enrichment.py — the pure enrichment helpers
the record emitters use (topic_id, chapter, title coalesce, ref normalization,
page-label validation, page clamp, revision guard).

Run:  python tests/test_enrichment.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

import shared.enrichment as e  # noqa: E402

_fail = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        _fail.append(name)


def main():
    # ---- topic_id ----
    t1 = e.topic_id("parentABC", 3)
    t2 = e.topic_id("parentABC", 3)
    t3 = e.topic_id("parentABC", 4)
    check("topic_id deterministic", t1 == t2 and t1.startswith("topic_"), t1)
    check("topic_id varies by section", t1 != t3)
    check("topic_id empty on missing parent", e.topic_id("", 3) == "")
    check("topic_id empty on missing section", e.topic_id("p", None) == "")

    # ---- section_path ----
    check("section_path joins", e.section_path("A", "B", "C") == "A > B > C")
    check("section_path skips empty", e.section_path("A", "", "C") == "A > C")
    check("section_path all empty", e.section_path("", None, "") == "")

    # ---- chapter extraction ----
    check("chapter arabic", e.extract_chapter("Chapter 1 - Grounding") == ("Chapter 1", "1"))
    check("chapter slash", e.extract_chapter("Chapter 1/2") == ("Chapter 1/2", "1"))
    check("chapter roman", e.extract_chapter("CHAPTER IV — Meters") == ("Chapter IV", "4"))
    check("chapter priority order", e.extract_chapter("", "Chapter 5") == ("Chapter 5", "5"))
    check("chapter none", e.extract_chapter("Grounding basics", "Section overview") == ("", ""))
    check("chapter not from 'subchapter'", e.extract_chapter("subchapter notes") == ("", ""))
    check("chapter word boundary", e.extract_chapter("chapters index") == ("", ""))

    # ---- title coalesce ----
    check("title uses metadata", e.coalesce_title("Overhead Manual", "H", "D-1", "x.pdf") == "Overhead Manual")
    check("title skips junk to heading",
          e.coalesce_title("Microsoft Word - final.docx", "Grounding Guide", "D-1", "x.pdf") == "Grounding Guide")
    check("title falls to doc number",
          e.coalesce_title("", "", "GD-AS-DWM", "x.pdf") == "GD-AS-DWM")
    check("title falls to filename stem",
          e.coalesce_title("", "", "", "CO-CC-GEN_rev2.pdf") == "CO CC GEN rev2")
    check("title never empty", e.coalesce_title(None, None, None, "a/b/doc_x.pdf") == "doc x")

    # ---- ref normalization ----
    check("normalize table", e.normalize_ref("Table 5-2") == "52")
    check("normalize figure", e.normalize_ref("Figure 18.117") == "18117")
    check("normalize plain", e.normalize_ref("5-2") == "52")
    check("normalize empty", e.normalize_ref("") == "")
    check("tables_referenced_normalized dedups",
          e.tables_referenced_normalized(["Table 5-2", "table 5.2", "Table 6"]) == ["52", "6"])

    # ---- page label validation ----
    check("label numeric valid", e.is_valid_page_label("12"))
    check("label roman valid", e.is_valid_page_label("iv"))
    check("label prefixed valid", e.is_valid_page_label("A-3"))
    check("label 5-7 valid", e.is_valid_page_label("5-7"))
    check("label junk invalid", not e.is_valid_page_label("Attachment"))
    check("label date invalid", not e.is_valid_page_label("2019-12-01"))
    check("label empty invalid", not e.is_valid_page_label(""))

    # ---- clamp page ----
    check("clamp within", e.clamp_page(5, 100) == (5, False))
    check("clamp over total", e.clamp_page(150, 100) == (100, True))
    check("clamp under one", e.clamp_page(0, 100) == (1, True))
    check("clamp no total", e.clamp_page(5, 0) == (5, False))
    check("clamp non-int passthrough", e.clamp_page(None, 100) == (None, False))

    # ---- revision boilerplate ----
    check("rev history is boilerplate", e.is_boilerplate_revision("History"))
    check("rev phrase is boilerplate", e.is_boilerplate_revision("Revision History"))
    check("rev real ok", not e.is_boilerplate_revision("B"))
    check("rev real ok 2", not e.is_boilerplate_revision("Rev3"))
    check("rev empty not flagged", not e.is_boilerplate_revision(""))

    print()
    if _fail:
        print(f"FAILED: {_fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
