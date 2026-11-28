"""Read a Word file (.docx): headings, paragraphs, and tables in order (T2.3).

- A "Title" or "Heading N" paragraph starts a new section. The section text is
  the heading followed by its paragraphs and tables.
- A table is written row by row as "cell | cell | cell".
- Word files have no reliable page numbers, so every section gets number=0
  and the anchor "section 1", "section 2", ...
- A heading with nothing under it is joined to the next heading
  ("Methodology" + "3.1 Data") instead of becoming an empty section.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.parsers.base import Page, build_parsed_document


def is_heading(paragraph) -> bool:
    style = paragraph.style.name if paragraph.style is not None else ""
    return style == "Title" or style.startswith("Heading")


def table_text(table) -> str:
    lines = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            if not cells or cells[-1] != text:  # a merged cell repeats its text: keep it once
                cells.append(text)
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def parse_docx(path, project):
    from docx import Document as open_docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    path = Path(path)
    try:
        document = open_docx(str(path))
    except Exception:  # a broken file is "failed", not a crash
        return build_parsed_document(path, project, [], parser="python-docx", page_count=0)

    pages = []
    headings = []  # headings of the section being collected
    body = []  # paragraphs and tables of that section

    def finish_section():
        if body:
            number = len(pages) + 1
            text = "\n".join(headings + ["\n\n".join(body)])
            pages.append(
                Page(number=0, text=text, headings=tuple(headings), anchor=f"section {number}")
            )
        headings.clear()
        body.clear()

    for block in document.iter_inner_content():  # paragraphs and tables, in order
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            if not text:
                continue
            if is_heading(block):
                if body:  # the previous section is complete
                    finish_section()
                headings.append(text)
            else:
                body.append(text)
        elif isinstance(block, Table):
            text = table_text(block)
            if text:
                body.append(text)
    finish_section()

    return build_parsed_document(path, project, pages, parser="python-docx", page_count=len(pages))
