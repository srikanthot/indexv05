"""
Crop figure regions from a PDF using PyMuPDF (fitz).

DI's bounding polygons are returned in INCHES against the page. PyMuPDF
operates in points (1 inch = 72 points). We render the cropped region as a
PNG at 200 DPI for the vision model.
"""

import base64
import io
import logging
from typing import List, Dict, Any, Tuple

import fitz  # PyMuPDF


RENDER_DPI = 200
INCH_TO_PT = 72.0


def _polygon_bbox_inches(polygon: List[float]) -> Tuple[float, float, float, float]:
    """
    DI returns polygons as a flat list [x1,y1,x2,y2,...] in inches.
    Returns (x0, y0, x1, y1) in inches.
    """
    xs = polygon[0::2]
    ys = polygon[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def crop_figure_png_b64(
    pdf_bytes: bytes,
    page_number_1based: int,
    polygon_inches: List[float],
    pad_inches: float = 0.05,
) -> Tuple[str, Dict[str, float]]:
    """
    Render the cropped figure region as a base64 PNG. Returns (b64, bbox_dict).
    bbox_dict is a JSON-serializable description of the figure location:
      {page, x_in, y_in, w_in, h_in}
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_index = page_number_1based - 1
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"page {page_number_1based} out of range (doc has {doc.page_count})")
        page = doc.load_page(page_index)

        x0_in, y0_in, x1_in, y1_in = _polygon_bbox_inches(polygon_inches)
        x0_in -= pad_inches
        y0_in -= pad_inches
        x1_in += pad_inches
        y1_in += pad_inches

        x0_pt = max(0.0, x0_in * INCH_TO_PT)
        y0_pt = max(0.0, y0_in * INCH_TO_PT)
        x1_pt = min(page.rect.width, x1_in * INCH_TO_PT)
        y1_pt = min(page.rect.height, y1_in * INCH_TO_PT)

        clip = fitz.Rect(x0_pt, y0_pt, x1_pt, y1_pt)
        zoom = RENDER_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("ascii")

        bbox = {
            "page": page_number_1based,
            "x_in": round(x0_in + pad_inches, 4),
            "y_in": round(y0_in + pad_inches, 4),
            "w_in": round((x1_in - x0_in) - 2 * pad_inches, 4),
            "h_in": round((y1_in - y0_in) - 2 * pad_inches, 4),
        }
        return b64, bbox
    finally:
        doc.close()
