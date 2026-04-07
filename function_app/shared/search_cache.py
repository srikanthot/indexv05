"""
Image-hash cache lookup against the existing index.

Skips re-running the vision model if a previous indexing run already
processed an identical image (same parent_id + image_hash).

Hardening:
- OData string-literal escaping to prevent filter injection (and to
  survive any unexpected characters in parent_id or image_hash).
- Field whitelist (`SELECT_FIELDS`) keeps the lookup robust to schema
  evolution: only fields known to exist in the current index are read.
- Feature-gated: silently no-ops if SEARCH_ENDPOINT or SEARCH_ADMIN_KEY
  are not configured.
"""

import logging
import re
from functools import lru_cache
from typing import Optional, Dict, Any

import httpx

from .config import optional_env, feature_enabled


# Fields the cache reads back from a previous successful diagram record.
# Whitelisted explicitly so this never tries to SELECT a field that has
# been removed from the index schema.
SELECT_FIELDS = [
    "diagram_description",
    "diagram_category",
    "figure_ref",
    "has_diagram",
    "surrounding_context",
    "header_1",
    "header_2",
    "header_3",
    "figure_bbox",
    "figure_id",
]

# Acceptable values for the filter inputs. Anything outside this regex
# is rejected before it touches the OData string. Hashes are hex; parent
# ids come from _short_hash which is also hex; figure ids are alnum +
# '_' + '-'.
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _odata_escape(value: str) -> str:
    """Escape an OData string literal: single quotes are doubled."""
    return (value or "").replace("'", "''")


def _safe_token(value: str) -> Optional[str]:
    if not value:
        return None
    if SAFE_TOKEN_RE.match(value):
        return value
    return None


def _enabled() -> bool:
    return feature_enabled("SEARCH_ENDPOINT", "SEARCH_ADMIN_KEY")


@lru_cache(maxsize=1)
def _index_url() -> str:
    endpoint = optional_env("SEARCH_ENDPOINT").rstrip("/")
    index_name = optional_env("SEARCH_INDEX_NAME", "mm-manuals-index")
    return f"{endpoint}/indexes/{index_name}/docs/search?api-version=2024-05-01-preview"


def lookup_existing_by_hash(parent_id: str, image_hash: str) -> Optional[Dict[str, Any]]:
    """
    Find a previous diagram record with the same parent + image hash that
    completed successfully. Returns the cached fields, or None.

    Returns None (not raises) on:
      - feature disabled (env vars not set)
      - empty/invalid inputs
      - any HTTP or parsing failure (logged)
    """
    if not _enabled():
        return None
    if not parent_id or not image_hash or image_hash == "noimage":
        return None

    safe_parent = _safe_token(parent_id)
    safe_hash = _safe_token(image_hash)
    if not safe_parent or not safe_hash:
        logging.warning(
            "hash cache lookup skipped: unsafe token (parent=%r, hash=%r)",
            parent_id, image_hash,
        )
        return None

    body = {
        "search": "*",
        "filter": (
            f"record_type eq 'diagram' "
            f"and parent_id eq '{_odata_escape(safe_parent)}' "
            f"and image_hash eq '{_odata_escape(safe_hash)}' "
            f"and processing_status eq 'ok'"
        ),
        "select": ",".join(SELECT_FIELDS),
        "top": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "api-key": optional_env("SEARCH_ADMIN_KEY"),
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_index_url(), json=body, headers=headers)
            if resp.status_code != 200:
                logging.warning(
                    "hash cache lookup failed: %s %s",
                    resp.status_code, resp.text[:200],
                )
                return None
            hits = resp.json().get("value", [])
            return hits[0] if hits else None
    except Exception as exc:
        logging.warning("hash cache lookup error: %s", exc)
        return None
