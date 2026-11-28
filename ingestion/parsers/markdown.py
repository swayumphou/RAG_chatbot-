"""Read a Markdown (.md) or text (.txt) file: each "#" heading starts a section (T2.5).

- "#" to "######" headings start a new section; the heading line is kept.
- A "#" inside a ``` code block is not a heading.
- Sections get number=0 and the anchor "section 1", "section 2", ...
- A heading with nothing under it is joined to the next heading.
- Short paragraphs are kept here; the chunker drops chunks under 80 characters.
"""

from __future__ import annotations

import re
from pathlib import Path

from ingestion.parsers.base import Page, build_parsed_document

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")  # "## Methods"
CODE_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def split_sections(text) -> list:
    pages = []
    headings = []  # heading texts of the section being collected
    heading_lines = []  # the same headings as written, e.g. "## Methods"
    body = []
    in_code = False

    def finish_section():
        content = "\n".join(body).strip()
        if content:
            number = len(pages) + 1
            section_text = "\n".join(heading_lines + [content])
            pages.append(
                Page(
                    number=0,
                    text=section_text,
                    headings=tuple(headings),
                    anchor=f"section {number}",
                )
            )
        headings.clear()
        heading_lines.clear()
        body.clear()

    for line in text.splitlines():
        if CODE_FENCE.match(line):
            in_code = not in_code
            body.append(line)
            continue
        match = HEADING.match(line) if not in_code else None
        if match:
            if "\n".join(body).strip():  # the previous section has text: finish it
                finish_section()
            headings.append(match.group(2).strip())
            heading_lines.append(line.strip())
        else:
            body.append(line.rstrip())
    finish_section()
    return pages


def parse_markdown(path, project):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return build_parsed_document(path, project, [], parser="markdown", page_count=0)
    pages = split_sections(text)
    return build_parsed_document(path, project, pages, parser="markdown", page_count=len(pages))
