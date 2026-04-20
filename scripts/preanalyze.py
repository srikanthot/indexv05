"""
Pre-analyze PDFs with Document Intelligence and cache results in blob.
Run BEFORE the indexer. The process-document skill reads cached results
instead of calling DI live, removing the 230-second WebApi skill timeout
constraint for large PDFs.

Features:
  - Image triage: skips tiny/decorative figures (< 1.0" or < 10KB PNG
    or extreme aspect ratio)
  - Hash dedup: skips identical crops within each PDF
  - Vision pre-analysis: calls GPT-4 vision during preanalyze so the
    indexer returns precomputed results instantly
  - Intra-PDF parallelism: runs N vision calls concurrently per PDF
  - Resumable phases: --phase di / vision / output so crashes don't
    lose progress
  - Incremental mode: --incremental skips PDFs that already have output
  - Cleanup mode: --cleanup removes orphaned cache for deleted PDFs

Usage:
    # Full run (all phases, sequential)
    python scripts/preanalyze.py --config deploy.config.json

    # Phased run (resumable)
    python scripts/preanalyze.py --config deploy.config.json --phase di --concurrency 3
    python scripts/preanalyze.py --config deploy.config.json --phase vision --vision-parallel 20
    python scripts/preanalyze.py --config deploy.config.json --phase output

    # Incremental (only new PDFs, for scheduled automation)
    python scripts/preanalyze.py --config deploy.config.json --incremental

    # Cleanup orphaned cache
    python scripts/preanalyze.py --config deploy.config.json --cleanup

    # Force re-process everything
    python scripts/preanalyze.py --config deploy.config.json --force --vision-parallel 20
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import threading

import httpx

# -- Vision API helpers --

_aoai_key_cache: str | None = None
_aoai_key_lock = threading.Lock()
_di_key_lock = threading.Lock()
_conn_str_lock = threading.Lock()
_storage_init_lock = threading.Lock()

FIGURE_REF_RE = re.compile(
    r"\b(Figure|Fig\.?)\s*[\-:]?\s*([A-Z]{0,3}[\-\.]?\d[\w\-\.]{0,8})",
    re.IGNORECASE,
)

VISION_SYSTEM_PROMPT = """You are a technical-manual diagram analyst.

Return STRICT JSON with these keys:
  category:    one of [circuit_diagram, wiring_diagram, schematic, line_diagram, block_diagram, pid_diagram, flow_diagram, control_logic, exploded_view, parts_list_diagram, nameplate, equipment_photo, decorative, unknown]
  is_useful:   boolean. true unless category is decorative/unknown.
  figure_ref:  e.g. "Figure 4-2", "Fig. 12", or "" if none visible.
  description: dense, retrieval-friendly description (3-8 sentences).
               For diagrams, name components, labels, connections, units, and
               what the diagram is showing. For nameplates, transcribe key
               fields. If any text or value is unclear, say so explicitly.
               Do not guess.
  ocr_text:    transcribe ALL visible text labels, part numbers, values,
               wire tags, terminal IDs, model numbers, and callout numbers
               found in the image. Preserve the original text exactly.
               Separate items with " | ". If no readable text, return "".

