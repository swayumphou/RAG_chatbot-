"""What every parser returns: a ParsedDocument made of Pages (T2.1, INTERFACES §5.1).

Rules every parser follows:
- doc_id is "<project_id>__<file name>", for example "dse2024-001__report.pdf".
- sha256 is the hash of the file's bytes (so we notice when a file changes).
- parse_status is "ok", "ocr" (text came from OCR), or "failed".
- A document with no usable page is ALWAYS "failed", never "ok". An empty
  "ok" would make the index look healthy while it can answer nothing.
- PDF pages and PPTX slides are numbered from 1. DOCX and Markdown have no
  real pages: they use number=0 and the anchor "section N".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

DOC_TYPES = ("report", "slides", "poster", "notes")


@dataclass(frozen=True)
class Page:
    """One PDF page, one slide, or one DOCX/Markdown section."""

    number: int  # page or slide number; 0 for DOCX/Markdown sections
    text: str
    headings: tuple = ()  # headings found on this page, in order
    anchor: str = ""  # "p.4", "slide 2", or "section 3"


@dataclass(frozen=True)
class ParsedDocument:
    doc_id: str
    project_id: str
    doc_type: str  # report, slides, poster, or notes
    file_path: str
    sha256: str
    pages: tuple  # only pages that have text
    parse_status: str  # ok, ocr, or failed
    parser: str  # which library read it, e.g. "pymupdf"
    page_count: int  # pages in the original file (also the empty ones)

    def to_langchain_documents(self, project):
        """One LangChain Document per page, with the page's metadata."""
        from langchain_core.documents import Document

        documents = []
        for page in self.pages:
            if not page.text.strip():
                continue
            metadata = {
                "doc_id": self.doc_id,
                "project_id": project.get("project_id") or self.project_id,
                "doc_type": self.doc_type,
                "page_number": page.number,
                "anchor": page.anchor or f"p.{page.number}",
                "parse_status": self.parse_status,
                "parser": self.parser,
                "sha256": self.sha256,
            }
            documents.append(Document(page_content=page.text, metadata=metadata))
        return documents


def sha256_file(path) -> str:
    """SHA-256 of a file, read 1 MB at a time."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def make_doc_id(project_id, path) -> str:
    return f"{project_id}__{Path(path).name}"


def infer_doc_type(path, project) -> str:
    """The document type: from the project row, else the file name, else the extension.

    "report.md" is a report, "notes.md" is notes, and every .pptx is slides.
    """
    explicit = str(project.get("doc_type") or "").strip().lower()
    if explicit in DOC_TYPES:
        return explicit

    name = Path(path).stem.lower()
    for keyword in ("slides", "poster", "notes", "report"):
        if keyword in name:
            return keyword

    extension = Path(path).suffix.lower()
    if extension == ".pptx":
        return "slides"
    if extension in (".md", ".txt"):
        return "notes"
    return "report"


def build_parsed_document(
    path, project, pages, parser, page_count, used_ocr=False, doc_type=None
) -> ParsedDocument:
    """Put a ParsedDocument together. No usable page -> "failed"."""
    path = Path(path)
    project_id = str(project["project_id"])
    usable = tuple(page for page in pages if page.text and page.text.strip())

    if not usable:
        status = "failed"
    elif used_ocr:
        status = "ocr"
    else:
        status = "ok"

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
    """Safety net for any parser: drop empty pages; no page left -> "failed"."""
    usable = tuple(page for page in parsed.pages if page.text and page.text.strip())
    if not usable:
        return replace(parsed, pages=(), parse_status="failed")
    if len(usable) != len(parsed.pages):
        return replace(parsed, pages=usable)
    return parsed
