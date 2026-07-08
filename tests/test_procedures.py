"""
Unit tests for shared.procedures — section-based procedure grouping.

The critical property (your multi-page scenario): every chunk of a procedure
section shares ONE procedure_id, including a continuation/warning chunk that has
no step numbers of its own, and every chunk carries the TOTAL step count so the
chatbot can detect a missing chunk.

Run:  python tests/test_procedures.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

from shared.procedures import parse_procedure  # noqa: E402

_fail = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        _fail.append(name)


# A 6-step transformer procedure that (in the real pipeline) SplitSkill breaks
# into several chunks across pages 2-5.
SECTION = """Maintenance steps for the 13 kV transformer

1. De-energize and verify absence of voltage.
2. Apply personal protective grounds to all phases.
3. Drain and sample the insulating oil.
WARNING: Hot oil can cause severe burns. Allow to cool.
4. Inspect bushings and replace worn gaskets.
5. Refill with tested oil to the correct level.
6. Remove grounds and return to service per switching order.
"""

H = ["Chapter 7", "Transformer Maintenance", "Maintenance steps for the 13 kV transformer"]
SP, SF = "blob://x/elecT&D.pdf", "elecT&D.pdf"


def main():
    # Chunk A: the head of the procedure (steps 1-3)
    chunk_a = "1. De-energize and verify absence of voltage.\n2. Apply personal protective grounds to all phases.\n3. Drain and sample the insulating oil."
    a = parse_procedure(page_text=chunk_a, section_content=SECTION, headers=H, source_path=SP, source_file=SF)

    # Chunk B: a CONTINUATION / warning chunk with NO step numbers of its own
    chunk_b = "WARNING: Hot oil can cause severe burns. Allow to cool before proceeding with the next steps."
    b = parse_procedure(page_text=chunk_b, section_content=SECTION, headers=H, source_path=SP, source_file=SF)

    # Chunk C: the tail (steps 5-6)
    chunk_c = "5. Refill with tested oil to the correct level.\n6. Remove grounds and return to service per switching order."
    c = parse_procedure(page_text=chunk_c, section_content=SECTION, headers=H, source_path=SP, source_file=SF)

    check("head chunk gets a procedure_id", bool(a["procedure_id"]), str(a))
    check("continuation/warning chunk ALSO gets a procedure_id (not orphaned)",
          bool(b["procedure_id"]), str(b))
    check("all three chunks share the SAME procedure_id",
          a["procedure_id"] == b["procedure_id"] == c["procedure_id"],
          f"{a['procedure_id']} / {b['procedure_id']} / {c['procedure_id']}")

    check("total step count = 6 on every chunk (completeness signal)",
          a["procedure_step_count"] == 6 and b["procedure_step_count"] == 6 and c["procedure_step_count"] == 6,
          f"{a['procedure_step_count']}/{b['procedure_step_count']}/{c['procedure_step_count']}")

    check("head chunk step_order = 1", a["procedure_step_order"] == 1, str(a["procedure_step_order"]))
    check("continuation chunk step_order is None (no own steps) but still grouped",
          b["procedure_step_order"] is None and bool(b["procedure_id"]), str(b))
    check("tail chunk step_order = 5", c["procedure_step_order"] == 5, str(c["procedure_step_order"]))

    # A non-procedure section -> nothing bound.
    prose = "This chapter describes the theory of transformer cooling in general terms."
    n = parse_procedure(page_text=prose, section_content=prose, headers=["X"], source_path=SP, source_file=SF)
    check("non-procedure section -> empty", n["procedure_id"] == "", str(n))

    print()
    if _fail:
        print(f"FAILED: {_fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
