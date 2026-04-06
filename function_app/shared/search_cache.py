"""
Image-hash cache lookup against the existing index.

Skips re-running the vision model if a previous indexing run already
processed an identical image (same parent_id + image_hash).
"""

import os
import logging
from functools import lru_cache
from typing import Optional, Dict, Any

import httpx


def _enabled() -> bool:
    return bool(os.environ.get("SEARCH_ADMIN_KEY")) and bool(os.environ.get("SEARCH_ENDPOINT"))


@lru_cache(maxsize=1)
def _index_url() -> str:
    endpoint = os.environ["SEARCH_ENDPOINT"].rstrip("/")
    index_name = os.environ.get("SEARCH_INDEX_NAME", "mm-manuals-index")
    return f"{endpoint}/indexes/{index_name}/docs/search?api-version=2024-05-01-preview"


def lookup_existing_by_hash(parent_id: str, image_hash: str) -> Optional[Dict[str, Any]]:
    """
    Find a previous diagram record with the same parent + image hash that
    completed successfully. Returns the cached fields, or None.
    """
    if not _enabled() or not parent_id or not image_hash or image_hash == "noimage":
        return None

    body = {
        "search": "*",
        "filter": (
            f"record_type eq 'diagram' "
            f"and parent_id eq '{parent_id}' "
            f"and image_hash eq '{image_hash}' "
            f"and processing_status eq 'ok'"
        ),
        "select": "diagram_description,diagram_category,figure_ref,has_diagram,surrounding_context,header_1,header_2,header_3,figure_bbox,figure_id",
        "top": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "api-key": os.environ["SEARCH_ADMIN_KEY"],
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_index_url(), json=body, headers=headers)
            if resp.status_code != 200:
                logging.warning("hash cache lookup failed: %s %s", resp.status_code, resp.text[:200])
                return None
            hits = resp.json().get("value", [])
            return hits[0] if hits else None
    except Exception as exc:
        logging.warning("hash cache lookup error: %s", exc)
        return None
