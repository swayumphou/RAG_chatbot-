"""PDF parser: PyMuPDF text blocks, font-size headings, OCR fallback (T2.2).

* Text comes from ``page.get_text("blocks")`` (text blocks only), joined with
  blank lines so the chunker can split on paragraph boundaries.
* Short blocks inside the top/bottom margin band are treated as running
  headers/footers (page numbers, report title) and dropped.
* Headings are lines whose font size is noticeably larger (x1.15) than the
  page's dominant body size.
* A page with fewer than 20 non-whitespace characters is rendered at 300 dpi
  and OCR'd with Tesseract ``eng+khm``; the document becomes ``ocr``.
* A page with no text after extraction/OCR is skipped, but ``page_count``
  remains the original PDF page count. No usable page -> ``failed``.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ingestion.parsers.base import Page, ParsedDocument, build_parsed_document
from ingestion.parsers.ocr import ocr_page

logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 20
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 150
MARGIN_BAND = 0.06  # fraction of page height treated as header/footer band
MAX_MARGIN_TEXT = 80


def _import_pymupdf():
    try:
        import pymupdf
    except ImportError:  # older wheels only expose the legacy name
        import fitz as pymupdf
    return pymupdf


def _in_margin(y0: float, y1: float, height: float) -> bool:
    if height <= 0:
        return False
    return y1 <= height * MARGIN_BAND or y0 >= height * (1.0 - MARGIN_BAND)


def _non_ws_len(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _page_text(page: Any) -> str:
    height = float(page.rect.height)
    parts: list[str] = []
    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text, _block_no, block_type = block[:7]
        if block_type != 0:
            continue  # image block
        text = (text or "").strip()
        if not text:
            continue
        if _in_margin(y0, y1, height) and len(text) <= MAX_MARGIN_TEXT:
            continue  # running header / footer / page number
        parts.append(text)
    return "\n\n".join(parts)


def detect_headings(page: Any) -> tuple[str, ...]:
    """Lines set in a font noticeably larger than the dominant body size."""

    height = float(page.rect.height)
    data = page.get_text("dict", sort=True)
    weight: Counter[float] = Counter()
    lines: list[tuple[str, float, float, float]] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            # Join spans exactly as PyMuPDF does in the block text, so the
            # chunker can find this heading in the page text with str.find().
            text = "".join(s["text"] for s in spans).strip()
            size = max(round(float(s.get("size", 0.0)), 1) for s in spans)
            for span in spans:
                weight[round(float(span.get("size", 0.0)), 1)] += len(span["text"].strip())
            bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
            lines.append((text, size, float(bbox[1]), float(bbox[3])))
    if not weight:
        return ()
    body_size = weight.most_common(1)[0][0]
    headings = [
        text
        for text, size, y0, y1 in lines
        if size >= body_size * HEADING_SIZE_RATIO
        and len(text) <= MAX_HEADING_CHARS
        and not _in_margin(y0, y1, height)
    ]
    return tuple(dict.fromkeys(headings))


def parse_pdf(path: Path, project: Mapping[str, Any]) -> ParsedDocument:
    path = Path(path)
    pymupdf = _import_pymupdf()
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:  # noqa: BLE001 - corrupt/encrypted file -> failed
        logger.warning("Could not open PDF %s: %s", path.name, exc)
        return build_parsed_document(path, project, [], parser="pymupdf", page_count=0)

    pages: list[Page] = []
    used_ocr = False
    with document:
        page_count = document.page_count
        for number, page in enumerate(document, start=1):
            text = _page_text(page)
            headings = detect_headings(page) if text else ()
            if _non_ws_len(text) < MIN_PAGE_CHARS:
                ocr_text = ocr_page(page).strip()
                if ocr_text:
                    text, headings, used_ocr = ocr_text, (), True
            if not text.strip():
                continue
            pages.append(Page(number=number, text=text, headings=headings, anchor=f"p.{number}"))

    return build_parsed_document(
        path,
        project,
        pages,
        parser="pymupdf+ocr" if used_ocr else "pymupdf",
        page_count=page_count,
        used_ocr=used_ocr,
    )
