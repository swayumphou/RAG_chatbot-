"""OCR for scanned PDF pages, using Tesseract with English + Khmer.

OCR is "best effort": if pytesseract, Pillow, the Tesseract program, or the
Khmer language data is missing, we log a warning and return "" instead of
crashing. A document where every page is empty is then marked "failed".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DPI = 300
LANG = "eng+khm"

_warned = False


def _warn(message: str):
    """Log the first OCR problem only, so the log is not flooded."""
    global _warned
    if not _warned:
        logger.warning(message)
        _warned = True


def ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_page(page, lang: str = LANG) -> str:
    """Render one PyMuPDF page as an image and read its text with Tesseract."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as error:
        _warn(f"OCR is not available ({error}); scanned pages will have no text")
        return ""

    try:
        pixmap = page.get_pixmap(dpi=DPI)
        mode = "RGBA" if pixmap.alpha else "RGB"
        image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
        return pytesseract.image_to_string(image, lang=lang)
    except Exception as error:
        if lang != "eng":
            # Most often the Khmer language data is not installed. Try English only.
            _warn(f"OCR with '{lang}' failed ({error}); retrying with 'eng'")
            return ocr_page(page, lang="eng")
        _warn(f"OCR failed: {error}")
        return ""
