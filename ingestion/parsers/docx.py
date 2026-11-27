"""DOCX parser: headings and tables in document order (T2.3).

* Blocks are read in order with ``Document.iter_inner_content()``.
* A ``Title``/``Heading N`` paragraph starts a new section; the section text
  holds the heading followed by its paragraphs and tables.
* Tables render row by row as ``cell | cell | cell``.
* DOCX has no reliable page numbers: every section has ``number=0`` and the
  anchor ``"section N"`` (1-based, counting emitted sections).
* A heading with no body directly before another heading is stacked onto the
  next section (``Methodology`` -> ``3.1 Data``) instead of being emitted as
  an empty section; a document with no body text is ``failed``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ingestion.parsers.base import Page, ParsedDocument, build_parsed_document


def _is_heading(paragraph: Any) -> bool:
    style = getattr(getattr(paragraph, "style", None), "name", "") or ""
    return style == "Title" or style.startswith("Heading")


def _table_text(table: Any) -> str:
    lines: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            if not cells or cells[-1] != text:  # merged cells repeat their text
                cells.append(text)
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


class _SectionBuilder:
    """Accumulates heading + body blocks and emits non-empty sections."""

    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.headings: list[str] = []
        self.body: list[str] = []

    def heading(self, text: str) -> None:
        if self.body:
            self.flush()
        self.headings.append(text)  # stacks onto a heading-only section

    def add(self, text: str) -> None:
        if text.strip():
            self.body.append(text.strip())

    def flush(self) -> None:
        if self.body:
            number = len(self.pages) + 1
            text = "\n".join([*self.headings, "\n\n".join(self.body)])
            self.pages.append(
                Page(number=0, text=text, headings=tuple(self.headings), anchor=f"section {number}")
            )
        self.headings, self.body = [], []


def parse_docx(path: Path, project: Mapping[str, Any]) -> ParsedDocument:
    from docx import Document as load_docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    path = Path(path)
    try:
        document = load_docx(str(path))
    except Exception:  # noqa: BLE001 - corrupt file -> failed
        return build_parsed_document(path, project, [], parser="python-docx", page_count=0)

    builder = _SectionBuilder()
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            if not text:
                continue
            if _is_heading(block):
                builder.heading(text)
            else:
                builder.add(text)
        elif isinstance(block, Table):
            builder.add(_table_text(block))
    builder.flush()

    return build_parsed_document(
        path, project, builder.pages, parser="python-docx", page_count=len(builder.pages)
    )
