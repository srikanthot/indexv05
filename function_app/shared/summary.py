"""
build-doc-summary

One summary record per parent document. Concise (300-500 words).
Used for high-recall, doc-level retrieval and as a routing signal.
"""

from typing import Any

from .aoai import chat_deployment, get_client
from .ids import (
    SKILL_VERSION,
    parent_id_for,
    safe_int,
    safe_str,
    summary_chunk_id,
)
from .text_utils import build_highlight_text

SYSTEM_PROMPT = """You are a technical-manual summarizer.

Given the manual's full text and its top-level section titles, write a
single dense summary (about 300-500 words) that captures:
  - what equipment/system the manual covers
  - the main procedures and chapters
  - critical safety or compliance notes
  - notable diagrams/figures referenced
Do not invent content. Plain prose only, no markdown."""


def _coerce_titles(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


def _coalesce_markdown(value: Any) -> str:
    """
    Accepts either a single markdown string or a list of section markdown
    strings (from /document/markdownDocument/*/content). Falls back to empty.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n\n".join([str(v) for v in value if v])
    return str(value)


def process_doc_summary(data: dict[str, Any]) -> dict[str, Any]:
    source_file = safe_str(data.get("source_file"))
    source_path = safe_str(data.get("source_path"))
    markdown_text = _coalesce_markdown(data.get("markdown_text"))
    primary_text = markdown_text.strip()
    titles = _coerce_titles(data.get("section_titles"))
    pdf_total_pages = safe_int(data.get("pdf_total_pages"), default=None)

    parent_id = parent_id_for(source_path, source_file)
    chunk_id = summary_chunk_id(source_path, source_file)

    # Document-level metadata from the cover page. Imported lazily to
    # avoid pulling in page_label's heavyweight imports at module load.
    from .page_label import cover_metadata_for_pdf
    cover_meta = cover_metadata_for_pdf(source_path)

    if not primary_text:
        return {
            "chunk_id": chunk_id,
            "parent_id": parent_id,
            "record_type": "summary",
            "chunk": "",
            "chunk_for_semantic": f"Source: {source_file}\nSummary unavailable.",
            "highlight_text": "",
            "pdf_total_pages": pdf_total_pages,
            # Summary records cover the whole document; they don't bind to
            # a specific page. Stamp a dedicated value so frontend code that
            # filters/sorts on page_resolution_method has a deterministic
            # signal for these rows ("don't render a page-jump button").
            "page_resolution_method": "document_summary",
            "document_revision": cover_meta["document_revision"],
            "effective_date": cover_meta["effective_date"],
            "document_number": cover_meta["document_number"],
            "processing_status": "no_content",
            "skill_version": SKILL_VERSION,
        }

    titles_block = (
        "Top-level section titles:\n- " + "\n- ".join(titles[:40])
        if titles else "Top-level section titles: (none detected)"
    )
    # gpt-4.1 has a 1M context, but we still cap at 60k chars so a pathological
    # OCR dump cannot blow up the request or cost.
    prompt = (
        f"Source file: {source_file}\n\n"
        f"{titles_block}\n\n"
        f"Manual content (truncated):\n{primary_text[:60000]}"
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
        "highlight_text": build_highlight_text(summary_text),
        "pdf_total_pages": pdf_total_pages,
        # Summary records are doc-level — no specific page. Stamp a
        # dedicated method tag so frontend logic has a deterministic
        # signal across record types.
        "page_resolution_method": "document_summary",
        "document_revision": cover_meta["document_revision"],
        "effective_date": cover_meta["effective_date"],
        "document_number": cover_meta["document_number"],
        "processing_status": status,
        "skill_version": SKILL_VERSION,
    }
