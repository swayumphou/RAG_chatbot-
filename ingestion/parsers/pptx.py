"""Read a PowerPoint file (.pptx): one page per slide (T2.4).

- Page number = slide number (from 1); anchor "slide N".
- Text boxes (also inside grouped shapes), table rows, and speaker notes are
  all included. The slide title, or else its first line, is the heading.
- The document type is "slides": the chunker uses smaller chunks for it.
- A slide without text is skipped; if every slide is empty -> "failed".
"""

from __future__ import annotations

from pathlib import Path

from ingestion.parsers.base import Page, build_parsed_document


def shape_texts(shapes) -> list:
    """The text of every shape: text boxes, table rows, and shapes inside groups."""
    from pptx.shapes.group import GroupShape

    texts = []
    for shape in shapes:
        if isinstance(shape, GroupShape):
            texts.extend(shape_texts(shape.shapes))
        elif getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                if any(cells):
                    texts.append(" | ".join(cells))
        elif getattr(shape, "has_text_frame", False):
            text = shape.text_frame.text.strip()
            if text:
                texts.append(text)
    return texts


def slide_page(slide, number):
    """One slide as a Page, or None when the slide has no text."""
    title_shape = slide.shapes.title
    title = title_shape.text_frame.text.strip() if title_shape is not None else ""
    # python-pptx makes a new object every time, so the title shape is skipped
    # by its id, not by comparing objects.
    title_id = title_shape.shape_id if title_shape is not None else None
    other_shapes = [shape for shape in slide.shapes if shape.shape_id != title_id]

    parts = [title] if title else []
    parts += shape_texts(other_shapes)
    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame
        notes_text = notes.text.strip() if notes is not None else ""
        if notes_text:
            parts.append(f"Notes: {notes_text}")

    text = "\n".join(parts).strip()
    if not text:
        return None
    heading = title or text.splitlines()[0].strip()
    return Page(number=number, text=text, headings=(heading,), anchor=f"slide {number}")


def parse_pptx(path, project):
    from pptx import Presentation

    path = Path(path)
    try:
        deck = Presentation(str(path))
    except Exception:  # a broken file is "failed", not a crash
        return build_parsed_document(
            path, project, [], parser="python-pptx", page_count=0, doc_type="slides"
        )

    slides = list(deck.slides)
    pages = []
    for number, slide in enumerate(slides, start=1):
        page = slide_page(slide, number)
        if page is not None:
            pages.append(page)

    return build_parsed_document(
        path, project, pages, parser="python-pptx", page_count=len(slides), doc_type="slides"
    )
