"""
build-doc-summary
 
One summary record per parent document. Concise (300-500 words).
Used for high-recall, doc-level retrieval and as a routing signal.
"""
 
import json
from typing import Any
 
from .aoai import chat_deployment, get_client
from .config import index_run_id as _index_run_id
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
 
    # Document-level metadata. Read from input data only -- preanalyze
    # always supplies these as top-level fields on /document (empty
    # string when not extractable). Previous fallback to
    # cover_metadata_for_pdf was burning 14-22 min on PDFs without
    # extractable cover metadata, blowing past the 230s skill timeout.
    cover_meta = {
        "document_revision": safe_str(data.get("document_revision")),
        "effective_date": safe_str(data.get("effective_date")),
        "document_number": safe_str(data.get("document_number")),
    }
 
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
            "content_class": "summary_content",
            "retrieval_eligible_reason": "summary_missing_content",
            "applies_to_equipment": [],
            "applies_to_system": [],
            "applies_to_voltage": [],
            "procedure_id": "",
            "procedure_step_id": "",
            "procedure_step_order": None,
            "procedure_branch_label": "",
            "figure_step_linked": False,
            "figure_linkage_confidence": 0.0,
            "locator_type": "none",
            "locator_value": "",
            "is_locator_artifact": False,
            "artifact_reason_codes": [],
            "retrieval_eligible": False,
            "suggested_for_eval_question": False,
            "processing_status": "no_content",
            "skill_version": SKILL_VERSION,
        }
 
    titles_block = (
        "Top-level section titles:\n- " + "\n- ".join(titles[:40])
        if titles else "Top-level section titles: (none detected)"
    )
    # Cap manual content at 20k chars (was 60k). At 60k chars on
    # markdown-heavy OCR with tables/symbols, gpt-5.1 calls regularly
    # ran 40-90s, eating the entire 60s SDK timeout with no room for
    # the response. 20k chars ≈ 6-8k tokens; calls return in 5-15s
    # consistently. titles_block above gives the model section-level
    # structure to anchor the summary.
    #
    # Strategy: sample beginning + middle + end of the manual so later
    # chapters are represented in the summary. Total ~20k chars.
    text_len = len(primary_text)
    if text_len <= 20000:
        content_sample = primary_text
    else:
        head = primary_text[:8000]
        mid_start = max(8000, text_len // 2 - 3000)
        mid = primary_text[mid_start:mid_start + 6000]
        tail = primary_text[-6000:]
        content_sample = (
            f"{head}\n\n[...middle of document...]\n\n{mid}"
            f"\n\n[...end of document...]\n\n{tail}"
        )
    prompt = (
        f"Source file: {source_file}\n\n"
        f"{titles_block}\n\n"
        f"Manual content (sampled from beginning, middle, end):\n{content_sample}"
    )
 
    # Narrowed exception scope (was bare `except Exception`). The bare
    # form silently emitted an empty summary with status="summary_error:..."
    # for every record — INCLUDING permanent AAD / quota / config errors.
    # The skill returned "success" with empty content, so the indexer
    # never retried; every PDF got indexed with an empty summary and the
    # operator had no signal in indexer telemetry. Now: catch only
    # transient API/network/decode errors; auth and config errors
    # propagate so the skill envelope reports per-record errors that
    # count toward maxFailedItemsPerBatch and surface in the indexer
    # dashboard.
    try:
        from .config import model_gen_kwargs
        client = get_client()
        resp = client.chat.completions.create(
            model=chat_deployment(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            # temperature/token policy centralized -- reasoning-model safe.
            **model_gen_kwargs(2000),
            # Explicit per-call timeout. Without this the SDK can hang for
            # its default 600s on a stuck socket, well past the 230s
            # Azure WebApi skill timeout, leaving the worker tied up.
            timeout=60.0,
        )
        summary_text = (resp.choices[0].message.content or "").strip()
        status = "ok"
    except (TimeoutError, json.JSONDecodeError) as exc:
        summary_text = ""
        status = f"summary_error:{type(exc).__name__}"
    except Exception as exc:
        exc_name = type(exc).__name__
        transient = {
            "APIError", "APIConnectionError", "APITimeoutError",
            "RateLimitError", "InternalServerError", "ServiceUnavailableError",
        }
        try:
            import httpx as _httpx
            is_http = isinstance(exc, _httpx.HTTPError)
        except ImportError:
            is_http = False
        if exc_name in transient or is_http:
            summary_text = ""
            status = f"summary_error:{exc_name}"
        else:
            # Auth / config / unknown — propagate so the skill envelope
            # surfaces the failure to the indexer.
            raise
 
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
        "content_class": "summary_content",
        "retrieval_eligible_reason": (
            "eligible_summary_content" if bool(status == "ok") else "summary_generation_failed"
        ),
        "applies_to_equipment": [],
        "applies_to_system": [],
        "applies_to_voltage": [],
        "procedure_id": "",
        "procedure_step_id": "",
        "procedure_step_order": None,
        "procedure_branch_label": "",
        "figure_step_linked": False,
        "figure_linkage_confidence": 0.0,
        "locator_type": "none",
        "locator_value": "",
        "is_locator_artifact": False,
        "artifact_reason_codes": [],
        "retrieval_eligible": bool(status == "ok"),
        "suggested_for_eval_question": bool(status == "ok"),
        "processing_status": status,
        "skill_version": SKILL_VERSION,
        "index_run_id": _index_run_id(),
    }
 