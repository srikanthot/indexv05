"""
build-semantic-string

Builds a single, real string for chunk_for_semantic. Two modes:
  - mode='text'    : assemble from headers + figure_ref + page label + chunk
  - mode='diagram' : assemble from figure_ref + category + description + ocr hint
"""

from typing import Dict, Any, List

from .ids import safe_str


def _join_nonempty(parts: List[str], sep: str) -> str:
    return sep.join([p for p in parts if p])


def _build_text_string(data: Dict[str, Any]) -> str:
    source_file = safe_str(data.get("source_file"))
    h1 = safe_str(data.get("header_1"))
    h2 = safe_str(data.get("header_2"))
    h3 = safe_str(data.get("header_3"))
    chunk = safe_str(data.get("chunk"))
    page_label = safe_str(data.get("printed_page_label"))

    header_path = _join_nonempty([h1, h2, h3], " > ")

    header_line = f"Section: {header_path}" if header_path else ""
    page_line = f"Page: {page_label}" if page_label else ""
    source_line = f"Source: {source_file}" if source_file else ""

    return _join_nonempty(
        [source_line, header_line, page_line, chunk.strip()],
        "\n",
    )


def _build_diagram_string(data: Dict[str, Any]) -> str:
    source_file = safe_str(data.get("source_file"))
    description = safe_str(data.get("diagram_description"))
    category = safe_str(data.get("diagram_category"))
    figure_ref = safe_str(data.get("figure_ref"))
    ocr = safe_str(data.get("ocr_text"))
    page = safe_str(data.get("physical_pdf_page"))

    head_bits = []
    if figure_ref:
        head_bits.append(figure_ref)
    if category:
        head_bits.append(f"({category})")
    head = " ".join(head_bits)

    source_line = f"Source: {source_file}" if source_file else ""
    page_line = f"Page: {page}" if page else ""
    head_line = f"Diagram: {head}" if head else "Diagram"
    desc_line = description.strip()
    ocr_line = f"Visible text: {ocr.strip()[:600]}" if ocr.strip() else ""

    return _join_nonempty(
        [source_line, page_line, head_line, desc_line, ocr_line],
        "\n",
    )


def process_semantic_string(data: Dict[str, Any]) -> Dict[str, Any]:
    mode = safe_str(data.get("mode"), "text").lower()
    if mode == "diagram":
        return {"chunk_for_semantic": _build_diagram_string(data)}
    return {"chunk_for_semantic": _build_text_string(data)}
