"""
Azure AI Search Custom WebApi Skill request/response envelope.

Each route receives:
{
  "values": [
    { "recordId": "0", "data": { ... inputs ... } },
    ...
  ]
}

And must return:
{
  "values": [
    { "recordId": "0", "data": { ... outputs ... }, "errors": [], "warnings": [] },
    ...
  ]
}
"""

import json
import logging
from typing import Callable, Dict, Any

import azure.functions as func

from .config import ConfigError


def handle_skill_request(
    req: func.HttpRequest,
    record_processor: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    values_in = body.get("values", [])
    values_out = []

    for record in values_in:
        record_id = record.get("recordId", "0")
        data_in = record.get("data", {}) or {}
        try:
            data_out = record_processor(data_in) or {}
            values_out.append({
                "recordId": record_id,
                "data": data_out,
                "errors": [],
                "warnings": [],
            })
        except ConfigError as exc:
            # Misconfigured Function App: surface as a clean per-record
            # error instead of a 500. The skillset run will continue and
            # the indexer error column will show exactly what is missing.
            logging.error("record %s config error: %s", record_id, exc)
            values_out.append({
                "recordId": record_id,
                "data": {"processing_status": "config_error"},
                "errors": [{"message": f"ConfigError: {exc}"}],
                "warnings": [],
            })
        except Exception as exc:
            logging.exception("record %s failed: %s", record_id, exc)
            values_out.append({
                "recordId": record_id,
                "data": {"processing_status": "error"},
                "errors": [{"message": f"{type(exc).__name__}: {exc}"}],
                "warnings": [],
            })

    return func.HttpResponse(
        json.dumps({"values": values_out}),
        mimetype="application/json",
        status_code=200,
    )
