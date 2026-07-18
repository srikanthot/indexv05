"""
content_classifiers — domain classifiers + safety detectors for gas &
electric utility field manuals.

Pure functions only. No I/O, no network, no pipeline state. Everything here
is deterministic and unit-tested so it can be called safely from every record
emitter (text / table / table_row / diagram / summary) without changing the
skillset's timing or failure behavior.

What lives here (each is a small, conservative, keyword/regex classifier):

  - extract_applies_to_voltage(text)   -> voltage classes + bands
  - classify_equipment(text, ids, hdr) -> equipment categories (gas + electric)
  - classify_domain(text, hdr, tax)    -> gas / electric / substation / ...
  - classify_hazard(text, callouts)    -> hazard classes (live_line, gas, ...)
  - compute_criticality(...)           -> 'critical' | 'high' | 'normal'
  - detect_prohibitions(text)          -> list of 'do NOT ...' prohibition spans
  - is_prohibition(text)               -> bool

Design rules (safety-critical, "right or say nothing"):
  * Precision over recall for the *routing* tags (voltage/equipment/domain):
    a wrong tag can scope the chatbot INTO the wrong manual. We only emit a
    tag when a clear surface signal is present.
  * Recall over precision for the *hazard* tags: missing a hazard is the
    dangerous direction, so hazard/criticality lean toward flagging.
  * All matching is case-insensitive and word-boundary aware to avoid
    substring false-positives ("gas" inside "gasket" must not tag domain=gas).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Voltage
# ---------------------------------------------------------------------------

# Explicit numeric voltages: "12 kV", "12.47kV", "115 KV", "480 V", "120/240V".
# Captures the numeric magnitude and the unit so we can band it.
_VOLTAGE_NUM_RE = re.compile(
    r"\b(\d{2,3}(?:\.\d{1,2})?)\s*(kV|KV|kv)\b"
    r"|\b(\d{2,4})\s*(V|volts?)\b",
    re.IGNORECASE,
)

# Named voltage tiers used across utility distribution/transmission manuals.
_VOLTAGE_NAMED = {
    "primary": "primary",
    "secondary": "secondary",
    "distribution": "distribution",
    "sub-transmission": "subtransmission",
    "subtransmission": "subtransmission",
    "transmission": "transmission",
    "service voltage": "service",
    "low voltage": "low_voltage",
    "medium voltage": "medium_voltage",
    "high voltage": "high_voltage",
    "extra high voltage": "extra_high_voltage",
}


def _voltage_band_for_kv(kv: float) -> str:
    """IEEE/utility convention bands (kept coarse and defensible)."""
    if kv < 1.0:
        return "low_voltage"
    if kv < 35.0:
        return "medium_voltage"
    if kv < 230.0:
        return "high_voltage"
    return "extra_high_voltage"


def extract_applies_to_voltage(text: str) -> list[str]:
    """Return a sorted, de-duplicated list of voltage tags for the text.

    Tags are a mix of (a) exact normalized magnitudes like '12.47kV' or '480V',
    (b) computed bands like 'medium_voltage', and (c) named tiers like
    'distribution'/'transmission' when spelled out. Empty list when nothing
    clearly voltage-related is present (do NOT guess)."""
    if not text:
        return []
    tags: set[str] = set()
    low = text.lower()

    for m in _VOLTAGE_NUM_RE.finditer(text):
        if m.group(1):  # kV branch
            try:
                kv = float(m.group(1))
            except ValueError:
                continue
            # Normalize display: drop trailing .0
            disp = f"{kv:g}kV"
            tags.add(disp)
            tags.add(_voltage_band_for_kv(kv))
        elif m.group(3):  # V branch
            try:
                v = int(m.group(3))
            except ValueError:
                continue
            # Ignore obviously non-voltage 3-4 digit numbers only when they
            # carry no 'V' unit — but here the unit matched, so keep it.
            tags.add(f"{v}V")
            tags.add(_voltage_band_for_kv(v / 1000.0))

    for phrase, tag in _VOLTAGE_NAMED.items():
        if phrase in low:
            tags.add(tag)

    return sorted(tags)


# ---------------------------------------------------------------------------
# Equipment classification (gas + electric)
# ---------------------------------------------------------------------------

# category -> tuple of surface terms (matched as whole words / phrases).
# Kept conservative: each term is a real utility asset noun. Order does not
# matter; a chunk can carry several equipment classes.
_EQUIPMENT_TERMS: dict[str, tuple[str, ...]] = {
    # --- electric distribution / substation ---
    "transformer": ("transformer", "xfmr", "transformer bank", "padmount transformer",
                    "pole-mount transformer", "distribution transformer", "power transformer"),
    "regulator": ("voltage regulator", "regulator", "ltc", "load tap changer"),
    "recloser": ("recloser", "reclosing relay"),
    "sectionalizer": ("sectionalizer",),
    "capacitor": ("capacitor bank", "capacitor", "cap bank"),
    "circuit_breaker": ("circuit breaker", "breaker", "oil breaker", "vacuum breaker",
                        "sf6 breaker"),
    "switch": ("disconnect switch", "load break switch", "air break switch",
               "switch", "gang switch"),
    "fuse": ("fuse cutout", "cutout", "fuse link", "fuse", "current-limiting fuse"),
    "relay": ("protective relay", "relay"),
    "meter": ("watthour meter", "kwh meter", "electric meter", "meter", "ct meter",
              "metering"),
    "arrester": ("surge arrester", "lightning arrester", "arrester"),
    "conductor": ("conductor", "overhead conductor", "acsr", "primary conductor",
                  "secondary conductor", "bus", "busbar"),
    "cable": ("underground cable", "cable", "urd cable", "feeder cable", "splice",
              "elbow", "termination"),
    "insulator": ("insulator", "bushing", "standoff insulator"),
    "pole": ("utility pole", "pole", "crossarm", "cross-arm", "guy", "anchor",
             "down guy"),
    "instrument_transformer": ("current transformer", "potential transformer",
                               "voltage transformer", " ct ", " pt ", " vt "),
    "switchgear": ("switchgear", "metal-clad switchgear", "pad-mounted gear"),
    "grounding": ("ground grid", "grounding", "ground rod", "static wire",
                  "neutral", "counterpoise"),
    # --- gas ---
    "gas_valve": ("gas valve", "valve", "curb valve", "service valve", "shutoff valve"),
    "gas_regulator": ("gas regulator", "pressure regulator", "district regulator",
                      "farm tap regulator"),
    "gas_meter": ("gas meter", "diaphragm meter", "rotary meter", "turbine meter"),
    "gas_main": ("gas main", "distribution main", "main", "pipeline", "gas pipe"),
    "gas_service": ("service line", "service tee", "service riser", "riser", "tap"),
    "gas_pipe": ("pe pipe", "polyethylene pipe", "steel pipe", "plastic pipe",
                 "coupling", "fitting"),
    "cathodic_protection": ("cathodic protection", "anode", "rectifier", "test station"),
    "compressor": ("compressor", "odorizer", "farm tap"),
}

# Fast pre-filter: only run the (larger) phrase scan when at least one of these
# cheap trigger substrings is present.
_EQUIP_TRIGGER = re.compile(
    r"transformer|regulat|reclos|section|capacit|breaker|switch|fuse|cutout|relay|"
    r"meter|arrest|conductor|\bbus\b|cable|splice|elbow|insulat|bushing|\bpole\b|"
    r"crossarm|cross-arm|\bguy\b|anchor|current transformer|potential transformer|"
    r"switchgear|ground|neutral|valve|pipe|\bmain\b|service|riser|\btap\b|anode|"
    r"rectifier|compressor|odoriz|cathodic",
    re.IGNORECASE,
)


def _word_present(term: str, low_text: str) -> bool:
    """Whole-word/phrase presence test. Terms already padded with spaces
    (like ' ct ') are matched literally; others get word boundaries."""
    if term.startswith(" ") or term.endswith(" "):
        return term in low_text
    return re.search(r"\b" + re.escape(term) + r"\b", low_text) is not None


def classify_equipment(
    text: str,
    equipment_ids: list[str] | None = None,
    headers: list[str] | None = None,
) -> list[str]:
    """Return sorted equipment category tags for the text (+ optional header
    context). This is the real classification the empty `applies_to_equipment`
    was supposed to hold — distinct from the raw `equipment_ids` tag strings.
    Conservative: emits a class only on a clear whole-word asset noun."""
    hay_parts = [text or ""]
    if headers:
        hay_parts.extend(h for h in headers if h)
    hay = " ".join(hay_parts)
    if not hay.strip() or not _EQUIP_TRIGGER.search(hay):
        return []
    low = " " + hay.lower() + " "
    out: set[str] = set()
    for category, terms in _EQUIPMENT_TERMS.items():
        for term in terms:
            if _word_present(term.lower(), low):
                out.add(category)
                break
    return sorted(out)


# ---------------------------------------------------------------------------
# Domain (gas / electric / substation / ...)
# ---------------------------------------------------------------------------

_DOMAIN_SIGNALS: dict[str, re.Pattern] = {
    "gas": re.compile(
        r"\b(natural gas|gas main|gas service|gas meter|gas valve|gas pressure|"
        r"pipeline|odor|odoriz|cathodic|pe pipe|polyethylene|leak survey|"
        r"purg(?:e|ing)|blow(?:ing)? off|mercaptan|psig|cubic feet)\b",
        re.IGNORECASE,
    ),
    "electric": re.compile(
        r"\b(energiz|de-energiz|conductor|transformer|feeder|kv\b|voltage|"
        r"phase|circuit|recloser|capacitor|primary|secondary|overhead|"
        r"underground cable|switchgear|relay|arrester)\b",
        re.IGNORECASE,
    ),
    "substation": re.compile(
        r"\b(substation|switchyard|bus\s?bar|\bbus\b|ltc|load tap changer|"
        r"station battery|control house|breaker bay|transmission line)\b",
        re.IGNORECASE,
    ),
    "metering": re.compile(
        r"\b(watthour|kwh meter|metering|ct ratio|instrument transformer|"
        r"meter socket|ami\b|amr\b)\b",
        re.IGNORECASE,
    ),
}


_PHASE_SINGLE_RE = re.compile(
    r"\b(single[\- ]phase|1[\- ]?phase|1\s?ph\b|1\s?ø|single\s?ph\b)", re.IGNORECASE)
_PHASE_THREE_RE = re.compile(
    r"\b(three[\- ]phase|3[\- ]phase|3\s?ph\b|3\s?ø|poly[\- ]?phase)", re.IGNORECASE)


def classify_phase(text: str, headers: list[str] | None = None) -> list[str]:
    """Return ['single_phase'] and/or ['three_phase'] when the text clearly says
    so, else []. A doc can reference both (a table covering 1Ø and 3Ø)."""
    hay_parts = [text or ""]
    if headers:
        hay_parts.extend(h for h in headers if h)
    hay = " ".join(hay_parts)
    if not hay.strip():
        return []
    out: list[str] = []
    if _PHASE_SINGLE_RE.search(hay):
        out.append("single_phase")
    if _PHASE_THREE_RE.search(hay):
        out.append("three_phase")
    return out


def classify_domain(
    text: str,
    headers: list[str] | None = None,
    taxonomy: dict | None = None,
) -> list[str]:
    """Return sorted domain tags. Prefers an explicit taxonomy hint
    (operationalarea/functionalarea) when present, then falls back to text
    signals. A chunk can be multi-domain (e.g. gas metering)."""
    out: set[str] = set()

    # Taxonomy hint (authoritative when an operator tagged the blob).
    if taxonomy:
        blob = " ".join(
            str(taxonomy.get(k) or "") for k in ("operationalarea", "functionalarea", "doctype")
        ).lower()
        if "gas" in blob:
            out.add("gas")
        if "electric" in blob or "elec" in blob:
            out.add("electric")
        if "substation" in blob:
            out.add("substation")
        if "meter" in blob:
            out.add("metering")

    hay_parts = [text or ""]
    if headers:
        hay_parts.extend(h for h in headers if h)
    hay = " ".join(hay_parts)
    if hay.strip():
        for domain, pat in _DOMAIN_SIGNALS.items():
            if pat.search(hay):
                out.add(domain)

    return sorted(out)


# ---------------------------------------------------------------------------
# Hazard classification + criticality
# ---------------------------------------------------------------------------

_HAZARD_SIGNALS: dict[str, re.Pattern] = {
    "live_line": re.compile(
        r"\b(live[\- ]line|energized line|hot line|barehand|rubber glove|"
        r"minimum approach distance|\bMAD\b|working (?:on|near) energized)\b",
        re.IGNORECASE,
    ),
    "energized": re.compile(
        r"\b(energiz|do not de-energiz|still energized|may be energized|"
        r"back[\- ]?feed|induced voltage)\b",
        re.IGNORECASE,
    ),
    "high_voltage": re.compile(
        r"\b(high voltage|extra high voltage|EHV\b|transmission voltage|"
        r"\d{2,3}\s?kV)\b",
        re.IGNORECASE,
    ),
    "arc_flash": re.compile(
        r"\b(arc[\- ]flash|arc blast|incident energy|flash hazard|"
        r"flash protection boundary|cal/cm)\b",
        re.IGNORECASE,
    ),
    "gas": re.compile(
        r"\b(gas leak|flammable|explos|ignition|purg(?:e|ing)|combustible|"
        r"lower explosive limit|\bLEL\b|blow[\- ]?off|escaping gas)\b",
        re.IGNORECASE,
    ),
    "confined_space": re.compile(
        r"\b(confined space|manhole|vault entry|permit[\- ]required|"
        r"atmospheric test|oxygen deficien)\b",
        re.IGNORECASE,
    ),
    "fall": re.compile(
        r"\b(fall protection|fall arrest|full body harness|climbing|aerial lift|"
        r"working at height|elevated work)\b",
        re.IGNORECASE,
    ),
    "excavation": re.compile(
        r"\b(excavat|trench|shoring|cave[\- ]in|dig[\- ]in|underground facilit|"
        r"call before you dig|one[\- ]call)\b",
        re.IGNORECASE,
    ),
    "traffic": re.compile(
        r"\b(traffic control|work zone|flagger|lane closure|roadway)\b",
        re.IGNORECASE,
    ),
    "lifting": re.compile(
        r"\b(crane|boom|rigging|lifting|hoist|load chart|outrigger)\b",
        re.IGNORECASE,
    ),
    "chemical": re.compile(
        r"\b(pcb|askarel|sf6|sulfur hexafluoride|asbestos|hazardous material|"
        r"\bSDS\b|\bMSDS\b|creosote)\b",
        re.IGNORECASE,
    ),
}


def classify_hazard(
    text: str,
    callouts: list[str] | None = None,
    headers: list[str] | None = None,
) -> list[str]:
    """Return sorted hazard-class tags. Leans toward flagging (missing a
    hazard is the dangerous direction). Callout text (WARNING/DANGER bodies)
    is included in the haystack because that is exactly where hazards are
    named."""
    hay_parts = [text or ""]
    if headers:
        hay_parts.extend(h for h in headers if h)
    if callouts:
        hay_parts.extend(callouts)
    hay = " ".join(hay_parts)
    if not hay.strip():
        return []
    out: set[str] = set()
    for hazard, pat in _HAZARD_SIGNALS.items():
        if pat.search(hay):
            out.add(hazard)
    return sorted(out)


# Hazards that put a life directly at risk if the answer is wrong/incomplete.
_CRITICAL_HAZARDS = frozenset(
    {"live_line", "energized", "high_voltage", "arc_flash", "gas", "confined_space"}
)


def compute_criticality(
    hazard_classes: list[str] | None,
    *,
    has_callouts: bool = False,
    has_prohibition: bool = False,
) -> str:
    """Coarse criticality tier used to decide where the chatbot must apply the
    strict answer-or-abstain gate.

      critical -> a life-threatening hazard class is present
      high     -> a WARNING/DANGER callout or an explicit prohibition, but no
                  life-threatening hazard class matched
      normal   -> everything else
    """
    hazards = set(hazard_classes or [])
    if hazards & _CRITICAL_HAZARDS:
        return "critical"
    if has_callouts or has_prohibition or hazards:
        return "high"
    return "normal"


# ---------------------------------------------------------------------------
# Prohibitions ("do NOT / never")
# ---------------------------------------------------------------------------

# A prohibition is an imperative that forbids an action. Capture the whole
# clause (up to end-of-line / sentence) so the chatbot can surface it verbatim.
_PROHIBITION_RE = re.compile(
    r"\b("
    r"do not|do\s?n[o']t|never|must not|shall not|may not|"
    r"under no circumstances|not permitted|prohibited|forbidden|"
    r"is not allowed|are not allowed|avoid ever"
    r")\b[\s:,\-]*([^\n.;]{0,180})",
    re.IGNORECASE,
)


def detect_prohibitions(text: str) -> list[str]:
    """Return a de-duplicated list of prohibition clauses found in the text,
    each normalized to 'TRIGGER rest-of-clause'. Empty when none."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _PROHIBITION_RE.finditer(text):
        trigger = re.sub(r"\s+", " ", m.group(1).strip())
        tail = re.sub(r"\s+", " ", (m.group(2) or "").strip())
        clause = (f"{trigger} {tail}").strip()
        key = clause.lower()
        if not tail or key in seen:
            # A bare trigger with no object is too weak to surface.
            continue
        seen.add(key)
        out.append(clause[:200])
    return out


