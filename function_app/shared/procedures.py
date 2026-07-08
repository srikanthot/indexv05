"""
procedures — structure-aware procedure/step parsing + warning binding for
safety-manual text chunks. Pure functions (no I/O).

Why this exists (audit findings A1 + A2):
  * The size-based SplitSkill can cut a numbered procedure mid-step, and the
    procedure_* index fields were always emitted empty, so a chatbot had no way
    to return a *complete, ordered* procedure — it paraphrased fragments.
  * A WARNING/DANGER at the bottom of one chunk governing steps that continue
    into the next chunk was lost: the next chunk shipped with no callout.

How this fixes it, within the existing per-chunk skill (no architecture change):
  * The extract-page-label skill receives BOTH the chunk (`page_text`) and the
    whole `section_content`. We parse steps from the chunk to populate the
    procedure model, and derive a stable `procedure_id` from the section so
    every chunk of one procedure shares a key — the chatbot reassembles the
    full procedure with a single `$filter procedure_id eq '…'` + sort by
    `procedure_step_order` (the small-to-big / whole-unit pattern, same idea
    the table_cluster_id already uses for tables).
  * `governing_callouts` is computed over the WHOLE section, so a step chunk
    inherits the warning even when the warning sits in a sibling chunk of the
    same section. Recall-over-precision on warnings is the safe direction.
"""

from __future__ import annotations

import re

from .ids import _short_hash, parent_id_for

# A numbered step marker at line start: "1. ", "2) ", "3:", "Step 4 -".
# Requires a space + non-space body so we don't match bare "1." in prose.
_STEP_RE = re.compile(
    r"(?m)^[ \t]*(?:step[ \t]+)?(\d{1,3})[.):\-]\s+(\S[^\n]*)",
    re.IGNORECASE,
)

# Conditional / branch phrasing that changes which step applies.
_BRANCH_RE = re.compile(
    r"\b(if|when|unless|in (?:the )?(?:event|case) of|should)\b[^\n.,;:]{0,80}",
    re.IGNORECASE,
)


def parse_steps(text: str) -> list[tuple[int, str]]:
    """Return [(step_order, verbatim_step_text), ...] found at line starts.
    Empty when the text has no numbered-step structure."""
    if not text:
        return []
    steps: list[tuple[int, str]] = []
    for m in _STEP_RE.finditer(text):
        try:
            order = int(m.group(1))
        except (TypeError, ValueError):
            continue
        body = re.sub(r"[ \t]+", " ", m.group(2).strip())
        if body:
            steps.append((order, body))
    return steps


def looks_like_procedure(steps: list[tuple[int, str]]) -> bool:
    """A chunk is a procedure chunk when it has >=2 numbered steps whose
    numbers are mostly increasing (guards against numbered *lists* that are
    not sequential procedures, e.g. a definition list that reuses '1.')."""
    if len(steps) < 2:
        return False
    orders = [o for o, _ in steps]
    increases = sum(1 for a, b in zip(orders, orders[1:]) if b > a)
    # allow a single non-increase (sub-list restart) but require net order.
    return increases >= len(orders) - 2


def _deepest_header(headers: list[str] | None) -> str:
    if not headers:
        return ""
    for h in reversed(headers):
        if h and h.strip():
            return h.strip()
    return ""


def parse_procedure(
    *,
    page_text: str,
    section_content: str | None,
    headers: list[str] | None,
    source_path: str,
    source_file: str,
) -> dict:
    """Populate the procedure model for a chunk.

    KEY DESIGN (fixes "a continuation chunk gets orphaned"):
      The decision "is this a procedure?" is made from the WHOLE SECTION
      (`section_content`), not from the individual chunk. If the section is a
      procedure, EVERY chunk of that section is bound to the same
      `procedure_id` — including a continuation chunk or a standalone WARNING
      box that has no visible step numbers of its own. That way the chatbot's
      "give me the whole procedure" expansion (`$filter procedure_id eq '…'`)
      never silently drops a middle chunk of a 2-5 page procedure.

    Completeness signal: `procedure_step_count` is the TOTAL number of steps in
    the whole procedure (section-level, identical on every chunk). The chatbot
    compares the step numbers it actually retrieved against this total to KNOW
    whether any chunk is missing (e.g. it has steps 1,2,3,5 but count says 6 →
    a chunk is missing → don't answer as if complete).

    procedure_id is stable across chunks because it derives from the parent
    document + the section's deepest header (one procedure per heading — the
    normal manual convention).
    """
    empty = {
        "procedure_id": "",
        "procedure_title": "",
        "procedure_step_id": "",
        "procedure_step_order": None,
        "procedure_step_text": "",
        "procedure_step_count": 0,
        "procedure_branch_label": "",
    }
    # Decide procedure-ness from the whole section (fall back to the chunk when
    # section text isn't available).
    section_steps = parse_steps(section_content or page_text)
    if not looks_like_procedure(section_steps):
        return empty

    title = _deepest_header(headers)
    seed = f"{parent_id_for(source_path, source_file)}|{title.lower()}|proc"
    procedure_id = "proc_" + _short_hash(seed, 12)

    # This chunk's own steps (may be empty for a continuation / warning chunk —
    # it still gets the procedure_id so it stays with the group).
    chunk_steps = parse_steps(page_text)
    orders = [o for o, _ in chunk_steps]
    first = min(orders) if orders else None

    branch = ""
    bm = _BRANCH_RE.search(page_text or "")
    if bm:
        branch = re.sub(r"\s+", " ", bm.group(0).strip())[:80]

    step_text = "\n".join(f"{o}. {t}" for o, t in chunk_steps)

    return {
        "procedure_id": procedure_id,
        "procedure_title": title[:200],
        # step id anchors this chunk's slice within the procedure so the
        # chatbot can dedupe overlapping SplitSkill windows.
        "procedure_step_id": f"{procedure_id}_s{first}" if first is not None else f"{procedure_id}_cont",
        "procedure_step_order": first,
        "procedure_step_text": step_text[:8000],
        # TOTAL steps in the whole procedure (completeness signal) — same on
        # every chunk of the procedure, so the chatbot can detect gaps.
        "procedure_step_count": len(section_steps),
        "procedure_branch_label": branch,
    }
