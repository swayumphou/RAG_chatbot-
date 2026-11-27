"""Step 02: cut parsed documents into small, citable chunks (LangChain splitters).

Two hard rules (Architecture §8.1):
1. The abstract is never split. It becomes one chunk: "<doc_id>::abstract".
2. References, bibliography, and appendix pages are thrown away.

The section of a page comes from its headings ("Abstract", "2. Methodology").
A page with no known heading is "body" and is never dropped.

Every chunk starts with a breadcrumb so it makes sense on its own:
    [Project: <title> | Author(s): <names> | Year: 2024 | Program: DSE | Section: abstract | p.4]

The abstract in the manifest is hand-checked, so for the main report it
replaces the parsed abstract text (Appendix C: "hand-verified wins").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MIN_CHUNK_CHARS,
    SLIDE_CHUNK_OVERLAP,
    SLIDE_CHUNK_SIZE,
)
from ingestion.parsers.base import ParsedDocument
from retrieval.intent import normalize

BODY_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " "],
)
SLIDE_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=SLIDE_CHUNK_SIZE, chunk_overlap=SLIDE_CHUNK_OVERLAP
)

SECTIONS = [
    "abstract",
    "introduction",
    "related work",
    "methodology",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "bibliography",
    "appendix",
]
DROP_SECTIONS = {"references", "bibliography", "appendix"}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str  # breadcrumb + "\n" + body
    metadata: dict

    def to_langchain_document(self):
        from langchain_core.documents import Document

        return Document(page_content=self.text, metadata=dict(self.metadata))


def section_of(text: str, headings) -> str:
    """The first heading that names a known section, or "body".

    "2. Methodology" -> "methodology", "ABSTRACT" -> "abstract".
    """
    for heading in headings:
        # Remove "#", numbering like "2." / "2.1" / "IV.", and punctuation.
        clean = heading.strip().lstrip("#").strip().lower()
        clean = re.sub(r"^([0-9]+(\.[0-9]+)*|[ivx]+)[.):]*\s+", "", clean)
        clean = clean.strip(" .:-")
        for name in SECTIONS:
            if clean.startswith(name):
                return name
    return "body"


def make_breadcrumb(project: dict, authors: str, section: str, page_number: int) -> str:
    return (
        f"[Project: {project['title_en']} | Author(s): {authors} | "
        f"Year: {project['year']} | Program: {project['program']} | "
        f"Section: {section} | p.{page_number}]"
    )


def build_chunks(parsed: ParsedDocument, project: dict, seniors: list[dict]) -> list[Chunk]:
    """Turn one parsed document into chunks.

    `project` is the manifest row (identity always comes from the manifest).
    `seniors` are the project's authors in order. Only their names are used;
    contact fields never reach a chunk.
    """
    if parsed.parse_status == "failed" or not parsed.pages:
        return []

    names = [s["full_name_latin"] for s in seniors]
    authors = ", ".join(names)
    is_report = parsed.doc_type == "report"
    splitter = SLIDE_SPLITTER if parsed.doc_type == "slides" else BODY_SPLITTER
    manifest_abstract = (project.get("abstract") or "").strip()

    # Payload fields that are the same for every chunk of this document.
    shared = {
        "project_id": project["project_id"],
        "title_en": project["title_en"],
        "senior_ids": list(project["senior_ids"]),
        "author_names_normalized": " / ".join(normalize(n) for n in names),
        "doc_id": parsed.doc_id,
        "doc_type": parsed.doc_type,
        "year": int(project["year"]),
        "program": project["program"],
        "domain": project.get("domain"),
        "tech_stack": list(project.get("tech_stack") or []),
        "supervisor_id": project.get("supervisor_id"),
        "is_primary_doc": is_report,
    }

    chunks: list[Chunk] = []
    abstract_done = False

    for page in parsed.pages:
        section = section_of(page.text, page.headings)
        if section in DROP_SECTIONS:
            continue  # citation noise

        if section == "abstract" and not abstract_done:
            # Rule 1: one atomic chunk. The manifest abstract wins for the report.
            bodies = [manifest_abstract if (is_report and manifest_abstract) else page.text]
            abstract_done = True
        else:
            if section == "abstract":
                section = "body"  # only the first abstract page is the atomic one
            bodies = splitter.split_text(page.text)

        for body in bodies:
            body = body.strip()
            if len(body) < MIN_CHUNK_CHARS:  # measured before the breadcrumb is added
                continue
            index = len(chunks)
            if section == "abstract":
                chunk_id = f"{parsed.doc_id}::abstract"
            else:
                chunk_id = f"{parsed.doc_id}::c{index:03d}"
            breadcrumb = make_breadcrumb(project, authors, section, page.number)
            metadata = {
                **shared,
                "section": section,
                "page_number": page.number,
                "chunk_index": index,
                "chunk_id": chunk_id,
                "breadcrumb": breadcrumb,
            }
            chunks.append(Chunk(chunk_id, f"{breadcrumb}\n{body}", metadata))
    return chunks
