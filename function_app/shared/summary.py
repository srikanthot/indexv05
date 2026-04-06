"""
build-doc-summary

One summary record per parent document. Concise (300-500 words).
Used for high-recall, doc-level retrieval and as a routing signal.
"""

from typing import Dict, Any, List

from .ids import (
    SKILL_VERSION,
    summary_chunk_id,
    parent_id_for,
    safe_str,
)
from .aoai import get_client, chat_deployment


SYSTEM_PROMPT = """You are a technical-manual summarizer.

Given the manual's full text and its top-level section titles, write a
single dense summary (about 300-500 words) that captures:
  - what equipment/system the manual covers
  - the main procedures and chapters
  - critical safety or compliance notes
  - notable diagrams/figures referenced
Do not invent content. Plain prose only, no markdown."""


def _coerce_titles(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


def process_doc_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    merged_text = safe_str(data.get("merged_text"))
    titles = _coerce_titles(data.get("section_titles"))

    parent_id = parent_id_for(source_path, source_file)
    chunk_id = summary_chunk_id(source_path, source_file)

    if not merged_text.strip():
        return {
            "chunk_id": chunk_id,
            "parent_id": parent_id,
            "record_type": "summary",
            "chunk": "",
            "chunk_for_semantic": f"Source: {source_file}\nSummary unavailable.",
            "processing_status": "no_content",
            "skill_version": SKILL_VERSION,
        }

    prompt = (
        f"Source file: {source_file}\n\n"
        f"Top-level section titles:\n- " + "\n- ".join(titles[:40]) + "\n\n"
        f"Manual content (truncated):\n{merged_text[:18000]}"
    )

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=chat_deployment(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=900,
        )
        summary_text = (resp.choices[0].message.content or "").strip()
        status = "ok"
    except Exception as exc:
        summary_text = ""
        status = f"summary_error:{type(exc).__name__}"

    semantic = (
        f"Source: {source_file}\n"
        f"Document summary:\n{summary_text}"
    )

    return {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "record_type": "summary",
        "chunk": summary_text,
        "chunk_for_semantic": semantic,
        "processing_status": status,
        "skill_version": SKILL_VERSION,
    }