Return ONLY the JSON object. No markdown, no commentary."""

USEFUL_CATEGORIES = {
    "circuit_diagram", "wiring_diagram", "schematic", "line_diagram",
    "block_diagram", "pid_diagram", "flow_diagram", "control_logic",
    "exploded_view", "parts_list_diagram", "nameplate", "equipment_photo",
}

# -- Image triage thresholds (v3 -- more aggressive) --
MIN_WIDTH_IN = 1.0
MIN_HEIGHT_IN = 1.0
MIN_CROP_BYTES = 10_000   # 10 KB
MAX_ASPECT_RATIO = 8.0    # skip horizontal lines / vertical dividers


def _get_aoai_key(cfg: dict) -> str:
    global _aoai_key_cache
    if _aoai_key_cache is not None:
        return _aoai_key_cache
    with _aoai_key_lock:
        if _aoai_key_cache is not None:
            return _aoai_key_cache
        ep = cfg["azureOpenAI"]["endpoint"].rstrip("/")
        resource_name = ep.split("//")[1].split(".")[0]
        rg = cfg["functionApp"]["resourceGroup"]
        raw = _run_az([
            "az", "cognitiveservices", "account", "keys", "list",
            "--name", resource_name, "--resource-group", rg, "-o", "json",
        ])
        _aoai_key_cache = json.loads(raw)["key1"]
        return _aoai_key_cache


def _call_vision_api(cfg: dict, image_b64: str, user_text: str, max_retries: int = 3) -> dict:
    ep = cfg["azureOpenAI"]["endpoint"].rstrip("/")
    deployment = cfg["azureOpenAI"]["visionDeployment"]
    api_ver = cfg["azureOpenAI"].get("apiVersion", "2024-12-01-preview")
    api_key = _get_aoai_key(cfg)
    url = f"{ep}/openai/deployments/{deployment}/chat/completions?api-version={api_ver}"

    body = {
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]},
        ],
        "temperature": 0.0,
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    headers = {"api-key": api_key, "Content-Type": "application/json"}

    ssl_verify: str | bool = os.environ.get("SSL_CERT_FILE") or True
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=60.0, verify=ssl_verify) as client:
                resp = client.post(url, json=body, headers=headers)
                if resp.status_code == 429:
                    # Cap Retry-After at 120s so a misconfigured server
                    # can't make us block for an hour on a single figure.
                    try:
                        retry_after = int(resp.headers.get("Retry-After", "10"))
                    except (TypeError, ValueError):
                        retry_after = 10
                    retry_after = min(max(retry_after, 1), 120)
                    print(f"        rate limited, waiting {retry_after}s...", flush=True)
                    time.sleep(retry_after)
                    continue
                if resp.status_code != 200:
                    # 4xx (content filter, bad request, auth) is not retryable.
                    # Only retry 5xx server errors.
                    if resp.status_code >= 500 and attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    raise RuntimeError(f"vision API {resp.status_code}: {resp.text[:300]}")
                # Parse the response. If the payload is malformed we treat
                # it as a transient failure and retry, because AOAI has been
                # known to return truncated chunked-transfer bodies.
                try:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    raise RuntimeError(f"vision API malformed response: {exc}") from exc
                text = (content or "").strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
                return json.loads(text)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError):
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            raise
    return {}


def _build_vision_user_text(fig_data: dict) -> str:
    source_file = fig_data.get("source_file", "")
    h1 = fig_data.get("header_1", "")
    h2 = fig_data.get("header_2", "")
    h3 = fig_data.get("header_3", "")
    header_path = " > ".join([h for h in [h1, h2, h3] if h])
    page = str(fig_data.get("page_number", ""))
    caption = fig_data.get("caption", "")
    surrounding = fig_data.get("surrounding_context", "")

    refs = ", ".join(
        sorted(set(f"{m.group(1).title()} {m.group(2)}" for m in FIGURE_REF_RE.finditer(surrounding)))
    )
    surrounding_safe = surrounding[:1500].replace('"', "'")

    return (
        f'You are analyzing a figure from technical manual "{source_file}".\n'
        f"Section: {header_path or '(unknown)'}\n"
        f"Page: {page or '(unknown)'}\n"
        f"Caption (from layout): {caption or '(none)'}\n"
        f"Body text references this figure as: {refs or '(none)'}\n"
        f'Surrounding text: "{surrounding_safe}"\n\n'
        f"If this is a technical diagram, describe it in full detail.\n"
        f"If any text/value is unclear, say so explicitly. Do not guess.\n"
        f"If decorative/logo/photo, return category=decorative and is_useful=false."
    )


# -- Config + storage helpers --

def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _account_name(cfg: dict) -> str:
    parts = cfg["storage"]["accountResourceId"].rstrip("/").split("/")
    return parts[-1]


_conn_str_cache: str | None = None


def _run_az(cmd: list[str]) -> str:
    if cmd and cmd[0] == "az":
        cmd[0] = "az.cmd" if os.name == "nt" else "az"
    r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        print(f"  az CLI error (exit {r.returncode}):", flush=True)
        print(f"  stderr: {r.stderr[:500]}", flush=True)
        print(f"  stdout: {r.stdout[:500]}", flush=True)
        r.check_returncode()
    return r.stdout.strip()


def _get_connection_string(cfg: dict) -> str:
    global _conn_str_cache
    if _conn_str_cache is not None:
        return _conn_str_cache
    with _conn_str_lock:
        if _conn_str_cache is not None:
            return _conn_str_cache
        account = _account_name(cfg)
        rg = cfg["functionApp"]["resourceGroup"]
        raw = _run_az([
            "az", "storage", "account", "show-connection-string",
            "--name", account, "--resource-group", rg, "-o", "json",
        ])
        _conn_str_cache = json.loads(raw)["connectionString"]
        return _conn_str_cache


def _parse_conn_str(conn_str: str) -> dict[str, str]:
    parts = {}
    for pair in conn_str.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            parts[k] = v
    return parts


_storage_client: httpx.Client | None = None
_storage_account_key: str = ""
_storage_account_name: str = ""
_storage_endpoint_suffix: str = ""


def _init_storage(cfg: dict) -> None:
    global _storage_client, _storage_account_key, _storage_account_name, _storage_endpoint_suffix
    if _storage_client is not None:
        return
    with _storage_init_lock:
        if _storage_client is not None:
            return
        conn_str = _get_connection_string(cfg)
        parts = _parse_conn_str(conn_str)
        _storage_account_name = parts.get("AccountName", "")
        _storage_account_key = parts.get("AccountKey", "")
        _storage_endpoint_suffix = parts.get("EndpointSuffix", "core.windows.net")
        ssl_verify: str | bool = os.environ.get("SSL_CERT_FILE") or True
        _storage_client = httpx.Client(
            timeout=httpx.Timeout(connect=30.0, write=600.0, read=600.0, pool=30.0),
            verify=ssl_verify,
        )


def _storage_auth_header(method: str, container: str, blob_name: str,
                          content_length: int = 0,
                          content_type: str = "",
                          extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    x_ms_version = "2023-11-03"
    x_ms_date = now

    canon_headers_dict = {"x-ms-date": x_ms_date, "x-ms-version": x_ms_version}
    if extra_headers:
        for k, v in extra_headers.items():
            if k.lower().startswith("x-ms-"):
                canon_headers_dict[k.lower()] = v
    canon_headers = "\n".join(f"{k}:{v}" for k, v in sorted(canon_headers_dict.items()))
    canon_resource = f"/{_storage_account_name}/{container}/{blob_name}"

    string_to_sign = (
        f"{method}\n\n\n"
        f"{content_length if content_length else ''}\n"
        f"\n{content_type}\n\n\n\n\n\n\n"
        f"{canon_headers}\n{canon_resource}"
    )

    key_bytes = base64.b64decode(_storage_account_key)
    sig = base64.b64encode(
        hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    return {
        "Authorization": f"SharedKey {_storage_account_name}:{sig}",
        "x-ms-date": x_ms_date,
        "x-ms-version": x_ms_version,
    }


def _blob_url(container: str, name: str) -> str:
    return f"https://{_storage_account_name}.blob.{_storage_endpoint_suffix}/{container}/{name}"


def list_pdfs(cfg: dict) -> list[str]:
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    conn_str = _get_connection_string(cfg)
    raw = _run_az([
        "az", "storage", "blob", "list",
        "--container-name", container,
        "--connection-string", conn_str,
        "--query", "[].name",
        "-o", "json",
    ])
    all_names = json.loads(raw)
    return [n for n in all_names if n.lower().endswith(".pdf")]


def list_cache_blobs(cfg: dict) -> list[str]:
    """List all blobs in _dicache/ prefix."""
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    conn_str = _get_connection_string(cfg)
    raw = _run_az([
        "az", "storage", "blob", "list",
        "--container-name", container,
        "--connection-string", conn_str,
        "--prefix", "_dicache/",
        "--query", "[].name",
        "-o", "json",
    ])
    return json.loads(raw)


_BLOB_RETRY_ATTEMPTS = 3
_BLOB_RETRY_BASE_DELAY = 2.0


def blob_exists(cfg: dict, name: str) -> bool:
    """HEAD a blob. Returns True if 200, False if 404. Raises on network
    errors or unexpected status codes so we never silently treat a
    transient failure as 'blob missing' (which would trigger duplicate work).
    """
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    url = _blob_url(container, name)

    last_exc: Exception | None = None
    for attempt in range(_BLOB_RETRY_ATTEMPTS):
        try:
            headers = _storage_auth_header("HEAD", container, name)
            resp = _storage_client.head(url, headers=headers)
            if resp.status_code == 200:
                return True
            if resp.status_code == 404:
                return False
            # Treat 5xx / throttling as transient; retry
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(f"blob HEAD {resp.status_code}: {resp.text[:120]}")
            else:
                raise RuntimeError(f"blob HEAD unexpected {resp.status_code}: {resp.text[:200]}")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
        if attempt < _BLOB_RETRY_ATTEMPTS - 1:
            time.sleep(_BLOB_RETRY_BASE_DELAY * (attempt + 1))
    raise RuntimeError(f"blob HEAD failed after {_BLOB_RETRY_ATTEMPTS} attempts: {last_exc}")


def fetch_blob(cfg: dict, name: str) -> bytes:
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    url = _blob_url(container, name)

    last_exc: Exception | None = None
    for attempt in range(_BLOB_RETRY_ATTEMPTS):
        try:
            headers = _storage_auth_header("GET", container, name)
            resp = _storage_client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.content
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(f"blob GET {resp.status_code}: {resp.text[:120]}")
            else:
                raise RuntimeError(f"blob fetch failed: {resp.status_code} {resp.text[:200]}")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
        if attempt < _BLOB_RETRY_ATTEMPTS - 1:
            time.sleep(_BLOB_RETRY_BASE_DELAY * (attempt + 1))
    raise RuntimeError(f"blob GET failed after {_BLOB_RETRY_ATTEMPTS} attempts: {last_exc}")


def upload_blob(cfg: dict, name: str, data: bytes) -> None:
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    url = _blob_url(container, name)
    content_type = "application/json"

    last_exc: Exception | None = None
    for attempt in range(_BLOB_RETRY_ATTEMPTS):
        try:
            extra = {"x-ms-blob-type": "BlockBlob"}
            headers = _storage_auth_header("PUT", container, name,
                                             content_length=len(data),
                                             content_type=content_type,
                                             extra_headers=extra)
            headers["Content-Type"] = content_type
            headers["x-ms-blob-type"] = "BlockBlob"
            resp = _storage_client.put(url, headers=headers, content=data)
            if resp.status_code in (200, 201):
                return
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = RuntimeError(f"blob PUT {resp.status_code}: {resp.text[:120]}")
            else:
                raise RuntimeError(f"blob upload failed: {resp.status_code} {resp.text[:200]}")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
        if attempt < _BLOB_RETRY_ATTEMPTS - 1:
            time.sleep(_BLOB_RETRY_BASE_DELAY * (attempt + 1))
    raise RuntimeError(f"blob PUT failed after {_BLOB_RETRY_ATTEMPTS} attempts: {last_exc}")


def delete_blob(cfg: dict, name: str) -> bool:
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    url = _blob_url(container, name)
    headers = _storage_auth_header("DELETE", container, name)
    resp = _storage_client.request("DELETE", url, headers=headers)
    return resp.status_code in (200, 202, 204)


# -- DI helpers --

_di_key_cache: str | None = None


def _get_di_key(cfg: dict) -> str:
    global _di_key_cache
    if _di_key_cache is not None:
        return _di_key_cache
    with _di_key_lock:
        if _di_key_cache is not None:
            return _di_key_cache
        ep = cfg["documentIntelligence"]["endpoint"].rstrip("/")
        resource_name = ep.split("//")[1].split(".")[0]
        rg = cfg["functionApp"]["resourceGroup"]
        raw = _run_az([
            "az", "cognitiveservices", "account", "keys", "list",
            "--name", resource_name, "--resource-group", rg, "-o", "json",
        ])
        _di_key_cache = json.loads(raw)["key1"]
        return _di_key_cache


def _generate_blob_sas(cfg: dict, blob_name: str, expiry_minutes: int = 60) -> str:
    """Generate a read-only SAS URL for a blob so DI can fetch it directly."""
    _init_storage(cfg)
    container = cfg["storage"]["pdfContainerName"]
    conn_str = _get_connection_string(cfg)
    expiry = (datetime.now(UTC).__add__(
        __import__("datetime").timedelta(minutes=expiry_minutes)
    )).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = _run_az([
        "az", "storage", "blob", "generate-sas",
        "--container-name", container,
        "--name", blob_name,
        "--connection-string", conn_str,
        "--permissions", "r",
        "--expiry", expiry,
        "-o", "tsv",
    ])
    sas_token = raw.strip().strip('"')
    return f"{_blob_url(container, blob_name)}?{sas_token}"


# Threshold above which we use urlSource (DI fetches from blob) instead
# of sending bytes in the POST body. Avoids server disconnects on large uploads.
_URL_SOURCE_THRESHOLD = 30 * 1024 * 1024  # 30 MB


def analyze_di(cfg: dict, pdf_bytes: bytes, timeout_s: int = 900,
               pdf_name: str = "") -> dict:
    ep = cfg["documentIntelligence"]["endpoint"].rstrip("/")
    api_ver = cfg["documentIntelligence"].get("apiVersion", "2024-11-30")
    url = (
        f"{ep}/documentintelligence/documentModels/prebuilt-layout:analyze"
        f"?api-version={api_ver}&outputContentFormat=markdown"
    )
    api_key = _get_di_key(cfg)
    use_url_source = len(pdf_bytes) > _URL_SOURCE_THRESHOLD and pdf_name

    ssl_verify: str | bool = os.environ.get("SSL_CERT_FILE") or True
    max_submit_retries = 3

    with httpx.Client(
        timeout=httpx.Timeout(connect=30.0, write=600.0, read=300.0, pool=30.0),
        verify=ssl_verify,
    ) as client:
        # Submit with retry
        op_loc = None
        for attempt in range(max_submit_retries):
            try:
                if use_url_source:
                    # Large PDF: give DI a SAS URL so it fetches server-to-server
                    sas_url = _generate_blob_sas(cfg, pdf_name, expiry_minutes=120)
                    headers = {
                        "Ocp-Apim-Subscription-Key": api_key,
                        "Content-Type": "application/json",
                    }
                    submit = client.post(url, headers=headers,
                                         json={"urlSource": sas_url})
                    print(f"      submitted via urlSource ({len(pdf_bytes) / 1024 / 1024:.0f} MB)", flush=True)
                else:
                    headers = {
                        "Ocp-Apim-Subscription-Key": api_key,
                        "Content-Type": "application/pdf",
                    }
                    submit = client.post(url, headers=headers, content=pdf_bytes)

                if submit.status_code not in (200, 202):
                    raise RuntimeError(f"DI submit: {submit.status_code} {submit.text[:300]}")
                op_loc = submit.headers.get("operation-location")
                if not op_loc:
                    raise RuntimeError("DI submit missing operation-location header")
                break  # success
            except (httpx.RemoteProtocolError, httpx.WriteTimeout, httpx.ConnectError) as exc:
                if attempt < max_submit_retries - 1:
                    wait = 10 * (attempt + 1)
                    print(f"      submit failed ({type(exc).__name__}), retrying in {wait}s...", flush=True)
                    time.sleep(wait)
                    continue
                raise

        if not op_loc:
            raise RuntimeError("DI submit failed after all retries")

        deadline = time.time() + timeout_s
        start = time.time()
        backoff = 3.0
        consecutive_poll_errors = 0
        while time.time() < deadline:
            poll_h = {"Ocp-Apim-Subscription-Key": api_key}
            try:
                poll = client.get(op_loc, headers=poll_h)
                body = poll.json()
                status = body.get("status")
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, json.JSONDecodeError) as exc:
                # Transient poll failure. Back off and try again; don't
                # kill an hours-long DI run over one blip.
                consecutive_poll_errors += 1
                if consecutive_poll_errors >= 10:
                    raise RuntimeError(f"DI poll failed 10x in a row: {exc}") from exc
                time.sleep(min(backoff * consecutive_poll_errors, 30.0))
                continue
            consecutive_poll_errors = 0
            if status == "succeeded":
                return body.get("analyzeResult", {})
            if status == "failed":
                raise RuntimeError(f"DI failed: {body}")
            if status not in ("running", "notStarted", None):
                # Unknown status; treat as transient and keep polling.
                print(f"      unexpected DI status '{status}', continuing to poll", flush=True)
            elapsed = int(time.time() - start)
            print(f"      polling... {elapsed}s ({status})", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 1.3, 15.0)
        raise TimeoutError(f"DI timed out after {timeout_s}s")


# -- Triage helpers --

def _passes_triage(polygon: list[float], image_b64: str | None = None) -> tuple[bool, str]:
    """Return (passes, reason). Checks dimensions + aspect ratio + crop size."""
    xs = polygon[0::2]
    ys = polygon[1::2]
    w_in = max(xs) - min(xs)
    h_in = max(ys) - min(ys)

    if w_in < MIN_WIDTH_IN or h_in < MIN_HEIGHT_IN:
        return False, f"tiny ({w_in:.1f}x{h_in:.1f} in)"

    if w_in > 0 and h_in > 0:
        ratio = max(w_in / h_in, h_in / w_in)
        if ratio > MAX_ASPECT_RATIO:
            return False, f"extreme aspect ratio ({ratio:.1f}:1)"

    if image_b64:
        try:
            raw_png = base64.b64decode(image_b64)
            if len(raw_png) < MIN_CROP_BYTES:
                return False, f"small crop ({len(raw_png)} bytes)"
        except Exception:
            pass

    return True, ""


# -- Phase A: DI analysis + cropping --

def _pdf_has_any_crops(cfg: dict, pdf_name: str) -> bool:
    """Cheap probe: does the cache already hold at least one crop blob
    for this PDF? Used to distinguish a fully-done PDF from one whose
    DI cache was written but whose crop phase crashed (e.g. previous
    fitz import failure). A single LIST call covers arbitrary PDF names."""
    try:
        _init_storage(cfg)
        container = cfg["storage"]["pdfContainerName"]
        conn_str = _get_connection_string(cfg)
        raw = _run_az([
            "az", "storage", "blob", "list",
            "--container-name", container,
            "--connection-string", conn_str,
            "--prefix", f"_dicache/{pdf_name}.crop.",
            "--num-results", "1",
            "--query", "[].name",
            "-o", "json",
        ])
        return len(json.loads(raw)) > 0
    except Exception:
        return False


def phase_di(cfg: dict, pdf_name: str, force: bool) -> str:
    """Run DI analysis and crop figures. Cache results to blob.

    Resumable: if DI cache exists but zero crops do, re-runs only the
    crop+upload phase using the cached DI result (no extra DI call)."""
    cache_name = "_dicache/" + pdf_name + ".di.json"

    if not force and blob_exists(cfg, cache_name):
        if _pdf_has_any_crops(cfg, pdf_name):
            return f"  skip-di  {pdf_name} (DI cached + crops present)"
        # DI cache exists but crops don't -- previous run crashed between
        # DI upload and crop upload. Resume without re-running DI.
        print(f"  resume-crops  {pdf_name} (DI cached, crops missing)", flush=True)
        try:
            di_cache_bytes = fetch_blob(cfg, cache_name)
            result = json.loads(di_cache_bytes)
            pdf_bytes = fetch_blob(cfg, pdf_name)
            elapsed = 0.0
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return f"  FAIL-di  {pdf_name}: resume-crops fetch failed: {type(exc).__name__}: {exc}"
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "function_app"))
        from shared.pdf_crop import crop_figure_png_b64
        return _do_crops(cfg, pdf_name, pdf_bytes, result, crop_figure_png_b64, elapsed)

    try:
        pdf_bytes = fetch_blob(cfg, pdf_name)
        size_mb = len(pdf_bytes) / (1024 * 1024)
        print(f"  DI analyzing {pdf_name} ({size_mb:.1f} MB) ...", flush=True)
        t0 = time.time()

        result = analyze_di(cfg, pdf_bytes, pdf_name=pdf_name)
        elapsed = time.time() - t0
        di_bytes = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        upload_blob(cfg, cache_name, di_bytes)
        di_mb = len(di_bytes) / (1024 * 1024)
        print(f"    DI done ({elapsed:.0f}s, {di_mb:.1f} MB)", flush=True)

        # Import shared modules for cropping
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "function_app"))
        from shared.pdf_crop import crop_figure_png_b64

        return _do_crops(cfg, pdf_name, pdf_bytes, result, crop_figure_png_b64, elapsed)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return f"  FAIL-di  {pdf_name}: {type(exc).__name__}: {exc}"


def _do_crops(cfg: dict, pdf_name: str, pdf_bytes: bytes,
              result: dict, crop_figure_png_b64, elapsed: float) -> str:
    """Shared crop + parallel-upload body used by both the fresh-run and
    resume-crops paths of phase_di. Safe to call when the DI cache already
    exists; only uploads crops that are new (parallel uploader overwrites
    by name, so it is idempotent)."""
    figures = result.get("figures", []) or []
    skip_count = 0
    seen_hashes: dict[str, str] = {}

    pending_uploads: list[tuple[str, bytes]] = []
    for fig_idx, figure in enumerate(figures):
        fig_id = figure.get("id") or f"fig_{fig_idx}"
        page = None
        polygon = None
        for br in figure.get("boundingRegions", []) or []:
            p = br.get("pageNumber")
            poly = br.get("polygon")
            if isinstance(p, int) and poly:
                page = p
                polygon = poly
                break
        if not page or not polygon or len(polygon) < 4:
            print(f"    skip fig {fig_id}: page={page} polygon_len={len(polygon) if polygon else 0}", flush=True)
            continue

        passes, reason = _passes_triage(polygon)
        if not passes:
            skip_count += 1
            continue

        try:
            image_b64, bbox = crop_figure_png_b64(pdf_bytes, page, polygon)

            passes2, reason2 = _passes_triage(polygon, image_b64)
            if not passes2:
                skip_count += 1
                continue

            raw_png = base64.b64decode(image_b64)
            crop_hash = hashlib.sha256(raw_png).hexdigest()
            if crop_hash in seen_hashes:
                skip_count += 1
                continue
            seen_hashes[crop_hash] = fig_id

            crop_obj = {"image_b64": image_b64, "bbox": bbox}
            crop_bytes = json.dumps(crop_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            pending_uploads.append((f"_dicache/{pdf_name}.crop.{fig_id}.json", crop_bytes))
        except Exception as exc:
            print(f"    crop error (fig {fig_id} pg {page}): {exc}", flush=True)
            continue

    crop_count = 0
    if pending_uploads:
        print(f"    uploading {len(pending_uploads)} crops (10 parallel)...", flush=True)

        def _upload_one(item: tuple[str, bytes]) -> bool:
            try:
                upload_blob(cfg, item[0], item[1])
                return True
            except Exception as exc:
                print(f"    upload error ({item[0]}): {exc}", flush=True)
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_upload_one, item) for item in pending_uploads]
            for f in as_completed(futures):
                if f.result():
                    crop_count += 1
                if crop_count % 100 == 0 and crop_count > 0:
                    print(f"    {crop_count}/{len(pending_uploads)} uploaded...", flush=True)

    return f"  ok-di  {pdf_name} ({elapsed:.0f}s, {crop_count} crops, {skip_count} skipped)"


# -- Phase B: Vision analysis (parallel within each PDF) --

_VISION_MAX_ATTEMPTS = 3


def _vision_one_figure(cfg: dict, pdf_name: str, fig_data: dict, force: bool) -> dict | None:
    """Process a single figure's vision call. Returns fig_data with vision fields, or None.

    Cache semantics:
      - Successful result: cached as vision JSON. Skipped on re-run.
      - Transient error (JSON parse, timeout, etc.): cached as
        {"_error": "...", "_attempts": N}. Retried until N >= _VISION_MAX_ATTEMPTS.
      - Permanent error (content filter): cached with _attempts forced to max so
        we never retry.
    """
    fig_id = fig_data["figure_id"]
    vision_blob = f"_dicache/{pdf_name}.vision.{fig_id}.json"

    prior_attempts = 0
    if not force:
        try:
            if blob_exists(cfg, vision_blob):
                vb = fetch_blob(cfg, vision_blob)
                cached = json.loads(vb)
                if not isinstance(cached, dict) or "_error" not in cached:
                    return cached
                prior_attempts = int(cached.get("_attempts", 0))
                if prior_attempts >= _VISION_MAX_ATTEMPTS:
                    return {}  # give up, treat as "no useful result"
        except Exception:
            pass

    crop_blob = f"_dicache/{pdf_name}.crop.{fig_id}.json"
    try:
        crop_bytes = fetch_blob(cfg, crop_blob)
        crop_data = json.loads(crop_bytes)
        image_b64 = crop_data.get("image_b64", "")
    except Exception:
        return None

    if not image_b64:
        return None

    try:
        user_text = _build_vision_user_text(fig_data)
        vision_result = _call_vision_api(cfg, image_b64, user_text)
    except Exception as exc:
        # The vision call itself failed (API error, JSON parse, etc.).
        # Record the error so we can retry sparingly and stop retrying
        # permanent failures like content-filter blocks.
        msg = str(exc)
        is_permanent = "content_filter" in msg or "ResponsibleAIPolicy" in msg
        attempts = _VISION_MAX_ATTEMPTS if is_permanent else prior_attempts + 1
        err_record = {"_error": msg[:500], "_attempts": attempts}
        try:
            err_bytes = json.dumps(err_record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            upload_blob(cfg, vision_blob, err_bytes)
        except Exception:
            pass  # if we can't even save the error, next run will still retry
        tag = "permanent" if is_permanent else f"attempt {attempts}/{_VISION_MAX_ATTEMPTS}"
        print(f"    vision error ({fig_id}, {tag}): {exc}", flush=True)
        return {}

    # Vision call succeeded. Cache is best-effort -- if the cache upload fails,
    # log it but still return the result so we don't waste the tokens we paid for.
    try:
        vr_bytes = json.dumps(vision_result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        upload_blob(cfg, vision_blob, vr_bytes)
    except Exception as exc:
        print(f"    cache-save failed ({fig_id}): {exc}  (result still used)", flush=True)
    return vision_result


def phase_vision(cfg: dict, pdf_name: str, force: bool, vision_parallel: int = 20) -> str:
    """Run vision API on all cropped figures with intra-PDF parallelism."""
    cache_name = "_dicache/" + pdf_name + ".di.json"
    output_name = "_dicache/" + pdf_name + ".output.json"

    # Fast path: if the PDF is already fully assembled, don't iterate 2000+
    # figures just to HEAD-check each cached vision blob.
    if not force and blob_exists(cfg, output_name):
        return f"  skip-vision  {pdf_name} (output already cached)"

    if not blob_exists(cfg, cache_name):
        return f"  skip-vision  {pdf_name} (no DI cache -- run --phase di first)"

    try:
        di_bytes = fetch_blob(cfg, cache_name)
        result = json.loads(di_bytes)
        if "analyzeResult" in result:
            result = result["analyzeResult"]

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "function_app"))
        from shared.ids import parent_id_for
        from shared.sections import build_section_index, extract_surrounding_text, find_section_for_page

        sections_index = build_section_index(result)
        account = _account_name(cfg)
        container = cfg["storage"]["pdfContainerName"]
        source_path = f"https://{account}.blob.{_storage_endpoint_suffix}/{container}/{pdf_name}"
        source_file = pdf_name
        parent_id = parent_id_for(source_path, source_file)

        figures = result.get("figures", []) or []
        fig_tasks: list[dict] = []

        for fig_idx, figure in enumerate(figures):
            fig_id = figure.get("id") or f"fig_{fig_idx}"
            page = None
            polygon = None
            for br in figure.get("boundingRegions", []) or []:
                p = br.get("pageNumber")
                poly = br.get("polygon")
                if isinstance(p, int) and poly:
                    page = p
                    polygon = poly
                    break
            if not page or not polygon or len(polygon) < 4:
                continue

            passes, _ = _passes_triage(polygon)
            if not passes:
                continue

            crop_blob = f"_dicache/{pdf_name}.crop.{fig_id}.json"
            if not blob_exists(cfg, crop_blob):
                continue

            cap = figure.get("caption") or {}
            caption = (cap.get("content") or "").strip()

            section = find_section_for_page(sections_index, page)
            h1 = section["header_1"] if section else ""
            h2 = section["header_2"] if section else ""
            h3 = section["header_3"] if section else ""
            surrounding = extract_surrounding_text(section["content"], caption, chars=200) if section else ""

            fig_tasks.append({
                "figure_id": fig_id,
                "page_number": page,
                "caption": caption,
                "header_1": h1, "header_2": h2, "header_3": h3,
                "surrounding_context": surrounding,
                "source_file": source_file,
                "source_path": source_path,
                "parent_id": parent_id,
            })

        if not fig_tasks:
            return f"  skip-vision  {pdf_name} (no figures to process)"

        # Pre-cache the AOAI key before spawning threads so 20 threads
        # don't race to call az CLI simultaneously.
        try:
            _get_aoai_key(cfg)
        except Exception as exc:
            return f"  FAIL-vision  {pdf_name}: could not fetch AOAI key: {exc}"

        print(f"  vision {pdf_name}: {len(fig_tasks)} figures, {vision_parallel} parallel...", flush=True)
        t0 = time.time()
        done = 0
        useful = 0

        with ThreadPoolExecutor(max_workers=vision_parallel) as pool:
            futures = {
                pool.submit(_vision_one_figure, cfg, pdf_name, fd, force): fd
                for fd in fig_tasks
            }
            for f in as_completed(futures):
                done += 1
                try:
                    vr = f.result()
                except Exception as exc:
                    # A figure thread crashed with an uncaught exception.
                    # Record and move on so one figure doesn't nuke the whole PDF.
                    fd = futures[f]
                    print(f"    vision crash ({fd.get('figure_id', '?')}): "
                          f"{type(exc).__name__}: {exc}", flush=True)
                    vr = None
                if vr and vr.get("is_useful"):
                    useful += 1
                if done % 50 == 0:
                    print(f"    {pdf_name}: {done}/{len(fig_tasks)} vision calls done...", flush=True)

        elapsed = time.time() - t0
        return f"  ok-vision  {pdf_name} ({done} calls, {useful} useful, {elapsed:.0f}s)"
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return f"  FAIL-vision  {pdf_name}: {type(exc).__name__}: {exc}"


# -- Phase C: Assemble output.json --

def phase_output(cfg: dict, pdf_name: str, force: bool) -> str:
    """Assemble the final output.json from cached DI + vision + crop results."""
    output_name = "_dicache/" + pdf_name + ".output.json"
    cache_name = "_dicache/" + pdf_name + ".di.json"

    if not force and blob_exists(cfg, output_name):
        return f"  skip-output  {pdf_name} (output cached)"

    if not blob_exists(cfg, cache_name):
        return f"  skip-output  {pdf_name} (no DI cache)"

    try:
        di_bytes = fetch_blob(cfg, cache_name)
        result = json.loads(di_bytes)
        if "analyzeResult" in result:
            result = result["analyzeResult"]

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "function_app"))
        from shared.ids import SKILL_VERSION, parent_id_for
        from shared.sections import build_section_index, extract_surrounding_text, find_section_for_page
        from shared.tables import extract_table_records

        sections_index = build_section_index(result)
        account = _account_name(cfg)
        container = cfg["storage"]["pdfContainerName"]
        source_path = f"https://{account}.blob.{_storage_endpoint_suffix}/{container}/{pdf_name}"
        source_file = pdf_name
        parent_id = parent_id_for(source_path, source_file)

        enriched_figures = []
        figures = result.get("figures", []) or []
        for fig_idx, figure in enumerate(figures):
            fig_id = figure.get("id") or f"fig_{fig_idx}"
            page = None
            polygon = None
            for br in figure.get("boundingRegions", []) or []:
                p = br.get("pageNumber")
                poly = br.get("polygon")
                if isinstance(p, int) and poly:
                    page = p
                    polygon = poly
                    break
            if not page or not polygon or len(polygon) < 4:
                continue

            passes, _ = _passes_triage(polygon)
            if not passes:
                continue

            crop_blob = f"_dicache/{pdf_name}.crop.{fig_id}.json"
            if not blob_exists(cfg, crop_blob):
                continue

            cap = figure.get("caption") or {}
            caption = (cap.get("content") or "").strip()

            # Load crop bbox (but NOT image_b64 -- keep output.json small)
            try:
                crop_data = json.loads(fetch_blob(cfg, crop_blob))
                bbox = crop_data.get("bbox", {})
            except Exception:
                bbox = {}

            section = find_section_for_page(sections_index, page)
            h1 = section["header_1"] if section else ""
            h2 = section["header_2"] if section else ""
            h3 = section["header_3"] if section else ""
            surrounding = extract_surrounding_text(section["content"], caption, chars=200) if section else ""

            fig_data = {
                "figure_id": fig_id,
                "page_number": page,
                "caption": caption,
                "image_b64": "",
                "bbox": bbox,
                "header_1": h1, "header_2": h2, "header_3": h3,
                "surrounding_context": surrounding,
                "source_file": source_file,
                "source_path": source_path,
                "parent_id": parent_id,
            }

            vision_blob = f"_dicache/{pdf_name}.vision.{fig_id}.json"
            vision_result: dict = {}
            try:
                if blob_exists(cfg, vision_blob):
                    cached = json.loads(fetch_blob(cfg, vision_blob))
                    # Error-cached records (from _vision_one_figure retry
                    # logic) have an "_error" key -- treat as no useful result.
                    if isinstance(cached, dict) and "_error" not in cached:
                        vision_result = cached
            except Exception:
                pass

            v_category = (vision_result.get("category") or "unknown").strip().lower()
            v_description = (vision_result.get("description") or "").strip()
            v_figure_ref = (vision_result.get("figure_ref") or "").strip()
            v_ocr_text = (vision_result.get("ocr_text") or "").strip()
            v_is_useful = bool(vision_result.get("is_useful"))
            v_has_diagram = v_is_useful and v_category in USEFUL_CATEGORIES and bool(v_description)

            if not v_figure_ref:
                m = FIGURE_REF_RE.search(caption or surrounding or "")
                if m:
                    v_figure_ref = f"{m.group(1).title()} {m.group(2)}"

            full_description = v_description
            if v_ocr_text:
                full_description = f"{v_description}\nLabels: {v_ocr_text}"

            fig_data["vision_description"] = full_description
            fig_data["vision_category"] = v_category
            fig_data["vision_figure_ref"] = v_figure_ref
            fig_data["vision_has_diagram"] = v_has_diagram
            fig_data["vision_is_useful"] = v_is_useful

            enriched_figures.append(fig_data)

        enriched_tables = []
        for tbl in extract_table_records(result):
            section = find_section_for_page(sections_index, tbl["page_start"])
            h1 = section["header_1"] if section else ""
            h2 = section["header_2"] if section else ""
            h3 = section["header_3"] if section else ""
            enriched_tables.append({
                "table_index": tbl["index"],
                "page_start": tbl["page_start"],
                "page_end": tbl["page_end"],
                "markdown": tbl["markdown"],
                "row_count": tbl["row_count"],
                "col_count": tbl["col_count"],
                "caption": tbl["caption"],
                "header_1": h1, "header_2": h2, "header_3": h3,
                "source_file": source_file,
                "source_path": source_path,
                "parent_id": parent_id,
            })

        output = {
            "enriched_figures": enriched_figures,
            "enriched_tables": enriched_tables,
            "processing_status": "ok",
            "skill_version": SKILL_VERSION,
        }
        output_bytes = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        upload_blob(cfg, output_name, output_bytes)

        vision_ok = sum(1 for f in enriched_figures if f.get("vision_has_diagram"))
        return f"  ok-output  {pdf_name} ({len(enriched_figures)} figs, {vision_ok} useful, {len(enriched_tables)} tables)"
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return f"  FAIL-output  {pdf_name}: {type(exc).__name__}: {exc}"


# -- Status: show per-PDF cache state --

def status_report(cfg: dict) -> None:
    """Print a table showing DI / vision / output cache state for every PDF.

    Fast by design: one LIST call for PDFs, one LIST call for cache blobs,
    then pure in-memory string matching. No per-blob downloads.
    """
    print("Listing PDFs...")
    pdfs = list_pdfs(cfg)
    print(f"Found {len(pdfs)} PDFs")

    if not pdfs:
        print("(no PDFs in container)")
        return

    print("Listing cache blobs...")
    cache_blobs = list_cache_blobs(cfg)
    cache_set = set(cache_blobs)
    print(f"Found {len(cache_blobs)} cache blobs\n")

    # Count vision blobs per PDF by prefix match. We cannot distinguish
    # error-cached from success-cached without downloading each blob, so
    # we just count total -- the end-of-run summary in a real run shows
    # the error split.
    vision_count: dict[str, int] = {pdf: 0 for pdf in pdfs}
    for blob in cache_blobs:
        if ".vision." not in blob:
            continue
        stem = blob.replace("_dicache/", "", 1)
        lower = stem.lower()
        idx = lower.find(".pdf")
        if idx == -1:
            continue
        pdf = stem[: idx + 4]
        if pdf in vision_count:
            vision_count[pdf] += 1

    name_w = max(len(p) for p in pdfs)
    name_w = max(name_w, 20)
    header = f"{'PDF'.ljust(name_w)}  DI    Output   Vision cached"
    print(header)
    print("-" * len(header))

    total_done_pdfs = 0
    for pdf in sorted(pdfs):
        di_ok = f"_dicache/{pdf}.di.json" in cache_set
        output_ok = f"_dicache/{pdf}.output.json" in cache_set
        v = vision_count.get(pdf, 0)
        di_tag = "OK  " if di_ok else "--  "
        out_tag = "OK    " if output_ok else "--    "
        v_str = str(v) if v else "--"
        print(f"{pdf.ljust(name_w)}  {di_tag}  {out_tag}  {v_str}")
        if output_ok:
            total_done_pdfs += 1

    remaining = len(pdfs) - total_done_pdfs
    print()
    print(f"Summary: {total_done_pdfs}/{len(pdfs)} PDFs fully done, "
          f"{remaining} remaining")


# -- Cleanup: remove orphaned cache --

def cleanup_orphans(cfg: dict) -> None:
    """Delete _dicache/ blobs whose source PDF no longer exists."""
    print("Listing PDFs...")
    pdfs = set(list_pdfs(cfg))
    print(f"Found {len(pdfs)} PDFs")
    print("Listing cache blobs...")
    cache_blobs = list_cache_blobs(cfg)
    print(f"Found {len(cache_blobs)} cache blobs")

    orphaned = []
    for blob_name in cache_blobs:
        # _dicache/manual.pdf.di.json -> manual.pdf  (case-insensitive on .pdf)
        stem = blob_name.replace("_dicache/", "", 1)
        lower = stem.lower()
        idx = lower.find(".pdf")
        pdf_name = stem[: idx + 4] if idx != -1 else None
        if pdf_name and pdf_name not in pdfs:
            orphaned.append(blob_name)

    if not orphaned:
        print("No orphaned cache blobs found.")
        return

    print(f"Deleting {len(orphaned)} orphaned cache blobs...")
    for name in orphaned:
        delete_blob(cfg, name)
    print(f"Deleted {len(orphaned)} orphaned blobs.")


# -- Legacy: full single-pass (all phases combined) --

def process_one_full(cfg: dict, pdf_name: str, force: bool, vision_parallel: int) -> str:
    """Run all three phases for a single PDF. Intermediate phases print their
    own line; the final phase's line is returned and printed by the caller."""
    r1 = phase_di(cfg, pdf_name, force)
    if "FAIL" in r1:
        return r1
    print(r1, flush=True)

    r2 = phase_vision(cfg, pdf_name, force, vision_parallel)
    if "FAIL" in r2:
        return r2
    print(r2, flush=True)

    r3 = phase_output(cfg, pdf_name, force)
    return r3


