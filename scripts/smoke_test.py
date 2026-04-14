"""
Post-deploy smoke test.

Triggers an indexer run against the deployed environment, waits for it
to finish, then validates that the built-in Layout skill output paths
our projections rely on actually materialized, and that every
record_type produced at least one document.

Exits non-zero on any failure so a CI job can gate the release on it.

Usage:
    python scripts/smoke_test.py --env dev
    python scripts/smoke_test.py --env prod --wait-minutes 20

Assumptions:
    - `az login` done, or AAD env vars set.
    - The Bicep deployment named `mm-manuals-<env>` has outputs.
    - At least one PDF is already in the PDF container; otherwise this
      script will still pass its status checks but record counts will
      be zero, and the assertion on that will fail loudly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

import httpx
from azure.identity import DefaultAzureCredential

API_VERSION = "2024-05-01-preview"

# Minimum fields we expect populated on at least one record of each
# record_type. Derived from the projection definitions in skillset.json.
# If the built-in Layout skill output paths shift in a given region, the
# corresponding field will be empty for every text record and this
# assertion will catch it.
REQUIRED_FIELDS = {
    "text": [
        "chunk_id",
        "chunk",
        "physical_pdf_page",
        "physical_pdf_pages",
        "header_1",
    ],
    "diagram": ["chunk_id", "figure_id", "diagram_description", "header_1"],
    "table": ["chunk_id", "chunk", "physical_pdf_page", "physical_pdf_pages"],
    "summary": ["chunk_id", "chunk"],
}


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def deployment_outputs(env: str) -> dict:
    raw = run([
        "az", "deployment", "sub", "show",
        "--name", f"mm-manuals-{env}",
        "--query", "properties.outputs",
        "-o", "json",
    ])
    return {k: v["value"] for k, v in json.loads(raw).items()}


def aad_token(scope: str) -> str:
    return DefaultAzureCredential().get_token(scope).token


def run_indexer(endpoint: str, token: str, indexer_name: str) -> None:
    url = f"{endpoint}/indexers/{indexer_name}/run?api-version={API_VERSION}"
    with httpx.Client(timeout=30.0) as c:
        resp = c.post(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code not in (200, 202, 204):
        raise SystemExit(f"indexer run failed: {resp.status_code} {resp.text[:500]}")


def wait_for_indexer(endpoint: str, token: str, indexer_name: str, minutes: int) -> dict:
    """Poll indexer status until the last execution transitions out of
    'inProgress'. Returns the last execution record."""
    url = f"{endpoint}/indexers/{indexer_name}/status?api-version={API_VERSION}"
    deadline = time.time() + minutes * 60
    backoff = 5.0
    with httpx.Client(timeout=30.0) as c:
        while time.time() < deadline:
            resp = c.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            body = resp.json()
            last = body.get("lastResult") or {}
            status = last.get("status")
            print(f"  indexer status: {status}")
            if status in ("success", "transientFailure", "persistentFailure"):
                return last
            time.sleep(backoff)
            backoff = min(backoff * 1.3, 30.0)
    raise SystemExit(f"indexer did not complete within {minutes} minutes")


def record_count(endpoint: str, token: str, index_name: str, filter_expr: str) -> int:
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    body = {"search": "*", "filter": filter_expr, "count": True, "top": 0}
    with httpx.Client(timeout=30.0) as c:
        resp = c.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json().get("@odata.count", 0)


def sample_record(endpoint: str, token: str, index_name: str, filter_expr: str) -> dict | None:
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    body = {"search": "*", "filter": filter_expr, "top": 1}
    with httpx.Client(timeout=30.0) as c:
        resp = c.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    hits = resp.json().get("value", [])
    return hits[0] if hits else None


def assert_populated(record: dict, fields: list[str], record_type: str) -> list[str]:
    missing = []
    for f in fields:
        v = record.get(f)
        if v is None or v == "" or v == 0:
            missing.append(f)
        elif isinstance(v, list) and len(v) == 0:
            missing.append(f)
    if missing:
        return [f"{record_type}: required field(s) empty or missing: {missing}"]
    # Extra cross-field consistency: if both physical_pdf_page{,_end} and
    # physical_pdf_pages are present, the list must cover the endpoints.
    start = record.get("physical_pdf_page")
    end = record.get("physical_pdf_page_end")
    pages = record.get("physical_pdf_pages") or []
    if pages and isinstance(pages, list):
        if start is not None and start not in pages:
            return [f"{record_type}: physical_pdf_pages {pages} missing start={start}"]
        if end is not None and end not in pages:
            return [f"{record_type}: physical_pdf_pages {pages} missing end={end}"]
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--wait-minutes", type=int, default=15)
    ap.add_argument("--skip-run", action="store_true", help="Don't trigger the indexer, just validate what's already in the index")
    args = ap.parse_args()

    outputs = deployment_outputs(args.env)
    endpoint = outputs["searchEndpoint"]
    index_name = outputs["indexName"]
    indexer_name = outputs["indexerName"]

    token = aad_token("https://search.azure.com/.default")

    if not args.skip_run:
        print(f"Triggering indexer {indexer_name} ...")
        run_indexer(endpoint, token, indexer_name)
        print(f"Waiting up to {args.wait_minutes} min for completion ...")
        last = wait_for_indexer(endpoint, token, indexer_name, args.wait_minutes)
        if last.get("status") != "success":
            # Surface errorMessage + first per-document errors.
            print(json.dumps(last, indent=2)[:2000])
            raise SystemExit(f"indexer finished with status={last.get('status')}")
        items = last.get("itemsProcessed", 0)
        errors = len(last.get("errors") or [])
        warnings = len(last.get("warnings") or [])
        print(f"  items processed: {items}  errors: {errors}  warnings: {warnings}")
        if items == 0:
            raise SystemExit("indexer processed 0 items; no PDFs in the container?")

    print("Checking per-record_type counts and schema ...")
    failures: list[str] = []
    for rt, required in REQUIRED_FIELDS.items():
        count = record_count(endpoint, token, index_name, f"record_type eq '{rt}'")
        print(f"  record_type={rt}: {count} record(s)")
        if count == 0:
            failures.append(f"{rt}: zero records in index")
            continue
        sample = sample_record(endpoint, token, index_name, f"record_type eq '{rt}'")
        if sample is None:
            failures.append(f"{rt}: count>0 but sample fetch returned nothing")
            continue
        failures.extend(assert_populated(sample, required, rt))

    # Confirm multi-page text span logic produced at least one spanning chunk.
    spanning = record_count(
        endpoint, token, index_name,
        "record_type eq 'text' and physical_pdf_page_end gt physical_pdf_page",
    )
    print(f"  multi-page text chunks: {spanning}")
    # Not a hard failure (not every corpus has multi-page chunks) but report it.

    if failures:
        print("\nSMOKE TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(2)

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"az cli call failed:\n  cmd: {e.cmd}\n  stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
