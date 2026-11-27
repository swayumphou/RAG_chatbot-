"""Tests for the structure-aware chunker (T2.6). Uses small hand-made documents."""

import pytest

pytest.importorskip("langchain_text_splitters")

from ingestion.chunker import build_chunks, section_of  # noqa: E402
from ingestion.parsers.base import Page, ParsedDocument  # noqa: E402

PROJECT = {
    "project_id": "p1",
    "title_en": "Fixture Project",
    "year": 2024,
    "program": "DSE",
    "domain": "Computer Vision",
    "tech_stack": ["Python"],
    "supervisor_id": "sup-001",
    "senior_ids": ["s1", "s2"],
    "abstract": "Manifest abstract. " * 10,
}
SENIORS = [{"full_name_latin": "Sok Dara"}, {"full_name_latin": "Chan Sreyneang"}]

PAYLOAD_KEYS = {
    "project_id", "title_en", "senior_ids", "author_names_normalized", "doc_id", "doc_type",
    "year", "program", "domain", "tech_stack", "supervisor_id", "section", "page_number",
    "chunk_index", "chunk_id", "breadcrumb", "is_primary_doc",
}  # fmt: skip


def document(pages, doc_type="report", status="ok"):
    return ParsedDocument(
        doc_id=f"p1__{doc_type}.pdf",
        project_id="p1",
        doc_type=doc_type,
        file_path="x",
        sha256="0" * 64,
        pages=tuple(pages),
        parse_status=status,
        parser="test",
        page_count=len(pages),
    )


LONG_TEXT = "This sentence is part of a long fixture paragraph about the method. " * 40


def test_abstract_atomic_and_references_dropped():
    pages = [
        Page(1, "Abstract\n" + LONG_TEXT, ("Abstract",)),  # long: would split if not atomic
        Page(2, "1. Introduction\n" + LONG_TEXT, ("1. Introduction",)),
        Page(3, "References\n[1] An invented reference. " * 20, ("References",)),
    ]
    project = dict(PROJECT, abstract="")  # no manifest abstract: keep the parsed one
    chunks = build_chunks(document(pages), project, SENIORS)

    abstracts = [c for c in chunks if c.metadata["section"] == "abstract"]
    assert len(abstracts) == 1
    assert abstracts[0].chunk_id == "p1__report.pdf::abstract"
    assert LONG_TEXT.strip() in abstracts[0].text  # the whole abstract, not split
    assert all(c.metadata["page_number"] != 3 for c in chunks)  # references dropped
    assert any(c.metadata["section"] == "introduction" for c in chunks)
    assert len([c for c in chunks if c.metadata["page_number"] == 2]) > 1  # body is split


def test_manifest_abstract_overrides_parsed_one():
    pages = [Page(4, "Abstract\nDraft abstract from the PDF. " * 5, ("Abstract",))]
    chunks = build_chunks(document(pages), PROJECT, SENIORS)
    assert len(chunks) == 1
    assert "Manifest abstract." in chunks[0].text
    assert "Draft abstract" not in chunks[0].text
    assert chunks[0].metadata["page_number"] == 4


def test_min_chunk_chars_boundary():
    short = document([Page(1, "x" * 79)])
    exact = document([Page(1, "y" * 80)])
    assert build_chunks(short, PROJECT, SENIORS) == []
    assert len(build_chunks(exact, PROJECT, SENIORS)) == 1


def test_slides_use_small_chunks_and_are_not_primary():
    pages = [Page(1, LONG_TEXT, ("Method",))]
    chunks = build_chunks(document(pages, doc_type="slides"), PROJECT, SENIORS)
    assert len(chunks) > 1
    for chunk in chunks:
        body = chunk.text.split("\n", 1)[1]
        assert len(body) <= 300
        assert chunk.metadata["is_primary_doc"] is False


def test_payload_keys_and_breadcrumb():
    chunks = build_chunks(document([Page(2, LONG_TEXT, ("Results",))]), PROJECT, SENIORS)
    chunk = chunks[0]
    assert set(chunk.metadata) == PAYLOAD_KEYS
    assert chunk.metadata["is_primary_doc"] is True
    assert chunk.metadata["author_names_normalized"] == "sok dara / chan sreyneang"
    assert chunk.metadata["year"] == 2024
    assert chunk.metadata["chunk_id"] == "p1__report.pdf::c000"
    assert chunk.metadata["breadcrumb"] == (
        "[Project: Fixture Project | Author(s): Sok Dara, Chan Sreyneang | Year: 2024 | "
        "Program: DSE | Section: results | p.2]"
    )
    assert chunk.text.startswith(chunk.metadata["breadcrumb"] + "\n")
    assert "contact" not in str(chunk.metadata)


def test_failed_document_gives_no_chunks():
    assert build_chunks(document([], status="failed"), PROJECT, SENIORS) == []


def test_section_of_headings():
    assert section_of("", ["Khmer OCR Report", "2. Methodology"]) == "methodology"
    assert section_of("", ["ABSTRACT"]) == "abstract"
    assert section_of("", ["IV. Results"]) == "results"
    assert section_of("", ["## References"]) == "references"
    assert section_of("", ["System Design"]) == "body"
    assert section_of("", []) == "body"
