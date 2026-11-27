"""Parser contract shared by every format (T2.1, INTERFACES §5.1).

Page anchors are non-negotiable: without them there are no citations, and
without citations the answer layer cannot be audited.

Invariants:

* ``doc_id = f"{project_id}__{filename}"``.
* ``sha256`` is the SHA-256 of the file bytes.
* ``parse_status`` is ``ok``, ``ocr``, or ``failed``.
* A document with zero usable pages is always ``failed`` — never ``ok`` with
  no pages. Silent success on an empty parse is the worst failure mode in a
  retrieval pipeline: the index looks healthy while answering nothing.
* Paged formats (PDF, PPTX) use 1-based page/slide numbers. Formats without
  reliable pages (DOCX, Markdown) use ``number=0`` and carry the section
  anchor ``"section N"`` in :attr:`Page.anchor`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover
    from langchain_core.documents import Document

ParseStatus = Literal["ok", "ocr", "failed"]

DOC_TYPES = ("report", "slides", "poster", "notes")


@dataclass(frozen=True)
class Page:
    """One page (PDF), slide (PPTX), or section (DOCX/Markdown)."""

    number: int
    text: str
    headings: tuple[str, ...] = ()
    anchor: str = ""


@dataclass(frozen=True)
class ParsedDocument:
    doc_id: str
    project_id: str
    doc_type: str
    file_path: str
    sha256: str
    pages: tuple[Page, ...]
    parse_status: ParseStatus
    parser: str
    page_count: int

    def to_langchain_documents(self, project: Mapping[str, Any]) -> list[Document]:
        """One LangChain ``Document`` per page with the FR-02 metadata."""

        from langchain_core.documents import Document

        project_id = str(project.get("project_id") or self.project_id)
        return [
            Document(
                page_content=page.text,
                metadata={
                    "doc_id": self.doc_id,
                    "project_id": project_id,
                    "doc_type": self.doc_type,
                    "page_number": page.number,
                    "anchor": page.anchor or f"p.{page.number}",
                    "parse_status": self.parse_status,
                    "parser": self.parser,
                    "sha256": self.sha256,
                },
            )
            for page in self.pages
            if page.text.strip()
        ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def make_doc_id(project_id: str, path: Path) -> str:
    return f"{project_id}__{Path(path).name}"


def infer_doc_type(path: Path, project: Mapping[str, Any]) -> str:
    """Document type: explicit project value, then filename keyword, then extension.

    ``report.md`` is a report; ``notes.md`` is notes; any ``.pptx`` is slides.
    """

    explicit = str(project.get("doc_type") or "").strip().lower()
    if explicit in DOC_TYPES:
        return explicit
    path = Path(path)
    stem = path.stem.lower()
    for keyword in ("slides", "poster", "notes", "report"):
        if keyword in stem:
            return keyword
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return "slides"
    if suffix in (".md", ".txt"):
        return "notes"
    return "report"


def build_parsed_document(
    path: Path,
    project: Mapping[str, Any],
    pages: Iterable[Page],
    *,
    parser: str,
    page_count: int,
    used_ocr: bool = False,
    doc_type: str | None = None,
) -> ParsedDocument:
    """Assemble a :class:`ParsedDocument`, enforcing the empty-document rule."""

    path = Path(path)
    project_id = str(project["project_id"])
    usable = tuple(p for p in pages if p.text and p.text.strip())
    if not usable:
        status: ParseStatus = "failed"
    else:
        status = "ocr" if used_ocr else "ok"
    return ParsedDocument(
        doc_id=make_doc_id(project_id, path),
        project_id=project_id,
        doc_type=doc_type or infer_doc_type(path, project),
        file_path=str(path),
        sha256=sha256_file(path),
        pages=usable,
        parse_status=status,
        parser=parser,
        page_count=page_count,
    )


def enforce_contract(parsed: ParsedDocument) -> ParsedDocument:
    """Downgrade any ``ok``/``ocr`` document without usable pages to ``failed``."""

    usable = tuple(p for p in parsed.pages if p.text and p.text.strip())
    if not usable:
        return replace(parsed, pages=(), parse_status="failed")
    if len(usable) != len(parsed.pages):
        return replace(parsed, pages=usable)
    return parsed
