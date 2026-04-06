"""
Stable, explicit chunk IDs with selector-specific prefixes.
"""

import hashlib
import os

SKILL_VERSION = os.environ.get("SKILL_VERSION", "1.0.0")


def _short_hash(value: str, length: int = 12) -> str:
    h = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()
    return h[:length]


def parent_id_for(source_path: str, source_file: str) -> str:
    base = source_path or source_file or "unknown"
    return _short_hash(base, 16)


def text_chunk_id(source_path: str, source_file: str, layout_ordinal, page_index: int = 0) -> str:
    pid = parent_id_for(source_path, source_file)
    ord_str = str(layout_ordinal) if layout_ordinal is not None else "0"
    return f"txt_{pid}_{ord_str}_{page_index}"


def diagram_chunk_id(source_path: str, source_file: str, image_hash: str) -> str:
    pid = parent_id_for(source_path, source_file)
    return f"dgm_{pid}_{image_hash[:16]}"


def summary_chunk_id(source_path: str, source_file: str) -> str:
    pid = parent_id_for(source_path, source_file)
    return f"sum_{pid}"


def safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_str(value, default=""):
    if value is None:
        return default
    return str(value)
