"""
Render the Azure AI Search artifacts (datasource, index, skillset,
indexer) with environment-specific values and PUT them to the target
Search service using AAD auth.

Inputs come from Bicep outputs (via `az deployment sub show`) and from
the Function App's function key (fetched via `az functionapp keys list`).

Usage:
    python scripts/deploy_search.py --env dev

Requires:
  - Azure CLI login with Contributor + Search Service Contributor on the
    target subscription (and Search Index Data Contributor on the service).
  - azure-identity installed locally.

Everything is idempotent: PUT on each resource updates in place. The
script prints the resolved values it substituted so the operator can
eyeball them before the index is rebuilt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIR = REPO_ROOT / "search"
API_VERSION = "2024-05-01-preview"

ARTIFACTS = [
    ("datasources", "mm-manuals-ds", SEARCH_DIR / "datasource.json"),
    ("indexes", "mm-manuals-index", SEARCH_DIR / "index.json"),
    ("skillsets", "mm-manuals-skillset", SEARCH_DIR / "skillset.json"),
    ("indexers", "mm-manuals-indexer", SEARCH_DIR / "indexer.json"),
]


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def deployment_outputs(env: str) -> dict:
    """Read the subscription-scoped Bicep deployment outputs."""
    deployment_name = f"mm-manuals-{env}"
    raw = run([
        "az", "deployment", "sub", "show",
        "--name", deployment_name,
        "--query", "properties.outputs",
        "-o", "json",
    ])
    return {k: v["value"] for k, v in json.loads(raw).items()}


def function_key(resource_group: str, function_app_name: str) -> str:
    raw = run([
        "az", "functionapp", "keys", "list",
        "-g", resource_group,
        "-n", function_app_name,
        "-o", "json",
    ])
    keys = json.loads(raw)
    # Prefer the default host key (works across all functions).
    return keys["functionKeys"].get("default") or next(iter(keys["functionKeys"].values()))


def render(body: str, mapping: dict[str, str]) -> str:
    """Replace every <PLACEHOLDER> token. Unknown placeholders are an error
    — silent substitution failures in production would be painful to
    diagnose after the fact."""
    out = body
    for placeholder, value in mapping.items():
        out = out.replace(placeholder, value)
    remaining = re.findall(r"<[A-Z_][A-Z0-9_]*>", out)
    if remaining:
        raise SystemExit(
            f"Unrendered placeholders remain: {sorted(set(remaining))}. "
            f"Add them to the mapping in scripts/deploy_search.py."
        )
    return out


def put_artifact(
    endpoint: str,
    token: str,
    collection: str,
    name: str,
    body_json: dict,
) -> None:
    url = f"{endpoint}/{collection}/{name}?api-version={API_VERSION}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.put(url, json=body_json, headers=headers)
        if resp.status_code not in (200, 201, 204):
            raise SystemExit(
                f"PUT {collection}/{name} failed: {resp.status_code} {resp.text[:600]}"
            )
    print(f"  ok  {collection}/{name}  ({resp.status_code})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help="dev|staging|prod")
    ap.add_argument("--run-indexer", action="store_true", help="POST indexer/run after deploy")
    args = ap.parse_args()

    print(f"Reading Bicep outputs for env={args.env} ...")
    outputs = deployment_outputs(args.env)
    rg = outputs["resourceGroupName"]
    func_app = outputs["functionAppName"]
    func_host = outputs["functionAppHost"]
    search_endpoint = outputs["searchEndpoint"]
    aoai_endpoint = outputs["aoaiEndpoint"]
    ais_subdomain = outputs["aiServicesSubdomainUrl"]
    storage_id = outputs["storageAccountId"]
    container = outputs["pdfContainerName"]

    print(f"Fetching function key for {func_app} ...")
    fkey = function_key(rg, func_app)

    mapping = {
        "<STORAGE_RESOURCE_ID>": storage_id,
        "<STORAGE_CONTAINER_NAME>": container,
        "<STORAGE_CONNECTION_STRING>": f"ResourceId={storage_id};",
        "<FUNCTION_APP_HOST>": func_host,
        "<FUNCTION_KEY>": fkey,
        "<AOAI_ENDPOINT>": aoai_endpoint.rstrip("/"),
        "<AOAI_EMBED_DEPLOYMENT>": os.environ.get("AOAI_EMBED_DEPLOYMENT", "text-embedding-ada-002"),
        "<AI_SERVICES_SUBDOMAIN_URL>": ais_subdomain.rstrip("/"),
    }

    print("Substitutions:")
    for k, v in mapping.items():
        if k == "<FUNCTION_KEY>":
            v = v[:4] + "…" + v[-4:]
        print(f"  {k} -> {v}")

    print("Acquiring AAD token for Search ...")
    cred = DefaultAzureCredential()
    token = cred.get_token("https://search.azure.com/.default").token

    print(f"PUTting artifacts to {search_endpoint} ...")
    for collection, name, path in ARTIFACTS:
        raw = path.read_text(encoding="utf-8")
        rendered = render(raw, mapping)
        body = json.loads(rendered)
        put_artifact(search_endpoint, token, collection, name, body)

    if args.run_indexer:
        print("Triggering indexer run ...")
        url = f"{search_endpoint}/indexers/mm-manuals-indexer/run?api-version={API_VERSION}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code not in (200, 202, 204):
                raise SystemExit(f"indexer run failed: {resp.status_code} {resp.text[:400]}")
        print("  indexer run accepted")

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"az cli call failed:\n  cmd: {e.cmd}\n  stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
