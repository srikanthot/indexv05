"""
Azure Document Intelligence REST client.

Calls the prebuilt-layout model directly so we can access the structured
figures[] and tables[] arrays that the built-in DocumentIntelligenceLayoutSkill
does not surface. Built-in skill remains in the skillset for the markdown
text path; this client is for the figure/table enrichment path.
"""

import os
import time
import logging
from typing import Dict, Any

import httpx

from .config import required_env, optional_env


def _endpoint() -> str:
    return required_env("DI_ENDPOINT").rstrip("/")


def _key() -> str:
    return required_env("DI_API_KEY")


def _api_version() -> str:
    return optional_env("DI_API_VERSION", "2024-11-30")


def analyze_layout(pdf_bytes: bytes, timeout_s: int = 300) -> Dict[str, Any]:
    """
    Submit a PDF to the prebuilt-layout model and poll until the result is ready.
    Returns the full analyzeResult payload (pages, paragraphs, sections,
    figures, tables, ...).
    """
    url = (
        f"{_endpoint()}/documentintelligence/documentModels/prebuilt-layout:analyze"
        f"?api-version={_api_version()}&outputContentFormat=markdown"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": _key(),
        "Content-Type": "application/pdf",
    }

    with httpx.Client(timeout=60.0) as client:
        submit = client.post(url, headers=headers, content=pdf_bytes)
        if submit.status_code not in (200, 202):
            raise RuntimeError(
                f"DI submit failed: {submit.status_code} {submit.text[:500]}"
            )
        op_loc = submit.headers.get("operation-location")
        if not op_loc:
            raise RuntimeError("DI submit missing operation-location header")

        deadline = time.time() + timeout_s
        backoff = 2.0
        while time.time() < deadline:
            poll = client.get(op_loc, headers={"Ocp-Apim-Subscription-Key": _key()})
            if poll.status_code != 200:
                raise RuntimeError(
                    f"DI poll failed: {poll.status_code} {poll.text[:500]}"
                )
            body = poll.json()
            status = body.get("status")
            if status == "succeeded":
                return body.get("analyzeResult", {})
            if status == "failed":
                raise RuntimeError(f"DI analyze failed: {body}")
            time.sleep(backoff)
            backoff = min(backoff * 1.25, 8.0)

        raise TimeoutError("DI analyze timed out")


def fetch_blob_bytes(blob_url: str) -> bytes:
    """
    Fetch a blob over HTTPS. The url passed in by the indexer is
    metadata_storage_path which is the *unauthenticated* blob URL.

    The Function App must have either:
      - the blob's container set to public read, OR
      - a SAS token appended via STORAGE_BLOB_SAS env var, OR
      - storage account key in STORAGE_ACCOUNT_KEY (not used here)

    Simplest path: STORAGE_BLOB_SAS contains a container-level SAS that gets
    appended to every fetch.
    """
    sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
    fetch_url = blob_url
    if sas and "?" not in blob_url:
        fetch_url = f"{blob_url}?{sas}"

    with httpx.Client(timeout=120.0) as client:
        resp = client.get(fetch_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"blob fetch failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.content
