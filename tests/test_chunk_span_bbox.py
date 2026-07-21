"""
Unit test for the continuous whole-chunk highlight geometry
(_span_union_from_lines in page_label). Proves it fills the gaps the per-line
matcher leaves: an interior line whose OCR text did NOT match the chunk is still
covered by the union rectangle, so the frontend never shows "some lines
highlight, some don't".

Run:  python tests/test_chunk_span_bbox.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

import shared.page_label as pl  # noqa: E402

_fail = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        _fail.append(name)


def main():
    # Page 5 has THREE body lines; the matcher only matched line 1 (y=1.0) and
    # line 3 (y=2.0). Line 2 (y=1.5, wider) did NOT text-match -> the old
    # per-line highlight would skip it. The span union must still cover it.
    matched = [
        {"page": 5, "x_in": 1.0, "y_in": 1.0, "w_in": 3.0, "h_in": 0.2},
        {"page": 5, "x_in": 1.0, "y_in": 2.0, "w_in": 3.0, "h_in": 0.2},
    ]
    all_lines = [
        ("line one", 5, (1.0, 1.0, 3.0, 0.2)),
        ("line two did not match", 5, (1.0, 1.5, 4.0, 0.2)),  # wider, unmatched
        ("line three", 5, (1.0, 2.0, 3.0, 0.2)),
    ]
    out = pl._span_union_from_lines(matched, all_lines)
    check("one rectangle for the page", len(out) == 1, str(out))
    r = out[0] if out else {}
    check("rectangle is on page 5", r.get("page") == 5, str(r))
    # width must reach the UNMATCHED line 2's right edge (1.0 + 4.0 = 5.0),
    # proving the gap line is covered — old per-line boxes maxed at 4.0 wide.
    check("covers unmatched interior line (width>=4.0)",
          round(r.get("w_in", 0), 2) >= 4.0, str(r))
    # vertical span covers line1 top (1.0) through line3 bottom (2.2)
    check("y starts at first line", abs(r.get("y_in", 0) - 1.0) < 0.01, str(r))
    check("height spans all lines (~1.2)", abs(r.get("h_in", 0) - 1.2) < 0.02, str(r))

    # multi-page: matched lines on pages 5 and 6 -> one rect each
    multi = [
        {"page": 5, "x_in": 1.0, "y_in": 1.0, "w_in": 3.0, "h_in": 0.2},
        {"page": 6, "x_in": 1.0, "y_in": 0.5, "w_in": 3.0, "h_in": 0.2},
    ]
    all2 = [
        ("a", 5, (1.0, 1.0, 3.0, 0.2)),
        ("b", 6, (1.0, 0.5, 3.0, 0.2)),
    ]
    out2 = pl._span_union_from_lines(multi, all2)
    check("multi-page -> one rect per page", {x["page"] for x in out2} == {5, 6}, str(out2))

    # empty input -> empty
    check("empty input -> []", pl._span_union_from_lines([], []) == [])

    # no body-line data for the page -> falls back to matched union (no crash)
    fb = pl._span_union_from_lines(
        [{"page": 9, "x_in": 2.0, "y_in": 3.0, "w_in": 1.0, "h_in": 0.2}], [])
    check("fallback to matched union when no line data",
          len(fb) == 1 and fb[0]["page"] == 9, str(fb))

    print()
    if _fail:
        print(f"FAILED: {_fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
