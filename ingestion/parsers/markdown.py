"""Markdown / plain-text parser: ATX headings start sections (T2.5).

A deterministic heading parser is used (allowed by T2.5) because it keeps the
heading line in the section text and never treats ``#`` inside a fenced code
block as a heading. ``.txt`` files go through the same path and usually yield
a single section.

* ``#`` .. ``######`` headings start a new section; the heading line is kept.
* ``page_number=0``; anchor ``"section N"`` (1-based, emitted sections only).
* A heading with no body directly before another heading is stacked onto the
  next section; empty sections are never emitted.
* Short paragraphs (for example a 20-character line) survive parsing; the
  chunker drops anything under ``MIN_CHUNK_CHARS`` later.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ingestion.parsers.base import Page, ParsedDocument, build_parsed_document

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def split_sections(text: str) -> list[Page]:
    pages: list[Page] = []
    headings: list[str] = []
    heading_lines: list[str] = []
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal headings, heading_lines, body
        content = "\n".join(body).strip()
        if content:
            number = len(pages) + 1
            section_text = "\n".join([*heading_lines, content])
            anchor = f"section {number}"
            pages.append(Page(number=0, text=section_text, headings=tuple(headings), anchor=anchor))
        headings, heading_lines, body = [], [], []

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            if "\n".join(body).strip():
                flush()
            headings.append(match.group(2).strip())
            heading_lines.append(line.strip())
            continue
        body.append(line.rstrip())
    flush()
    return pages


def parse_markdown(path: Path, project: Mapping[str, Any]) -> ParsedDocument:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return build_parsed_document(path, project, [], parser="markdown", page_count=0)
    pages = split_sections(text)
    return build_parsed_document(path, project, pages, parser="markdown", page_count=len(pages))
