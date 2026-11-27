"""Pick the right reader for a file by its extension.

    parse_document(Path("report.pdf"), project) -> ParsedDocument

Each reader is imported only when it is needed, so reading a Markdown file
never needs PyMuPDF to be installed.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.parsers.base import Page, ParsedDocument, enforce_contract


def _pdf(path, project):
    from ingestion.parsers.pdf import parse_pdf

    return parse_pdf(path, project)


def _docx(path, project):
    from ingestion.parsers.docx import parse_docx

    return parse_docx(path, project)


def _pptx(path, project):
    from ingestion.parsers.pptx import parse_pptx

    return parse_pptx(path, project)


def _markdown(path, project):
    from ingestion.parsers.markdown import parse_markdown

    return parse_markdown(path, project)


BUILT_IN = {".pdf": _pdf, ".docx": _docx, ".pptx": _pptx, ".md": _markdown, ".txt": _markdown}

# extension -> function(path, project) -> ParsedDocument
PARSERS = dict(BUILT_IN)


def register_parser(extension: str, parser):
    """Add or replace a reader. Tests use this to plug in a fake reader."""
    PARSERS[extension.lower()] = parser


def reset_registry():
    PARSERS.clear()
    PARSERS.update(BUILT_IN)


def parse_document(path, project) -> ParsedDocument:
    """Read one document. Raises ValueError for an unsupported file type."""
    path = Path(path)
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(
            f"Unsupported file type '{path.suffix or '(none)'}' for '{path.name}'. "
            f"Supported: {', '.join(sorted(PARSERS))}"
        )
    # A reader may never say "ok" with no pages; enforce_contract fixes that.
    return enforce_contract(parser(path, project))


__all__ = ["PARSERS", "Page", "ParsedDocument", "parse_document", "register_parser"]
