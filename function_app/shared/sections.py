"""
Walk DI's analyzeResult to build a flat section index that maps a page
number to header_1/header_2/header_3 and the section's text content.

DI's "sections" are nested by hierarchy. Each section has elements[]
that reference paragraphs/figures/tables via JSON pointer paths like
"/paragraphs/0". A paragraph's bounding regions tell us which page it
sits on; we use those to compute (start_page, end_page) for each
section, then resolve headers by walking up the section tree.
"""

import re
from typing import Any

PARAGRAPH_REF_RE = re.compile(r"^/paragraphs/(\d+)$")
SECTION_REF_RE = re.compile(r"^/sections/(\d+)$")


def _paragraph_pages(paragraphs: list[dict[str, Any]], idx: int) -> list[int]:
    if idx < 0 or idx >= len(paragraphs):
        return []
    para = paragraphs[idx]
    pages = []
    for br in para.get("boundingRegions", []) or []:
        pn = br.get("pageNumber")
        if isinstance(pn, int):
            pages.append(pn)
    return pages


def _paragraph_role(paragraphs: list[dict[str, Any]], idx: int) -> str:
    if idx < 0 or idx >= len(paragraphs):
        return ""
    return (paragraphs[idx].get("role") or "").lower()


def _paragraph_content(paragraphs: list[dict[str, Any]], idx: int) -> str:
    if idx < 0 or idx >= len(paragraphs):
        return ""
    return paragraphs[idx].get("content") or ""


