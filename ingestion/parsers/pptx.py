"""PPTX parser: one page per slide, with tables and speaker notes (T2.4).

* ``number`` is the 1-based slide number; anchor ``"slide N"``.
* Text frames (including inside group shapes), table cells, and speaker notes
  are included. The slide title (or the first non-empty line) is the heading.
* ``doc_type="slides"``; the chunker uses the 300/50 splitter and marks the
  chunks non-primary.
* A slide without text is skipped; if every slide is empty -> ``failed``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ingestion.parsers.base import Page, ParsedDocument, build_parsed_document


def _shape_texts(shapes: Any) -> Iterator[str]:
    from pptx.shapes.group import GroupShape

    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from _shape_texts(shape.shapes)
            continue
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                if any(cells):
                    yield " | ".join(cells)
            continue
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text:
                yield text


def _slide_page(slide: Any, number: int) -> Page | None:
    title_shape = slide.shapes.title
    title = title_shape.text_frame.text.strip() if title_shape is not None else ""
    # python-pptx returns a new proxy object on every access, so the title is
    # excluded by shape_id rather than by identity.
    title_id = title_shape.shape_id if title_shape is not None else None
    parts: list[str] = [title] if title else []
    for text in _shape_texts(s for s in slide.shapes if s.shape_id != title_id):
        parts.append(text)

    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame
        notes_text = notes.text.strip() if notes is not None else ""
        if notes_text:
            parts.append(f"Notes: {notes_text}")

    text = "\n".join(parts).strip()
    if not text:
        return None
    heading = title or next(line.strip() for line in text.splitlines() if line.strip())
    return Page(number=number, text=text, headings=(heading,), anchor=f"slide {number}")


def parse_pptx(path: Path, project: Mapping[str, Any]) -> ParsedDocument:
    from pptx import Presentation

    path = Path(path)
    try:
        deck = Presentation(str(path))
    except Exception:  # noqa: BLE001 - corrupt file -> failed
        return build_parsed_document(
            path, project, [], parser="python-pptx", page_count=0, doc_type="slides"
        )

    pages: list[Page] = []
    slides = list(deck.slides)
    for number, slide in enumerate(slides, start=1):
        page = _slide_page(slide, number)
        if page is not None:
            pages.append(page)

    return build_parsed_document(
        path, project, pages, parser="python-pptx", page_count=len(slides), doc_type="slides"
    )
