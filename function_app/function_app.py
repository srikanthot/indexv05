import logging
import azure.functions as func

from shared.skill_io import handle_skill_request
from shared.page_label import process_page_label
from shared.diagram import process_diagram
from shared.semantic import process_semantic_string
from shared.summary import process_doc_summary

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


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
