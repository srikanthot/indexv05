"""
Azure Document Intelligence REST client.

Calls the prebuilt-layout model directly so we can access the structured
figures[] and tables[] arrays that the built-in DocumentIntelligenceLayoutSkill
does not surface. Built-in skill remains in the skillset for the markdown
text path; this client is for the figure/table enrichment path.

Pre-analysis cache: if a .di.json file exists alongside the PDF in blob
storage, we read that instead of calling DI live. This removes the 230-second
Azure Search WebApi skill timeout constraint for large PDFs. Run
scripts/preanalyze.py before the indexer to populate the cache.
"""

import json
import logging
import time
from typing import Any

import httpx

from .config import optional_env, required_env
from .credentials import (
    DI_SCOPE,
    STORAGE_SCOPE,
    bearer_token,
    use_managed_identity,
)


def _endpoint() -> str:
    return required_env("DI_ENDPOINT").rstrip("/")


def _api_version() -> str:
    return optional_env("DI_API_VERSION", "2024-11-30")


def _auth_headers() -> dict[str, str]:
    """
    Header set for DI requests. MI path uses a bearer token; key path uses
    Ocp-Apim-Subscription-Key. DI supports both natively.
    """
    if use_managed_identity():
        return {"Authorization": f"Bearer {bearer_token(DI_SCOPE)}"}
    return {"Ocp-Apim-Subscription-Key": required_env("DI_API_KEY")}