def is_prohibition(text: str) -> bool:
    """True when the text contains at least one actionable prohibition."""
    return len(detect_prohibitions(text)) > 0


# ---------------------------------------------------------------------------
# Convenience bundle — one call for every record emitter (text/table/diagram/
# summary) so the applicability + hazard tags stay consistent across types.
# ---------------------------------------------------------------------------

def enrich(
    text: str,
    *,
    headers: list[str] | None = None,
    callouts: list[str] | None = None,
    equipment_ids: list[str] | None = None,
    taxonomy: dict | None = None,
) -> dict:
    """Compute the full applicability + hazard tag bundle for a record.

    Returns a dict with exactly the index-field names, so an emitter can spread
    the relevant keys straight into its record dict."""
    voltage = extract_applies_to_voltage(text)
    equipment = classify_equipment(text, equipment_ids, headers)
    domain = classify_domain(text, headers, taxonomy)
    phase = classify_phase(text, headers)
    prohibitions = detect_prohibitions(text)
    hazard = classify_hazard(text, callouts=callouts, headers=headers)
    criticality = compute_criticality(
        hazard,
        has_callouts=bool(callouts),
        has_prohibition=bool(prohibitions),
    )
    return {
        "applies_to_voltage": voltage,
        "applies_to_equipment": equipment,
        "applies_to_domain": domain,
        "applies_to_phase": phase,
        "hazard_class": hazard,
        "criticality": criticality,
        "is_prohibition": bool(prohibitions),
        "prohibitions": prohibitions,
    }
