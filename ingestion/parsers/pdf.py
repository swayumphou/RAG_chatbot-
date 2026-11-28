"""Read a PDF page by page with PyMuPDF; scanned pages go through OCR (T2.2).

For each page:
1. Take the text blocks. Short blocks in the top or bottom 6% of the page are
   running headers, footers, or page numbers, so they are dropped.
2. Find the headings: lines in a font at least 15% bigger than the normal text.
3. A page with fewer than 20 letters is probably a scan: run OCR on it.
4. A page that still has no text is skipped. page_count stays the real
   number of pages in the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ingestion.parsers.base import Page, build_parsed_document
from ingestion.parsers.ocr import ocr_page

logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 20  # fewer letters than this -> try OCR
HEADING_SIZE_RATIO = 1.15  # a heading's font is this much bigger than body text
MAX_HEADING_CHARS = 150
MARGIN_BAND = 0.06  # top and bottom 6% of the page: header/footer area
MAX_MARGIN_TEXT = 80  # longer text in the margin is kept (it is real content)


def load_pymupdf():
    try:
        import pymupdf
    except ImportError:  # older versions only have the old name
        import fitz as pymupdf
    return pymupdf


def in_margin(top, bottom, page_height) -> bool:
    """True when a line sits in the header or footer band of the page."""
    if page_height <= 0:
        return False
    return bottom <= page_height * MARGIN_BAND or top >= page_height * (1 - MARGIN_BAND)


def count_letters(text) -> int:
    """Number of characters that are not spaces or line breaks."""
    return len("".join(text.split()))


def page_text(page) -> str:
    """The page's text blocks, without headers and footers, separated by blank lines."""
    height = page.rect.height
    parts = []
    for block in page.get_text("blocks", sort=True):
        top, bottom, text, block_type = block[1], block[3], block[4], block[6]
        if block_type != 0:
            continue  # an image, not text
        text = (text or "").strip()
        if not text:
            continue
        if in_margin(top, bottom, height) and len(text) <= MAX_MARGIN_TEXT:
            continue  # running header, footer, or page number
        parts.append(text)
    return "\n\n".join(parts)


def detect_headings(page) -> tuple:
    """Lines written in a font clearly bigger than the page's normal text."""
    height = page.rect.height
    letters_per_size = {}  # font size -> how many letters use it
    lines = []  # (text, size, top, bottom)

    for block in page.get_text("dict", sort=True).get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            # Join the spans the same way PyMuPDF does, so the heading can be
            # found again in the page text.
            text = "".join(span["text"] for span in spans).strip()
            size = max(round(span.get("size", 0.0), 1) for span in spans)
            for span in spans:
                span_size = round(span.get("size", 0.0), 1)
                letters_per_size[span_size] = letters_per_size.get(span_size, 0) + len(
                    span["text"].strip()
                )
            box = line.get("bbox", (0, 0, 0, 0))
            lines.append((text, size, box[1], box[3]))

    if not letters_per_size:
        return ()
    body_size = max(letters_per_size, key=letters_per_size.get)  # the most-used size

    headings = []
    for text, size, top, bottom in lines:
        big_enough = size >= body_size * HEADING_SIZE_RATIO
        short_enough = len(text) <= MAX_HEADING_CHARS
        in_body = not in_margin(top, bottom, height)
        if big_enough and short_enough and in_body and text not in headings:
            headings.append(text)
    return tuple(headings)


def parse_pdf(path, project):
    path = Path(path)
    pymupdf = load_pymupdf()
    try:
        document = pymupdf.open(str(path))
    except Exception as error:  # a broken or encrypted file is "failed", not a crash
        logger.warning("Could not open PDF %s: %s", path.name, error)
        return build_parsed_document(path, project, [], parser="pymupdf", page_count=0)

    pages = []
    used_ocr = False
    with document:
        page_count = document.page_count
        for number, page in enumerate(document, start=1):
            text = page_text(page)
            headings = detect_headings(page) if text else ()

            if count_letters(text) < MIN_PAGE_CHARS:  # probably a scanned page
                ocr_text = ocr_page(page).strip()
                if ocr_text:
                    text, headings, used_ocr = ocr_text, (), True

            if text.strip():
                pages.append(
                    Page(number=number, text=text, headings=headings, anchor=f"p.{number}")
                )

    parser = "pymupdf+ocr" if used_ocr else "pymupdf"
    return build_parsed_document(
        path, project, pages, parser=parser, page_count=page_count, used_ocr=used_ocr
    )