def build_section_index(analyze_result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Returns a list of section descriptors. Each item:
      {
        "section_idx": int,
        "header_1": str,
        "header_2": str,
        "header_3": str,
        "page_start": int,
        "page_end": int,
        "content": str,        # concatenated paragraph content for this section
      }

    Header inheritance: walking sections in document order, we keep a
    running stack of (level, title). When a sectionHeading is encountered
    inside a section, we update the stack at the appropriate level.
    """
    sections = analyze_result.get("sections", []) or []
    paragraphs = analyze_result.get("paragraphs", []) or []

    # First pass: walk the section tree in DFS order so we can carry
    # header_1/2/3 as we descend.
    visited = [False] * len(sections)
    flat: list[dict[str, Any]] = []

    def walk(section_idx: int, hdr_stack: list[tuple[int, str]]):
        if section_idx < 0 or section_idx >= len(sections) or visited[section_idx]:
            return
        visited[section_idx] = True
        section = sections[section_idx]

        local_stack = list(hdr_stack)
        para_indices: list[int] = []
        child_section_indices: list[int] = []

        for ref in section.get("elements", []) or []:
            m_p = PARAGRAPH_REF_RE.match(ref)
            if m_p:
                para_indices.append(int(m_p.group(1)))
                continue
            m_s = SECTION_REF_RE.match(ref)
            if m_s:
                child_section_indices.append(int(m_s.group(1)))
                continue

        # Update header stack from heading-role paragraphs
        for pidx in para_indices:
            role = _paragraph_role(paragraphs, pidx)
            if role in ("title", "sectionheading"):
                title = _paragraph_content(paragraphs, pidx).strip()
                if not title:
                    continue
                level = 1 if role == "title" else _guess_heading_level(title, local_stack)
                local_stack = [(lvl, txt) for (lvl, txt) in local_stack if lvl < level]
                local_stack.append((level, title))

        h1 = _stack_at(local_stack, 1)
        h2 = _stack_at(local_stack, 2)
        h3 = _stack_at(local_stack, 3)

        page_set = set()
        body_chunks: list[str] = []
        for pidx in para_indices:
            for pn in _paragraph_pages(paragraphs, pidx):
                page_set.add(pn)
            body_chunks.append(_paragraph_content(paragraphs, pidx))

        if page_set:
            flat.append({
                "section_idx": section_idx,
                "header_1": h1,
                "header_2": h2,
                "header_3": h3,
                "page_start": min(page_set),
                "page_end": max(page_set),
                "content": "\n".join([c for c in body_chunks if c]),
            })

        for child_idx in child_section_indices:
            walk(child_idx, local_stack)

    if sections:
        walk(0, [])
        for i in range(len(sections)):
            if not visited[i]:
                walk(i, [])

    return flat


def _guess_heading_level(title: str, stack: list[tuple[int, str]]) -> int:
    """
    Cheap heuristic for level when DI only tells us 'sectionHeading'.
    Numbered prefixes like '1', '1.2', '1.2.3' map to levels 1/2/3.
    Otherwise: deepen one level relative to the current stack top, capped at 3.
    """
    m = re.match(r"^(\d+(?:\.\d+){0,2})\b", title)
    if m:
        return min(3, len(m.group(1).split(".")))
    if stack:
        return min(3, stack[-1][0] + 1)
    return 2


def _stack_at(stack: list[tuple[int, str]], level: int) -> str:
    for lvl, txt in stack:
        if lvl == level:
            return txt
    return ""


def find_section_for_page(
    sections_index: list[dict[str, Any]],
    page_number: int,
) -> dict[str, Any] | None:
    """
    Return the most-specific section whose page range covers page_number.
    Most-specific = smallest page span among matches; ties broken by
    document order (later wins, since deeper sections are visited later).
    """
    matches = [
        s for s in sections_index
        if s["page_start"] <= page_number <= s["page_end"]
    ]
    if not matches:
        return None
    matches.sort(key=lambda s: (s["page_end"] - s["page_start"], -s["section_idx"]))
    return matches[0]


def extract_surrounding_text(
    section_content: str,
    anchor: str,
    chars: int = 200,
) -> str:
    """
    If `anchor` (typically a figure caption) is found in section_content,
    return up to `chars` characters before and after it. Otherwise return
    the first 2*chars characters of the section. Strips simple repeating
    header/footer noise.
    """
    if not section_content:
        return ""
    cleaned = _strip_running_artifacts(section_content)
    anchor_clean = (anchor or "").strip()
    if anchor_clean:
        idx = cleaned.find(anchor_clean)
        if idx >= 0:
            anchor_end = idx + len(anchor_clean)
            start = max(0, idx - chars)
            end = min(len(cleaned), anchor_end + chars)
            before = cleaned[start:idx].strip()
            after = cleaned[anchor_end:end].strip()
            return f"{before} [...] {after}".strip()
    return cleaned[: 2 * chars].strip()


_RUNNING_ARTIFACT_PATTERNS = [
    # "Page 215", "Page 215 of 600"
    re.compile(r"page\s+\d+(\s+of\s+\d+)?", re.IGNORECASE),
    # bare page-number footers ("215", "iv")
    re.compile(r"\d{1,4}"),
    re.compile(r"[ivxlcdm]{1,6}", re.IGNORECASE),
    # revision / version footers commonly stamped on every page of a
    # technical manual: "Rev. 3.2", "Revision 4", "Version 1.0",
    # "Issue 2.1 -- March 2024". These pollute embeddings if not stripped.
    re.compile(r"(rev|revision|version|issue|ver)\.?\s*\d+(\.\d+)*([\s\-–—].*)?", re.IGNORECASE),
    # standalone copyright lines appearing in headers/footers
    re.compile(r"(copyright|\(c\)|©)\s.*", re.IGNORECASE),
    # "Confidential", "Proprietary", "For Internal Use Only" stamps
    re.compile(r"(confidential|proprietary|internal\s+use\s+only|do\s+not\s+distribute)\.?", re.IGNORECASE),
    # document numbers in headers/footers: "GD-AS-ATM-001", "DOC-12345"
    re.compile(r"[A-Z]{2,5}-[A-Z0-9]{2,5}(-[A-Z0-9]{1,5}){0,3}"),
    # date footers: "March 2024", "2024-03-15", "03/15/2024"
    re.compile(r"(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?)\s+\d{4}", re.IGNORECASE),
    re.compile(r"\d{4}[\-/]\d{1,2}[\-/]\d{1,2}"),
    re.compile(r"\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}"),
]


def _strip_running_artifacts(text: str) -> str:
    """Strip lines that look like repeating page headers/footers.

    Designed for technical manuals: revision stamps, document IDs,
    copyright lines, page numbers, and date footers all repeat on
    every page and otherwise pollute the embedding vector by adding
    noise tokens to every chunk.

    Conservative by design: only strips lines that match a pattern
    end-to-end (fullmatch). A line that has artifact text inline with
    real content survives.
    """
    lines = [ln for ln in text.splitlines()]
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if any(p.fullmatch(s) for p in _RUNNING_ARTIFACT_PATTERNS):
            continue
        out.append(s)
    return "\n".join(out)
