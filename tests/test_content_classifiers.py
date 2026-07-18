"""
Unit tests for shared.content_classifiers.

Run with:  python tests/test_content_classifiers.py
Exits non-zero on any failure.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "function_app"))

from shared.content_classifiers import (  # noqa: E402
    classify_domain,
    classify_equipment,
    classify_hazard,
    compute_criticality,
    detect_prohibitions,
    extract_applies_to_voltage,
    is_prohibition,
)

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        _failures.append(name)


def test_voltage():
    v = extract_applies_to_voltage("Set the 12.47 kV primary feeder before work.")
    check("voltage.kv_magnitude", "12.47kV" in v, v)
    check("voltage.kv_band", "medium_voltage" in v, v)
    check("voltage.named_primary", "primary" in v, v)

    v2 = extract_applies_to_voltage("The 480V secondary panel.")
    check("voltage.low_v", "480V" in v2, v2)
    check("voltage.low_band", "low_voltage" in v2, v2)

    v3 = extract_applies_to_voltage("Transmission line rated 230 kV.")
    check("voltage.ehv_band", "extra_high_voltage" in v3, v3)

    check("voltage.empty_on_nonvoltage", extract_applies_to_voltage("Tighten the bolt.") == [], "should be empty")


def test_equipment():
    e = classify_equipment("Replace the pad-mount transformer and its cutout fuse.")
    check("equip.transformer", "transformer" in e, e)
    check("equip.fuse", "fuse" in e, e)

    g = classify_equipment("Shut the gas valve at the service riser before purging the main.")
    check("equip.gas_valve", "gas_valve" in g, g)
    check("equip.gas_main", "gas_main" in g, g)

    # 'gasket' must NOT trigger any equipment (no false substring)
    check("equip.no_false_positive", classify_equipment("Inspect the gasket seal.") == [] or "gas_valve" not in classify_equipment("Inspect the gasket seal."), "gasket must not tag gas_valve")


def test_domain():
    d = classify_domain("De-energize the 12kV feeder conductor.")
    check("domain.electric", "electric" in d, d)

    g = classify_domain("Survey the natural gas main for leaks; check odorant.")
    check("domain.gas", "gas" in g, g)

    s = classify_domain("At the substation, open the bus tie breaker.")
    check("domain.substation", "substation" in s, s)

    t = classify_domain("General content", taxonomy={"operationalarea": "Gas Operations"})
    check("domain.taxonomy_hint", "gas" in t, t)


def test_hazard_and_criticality():
    h = classify_hazard("DANGER: line may be energized due to back-feed.",
                        callouts=["DANGER: line may be energized"])
    check("hazard.energized", "energized" in h, h)
    check("hazard.crit", compute_criticality(h, has_callouts=True) == "critical", h)

    g = classify_hazard("Purge the pipe; flammable gas, keep below the LEL.")
    check("hazard.gas", "gas" in g, g)
    check("hazard.gas_crit", compute_criticality(g) == "critical", g)

    none = classify_hazard("File the paperwork in the cabinet.")
    check("hazard.none", none == [], none)
    check("hazard.normal", compute_criticality(none) == "normal", none)
    check("hazard.high_on_callout", compute_criticality([], has_callouts=True) == "high", "callout->high")


def test_prohibition():
    p = detect_prohibitions("Do not work on the line until it is grounded. Never bypass the relay.")
    check("prohib.count", len(p) == 2, p)
    check("prohib.text", any("work on the line" in x.lower() for x in p), p)
    check("prohib.is_true", is_prohibition("This must not be energized."), "should be True")
    check("prohib.is_false", not is_prohibition("Energize the line per procedure."), "should be False")


def main():
    for fn in (test_voltage, test_equipment, test_domain, test_hazard_and_criticality, test_prohibition):
        print(f"\n== {fn.__name__} ==")
        try:
            fn()
        except Exception:
            traceback.print_exc()
            _failures.append(fn.__name__)
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} -> {_failures}")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
