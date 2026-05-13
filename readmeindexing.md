import logging

import azure.functions as func
from shared.diagram import process_diagram
from shared.page_label import process_page_label
from shared.process_document import process_document
from shared.process_table import process_table
from shared.semantic import process_semantic_string
from shared.skill_io import handle_skill_request
from shared.summary import process_doc_summary

# Defensive auto_heal import. If anything in shared/auto_heal.py fails to
# import (missing dep, syntax error, etc.), we log it but keep the rest of
# the function app working. Otherwise a single buggy module would prevent
# all 6 indexer skills from being registered.
try:
    from shared.auto_heal import auto_heal_run
    _AUTO_HEAL_AVAILABLE = True
except Exception as _exc:
    logging.exception("auto_heal: failed to import; timer disabled: %s", _exc)
    _AUTO_HEAL_AVAILABLE = False

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.timer_trigger(schedule="0 */30 * * * *", arg_name="timer", run_on_startup=False)
def auto_heal_timer(timer: func.TimerRequest) -> None:
    """Self-heal stuck blobs every 30 min."""
    if not _AUTO_HEAL_AVAILABLE:
        logging.warning("auto_heal: module not loaded -- skipping")
        return
    logging.info("auto_heal: timer fired")
    try:
        auto_heal_run()
    except Exception as exc:
        logging.exception("auto_heal: unhandled error: %s", exc)


@app.route(route="extract-page-label", methods=["POST"])
def extract_page_label(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("extract-page-label invoked")
    return handle_skill_request(req, process_page_label)


@app.route(route="build-semantic-string", methods=["POST"])
def build_semantic_string(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("build-semantic-string invoked")
    return handle_skill_request(req, process_semantic_string)


@app.route(route="analyze-diagram", methods=["POST"])
def analyze_diagram(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("analyze-diagram invoked")
    return handle_skill_request(req, process_diagram)


@app.route(route="build-doc-summary", methods=["POST"])
def build_doc_summary(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("build-doc-summary invoked")
    return handle_skill_request(req, process_doc_summary)


@app.route(route="process-document", methods=["POST"])
def process_document_route(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("process-document invoked")
    return handle_skill_request(req, process_document)


@app.route(route="shape-table", methods=["POST"])
def shape_table(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("shape-table invoked")
    return handle_skill_request(req, process_table)
