"""
Unit test for B4 — DI line-level highlight precision.

Proves _line_level_bboxes_for_chunk returns tight per-line boxes for exactly
the chunk's lines (not the whole enclosing paragraph), respects page filtering,
and that the union is tighter than a paragraph-spanning box.

Run:  python tests/test_bbox_precision.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

import shared.page_label as pl  # noqa: E402

_fail = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        _fail.append(name)


def main():
    KEY = "blob://x/precise.pdf"
    # Pre-seed the derived cache so the helper doesn't try to load analysis.
    pl._DERIVED_CACHE[KEY] = {
        "text_bbox_lines": [
            (pl._normalize_text("De-energize the 12 kV feeder"), 1, (1.0, 2.00, 5.0, 0.30)),
            (pl._normalize_text("Apply protective grounds now"), 1, (1.0, 2.40, 5.0, 0.30)),
            (pl._normalize_text("Unrelated appendix line text"), 9, (1.0, 8.00, 5.0, 0.30)),
        ],
    }

    # Chunk contains ONLY the first line's text.
    chunk = "Procedure: De-energize the 12 kV feeder and verify absence of voltage."
    boxes = pl._line_level_bboxes_for_chunk(chunk, KEY, allowed_pages=[1])
    check("only matching line returned", len(boxes) == 1, f"got {len(boxes)}")
    check("box is the tight line box (y=2.0)", boxes and abs(boxes[0]["y_in"] - 2.0) < 1e-6,
          str(boxes))
    check("box height is one line (0.3), not the paragraph",
          boxes and abs(boxes[0]["h_in"] - 0.30) < 1e-6, str(boxes))

    # Chunk with BOTH lines -> two boxes, union spans both but nothing beyond.
    chunk2 = "De-energize the 12 kV feeder. Apply protective grounds now."
    boxes2 = pl._line_level_bboxes_for_chunk(chunk2, KEY, allowed_pages=[1])
    check("both lines matched", len(boxes2) == 2, f"got {len(boxes2)}")
    union = pl._chunk_bboxes_from_line_bboxes(boxes2)
    # union y should be 2.0 .. 2.7 (0.7 tall), NOT a whole-paragraph 4in box.
    check("union hugs the two lines (~0.7in tall)",
          union and abs(union[0]["h_in"] - 0.70) < 1e-6, str(union))

    # Page filter excludes the page-9 appendix line even if text matched.
    boxes3 = pl._line_level_bboxes_for_chunk("Unrelated appendix line text", KEY, allowed_pages=[1])
    check("page filter excludes off-page line", boxes3 == [], str(boxes3))

    # No line data -> empty (caller falls back to paragraph matcher).
    pl._DERIVED_CACHE["blob://x/none.pdf"] = {"text_bbox_lines": []}
    check("empty when no line data", pl._line_level_bboxes_for_chunk("x", "blob://x/none.pdf") == [])

    print()
    if _fail:
        print(f"FAILED: {_fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
