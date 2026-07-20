"""
Regression tests for safety-critical extraction fixes:
- voltage classifier catches single-leading-digit kV (2.4 / 4.16 / 7.2 / 8.32)
- procedure step bodies include wrapped lines + sub-steps (not just first line)
- multi-line WARNING/DANGER callouts keep their full actionable text
- NOTE / NOTICE do NOT set the safety_callout signal

Run:  python tests/test_safety_extraction.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

from shared.content_classifiers import extract_applies_to_voltage  # noqa: E402
from shared.procedures import parse_steps  # noqa: E402
from shared.semantic import (  # noqa: E402
    _extract_callouts,
    extract_callout_keywords,
    safety_callout_flag,
)

_fail = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        _fail.append(name)


def main():
    # ---- voltage: single-leading-digit kV distribution classes ----
    for v in ("2.4", "4.16", "4.8", "7.2", "8.32"):
        tags = extract_applies_to_voltage(f"maintenance of {v}kV feeder")
        check(f"voltage {v}kV captured", f"{v}kV" in tags, str(tags))
    check("voltage 12.47kV still works", "12.47kV" in extract_applies_to_voltage("12.47kV primary"))
    check("voltage 480V still works", "480V" in extract_applies_to_voltage("480V service"))

    # ---- procedure step bodies: full multi-line, sub-steps included ----
    proc = ("1. De-energize the transformer.\n"
            "   Verify zero voltage.\n"
            "   a. Lock out the primary.\n"
            "2. Remove the cover.\n")
    steps = dict(parse_steps(proc))
    check("step body includes wrapped line", "Verify zero voltage" in steps.get(1, ""), steps.get(1))
    check("step body includes sub-step", "Lock out the primary" in steps.get(1, ""), steps.get(1))
    check("next step still parsed", steps.get(2, "").startswith("Remove the cover"), steps.get(2))

    # ---- multi-line callout keeps the actionable clause ----
    co = _extract_callouts("WARNING\nDe-energize before servicing.\nVerify with a tester.\n\nStep 1.")
    check("multi-line WARNING captured", co and "De-energize before servicing" in co[0], str(co))

    # ---- NOTE/NOTICE do not set safety_callout; DANGER/WARNING/CAUTION do ----
    check("NOTE not safety", safety_callout_flag(extract_callout_keywords("NOTE: torque to 45")) is False)
    check("NOTICE not safety", safety_callout_flag(["NOTICE: info"]) is False)
    check("DANGER is safety", safety_callout_flag(extract_callout_keywords("DANGER: energized")) is True)
    check("WARNING is safety", safety_callout_flag(["WARNING: gas"]) is True)
    check("CAUTION is safety", safety_callout_flag(["CAUTION: hot"]) is True)

    print()
    if _fail:
        print(f"FAILED: {_fail}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
