"""Tests for the parser contract and the four readers (T1.3, T2.1-T2.5)."""

import importlib.util
from pathlib import Path

import pytest

from ingestion import parsers
from ingestion.parsers import parse_document, register_parser
from ingestion.parsers.base import Page, ParsedDocument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def project(project_id):
    return {"project_id": project_id}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """The fixture corpus: the committed files, or freshly built ones."""
    committed = FIXTURES / "corpus"
    if (committed / "dse2024-001" / "report.pdf").exists():
        return committed
    for library in ("pymupdf", "docx", "pptx", "PIL"):
        pytest.importorskip(library)
    spec = importlib.util.spec_from_file_location("build_fixtures", FIXTURES / "build_fixtures.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder.build_all(tmp_path_factory.mktemp("corpus"))


def test_parser_registry_and_empty_document(tmp_path):
    with pytest.raises(ValueError, match="notes.xyz"):
        parse_document(tmp_path / "notes.xyz", project("p1"))

    def fake_parser(path, proj):
        # A broken reader that says "ok" but found no pages.
        return ParsedDocument(
            doc_id=f"{proj['project_id']}__{path.name}",
            project_id=proj["project_id"],
            doc_type="report",
            file_path=str(path),
            sha256="0" * 64,
            pages=(Page(1, "   "),),
            parse_status="ok",
            parser="fake",
            page_count=1,
        )

    register_parser(".fake", fake_parser)
    try:
        parsed = parse_document(tmp_path / "report.fake", project("p1"))
    finally:
        parsers.reset_registry()
    assert parsed.parse_status == "failed"
    assert parsed.pages == ()
    assert parsed.doc_id == "p1__report.fake"


def test_fixture_files_exist(corpus):
    import pymupdf

    for name in (
        "dse2024-001/report.pdf",
        "dse2024-001/slides.pptx",
        "dse2024-002/report.pdf",
        "dse2024-003/report.docx",
        "dse2024-004/report.md",
        "dse2024-006/report.pdf",
    ):
        path = corpus / name
        assert path.is_file() and path.stat().st_size > 0, name
        assert path.stat().st_size < 100_000, f"{name} is larger than 100 KB"
    assert (corpus / "dse2024-005").is_dir()

    with pymupdf.open(corpus / "dse2024-002" / "report.pdf") as scanned:
        assert scanned[0].get_text().strip() == ""  # no text layer before OCR
    with pymupdf.open(corpus / "dse2024-006" / "report.pdf") as blank:
        assert blank[0].get_text().strip() == ""


def test_pdf_pages_and_ocr_flag(corpus):
    from ingestion.parsers.ocr import ocr_available

    parsed = parse_document(corpus / "dse2024-001" / "report.pdf", project("dse2024-001"))
    assert parsed.parse_status == "ok"
    assert parsed.page_count == 4
    assert [p.number for p in parsed.pages] == [1, 2, 3, 4]  # 1-based page anchors
    assert "Abstract" in parsed.pages[0].headings
    assert "References" in parsed.pages[3].headings
    assert "RUPP Senior Project Fixture" not in parsed.pages[0].text  # header removed
    assert parsed.doc_id == "dse2024-001__report.pdf"
    assert len(parsed.sha256) == 64

    blank = parse_document(corpus / "dse2024-006" / "report.pdf", project("dse2024-006"))
    assert blank.parse_status == "failed"
    assert blank.pages == ()
    assert blank.page_count == 1

    scanned = parse_document(corpus / "dse2024-002" / "report.pdf", project("dse2024-002"))
    if not ocr_available():
        assert scanned.parse_status == "failed"  # no OCR -> no text, never a silent "ok"
        pytest.skip("Tesseract is not installed; OCR text cannot be checked")
    assert scanned.parse_status == "ocr"
    assert "Scanned" in scanned.pages[0].text


def test_docx_headings_tables_and_anchor(corpus):
    parsed = parse_document(corpus / "dse2024-003" / "report.docx", project("dse2024-003"))
    assert parsed.parse_status == "ok"
    assert parsed.doc_type == "report"
    assert len(parsed.pages) >= 2
    expected = [f"section {i}" for i in range(1, len(parsed.pages) + 1)]
    assert [p.anchor for p in parsed.pages] == expected
    assert all(p.number == 0 for p in parsed.pages)  # DOCX has no reliable pages
    text = "\n".join(p.text for p in parsed.pages)
    assert "room | Study rooms and their capacity | librarian" in text  # table row
    assert any("Abstract" in p.headings for p in parsed.pages)
    assert all(p.text.strip() for p in parsed.pages)  # no empty sections


def test_pptx_slides_notes_and_table(corpus):
    parsed = parse_document(corpus / "dse2024-001" / "slides.pptx", project("dse2024-001"))
    assert parsed.parse_status == "ok"
    assert parsed.doc_type == "slides"
    assert [p.number for p in parsed.pages] == [1, 2, 3]
    assert "Speaker note" in parsed.pages[0].text
    assert "Fine-tuned | higher" in parsed.pages[2].text
    assert parsed.pages[2].headings == ("Results",)


def test_markdown_sections_and_short_paragraph(corpus):
    parsed = parse_document(corpus / "dse2024-004" / "report.md", project("dse2024-004"))
    assert parsed.parse_status == "ok"
    headings = [p.headings for p in parsed.pages]
    assert ("Khmer News Topic Classification", "Abstract") in headings  # title stacks on Abstract
    assert any("References" in h for h in headings)
    expected = [f"section {i}" for i in range(1, len(parsed.pages) + 1)]
    assert [p.anchor for p in parsed.pages] == expected
    # The 20-character paragraph survives parsing; the chunker drops it later.
    assert any("Short paragraph only" in p.text for p in parsed.pages)


def test_markdown_without_text_is_failed(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("# Only a heading\n\n## Another heading\n", encoding="utf-8")
    parsed = parse_document(path, project("p1"))
    assert parsed.parse_status == "failed"


def test_to_langchain_documents_metadata(tmp_path):
    pytest.importorskip("langchain_core")
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nSome fixture notes about the project.\n", encoding="utf-8")
    parsed = parse_document(path, project("p1"))
    documents = parsed.to_langchain_documents(project("p1"))
    assert len(documents) == 1
    meta = documents[0].metadata
    assert meta["doc_id"] == "p1__notes.md"
    assert meta["project_id"] == "p1"
    assert meta["page_number"] == 0
    assert meta["parse_status"] == "ok" and meta["parser"] == "markdown"