# -- Main --

def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-analyze PDFs with Document Intelligence")
    ap.add_argument("--config", default="deploy.config.json")
    ap.add_argument("--force", action="store_true", help="Re-analyze even if cache exists")
    ap.add_argument("--concurrency", type=int, default=1, help="Parallel PDFs (default 1)")
    ap.add_argument("--vision-parallel", type=int, default=20, help="Parallel vision calls per PDF (default 20)")
    ap.add_argument("--phase", choices=["di", "vision", "output", "all"], default="all",
                    help="Run a specific phase (default: all)")
    ap.add_argument("--incremental", action="store_true",
                    help="Only process PDFs without an output.json cache")
    ap.add_argument("--cleanup", action="store_true",
                    help="Delete orphaned cache blobs for deleted PDFs")
    ap.add_argument("--status", action="store_true",
                    help="Print per-PDF cache state (no work done)")
    args = ap.parse_args()

    # Bounds check user-facing knobs. AOAI TPM quotas cap useful parallelism
    # well below 100; concurrency above 6 rarely helps and adds thread
    # pressure. Fail fast with a clear message instead of producing weird
    # runtime behavior.
    if not 1 <= args.vision_parallel <= 100:
        raise SystemExit(
            f"--vision-parallel must be between 1 and 100 (got {args.vision_parallel})"
        )
    if not 1 <= args.concurrency <= 10:
        raise SystemExit(
            f"--concurrency must be between 1 and 10 (got {args.concurrency})"
        )

    cfg = load_config(Path(args.config))

    if args.cleanup:
        cleanup_orphans(cfg)
        return

    if args.status:
        status_report(cfg)
        return

    print(f"Container: {cfg['storage']['pdfContainerName']}")
    print(f"DI endpoint: {cfg['documentIntelligence']['endpoint']}")
    print(f"Phase: {args.phase}  Force: {args.force}  Concurrency: {args.concurrency}  Vision parallel: {args.vision_parallel}\n")

    print("Listing PDFs...")
    pdfs = list_pdfs(cfg)
    print(f"Found {len(pdfs)} PDFs")

    if args.incremental:
        before = len(pdfs)
        pdfs = [p for p in pdfs if not blob_exists(cfg, f"_dicache/{p}.output.json")]
        print(f"Incremental: {before - len(pdfs)} already cached, {len(pdfs)} to process")

    if not pdfs:
        print("Nothing to process.")
        return

    print()

    phase_func = {
        "di": lambda cfg, name, force: phase_di(cfg, name, force),
        "vision": lambda cfg, name, force: phase_vision(cfg, name, force, args.vision_parallel),
        "output": lambda cfg, name, force: phase_output(cfg, name, force),
        "all": lambda cfg, name, force: process_one_full(cfg, name, force, args.vision_parallel),
    }[args.phase]

    results: list[str] = []
    if args.concurrency <= 1:
        for name in pdfs:
            try:
                r = phase_func(cfg, name, args.force)
            except Exception as exc:
                r = f"  FAIL-crash  {name}: {type(exc).__name__}: {exc}"
            results.append(r)
            print(r)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(phase_func, cfg, name, args.force): name
                for name in pdfs
            }
            for f in as_completed(futures):
                name = futures[f]
                try:
                    r = f.result()
                except Exception as exc:
                    # Don't let one PDF's crash kill the whole batch. The
                    # script is designed to be re-run, so record the failure
                    # and let the rest finish.
                    r = f"  FAIL-crash  {name}: {type(exc).__name__}: {exc}"
                results.append(r)
                print(r)

    ok = sum(1 for r in results if "ok" in r.lower() and "FAIL" not in r)
    skip = sum(1 for r in results if "skip" in r.lower())
    fail = sum(1 for r in results if "FAIL" in r)
    print(f"\nDone: {ok} processed, {skip} skipped, {fail} failed")

    if fail:
        print("\nFailed PDFs (re-run to retry):")
        for r in results:
            if "FAIL" in r:
                print(f"  {r.strip()}")


if __name__ == "__main__":
    main()
