"""
analyze-diagram (per-figure)

Inputs come from process-document's enriched_figures:
  - image_b64 (already cropped to the figure region)
  - figure_id, page_number, caption, bbox
  - header_1/2/3, surrounding_context
  - source_file, source_path, parent_id

The vision prompt is enriched with section path, page, caption, surrounding
text, and any figure refs we can recover from the surrounding text.

Hash cache: before calling vision, we look up an existing index document
with the same parent_id + image_hash. On a hit we copy the previous
description and skip the vision call entirely.
"""

import base64
import hashlib
import json
import re
from typing import Dict, Any

from .ids import (
    SKILL_VERSION,
    diagram_chunk_id,
    safe_int,
    safe_str,
)
from .aoai import get_client, vision_deployment
from .search_cache import lookup_existing_by_hash


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
    # table_image intentionally excluded: tables go through the table pipeline
}

SYSTEM_PROMPT = """You are a technical-manual diagram analyst.

Return STRICT JSON with these keys:
  category:    one of [circuit_diagram, line_diagram, nameplate, equipment_photo, decorative, unknown]
  is_useful:   boolean. true unless category is decorative/unknown.
  figure_ref:  e.g. "Figure 4-2", "Fig. 12", or "" if none visible.
  description: dense, retrieval-friendly description (3-8 sentences).
               For diagrams, name components, labels, connections, units, and
               what the diagram is showing. For nameplates, transcribe key
               fields. If any text or value is unclear, say so explicitly.
               Do not guess.

Return ONLY the JSON object. No markdown, no commentary."""


FIGURE_REF_RE = re.compile(r"\b(Figure|Fig\.?)\s*[\-:]?\s*([A-Z0-9][\w\-\.]{0,8})", re.IGNORECASE)


def _image_hash(image_b64: str) -> str:
    if not image_b64:
        return "noimage"
    try:
        raw = base64.b64decode(image_b64)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return hashlib.sha256(image_b64.encode("utf-8")).hexdigest()


def _build_user_text(data: Dict[str, Any]) -> str:
    source_file = safe_str(data.get("source_file"))
    h1 = safe_str(data.get("header_1"))
    h2 = safe_str(data.get("header_2"))
    h3 = safe_str(data.get("header_3"))
    header_path = " > ".join([h for h in [h1, h2, h3] if h])
    page = safe_str(data.get("page_number"))
    caption = safe_str(data.get("caption"))
    surrounding = safe_str(data.get("surrounding_context"))

    refs = ", ".join(
        sorted(set(f"{m.group(1).title()} {m.group(2)}" for m in FIGURE_REF_RE.finditer(surrounding)))
    )

    return (
        f"You are analyzing a figure from technical manual \"{source_file}\".\n"
        f"Section: {header_path or '(unknown)'}\n"
        f"Page: {page or '(unknown)'}\n"
        f"Caption (from layout): {caption or '(none)'}\n"
        f"Body text references this figure as: {refs or '(none)'}\n"
        f"Surrounding text: \"{surrounding[:1500]}\"\n\n"
        f"If this is a technical diagram, describe it in full detail.\n"
        f"If any text/value is unclear, say so explicitly. Do not guess.\n"
        f"If decorative/logo/photo, return category=decorative and is_useful=false."
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _call_vision(image_b64: str, user_text: str) -> Dict[str, Any]:
    client = get_client()
    user_content = [
        {"type": "text", "text": user_text},
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
        max_tokens=700,
        response_format={"type": "json_object"},
    )
    return _extract_json(resp.choices[0].message.content or "{}")


def process_diagram(data: Dict[str, Any]) -> Dict[str, Any]:
    image_b64 = safe_str(data.get("image_b64"))
    figure_id = safe_str(data.get("figure_id"))
    page_number = safe_int(data.get("page_number"), default=None)
    caption = safe_str(data.get("caption"))
    h1 = safe_str(data.get("header_1"))
    h2 = safe_str(data.get("header_2"))
    h3 = safe_str(data.get("header_3"))
    surrounding = safe_str(data.get("surrounding_context"))
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    parent_id = safe_str(data.get("parent_id"))
    bbox = data.get("bbox") or {}
    bbox_json = json.dumps(bbox, separators=(",", ":")) if bbox else ""

    img_hash = _image_hash(image_b64)
    chunk_id = diagram_chunk_id(source_path, source_file, img_hash)

    base_record = {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "record_type": "diagram",
        "figure_id": figure_id,
        "figure_bbox": bbox_json,
        "image_hash": img_hash,
        "physical_pdf_page": page_number,
        "physical_pdf_page_end": page_number,
        "header_1": h1,
        "header_2": h2,
        "header_3": h3,
        "surrounding_context": surrounding,
        "skill_version": SKILL_VERSION,
    }

    if not image_b64:
        return {
            **base_record,
            "has_diagram": False,
            "diagram_description": "",
            "diagram_category": "unknown",
            "figure_ref": "",
            "processing_status": "no_image",
        }

    cached = lookup_existing_by_hash(parent_id, img_hash)
    if cached:
        return {
            **base_record,
            "has_diagram": bool(cached.get("has_diagram")),
            "diagram_description": safe_str(cached.get("diagram_description")),
            "diagram_category": safe_str(cached.get("diagram_category"), "unknown"),
            "figure_ref": safe_str(cached.get("figure_ref")),
            "processing_status": "cache_hit",
        }

    try:
        result = _call_vision(image_b64, _build_user_text(data))
    except Exception as exc:
        return {
            **base_record,
            "has_diagram": False,
            "diagram_description": "",
            "diagram_category": "unknown",
            "figure_ref": "",
            "processing_status": f"vision_error:{type(exc).__name__}",
        }

    category = (result.get("category") or "unknown").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "unknown"

    description = safe_str(result.get("description")).strip()
    figure_ref = safe_str(result.get("figure_ref")).strip()
    if not figure_ref:
        m = FIGURE_REF_RE.search(caption or surrounding or "")
        if m:
            figure_ref = f"{m.group(1).title()} {m.group(2)}"

    has_diagram = (
        bool(result.get("is_useful"))
        and category in USEFUL_CATEGORIES
        and bool(description)
    )

    return {
        **base_record,
        "has_diagram": has_diagram,
        "diagram_description": description,
        "diagram_category": category,
        "figure_ref": figure_ref,
        "processing_status": "ok" if has_diagram else "skipped_decorative",
    }
