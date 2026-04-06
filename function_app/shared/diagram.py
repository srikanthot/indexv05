"""
analyze-diagram

Single-pass vision call. Triages the image, classifies it, and either
returns a rich diagram description or marks it as decorative/unknown
so retrieval can ignore it.
"""

import base64
import hashlib
import json
import re
from typing import Dict, Any

from .ids import (
    SKILL_VERSION,
    diagram_chunk_id,
    parent_id_for,
    safe_int,
    safe_str,
)
from .aoai import get_client, vision_deployment

VALID_CATEGORIES = {
    "circuit_diagram",
    "line_diagram",
    "nameplate",
    "equipment_photo",
    "table_image",
    "decorative",
    "unknown",
}

USEFUL_CATEGORIES = {
    "circuit_diagram",
    "line_diagram",
    "nameplate",
    "equipment_photo",
    "table_image",
}

SYSTEM_PROMPT = """You are a technical-manual diagram analyst.

For the given image, return STRICT JSON with these keys:
  category:        one of [circuit_diagram, line_diagram, nameplate, equipment_photo, table_image, decorative, unknown]
  is_useful:       boolean. true unless category is decorative/unknown.
  figure_ref:      e.g. "Figure 4-2", "Fig. 12", "Table 3.1", or "" if none visible.
  description:     dense, retrieval-friendly description (2-6 sentences).
                   For diagrams, name components, labels, connections, units, and what the
                   diagram is showing. For nameplates/tables, transcribe key fields.

Return ONLY the JSON object. No markdown, no commentary."""


FIGURE_REF_RE = re.compile(r"\b(Figure|Fig\.?|Table)\s*[\-:]?\s*([A-Z0-9][\w\-\.]{0,8})", re.IGNORECASE)


def _image_bytes_and_hash(image_field: Any):
    """
    Azure AI Search passes normalized images as { data: <base64>, width, height, ... }
    """
    if isinstance(image_field, dict):
        b64 = image_field.get("data") or ""
    else:
        b64 = str(image_field or "")
    raw = base64.b64decode(b64) if b64 else b""
    h = hashlib.sha256(raw).hexdigest() if raw else "noimage"
    return b64, h


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _call_vision(image_b64: str, ocr_hint: str) -> Dict[str, Any]:
    client = get_client()
    user_content = [
        {
            "type": "text",
            "text": (
                "Analyze this image from a technical manual page. "
                "Use any OCR hint below as supporting evidence, but rely on what you see.\n\n"
                f"OCR hint:\n{ocr_hint[:1500]}"
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        },
    ]

    resp = client.chat.completions.create(
        model=vision_deployment(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    return _extract_json(resp.choices[0].message.content or "{}")


def process_diagram(data: Dict[str, Any]) -> Dict[str, Any]:
    image_field = data.get("image")
    ocr_text = safe_str(data.get("ocr_text"))
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    physical_pdf_page = safe_int(data.get("physical_pdf_page"), default=None)

    image_b64, image_hash = _image_bytes_and_hash(image_field)

    if not image_b64:
        return {
            "chunk_id": diagram_chunk_id(source_path, source_file, image_hash),
            "parent_id": parent_id_for(source_path, source_file),
            "record_type": "diagram",
            "has_diagram": False,
            "diagram_description": "",
            "diagram_category": "unknown",
            "figure_ref": "",
            "image_hash": image_hash,
            "physical_pdf_page": physical_pdf_page,
            "processing_status": "no_image",
            "skill_version": SKILL_VERSION,
        }

    try:
        result = _call_vision(image_b64, ocr_text)
    except Exception as exc:
        return {
            "chunk_id": diagram_chunk_id(source_path, source_file, image_hash),
            "parent_id": parent_id_for(source_path, source_file),
            "record_type": "diagram",
            "has_diagram": False,
            "diagram_description": "",
            "diagram_category": "unknown",
            "figure_ref": "",
            "image_hash": image_hash,
            "physical_pdf_page": physical_pdf_page,
            "processing_status": f"vision_error:{type(exc).__name__}",
            "skill_version": SKILL_VERSION,
        }

    category = (result.get("category") or "unknown").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "unknown"

    description = safe_str(result.get("description")).strip()
    figure_ref = safe_str(result.get("figure_ref")).strip()

    if not figure_ref:
        m = FIGURE_REF_RE.search(ocr_text or "")
        if m:
            figure_ref = f"{m.group(1).title()} {m.group(2)}"

    has_diagram = bool(result.get("is_useful")) and category in USEFUL_CATEGORIES and bool(description)

    if not has_diagram:
        description = description or ""

    return {
        "chunk_id": diagram_chunk_id(source_path, source_file, image_hash),
        "parent_id": parent_id_for(source_path, source_file),
        "record_type": "diagram",
        "has_diagram": has_diagram,
        "diagram_description": description,
        "diagram_category": category,
        "figure_ref": figure_ref,
        "image_hash": image_hash,
        "physical_pdf_page": physical_pdf_page,
        "processing_status": "ok" if has_diagram else "skipped_decorative",
        "skill_version": SKILL_VERSION,
    }