def analyze_layout(pdf_bytes: bytes, timeout_s: int = 210) -> dict[str, Any]:
    """
    Submit a PDF to the prebuilt-layout model and poll until the result is ready.
    Returns the full analyzeResult payload (pages, paragraphs, sections,
    figures, tables, ...).
    """
    url = (
        f"{_endpoint()}/documentintelligence/documentModels/prebuilt-layout:analyze"
        f"?api-version={_api_version()}&outputContentFormat=markdown"
    )
    auth = _auth_headers()
    headers = {**auth, "Content-Type": "application/pdf"}

    with httpx.Client(timeout=180.0) as client:
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
            # Re-fetch the auth header each poll so MI token refreshes are picked up.
            poll = client.get(op_loc, headers=_auth_headers())
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
    metadata_storage_path — the unauthenticated blob URL.

    Auth priority:
      1. Managed identity (production). Requires the Function App's MI to
         have the 'Storage Blob Data Reader' role on the account.
      2. SAS token in STORAGE_BLOB_SAS (useful for local dev when MI is
         not available).
      3. Bare URL (blob must be public-read).
    """
    headers: dict[str, str] = {}
    fetch_url = blob_url

    if use_managed_identity():
        headers["Authorization"] = f"Bearer {bearer_token(STORAGE_SCOPE)}"
        headers["x-ms-version"] = "2023-11-03"
    else:
        sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
        if sas and "?" not in blob_url:
            fetch_url = f"{blob_url}?{sas}"

    with httpx.Client(timeout=180.0) as client:
        resp = client.get(fetch_url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"blob fetch failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.content


def fetch_cached_analysis(blob_url: str) -> dict[str, Any] | None:
    """
    Check if a pre-analyzed DI result exists in the _dicache/ subfolder.
    The cache blob path is: <container>/_dicache/<filename>.pdf.di.json

    Returns {"analyzeResult": {...}} on hit, or None on miss/error.
    Crops are stored as separate per-figure blobs and fetched on demand
    via fetch_cached_crop(). This keeps memory usage low even for PDFs
    with thousands of figures.

    Run scripts/preanalyze.py to populate the cache for large PDFs.
    """
    # blob_url is like https://account.blob.../container/file.pdf
    # Cache URL is    https://account.blob.../container/_dicache/file.pdf.di.json
    last_slash = blob_url.rfind("/")
    if last_slash < 0:
        return None
    base = blob_url[:last_slash]
    filename = blob_url[last_slash + 1:]
    cache_url = f"{base}/_dicache/{filename}.di.json"
    headers: dict[str, str] = {}
    fetch_url = cache_url

    if use_managed_identity():
        headers["Authorization"] = f"Bearer {bearer_token(STORAGE_SCOPE)}"
        headers["x-ms-version"] = "2023-11-03"
    else:
        sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
        if sas and "?" not in cache_url:
            fetch_url = f"{cache_url}?{sas}"

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.get(fetch_url, headers=headers)
            if resp.status_code == 200:
                logging.info("DI cache hit: %s", cache_url)
                data = json.loads(resp.content)
                # Support both new format (bare analyzeResult) and old
                # v2 wrapper format ({"analyzeResult": ..., "crops": ...})
                if "analyzeResult" in data:
                    return {"analyzeResult": data["analyzeResult"]}
                # Bare analyzeResult dict (new format or legacy v1)
                return {"analyzeResult": data}
            return None
    except Exception:
        logging.debug("DI cache miss or error for %s", cache_url)
        return None


def fetch_cached_crop(blob_url: str, figure_id: str) -> dict[str, Any] | None:
    """
    Fetch a single pre-cropped figure from the _dicache/ subfolder.
    Blob path: <container>/_dicache/<filename>.pdf.crop.<figure_id>.json

    Returns {"image_b64": "...", "bbox": {...}} on hit, or None on miss.
    """
    last_slash = blob_url.rfind("/")
    if last_slash < 0:
        return None
    base = blob_url[:last_slash]
    filename = blob_url[last_slash + 1:]
    crop_url = f"{base}/_dicache/{filename}.crop.{figure_id}.json"
    headers: dict[str, str] = {}
    fetch_url = crop_url

    if use_managed_identity():
        headers["Authorization"] = f"Bearer {bearer_token(STORAGE_SCOPE)}"
        headers["x-ms-version"] = "2023-11-03"
    else:
        sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
        if sas and "?" not in crop_url:
            fetch_url = f"{crop_url}?{sas}"

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(fetch_url, headers=headers)
            if resp.status_code == 200:
                return json.loads(resp.content)
            return None
    except Exception:
        logging.debug("crop cache miss for %s/%s", filename, figure_id)
        return None


def fetch_precomputed_output(blob_url: str) -> dict[str, Any] | None:
    """
    Check if a pre-computed process-document output exists.
    Blob path: <container>/_dicache/<filename>.pdf.output.json

    Returns the full output dict on hit, or None on miss.
    """
    last_slash = blob_url.rfind("/")
    if last_slash < 0:
        return None
    base = blob_url[:last_slash]
    filename = blob_url[last_slash + 1:]
    output_url = f"{base}/_dicache/{filename}.output.json"
    headers: dict[str, str] = {}
    fetch_url = output_url

    if use_managed_identity():
        headers["Authorization"] = f"Bearer {bearer_token(STORAGE_SCOPE)}"
        headers["x-ms-version"] = "2023-11-03"
    else:
        sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
        if sas and "?" not in output_url:
            fetch_url = f"{output_url}?{sas}"

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.get(fetch_url, headers=headers)
            if resp.status_code == 200:
                logging.info("pre-computed output hit: %s", output_url)
                return json.loads(resp.content)
            return None
    except Exception:
        logging.debug("pre-computed output miss for %s", output_url)
        return None


def fetch_precomputed_vision(blob_url: str, figure_id: str) -> dict[str, Any] | None:
    """
    Fetch pre-computed vision analysis for a single figure.
    Blob path: <container>/_dicache/<filename>.pdf.vision.<figure_id>.json

    Returns the vision result dict on hit, or None on miss.
    """
    last_slash = blob_url.rfind("/")
    if last_slash < 0:
        return None
    base = blob_url[:last_slash]
    filename = blob_url[last_slash + 1:]
    vision_url = f"{base}/_dicache/{filename}.vision.{figure_id}.json"
    headers: dict[str, str] = {}
    fetch_url = vision_url

    if use_managed_identity():
        headers["Authorization"] = f"Bearer {bearer_token(STORAGE_SCOPE)}"
        headers["x-ms-version"] = "2023-11-03"
    else:
        sas = optional_env("STORAGE_BLOB_SAS").lstrip("?")
        if sas and "?" not in vision_url:
            fetch_url = f"{vision_url}?{sas}"

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(fetch_url, headers=headers)
            if resp.status_code == 200:
                logging.info("pre-computed vision hit: %s/%s", filename, figure_id)
                return json.loads(resp.content)
            return None
    except Exception:
        logging.debug("vision cache miss for %s/%s", filename, figure_id)
        return None
